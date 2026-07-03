"""数据库源连接器（integration 页面 · database system_type）。

把内网数据库接入连接器管理页面，提供探活（test_connection）和结构反射能力。
复用 `ExternalSystemConnector` 协议与工厂分发（`connector_for`），与既有
rest_api/doc_repo/aps 连接器同构。

凭据经 env 注入（FR-006）：`connection_config` **仅含 `dsn_env` 变量名引用**，
绝不含明文 DSN。运行时经 `os.environ[dsn_env]` 注入；env 变量缺失则显式报错。
"""

from __future__ import annotations

import logging
import os
from datetime import datetime

from sqlalchemy import create_engine, inspect, text

from app.services.integration.base import (
    EquipmentStatus,
    ExternalSystemConnector,
    LabResult,
    MaterialStock,
    ProductionBatch,
    TrialInfo,
)

logger = logging.getLogger(__name__)


class DatabaseConnector(ExternalSystemConnector):
    """`database` 连接器：内网数据库的探活 + 结构反射。"""

    def __init__(
        self,
        connection_config: dict | None,
        field_mapping: dict | None = None,
        timeout: float = 5.0,
    ) -> None:
        self.config = connection_config or {}
        self.field_mapping = field_mapping or {}
        self.timeout = timeout
        self.last_status: str | None = None
        self.last_error: str | None = None

    def _resolve_dsn(self) -> str:
        dsn_env = self.config.get("dsn_env", "")
        if not dsn_env:
            raise ValueError("未配置 dsn_env（须填写环境变量名, FR-006）")
        dsn = os.environ.get(dsn_env)
        if not dsn:
            raise ValueError(
                f"凭据环境变量未注入：{dsn_env}（须经 env 注入，不入库, FR-006）"
            )
        return dsn

    async def test_connection(self) -> bool:
        try:
            dsn = self._resolve_dsn()
            kwargs: dict = {}
            if "sqlite" not in dsn:
                kwargs["connect_args"] = {"connect_timeout": int(self.timeout)}
            engine = create_engine(dsn, **kwargs)
            try:
                with engine.connect() as conn:
                    conn.execute(text("SELECT 1"))
            finally:
                engine.dispose()
            self.last_status = "ok"
            self.last_error = None
            return True
        except Exception as exc:  # noqa: BLE001
            self.last_status = "error"
            self.last_error = f"{type(exc).__name__}: {exc}"
            logger.warning("DB 连接器探活失败：%s", self.last_error)
            return False

    def reflect_tables(self) -> list[dict]:
        """反射源库表结构，返回表名+列信息列表。"""
        dsn = self._resolve_dsn()
        engine = create_engine(dsn)
        try:
            inspector = inspect(engine)
            schema = self.config.get("schema")
            tables = inspector.get_table_names(schema=schema)
            include = self.config.get("include_tables")
            if include:
                wanted = set(include)
                tables = [t for t in tables if t in wanted]
            result = []
            for t in tables:
                cols = inspector.get_columns(t, schema=schema)
                result.append({
                    "table": t,
                    "columns": [
                        {"name": c["name"], "type": str(c.get("type"))}
                        for c in cols
                    ],
                })
            return result
        finally:
            engine.dispose()

    # --- ExternalSystemConnector 抽象方法（DB 源不供业务域实时数据 → 最小实现）---
    async def fetch_production_schedule(
        self, start_date: datetime, end_date: datetime
    ) -> list[ProductionBatch]:
        return []

    async def fetch_equipment_status(
        self, equipment_ids: list[str]
    ) -> list[EquipmentStatus]:
        return []

    async def fetch_material_inventory(
        self, material_ids: list[str]
    ) -> list[MaterialStock]:
        return []

    async def fetch_lab_results(self, batch_ids: list[str]) -> list[LabResult]:
        return []

    async def fetch_clinical_trial_info(
        self, trial_ids: list[str]
    ) -> list[TrialInfo]:
        return []
