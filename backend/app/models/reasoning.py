import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.types import GUID


def _uuid():
    return uuid.uuid4()


def _now():
    return datetime.now(timezone.utc)


class ReasoningExecution(Base):
    __tablename__ = "reasoning_executions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    execution_type: Mapped[str] = mapped_column(String(50), nullable=False)
    input_params: Mapped[dict] = mapped_column(JSON, nullable=False)
    rules_fired: Mapped[dict | None] = mapped_column(JSON)
    results: Mapped[dict] = mapped_column(JSON, nullable=False)
    risk_level: Mapped[str | None] = mapped_column(String(20))
    maco_value: Mapped[float | None] = mapped_column(Numeric(15, 6))
    maco_method: Mapped[str | None] = mapped_column(String(50))
    scenarios_identified: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_iri: Mapped[str | None] = mapped_column(String(500))
    actor: Mapped[str | None] = mapped_column(String(100), index=True)
    release_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), index=True)
    details: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
