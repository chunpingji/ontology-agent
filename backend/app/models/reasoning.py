import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text
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

    # 002-extraction-realtime-reasoning（data-model §1.2）：结论生效状态 / Part 11
    # 签名绑定 / 增量重算受影响子图 / 历史取代链。
    requires_signature: Mapped[bool] = mapped_column(Boolean, default=False)
    effective: Mapped[bool] = mapped_column(Boolean, default=False)
    # signature_id 与 electronic_signatures.conclusion_id 互为引用，为避免 create_all
    # 的循环外键依赖，这里仅作逻辑引用列（不声明 DB 级 FK 约束）。
    signature_id: Mapped[uuid.UUID | None] = mapped_column(GUID())
    affected_subgraph: Mapped[dict | None] = mapped_column(JSON)
    superseded_by: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("reasoning_executions.id")
    )


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_iri: Mapped[str | None] = mapped_column(String(500))
    actor: Mapped[str | None] = mapped_column(String(100), index=True)
    release_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), index=True)
    details: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    # 002-extraction-realtime-reasoning（data-model §1.3）：append-only 哈希链
    # entry_hash = SHA-256(prev_hash ‖ 规范化记录)，seq 单调递增定位断裂点（FR-028/029）。
    prev_hash: Mapped[str | None] = mapped_column(String(64))
    entry_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    seq: Mapped[int | None] = mapped_column(Integer, unique=True, index=True)


class ActionExecution(Base):
    """结论触发的动作执行与留痕（data-model §2.2, FR-020–023）。"""

    __tablename__ = "action_execution"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    conclusion_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("reasoning_executions.id"), index=True, nullable=False
    )
    action_type: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    payload: Mapped[dict | None] = mapped_column(JSON)
    rule_chain: Mapped[dict | None] = mapped_column(JSON)
    writeback_status: Mapped[str | None] = mapped_column(String(20))
    result: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )


class ElectronicSignature(Base):
    """21 CFR Part 11 电子签名（data-model §2.3, FR-030）。"""

    __tablename__ = "electronic_signatures"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    conclusion_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("reasoning_executions.id"), index=True, nullable=False
    )
    signer: Mapped[str] = mapped_column(String(100), nullable=False)
    signer_role: Mapped[str] = mapped_column(String(50), nullable=False)
    meaning: Mapped[str] = mapped_column(String(200), nullable=False)
    reauth_verified: Mapped[bool] = mapped_column(Boolean, nullable=False)
    signed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    audit_seq: Mapped[int | None] = mapped_column(Integer)
