"""Shared local inference admission and bounded request telemetry (no prompts)."""

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class LocalModelPool(Base):
    __tablename__ = "local_model_pools"

    pool_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    capacity: Mapped[int] = mapped_column(Integer, nullable=False)


class LocalModelRequest(Base):
    __tablename__ = "local_model_requests"
    __table_args__ = (
        Index("ix_local_model_requests_admission", "pool_id", "status", "sequence"),
        Index("ix_local_model_requests_job", "job_id", "created_at"),
    )

    sequence: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    pool_id: Mapped[str] = mapped_column(ForeignKey("local_model_pools.pool_id"), nullable=False)
    logical_call_id: Mapped[str] = mapped_column(String(32), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    job_id: Mapped[str | None] = mapped_column(String(36))
    # Shared by legacy hex UUIDs, document UUIDs and prefixed evaluation IDs.
    run_id: Mapped[str | None] = mapped_column(String(64))
    task_id: Mapped[str | None] = mapped_column(String(64))
    stage: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    lease_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    metrics: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
