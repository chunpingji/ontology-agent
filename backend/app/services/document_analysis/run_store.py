"""Transactional store for durable, ontology-guided document-analysis runs.

Methods flush but never commit the caller's transaction.  Every worker write
checks an owner-scoped run, the current secret execution token, and the
relevant expected head.  Public reads do not claim work or create side effects.
"""

from __future__ import annotations

import hmac
import json
import secrets
import time
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Any, Callable
from uuid import UUID, uuid4

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.document_analysis import (
    DocumentAnalysisArtifact,
    DocumentAnalysisControlOperation,
    DocumentAnalysisExecution,
    DocumentAnalysisRun,
    DocumentAnalysisTombstone,
    DocumentRecognitionEvent,
    DocumentRecognitionEventBatch,
    DocumentRunArtifact,
    DocumentRunArtifactHead,
    DocumentRunCandidate,
    DocumentRunCandidateHead,
    DocumentVerificationProof,
    DocumentVerificationProofHead,
)

ACTIVE_LEASE_STATUS = "active"
TERMINAL_EXECUTION_STATUSES = {"finished", "cancelled"}
LEASE_RELEASING_EXECUTION_STATUSES = {
    *TERMINAL_EXECUTION_STATUSES,
    "failed",
    "blocked_dependency",
}

