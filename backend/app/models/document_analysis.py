"""Durable persistence models for ontology-guided document analysis runs.

This domain is intentionally independent from ``ExtractionJob`` and the legacy
candidate/review tables.  Public run identity, immutable object revisions, and
the private execution lease are represented separately so a stale worker can
never use a public run id as a fencing credential.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.types import GUID


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


def _now() -> datetime:
    return datetime.now(timezone.utc)


class DocumentAnalysisRun(Base):
    """Mutable public head for one explicit document-analysis request."""

    __tablename__ = "document_analysis_runs"
    __table_args__ = (
        UniqueConstraint("owner_id", "request_key", name="uq_doc_analysis_owner_request"),
        CheckConstraint("revision >= 0", name="ck_doc_analysis_run_revision"),
        CheckConstraint("event_head >= 0", name="ck_doc_analysis_event_head"),
        CheckConstraint("artifact_revision >= 0", name="ck_doc_analysis_artifact_revision"),
        CheckConstraint("control_version >= 0", name="ck_doc_analysis_control_version"),
        Index("ix_doc_analysis_run_owner_status", "owner_id", "execution_status"),
        Index("ix_doc_analysis_run_expiry", "deletion_state", "expires_at"),
    )

    recognition_run_id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    contract_version: Mapped[str] = mapped_column(
        String(64), nullable=False, default="document-analysis-runs-v1"
    )
    owner_id: Mapped[str] = mapped_column(String(200), nullable=False)
    request_key: Mapped[str] = mapped_column(String(200), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    document_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_artifact_ref: Mapped[str | None] = mapped_column(String(200))
    root_class_iri: Mapped[str] = mapped_column(String(1000), nullable=False)
    root_class_label: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    ontology_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    metadata_mode: Mapped[str] = mapped_column(
        String(32), nullable=False, default="generate_summary"
    )
    scope_mode: Mapped[str] = mapped_column(String(32), nullable=False, default="document_graph")
    focus_path: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    provisional_fingerprint: Mapped[str | None] = mapped_column(String(64))
    run_fingerprint: Mapped[str | None] = mapped_column(String(64))
    analysis_id: Mapped[str | None] = mapped_column(String(200))
    metadata_snapshot_id: Mapped[str | None] = mapped_column(String(200))
    graph_snapshot_id: Mapped[str | None] = mapped_column(String(200))

    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    event_head: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    artifact_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stage: Mapped[str] = mapped_column(String(32), nullable=False, default="ingest")
    execution_status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    coverage_status: Mapped[str] = mapped_column(String(32), nullable=False, default="partial")
    semantic_status: Mapped[str] = mapped_column(String(32), nullable=False, default="unreviewed")
    stop_reason: Mapped[str | None] = mapped_column(String(100))
    control_action: Mapped[str | None] = mapped_column(String(32))
    control_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ranking_budget_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true(),
    )
    deletion_state: Mapped[str] = mapped_column(String(32), nullable=False, default="none")
    progress: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    artifact_manifest: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    error: Mapped[dict | None] = mapped_column(JSON)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DocumentAnalysisExecution(Base):
    """One private lease/fencing generation for each public run."""

    __tablename__ = "document_analysis_executions"
    __table_args__ = (
        CheckConstraint("generation >= 0", name="ck_doc_analysis_execution_generation"),
        Index("ix_doc_analysis_execution_claim", "status", "lease_expires_at"),
    )

    recognition_run_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("document_analysis_runs.recognition_run_id", ondelete="CASCADE"),
        primary_key=True,
    )
    execution_token_hash: Mapped[str | None] = mapped_column(String(64))
    generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="idle")
    actor: Mapped[str | None] = mapped_column(String(200))
    worker_id: Mapped[str | None] = mapped_column(String(200))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pause_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class DocumentAnalysisArtifact(Base):
    """Immutable artifact identity; access is always mediated by a run ref."""

    __tablename__ = "document_analysis_artifacts"
    __table_args__ = (Index("ix_doc_analysis_artifact_hash", "content_hash", "artifact_kind"),)

    artifact_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    artifact_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_uri: Mapped[str | None] = mapped_column(String(2000))
    media_type: Mapped[str | None] = mapped_column(String(200))
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    payload: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class DocumentRunArtifact(Base):
    """Immutable, owner-scoped run-to-artifact revision."""

    __tablename__ = "document_analysis_run_artifacts"
    __table_args__ = (
        CheckConstraint("revision > 0", name="ck_doc_run_artifact_revision"),
        Index("ix_doc_run_artifact_id", "artifact_id"),
    )

    recognition_run_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("document_analysis_runs.recognition_run_id", ondelete="CASCADE"),
        primary_key=True,
    )
    artifact_kind: Mapped[str] = mapped_column(String(64), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    artifact_id: Mapped[str] = mapped_column(
        String(200), ForeignKey("document_analysis_artifacts.artifact_id"), nullable=False
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    event_head: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_exclusive: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class DocumentRunArtifactHead(Base):
    """CAS-protected current artifact revision for one run/kind."""

    __tablename__ = "document_analysis_artifact_heads"
    __table_args__ = (
        ForeignKeyConstraint(
            ["recognition_run_id", "artifact_kind", "revision"],
            [
                "document_analysis_run_artifacts.recognition_run_id",
                "document_analysis_run_artifacts.artifact_kind",
                "document_analysis_run_artifacts.revision",
            ],
            ondelete="CASCADE",
            name="fk_doc_artifact_head_revision",
        ),
        CheckConstraint("revision > 0", name="ck_doc_artifact_head_revision"),
    )

    recognition_run_id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True)
    artifact_kind: Mapped[str] = mapped_column(String(64), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    artifact_id: Mapped[str] = mapped_column(String(200), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    event_head: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class DocumentRecognitionEvent(Base):
    """Append-only run event with monotonic, run-local sequence."""

    __tablename__ = "document_analysis_events"
    __table_args__ = (
        UniqueConstraint("recognition_run_id", "event_key", name="uq_doc_analysis_event_key"),
        CheckConstraint("sequence > 0", name="ck_doc_analysis_event_sequence"),
        Index("ix_doc_analysis_event_type", "recognition_run_id", "event_type"),
    )

    recognition_run_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("document_analysis_runs.recognition_run_id", ondelete="CASCADE"),
        primary_key=True,
    )
    sequence: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_key: Mapped[str] = mapped_column(String(200), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class DocumentRecognitionEventBatch(Base):
    """Idempotency receipt for one atomic recognition checkpoint batch."""

    __tablename__ = "document_analysis_event_batches"
    __table_args__ = (
        CheckConstraint("first_sequence > 0", name="ck_doc_batch_first_sequence"),
        CheckConstraint("last_sequence >= first_sequence", name="ck_doc_batch_sequence_range"),
        Index("ix_doc_batch_watermark", "recognition_run_id", "last_sequence"),
    )

    recognition_run_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("document_analysis_runs.recognition_run_id", ondelete="CASCADE"),
        primary_key=True,
    )
    batch_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    first_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    last_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    checkpoint_artifact_id: Mapped[str] = mapped_column(
        String(200), ForeignKey("document_analysis_artifacts.artifact_id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class DocumentRunCandidate(Base):
    """One immutable, exact candidate revision (revision is never renumbered)."""

    __tablename__ = "document_analysis_run_candidates"
    __table_args__ = (
        CheckConstraint("revision > 0", name="ck_doc_candidate_revision"),
        Index("ix_doc_candidate_kind", "recognition_run_id", "kind"),
    )

    recognition_run_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("document_analysis_runs.recognition_run_id", ondelete="CASCADE"),
        primary_key=True,
    )
    candidate_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    proof_refs: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    event_sequence: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class DocumentRunCandidateHead(Base):
    """CAS head for an immutable candidate revision chain."""

    __tablename__ = "document_analysis_candidate_heads"
    __table_args__ = (
        ForeignKeyConstraint(
            ["recognition_run_id", "candidate_id", "revision"],
            [
                "document_analysis_run_candidates.recognition_run_id",
                "document_analysis_run_candidates.candidate_id",
                "document_analysis_run_candidates.revision",
            ],
            ondelete="CASCADE",
            name="fk_doc_candidate_head_revision",
        ),
        CheckConstraint("revision > 0", name="ck_doc_candidate_head_revision"),
    )

    recognition_run_id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True)
    candidate_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class DocumentVerificationProof(Base):
    """Immutable verification proof scoped to exactly one analysis run."""

    __tablename__ = "document_analysis_verification_proofs"
    __table_args__ = (
        CheckConstraint("proof_revision > 0", name="ck_doc_proof_revision"),
        Index("ix_doc_proof_target", "recognition_run_id", "target_id"),
    )

    recognition_run_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("document_analysis_runs.recognition_run_id", ondelete="CASCADE"),
        primary_key=True,
    )
    proof_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    proof_revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    target_id: Mapped[str] = mapped_column(String(300), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    event_sequence: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class DocumentVerificationProofHead(Base):
    """CAS head for an immutable proof revision chain."""

    __tablename__ = "document_analysis_proof_heads"
    __table_args__ = (
        ForeignKeyConstraint(
            ["recognition_run_id", "proof_id", "proof_revision"],
            [
                "document_analysis_verification_proofs.recognition_run_id",
                "document_analysis_verification_proofs.proof_id",
                "document_analysis_verification_proofs.proof_revision",
            ],
            ondelete="CASCADE",
            name="fk_doc_proof_head_revision",
        ),
        CheckConstraint("proof_revision > 0", name="ck_doc_proof_head_revision"),
    )

    recognition_run_id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True)
    proof_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    proof_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class DocumentAnalysisControlOperation(Base):
    """Immutable idempotency receipt for a public lifecycle operation."""

    __tablename__ = "document_analysis_control_operations"
    __table_args__ = (
        CheckConstraint("expected_revision >= 0", name="ck_doc_control_expected_revision"),
        CheckConstraint("result_revision > 0", name="ck_doc_control_result_revision"),
    )

    recognition_run_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("document_analysis_runs.recognition_run_id", ondelete="CASCADE"),
        primary_key=True,
    )
    action: Mapped[str] = mapped_column(String(32), primary_key=True)
    request_key: Mapped[str] = mapped_column(String(200), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expected_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    result_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    result_payload_hash: Mapped[str | None] = mapped_column(String(64))
    result_payload: Mapped[dict | None] = mapped_column(JSON)
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class DocumentAnalysisTombstone(Base):
    """Minimal deletion marker that survives removal of run-owned content."""

    __tablename__ = "document_analysis_tombstones"
    __table_args__ = (
        CheckConstraint("token_generation >= 0", name="ck_doc_tombstone_generation"),
        CheckConstraint(
            "(delete_request_key IS NULL AND delete_request_hash IS NULL "
            "AND delete_result_payload_hash IS NULL AND delete_result_payload IS NULL) "
            "OR (delete_request_key IS NOT NULL AND delete_request_hash IS NOT NULL "
            "AND delete_result_payload_hash IS NOT NULL AND delete_result_payload IS NOT NULL)",
            name="ck_doc_tombstone_delete_receipt_complete",
        ),
        Index("ix_doc_tombstone_owner", "owner_hash", "deleted_at"),
    )

    # Deliberately no FK: this row must outlive future physical cleanup of the run.
    recognition_run_id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True)
    owner_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    token_generation: Mapped[int] = mapped_column(Integer, nullable=False)
    final_state: Mapped[str] = mapped_column(String(24), nullable=False, default="deleted")
    reason: Mapped[str | None] = mapped_column(Text)
    # The only surviving operation receipt is the exact, schema-limited public
    # delete acknowledgement.  It contains watermarks/status, never source or
    # candidate payloads, and is sufficient to replay a completed DELETE.
    delete_request_key: Mapped[str | None] = mapped_column(String(200))
    delete_request_hash: Mapped[str | None] = mapped_column(String(64))
    delete_result_payload_hash: Mapped[str | None] = mapped_column(String(64))
    delete_result_payload: Mapped[dict | None] = mapped_column(JSON)
    deleted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
