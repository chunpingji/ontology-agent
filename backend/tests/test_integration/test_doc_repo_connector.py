"""doc_repo 连接器契约测试（007 US1，T006）。

[doc-repo-connector C2.1/C2.2/C2.4](../../../specs/007-rnd-document-fact-source/contracts/doc-repo-connector.md)：
`inline` 增量（`version>cursor.version`、`cursor_to.version=max`）、`upload`/`inline` 产出**同一
变更骨架**、`simulate=='timeout'` → `asyncio.TimeoutError`。pytest 无 asyncio 自动模式 → `asyncio.run()`。
"""

from __future__ import annotations

import asyncio

import pytest

from app.services.integration.doc_repo_connector import DocumentRepositoryConnector
from tests.test_integration.fixtures.doc_repo_changes import (
    DOC_REPO_CHANGE_STAB_PRECLIN,
    DOC_REPO_CHANGE_TTR_V2,
    inline_config,
    upload_config,
)


def _run(coro):
    return asyncio.run(coro)


def test_inline_increment_respects_cursor_and_advances_watermark():
    """C2.1：仅返回 version>cursor.version 的变更；cursor_to.version=max。"""
    conn = DocumentRepositoryConnector(inline_config(), {}, timeout=2.0)
    pull = _run(conn.fetch_incremental({"version": 0}))
    assert [c["entity_id"] for c in pull.changes] == ["doc-TTR-001"]
    assert pull.cursor_to["version"] == 2


def test_inline_already_seen_returns_empty_without_regressing_watermark():
    """C2.1：水位已达 v2 → 再拉无新增、水位不回退。"""
    conn = DocumentRepositoryConnector(inline_config(), {}, timeout=2.0)
    pull = _run(conn.fetch_incremental({"version": 2}))
    assert pull.changes == []
    assert pull.cursor_to["version"] == 2


def test_inline_multi_change_watermark_is_max():
    conn = DocumentRepositoryConnector(
        inline_config(changes=[DOC_REPO_CHANGE_STAB_PRECLIN, DOC_REPO_CHANGE_TTR_V2]),
        {},
        timeout=2.0,
    )
    pull = _run(conn.fetch_incremental({}))
    assert {c["entity_id"] for c in pull.changes} == {"doc-STAB-009", "doc-TTR-001"}
    assert pull.cursor_to["version"] == 2  # max(1, 2)


def test_upload_honors_webhook_appended_inline_changes():
    """upload 连接器在 upload_payload 之外并入 webhook 增量推送的 inline_changes。

    webhook 把已归一化骨架追加进 `inline_changes`——同一上传连接器据此**累积**新上传文档
    （无须每次上传新建连接器）。骨架经更高传输 version 推进水位 → 增量同步逐条物化。
    """
    cfg = upload_config()  # upload_payload = [TTR v2]
    cfg["inline_changes"] = [
        {**DOC_REPO_CHANGE_STAB_PRECLIN, "version": 3}  # webhook 推送的新上传（更高水位）
    ]
    conn = DocumentRepositoryConnector(cfg, {}, timeout=2.0)
    pull = _run(conn.fetch_incremental({"version": 2}))  # 水位已含 upload_payload 的 v2
    assert [c["entity_id"] for c in pull.changes] == ["doc-STAB-009"]  # 仅新增项 fresh
    assert pull.cursor_to["version"] == 3


def test_upload_and_inline_yield_same_change_skeleton():
    """C2.2/C2.4：upload 归一化后与 inline 产出逐字节同一骨架（FR-015 parity）。"""
    inline = _run(
        DocumentRepositoryConnector(inline_config(), {}, timeout=2.0).fetch_incremental({})
    )
    upload = _run(
        DocumentRepositoryConnector(upload_config(), {}, timeout=2.0).fetch_incremental({})
    )
    assert inline.changes == upload.changes
    assert upload.changes[0] == DOC_REPO_CHANGE_TTR_V2


def test_simulate_timeout_raises_timeout_error():
    """C2.3：simulate=='timeout' → fetch_incremental 抛 asyncio.TimeoutError。"""
    conn = DocumentRepositoryConnector(inline_config(simulate="timeout"), {}, timeout=0.05)
    with pytest.raises(asyncio.TimeoutError):
        _run(conn.fetch_incremental({}))


def test_test_connection_ok_and_timeout():
    ok = DocumentRepositoryConnector(inline_config(), {}, timeout=2.0)
    assert _run(ok.test_connection()) is True
    bad = DocumentRepositoryConnector(inline_config(simulate="timeout"), {}, timeout=0.05)
    assert _run(bad.test_connection()) is False
