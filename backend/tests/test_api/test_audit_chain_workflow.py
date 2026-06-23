"""Polish T029：US1–US4 全程审计事件齐备连续 + 篡改定位（FR-018, SC-006）。

驱动一条贯穿落库 / 动作流转 / 增量重算 / QA 拒绝 / QA 签批的端到端流程，断言哈希链
覆盖全部关键动作类型且 `verify` 通过；随后篡改一条记录验证可定位首个断点。
"""

from __future__ import annotations

import pytest

from app.services.reasoning import engine as reasoning_engine
from app.services.reasoning.engine import AssessmentResult


def _result(drug_iri):
    r = AssessmentResult()
    if str(drug_iri).startswith("HR"):
        r.risk_level = "HighRisk"
        r.requires_dedication = True
        r.rules_fired = [{
            "rule_id": "DED-003", "rule_group": "equipment_dedication",
            "description": "需专用化", "inputs": {},
            "conclusion": {"requires_dedication": True}, "regulation_ref": "GMP附录一",
        }]
    else:
        r.risk_level = "LowRisk"
        r.requires_dedication = False
        r.rules_fired = [{
            "rule_id": "REC-001", "rule_group": "contamination_risk",
            "description": "需复清洁", "inputs": {"pathway": "residue"},
            "conclusion": {"requires_recleaning": True, "risk_level": "LowRisk"},
        }]
    r.scenarios = []
    r.maco = None
    r.recommendations = []
    return r


@pytest.fixture
def patched(monkeypatch):
    monkeypatch.setattr(reasoning_engine, "run_assessment",
                        lambda engine, drug_iri, equipment_iris: _result(drug_iri))


def _assess(client, headers, drug):
    return client.post("/api/reasoning/assess", headers=headers,
                       json={"drug_iri": drug, "equipment_iris": ["EQ-1"]}).json()


def test_full_workflow_audit_chain_complete_and_tamper_detected(
    client, db, analyst_headers, qa_headers, patched
):
    # A) 落库低风险 → effective + 动作编排（persist / transition / action.orchestrate）
    lr = _assess(client, analyst_headers, "LR-1")
    detail = client.get(f"/api/reasoning/conclusions/{lr['execution_id']}").json()
    act_id = detail["actions"][0]["id"]

    # B) 人工流转动作 → action.transition
    client.patch(f"/api/actions/{act_id}", headers=analyst_headers,
                 json={"status": "in_progress"})

    # C) 事实变更触发增量重算 → reasoning.recompute + transition(T5) + action.void
    client.post("/api/reasoning/incremental", headers=analyst_headers,
                json={"affected_subgraph": {"equipment": ["EQ-1"]}})

    # D/E) 高风险落库待签 → QA 拒绝（compliance.reject + transition(T4) + action.void）
    hr1 = _assess(client, analyst_headers, "HR-1")
    client.post("/api/compliance/reject", headers=qa_headers, json={
        "conclusion_id": hr1["execution_id"], "username": "qa01",
        "password": "qa-reauth", "reason": "不符合共线",
    })

    # F/G) 高风险落库待签 → QA 签批（compliance.sign + transition(T3)）
    hr2 = _assess(client, analyst_headers, "HR-2")
    r = client.post("/api/compliance/signatures", headers=qa_headers, json={
        "conclusion_id": hr2["execution_id"], "username": "qa01",
        "password": "qa-reauth", "meaning": "复核批准",
    })
    assert r.status_code == 201, r.text

    # 全程关键事件齐备。
    entries = client.get("/api/compliance/audit", headers=analyst_headers).json()["entries"]
    actions = {e["action"] for e in entries}
    expected = {
        "reasoning.persist", "reasoning.transition", "action.orchestrate",
        "reasoning.recompute", "action.void", "compliance.reject", "compliance.sign",
        "action.transition",
    }
    assert expected.issubset(actions), f"缺失事件：{expected - actions}"

    # 链连续完整。
    body = client.get("/api/compliance/audit/verify", headers=analyst_headers).json()
    assert body["ok"] is True

    # 篡改首条记录 → verify 定位首个断点。
    from app.models.reasoning import AuditLog
    row = db.query(AuditLog).filter(AuditLog.seq == 1).first()
    row.details = {"tampered": True}
    db.commit()
    broken = client.get("/api/compliance/audit/verify", headers=analyst_headers).json()
    assert broken["ok"] is False
    assert broken["broken_at_seq"] == 1
