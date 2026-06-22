"""US2 契约/集成测试：跨源对齐归组 + 人工审核闭环 + DB 源（FR-009~013）。"""

from __future__ import annotations

import io
import os

import openpyxl
from sqlalchemy import create_engine, text


def _xlsx_dupe_equipment() -> bytes:
    """两行同一设备编号（跨源/重复）→ 期望归入同一 group_key。"""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["设备编号", "设备名称", "材质"])
    ws.append(["CT64201", "压片机A", "316L不锈钢"])
    ws.append(["CT64201", "压片机A（台账副本）", "316L不锈钢"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _make_config(client, source_type="excel"):
    r = client.post("/api/extraction/configs", json={
        "name": "设备台账",
        "target_class_iri": "http://slpra.org/equipment#Equipment",
        "source_type": source_type,
        "column_mapping": {"设备编号": "equipmentID", "设备名称": "equipmentName",
                           "材质": "material"},
    })
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _create_job(client, headers, cfg_id, content, source_type="excel", db_source=None):
    data = {"source_type": source_type, "config_id": cfg_id}
    if db_source:
        import json as _json
        data["db_source"] = _json.dumps(db_source)
    files = {"file": ("src.xlsx", content, "application/octet-stream")} if content else None
    r = client.post("/api/extraction/jobs", data=data, files=files, headers=headers)
    assert r.status_code == 202, r.text
    return r.json()


def _all_candidates(grouped):
    return grouped["ungrouped"] + [c for g in grouped["groups"] for c in g["candidates"]]


def test_candidates_grouped_with_canonical(client, analyst_headers):
    cfg_id = _make_config(client)
    job = _create_job(client, analyst_headers, cfg_id, _xlsx_dupe_equipment())
    cands = client.get(f"/api/extraction/jobs/{job['id']}/candidates").json()
    # 同一设备编号归为一组。
    groups = [g for g in cands["groups"] if len(g["candidates"]) >= 2]
    assert groups, f"expected a multi-member group, got {cands}"
    grp = groups[0]
    assert grp["group_key"]
    canon = [c for c in grp["candidates"] if c["is_canonical"]]
    assert len(canon) == 1, "每组恰好一个规范实例"
    assert grp["canonical_candidate_id"] == canon[0]["id"]


def test_review_confirm_commits_only_confirmed(client, analyst_headers):
    cfg_id = _make_config(client)
    job = _create_job(client, analyst_headers, cfg_id, _xlsx_dupe_equipment())
    cands = _all_candidates(client.get(f"/api/extraction/jobs/{job['id']}/candidates").json())
    c0, c1 = cands[0], cands[1]

    r = client.put(f"/api/extraction/candidates/{c0['id']}/review",
                   json={"status": "confirmed"}, headers=analyst_headers)
    assert r.status_code == 200, r.text
    assert r.json()["review_status"] == "committed"
    assert r.json()["committed_iri"]

    r = client.put(f"/api/extraction/candidates/{c1['id']}/review",
                   json={"status": "rejected"}, headers=analyst_headers)
    assert r.status_code == 200, r.text
    assert r.json()["review_status"] == "rejected"
    assert not r.json()["committed_iri"]


def test_merge_endpoint(client, analyst_headers):
    cfg_id = _make_config(client)
    job = _create_job(client, analyst_headers, cfg_id, _xlsx_dupe_equipment())
    cands = _all_candidates(client.get(f"/api/extraction/jobs/{job['id']}/candidates").json())
    target, src = cands[0], cands[1]
    r = client.post("/api/extraction/candidates/merge",
                    json={"target_id": target["id"], "source_ids": [src["id"]]},
                    headers=analyst_headers)
    assert r.status_code == 200, r.text
    merged = {c["id"]: c for c in r.json()}
    assert merged[src["id"]]["merged_into_id"] == target["id"]
    assert merged[src["id"]]["review_status"] == "merged"


def test_split_endpoint(client, analyst_headers):
    cfg_id = _make_config(client)
    job = _create_job(client, analyst_headers, cfg_id, _xlsx_dupe_equipment())
    cands = _all_candidates(client.get(f"/api/extraction/jobs/{job['id']}/candidates").json())
    c0 = cands[0]
    r = client.post(f"/api/extraction/candidates/{c0['id']}/split",
                    json={"splits": [{"equipmentID": "CT64201A"}, {"equipmentID": "CT64201B"}]},
                    headers=analyst_headers)
    assert r.status_code == 200, r.text
    assert len(r.json()) == 2
    assert all(d["review_status"] == "pending" for d in r.json())


def test_database_source_produces_class_and_link_candidates(client, analyst_headers, tmp_path):
    # 构造只读源库：两表 + 外键。
    db_file = tmp_path / "source.db"
    src_dsn = f"sqlite:///{db_file}"
    eng = create_engine(src_dsn)
    with eng.begin() as conn:
        conn.execute(text("CREATE TABLE room (id INTEGER PRIMARY KEY, grade TEXT)"))
        conn.execute(text(
            "CREATE TABLE equipment (id INTEGER PRIMARY KEY, name TEXT, "
            "room_id INTEGER REFERENCES room(id))"
        ))
    eng.dispose()

    os.environ["SLPRA_TEST_SOURCE_DSN"] = src_dsn
    try:
        cfg_id = _make_config(client, source_type="database")
        job = _create_job(
            client, analyst_headers, cfg_id, content=None, source_type="database",
            db_source={"dsn_ref": "SLPRA_TEST_SOURCE_DSN", "include_tables": ["room", "equipment"]},
        )
        final = client.get(f"/api/extraction/jobs/{job['id']}").json()
        assert final["status"] == "reviewing", final
        cands = _all_candidates(
            client.get(f"/api/extraction/jobs/{job['id']}/candidates").json())
        kinds = {c["candidate_kind"] for c in cands}
        assert "class" in kinds, f"expected class candidates, kinds={kinds}"
        assert "link" in kinds, f"expected link (FK) candidates, kinds={kinds}"
    finally:
        os.environ.pop("SLPRA_TEST_SOURCE_DSN", None)
