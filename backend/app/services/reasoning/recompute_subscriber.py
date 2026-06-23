"""事实事件 → 增量重算的订阅桥（003, G3, FR-010, research R4-D-A）。

为既有 `fact_event_bus` 注册订阅者，把物化产生的事实变更事件桥接到
`incremental.recompute_subgraph`，使「事实变更 → 结论刷新」无人工触发。订阅者回调
在**独立会话**中运行（发布线程与请求事务解耦），读已提交结论表（C-5 顺序不变式）。

`make_recompute_subscriber` 暴露可注入 `session_factory` / `engine` 的工厂，便于单测
直接调用回调、绑定测试库会话，无需走真实 `SessionLocal`。
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from app.services.reasoning import incremental

logger = logging.getLogger(__name__)


def make_recompute_subscriber(
    session_factory: Callable | None = None,
    engine=None,
    *,
    close_session: bool = True,
) -> Callable[[dict], None]:
    """构造事实事件订阅回调。

    - ``session_factory``：会话工厂（默认 `app.db.SessionLocal`）；单测可注入测试库工厂。
    - ``engine``：本体引擎（默认全局 `ontology_engine`，仅在已加载时传入；否则 None →
      `recompute_subgraph` 退化为保留既有结论结果）。
    - ``close_session``：回调结束是否关闭会话（注入共享测试会话时置 False）。
    """

    def _on_fact_event(event: dict) -> None:
        subgraph = (event or {}).get("affected_subgraph") or {}
        if not any(subgraph.values()):
            return  # 无受影响实体 → 无需重算。

        if session_factory is not None:
            factory = session_factory
        else:
            from app.db import SessionLocal

            factory = SessionLocal

        if engine is not None:
            eng = engine
        else:
            from app.services.ontology_engine import ontology_engine

            eng = ontology_engine if ontology_engine.is_loaded else None

        session = factory()
        try:
            incremental.recompute_subgraph(session, subgraph, engine=eng)
        except Exception:  # noqa: BLE001 — 订阅者异常不得影响发布方
            logger.exception("auto-recompute subscriber failed for event %s",
                             (event or {}).get("id"))
        finally:
            if close_session:
                session.close()

    return _on_fact_event
