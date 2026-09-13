"""Append-only expert feedback, separate from fact acceptance and model calibration."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, CheckConstraint, DateTime, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.types import GUID


class ReportExpertOpinion(Base):
    __tablename__ = "report_expert_opinions"
    __table_args__ = (
        UniqueConstraint("owner_id", "request_key", name="uq_expert_opinion_request"),
        CheckConstraint("revision = 1", name="ck_expert_opinion_immutable"),
        Index("ix_expert_opinion_target", "owner_id", "target_hash", "created_at"),
    )

    opinion_id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[str] = mapped_column(String(200), nullable=False)
    actor_role: Mapped[str] = mapped_column(String(50), nullable=False)
    target_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_key: Mapped[str] = mapped_column(String(200), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                               default=lambda: datetime.now(UTC), nullable=False)
