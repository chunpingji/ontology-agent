"""实时事实源轮询调度（能力三, R4, FR-014）。

无消息中间件：启动期 asyncio 后台任务，按各连接器 `poll_interval_seconds` 周期性
触发增量物化。每轮独立 DB 会话；单连接器异常不影响其余（告警写 `last_error`）。
默认关闭（`realtime_polling_enabled=False`），仅生产/手动开启。
"""

from __future__ import annotations

import asyncio
import logging

from app.db import SessionLocal
from app.models.integration import IntegrationConnector
from app.services.integration.materializer import FactMaterializer
from app.services.ontology_engine import ontology_engine

logger = logging.getLogger(__name__)

_DEFAULT_TICK = 2.0  # 调度器基础节拍（秒）


async def _poll_once() -> None:
    """对所有到期的活跃连接器各执行一次增量同步。"""
    db = SessionLocal()
    try:
        connectors = (
            db.query(IntegrationConnector)
            .filter(IntegrationConnector.is_active.is_(True))
            .filter(IntegrationConnector.ingest_mode == "poll")
            .all()
        )
        if not connectors:
            return
        engine = ontology_engine if ontology_engine.is_loaded else None
        if engine is None:
            return  # 本体未加载，跳过本轮
        materializer = FactMaterializer(db=db, engine=engine)
        for c in connectors:
            try:
                await materializer.run_sync(c)
            except Exception:  # noqa: BLE001 — 单连接器异常隔离
                logger.exception("poll sync failed for connector %s", c.id)
    finally:
        db.close()


async def run_polling_loop(tick: float = _DEFAULT_TICK) -> None:
    """后台轮询主循环；被取消时优雅退出（lifespan 关闭时）。"""
    logger.info("realtime polling loop started (tick=%ss)", tick)
    try:
        while True:
            await _poll_once()
            await asyncio.sleep(tick)
    except asyncio.CancelledError:  # pragma: no cover
        logger.info("realtime polling loop stopped")
        raise
