"""增量事实物化服务（能力三, R5, FR-015/016/018/019, SC-004）。

将 APS 增量变更归一化为 A-Box 事实个体：经 `OntologyEngine` 投影 + `KGStore`
写影子表；按 `connector_id`+实体+版本幂等去重；写 `FactMaterializationRun` 留痕；
超时不推进水位（保留上一良好状态）并告警；发布事实变更事件供增量重算。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.integration import FactMaterializationRun, IntegrationConnector
from app.services import audit
from app.services.integration.aps_connector import APSConnector
from app.services.integration.events import fact_event_bus
from app.services.kg_store import KGStore
from app.services.ontology_engine import IndividualInfo, OntologyEngine

logger = logging.getLogger(__name__)

_FACT_BASE_IRI = "http://slpra.org/facts#"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class FactMaterializer:
    def __init__(self, db: Session, engine: OntologyEngine) -> None:
        self.db = db
        self.engine = engine
        self.kg = KGStore(db=db, onto_engine=engine)

    async def run_sync(self, connector: IntegrationConnector) -> FactMaterializationRun:
        """执行一次增量同步。返回留痕记录。"""
        cursor = dict(connector.sync_cursor or {})
        run = FactMaterializationRun(
            connector_id=connector.id,
            status="running",
            cursor_from=cursor or None,
        )
        self.db.add(run)
        self.db.commit()
        self.db.refresh(run)

        aps = APSConnector(connector.connection_config, connector.field_mapping,
                           timeout=float(connector.poll_interval_seconds or 2) + 3.0)
        try:
            pull = await aps.fetch_incremental(cursor)
        except (asyncio.TimeoutError, ConnectionError) as exc:
            return self._fail(run, connector, "timeout", str(exc) or "timeout")
        except Exception as exc:  # noqa: BLE001
            return self._fail(run, connector, "error", f"{type(exc).__name__}: {exc}")

        # 幂等去重：维护每实体已物化的最高版本（抗重复/乱序, FR-019/VR-3）。
        versions: dict[str, int] = dict(cursor.get("versions", {}))
        applied: list[dict] = []
        event_ids: list[str] = []

        for change in pull.changes:
            eid = str(change.get("entity_id", ""))
            ver = int(change.get("version", 0))
            if not eid:
                continue
            if ver <= versions.get(eid, 0):
                continue  # 重复或乱序旧版本 → 跳过（幂等）
            versions[eid] = ver
            self._materialize(change)
            applied.append(change)
            event = fact_event_bus.publish(connector_id=str(connector.id), change=change)
            event_ids.append(event["id"])

        new_cursor = {"version": pull.cursor_to.get("version", cursor.get("version", 0)),
                      "versions": versions}
        run.status = "success"
        run.cursor_to = new_cursor
        run.change_count = len(applied)
        run.changes = applied
        run.event_ids = event_ids
        run.finished_at = _now()
        connector.sync_cursor = new_cursor
        connector.last_status = "success"
        connector.last_error = None
        connector.last_sync_at = _now()
        self.db.commit()
        self.db.refresh(run)

        audit.append(
            self.db, "integration.materialize", actor="system",
            entity_iri=str(connector.id),
            details={"run_id": str(run.id), "change_count": len(applied)},
        )
        return run

    def _materialize(self, change: dict) -> None:
        """归一化一条变更为 A-Box 事实个体并写影子表。"""
        eid = str(change.get("entity_id"))
        info = IndividualInfo(
            iri=f"{_FACT_BASE_IRI}{eid}",
            name=eid,
            class_iris=[f"{_FACT_BASE_IRI}{change.get('entity_type', 'Fact')}"],
            label_zh=change.get("label"),
            properties={**(change.get("fields") or {}), "_version": change.get("version")},
        )
        try:  # best-effort World 投影（fake engine 下为 no-op）
            self.engine.project_entities([info.properties])
        except Exception:  # noqa: BLE001  # pragma: no cover
            pass
        self.kg.sync_individual_to_shadow(info)

    def _fail(self, run: FactMaterializationRun, connector: IntegrationConnector,
              status: str, message: str) -> FactMaterializationRun:
        run.status = status
        run.cursor_to = None  # 不推进水位（保留上一良好状态, FR-018/VR-4）
        run.error_message = message
        run.finished_at = _now()
        connector.last_status = status
        connector.last_error = message
        self.db.commit()
        self.db.refresh(run)
        logger.warning("materialization %s for connector %s: %s", status, connector.id, message)
        return run
