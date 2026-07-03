"""能力三 DTO：连接器 / 物化运行 / 事实事件 / 增量重算（contracts/integration-realtime-api）。"""

from __future__ import annotations

import ipaddress
from datetime import datetime
from typing import Any
from uuid import UUID
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, model_validator

# rest_api 分页方式白名单（其余 → 422）。
_REST_PAGINATION_STYLES = {"offset", "page", "cursor"}
# auth 结构键（非凭据，可不带 `_env` 后缀）；其余键 MUST 为 `*_env` 引用（FR-006）。
_AUTH_STRUCTURAL_KEYS = {"scheme", "header", "query_param", "in", "prefix"}
# 内网主机名后缀白名单（单标签主机与私有/环回 IP 亦视为内网）。
_INTRANET_SUFFIXES = (
    ".local", ".internal", ".intranet", ".lan", ".corp",
    ".home", ".localdomain", ".localhost", ".svc", ".cluster.local",
)


def _is_intranet_host(host: str) -> bool:
    """判定 `host` 是否内网/内部网络（FR-022：仅允许内网 base_url，公网/云端拒绝）。

    私有/环回/链路本地 IP、`localhost`、单标签主机名、内网后缀（.local/.internal/…）
    视为内网；公网 FQDN（api.public-cloud.com 之类）视为外网。air-gap 姿态下无 DNS，
    故用主机名启发式而非解析（[[air-gap-allows-intranet-sources]]）。
    """
    host = (host or "").strip().lower()
    if not host:
        return False
    try:  # 字面 IP：仅私有/环回/链路本地放行，公网 IP 拒绝。
        return ipaddress.ip_address(host).is_private or \
            ipaddress.ip_address(host).is_loopback or \
            ipaddress.ip_address(host).is_link_local
    except ValueError:
        pass
    if host == "localhost" or "." not in host:  # 单标签主机名 → 内网。
        return True
    return host.endswith(_INTRANET_SUFFIXES)


def _validate_rest_api_config(config: dict | None) -> None:
    """校验 `rest_api` 连接配置：内网 base_url + 仅 `*_env` 凭据引用 + 合法分页方式。

    违规抛 `ValueError`（FastAPI → 422）。**凭据绝不入库**：明文 token/密钥 → 拒绝，
    只接受 `token_env`/`api_key_env` 之类环境变量名引用（FR-006/FR-022）。
    """
    config = config or {}
    base_url = str(config.get("base_url") or "")
    host = urlparse(base_url).hostname if base_url else None
    if not host or not _is_intranet_host(host):
        raise ValueError(
            f"base_url 必须为内网/内部网络地址（FR-022），拒绝公网/云端：{base_url!r}"
        )

    auth = config.get("auth") or {}
    if isinstance(auth, dict):
        for key, value in auth.items():
            if key in _AUTH_STRUCTURAL_KEYS or key.endswith("_env"):
                continue
            # 任何非结构键且非 `*_env` 引用者，视为明文凭据 → 拒绝（FR-006）。
            raise ValueError(
                f"auth 仅接受 `*_env` 环境变量名引用，禁止明文凭据入库（FR-006）：{key!r}"
            )

    pagination = config.get("pagination") or {}
    style = str(pagination.get("style") or "cursor").lower()
    if style not in _REST_PAGINATION_STYLES:
        raise ValueError(
            f"pagination.style 必须 ∈ {sorted(_REST_PAGINATION_STYLES)}：{style!r}"
        )


def _validate_database_config(config: dict | None) -> None:
    """校验 `database` 连接配置：仅接受 `dsn_env` 环境变量名引用（FR-006）。

    明文 DSN 字符串含凭据 → 拒绝；仅以环境变量名引用，运行时经 os.environ 注入。
    """
    config = config or {}
    dsn_env = config.get("dsn_env")
    if not dsn_env or not isinstance(dsn_env, str) or not dsn_env.strip():
        raise ValueError(
            "database 连接器须配置 dsn_env（环境变量名引用，不含明文 DSN, FR-006）"
        )
    if "://" in dsn_env:
        raise ValueError(
            "dsn_env 须为环境变量名（如 SOURCE_DB_DSN），不可填写明文连接串（FR-006）"
        )


_FILE_FORMATS = {"excel", "word", "pdf"}


def _validate_file_config(config: dict | None) -> None:
    """校验 `file` 连接配置：须指定 file_path + 合法 format。"""
    config = config or {}
    file_path = config.get("file_path")
    if not file_path or not isinstance(file_path, str) or not file_path.strip():
        raise ValueError("file 连接器须配置 file_path（文件路径或 glob 模式）")
    fmt = (config.get("format") or "").lower()
    if fmt and fmt not in _FILE_FORMATS:
        raise ValueError(
            f"format 须 ∈ {sorted(_FILE_FORMATS)}（或留空自动推断）：{fmt!r}"
        )


class ConnectorCreate(BaseModel):
    system_type: str
    name: str
    ingest_mode: str = "poll"
    poll_interval_seconds: int = 2
    connection_config: dict | None = None  # 不含明文凭据（R7）
    field_mapping: dict | None = None

    @model_validator(mode="after")
    def _check_config(self) -> "ConnectorCreate":
        st = (self.system_type or "").lower()
        if st == "rest_api":
            _validate_rest_api_config(self.connection_config)
        elif st == "database":
            _validate_database_config(self.connection_config)
        elif st == "file":
            _validate_file_config(self.connection_config)
        return self


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


class ActionBrief(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    action_type: str
    status: str


class ConclusionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    execution_type: str
    risk_level: str | None = None
    # 003：lifecycle_state 为状态真理来源；effective/superseded_by 为兼容映射（FR-014）。
    lifecycle_state: str | None = None
    effective: bool = False
    requires_signature: bool = False
    affected_subgraph: dict | None = None
    superseded_by: UUID | None = None
    results: dict | None = None
    actions: list[ActionBrief] | None = None


class IncrementalResponse(BaseModel):
    refreshed: list[ConclusionResponse]
