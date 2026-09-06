"""Versioned candidates, immutable assertions and transactional publication heads."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.types import GUID


def now():
    return datetime.now(timezone.utc)


class EvidenceCandidateRecord(Base):
    __tablename__ = "evidence_candidates"
    __table_args__ = (
        UniqueConstraint("job_id", "source_key", name="uq_evidence_candidate_source"),
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    job_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("extraction_jobs.id"), index=True)
    source_key: Mapped[str] = mapped_column(String(64))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    kind: Mapped[str] = mapped_column(String(20))
    review_status: Mapped[str] = mapped_column(String(20), default="pending")
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class EvidenceCandidateRevision(Base):
    __tablename__ = "evidence_candidate_revisions"
    __table_args__ = (UniqueConstraint("candidate_id", "revision", name="uq_evidence_revision"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    candidate_id: Mapped[str] = mapped_column(String(64), ForeignKey("evidence_candidates.id"))
    revision: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class EvidenceReview(Base):
    __tablename__ = "evidence_reviews"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    candidate_id: Mapped[str] = mapped_column(String(64), ForeignKey("evidence_candidates.id"))
    expected_revision: Mapped[int] = mapped_column(Integer)
    actor: Mapped[str] = mapped_column(String(100))
    decision: Mapped[str] = mapped_column(String(20))
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class DocumentAnalysisRecord(Base):
    __tablename__ = "document_analyses"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    document_hash: Mapped[str] = mapped_column(String(64), index=True)
    structure_hash: Mapped[str] = mapped_column(String(64))
    role: Mapped[str] = mapped_column(String(30))
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class EvidenceCommit(Base):
    __tablename__ = "evidence_commits"
    __table_args__ = (UniqueConstraint("job_id", "idempotency_key", name="uq_evidence_commit_key"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    job_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("extraction_jobs.id"), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(200))
    content_hash: Mapped[str] = mapped_column(String(64))
    manifest: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    actor: Mapped[str] = mapped_column(String(100))
    error: Mapped[str | None] = mapped_column(Text)
    world_path: Mapped[str | None] = mapped_column(Text)
    snapshot_id: Mapped[str | None] = mapped_column(String(64))
    lease_token: Mapped[str | None] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class EvidenceAssertion(Base):
    __tablename__ = "evidence_assertions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    job_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("extraction_jobs.id"), index=True)
    commit_id: Mapped[str] = mapped_column(String(64), ForeignKey("evidence_commits.id"))
    candidate_id: Mapped[str] = mapped_column(String(64), ForeignKey("evidence_candidates.id"))
    candidate_revision: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column(JSON)


class EvidenceSnapshot(Base):
    __tablename__ = "evidence_snapshots"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    job_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("extraction_jobs.id"), index=True)
    parent_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("evidence_snapshots.id"))
    assertion_ids: Mapped[list] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class EvidenceJobState(Base):
    __tablename__ = "evidence_job_states"
    job_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("extraction_jobs.id"), primary_key=True
    )
    revision: Mapped[int] = mapped_column(Integer, default=0)
    snapshot_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("evidence_snapshots.id"))
    analysis_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("document_analyses.id"))
    extraction_run: Mapped[dict | None] = mapped_column(JSON)


class EvidenceCoverage(Base):
    __tablename__ = "evidence_coverage"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    job_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("extraction_jobs.id"), index=True)
    snapshot_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("evidence_snapshots.id"))
    template_id: Mapped[str] = mapped_column(String(100))
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
