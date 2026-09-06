"""Server-owned candidate states with immutable content revisions and CAS review."""

from __future__ import annotations

import uuid

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.evidence import (
    DocumentAnalysisRecord,
    EvidenceCandidateRecord,
    EvidenceCandidateRevision,
    EvidenceJobState,
    EvidenceReview,
)
from app.schemas.evidence import Candidate
from app.services import audit
from app.services.extraction.document_ir import DocumentIR
from app.services.extraction.evidence_identity import evidence_hash, stable_id


class CandidateConflict(ValueError):
    pass


def _rewrite_refs(value, identities):
    if isinstance(value, list):
        return [_rewrite_refs(item, identities) for item in value]
    if isinstance(value, dict):
        return {
            key: [identities.get(ref, ref) for ref in item]
            if key == "shared_subject_ids" and isinstance(item, list)
            else identities.get(item, item)
            if key in {"candidate_id", "subject_candidate_id", "object_candidate_id"}
            and isinstance(item, str)
            else _rewrite_refs(item, identities)
            for key, item in value.items()
        }
    return value


class CandidateStore:
    def __init__(self, db: Session):
        self.db = db

    def get(self, identity: str) -> Candidate:
        record = self.db.get(EvidenceCandidateRecord, identity, populate_existing=True)
        if record is None:
            raise LookupError("candidate not found")
        return Candidate.model_validate(record.payload)

    def list(self, job_id) -> list[Candidate]:
        records = self.db.scalars(
            select(EvidenceCandidateRecord)
            .where(
                EvidenceCandidateRecord.job_id == job_id,
            )
            .order_by(EvidenceCandidateRecord.created_at, EvidenceCandidateRecord.id)
            .execution_options(populate_existing=True)
        ).all()
        return [Candidate.model_validate(row.payload) for row in records]

    def save_analysis(self, job_id, ir: DocumentIR, run: dict | None = None):
        record = self.db.get(DocumentAnalysisRecord, ir.analysis_id)
        if record is None:
            self.db.add(
                DocumentAnalysisRecord(
                    id=ir.analysis_id,
                    document_hash=ir.document_hash,
                    structure_hash=ir.structure_hash,
                    role=ir.document_role,
                    payload=ir.model_dump(mode="json"),
                )
            )
            self.db.flush()
        state = self.db.get(EvidenceJobState, job_id)
        if state is None:
            state = EvidenceJobState(job_id=job_id, revision=0)
            self.db.add(state)
        previous = state.extraction_run or {}
        # Rerunning source extraction must not reset the job's bounded gap budget.
        next_run = {
            **(run or {}),
            "gap_history": previous.get("gap_history", []),
            "discovery_decisions": {},
        }
        self.db.flush()
        result = self.db.execute(
            update(EvidenceJobState)
            .where(EvidenceJobState.job_id == job_id, EvidenceJobState.revision == state.revision)
            .values(analysis_id=ir.analysis_id, extraction_run=next_run, revision=state.revision + 1)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            self.db.rollback()
            raise CandidateConflict("source state changed during analysis publication")
        self.db.expire_all()
        self.db.commit()

    def persist_validated(
        self, job_id, candidates: list[Candidate], *, actor: str
    ) -> list[Candidate]:
        """Internal only: callers validate on the server, never pass request state here."""
        identities = {}
        for candidate in candidates:
            existing = self.db.get(EvidenceCandidateRecord, candidate.candidate_id)
            if existing is not None:
                if str(existing.job_id) != str(job_id):
                    raise CandidateConflict("cross-job candidate reference")
                identities[candidate.candidate_id] = candidate.candidate_id
            else:
                identities[candidate.candidate_id] = stable_id(
                    "job-candidate",
                    [
                        str(job_id),
                        candidate.candidate_id,
                        candidate.ontology_release,
                        candidate.model_identity,
                        candidate.extractor_version,
                    ],
                )
        normalized = []
        for candidate in candidates:
            payload = _rewrite_refs(candidate.model_dump(mode="json"), identities)
            payload.update(revision=1, review_status="pending", commit_status="not_requested")
            if payload.get("scope"):
                payload["scope"]["scope_id"] = stable_id("job-scope", payload["scope"])
            normalized.append(Candidate.model_validate(payload))
        try:
            new_values = []
            normalized_by_id = {c.candidate_id: c for c in normalized}
            for source, candidate in zip(candidates, normalized, strict=True):
                existing = self.db.get(EvidenceCandidateRecord, candidate.candidate_id)
                if existing is not None:
                    continue  # reruns cannot overwrite reviews or human edits
                for ref in [candidate.subject, candidate.object, *candidate.dependency_refs]:
                    if ref is None:
                        continue
                    target = self.db.get(EvidenceCandidateRecord, ref.candidate_id)
                    if target is not None and str(target.job_id) != str(job_id):
                        raise CandidateConflict("missing or cross-job endpoint")
                    target_candidate = (
                        self.get(target.id) if target else normalized_by_id.get(ref.candidate_id)
                    )
                    if target_candidate is None:
                        raise CandidateConflict("missing or cross-job endpoint")
                    if (
                        target_candidate.revision != ref.revision
                        or not target_candidate.positive_eligible
                    ):
                        payload = candidate.model_dump(mode="json")
                        payload.update(
                            validation_status="pending",
                            validation_issues=[{"code": "stale_dependency"}],
                        )
                        candidate = Candidate.model_validate(payload)
                        normalized_by_id[candidate.candidate_id] = candidate
                self.db.add(
                    EvidenceCandidateRecord(
                        id=candidate.candidate_id,
                        job_id=job_id,
                        source_key=evidence_hash(
                            [
                                source.candidate_id,
                                source.ontology_release,
                                source.model_identity,
                                source.extractor_version,
                            ]
                        ),
                        revision=1,
                        kind=candidate.kind,
                        review_status="pending",
                        payload=candidate.model_dump(mode="json"),
                    )
                )
                new_values.append(candidate)
            self.db.flush()
            for candidate in new_values:
                self._revision(candidate)
            if self.db.get(EvidenceJobState, job_id) is None:
                self.db.add(EvidenceJobState(job_id=job_id, revision=0))
            if new_values:
                audit.append(
                    self.db,
                    "evidence.candidates_create",
                    actor=actor,
                    entity_iri=str(job_id),
                    details={"candidate_ids": [c.candidate_id for c in new_values]},
                    commit=False,
                )
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            # Database uniqueness arbitrates concurrent reruns. If the entire
            # batch was inserted by the other writer, reuse it; never overwrite.
            if not all(self.db.get(EvidenceCandidateRecord, c.candidate_id) for c in normalized):
                raise CandidateConflict("concurrent candidate creation; retry batch") from None
        except Exception:
            self.db.rollback()
            raise
        return [self.get(c.candidate_id) for c in normalized]

    def _revision(self, candidate):
        self.db.add(
            EvidenceCandidateRevision(
                id=stable_id("candidate-revision", [candidate.candidate_id, candidate.revision]),
                candidate_id=candidate.candidate_id,
                revision=candidate.revision,
                payload=candidate.model_dump(mode="json"),
            )
        )

    def review(
        self,
        identity: str,
        expected_revision: int,
        decision: str,
        reason: str,
        actor: str,
        *,
        edited_payload: dict | None = None,
        validator=None,
    ) -> Candidate:
        if decision not in {"confirmed", "rejected"} or not actor or not reason.strip():
            raise ValueError("review requires a decision, actor and reason")
        record = self.db.get(EvidenceCandidateRecord, identity)
        if record is None:
            raise LookupError("candidate not found")
        current = self.get(identity)
        if current.revision != expected_revision:
            raise CandidateConflict("stale candidate revision")
        payload = current.model_dump(mode="json")
        if edited_payload is not None:
            if validator is None:
                raise ValueError("edited assertion requires server revalidation")
            payload.update(edited_payload)
            payload.update(
                candidate_id=identity,
                revision=expected_revision + 1,
                validation_status="pending",
                validation_issues=[],
                review_status="pending",
                commit_status="not_requested",
                bindings=[],
                dependency_refs=[],
                path_root=None,
                relationship_path=[],
            )
            candidate = validator(Candidate.model_validate(payload))
            if candidate.kind != current.kind:
                raise ValueError("candidate kind cannot be changed by editing")
            candidate = candidate.model_copy(
                update={"review_status": "pending", "commit_status": "not_requested"}
            )
            action = "edited"
        else:
            if current.review_status != "pending":
                raise CandidateConflict(
                    "candidate already reviewed; create an edited revision first"
                )
            if decision == "confirmed" and current.validation_status != "passed":
                raise ValueError("only validated assertions can be confirmed")
            payload["review_status"] = decision
            candidate = Candidate.model_validate(payload)
            action = decision
        try:
            changed = self.db.execute(
                update(EvidenceCandidateRecord)
                .where(
                    EvidenceCandidateRecord.id == identity,
                    EvidenceCandidateRecord.revision == expected_revision,
                    EvidenceCandidateRecord.review_status == current.review_status,
                )
                .values(
                    revision=candidate.revision,
                    review_status=candidate.review_status,
                    payload=candidate.model_dump(mode="json"),
                )
                .execution_options(synchronize_session=False)
            )
            if changed.rowcount != 1:
                raise CandidateConflict("concurrent review or edit")
            if edited_payload is not None:
                self._revision(candidate)
                self._invalidate_dependents(record.job_id, identity, candidate.revision)
            self.db.add(
                EvidenceReview(
                    id=uuid.uuid4().hex,
                    candidate_id=identity,
                    expected_revision=expected_revision,
                    decision=action,
                    actor=actor,
                    reason=reason,
                )
            )
            audit.append(
                self.db,
                "evidence.review",
                actor=actor,
                entity_iri=identity,
                details={
                    "expected_revision": expected_revision,
                    "decision": action,
                    "reason": reason,
                },
                commit=False,
            )
            self.db.commit()
            self.db.expire_all()
            return self.get(identity)
        except Exception:
            self.db.rollback()
            raise

    def resolve(
        self,
        identity: str,
        expected_revision: int,
        target_id: str,
        target_revision: int,
        reason: str,
        actor: str,
        *,
        validator,
    ) -> Candidate:
        """Explicit same-job entity merge; no lexical/nearest-neighbour identity guesses."""
        source, target = self.get(identity), self.get(target_id)
        source_row = self.db.get(EvidenceCandidateRecord, identity)
        target_row = self.db.get(EvidenceCandidateRecord, target_id)
        if identity == target_id or source_row.job_id != target_row.job_id:
            raise ValueError("self or cross-job merge is forbidden")
        if source.revision != expected_revision or target.revision != target_revision:
            raise CandidateConflict("stale merge endpoint")
        if (
            source.kind != "entity"
            or target.kind != "entity"
            or source.class_iri != target.class_iri
        ):
            raise ValueError("merge requires compatible entity classes")
        if source.identity.get("canonical_candidate_id") or target.identity.get(
            "canonical_candidate_id"
        ):
            raise CandidateConflict("merge endpoint already resolved; select its canonical target")
        if target.validation_status != "passed" or target.review_status != "confirmed":
            raise ValueError("canonical target must be validated and confirmed")
        if not reason.strip() or not actor:
            raise ValueError("merge requires actor and reason")
        try:
            # Also CAS-lock the target; an edit cannot race reference rewriting.
            locked = self.db.execute(
                update(EvidenceCandidateRecord)
                .where(
                    EvidenceCandidateRecord.id == target_id,
                    EvidenceCandidateRecord.revision == target_revision,
                    EvidenceCandidateRecord.review_status == "confirmed",
                )
                .values(revision=target_revision)
                .execution_options(synchronize_session=False)
            )
            if locked.rowcount != 1:
                raise CandidateConflict("canonical target changed")
            payload = source.model_dump(mode="json")
            payload.update(
                revision=expected_revision + 1,
                validation_status="conflict",
                validation_issues=[{"code": "resolved_alias"}],
                review_status="rejected",
                commit_status="not_requested",
                identity={**source.identity, "canonical_candidate_id": target_id},
            )
            resolved = Candidate.model_validate(payload)
            changed = self.db.execute(
                update(EvidenceCandidateRecord)
                .where(
                    EvidenceCandidateRecord.id == identity,
                    EvidenceCandidateRecord.revision == expected_revision,
                    EvidenceCandidateRecord.review_status == source.review_status,
                )
                .values(revision=resolved.revision, review_status="rejected", payload=payload)
                .execution_options(synchronize_session=False)
            )
            if changed.rowcount != 1:
                raise CandidateConflict("concurrent merge")
            self._revision(resolved)
            for dependent in self.list(source_row.job_id):
                if not any(
                    ref and ref.candidate_id == identity
                    for ref in [dependent.subject, dependent.object, *dependent.dependency_refs]
                ):
                    continue
                payload = _rewrite_refs(dependent.model_dump(mode="json"), {identity: target_id})

                def reversion(value):
                    if isinstance(value, dict):
                        if value.get("candidate_id") == target_id and "revision" in value:
                            value.update(revision=target_revision)
                        for item in value.values():
                            reversion(item)
                    elif isinstance(value, list):
                        for item in value:
                            reversion(item)

                reversion(payload)
                payload.update(
                    revision=dependent.revision + 1,
                    validation_status="pending",
                    validation_issues=[],
                    review_status="pending",
                    commit_status="not_requested",
                )
                if payload.get("scope"):
                    payload["scope"]["scope_id"] = stable_id("rebound-scope", payload["scope"])
                rebound = validator(Candidate.model_validate(payload))
                rebound = rebound.model_copy(
                    update={"review_status": "pending", "commit_status": "not_requested"}
                )
                changed = self.db.execute(
                    update(EvidenceCandidateRecord)
                    .where(
                        EvidenceCandidateRecord.id == dependent.candidate_id,
                        EvidenceCandidateRecord.revision == dependent.revision,
                        EvidenceCandidateRecord.review_status == dependent.review_status,
                    )
                    .values(
                        revision=rebound.revision,
                        review_status="pending",
                        payload=rebound.model_dump(mode="json"),
                    )
                    .execution_options(synchronize_session=False)
                )
                if changed.rowcount != 1:
                    raise CandidateConflict("concurrent dependent edit")
                self._revision(rebound)
            self.db.add(
                EvidenceReview(
                    id=uuid.uuid4().hex,
                    candidate_id=identity,
                    expected_revision=expected_revision,
                    decision="resolved",
                    actor=actor,
                    reason=reason,
                )
            )
            audit.append(
                self.db,
                "evidence.resolve",
                actor=actor,
                entity_iri=identity,
                details={
                    "target_id": target_id,
                    "target_revision": target_revision,
                    "reason": reason,
                },
                commit=False,
            )
            self.db.commit()
            self.db.expire_all()
            return self.get(identity)
        except Exception:
            self.db.rollback()
            raise

    def _invalidate_dependents(self, job_id, identity, revision):
        candidates = self.list(job_id)
        invalidated = {identity}
        affected = []
        changed = True
        while changed:
            changed = False
            for dependent in candidates:
                if dependent.candidate_id in invalidated:
                    continue
                refs = [
                    dependent.subject,
                    dependent.object,
                    dependent.path_root,
                    *dependent.dependency_refs,
                ]
                if any(ref and ref.candidate_id in invalidated for ref in refs):
                    invalidated.add(dependent.candidate_id)
                    affected.append(dependent)
                    changed = True
        for dependent in affected:
            payload = dependent.model_dump(mode="json")
            payload.update(
                revision=dependent.revision + 1,
                validation_status="pending",
                validation_issues=[{"code": "stale_dependency"}],
                review_status="pending",
                commit_status="not_requested",
            )
            # Keep the old reference until a deliberate rebind/revalidation.
            updated = Candidate.model_validate(payload)
            result = self.db.execute(
                update(EvidenceCandidateRecord)
                .where(
                    EvidenceCandidateRecord.id == dependent.candidate_id,
                    EvidenceCandidateRecord.revision == dependent.revision,
                    EvidenceCandidateRecord.review_status == dependent.review_status,
                )
                .values(revision=updated.revision, review_status="pending", payload=payload)
                .execution_options(synchronize_session=False)
            )
            if result.rowcount != 1:
                raise CandidateConflict("concurrent dependent revision")
            self._revision(updated)
