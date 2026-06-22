"""能力三 DTO：连接器 / 物化运行 / 事实事件 / 增量重算（contracts/integration-realtime-api）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ConnectorCreate(BaseModel):
    system_type: str
    name: str
    ingest_mode: str = "poll"
    poll_interval_seconds: int = 2
    connection_config: dict | None = None  # 不含明文凭据（R7）
    field_mapping: dict | None = None


class ConnectorResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    system_type: str
    name: str
    ingest_mode: str = "poll"
    poll_interval_seconds: int = 2
    connection_config: dict | None = None
    field_mapping: dict | None = None
    is_active: bool = False
    last_status: str | None = None
    last_error: str | None = None


class TestConnectionResponse(BaseModel):
    ok: bool
    latency_ms: int | None = None
    error: str | None = None


class SyncTriggerResponse(BaseModel):
    run_id: UUID
    status: str


class MaterializationRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    connector_id: UUID
    status: str
    started_at: datetime
    finished_at: datetime | None = None
    cursor_from: dict | None = None
    cursor_to: dict | None = None
    change_count: int = 0
    changes: Any | None = None
    event_ids: Any | None = None
    error_message: str | None = None


class RunListResponse(BaseModel):
    runs: list[MaterializationRunResponse]


class FactEvent(BaseModel):
    id: str
    connector_id: str
    entity_type: str | None = None
    entity_id: str | None = None
    version: int | None = None
    affected_subgraph: dict
    created_at: str


class EventListResponse(BaseModel):
    events: list[FactEvent]


class FactsResponse(BaseModel):
    facts: list[dict]


class WebhookResponse(BaseModel):
    accepted: bool


class IncrementalRequest(BaseModel):
    affected_subgraph: dict  # {"equipment": [...], "product": [...], "area": [...]}


class ConclusionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    execution_type: str
    risk_level: str | None = None
    effective: bool = False
    requires_signature: bool = False
    affected_subgraph: dict | None = None
    superseded_by: UUID | None = None
    results: dict | None = None


class IncrementalResponse(BaseModel):
    refreshed: list[ConclusionResponse]
