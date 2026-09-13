"""Owner-scoped immutable property reviews and bounded repair operations."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.types import GUID


def now() -> datetime:
    return datetime.now(UTC)


class DocumentPropertyReview(Base):
    __tablename__ = "document_analysis_property_reviews"
    __table_args__ = (
        ForeignKeyConstraint(
            ["recognition_run_id", "candidate_id", "candidate_revision"],
            ["document_analysis_run_candidates.recognition_run_id",
             "document_analysis_run_candidates.candidate_id",
             "document_analysis_run_candidates.revision"],
            ondelete="CASCADE", name="fk_doc_property_review_candidate",
        ),
        UniqueConstraint("recognition_run_id", "request_key",
                         name="uq_doc_property_review_request"),
        UniqueConstraint("recognition_run_id", "candidate_id", "candidate_revision", "revision",
                         name="uq_doc_property_review_revision"),
        CheckConstraint("revision > 0", name="ck_doc_property_review_revision"),
        CheckConstraint("decision IN ('accepted', 'rejected')",
                        name="ck_doc_property_review_decision"),
        Index("ix_doc_property_review_run", "recognition_run_id", "created_at"),
    )

    review_id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    recognition_run_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("document_analysis_runs.recognition_run_id", ondelete="CASCADE"),
        nullable=False,
    )
    owner_id: Mapped[str] = mapped_column(String(200), nullable=False)
    actor_role: Mapped[str] = mapped_column(String(50), nullable=False)
    request_key: Mapped[str] = mapped_column(String(200), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    candidate_id: Mapped[str] = mapped_column(String(200), nullable=False)
    candidate_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    decision: Mapped[str] = mapped_column(String(20), nullable=False)
    graph_snapshot_id: Mapped[str] = mapped_column(String(200), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    receipt: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class DocumentPropertyReviewHead(Base):
    __tablename__ = "document_analysis_property_review_heads"
    __table_args__ = (
        CheckConstraint("revision > 0", name="ck_doc_property_review_head_revision"),
    )

    recognition_run_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("document_analysis_runs.recognition_run_id", ondelete="CASCADE"),
        primary_key=True,
    )
    candidate_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    candidate_revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    review_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("document_analysis_property_reviews.review_id", ondelete="CASCADE"),
        nullable=False,
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)


class DocumentPropertyRepair(Base):
    __tablename__ = "document_analysis_property_repairs"
    __table_args__ = (
        UniqueConstraint("recognition_run_id", "request_key",
                         name="uq_doc_property_repair_request"),
        UniqueConstraint("review_id", name="uq_doc_property_repair_review"),
        CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'unresolved', 'failed', 'cancelled')",
            name="ck_doc_property_repair_status",
        ),
        Index("ix_doc_property_repair_run", "recognition_run_id", "status"),
    )

    operation_id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    recognition_run_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("document_analysis_runs.recognition_run_id", ondelete="CASCADE"),
        nullable=False,
    )
    review_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("document_analysis_property_reviews.review_id", ondelete="CASCADE"),
        nullable=False,
    )
    request_key: Mapped[str] = mapped_column(String(200), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    base_checkpoint_artifact_id: Mapped[str] = mapped_column(
        String(200), ForeignKey("document_analysis_artifacts.artifact_id"), nullable=False,
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    result: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    receipt: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
