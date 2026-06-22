"""能力三 集成网关路由：连接器 / 探活 / 增量同步 / 物化留痕 / 事实事件（R4–R7）。

凭据经 env/`settings` 注入，`connection_config` 仅存引用键（R7）；写类操作要求
`senior_analyst`（FR-033）。同步直接 `await` 物化以保证留痕在响应前落库。
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependencies import (
    ROLE_OPERATOR,
    ROLE_SENIOR_ANALYST,
    get_materializer,
    require_role,
)
from app.models.entity_shadow import EntityShadow
from app.models.integration import FactMaterializationRun, IntegrationConnector
from app.models.reasoning import ReasoningExecution
from app.schemas.integration import (
    ConnectorCreate,
    ConnectorResponse,
    EventListResponse,
    FactsResponse,
    MaterializationRunResponse,
    RunListResponse,
    SyncTriggerResponse,
    TestConnectionResponse,
    WebhookResponse,
)
from app.services.integration.aps_connector import APSConnector
from app.services.integration.events import fact_event_bus
from app.services.integration.materializer import FactMaterializer

router = APIRouter()

_maintainer = require_role(ROLE_SENIOR_ANALYST)
_operator = require_role(ROLE_SENIOR_ANALYST, ROLE_OPERATOR)
_FACT_BASE_IRI = "http://slpra.org/facts#"


class IntegrationSpec(BaseModel):
    system_type: str
    description: str
    endpoints: list[dict]


class DashboardResponse(BaseModel):
    compatibility_matrix: list[dict]
    schedule_risks: list[dict]
    updated_at: str


# --- 连接器 CRUD ----------------------------------------------------------


@router.get("/connectors", response_model=list[ConnectorResponse])
def list_connectors(db: Session = Depends(get_db)):
    return db.query(IntegrationConnector).all()


@router.post("/connectors", response_model=ConnectorResponse, status_code=201)
def create_connector(
    req: ConnectorCreate,
    db: Session = Depends(get_db),
    _: object = Depends(_maintainer),
):
    connector = IntegrationConnector(**req.model_dump(), is_active=True)
    db.add(connector)
    db.commit()
    db.refresh(connector)
    return connector


@router.delete("/connectors/{connector_id}", status_code=204)
def delete_connector(
    connector_id: UUID,
    db: Session = Depends(get_db),
    _: object = Depends(_maintainer),
):
    c = db.get(IntegrationConnector, connector_id)
    if not c:
        raise HTTPException(404)
    db.delete(c)
    db.commit()


# --- 探活 / 增量同步 ------------------------------------------------------


@router.post("/connectors/{connector_id}/test", response_model=TestConnectionResponse)
async def test_connector(
    connector_id: UUID,
    db: Session = Depends(get_db),
    _: object = Depends(_operator),
):
    c = db.get(IntegrationConnector, connector_id)
    if not c:
        raise HTTPException(404)
    aps = APSConnector(
        c.connection_config, c.field_mapping,
        timeout=float(c.poll_interval_seconds or 2) + 3.0,
    )
    t0 = time.monotonic()
    ok = await aps.test_connection()
    latency_ms = int((time.monotonic() - t0) * 1000)
    if not ok:
        return TestConnectionResponse(ok=False, latency_ms=latency_ms, error="探活失败/超时")
    return TestConnectionResponse(ok=True, latency_ms=latency_ms)


@router.post(
    "/connectors/{connector_id}/sync",
    response_model=SyncTriggerResponse,
    status_code=202,
)
async def sync_connector(
    connector_id: UUID,
    db: Session = Depends(get_db),
    materializer: FactMaterializer = Depends(get_materializer),
    _: object = Depends(_operator),
):
    c = db.get(IntegrationConnector, connector_id)
    if not c:
        raise HTTPException(404)
    run = await materializer.run_sync(c)
    return SyncTriggerResponse(run_id=run.id, status=run.status)


@router.post("/connectors/{connector_id}/webhook", response_model=WebhookResponse)
async def connector_webhook(
    connector_id: UUID,
    payload: dict = Body(default_factory=dict),
    db: Session = Depends(get_db),
    materializer: FactMaterializer = Depends(get_materializer),
):
    """可选 Webhook 推送：将 payload.changes 合入内联配置后触发一次同步（R6）。"""
    c = db.get(IntegrationConnector, connector_id)
    if not c:
        raise HTTPException(404)
    changes = payload.get("changes", [])
    if changes:
        cfg = dict(c.connection_config or {})
        cfg.setdefault("source_mode", "inline")
        cfg["inline_changes"] = [*cfg.get("inline_changes", []), *changes]
        c.connection_config = cfg
        db.commit()
    await materializer.run_sync(c)
    return WebhookResponse(accepted=True)


# --- 物化留痕 / 事实事件 / A-Box 事实 -------------------------------------


@router.get("/connectors/{connector_id}/runs", response_model=RunListResponse)
def list_runs(connector_id: UUID, db: Session = Depends(get_db)):
    runs = (
        db.query(FactMaterializationRun)
        .filter(FactMaterializationRun.connector_id == connector_id)
        .order_by(FactMaterializationRun.started_at.desc())
        .all()
    )
    return RunListResponse(runs=[MaterializationRunResponse.model_validate(r) for r in runs])


@router.get("/runs/{run_id}", response_model=MaterializationRunResponse)
def get_run(run_id: UUID, db: Session = Depends(get_db)):
    run = db.get(FactMaterializationRun, run_id)
    if not run:
        raise HTTPException(404)
    return run


@router.get("/events", response_model=EventListResponse)
def list_events():
    return EventListResponse(events=fact_event_bus.history())


@router.get("/facts", response_model=FactsResponse)
def list_facts(
    equipment: str | None = None,
    product: str | None = None,
    db: Session = Depends(get_db),
):
    """已物化的 A-Box 事实个体（影子表中 facts# 命名空间）。"""
    q = db.query(EntityShadow).filter(EntityShadow.iri.like(f"{_FACT_BASE_IRI}%"))
    rows = q.all()
    facts: list[dict] = []
    for r in rows:
        if equipment and equipment not in r.iri:
            continue
        if product and product not in str(r.properties_json or {}):
            continue
        facts.append({
            "iri": r.iri,
            "label": r.label_zh or r.label_en,
            "class_iri": r.class_iri,
            "properties": r.properties_json,
        })
    return FactsResponse(facts=facts)


