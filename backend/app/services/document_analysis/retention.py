"""Retention and physical cleanup for the independent analysis-run domain.

The cleanup protocol deliberately has two durable phases:

1. revoke the private execution generation and persist ``deleting``;
2. remove only artifacts proven exclusive to that run, then replace the run
   aggregate with its owner-verifiable minimal tombstone.

This module never inspects or mutates legacy extraction jobs, published facts,
global audit records, ontology configuration, or any other business domain.
Filesystem cleanup is driven only by server-side run artifact references and
is confined by :class:`RunArtifactStorage`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Literal
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.config import settings
from app.db import engine
from app.models.document_analysis import (
    DocumentAnalysisArtifact,
    DocumentAnalysisControlOperation,
    DocumentAnalysisExecution,
    DocumentAnalysisRun,
    DocumentRecognitionEvent,
    DocumentRecognitionEventBatch,
    DocumentRunArtifact,
    DocumentRunArtifactHead,
    DocumentRunCandidate,
    DocumentRunCandidateHead,
    DocumentVerificationProof,
    DocumentVerificationProofHead,
)
from app.services.document_analysis.artifact_store import (
    RunArtifactStorage,
    SourceArtifactError,
)
from app.services.document_analysis.run_store import (
    DocumentAnalysisRunStore,
    HeadConflict,
    InvalidRunState,
    utcnow,
)

FinalState = Literal["deleted", "expired"]
_RETENTION_STATUSES = {
    "paused",
    "finished",
    "failed",
    "blocked_dependency",
    "cancelled",
}
_ALWAYS_SHARED_ARTIFACT_KINDS = {"ontology_snapshot"}


class RetentionCleanupError(RuntimeError):
    """A cleanup could not safely finish; the run remains fenced/deleting."""


class RetentionSafetyError(RetentionCleanupError):
    """Persisted ownership/path data was insufficient for physical deletion."""


@dataclass(frozen=True)
class RetentionCleanupResult:
    """Non-sensitive accounting for one completed or replayed cleanup."""

    recognition_run_id: UUID
    final_state: FinalState
    token_generation: int
    deleted_artifact_ids: tuple[str, ...] = ()
    retained_shared_artifact_ids: tuple[str, ...] = ()
    deleted_storage_uris: tuple[str, ...] = ()
    idempotent_replay: bool = False


@dataclass(frozen=True)
class _ArtifactPlan:
    delete_artifact_ids: tuple[str, ...]
    retain_artifact_ids: tuple[str, ...]
    delete_storage_uris: tuple[str, ...]


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


class DocumentAnalysisRetentionService:
    """Own transactional retention operations for ``DocumentAnalysisRun``.

    Methods in this service commit their own transaction.  In particular,
    ``delete_run`` commits the generation-revocation/deleting barrier before
    deleting a file, so a filesystem failure can never reactivate a stale
    worker through a database rollback.
    """

    def __init__(
        self,
        db: Session,
        *,
        storage: RunArtifactStorage | None = None,
        clock: Callable[[], datetime] = utcnow,
        retention_days: int | None = None,
    ) -> None:
        configured_days = (
            settings.document_analysis_retention_days if retention_days is None else retention_days
        )
        if configured_days < 0:
            raise ValueError("retention_days cannot be negative")
        self.db = db
        self.clock = clock
        self.retention_days = configured_days
        self.storage = storage or RunArtifactStorage(settings.document_analysis_storage_dir)
        self.store = DocumentAnalysisRunStore(db, clock=clock)

    def schedule_expiry(
        self,
        recognition_run_id: UUID | str,
        owner_id: str,
        *,
        expected_revision: int,
        retention_days: int | None = None,
    ) -> DocumentAnalysisRun:
        """Persist the configured deadline for a paused or terminal run.

        Paused retention is anchored at ``paused_at``; terminal retention is
        anchored at ``finished_at``.  Active runs never receive a deadline.
        """

        days = self.retention_days if retention_days is None else retention_days
        if days < 0:
            raise ValueError("retention_days cannot be negative")
        try:
            run = self.store.get_owned(recognition_run_id, owner_id)
            if run.revision != expected_revision:
                raise HeadConflict(
                    "stale run revision",
                    expected=expected_revision,
                    actual=run.revision,
                )
            if run.deletion_state != "none":
                raise InvalidRunState("a deleting run cannot receive a retention deadline")
            if run.execution_status not in _RETENTION_STATUSES:
                raise InvalidRunState("only paused or terminal runs can expire")
            anchor = run.paused_at if run.execution_status == "paused" else run.finished_at
            if anchor is None:
                raise InvalidRunState("retention anchor is missing")
            deadline = _aware(anchor) + timedelta(days=days)
            changed = self.db.execute(
                update(DocumentAnalysisRun)
                .where(
                    DocumentAnalysisRun.recognition_run_id == run.recognition_run_id,
                    DocumentAnalysisRun.owner_id == owner_id,
                    DocumentAnalysisRun.revision == expected_revision,
                    DocumentAnalysisRun.deletion_state == "none",
                )
                .values(
                    expires_at=deadline,
                    revision=expected_revision + 1,
                    updated_at=self.clock(),
                )
                .execution_options(synchronize_session=False)
            )
            if changed.rowcount != 1:
                raise HeadConflict("concurrent run head update", expected=expected_revision)
            self.db.commit()
            return self.store.get_owned(recognition_run_id, owner_id)
        except Exception:
            self.db.rollback()
            raise

    def delete_run(
        self,
        recognition_run_id: UUID | str,
        owner_id: str,
        *,
        expected_revision: int | None = None,
        request_key: str | None = None,
        reason: str | None = None,
        final_state: FinalState = "deleted",
    ) -> RetentionCleanupResult:
        """Fence and irreversibly clean one owner-scoped analysis run.

        ``expected_revision`` is needed only when this call initiates deletion.
        API background work normally receives a run already in ``requested``
        and therefore proceeds directly to ``mark_deleting``.
        """

        if final_state not in {"deleted", "expired"}:
            raise ValueError("final_state must be deleted or expired")
        run_id = UUID(str(recognition_run_id))
        run = self._ensure_deleting(
            run_id,
            owner_id,
            expected_revision=expected_revision,
            request_key=request_key,
            reason=reason,
        )
        if run is None:
            marker = self.store.get_tombstone(run_id, owner_id)
            return RetentionCleanupResult(
                recognition_run_id=run_id,
                final_state=marker.final_state,  # type: ignore[arg-type]
                token_generation=marker.token_generation,
                idempotent_replay=True,
            )

        plan = self._artifact_plan(run_id)
        deleted_storage_uris = self._delete_exclusive_files(run_id, plan)
        try:
            return self._purge_database_rows(
                run_id,
                owner_id,
                plan=plan,
                deleted_storage_uris=deleted_storage_uris,
                reason=reason,
                final_state=final_state,
            )
        except Exception:
            # The durable deleting barrier remains.  Missing files are accepted
            # on retry, so database/transient failures are safely resumable.
            self.db.rollback()
            raise

    def cleanup_expired(
        self,
        *,
        limit: int = 100,
        now: datetime | None = None,
    ) -> tuple[RetentionCleanupResult, ...]:
        """Clean at most ``limit`` due paused/terminal runs, oldest first."""

        if limit <= 0:
            raise ValueError("limit must be positive")
        cutoff = _aware(now or self.clock())
        due = list(
            self.db.execute(
                select(
                    DocumentAnalysisRun.recognition_run_id,
                    DocumentAnalysisRun.owner_id,
                )
                .where(
                    DocumentAnalysisRun.expires_at.is_not(None),
                    DocumentAnalysisRun.expires_at <= cutoff,
                    DocumentAnalysisRun.execution_status.in_(_RETENTION_STATUSES),
                    DocumentAnalysisRun.deletion_state.in_({"none", "requested", "deleting"}),
                )
                .order_by(
                    DocumentAnalysisRun.expires_at,
                    DocumentAnalysisRun.recognition_run_id,
                )
                .limit(limit)
            )
        )
        # End the discovery read transaction before each cleanup establishes
        # and commits its own fencing barrier.
        self.db.rollback()
        results = []
        for run_id, owner_id in due:
            results.append(
                self.delete_run(
                    run_id,
                    owner_id,
                    reason="retention_expired",
                    final_state="expired",
                )
            )
        return tuple(results)

    def _ensure_deleting(
        self,
        run_id: UUID,
        owner_id: str,
        *,
        expected_revision: int | None,
        request_key: str | None,
        reason: str | None,
    ) -> DocumentAnalysisRun | None:
        row = self.db.scalar(
            select(DocumentAnalysisRun)
            .where(
                DocumentAnalysisRun.recognition_run_id == run_id,
                DocumentAnalysisRun.owner_id == owner_id,
            )
            .execution_options(populate_existing=True)
        )
        if row is None:
            # Owner comparison remains constant-time inside RunStore.
            self.store.get_tombstone(run_id, owner_id)
            self.db.rollback()
            return None
        try:
            if row.deletion_state == "none":
                expected = row.revision if expected_revision is None else expected_revision
                row = self.store.request_control(
                    run_id,
                    owner_id,
                    action="delete",
                    expected_revision=expected,
                    request_key=request_key,
                    reason=reason,
                )
            if row.deletion_state == "requested":
                row = self.store.mark_deleting(
                    run_id,
                    owner_id,
                    expected_revision=row.revision,
                )
            elif row.deletion_state not in {"deleting", "deleted"}:
                raise InvalidRunState("run is not in the deletion workflow")
            # This is the critical durability boundary.  No path is touched
            # until the revoked generation and deleting state are committed.
            self.db.commit()
            return self.db.scalar(
                select(DocumentAnalysisRun)
                .where(
                    DocumentAnalysisRun.recognition_run_id == run_id,
                    DocumentAnalysisRun.owner_id == owner_id,
                )
                .execution_options(populate_existing=True)
            )
        except Exception:
            self.db.rollback()
            raise

    def _artifact_plan(self, run_id: UUID) -> _ArtifactPlan:
        refs = list(
            self.db.scalars(
                select(DocumentRunArtifact).where(DocumentRunArtifact.recognition_run_id == run_id)
            )
        )
        refs_by_artifact: dict[str, list[DocumentRunArtifact]] = {}
        for ref in refs:
            refs_by_artifact.setdefault(ref.artifact_id, []).append(ref)

        delete_ids: list[str] = []
        retain_ids: list[str] = []
        delete_uris: set[str] = set()
        for artifact_id, run_refs in sorted(refs_by_artifact.items()):
            artifact = self.db.get(DocumentAnalysisArtifact, artifact_id)
            if artifact is None:
                # A missing immutable object has nothing left to delete.  Its
                # dangling run refs are still scoped to this aggregate.
                continue
            other_ref = self.db.scalar(
                select(DocumentRunArtifact.recognition_run_id)
                .where(
                    DocumentRunArtifact.artifact_id == artifact_id,
                    DocumentRunArtifact.recognition_run_id != run_id,
                )
                .limit(1)
            )
            exclusive = (
                artifact.artifact_kind not in _ALWAYS_SHARED_ARTIFACT_KINDS
                and all(ref.is_exclusive for ref in run_refs)
                and other_ref is None
            )
            if not exclusive:
                retain_ids.append(artifact_id)
                continue
            delete_ids.append(artifact_id)
            if artifact.storage_uri:
                other_storage_identity = self.db.scalar(
                    select(DocumentAnalysisArtifact.artifact_id)
                    .where(
                        DocumentAnalysisArtifact.storage_uri == artifact.storage_uri,
                        DocumentAnalysisArtifact.artifact_id != artifact_id,
                    )
                    .limit(1)
                )
                if other_storage_identity is None:
                    delete_uris.add(artifact.storage_uri)

        self.db.rollback()
        return _ArtifactPlan(
            delete_artifact_ids=tuple(delete_ids),
            retain_artifact_ids=tuple(retain_ids),
            delete_storage_uris=tuple(sorted(delete_uris)),
        )

    def _delete_exclusive_files(self, run_id: UUID, plan: _ArtifactPlan) -> tuple[str, ...]:
        deleted = []
        for storage_uri in plan.delete_storage_uris:
            try:
                path = self.storage.resolve(run_id, storage_uri)
            except SourceArtifactError as exc:
                raise RetentionSafetyError(
                    f"refusing unsafe artifact path for run {run_id}"
                ) from exc
            if path.exists() and not path.is_file():
                raise RetentionSafetyError(
                    f"artifact storage reference is not a file: {storage_uri}"
                )
            try:
                existed = path.exists()
                path.unlink(missing_ok=True)
            except OSError as exc:
                raise RetentionCleanupError(
                    f"failed to remove run artifact: {storage_uri}"
                ) from exc
            if existed:
                deleted.append(storage_uri)
            self._prune_empty_parents(run_id, path.parent)
        return tuple(deleted)

    def _prune_empty_parents(self, run_id: UUID, start: Path) -> None:
        run_root = (self.storage.root / str(run_id)).resolve()
        current = start
        while current == run_root or run_root in current.parents:
            try:
                current.rmdir()
            except OSError:
                break
            if current == run_root:
                break
            current = current.parent

    def _purge_database_rows(
        self,
        run_id: UUID,
        owner_id: str,
        *,
        plan: _ArtifactPlan,
        deleted_storage_uris: tuple[str, ...],
        reason: str | None,
        final_state: FinalState,
    ) -> RetentionCleanupResult:
        run = self.db.scalar(
            select(DocumentAnalysisRun)
            .where(
                DocumentAnalysisRun.recognition_run_id == run_id,
                DocumentAnalysisRun.owner_id == owner_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if run is None:
            marker = self.store.get_tombstone(run_id, owner_id)
            self.db.rollback()
            return RetentionCleanupResult(
                recognition_run_id=run_id,
                final_state=marker.final_state,  # type: ignore[arg-type]
                token_generation=marker.token_generation,
                idempotent_replay=True,
            )
        if run.deletion_state not in {"deleting", "deleted"}:
            raise InvalidRunState("run lost its deleting barrier")

        # Always pass through the store so a partially completed cleanup can
        # authenticate the owner and preserve a now-frozen public DELETE
        # receipt before the run-scoped control rows are removed.
        marker = self.store.tombstone(
            run_id,
            owner_id,
            expected_revision=run.revision,
            reason=reason,
            final_state=final_state,
        )

        for model in (
            DocumentRunArtifactHead,
            DocumentRunCandidateHead,
            DocumentVerificationProofHead,
            DocumentAnalysisControlOperation,
            # Batch receipts hold an immediate FK to their checkpoint artifact.
            # Remove them before deleting run-owned artifacts on PostgreSQL (and
            # on SQLite whenever foreign-key enforcement is enabled).
            DocumentRecognitionEventBatch,
            DocumentRecognitionEvent,
            DocumentRunCandidate,
            DocumentVerificationProof,
            DocumentRunArtifact,
        ):
            self.db.execute(
                delete(model)
                .where(model.recognition_run_id == run_id)
                .execution_options(synchronize_session=False)
            )

        actually_deleted_artifacts: list[str] = []
        for artifact_id in plan.delete_artifact_ids:
            # Re-check after removing this run's refs.  A concurrently created
            # reference protects the immutable artifact through this predicate
            # (and ultimately its database foreign key).
            remaining_ref = self.db.scalar(
                select(DocumentRunArtifact.recognition_run_id)
                .where(DocumentRunArtifact.artifact_id == artifact_id)
                .limit(1)
            )
            if remaining_ref is not None:
                continue
            changed = self.db.execute(
                delete(DocumentAnalysisArtifact)
                .where(
                    DocumentAnalysisArtifact.artifact_id == artifact_id,
                    DocumentAnalysisArtifact.artifact_kind.not_in(_ALWAYS_SHARED_ARTIFACT_KINDS),
                )
                .execution_options(synchronize_session=False)
            )
            if changed.rowcount == 1:
                actually_deleted_artifacts.append(artifact_id)

        self.db.execute(
            delete(DocumentAnalysisExecution)
            .where(DocumentAnalysisExecution.recognition_run_id == run_id)
            .execution_options(synchronize_session=False)
        )
        removed = self.db.execute(
            delete(DocumentAnalysisRun)
            .where(
                DocumentAnalysisRun.recognition_run_id == run_id,
                DocumentAnalysisRun.owner_id == owner_id,
                DocumentAnalysisRun.deletion_state == "deleted",
            )
            .execution_options(synchronize_session="fetch")
        )
        if removed.rowcount != 1:
            raise RetentionCleanupError("run tombstone replacement lost its CAS boundary")
        self.db.commit()
        return RetentionCleanupResult(
            recognition_run_id=run_id,
            final_state=marker.final_state,  # type: ignore[arg-type]
            token_generation=marker.token_generation,
            deleted_artifact_ids=tuple(sorted(actually_deleted_artifacts)),
            retained_shared_artifact_ids=plan.retain_artifact_ids,
            deleted_storage_uris=deleted_storage_uris,
        )


def delete_run(
    recognition_run_id: UUID | str,
    owner_id: str,
    *,
    bind: Any | None = None,
    storage_root: Path | None = None,
    expected_revision: int | None = None,
    request_key: str | None = None,
    reason: str | None = None,
    final_state: FinalState = "deleted",
) -> RetentionCleanupResult:
    """Background-safe wrapper that owns a fresh SQLAlchemy session."""

    db = Session(bind=bind or engine)
    try:
        storage = RunArtifactStorage(storage_root or settings.document_analysis_storage_dir)
        return DocumentAnalysisRetentionService(db, storage=storage).delete_run(
            recognition_run_id,
            owner_id,
            expected_revision=expected_revision,
            request_key=request_key,
            reason=reason,
            final_state=final_state,
        )
    finally:
        db.close()


def cleanup_expired_runs(
    *,
    bind: Any | None = None,
    storage_root: Path | None = None,
    limit: int = 100,
    now: datetime | None = None,
) -> tuple[RetentionCleanupResult, ...]:
    """Background-safe wrapper for bounded expiry cleanup."""

    db = Session(bind=bind or engine)
    try:
        storage = RunArtifactStorage(storage_root or settings.document_analysis_storage_dir)
        return DocumentAnalysisRetentionService(db, storage=storage).cleanup_expired(
            limit=limit,
            now=now,
        )
    finally:
        db.close()


__all__ = [
    "DocumentAnalysisRetentionService",
    "RetentionCleanupError",
    "RetentionCleanupResult",
    "RetentionSafetyError",
    "cleanup_expired_runs",
    "delete_run",
]
