"""文档库（EDMS/eTMF）连接器（能力三, 007 US1/US4, FR-001/010/014/015/018）。

把研发各阶段文档"作为记录"接成增量事实源。复用 `ExternalSystemConnector` 协议、
`IncrementalPull` 水位语义与 `FactMaterializer` 物化/留痕/事件管线（零并行框架）。

三接入模式（FR-015）：
- `inline`：从 `connection_config["inline_changes"]` 读归一化变更（确定性，供契约/集成测试）。
- `upload`：从 `connection_config["upload_payload"]` 读上传信封（doc_id/doc_type/title/metadata），
  经 `_normalize_upload` 归一化为**同一变更骨架**，与 `inline` 逐字节一致（parity）。
- `http`（US4 生产接入）：经 `base_url` + env 注入凭据探活/增量拉取真实 EDMS/eTMF；端点返回
  文档信封，经**同一** `_normalize_upload` 归一化 → 与 inline/upload 逐字节同骨架（C2.4）。

凭据经 env 注入（FR-010）：`connection_config` **仅含变量名引用**（如 `token_ref="EDMS_TOKEN"`），
绝不含明文 token/密钥（宪章：密钥 MUST NOT 入库/提交）。运行时经 `os.environ` 解析为请求头；
凭据从不进入 `changes`/审计/日志（最小暴露）。
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime

from app.services.integration.aps_connector import IncrementalPull
from app.services.integration.base import (
    EquipmentStatus,
    ExternalSystemConnector,
    LabResult,
    MaterialStock,
    ProductionBatch,
    TrialInfo,
)

logger = logging.getLogger(__name__)


async def _httpx_get_changes(
    base_url: str, headers: dict, cursor: dict, timeout: float
) -> list[dict]:
    """生产默认 HTTP 传输：经 httpx GET 拉取 EDMS/eTMF 文档信封（凭据仅在请求头）。

    返回端点的文档信封列表（doc_id/doc_type/version/title/metadata 形态）；连接器再经
    `_normalize_upload` 归一化为统一变更骨架。测试经构造参数 `http_fetcher` 注入桩端点，
    或打桩本函数，从而无需真实网络。
    """
    import httpx  # 延迟导入：仅 http 生产路径需要（既有直接依赖，零新增）。

    since = int((cursor or {}).get("version", 0))
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(base_url, headers=headers, params={"since_version": since})
        resp.raise_for_status()
        data = resp.json()
    return data if isinstance(data, list) else list(data.get("changes", []))


class DocumentRepositoryConnector(ExternalSystemConnector):
    """doc_repo 连接器：文档生命周期变更的增量拉取 + 超时处理（inline / upload / http 三模）。"""

    def __init__(self, connection_config: dict | None, field_mapping: dict | None = None,
                 timeout: float = 5.0, *, http_fetcher=None) -> None:
        self.config = connection_config or {}
        self.field_mapping = field_mapping or {}
        self.timeout = timeout
        # http 传输层接缝（可注入桩端点）；None → 生产默认 `_httpx_get_changes`。
        self._http_fetcher = http_fetcher

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
            return None
        # http：真实探活——解析注入凭据 + 经 base_url 触达端点（其余模式无外部依赖→直接成功）。
        if self._mode() == "http":
            await self._http_fetch(self._require_base_url(), self._auth_headers(), {})
        return None

    async def fetch_incremental(self, cursor: dict | None) -> IncrementalPull:
        """拉取 cursor 之后的文档变更；超时上抛 `asyncio.TimeoutError`。"""
        return await asyncio.wait_for(self._pull(cursor or {}), timeout=self.timeout)

    async def _pull(self, cursor: dict) -> IncrementalPull:
        if self.config.get("simulate") == "timeout":
            await asyncio.sleep(self.timeout + 1)

        last_version = int(cursor.get("version", 0))
        all_changes = await self._collect_changes(cursor)
        fresh = [c for c in all_changes if int(c.get("version", 0)) > last_version]
        max_version = max(
            [last_version, *[int(c.get("version", 0)) for c in fresh]]
        )
        return IncrementalPull(changes=fresh, cursor_to={"version": max_version})

    # --- 接入模式归一化（FR-015 parity）---------------------------------

    def _mode(self) -> str:
        return str(self.config.get("access_mode", "inline")).lower()

    async def _collect_changes(self, cursor: dict) -> list[dict]:
        """按 `access_mode` 产出统一变更骨架（inline 直读 / upload / http 信封归一化）。"""
        if self._mode() == "http":
            envelopes = await self._http_fetch(
                self._require_base_url(), self._auth_headers(), cursor
            )
            # http 端点返回文档信封 → 同一 _normalize_upload 归一化（C2.4 单一物化路径）。
            return [self._normalize_upload(p) for p in (envelopes or [])]
        return self._normalized_changes()

    def _normalized_changes(self) -> list[dict]:
        """同步模式（inline 直读 / upload 信封归一化）的统一变更骨架。

        upload 额外并入经 webhook 增量推送、已归一化的 `inline_changes`——使**同一**上传
        连接器持续累积新上传文档（无须每次上传新建连接器；webhook 写 inline_changes，
        归一化骨架逐字节一致）。无 `inline_changes` 时与原行为逐字节一致（既有 parity 不变）。
        """
        inline = list(self.config.get("inline_changes", []))
        if self._mode() == "upload":
            payload = [self._normalize_upload(p) for p in self.config.get("upload_payload", [])]
            return payload + inline
        return inline

    # --- http 凭据注入 / 传输（FR-010 / C3）------------------------------

    def _require_base_url(self) -> str:
        base_url = self.config.get("base_url")
        if not base_url:
            raise ValueError("http access_mode 需 connection_config.base_url")
        return str(base_url)

    def _auth_headers(self) -> dict[str, str]:
        """据 `connection_config` 的 `*_ref` 变量名引用，从 `os.environ` 解析凭据 → 请求头。

        明文凭据 MUST NOT 入 `connection_config`（FR-010）；env 变量缺失则明确报错，绝不
        静默用空凭据触达端点。凭据仅存在于本方法返回的请求头，从不写入 `changes`/审计/日志。
        """
        headers: dict[str, str] = {}
        token_ref = self.config.get("token_ref")
        if token_ref:
            token = os.environ.get(token_ref)
            if not token:
                raise ValueError(f"凭据环境变量未注入：{token_ref}")
            headers["Authorization"] = f"Bearer {token}"
        api_key_ref = self.config.get("api_key_ref")
        if api_key_ref:
            api_key = os.environ.get(api_key_ref)
            if not api_key:
                raise ValueError(f"凭据环境变量未注入：{api_key_ref}")
            headers["X-API-Key"] = api_key
        return headers

    async def _http_fetch(self, base_url: str, headers: dict, cursor: dict) -> list[dict]:
        """HTTP 增量拉取（可注入桩端点；默认走 `_httpx_get_changes` 真实 GET）。"""
        if self._http_fetcher is not None:
            return await self._http_fetcher(base_url, headers, cursor)
        return await _httpx_get_changes(base_url, headers, cursor, self.timeout)

    @staticmethod
    def _normalize_upload(payload: dict) -> dict:
        """上传信封 → 同一变更骨架（doc_id→entity_id 等），与 inline 逐字节一致。"""
        return {
            "entity_id": payload.get("doc_id"),
            "entity_type": payload.get("doc_type"),
            "version": payload.get("version"),
            "label": payload.get("title"),
            "fields": dict(payload.get("metadata") or {}),
        }

    # --- ExternalSystemConnector 抽象方法（文档源不供业务域实时数据 → 最小实现）---

    async def fetch_production_schedule(
        self, start_date: datetime, end_date: datetime
    ) -> list[ProductionBatch]:
        return []

    async def fetch_equipment_status(self, equipment_ids: list[str]) -> list[EquipmentStatus]:
        return []

    async def fetch_material_inventory(self, material_ids: list[str]) -> list[MaterialStock]:
        return []

    async def fetch_lab_results(self, batch_ids: list[str]) -> list[LabResult]:
        return []

    async def fetch_clinical_trial_info(self, trial_ids: list[str]) -> list[TrialInfo]:
        return []
