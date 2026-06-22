"""US3 集成测试：增量物化→A-Box + 运行留痕 + 事件发布 + 幂等去重（FR-015/016/018/019）。"""

from __future__ import annotations


def _create_connector(client, headers, changes, *, simulate=None):
    cfg = {"source_mode": "inline", "inline_changes": changes}
    if simulate:
        cfg["simulate"] = simulate
    r = client.post("/api/integration/connectors", json={
        "system_type": "APS", "name": "生产排期-APS",
        "ingest_mode": "poll", "poll_interval_seconds": 2,
        "connection_config": cfg,
        "field_mapping": {"equipment": "eq_no"},
    }, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_sync_materializes_facts_run_and_event(client, analyst_headers):
    cid = _create_connector(client, analyst_headers, [
        {"entity_type": "equipment", "entity_id": "EQ-2001", "version": 1,
         "label": "压片机A", "fields": {"status": "running", "product": "P-A"}},
    ])
    r = client.post(f"/api/integration/connectors/{cid}/sync", headers=analyst_headers)
    assert r.status_code == 202, r.text

    runs = client.get(f"/api/integration/connectors/{cid}/runs").json()["runs"]
    assert runs, "expected a materialization run"
    run = runs[0]
    assert run["status"] == "success"
    assert run["change_count"] == 1
    assert run["cursor_to"] is not None
    assert run["event_ids"]

    # 事实事件带 affected_subgraph（设备）。
    events = client.get("/api/integration/events").json()["events"]
    assert any("EQ-2001" in str(e["affected_subgraph"]) for e in events)

    # A-Box 投影：facts 端点可见。
    facts = client.get("/api/integration/facts").json()["facts"]
    assert any("EQ-2001" in str(f) for f in facts)


def test_timeout_preserves_last_good_cursor(client, analyst_headers):
    cid = _create_connector(client, analyst_headers, [], simulate="timeout")
    r = client.post(f"/api/integration/connectors/{cid}/sync", headers=analyst_headers)
    assert r.status_code == 202, r.text
    runs = client.get(f"/api/integration/connectors/{cid}/runs").json()["runs"]
    assert runs[0]["status"] == "timeout"
    assert runs[0]["cursor_to"] is None  # 不推进水位（保留上一良好状态, FR-018）

    c = client.get("/api/integration/connectors").json()
    conn = next(x for x in c if x["id"] == cid)
    assert conn["last_status"] == "timeout"


def test_idempotent_dedup_out_of_order(client, analyst_headers):
    # 重复 v2 + 乱序 v1：仅物化一次 v2，丢弃旧版本（FR-019/VR-3）。
    cid = _create_connector(client, analyst_headers, [
        {"entity_type": "equipment", "entity_id": "EQ-9", "version": 2, "fields": {"s": "b"}},
        {"entity_type": "equipment", "entity_id": "EQ-9", "version": 2, "fields": {"s": "b"}},
        {"entity_type": "equipment", "entity_id": "EQ-9", "version": 1, "fields": {"s": "a"}},
    ])
    r = client.post(f"/api/integration/connectors/{cid}/sync", headers=analyst_headers)
    assert r.status_code == 202, r.text
    runs = client.get(f"/api/integration/connectors/{cid}/runs").json()["runs"]
    assert runs[0]["change_count"] == 1, runs[0]