# --- 实时看板 ------------------------------------------------------------


@router.get("/dashboard", response_model=DashboardResponse)
def dashboard(db: Session = Depends(get_db)):
    """设备×产品共线相容性矩阵 + 未来排期风险（复用当前生效结论, FR-025）。"""
    conclusions = (
        db.query(ReasoningExecution)
        .filter(ReasoningExecution.effective.is_(True))
        .filter(ReasoningExecution.superseded_by.is_(None))
        .all()
    )
    matrix: list[dict] = []
    risks: list[dict] = []
    for c in conclusions:
        params = c.input_params or {}
        results = c.results or {}
        equipment = (params.get("equipment_iris") or [None])[0]
        product = params.get("drug_iri") or results.get("product")
        matrix.append({
            "equipment": equipment,
            "product": product,
            "risk_level": c.risk_level,
            "conclusion_id": str(c.id),
        })
        if results.get("schedule_conflict"):
            risks.append({
                "date": results.get("conflict_date"),
                "equipment": equipment,
                "conflict": True,
                "detail": results.get("conflict_detail", "不相容同设备同时段"),
            })
    return DashboardResponse(
        compatibility_matrix=matrix,
        schedule_risks=risks,
        updated_at=datetime.now(timezone.utc).isoformat(),
    )


# --- 集成规格（保留既有契约说明） ----------------------------------------


@router.get("/specs", response_model=list[IntegrationSpec])
def list_integration_specs():
    return [
        IntegrationSpec(
            system_type="aps",
            description="Advanced Planning & Scheduling — 实时排产/设备状态增量事实源",
            endpoints=[
                {"method": "POST", "path": "/connectors/{id}/sync", "params": "—"},
                {"method": "POST", "path": "/connectors/{id}/webhook", "params": "changes[]"},
                {"method": "GET", "path": "/connectors/{id}/runs", "params": "—"},
            ],
        ),
        IntegrationSpec(
            system_type="mes",
            description="Manufacturing Execution System — production batch data, equipment status",
            endpoints=[
                {"method": "GET", "path": "/production-schedule", "params": "date_range"},
                {"method": "GET", "path": "/equipment-status", "params": "equipment_ids[]"},
                {"method": "POST", "path": "/batch-record", "params": "batch_data"},
            ],
        ),
    ]
