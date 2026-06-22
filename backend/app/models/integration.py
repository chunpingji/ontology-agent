import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.types import GUID


def _now():
    return datetime.now(timezone.utc)


class IntegrationConnector(Base):
    __tablename__ = "integration_connectors"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    system_type: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    connection_config: Mapped[dict | None] = mapped_column(JSON)
    field_mapping: Mapped[dict | None] = mapped_column(JSON)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # 002-extraction-realtime-reasoning（data-model §1.4）：轮询调度 / 同步水位 /
    # 最近状态（告警依据）。敏感凭据不入 connection_config，经 env 注入（R7）。
    ingest_mode: Mapped[str] = mapped_column(String(20), default="poll")
    poll_interval_seconds: Mapped[int] = mapped_column(Integer, default=2)
    sync_cursor: Mapped[dict | None] = mapped_column(JSON)
    last_status: Mapped[str | None] = mapped_column(String(20))
    last_error: Mapped[str | None] = mapped_column(Text)


class FactMaterializationRun(Base):
    """一次增量物化运行的完整留痕（data-model §2.1, FR-016/018/019, SC-004）。"""

    __tablename__ = "fact_materialization_run"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    connector_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("integration_connectors.id"), index=True, nullable=False
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="running")
    cursor_from: Mapped[dict | None] = mapped_column(JSON)
    cursor_to: Mapped[dict | None] = mapped_column(JSON)
    change_count: Mapped[int] = mapped_column(Integer, default=0)
    changes: Mapped[dict | None] = mapped_column(JSON)
    event_ids: Mapped[dict | None] = mapped_column(JSON)
    error_message: Mapped[str | None] = mapped_column(Text)
