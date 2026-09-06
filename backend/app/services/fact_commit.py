"""Immutable commit outbox and CAS publication of cumulative fact snapshots."""

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.models.evidence import (
    EvidenceAssertion,
    EvidenceCandidateRecord,
    EvidenceCommit,
    EvidenceJobState,
    EvidenceSnapshot,
)
from app.schemas.evidence import Candidate
from app.services import audit
from app.services.extraction.candidate_store import CandidateConflict
from app.services.extraction.evidence_identity import evidence_hash, stable_id
from app.services.ontology_instance_writer import assertion_record


class FactCommitService:
    def __init__(self, db: Session, writer):
        self.db, self.writer = db, writer

    def _current(self, job_id, identity, revision):
        row = self.db.get(EvidenceCandidateRecord, identity, populate_existing=True)
        if row is None or str(row.job_id) != str(job_id) or row.revision != revision:
            raise CandidateConflict("missing, cross-job or stale candidate")
        candidate = Candidate.model_validate(row.payload)
        if candidate.validation_status != "passed" or candidate.review_status != "confirmed":
            raise ValueError("only validated and confirmed assertions may be committed")
        return candidate

    def request(
        self, job_id, idempotency_key: str, items: list[dict], actor: str
    ) -> EvidenceCommit:
        if not items or not actor or not idempotency_key or len(idempotency_key) > 200:
            raise ValueError("commit requires items, actor and a bounded idempotency key")
        items = sorted(items, key=lambda item: item["candidate_id"])
        if len({item["candidate_id"] for item in items}) != len(items):
            raise ValueError("duplicate candidate in commit items")
        content_hash = evidence_hash(items)
        identity = stable_id("commit", [str(job_id), idempotency_key])
        existing = self.db.get(EvidenceCommit, identity, populate_existing=True)
        if existing:
            if existing.content_hash != content_hash:
                raise CandidateConflict("idempotency key already used with different content")
            return existing
        selected = {}
        pending = list(items)
        while pending:
            ref = pending.pop()
            if ref["candidate_id"] in selected:
                if selected[ref["candidate_id"]].revision != ref["revision"]:
                    raise CandidateConflict("incompatible endpoint revisions")
                continue
            candidate = self._current(job_id, ref["candidate_id"], ref["revision"])
            # Commit-state is mutable workflow state, not assertion content.
            selected[candidate.candidate_id] = candidate.model_copy(
                update={"commit_status": "not_requested"}
            )
            for endpoint in [candidate.subject, candidate.object, *candidate.dependency_refs]:
                if endpoint is not None:
                    pending.append(
                        {"candidate_id": endpoint.candidate_id, "revision": endpoint.revision}
                    )
        entities = {key: c for key, c in selected.items() if c.kind == "entity"}
        values = [selected[key] for key in sorted(selected)]
        manifest = {
            "requested_items": items,
            "candidates": [c.model_dump(mode="json") for c in values],
            "assertions": [assertion_record(c, entities) for c in values],
        }
        commit = EvidenceCommit(
            id=identity,
            job_id=job_id,
            idempotency_key=idempotency_key,
            content_hash=content_hash,
            manifest=manifest,
            status="queued",
            attempts=0,
            actor=actor,
        )
        try:
            self.db.add(commit)
            if self.db.get(EvidenceJobState, job_id) is None:
                self.db.add(EvidenceJobState(job_id=job_id, revision=0))
            self.db.flush()
            for candidate in values:
                row = self.db.get(EvidenceCandidateRecord, candidate.candidate_id)
                payload = {**row.payload, "commit_status": "queued"}
                changed = self.db.execute(
                    update(EvidenceCandidateRecord)
                    .where(
                        EvidenceCandidateRecord.id == candidate.candidate_id,
                        EvidenceCandidateRecord.revision == candidate.revision,
                        EvidenceCandidateRecord.review_status == "confirmed",
                    )
                    .values(payload=payload)
                    .execution_options(synchronize_session=False)
                )
                if changed.rowcount != 1:
                    raise CandidateConflict("candidate changed while queuing commit")
            audit.append(
                self.db,
                "evidence.commit_request",
                actor=actor,
                entity_iri=identity,
                details={"job_id": str(job_id), "content_hash": content_hash},
                commit=False,
            )
            self.db.commit()
            self.db.expire_all()
            return self.db.get(EvidenceCommit, identity)
        except IntegrityError:
            self.db.rollback()
            existing = self.db.get(EvidenceCommit, identity)
            if existing and existing.content_hash == content_hash:
                return existing
            raise CandidateConflict("concurrent commit request; retry with the same key") from None
        except Exception:
            self.db.rollback()
            raise

    def apply(self, identity: str) -> EvidenceCommit:
        commit = self.db.get(EvidenceCommit, identity, populate_existing=True)
        if commit is None:
            raise LookupError("commit not found")
        if commit.status == "succeeded":
            return commit
        token = uuid.uuid4().hex
        now = datetime.now(timezone.utc)
        claimed = self.db.execute(
            update(EvidenceCommit)
            .where(
                EvidenceCommit.id == identity,
                or_(
                    EvidenceCommit.status.in_(["queued", "failed"]),
                    and_(
                        EvidenceCommit.status == "applying",
                        EvidenceCommit.lease_expires_at < now,
                    ),
                ),
            )
            .values(
                status="applying",
                attempts=EvidenceCommit.attempts + 1,
                lease_token=token,
                lease_expires_at=now + timedelta(minutes=5),
                error=None,
            )
            .execution_options(synchronize_session=False)
        )
        if claimed.rowcount != 1:
            self.db.rollback()
            return self.db.get(EvidenceCommit, identity, populate_existing=True)
        self.db.commit()
        commit = self.db.get(EvidenceCommit, identity, populate_existing=True)
        manifest, job_id = commit.manifest, commit.job_id
        try:
            for payload in manifest["candidates"]:
                self._current(job_id, payload["candidate_id"], payload["revision"])
            self.db.rollback()  # do not hold a SQL read transaction during graph IO
            world_path = self.writer.write(identity, manifest)
            for _attempt in range(8):
                self.db.expire_all()
                commit = self.db.get(EvidenceCommit, identity, populate_existing=True)
                if commit.lease_token != token or commit.status != "applying":
                    self.db.rollback()
                    return self.db.get(EvidenceCommit, identity, populate_existing=True)
                for payload in manifest["candidates"]:
                    self._current(job_id, payload["candidate_id"], payload["revision"])
                state = self.db.get(EvidenceJobState, job_id, populate_existing=True)
                expected_revision, parent_id = state.revision, state.snapshot_id
                # Acquire the job's publication CAS before inserting immutable
                # shared dependencies. The loser rolls back and merges the new
                # head; it cannot collide with the winner's assertion inserts.
                try:
                    claimed_head = self.db.execute(
                        update(EvidenceJobState)
                        .where(
                            EvidenceJobState.job_id == job_id,
                            EvidenceJobState.revision == expected_revision,
                        )
                        .values(revision=expected_revision + 1)
                        .execution_options(synchronize_session=False)
                    )
                except OperationalError as exc:
                    self.db.rollback()
                    code = getattr(exc.orig, "sqlite_errorcode", None)
                    sqlstate = getattr(exc.orig, "sqlstate", None) or getattr(
                        exc.orig, "pgcode", None
                    )
                    if (code is not None and code & 255 in {5, 6}) or sqlstate in {
                        "40001",
                        "40P01",
                    }:
                        continue
                    raise
                if claimed_head.rowcount != 1:
                    self.db.rollback()
                    continue
                inherited = self.db.get(EvidenceSnapshot, parent_id) if parent_id else None
                active_ids = set()
                for assertion_id in inherited.assertion_ids if inherited else []:
                    assertion = self.db.get(EvidenceAssertion, assertion_id)
                    current = self.db.get(
                        EvidenceCandidateRecord, assertion.candidate_id, populate_existing=True
                    )
                    if (
                        current
                        and current.revision == assertion.candidate_revision
                        and (
                            current.review_status == "confirmed"
                            and current.payload["validation_status"] == "passed"
                        )
                    ):
                        active_ids.add(assertion_id)
                for record in manifest["assertions"]:
                    assertion_id = record["assertion_id"]
                    existing = self.db.get(EvidenceAssertion, assertion_id)
                    if existing is None:
                        candidate = record["candidate"]
                        self.db.add(
                            EvidenceAssertion(
                                id=assertion_id,
                                job_id=job_id,
                                commit_id=identity,
                                candidate_id=candidate["candidate_id"],
                                candidate_revision=candidate["revision"],
                                payload=record,
                            )
                        )
                    elif evidence_hash(existing.payload) != evidence_hash(record):
                        raise CandidateConflict("immutable assertion content changed")
                    active_ids.add(assertion_id)
                assertion_ids = sorted(active_ids)
                snapshot_id = stable_id("snapshot", [str(job_id), parent_id, assertion_ids])
                if self.db.get(EvidenceSnapshot, snapshot_id) is None:
                    self.db.add(
                        EvidenceSnapshot(
                            id=snapshot_id,
                            job_id=job_id,
                            parent_id=parent_id,
                            assertion_ids=assertion_ids,
                        )
                    )
                self.db.flush()
                published = self.db.execute(
                    update(EvidenceJobState)
                    .where(
                        EvidenceJobState.job_id == job_id,
                        EvidenceJobState.revision == expected_revision + 1,
                    )
                    .values(snapshot_id=snapshot_id)
                    .execution_options(synchronize_session=False)
                )
                if published.rowcount != 1:
                    self.db.rollback()
                    continue  # reload/merge the winning snapshot; never last-writer overwrite
                finalized = self.db.execute(
                    update(EvidenceCommit)
                    .where(
                        EvidenceCommit.id == identity,
                        EvidenceCommit.lease_token == token,
                        EvidenceCommit.status == "applying",
                    )
                    .values(
                        status="succeeded",
                        snapshot_id=snapshot_id,
                        world_path=world_path,
                        lease_expires_at=None,
                        error=None,
                    )
                    .execution_options(synchronize_session=False)
                )
                if finalized.rowcount != 1:
                    raise CandidateConflict("commit lease changed before publication")
                for payload in manifest["candidates"]:
                    candidate = self._current(job_id, payload["candidate_id"], payload["revision"])
                    finalized_candidate = self.db.execute(
                        update(EvidenceCandidateRecord)
                        .where(
                            EvidenceCandidateRecord.id == candidate.candidate_id,
                            EvidenceCandidateRecord.revision == candidate.revision,
                            EvidenceCandidateRecord.review_status == "confirmed",
                        )
                        .values(
                            payload={
                                **candidate.model_dump(mode="json"),
                                "commit_status": "succeeded",
                            }
                        )
                        .execution_options(synchronize_session=False)
                    )
                    if finalized_candidate.rowcount != 1:
                        raise CandidateConflict("candidate changed before publication")
                audit.append(
                    self.db,
                    "evidence.snapshot_publish",
                    actor=commit.actor,
                    entity_iri=identity,
                    details={
                        "snapshot_id": snapshot_id,
                        "parent_id": parent_id,
                        "assertion_ids": assertion_ids,
                    },
                    commit=False,
                )
                self.db.commit()
                self.db.expire_all()
                return self.db.get(EvidenceCommit, identity)
            raise CandidateConflict("snapshot head contention; retry immutable commit")
        except Exception as exc:
            self.db.rollback()
            failed = self.db.execute(
                update(EvidenceCommit)
                .where(
                    EvidenceCommit.id == identity,
                    EvidenceCommit.lease_token == token,
                    EvidenceCommit.status == "applying",
                )
                .values(
                    status="failed", error=f"{type(exc).__name__}: {exc}", lease_expires_at=None
                )
                .execution_options(synchronize_session=False)
            )
            if failed.rowcount == 1:
                for payload in manifest["candidates"]:
                    row = self.db.get(
                        EvidenceCandidateRecord, payload["candidate_id"], populate_existing=True
                    )
                    if (
                        row
                        and row.revision == payload["revision"]
                        and row.payload.get("commit_status") != "succeeded"
                    ):
                        self.db.execute(
                            update(EvidenceCandidateRecord)
                            .where(
                                EvidenceCandidateRecord.id == row.id,
                                EvidenceCandidateRecord.revision == row.revision,
                            )
                            .values(payload={**row.payload, "commit_status": "failed"})
                            .execution_options(synchronize_session=False)
                        )
                audit.append(
                    self.db,
                    "evidence.commit_failed",
                    actor=commit.actor,
                    entity_iri=identity,
                    details={"error": str(exc)},
                    commit=False,
                )
            self.db.commit()
            self.db.expire_all()
            return self.db.get(EvidenceCommit, identity)

    def published_snapshot(self, job_id, snapshot_id: str | None = None) -> dict | None:
        if snapshot_id is None:
            state = self.db.get(EvidenceJobState, job_id, populate_existing=True)
            snapshot_id = state.snapshot_id if state else None
        if snapshot_id is None:
            return None
        snapshot = self.db.get(EvidenceSnapshot, snapshot_id)
        if snapshot is None or str(snapshot.job_id) != str(job_id):
            raise LookupError("published snapshot not found")
        return {
            "snapshot_id": snapshot.id,
            "parent_id": snapshot.parent_id,
            "assertions": [
                self.db.get(EvidenceAssertion, identity).payload
                for identity in snapshot.assertion_ids
            ],
        }

    def recover_pending(self, limit: int = 20) -> list[str]:
        """Bounded startup/worker replay. Fresh leases remain owned by their worker."""
        now = datetime.now(timezone.utc)
        identities = list(
            self.db.scalars(
                select(EvidenceCommit.id)
                .where(
                    or_(
                        EvidenceCommit.status == "queued",
                        and_(
                            EvidenceCommit.status == "applying",
                            EvidenceCommit.lease_expires_at < now,
                        ),
                    )
                )
                .order_by(EvidenceCommit.created_at)
                .limit(limit)
            )
        )
        for identity in identities:
            self.apply(identity)
        return identities


def recover_evidence_commits(engine):
    """Startup worker owns its SQL session; errors never turn staged Worlds public."""
    import logging

    from app.config import settings
    from app.db import SessionLocal
    from app.services.ontology_instance_writer import EvidenceInstanceWriter

    with SessionLocal() as db:
        try:
            return FactCommitService(
                db, EvidenceInstanceWriter(engine, settings.evidence_world_dir)
            ).recover_pending()
        except Exception:
            db.rollback()
            logging.getLogger(__name__).exception(
                "Evidence outbox recovery failed; retained for retry"
            )
            return []
