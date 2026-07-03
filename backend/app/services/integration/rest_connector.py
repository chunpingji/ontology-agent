"""通用 REST/JSON 源连接器（014 US2，R7/FR-008）。

把内网 REST/JSON 端点接成声明驱动抽取的源实体读取器。复用 `ExternalSystemConnector`
协议与工厂分发（`connector_for`），与既有 doc_repo/aps 连接器同构。

凭据经 env 注入（FR-006）：`connection_config.auth` **仅含变量名引用**（`token_env` /
`api_key_env`），绝不含明文 token/密钥。运行时经 `os.environ` 解析为请求头；env 变量缺失
则显式报错，绝不静默用空凭据触达端点；凭据从不写入候选/审计/日志（最小暴露）。

传输接缝（离线确定性测试，零真实网络）：
- `http_fetcher`：注入 `async (url, headers, params) -> dict` 桩端点（同 doc_repo DI 模式）。
- `connection_config["inline_pages"]`：内联分页响应（游标索引寻址，镜像 `PaginatedRestStub`）。
- `connection_config["simulate"]` ∈ {unreachable, timeout}：触发不可达/超时（驱动优雅降级）。
无接缝时走生产默认 `httpx` GET。
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime

import httpx

from app.services.integration.base import (
    EquipmentStatus,
    ExternalSystemConnector,
    LabResult,
    MaterialStock,
    ProductionBatch,
    TrialInfo,
)

logger = logging.getLogger(__name__)

# 传输失败统一视作「内网不可达」→ 优雅降级（读取器捕获，绝不崩溃, R12/FR-019）。
TRANSPORT_ERRORS = (httpx.HTTPError, asyncio.TimeoutError, ConnectionError, OSError)

_MAX_PAGES = 1000  # 分页护栏：避免游标环/超大源无界拉取。


class RestConnector(ExternalSystemConnector):
    """`rest_api` 连接器：内网 REST/JSON 端点的分页拉取 + env 凭据注入 + 探活。"""

    def __init__(self, connection_config: dict | None, field_mapping: dict | None = None,
                 timeout: float = 5.0, *, http_fetcher=None) -> None:
        self.config = connection_config or {}
        self.field_mapping = field_mapping or {}
        self.timeout = timeout
        # 传输层接缝（可注入桩端点）；None → 生产默认 httpx / 内联分页。
        self._http_fetcher = http_fetcher
        # 健康态（供工厂/读取器写回连接器行 last_status/last_error, FR-008）。
        self.last_status: str | None = None
        self.last_error: str | None = None

    # --- 探活 -------------------------------------------------------------
    async def test_connection(self) -> bool:
        """最小请求探活：可达→True；不可达/超时/缺凭据→False（不抛异常, R12）。"""
        try:
            await asyncio.wait_for(self._probe(), timeout=self.timeout + 3.0)
            self.last_status = "ok"
            self.last_error = None
            return True
        except (*TRANSPORT_ERRORS, ValueError) as exc:
            self.last_status = "error"
            self.last_error = type(exc).__name__
            return False

    async def _probe(self) -> None:
        headers = self._auth_headers()
        await self._fetch_page(headers, {})

    # --- 分页拉取 ---------------------------------------------------------
    async def fetch_items(self) -> list[dict]:
        """遍历分页 → 返回全部条目（`list[dict]`）。

        凭据经 env 注入（缺失即抛 ValueError，FR-006）；传输失败上抛 `TRANSPORT_ERRORS`
        供读取器捕获降级。分页方式 `cursor`（跟随 `cursor_path` 至空）/ `offset` / `page`
        （递增至空页）。
        """
        headers = self._auth_headers()
        pg = self._pagination()
        style = (pg.get("style") or "cursor").lower()
        data_path = pg.get("data_path", "$.data")
        items: list[dict] = []

        if style == "cursor":
            cursor_path = pg.get("cursor_path", "$.next")
            cursor = None
            for _ in range(_MAX_PAGES):
                params = {} if cursor in (None, "") else {"cursor": cursor}
                page = await self._fetch_page(headers, params)
                items.extend(self._as_list(self._json_walk(page, data_path)))
                cursor = self._json_walk(page, cursor_path)
                if cursor in (None, ""):
                    break
        else:  # offset / page — 递增至空页。
            page_size = int(pg.get("page_size", 100) or 100)
            offset = 0
            for i in range(_MAX_PAGES):
                params = ({"offset": offset, "limit": page_size} if style == "offset"
                          else {"page": i + 1, "page_size": page_size})
                page = await self._fetch_page(headers, params)
                batch = self._as_list(self._json_walk(page, data_path))
                if not batch:
                    break
                items.extend(batch)
                offset += len(batch)

        self.last_status = "ok"
        return items

    # --- 传输（接缝 → 内联 → 生产 httpx）---------------------------------
    async def _fetch_page(self, headers: dict, params: dict) -> dict:
        url = self._url()
        if self._http_fetcher is not None:
            return await self._http_fetcher(url, headers, params)
        if "inline_pages" in self.config or self.config.get("simulate"):
            return await self._serve_inline(params)
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(url, headers=headers, params=params)
            resp.raise_for_status()
            return resp.json()

    async def _serve_inline(self, params: dict) -> dict:
        """内联分页桩（游标索引寻址，镜像 `PaginatedRestStub`；`simulate` 触发传输错误）。"""
        sim = self.config.get("simulate")
        if sim in ("unreachable", "timeout"):
            if sim == "timeout":
                raise httpx.ReadTimeout("intranet timeout (simulated)", request=None)
            raise httpx.ConnectError("intranet connection refused (simulated)", request=None)
        pages = self.config.get("inline_pages") or []
        cursor = params.get("cursor")
        idx = 0 if cursor in (None, "") else int(cursor)
        if idx >= len(pages):
            return {"data": [], "next": None}
        return pages[idx]

    # --- 凭据注入（FR-006）-----------------------------------------------
    def _auth_headers(self) -> dict[str, str]:
        """据 `auth` 的 `*_env` 变量名引用，从 `os.environ` 解析凭据 → 请求头。

        明文凭据 MUST NOT 入 `connection_config`（FR-006）；env 变量缺失则显式报错，绝不
        静默用空凭据触达端点。凭据仅存在于返回的请求头，从不写入候选/审计/日志。
        """
        headers: dict[str, str] = {}
        auth = self.config.get("auth") or {}
        scheme = (auth.get("scheme") or "").lower()
        if scheme == "bearer":
            env = auth.get("token_env")
            if env:
                token = os.environ.get(env)
                if not token:
                    raise ValueError(f"凭据环境变量未注入：{env}（须经 env 注入，不入库, FR-006）")
                headers["Authorization"] = f"Bearer {token}"
        elif scheme == "api_key":
            env = auth.get("api_key_env")
            if env:
                key = os.environ.get(env)
                if not key:
                    raise ValueError(f"凭据环境变量未注入：{env}（须经 env 注入，不入库, FR-006）")
                headers[auth.get("header") or "X-API-Key"] = key
        return headers

    # --- 配置/JSON 辅助 ---------------------------------------------------
    def _pagination(self) -> dict:
        return self.config.get("pagination") or {}

    def _url(self) -> str:
        base = str(self.config.get("base_url") or "").rstrip("/")
        endpoint = str(self.config.get("endpoint") or "")
        if endpoint and not endpoint.startswith("/"):
            endpoint = "/" + endpoint
        return f"{base}{endpoint}"

    @staticmethod
    def _json_walk(node, path: str):
        """按点分 JSON 路径（`$.a.b`）向下取值；缺失/非 dict 中断返回 None。"""
        for key in (k for k in (path or "").replace("$", "").split(".") if k):
            node = node.get(key) if isinstance(node, dict) else None
        return node

    @staticmethod
    def _as_list(node) -> list:
        return node if isinstance(node, list) else []

    # --- ExternalSystemConnector 抽象方法（REST 源不供业务域实时数据 → 最小实现）---
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