_DELETE_CONTROL_RESULT_FIELDS = frozenset(
    {
        "contract_version",
        "recognition_run_id",
        "run_revision",
        "event_head",
        "artifact_revision",
        "status",
        "stage",
        "operation",
        "operation_status",
        "available_actions",
    }
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def content_hash(value: Any) -> str:
    """Return the stable lowercase SHA-256 used by immutable payload rows."""

    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def control_request_hash(
    *,
    action: str,
    expected_revision: int | None,
    expected_version: int | None,
    reason: str | None,
) -> str:
    """Hash the complete semantic input of one lifecycle operation."""

    return content_hash(
        {
            "action": action,
            "expected_revision": expected_revision,
            "expected_version": expected_version,
            "reason": reason,
        }
    )


def _token_hash(token: str) -> str:
    return sha256(token.encode("utf-8")).hexdigest()


def _owner_hash(owner_id: str) -> str:
    # Domain-separated and deliberately one-way; tombstones contain no owner id.
    return sha256(f"document-analysis-owner-v1\0{owner_id}".encode()).hexdigest()


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


class RunStoreError(RuntimeError):
    code = "RUN_STORE_ERROR"

    def __init__(self, message: str | None = None, **details: Any) -> None:
        super().__init__(message or self.code)
        self.details = details


class RunNotFound(RunStoreError):
    code = "RUN_NOT_FOUND"


class RunDeleted(RunStoreError):
    code = "RUN_DELETED"


class IdempotencyConflict(RunStoreError):
    code = "IDEMPOTENCY_CONFLICT"


class HeadConflict(RunStoreError):
    code = "RUN_REVISION_CONFLICT"


class EventConflict(HeadConflict):
    code = "EVENT_CONFLICT"


class ArtifactConflict(HeadConflict):
    code = "ARTIFACT_CONFLICT"


class LeaseBusy(RunStoreError):
    code = "RUN_LEASE_BUSY"


class FenceViolation(RunStoreError):
    code = "RUN_FENCE_LOST"


class InvalidRunState(RunStoreError):
    code = "INVALID_RUN_STATE"


class PublicationTimeout(TimeoutError):
    """The write transaction exceeded its operational publication bound."""


class DocumentAnalysisRunStore:
    """SQLAlchemy repository for the independent DocumentAnalysisRun aggregate."""

    def __init__(self, db: Session, *, clock: Callable[[], datetime] = utcnow) -> None:
        self.db = db
        self._clock = clock

    def check_publication_deadline(self):
        deadline = getattr(self, "publication_deadline", None)
        if deadline is not None and time.monotonic() >= deadline:
            raise PublicationTimeout("document analysis publication deadline exceeded")

    def _progress_watermark(self, run, execution):
        if (execution.last_progress_at is not None
                and execution.recovery_event_head == run.event_head):
            return {}
        latest = self.db.scalar(select(DocumentRecognitionEvent.created_at).where(
            DocumentRecognitionEvent.recognition_run_id == run.recognition_run_id,
            DocumentRecognitionEvent.sequence <= run.event_head,
        ).order_by(DocumentRecognitionEvent.sequence.desc()).limit(1)) if run.event_head else None
        return {
            "recovery_event_head": run.event_head,
            "recovery_attempts": 0,
            "last_progress_at": latest or run.started_at or run.created_at,
        }

    def create_run(
        self,
        *,
        owner_id: str,
        request_key: str,
        filename: str,
        document_hash: str,
        root_class_iri: str,
        request_hash: str | None = None,
        root_class_label: str = "",
        ontology_snapshot_hash: str = "",
        source_artifact_ref: str | None = None,
        metadata_mode: str = "generate_summary",
        ranking_budget_enabled: bool = True,
        scope_mode: str = "document_graph",
        focus_path: list[str] | None = None,
        provisional_fingerprint: str | None = None,
        recognition_run_id: UUID | str | None = None,
        contract_version: str = "document-analysis-runs-v1",
        progress: dict[str, Any] | None = None,
        source: dict[str, Any] | None = None,
    ) -> tuple[DocumentAnalysisRun, bool]:
        """Create once per ``(owner_id, request_key)`` or replay identical input."""

        if not owner_id or not request_key or len(request_key) > 200:
            raise ValueError("owner_id and a 1-200 character request_key are required")
        focus = list(focus_path or [])
        digest = request_hash or content_hash(
            {
                "contract_version": contract_version,
                "document_hash": document_hash,
                "filename": filename,
                "root_class_iri": root_class_iri,
                "metadata_mode": metadata_mode,
                "scope_mode": scope_mode,
                "focus_path": focus,
            }
        )
        existing = self.db.scalar(
            select(DocumentAnalysisRun).where(
                DocumentAnalysisRun.owner_id == owner_id,
                DocumentAnalysisRun.request_key == request_key,
            )
        )
        if existing is not None:
            if existing.request_hash != digest:
                raise IdempotencyConflict(
                    "request key was already used for different input",
                    owner_id=owner_id,
                    request_key=request_key,
                )
            if existing.deletion_state == "deleted":
                raise RunDeleted(run_id=str(existing.recognition_run_id))
            return existing, False

        stamp = self._clock()
        source_identity = None
        source_hash = None
        source_event_payload = None
        initial_artifacts: list[dict[str, Any]] = []
        if source is not None:
            source_identity = str(source.get("artifact_id") or uuid4().hex)
            source_hash = str(source.get("content_hash") or document_hash)
            if source_hash != document_hash:
                raise ValueError("source artifact hash must equal document_hash")
            initial_artifacts.append(
                {
                    "artifact_id": source_identity,
                    "artifact_kind": "source",
                    "content_hash": source_hash,
                    "storage_uri": source.get("storage_uri"),
                    "media_type": source.get("media_type"),
                    "size_bytes": source.get("size_bytes"),
                    "payload": source.get("payload"),
                    "is_exclusive": bool(source.get("is_exclusive", True)),
                }
            )
            ontology_artifact = source.get("ontology_artifact")
            if ontology_artifact is not None:
                ontology_identity = str(ontology_artifact["artifact_id"])
                ontology_hash = str(ontology_artifact.get("content_hash") or "")
                if not ontology_hash or ontology_hash != ontology_snapshot_hash:
                    raise ValueError("ontology artifact hash must equal ontology_snapshot_hash")
                initial_artifacts.append(
                    {
                        "artifact_id": ontology_identity,
                        "artifact_kind": "ontology_snapshot",
                        "content_hash": ontology_hash,
                        "storage_uri": ontology_artifact.get("storage_uri"),
                        "media_type": ontology_artifact.get("media_type"),
                        "size_bytes": ontology_artifact.get("size_bytes"),
                        "payload": ontology_artifact.get("payload"),
                        "is_exclusive": bool(ontology_artifact.get("is_exclusive", True)),
                    }
                )
            source_event_payload = dict(
                source.get("event_payload")
                or {
                    "status": "queued",
                    "stage": "ingest",
                    "artifact_kind": "source",
                    "availability": "ready",
                }
            )
        initial_manifest = {
            item["artifact_kind"]: {
                "artifact_id": item["artifact_id"],
                "hash": item["content_hash"],
                "revision": 1,
                "status": "ready",
                "event_head": 1,
            }
            for item in initial_artifacts
        }
        row = DocumentAnalysisRun(
            recognition_run_id=recognition_run_id or uuid4(),
            contract_version=contract_version,
            owner_id=owner_id,
            request_key=request_key,
            request_hash=digest,
            filename=filename,
            document_hash=document_hash,
            source_artifact_ref=source_identity or source_artifact_ref,
            root_class_iri=root_class_iri,
            root_class_label=root_class_label,
            ontology_snapshot_hash=ontology_snapshot_hash,
            metadata_mode=metadata_mode,
            ranking_budget_enabled=ranking_budget_enabled,
            scope_mode=scope_mode,
            focus_path=focus,
            provisional_fingerprint=provisional_fingerprint,
            revision=1,
            event_head=1 if source is not None else 0,
            artifact_revision=1 if source is not None else 0,
            stage="ingest",
            execution_status="queued",
            coverage_status="partial",
            semantic_status="unreviewed",
            control_version=0,
            deletion_state="none",
            progress=dict(progress or {}),
            artifact_manifest=initial_manifest,
            created_at=stamp,
            updated_at=stamp,
        )
        try:
            with self.db.begin_nested():
                self.db.add(row)
                self.db.flush()
                self.db.add(
                    DocumentAnalysisExecution(
                        recognition_run_id=row.recognition_run_id,
                        generation=0,
                        status="idle",
                        pause_requested=False,
                        cancel_requested=False,
                        updated_at=stamp,
                    )
                )
                if source is not None:
                    for item in initial_artifacts:
                        artifact = self.db.get(DocumentAnalysisArtifact, item["artifact_id"])
                        if artifact is None:
                            self.db.add(
                                DocumentAnalysisArtifact(
                                    artifact_id=item["artifact_id"],
                                    artifact_kind=item["artifact_kind"],
                                    content_hash=item["content_hash"],
                                    storage_uri=item["storage_uri"],
                                    media_type=item["media_type"],
                                    size_bytes=item["size_bytes"],
                                    payload=(
                                        dict(item["payload"])
                                        if item["payload"] is not None
                                        else None
                                    ),
                                    created_at=stamp,
                                )
                            )
                        elif (
                            artifact.artifact_kind != item["artifact_kind"]
                            or artifact.content_hash != item["content_hash"]
                        ):
                            raise ArtifactConflict(
                                "immutable initial artifact identity changed",
                                artifact_id=item["artifact_id"],
                            )
                        self._validate_artifact_link(
                            item["artifact_id"],
                            row.recognition_run_id,
                            is_exclusive=item["is_exclusive"],
                        )
                        self.db.add(
                            DocumentRunArtifact(
                                recognition_run_id=row.recognition_run_id,
                                artifact_kind=item["artifact_kind"],
                                revision=1,
                                artifact_id=item["artifact_id"],
                                content_hash=item["content_hash"],
                                status="ready",
                                event_head=1,
                                is_exclusive=item["is_exclusive"],
                                created_at=stamp,
                            )
                        )
                        self.db.add(
                            DocumentRunArtifactHead(
                                recognition_run_id=row.recognition_run_id,
                                artifact_kind=item["artifact_kind"],
                                revision=1,
                                artifact_id=item["artifact_id"],
                                content_hash=item["content_hash"],
                                status="ready",
                                event_head=1,
                                updated_at=stamp,
                            )
                        )
                    self.db.add(
                        DocumentRecognitionEvent(
                            recognition_run_id=row.recognition_run_id,
                            sequence=1,
                            event_key=str(source.get("event_key") or "run:queued"),
                            event_type="run_state",
                            payload_hash=content_hash(source_event_payload),
                            payload=source_event_payload,
                            created_at=stamp,
                        )
                    )
                self.db.flush()
        except IntegrityError as exc:
            # A concurrent creator may have won the owner/request unique key.
            existing = self.db.scalar(
                select(DocumentAnalysisRun).where(
                    DocumentAnalysisRun.owner_id == owner_id,
                    DocumentAnalysisRun.request_key == request_key,
                )
            )
            if existing is None:
                raise
            if existing.request_hash != digest:
                raise IdempotencyConflict(
                    "request key was concurrently used for different input",
                    owner_id=owner_id,
                    request_key=request_key,
                ) from exc
            return existing, False
        return row, True

    def create_run_with_source(
        self,
        *,
        owner_id: str,
        request_key: str,
        filename: str,
        document_hash: str,
        root_class_iri: str,
        source_artifact_id: str,
        source_storage_uri: str,
        source_media_type: str,
        source_size_bytes: int,
        request_hash: str | None = None,
        root_class_label: str = "",
        ontology_snapshot_hash: str = "",
        metadata_mode: str = "generate_summary",
        ranking_budget_enabled: bool = True,
        scope_mode: str = "document_graph",
        focus_path: list[str] | None = None,
        provisional_fingerprint: str | None = None,
        recognition_run_id: UUID | str | None = None,
        contract_version: str = "document-analysis-runs-v1",
        progress: dict[str, Any] | None = None,
        source_payload: dict[str, Any] | None = None,
        source_is_exclusive: bool = True,
        ontology_artifact_id: str | None = None,
        ontology_storage_uri: str | None = None,
        ontology_media_type: str = "application/json",
        ontology_size_bytes: int | None = None,
        ontology_payload: dict[str, Any] | None = None,
        ontology_is_exclusive: bool = True,
        queued_event_key: str = "run:queued",
        queued_event_payload: dict[str, Any] | None = None,
    ) -> tuple[DocumentAnalysisRun, bool]:
        """Atomically create a run, ready source head, and initial queued event."""

        return self.create_run(
            owner_id=owner_id,
            request_key=request_key,
            filename=filename,
            document_hash=document_hash,
            root_class_iri=root_class_iri,
            request_hash=request_hash,
            root_class_label=root_class_label,
            ontology_snapshot_hash=ontology_snapshot_hash,
            metadata_mode=metadata_mode,
            ranking_budget_enabled=ranking_budget_enabled,
            scope_mode=scope_mode,
            focus_path=focus_path,
            provisional_fingerprint=provisional_fingerprint,
            recognition_run_id=recognition_run_id,
            contract_version=contract_version,
            progress=progress,
            source={
                "artifact_id": source_artifact_id,
                "content_hash": document_hash,
                "storage_uri": source_storage_uri,
                "media_type": source_media_type,
                "size_bytes": source_size_bytes,
                "payload": source_payload,
                "is_exclusive": source_is_exclusive,
                "event_key": queued_event_key,
                "event_payload": queued_event_payload,
                "ontology_artifact": (
                    {
                        "artifact_id": ontology_artifact_id,
                        "content_hash": ontology_snapshot_hash,
                        "storage_uri": ontology_storage_uri,
                        "media_type": ontology_media_type,
                        "size_bytes": ontology_size_bytes,
                        "payload": ontology_payload,
                        "is_exclusive": ontology_is_exclusive,
                    }
                    if ontology_artifact_id is not None
                    else None
                ),
            },
        )

    def list_owned(
        self, owner_id: str, *, limit: int = 20, offset: int = 0
    ) -> tuple[list[DocumentAnalysisRun], bool]:
        """List retained online runs without claiming work or cleaning artifacts."""

        if not 1 <= limit <= 100 or offset < 0:
            raise ValueError("limit must be 1-100 and offset must be nonnegative")
        rows = list(
            self.db.scalars(
                select(DocumentAnalysisRun)
                .where(
                    DocumentAnalysisRun.owner_id == owner_id,
                    DocumentAnalysisRun.scope_mode == "document_graph",
                    DocumentAnalysisRun.deletion_state != "deleted",
                    DocumentAnalysisRun.execution_status.not_in(["deleted", "expired"]),
                    or_(
                        DocumentAnalysisRun.expires_at.is_(None),
                        DocumentAnalysisRun.expires_at > self._clock(),
                    ),
                )
                .order_by(
                    DocumentAnalysisRun.created_at.desc(),
                    DocumentAnalysisRun.recognition_run_id.desc(),
                )
                .offset(offset)
                .limit(limit + 1)
                .execution_options(populate_existing=True)
            )
        )
        return rows[:limit], len(rows) > limit

    def get_owned(
        self,
        recognition_run_id: UUID | str,
        owner_id: str,
        *,
        include_deleted: bool = False,
    ) -> DocumentAnalysisRun:
        """Read an owner-scoped run without creating work or changing a lease."""

        row = self.db.scalar(
            select(DocumentAnalysisRun)
            .where(
                DocumentAnalysisRun.recognition_run_id == recognition_run_id,
                DocumentAnalysisRun.owner_id == owner_id,
            )
            .execution_options(populate_existing=True)
        )
        if row is None:
            tombstone = self.db.get(DocumentAnalysisTombstone, recognition_run_id)
            if tombstone is not None and hmac.compare_digest(
                tombstone.owner_hash, _owner_hash(owner_id)
            ):
                raise RunDeleted(run_id=str(recognition_run_id), final_state=tombstone.final_state)
            raise RunNotFound(run_id=str(recognition_run_id))
        if row.deletion_state == "deleted" and not include_deleted:
            raise RunDeleted(run_id=str(recognition_run_id))
        return row

    def get_tombstone(
        self, recognition_run_id: UUID | str, owner_id: str
    ) -> DocumentAnalysisTombstone:
        row = self.db.get(DocumentAnalysisTombstone, recognition_run_id)
        if row is None or not hmac.compare_digest(row.owner_hash, _owner_hash(owner_id)):
            raise RunNotFound(run_id=str(recognition_run_id))
        return row

    def replay_tombstone_delete_result(
        self,
        recognition_run_id: UUID | str,
        owner_id: str,
        *,
        expected_revision: int,
        request_key: str,
        reason: str | None = None,
    ) -> dict[str, Any] | None:
        """Replay an authenticated public DELETE after the run rows are gone.

        ``None`` means no tombstone exists and lets the caller continue through
        the ordinary live-run path.  A foreign owner is deliberately
        indistinguishable from a missing run.  Tombstones without a public
        delete receipt (for example retention expiry) remain ordinary 410s.
        """

        marker = self.db.get(DocumentAnalysisTombstone, recognition_run_id)
        if marker is None:
            return None
        if not hmac.compare_digest(marker.owner_hash, _owner_hash(owner_id)):
            raise RunNotFound(run_id=str(recognition_run_id))
        # request_key is a public idempotency identifier, not a secret.  Plain
        # equality also supports the non-ASCII values accepted by the schema;
        # hmac.compare_digest(str, str) raises TypeError for such values.
        if marker.delete_request_key is None or marker.delete_request_key != request_key:
            raise RunDeleted(run_id=str(recognition_run_id), final_state=marker.final_state)
        request_digest = control_request_hash(
            action="delete",
            expected_revision=expected_revision,
            expected_version=None,
            reason=reason,
        )
        if marker.delete_request_hash is None or not hmac.compare_digest(
            marker.delete_request_hash, request_digest
        ):
            raise IdempotencyConflict(
                "control request_key was already used for different content",
                action="delete",
                request_key=request_key,
            )
        return self._verified_delete_result_payload(
            marker.recognition_run_id,
            marker.delete_result_payload,
            marker.delete_result_payload_hash,
        )

    def claim(
        self,
        recognition_run_id: UUID | str,
        owner_id: str,
        *,
        actor: str,
        worker_id: str,
        lease_seconds: int = 120,
    ) -> str:
        """Atomically issue a new secret token for a queued or expired run."""

        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        stamp = self._clock()
        token = secrets.token_urlsafe(32)
        digest = _token_hash(token)
        with self.db.begin_nested():
            self.get_owned(recognition_run_id, owner_id)
            execution = self._execution_for_update(recognition_run_id)
            run = self._run_for_update(recognition_run_id, owner_id)
            stamp = self._clock()
            if run.deletion_state != "none" or run.execution_status not in {
                "queued",
                "running",
                "pausing",
            }:
                raise InvalidRunState(
                    "run cannot be claimed",
                    status=run.execution_status,
                    deletion_state=run.deletion_state,
                )
            lease_expiry = _aware(execution.lease_expires_at)
            available = execution.execution_token_hash is None or (
                lease_expiry is not None and lease_expiry <= stamp
            )
            if not available:
                raise LeaseBusy(run_id=str(recognition_run_id))
            generation = execution.generation
            progress_values = self._progress_watermark(run, execution)
            if run.execution_status == "queued":
                # Queue delay is not execution failure. An explicit resume also
                # starts one new window; automatic replacement remains running.
                progress_values.update(last_progress_at=stamp, recovery_attempts=0,
                                       recovery_event_head=run.event_head)
            elif not progress_values and run.execution_status in {"running", "pausing"}:
                progress_values["recovery_attempts"] = execution.recovery_attempts + 1
            changed = self.db.execute(
                update(DocumentAnalysisExecution)
                .where(
                    DocumentAnalysisExecution.recognition_run_id == recognition_run_id,
                    DocumentAnalysisExecution.generation == generation,
                    or_(
                        DocumentAnalysisExecution.execution_token_hash.is_(None),
                        DocumentAnalysisExecution.lease_expires_at <= stamp,
                    ),
                )
                .values(
                    execution_token_hash=digest,
                    generation=generation + 1,
                    status=ACTIVE_LEASE_STATUS,
                    actor=actor,
                    worker_id=worker_id,
                    lease_expires_at=stamp + timedelta(seconds=lease_seconds),
                    heartbeat_at=stamp,
                    pause_requested=run.execution_status == "pausing",
                    cancel_requested=False,
                    updated_at=stamp,
                    **progress_values,
                )
                .execution_options(synchronize_session=False)
            )
            if changed.rowcount != 1:
                raise LeaseBusy(run_id=str(recognition_run_id))
            values: dict[str, Any] = {
                "execution_status": ("pausing" if run.execution_status == "pausing" else "running"),
                "control_action": None,
            }
            if run.started_at is None:
                values["started_at"] = stamp
            self._cas_run(run, values)
        return token

    def claim_next(
        self,
        *,
        actor: str,
        worker_id: str,
        lease_seconds: int = 120,
    ) -> tuple[DocumentAnalysisRun, str] | None:
        """Atomically claim the oldest runnable queue head, if one exists.

        PostgreSQL dispatchers skip execution heads already locked by another
        dispatcher so independent processes can drain the queue concurrently.
        SQLite deliberately omits ``FOR UPDATE`` (it is unsupported there) and
        falls back to the generation/token compare-and-swap in :meth:`claim`.
        """

        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        stamp = self._clock()
        with self.db.begin_nested():
            statement = (
                select(
                    DocumentAnalysisExecution.recognition_run_id,
                    DocumentAnalysisRun.owner_id,
                )
                .join(
                    DocumentAnalysisRun,
                    DocumentAnalysisRun.recognition_run_id
                    == DocumentAnalysisExecution.recognition_run_id,
                )
                .where(
                    DocumentAnalysisRun.deletion_state == "none",
                    DocumentAnalysisRun.execution_status.in_({"queued", "running", "pausing"}),
                    or_(
                        DocumentAnalysisExecution.execution_token_hash.is_(None),
                        DocumentAnalysisExecution.lease_expires_at <= stamp,
                    ),
                )
                .order_by(
                    DocumentAnalysisRun.created_at,
                    DocumentAnalysisRun.recognition_run_id,
                )
                .limit(1)
            )
            bind = self.db.get_bind()
            if bind.dialect.name == "postgresql":
                statement = statement.with_for_update(
                    skip_locked=True,
                    of=DocumentAnalysisExecution,
                )
            candidate = self.db.execute(statement).first()
            if candidate is None:
                return None
            run_id, owner_id = candidate
            token = self.claim(
                run_id,
                owner_id,
                actor=actor,
                worker_id=worker_id,
                lease_seconds=lease_seconds,
            )
            return self.get_owned(run_id, owner_id), token

    def resume(
        self,
        recognition_run_id: UUID | str,
        owner_id: str,
        *,
        expected_revision: int,
        actor: str,
        worker_id: str,
        lease_seconds: int = 120,
    ) -> str:
        """CAS a paused/failed run back to queued and issue a fresh generation."""

        with self.db.begin_nested():
            self.request_control(
                recognition_run_id,
                owner_id,
                action="resume",
                expected_revision=expected_revision,
            )
            return self.claim(
                recognition_run_id,
                owner_id,
                actor=actor,
                worker_id=worker_id,
                lease_seconds=lease_seconds,
            )

    def assert_fence(
        self,
        recognition_run_id: UUID | str,
        owner_id: str,
        execution_token: str,
        *,
        for_update: bool = False,
    ) -> DocumentAnalysisExecution:
        """Reject missing, expired, revoked, cross-run, or stale-generation tokens."""

        if for_update:
            _run, execution = self._lock_fence(
                recognition_run_id, owner_id, execution_token, self._clock(),
            )
            # Waiting for another writer can cross the original lease deadline.
            self._check_fence(execution, execution_token, self._clock())
            return execution
        run = self.get_owned(recognition_run_id, owner_id)
        if run.deletion_state != "none":
            raise FenceViolation("run is being deleted", run_id=str(recognition_run_id))
        execution = self.db.get(
            DocumentAnalysisExecution,
            recognition_run_id,
            populate_existing=True,
        )
        self._check_fence(execution, execution_token, self._clock())
        return execution

    def heartbeat(
        self,
        recognition_run_id: UUID | str,
        owner_id: str,
        execution_token: str,
        *,
        lease_seconds: int = 120,
    ) -> DocumentAnalysisExecution:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        stamp = self._clock()
        with self.db.begin_nested():
            _run, execution = self._lock_fence(recognition_run_id, owner_id, execution_token, stamp)
            # Lock acquisition may outlive the lease. Never acknowledge a renewal
            # against a timestamp captured before waiting, or revive an expired owner.
            stamp = self._clock()
            self._check_fence(execution, execution_token, stamp)
            changed = self.db.execute(
                update(DocumentAnalysisExecution)
                .where(
                    DocumentAnalysisExecution.recognition_run_id == recognition_run_id,
                    DocumentAnalysisExecution.execution_token_hash
                    == execution.execution_token_hash,
                    DocumentAnalysisExecution.generation == execution.generation,
                    DocumentAnalysisExecution.status == ACTIVE_LEASE_STATUS,
                    DocumentAnalysisExecution.lease_expires_at > stamp,
                )
                .values(
                    heartbeat_at=stamp,
                    lease_expires_at=stamp + timedelta(seconds=lease_seconds),
                    updated_at=stamp,
                    **self._progress_watermark(_run, execution),
                )
                .execution_options(synchronize_session=False)
            )
            if changed.rowcount != 1:
                raise FenceViolation(run_id=str(recognition_run_id))
        return self.db.get(
            DocumentAnalysisExecution,
            recognition_run_id,
            populate_existing=True,
        )

    def append_event(
        self,
        recognition_run_id: UUID | str,
        owner_id: str,
        execution_token: str,
        *,
        expected_head: int,
        event_key: str,
        event_type: str,
        payload: dict[str, Any],
        payload_hash: str | None = None,
    ) -> DocumentRecognitionEvent:
        """Append exactly one event, assigning ``expected_head + 1``."""

        digest = self._validated_payload_hash(payload, payload_hash)
        try:
            with self.db.begin_nested():
                run, _execution = self._lock_fence(
                    recognition_run_id, owner_id, execution_token, self._clock()
                )
                existing = self.db.scalar(
                    select(DocumentRecognitionEvent).where(
                        DocumentRecognitionEvent.recognition_run_id == recognition_run_id,
                        DocumentRecognitionEvent.event_key == event_key,
                    )
                )
                if existing is not None:
                    if existing.event_type != event_type or existing.payload_hash != digest:
                        raise EventConflict(
                            "event_key was already used for different content",
                            event_key=event_key,
                        )
                    return existing
                if run.event_head != expected_head:
                    raise HeadConflict(
                        "stale event head", expected=expected_head, actual=run.event_head
                    )
                event = DocumentRecognitionEvent(
                    recognition_run_id=run.recognition_run_id,
                    sequence=expected_head + 1,
                    event_key=event_key,
                    event_type=event_type,
                    payload_hash=digest,
                    payload=dict(payload),
                    created_at=self._clock(),
                )
                self.db.add(event)
                self._cas_run(run, {"event_head": expected_head + 1})
                self.db.flush()
                return event
        except IntegrityError as exc:
            existing = self.db.scalar(
                select(DocumentRecognitionEvent).where(
                    DocumentRecognitionEvent.recognition_run_id == recognition_run_id,
                    DocumentRecognitionEvent.event_key == event_key,
                )
            )
            if (
                existing is not None
                and existing.event_type == event_type
                and existing.payload_hash == digest
            ):
                return existing
            raise EventConflict("concurrent event append", event_key=event_key) from exc

    def append_owner_event(
        self,
        recognition_run_id: UUID | str,
        owner_id: str,
        *,
        expected_head: int,
        event_key: str,
        event_type: str,
        payload: dict[str, Any],
        payload_hash: str | None = None,
    ) -> DocumentRecognitionEvent:
        """Append an authenticated owner/control event without a worker lease.

        Lifecycle operations can revoke the execution token in the same
        transaction, so their durable event cannot be appended through the
        worker-only method. Owner scoping and the event-head CAS still apply.
        """

        digest = self._validated_payload_hash(payload, payload_hash)
        try:
            with self.db.begin_nested():
                run = self._run_for_update(recognition_run_id, owner_id)
                existing = self.db.scalar(
                    select(DocumentRecognitionEvent).where(
                        DocumentRecognitionEvent.recognition_run_id == recognition_run_id,
                        DocumentRecognitionEvent.event_key == event_key,
                    )
                )
                if existing is not None:
                    if existing.event_type != event_type or existing.payload_hash != digest:
                        raise EventConflict(
                            "event_key was already used for different content",
                            event_key=event_key,
                        )
                    return existing
                if run.event_head != expected_head:
                    raise HeadConflict(
                        "stale event head", expected=expected_head, actual=run.event_head
                    )
                event = DocumentRecognitionEvent(
                    recognition_run_id=run.recognition_run_id,
                    sequence=expected_head + 1,
                    event_key=event_key,
                    event_type=event_type,
                    payload_hash=digest,
                    payload=dict(payload),
                    created_at=self._clock(),
                )
                self.db.add(event)
                self._cas_run(run, {"event_head": expected_head + 1})
                self.db.flush()
                return event
        except IntegrityError as exc:
            existing = self.db.scalar(
                select(DocumentRecognitionEvent).where(
                    DocumentRecognitionEvent.recognition_run_id == recognition_run_id,
                    DocumentRecognitionEvent.event_key == event_key,
                )
            )
            if (
                existing is not None
                and existing.event_type == event_type
                and existing.payload_hash == digest
            ):
                return existing
            raise EventConflict("concurrent owner event append", event_key=event_key) from exc

    def list_events(
        self,
        recognition_run_id: UUID | str,
        owner_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 500,
    ) -> list[DocumentRecognitionEvent]:
        self.get_owned(recognition_run_id, owner_id)
        if after_sequence < 0 or limit < 1:
            raise ValueError("invalid event cursor")
        return list(
            self.db.scalars(
                select(DocumentRecognitionEvent)
                .where(
                    DocumentRecognitionEvent.recognition_run_id == recognition_run_id,
                    DocumentRecognitionEvent.sequence > after_sequence,
                )
                .order_by(DocumentRecognitionEvent.sequence)
                .limit(limit)
            )
        )

    def event_batch_receipt(
        self,
        recognition_run_id: UUID | str,
        owner_id: str,
        execution_token: str,
        *,
        batch_id: str,
        batch_hash: str,
    ) -> DocumentRecognitionEventBatch | None:
        """Fence and resolve an already committed recognition batch, if any."""

        self.assert_fence(recognition_run_id, owner_id, execution_token)
        row = self.db.get(
            DocumentRecognitionEventBatch,
            (recognition_run_id, batch_id),
            populate_existing=True,
        )
        if row is not None and not hmac.compare_digest(row.content_hash, batch_hash):
            raise EventConflict(
                "batch_id was already used for different content",
                batch_id=batch_id,
            )
        return row

    def put_event_batch(
        self,
        recognition_run_id: UUID | str,
        owner_id: str,
        execution_token: str,
        *,
        batch_id: str,
        batch_hash: str,
        first_sequence: int,
        last_sequence: int,
        checkpoint_artifact_id: str,
    ) -> DocumentRecognitionEventBatch:
        """Append one immutable, fenced batch receipt in the caller transaction."""

        if not batch_id or len(batch_id) > 200:
            raise ValueError("batch_id must contain 1-200 characters")
        if first_sequence <= 0 or last_sequence < first_sequence:
            raise ValueError("invalid batch event range")
        with self.db.begin_nested():
            run, _execution = self._lock_fence(
                recognition_run_id, owner_id, execution_token, self._clock()
            )
            existing = self.db.get(
                DocumentRecognitionEventBatch,
                (run.recognition_run_id, batch_id),
            )
            if existing is not None:
                if not hmac.compare_digest(existing.content_hash, batch_hash):
                    raise EventConflict(
                        "batch_id was already used for different content",
                        batch_id=batch_id,
                    )
                return existing
            if last_sequence > run.event_head:
                raise HeadConflict(
                    "batch references an uncommitted event",
                    last_sequence=last_sequence,
                    event_head=run.event_head,
                )
            for sequence in {first_sequence, last_sequence}:
                if (
                    self.db.get(
                        DocumentRecognitionEvent,
                        (run.recognition_run_id, sequence),
                    )
                    is None
                ):
                    raise HeadConflict("batch event range is not owned by this run")
            checkpoint = self.db.get(DocumentAnalysisArtifact, checkpoint_artifact_id)
            if checkpoint is None or checkpoint.artifact_kind != "recognition_checkpoint":
                raise ArtifactConflict("batch checkpoint artifact is missing or invalid")
            row = DocumentRecognitionEventBatch(
                recognition_run_id=run.recognition_run_id,
                batch_id=batch_id,
                content_hash=batch_hash,
                first_sequence=first_sequence,
                last_sequence=last_sequence,
                checkpoint_artifact_id=checkpoint_artifact_id,
                created_at=self._clock(),
            )
            self.db.add(row)
            self.db.flush()
            return row

    def put_candidate(
        self,
        recognition_run_id: UUID | str,
        owner_id: str,
        execution_token: str,
        *,
        candidate_id: str,
        revision: int,
        kind: str,
        payload: dict[str, Any],
        proof_refs: list[Any] | None = None,
        expected_head_revision: int | None = None,
        payload_hash: str | None = None,
        event_sequence: int | None = None,
    ) -> DocumentRunCandidate:
        """Persist the caller's exact candidate revision; never renumber it to one."""

        if revision <= 0:
            raise ValueError("candidate revision must be positive")
        digest = self._validated_payload_hash(payload, payload_hash)
        refs = list(proof_refs or [])
        with self.db.begin_nested():
            run, _execution = self._lock_fence(
                recognition_run_id, owner_id, execution_token, self._clock()
            )
            existing = self.db.get(
                DocumentRunCandidate,
                (run.recognition_run_id, candidate_id, revision),
            )
            if existing is not None:
                if (
                    existing.kind != kind
                    or existing.payload_hash != digest
                    or existing.proof_refs != refs
                ):
                    raise HeadConflict("immutable candidate revision changed")
                return existing
            head = self.db.scalar(
                select(DocumentRunCandidateHead)
                .where(
                    DocumentRunCandidateHead.recognition_run_id == recognition_run_id,
                    DocumentRunCandidateHead.candidate_id == candidate_id,
                )
                .with_for_update()
            )
            actual_head = head.revision if head is not None else 0
            expected = 0 if expected_head_revision is None else expected_head_revision
            if actual_head != expected:
                raise HeadConflict("stale candidate head", expected=expected, actual=actual_head)
            if head is not None and revision <= head.revision:
                raise HeadConflict("candidate revision must advance its current head")
            self._require_event(run.recognition_run_id, event_sequence)
            row = DocumentRunCandidate(
                recognition_run_id=run.recognition_run_id,
                candidate_id=candidate_id,
                revision=revision,
                kind=kind,
                payload_hash=digest,
                payload=dict(payload),
                proof_refs=refs,
                event_sequence=event_sequence,
                created_at=self._clock(),
            )
            self.db.add(row)
            self.db.flush()
            if head is None:
                self.db.add(
                    DocumentRunCandidateHead(
                        recognition_run_id=run.recognition_run_id,
                        candidate_id=candidate_id,
                        revision=revision,
                        payload_hash=digest,
                        updated_at=self._clock(),
                    )
                )
            else:
                changed = self.db.execute(
                    update(DocumentRunCandidateHead)
                    .where(
                        DocumentRunCandidateHead.recognition_run_id == recognition_run_id,
                        DocumentRunCandidateHead.candidate_id == candidate_id,
                        DocumentRunCandidateHead.revision == expected,
                    )
                    .values(
                        revision=revision,
                        payload_hash=digest,
                        updated_at=self._clock(),
                    )
                    .execution_options(synchronize_session=False)
                )
                if changed.rowcount != 1:
                    raise HeadConflict("concurrent candidate head update")
            self._cas_run(run, {})
            self.db.flush()
            return row

    def put_proof(
        self,
        recognition_run_id: UUID | str,
        owner_id: str,
        execution_token: str,
        *,
        proof_id: str,
        proof_revision: int,
        target_id: str,
        payload: dict[str, Any],
        expected_head_revision: int | None = None,
        payload_hash: str | None = None,
        event_sequence: int | None = None,
    ) -> DocumentVerificationProof:
        """Persist an exact proof revision, scoped to one run and target."""

        if proof_revision <= 0:
            raise ValueError("proof revision must be positive")
        digest = self._validated_payload_hash(payload, payload_hash)
        with self.db.begin_nested():
            run, _execution = self._lock_fence(
                recognition_run_id, owner_id, execution_token, self._clock()
            )
            existing = self.db.get(
                DocumentVerificationProof,
                (run.recognition_run_id, proof_id, proof_revision),
            )
            if existing is not None:
                if existing.target_id != target_id or existing.payload_hash != digest:
                    raise HeadConflict("immutable proof revision changed")
                return existing
            head = self.db.scalar(
                select(DocumentVerificationProofHead)
                .where(
                    DocumentVerificationProofHead.recognition_run_id == recognition_run_id,
                    DocumentVerificationProofHead.proof_id == proof_id,
                )
                .with_for_update()
            )
            actual_head = head.proof_revision if head is not None else 0
            expected = 0 if expected_head_revision is None else expected_head_revision
            if actual_head != expected:
                raise HeadConflict("stale proof head", expected=expected, actual=actual_head)
            if head is not None and proof_revision <= head.proof_revision:
                raise HeadConflict("proof revision must advance its current head")
            self._require_event(run.recognition_run_id, event_sequence)
            row = DocumentVerificationProof(
                recognition_run_id=run.recognition_run_id,
                proof_id=proof_id,
                proof_revision=proof_revision,
                target_id=target_id,
                payload_hash=digest,
                payload=dict(payload),
                event_sequence=event_sequence,
                created_at=self._clock(),
            )
            self.db.add(row)
            self.db.flush()
            if head is None:
                self.db.add(
                    DocumentVerificationProofHead(
                        recognition_run_id=run.recognition_run_id,
                        proof_id=proof_id,
                        proof_revision=proof_revision,
                        payload_hash=digest,
                        updated_at=self._clock(),
                    )
                )
            else:
                changed = self.db.execute(
                    update(DocumentVerificationProofHead)
                    .where(
                        DocumentVerificationProofHead.recognition_run_id == recognition_run_id,
                        DocumentVerificationProofHead.proof_id == proof_id,
                        DocumentVerificationProofHead.proof_revision == expected,
                    )
                    .values(
                        proof_revision=proof_revision,
                        payload_hash=digest,
                        updated_at=self._clock(),
                    )
                    .execution_options(synchronize_session=False)
                )
                if changed.rowcount != 1:
                    raise HeadConflict("concurrent proof head update")
            self._cas_run(run, {})
            self.db.flush()
            return row

    def update_stage(
        self,
        recognition_run_id: UUID | str,
        owner_id: str,
        execution_token: str,
        *,
        expected_revision: int,
        stage: str,
        execution_status: str | None = None,
        progress: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
        analysis_id: str | None = None,
        metadata_snapshot_id: str | None = None,
        graph_snapshot_id: str | None = None,
        run_fingerprint: str | None = None,
        stop_reason: str | None = None,
        expires_at: datetime | None = None,
    ) -> DocumentAnalysisRun:
        """CAS the run head after a fenced stage transition."""

        if not stage:
            raise ValueError("stage is required")
        with self.db.begin_nested():
            run, execution = self._lock_fence(
                recognition_run_id, owner_id, execution_token, self._clock()
            )
            if run.revision != expected_revision:
                raise HeadConflict(
                    "stale run revision", expected=expected_revision, actual=run.revision
                )
            values: dict[str, Any] = {"stage": stage}
            if execution_status is not None:
                values["execution_status"] = execution_status
            if progress is not None:
                values["progress"] = dict(progress)
            if error is not None:
                values["error"] = dict(error)
            if analysis_id is not None:
                values["analysis_id"] = analysis_id
            if metadata_snapshot_id is not None:
                values["metadata_snapshot_id"] = metadata_snapshot_id
            if graph_snapshot_id is not None:
                values["graph_snapshot_id"] = graph_snapshot_id
            if run_fingerprint is not None:
                values["run_fingerprint"] = run_fingerprint
            if stop_reason is not None:
                values["stop_reason"] = stop_reason
            if expires_at is not None:
                values["expires_at"] = expires_at
            self._validate_distinct_ids(run, values)
            if execution_status in LEASE_RELEASING_EXECUTION_STATUSES:
                values["finished_at"] = self._clock()
            updated = self._cas_run(run, values)
            if execution_status in LEASE_RELEASING_EXECUTION_STATUSES:
                self._revoke_execution(execution, cancel=execution_status == "cancelled")
            return updated

    def update_artifact(
        self,
        recognition_run_id: UUID | str,
        owner_id: str,
        execution_token: str,
        *,
        artifact_kind: str,
        expected_revision: int,
        artifact_hash: str,
        status: str,
        artifact_id: str | None = None,
        storage_uri: str | None = None,
        media_type: str | None = None,
        size_bytes: int | None = None,
        payload: dict[str, Any] | None = None,
        event_head: int | None = None,
        is_exclusive: bool = True,
        analysis_id: str | None = None,
        metadata_snapshot_id: str | None = None,
        graph_snapshot_id: str | None = None,
    ) -> DocumentRunArtifact:
        """CAS an artifact head while retaining every immutable prior revision."""

        if expected_revision < 0:
            raise ValueError("expected artifact revision cannot be negative")
        identity = artifact_id or uuid4().hex
        with self.db.begin_nested():
            run, _execution = self._lock_fence(
                recognition_run_id, owner_id, execution_token, self._clock()
            )
            head = self.db.scalar(
                select(DocumentRunArtifactHead)
                .where(
                    DocumentRunArtifactHead.recognition_run_id == recognition_run_id,
                    DocumentRunArtifactHead.artifact_kind == artifact_kind,
                )
                .with_for_update()
            )
            if (
                head is not None
                and head.artifact_id == identity
                and head.content_hash == artifact_hash
                and head.status == status
            ):
                return self.db.get(
                    DocumentRunArtifact,
                    (run.recognition_run_id, artifact_kind, head.revision),
                )
            actual = head.revision if head is not None else 0
            if actual != expected_revision:
                raise ArtifactConflict(
                    "stale artifact head", expected=expected_revision, actual=actual
                )
            durable_event_head = run.event_head if event_head is None else event_head
            if durable_event_head < 0 or durable_event_head > run.event_head:
                raise ArtifactConflict(
                    "artifact cannot reference an uncommitted event head",
                    event_head=durable_event_head,
                    run_event_head=run.event_head,
                )
            artifact = self.db.get(DocumentAnalysisArtifact, identity)
            if artifact is None:
                artifact = DocumentAnalysisArtifact(
                    artifact_id=identity,
                    artifact_kind=artifact_kind,
                    content_hash=artifact_hash,
                    storage_uri=storage_uri,
                    media_type=media_type,
                    size_bytes=size_bytes,
                    payload=dict(payload) if payload is not None else None,
                    created_at=self._clock(),
                )
                self.db.add(artifact)
                self.db.flush()
            elif (
                artifact.artifact_kind != artifact_kind
                or artifact.content_hash != artifact_hash
                or (storage_uri is not None and artifact.storage_uri != storage_uri)
                or (payload is not None and artifact.payload != payload)
            ):
                raise ArtifactConflict("immutable artifact identity changed", artifact_id=identity)
            self._validate_artifact_link(
                identity, run.recognition_run_id, is_exclusive=is_exclusive
            )

            revision = expected_revision + 1
            row = DocumentRunArtifact(
                recognition_run_id=run.recognition_run_id,
                artifact_kind=artifact_kind,
                revision=revision,
                artifact_id=identity,
                content_hash=artifact_hash,
                status=status,
                event_head=durable_event_head,
                is_exclusive=is_exclusive,
                created_at=self._clock(),
            )
            self.db.add(row)
            self.db.flush()
            if head is None:
                self.db.add(
                    DocumentRunArtifactHead(
                        recognition_run_id=run.recognition_run_id,
                        artifact_kind=artifact_kind,
                        revision=revision,
                        artifact_id=identity,
                        content_hash=artifact_hash,
                        status=status,
                        event_head=durable_event_head,
                        updated_at=self._clock(),
                    )
                )
            else:
                changed = self.db.execute(
                    update(DocumentRunArtifactHead)
                    .where(
                        DocumentRunArtifactHead.recognition_run_id == recognition_run_id,
                        DocumentRunArtifactHead.artifact_kind == artifact_kind,
                        DocumentRunArtifactHead.revision == expected_revision,
                    )
                    .values(
                        revision=revision,
                        artifact_id=identity,
                        content_hash=artifact_hash,
                        status=status,
                        event_head=durable_event_head,
                        updated_at=self._clock(),
                    )
                    .execution_options(synchronize_session=False)
                )
                if changed.rowcount != 1:
                    raise ArtifactConflict("concurrent artifact head update")
            manifest = dict(run.artifact_manifest or {})
            manifest[artifact_kind] = {
                "artifact_id": identity,
                "hash": artifact_hash,
                "revision": revision,
                "status": status,
                "event_head": durable_event_head,
            }
            values: dict[str, Any] = {
                "artifact_manifest": manifest,
                "artifact_revision": run.artifact_revision + 1,
            }
            if analysis_id is not None:
                values["analysis_id"] = analysis_id
            if metadata_snapshot_id is not None:
                values["metadata_snapshot_id"] = metadata_snapshot_id
            if graph_snapshot_id is not None:
                values["graph_snapshot_id"] = graph_snapshot_id
            self._validate_distinct_ids(run, values)
            self._cas_run(run, values)
            self.db.flush()
            return row

    def get_artifact_head(
        self,
        recognition_run_id: UUID | str,
        owner_id: str,
        artifact_kind: str,
    ) -> DocumentRunArtifactHead | None:
        self.get_owned(recognition_run_id, owner_id)
        return self.db.get(DocumentRunArtifactHead, (recognition_run_id, artifact_kind))

    def get_artifact(
        self,
        recognition_run_id: UUID | str,
        owner_id: str,
        artifact_kind: str,
        *,
        revision: int | None = None,
    ) -> DocumentRunArtifact | None:
        self.get_owned(recognition_run_id, owner_id)
        if revision is None:
            head = self.get_artifact_head(recognition_run_id, owner_id, artifact_kind)
            if head is None:
                return None
            revision = head.revision
        return self.db.get(
            DocumentRunArtifact,
            (recognition_run_id, artifact_kind, revision),
        )

    def request_control(
        self,
        recognition_run_id: UUID | str,
        owner_id: str,
        *,
        action: str,
        expected_revision: int | None = None,
        expected_version: int | None = None,
        request_key: str | None = None,
        reason: str | None = None,
        expires_at: datetime | None = None,
        include_receipt: bool = False,
    ) -> (
        DocumentAnalysisRun
        | tuple[
            DocumentAnalysisRun,
            DocumentAnalysisControlOperation | None,
            bool,
        ]
    ):
        """Apply lifecycle/budget controls with a run or control-version CAS."""

        if expected_revision is None and expected_version is None:
            raise ValueError("an expected run or control version is required")
        if request_key is not None and (not request_key or len(request_key) > 200):
            raise ValueError("control request_key must contain 1-200 characters")
        if reason is not None and len(reason) > 4000:
            raise ValueError("control reason cannot exceed 4000 characters")
        operation_hash = control_request_hash(
            action=action,
            expected_revision=expected_revision,
            expected_version=expected_version,
            reason=reason,
        )
        with self.db.begin_nested():
            self.get_owned(recognition_run_id, owner_id)
            execution = self._execution_for_update(recognition_run_id)
            run = self._run_for_update(recognition_run_id, owner_id)
            if request_key is not None:
                receipt = self.db.get(
                    DocumentAnalysisControlOperation,
                    (run.recognition_run_id, action, request_key),
                )
                if receipt is not None:
                    if not hmac.compare_digest(receipt.request_hash, operation_hash):
                        raise IdempotencyConflict(
                            "control request_key was already used for different content",
                            action=action,
                            request_key=request_key,
                        )
                    if include_receipt:
                        return run, receipt, True
                    return run
            if expected_revision is not None and run.revision != expected_revision:
                raise HeadConflict(
                    "stale run revision", expected=expected_revision, actual=run.revision
                )
            if expected_version is not None and run.control_version != expected_version:
                raise HeadConflict(
                    "stale control version",
                    expected=expected_version,
                    actual=run.control_version,
                )
            if run.deletion_state != "none" and action != "delete":
                raise InvalidRunState("a deleting run cannot be controlled")
            stamp = self._clock()
            values: dict[str, Any] = {
                "control_action": action,
                "control_version": run.control_version + 1,
            }
            if action == "pause":
                if run.execution_status not in {"queued", "running"}:
                    raise InvalidRunState("only queued/running runs can be paused")
                if run.execution_status == "queued":
                    values.update(
                        execution_status="paused",
                        paused_at=stamp,
                        expires_at=expires_at,
                    )
                    self._revoke_execution(execution)
                else:
                    values["execution_status"] = "pausing"
                    execution.pause_requested = True
                    execution.updated_at = stamp
            elif action == "resume":
                if run.execution_status not in {"paused", "failed"}:
                    raise InvalidRunState("only paused/failed runs can be resumed")
                if run.expires_at is not None and _aware(run.expires_at) <= stamp:
                    raise InvalidRunState("an expired run cannot be resumed")
                values.update(
                    execution_status="queued",
                    paused_at=None,
                    expires_at=None,
                    stop_reason=None,
                    error=None,
                )
                self._revoke_execution(execution)
                execution.pause_requested = False
                execution.cancel_requested = False
                execution.recovery_attempts = 0
                execution.last_progress_at = stamp
                execution.recovery_event_head = run.event_head
            elif action in {"ranking_budget_enable", "ranking_budget_disable"}:
                if run.execution_status not in {"paused", "failed"}:
                    raise InvalidRunState("only paused/failed runs can change ranking budgets")
                if run.expires_at is not None and _aware(run.expires_at) <= stamp:
                    raise InvalidRunState("an expired run cannot change ranking budgets")
                values["ranking_budget_enabled"] = action == "ranking_budget_enable"
                self._revoke_execution(execution)
            elif action == "cancel":
                if run.execution_status in TERMINAL_EXECUTION_STATUSES:
                    raise InvalidRunState("terminal run cannot be cancelled again")
                values.update(
                    execution_status="cancelled",
                    stop_reason=reason or "operator_cancel",
                    finished_at=stamp,
                    expires_at=expires_at,
                )
                self._revoke_execution(execution, cancel=True)
            elif action == "delete":
                if run.deletion_state == "deleted":
                    raise RunDeleted(run_id=str(recognition_run_id))
                if run.deletion_state != "none":
                    raise InvalidRunState("deletion was already requested")
                values["deletion_state"] = "requested"
                self._revoke_execution(execution, cancel=True)
            else:
                raise ValueError(f"unknown control action: {action}")
            updated = self._cas_run(run, values)
            receipt = None
            if request_key is not None:
                receipt = DocumentAnalysisControlOperation(
                    recognition_run_id=run.recognition_run_id,
                    action=action,
                    request_key=request_key,
                    request_hash=operation_hash,
                    expected_revision=(
                        expected_revision if expected_revision is not None else run.revision - 1
                    ),
                    result_revision=updated.revision,
                    reason=reason,
                    created_at=stamp,
                )
                self.db.add(receipt)
            self.db.flush()
            if include_receipt:
                return updated, receipt, False
            return updated

    def freeze_control_result(
        self,
        receipt: DocumentAnalysisControlOperation,
        *,
        result_revision: int,
        payload: dict[str, Any],
    ) -> None:
        """Freeze the exact public response returned for an idempotent operation."""

        frozen = dict(payload)
        digest = content_hash(frozen)
        if receipt.result_payload is not None:
            if (
                receipt.result_revision != result_revision
                or receipt.result_payload_hash is None
                or not hmac.compare_digest(receipt.result_payload_hash, digest)
            ):
                raise IdempotencyConflict(
                    "control receipt was already frozen with a different result",
                    action=receipt.action,
                    request_key=receipt.request_key,
                )
            return
        receipt.result_revision = result_revision
        receipt.result_payload = frozen
        receipt.result_payload_hash = digest
        self.db.flush()

    @staticmethod
    def replay_control_result(receipt: DocumentAnalysisControlOperation) -> dict[str, Any]:
        """Return a verified copy of a previously frozen public response."""

        payload = receipt.result_payload
        digest = receipt.result_payload_hash
        if payload is None or digest is None:
            raise InvalidRunState("control receipt has no frozen result")
        restored = dict(payload)
        if not hmac.compare_digest(digest, content_hash(restored)):
            raise InvalidRunState("control receipt result failed integrity verification")
        return restored

    @staticmethod
    def _verified_delete_result_payload(
        recognition_run_id: UUID | str,
        payload: dict[str, Any] | None,
        digest: str | None,
    ) -> dict[str, Any]:
        """Verify that a tombstone retained only a safe DELETE acknowledgement."""

        if payload is None or digest is None:
            raise InvalidRunState("delete tombstone has no frozen result")
        restored = dict(payload)
        if not hmac.compare_digest(digest, content_hash(restored)):
            raise InvalidRunState("delete tombstone result failed integrity verification")
        if (
            set(restored) - {"ranking_budget_enabled"} != _DELETE_CONTROL_RESULT_FIELDS
            or (
                "ranking_budget_enabled" in restored
                and not isinstance(restored["ranking_budget_enabled"], bool)
            )
        ):
            raise InvalidRunState("delete tombstone result contains unsafe fields")
        if (
            restored.get("recognition_run_id") != str(recognition_run_id)
            or restored.get("operation") != "delete"
            or restored.get("operation_status") != "accepted"
        ):
            raise InvalidRunState("delete tombstone result does not identify this operation")
        if not isinstance(restored.get("available_actions"), list):
            raise InvalidRunState("delete tombstone result has invalid actions")
        return restored

    def _preserve_delete_control_receipt(self, marker: DocumentAnalysisTombstone) -> None:
        """Copy the sole safe public DELETE receipt before cascade cleanup."""

        receipt = self.db.scalar(
            select(DocumentAnalysisControlOperation).where(
                DocumentAnalysisControlOperation.recognition_run_id == marker.recognition_run_id,
                DocumentAnalysisControlOperation.action == "delete",
            )
        )
        if receipt is None:
            return
        # Internal retention callers may request deletion without producing a
        # public response.  Such cleanup keeps a plain tombstone and exposes no
        # replay contract.  A partially frozen receipt is corruption and must
        # stop cleanup before the run-owned rows are removed.
        if receipt.result_payload is None and receipt.result_payload_hash is None:
            return
        if receipt.result_payload is None or receipt.result_payload_hash is None:
            raise InvalidRunState("delete control receipt is only partially frozen")
        payload = self.replay_control_result(receipt)
        self._verified_delete_result_payload(
            marker.recognition_run_id,
            payload,
            receipt.result_payload_hash,
        )

        existing_values = (
            marker.delete_request_key,
            marker.delete_request_hash,
            marker.delete_result_payload_hash,
            marker.delete_result_payload,
        )
        if any(value is not None for value in existing_values):
            if any(value is None for value in existing_values):
                raise InvalidRunState("delete tombstone receipt is incomplete")
            if (
                marker.delete_request_key != receipt.request_key
                or marker.delete_request_hash != receipt.request_hash
                or marker.delete_result_payload_hash != receipt.result_payload_hash
                or marker.delete_result_payload != payload
            ):
                raise InvalidRunState("delete tombstone receipt cannot be rewritten")
            return
        marker.delete_request_key = receipt.request_key
        marker.delete_request_hash = receipt.request_hash
        marker.delete_result_payload_hash = receipt.result_payload_hash
        marker.delete_result_payload = payload

    def complete_pause(
        self,
        recognition_run_id: UUID | str,
        owner_id: str,
        execution_token: str,
        *,
        expected_revision: int,
        expires_at: datetime | None = None,
    ) -> DocumentAnalysisRun:
        """A worker acknowledges pause only after its last safe batch commits."""

        with self.db.begin_nested():
            run, execution = self._lock_fence(
                recognition_run_id, owner_id, execution_token, self._clock()
            )
            if run.revision != expected_revision:
                raise HeadConflict(
                    "stale run revision", expected=expected_revision, actual=run.revision
                )
            if run.execution_status != "pausing" or not execution.pause_requested:
                raise InvalidRunState("pause was not requested")
            updated = self._cas_run(
                run,
                {
                    "execution_status": "paused",
                    "paused_at": self._clock(),
                    "expires_at": expires_at,
                    "stop_reason": "operator_pause",
                },
            )
            self._revoke_execution(execution)
            self.db.flush()
            return updated

    def mark_deleting(
        self,
        recognition_run_id: UUID | str,
        owner_id: str,
        *,
        expected_revision: int,
    ) -> DocumentAnalysisRun:
        """Advance deletion state only; this method never deletes rows or files."""

        with self.db.begin_nested():
            self.get_owned(recognition_run_id, owner_id)
            execution = self._execution_for_update(recognition_run_id)
            run = self._run_for_update(recognition_run_id, owner_id)
            if run.revision != expected_revision:
                raise HeadConflict(
                    "stale run revision", expected=expected_revision, actual=run.revision
                )
            if run.deletion_state != "requested":
                raise InvalidRunState("deletion must be requested first")
            self._revoke_execution(execution, cancel=True)
            return self._cas_run(run, {"deletion_state": "deleting"})

    def tombstone(
        self,
        recognition_run_id: UUID | str,
        owner_id: str,
        *,
        expected_revision: int,
        reason: str | None = None,
        final_state: str = "deleted",
    ) -> DocumentAnalysisTombstone:
        """Record a minimal tombstone and fence the run; no physical delete occurs."""

        if final_state not in {"deleted", "expired"}:
            raise ValueError("final_state must be deleted or expired")
        existing = self.db.get(DocumentAnalysisTombstone, recognition_run_id)
        if existing is not None:
            if not hmac.compare_digest(existing.owner_hash, _owner_hash(owner_id)):
                raise RunNotFound(run_id=str(recognition_run_id))
            self._preserve_delete_control_receipt(existing)
            self.db.flush()
            return existing
        with self.db.begin_nested():
            run = self.get_owned(recognition_run_id, owner_id, include_deleted=True)
            execution = self._execution_for_update(recognition_run_id)
            run = self._run_for_update(recognition_run_id, owner_id, include_deleted=True)
            if run.revision != expected_revision:
                raise HeadConflict(
                    "stale run revision", expected=expected_revision, actual=run.revision
                )
            if run.deletion_state not in {"requested", "deleting"}:
                raise InvalidRunState("run is not in the deletion workflow")
            self._revoke_execution(execution, cancel=True)
            stamp = self._clock()
            marker = DocumentAnalysisTombstone(
                recognition_run_id=run.recognition_run_id,
                owner_hash=_owner_hash(owner_id),
                token_generation=execution.generation,
                final_state=final_state,
                reason=reason,
                deleted_at=stamp,
            )
            self.db.add(marker)
            self._preserve_delete_control_receipt(marker)
            self._cas_run(
                run,
                {
                    "deletion_state": "deleted",
                    "deleted_at": stamp,
                    "control_action": "delete",
                },
                allow_deleted=True,
            )
            self.db.flush()
            return marker

    def _run_for_update(
        self,
        recognition_run_id: UUID | str,
        owner_id: str,
        *,
        include_deleted: bool = False,
    ) -> DocumentAnalysisRun:
        row = self.db.scalar(
            select(DocumentAnalysisRun)
            .where(
                DocumentAnalysisRun.recognition_run_id == recognition_run_id,
                DocumentAnalysisRun.owner_id == owner_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if row is None:
            raise RunNotFound(run_id=str(recognition_run_id))
        if row.deletion_state == "deleted" and not include_deleted:
            raise RunDeleted(run_id=str(recognition_run_id))
        return row

    def _execution_for_update(self, recognition_run_id: UUID | str) -> DocumentAnalysisExecution:
        row = self.db.scalar(
            select(DocumentAnalysisExecution)
            .where(DocumentAnalysisExecution.recognition_run_id == recognition_run_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if row is None:
            raise InvalidRunState("run has no execution head")
        return row

    def _lock_fence(
        self,
        recognition_run_id: UUID | str,
        owner_id: str,
        execution_token: str,
        stamp: datetime,
    ) -> tuple[DocumentAnalysisRun, DocumentAnalysisExecution]:
        self.check_publication_deadline()
        self.get_owned(recognition_run_id, owner_id)
        execution = self._execution_for_update(recognition_run_id)
        self._check_fence(execution, execution_token, stamp)
        run = self._run_for_update(recognition_run_id, owner_id)
        self._check_fence(execution, execution_token, self._clock())
        if run.deletion_state != "none":
            raise FenceViolation("run is being deleted", run_id=str(recognition_run_id))
        return run, execution

    @staticmethod
    def _check_fence(
        execution: DocumentAnalysisExecution | None,
        execution_token: str,
        stamp: datetime,
    ) -> None:
        if execution is None or not execution_token or not execution.execution_token_hash:
            raise FenceViolation("missing execution lease")
        expiry = _aware(execution.lease_expires_at)
        if (
            execution.status != ACTIVE_LEASE_STATUS
            or expiry is None
            or expiry <= stamp
            or not hmac.compare_digest(execution.execution_token_hash, _token_hash(execution_token))
        ):
            raise FenceViolation("execution lease is stale or expired")

    def _cas_run(
        self,
        run: DocumentAnalysisRun,
        values: dict[str, Any],
        *,
        allow_deleted: bool = False,
    ) -> DocumentAnalysisRun:
        expected = run.revision
        clauses = [
            DocumentAnalysisRun.recognition_run_id == run.recognition_run_id,
            DocumentAnalysisRun.owner_id == run.owner_id,
            DocumentAnalysisRun.revision == expected,
        ]
        if not allow_deleted:
            clauses.append(DocumentAnalysisRun.deletion_state != "deleted")
        changed = self.db.execute(
            update(DocumentAnalysisRun)
            .where(*clauses)
            .values(**values, revision=expected + 1, updated_at=self._clock())
            .execution_options(synchronize_session=False)
        )
        if changed.rowcount != 1:
            raise HeadConflict("concurrent run head update", expected=expected)
        self.db.expire(run)
        self.db.refresh(run)
        return run

    def _revoke_execution(
        self, execution: DocumentAnalysisExecution, *, cancel: bool = False
    ) -> None:
        execution.execution_token_hash = None
        execution.generation += 1
        execution.status = "revoked"
        execution.lease_expires_at = self._clock()
        execution.heartbeat_at = self._clock()
        execution.cancel_requested = cancel
        execution.updated_at = self._clock()

    def _require_event(self, recognition_run_id: UUID, event_sequence: int | None) -> None:
        if event_sequence is None:
            return
        event = self.db.get(DocumentRecognitionEvent, (recognition_run_id, event_sequence))
        if event is None:
            raise HeadConflict("missing or cross-run event reference")

    def _validate_artifact_link(
        self,
        artifact_id: str,
        recognition_run_id: UUID,
        *,
        is_exclusive: bool,
    ) -> None:
        other_refs = list(
            self.db.scalars(
                select(DocumentRunArtifact).where(
                    DocumentRunArtifact.artifact_id == artifact_id,
                    DocumentRunArtifact.recognition_run_id != recognition_run_id,
                )
            )
        )
        if other_refs and (is_exclusive or any(ref.is_exclusive for ref in other_refs)):
            raise ArtifactConflict(
                "an exclusive artifact cannot grant access to another run",
                artifact_id=artifact_id,
            )

    @staticmethod
    def _validated_payload_hash(value: Any, supplied: str | None) -> str:
        actual = content_hash(value)
        if supplied is not None and not hmac.compare_digest(actual, supplied):
            raise ValueError("payload_hash does not match canonical payload")
        return actual

    @staticmethod
    def _validate_distinct_ids(run: DocumentAnalysisRun, values: dict[str, Any]) -> None:
        analysis_id = values.get("analysis_id", run.analysis_id)
        metadata_id = values.get("metadata_snapshot_id", run.metadata_snapshot_id)
        graph_id = values.get("graph_snapshot_id", run.graph_snapshot_id)
        present = [str(value) for value in (analysis_id, metadata_id, graph_id) if value]
        if str(run.recognition_run_id) in present or len(present) != len(set(present)):
            raise InvalidRunState(
                "run, analysis, metadata, and graph artifact identities must remain distinct"
            )
