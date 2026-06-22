"""APS（高级排产）实时连接器（能力三, R4, FR-014/018）。

替换 `StubConnector`：支持增量拉取（轮询）与超时/不可达处理。凭据经 env/`settings`
注入，`connection_config` 不含明文凭据（R7）。

可测试性接缝：当 `connection_config["source_mode"] == "inline"` 时，从配置内联的
`inline_changes` 读取增量（用于契约/集成测试，确定性、无外部依赖）。生产模式
（`http`）经 `base_url` + env 注入凭据真实探活/拉取（此处留出实现位）。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime

from app.services.integration.base import (
    EquipmentStatus,
    ExternalSystemConnector,
    LabResult,
    MaterialStock,
    ProductionBatch,
    TrialInfo,
)

logger = logging.getLogger(__name__)


@dataclass
class IncrementalPull:
    """一次增量拉取结果：变更列表 + 推进后的水位。"""

    changes: list[dict] = field(default_factory=list)
    cursor_to: dict = field(default_factory=dict)


class APSConnector(ExternalSystemConnector):
    """APS 连接器：增量拉取 + 超时处理。"""

    def __init__(self, connection_config: dict | None, field_mapping: dict | None = None,
                 timeout: float = 5.0) -> None:
        self.config = connection_config or {}
        self.field_mapping = field_mapping or {}
        self.timeout = timeout

    # --- 探活 / 增量 ------------------------------------------------------

    async def test_connection(self) -> bool:
        try:
            await asyncio.wait_for(self._probe(), timeout=self.timeout)
            return True
        except (asyncio.TimeoutError, ConnectionError):
            return False

    async def _probe(self) -> None:
        if self.config.get("simulate") == "timeout":
            await asyncio.sleep(self.timeout + 1)
        # inline / http 模式探活成功（http 真实探活留待生产接入）。
        return None

    async def fetch_incremental(self, cursor: dict | None) -> IncrementalPull:
        """拉取 cursor 之后的增量变更；超时上抛 `asyncio.TimeoutError`。"""
        return await asyncio.wait_for(self._pull(cursor or {}), timeout=self.timeout)

    async def _pull(self, cursor: dict) -> IncrementalPull:
        if self.config.get("simulate") == "timeout":
            await asyncio.sleep(self.timeout + 1)

        last_version = int(cursor.get("version", 0))
        all_changes: list[dict] = list(self.config.get("inline_changes", []))
        fresh = [c for c in all_changes if int(c.get("version", 0)) > last_version]
        max_version = max(
            [last_version, *[int(c.get("version", 0)) for c in fresh]]
        )
        return IncrementalPull(changes=fresh, cursor_to={"version": max_version})

    # --- ExternalSystemConnector 抽象方法（APS 主要供排产/设备，其余最小实现）---

    async def fetch_production_schedule(
        self, start_date: datetime, end_date: datetime
    ) -> list[ProductionBatch]:
        batches: list[ProductionBatch] = []
        for ch in self.config.get("inline_changes", []):
            if ch.get("entity_type") == "product":
                batches.append(ProductionBatch(
                    batch_id=ch.get("entity_id", ""),
                    product_name=ch.get("label", ""),
                    equipment_ids=ch.get("fields", {}).get("equipment_ids", []),
                ))
        return batches

    async def fetch_equipment_status(self, equipment_ids: list[str]) -> list[EquipmentStatus]:
        out: list[EquipmentStatus] = []
        for ch in self.config.get("inline_changes", []):
            if ch.get("entity_type") == "equipment" and ch.get("entity_id") in equipment_ids:
                out.append(EquipmentStatus(
                    equipment_id=ch["entity_id"],
                    status=ch.get("fields", {}).get("status", "idle"),
                ))
        return out

    async def fetch_material_inventory(self, material_ids: list[str]) -> list[MaterialStock]:
        return []

    async def fetch_lab_results(self, batch_ids: list[str]) -> list[LabResult]:
        return []

    async def fetch_clinical_trial_info(self, trial_ids: list[str]) -> list[TrialInfo]:
        return []
