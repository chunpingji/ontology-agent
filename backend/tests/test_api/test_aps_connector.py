"""US3 契约测试：APSConnector 真实 test/sync 语义 + 超时保留上一良好状态（FR-014/018）。"""

from __future__ import annotations

import asyncio

import pytest

from app.services.integration.aps_connector import APSConnector


def test_test_connection_ok_inline():
    c = APSConnector({"source_mode": "inline", "base_url": "http://aps.internal"})
    assert asyncio.run(c.test_connection()) is True


def test_test_connection_timeout_returns_false():
    c = APSConnector({"source_mode": "inline", "simulate": "timeout"}, timeout=0.05)
    assert asyncio.run(c.test_connection()) is False


def test_incremental_filters_by_cursor():
    cfg = {"source_mode": "inline", "inline_changes": [
        {"entity_type": "equipment", "entity_id": "EQ-1", "version": 1, "fields": {}},
        {"entity_type": "equipment", "entity_id": "EQ-1", "version": 2, "fields": {}},
    ]}
    c = APSConnector(cfg)
    pull = asyncio.run(c.fetch_incremental({"version": 1}))
    assert [ch["version"] for ch in pull.changes] == [2]
    assert pull.cursor_to["version"] == 2


def test_incremental_timeout_raises():
    c = APSConnector({"source_mode": "inline", "simulate": "timeout"}, timeout=0.05)
    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(c.fetch_incremental({}))
