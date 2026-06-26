"""事实变更事件总线（能力三, R6, FR-016/017）。

进程内发布/订阅，无消息中间件（宪法/研究约束）。每条事件携带受影响子图
（设备/产品/区域标识），供增量重算编排按子图触发（FR-017）。
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from datetime import datetime, timezone

from app.services.integration.connector_factory import DEFAULT_DOC_TYPE_TO_CLASS

logger = logging.getLogger(__name__)

Subscriber = Callable[[dict], None]

# 文档变更的 entity_type 取值集（托管文档类名 ∪ 通用 'document'）——单一来源复用工厂默认表。
_DOC_ETYPES = set(DEFAULT_DOC_TYPE_TO_CLASS) | {"document"}


def resolve_affected_subgraph(change: dict) -> dict:
    """从一条归一化变更解析受影响子图（设备/产品/区域/文档）。

    本期（007 US2）增 `document` 维及文档关联的 `sample`/`product`，供文档/阶段事实变更
    触发受影响子图推理重算（FR-007 下半句，数据模型 §6）。空维仍被剔除 → 非文档变更
    对外形状不变（零回归）。
    """
    subgraph: dict[str, list[str]] = {
        "equipment": [], "product": [], "area": [], "document": [],
    }
    etype = change.get("entity_type")
    eid = change.get("entity_id")
    fields = change.get("fields", {}) or {}

    if etype == "equipment" and eid:
        subgraph["equipment"].append(eid)
    elif etype == "product" and eid:
        subgraph["product"].append(eid)
    elif etype == "area" and eid:
        subgraph["area"].append(eid)
    elif etype in _DOC_ETYPES and eid:
        subgraph["document"].append(str(eid))

    # 关联字段也纳入受影响范围（如设备变更携带在产产品/区域；文档携关联样品/产品）。
    for key in ("product", "prod_code"):
        if fields.get(key):
            subgraph["product"].append(str(fields[key]))
    if fields.get("equipment"):
        subgraph["equipment"].append(str(fields["equipment"]))
    if fields.get("area") or fields.get("room"):
        subgraph["area"].append(str(fields.get("area") or fields.get("room")))
    if fields.get("sample"):
        subgraph.setdefault("sample", []).append(str(fields["sample"]))

    # 去重，剔除空键。
    return {k: sorted(set(v)) for k, v in subgraph.items() if v}


class FactEventBus:
    """进程内事实变更事件总线。"""

    def __init__(self) -> None:
        self._events: list[dict] = []
        self._subscribers: list[Subscriber] = []

    def publish(self, *, connector_id: str, change: dict) -> dict:
        event = {
            "id": str(uuid.uuid4()),
            "connector_id": connector_id,
            "entity_type": change.get("entity_type"),
            "entity_id": change.get("entity_id"),
            "version": change.get("version"),
            "affected_subgraph": resolve_affected_subgraph(change),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self._events.append(event)
        for cb in list(self._subscribers):
            try:
                cb(event)
            except Exception:  # noqa: BLE001 — 订阅者异常不影响发布
                logger.exception("fact event subscriber failed")
        return event

    def subscribe(self, cb: Subscriber) -> Callable[[], None]:
        self._subscribers.append(cb)

        def _unsub() -> None:
            if cb in self._subscribers:
                self._subscribers.remove(cb)

        return _unsub

    def history(self, limit: int = 200) -> list[dict]:
        return self._events[-limit:]


fact_event_bus = FactEventBus()
