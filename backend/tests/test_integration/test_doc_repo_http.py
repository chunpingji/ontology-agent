"""US4 http 模式契约测试（T037；US4 AS#1）。

契约 [doc-repo-connector C2 http](../../../specs/007-rnd-document-fact-source/contracts/doc-repo-connector.md)：
`http` 模式经 `base_url` + env 注入凭据探活/增量拉取；传输层经构造参数 `http_fetcher`
注入桩端点（避免真实网络）。真实端点返回 EDMS 文档信封，经同一归一化产出与 `inline`/
`upload` **逐字节同一变更骨架**（C2.4）——下游物化路径无分支差异。
pytest 无 asyncio 自动模式 → `asyncio.run()`。
"""

from __future__ import annotations

import asyncio

import pytest

from app.services.integration.doc_repo_connector import DocumentRepositoryConnector
from tests.test_integration.fixtures.doc_repo_changes import (
    DOC_REPO_CHANGE_TTR_V2,
    http_config,
    http_endpoint_envelopes,
    inline_config,
    upload_config,
)


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _edms_token(monkeypatch):
    """token_ref 指向的 env 变量在运行时注入（凭据不入库，FR-010）。"""
    monkeypatch.setenv("EDMS_TOKEN", "edms-secret-xyz")


def _stub_endpoint(captured: dict | None = None):
    """桩 http 端点：返回 EDMS 文档信封；可选记录收到的 base_url/headers/cursor。"""

    async def fetch(base_url, headers, cursor):
        if captured is not None:
            captured["base_url"] = base_url
            captured["headers"] = dict(headers)
            captured["cursor"] = dict(cursor or {})
        return http_endpoint_envelopes()

    return fetch


def _http_conn(*, simulate: str | None = None, captured: dict | None = None, timeout: float = 2.0):
    return DocumentRepositoryConnector(
        http_config(simulate=simulate), {}, timeout=timeout, http_fetcher=_stub_endpoint(captured)
    )


def test_http_test_connection_ok_touches_endpoint_with_creds():
    """C2 http：探活经 base_url + 注入凭据触达端点（桩）→ True。"""
    captured: dict = {}
    conn = _http_conn(captured=captured)
    assert _run(conn.test_connection()) is True
    assert captured["base_url"] == "https://edms.internal/api/changes"
    assert captured["headers"].get("Authorization") == "Bearer edms-secret-xyz"


def test_http_test_connection_timeout_returns_false():
    conn = _http_conn(simulate="timeout", timeout=0.05)
    assert _run(conn.test_connection()) is False


def test_http_fetch_incremental_respects_cursor_and_advances_watermark():
    """C2.1（http）：仅返回 version>cursor.version 的变更；cursor_to.version=max。"""
    conn = _http_conn()
    pull = _run(conn.fetch_incremental({"version": 0}))
    assert [c["entity_id"] for c in pull.changes] == ["doc-TTR-001"]
    assert pull.cursor_to["version"] == 2


def test_http_already_seen_returns_empty_without_regressing_watermark():
    conn = _http_conn()
    pull = _run(conn.fetch_incremental({"version": 2}))
    assert pull.changes == []
    assert pull.cursor_to["version"] == 2


def test_http_yields_same_skeleton_as_inline_and_upload():
    """C2.4：三模 fetch_incremental 产出逐字节同一变更骨架（下游物化无分支差异）。"""
    http = _run(_http_conn().fetch_incremental({}))
    inline = _run(
        DocumentRepositoryConnector(inline_config(), {}, timeout=2.0).fetch_incremental({})
    )
    upload = _run(
        DocumentRepositoryConnector(upload_config(), {}, timeout=2.0).fetch_incremental({})
    )
    assert http.changes == inline.changes == upload.changes
    assert http.changes[0] == DOC_REPO_CHANGE_TTR_V2


def test_http_simulate_timeout_raises_timeout_error():
    conn = _http_conn(simulate="timeout", timeout=0.05)
    with pytest.raises(asyncio.TimeoutError):
        _run(conn.fetch_incremental({}))
