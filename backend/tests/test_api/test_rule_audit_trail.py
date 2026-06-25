"""US3-AS3 / SC-007 — 规则改动可追溯到 操作者 + 批次(TTL导出 + Git SHA) + 时间。

一次纯数据的决策规则改动须留下完整审计链：
  • 操作者 + 时间：审计条目记录 actor 与 created_at；
  • 批次：草稿改动进入发布批次（OntologyRelease）的变更集（change_log）；
  • TTL 导出：受管图可独立外科式导出（不依赖 Git）；
  • Git SHA：批次承载 `ttl_commit_sha` 字段（发布前 None，发布时落 SHA——
    隔离 tmp ontology_dir 非 git 仓库时 _write_and_commit 返回 None，字段仍在）。
"""

from __future__ import annotations

import shutil
from pathlib import Path

from app.config import settings
from app.services.ontology_meta_store import OntologyMetaStore
from app.services.reasoning.seed_declarative import seed_declarative_rules
from tests.conftest import FakeOntologyEngine

_REAL_ONTOLOGY = Path(__file__).resolve().parents[3] / "ontology" / "slpra"


def _seed(db) -> OntologyMetaStore:
    for ttl in _REAL_ONTOLOGY.glob("*.ttl"):
        shutil.copy(ttl, Path(settings.ontology_dir) / ttl.name)
    store = OntologyMetaStore(db=db, engine=FakeOntologyEngine())
    store.project_from_ttl()
    seed_declarative_rules(db)
    return store


def test_rule_change_is_traceable_to_actor_batch_and_time(client, db, analyst_headers):
    _seed(db)

    # 取一条已发布的决策规则，做一次纯数据改动（调整 priority）。
    rules = client.get("/api/ontology/decision-rules").json()
    assert rules, "种子应包含 R-ED/R-SC/R-CP 决策规则"
    rule = rules[0]
    rk, iri, ver = rule["rule_key"], rule["slpra_iri"], rule["version"]

    upd = client.put(
        f"/api/ontology/decision-rules/{rk}",
        headers=analyst_headers,
        json={"expected_version": ver, "priority": rule["priority"] + 7},
    )
    assert upd.status_code == 200, upd.text
    assert upd.json()["priority"] == rule["priority"] + 7
    assert upd.json()["status"] == "draft"

    # --- 操作者 + 时间 ----------------------------------------------------
    audit = client.get("/api/ontology/audit", params={"entity_iri": iri}).json()
    assert audit, "规则改动须留痕"
    top = audit[0]  # list_audit 按 created_at desc 排序
    assert top["action"] == "decision_rule.update"
    assert top["actor"] == "analyst"  # 操作者
    assert top["entity_iri"] == iri
    assert top["created_at"]  # 时间

    # --- 批次：草稿改动进入发布批次的变更集 -------------------------------
    rel = client.post(
        "/api/ontology/releases", headers=analyst_headers, json={"title": "规则阈值微调批次"}
    )
    assert rel.status_code == 201, rel.text
    detail = rel.json()
    rule_changes = [
        cl for cl in detail["change_log"] if cl["entity_table"] == "ontology_decision_rule"
    ]
    assert any(cl["after"]["slpra_iri"] == iri for cl in rule_changes), "改动须纳入批次变更集"

    # --- Git SHA：批次承载提交哈希字段（发布前为 None）--------------------
    assert "ttl_commit_sha" in detail

    # --- TTL 导出：规则即数据，受管图可独立外科式导出 ----------------------
    diff = client.get("/api/ontology/export/diff").json()
    assert "turtle_preview" in diff


def test_audit_is_filterable_by_actor(client, db, analyst_headers):
    _seed(db)
    rule = client.get("/api/ontology/decision-rules").json()[0]
    client.put(
        f"/api/ontology/decision-rules/{rule['rule_key']}",
        headers=analyst_headers,
        json={"expected_version": rule["version"], "regulation_ref": "GMP附录三 §2.1"},
    )
    by_actor = client.get("/api/ontology/audit", params={"actor": "analyst"}).json()
    assert by_actor
    assert all(e["actor"] == "analyst" for e in by_actor)
    assert any(e["action"] == "decision_rule.update" for e in by_actor)
