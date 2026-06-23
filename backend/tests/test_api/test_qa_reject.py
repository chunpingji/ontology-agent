"""US2 集成测试：QA 签批生效 / 拒绝终结闭环（G2, FR-008/009/020, AC3~5）。"""

from __future__ import annotations

import pytest

from app.models.reasoning import ActionExecution
from app.services.reasoning import engine as reasoning_engine
from app.services.reasoning.engine import AssessmentResult

_ASSESS_BODY = {"drug_iri": "D-1", "equipment_iris": ["EQ-1"], "assessment_type": "full"}


def _high_risk_result() -> AssessmentResult:
    r = AssessmentResult()
    r.risk_level = "HighRisk"
    r.requires_dedication = True
    r.rules_fired = [
        {
            "rule_id": "DED-003",
            "rule_group": "equipment_dedication",
            "description": "强致敏需专用化",
            "inputs": {},
            "conclusion": {"requires_dedication": True},
            "regulation_ref": "GMP附录一",
        }
    ]
    r.scenarios = []
    r.maco = None
    r.recommendations = []
    return r


@pytest.fixture
def pending_id(client, db, analyst_headers, monkeypatch):
    """落库一条高风险待签结论，返回其 execution_id。"""
    monkeypatch.setattr(reasoning_engine, "run_assessment",
                        lambda engine, drug_iri, equipment_iris: _high_risk_result())
    body = client.post("/api/reasoning/assess", headers=analyst_headers,
                       json=_ASSESS_BODY).json()
    assert body["lifecycle_state"] == "pending_signature"
    return body["execution_id"]


def _sign(client, headers, cid, meaning="已复核批准生效"):
    return client.post("/api/compliance/signatures", headers=headers, json={
        "conclusion_id": cid, "username": "qa01",
        "password": "qa-reauth", "meaning": meaning,
    })


def _reject(client, headers, cid, reason="不符合共线条件"):
    return client.post("/api/compliance/reject", headers=headers, json={
        "conclusion_id": cid, "username": "qa01",
        "password": "qa-reauth", "reason": reason,
    })


def test_qa_sign_makes_effective_and_releases(client, db, qa_headers, pending_id):
    """AC3：QA 签名后结论转 effective + 动作解抑 + 审计齐备。"""
    r = _sign(client, qa_headers, pending_id)
    assert r.status_code == 201, r.text
    assert r.json()["effective"] is True

    detail = client.get(f"/api/reasoning/conclusions/{pending_id}").json()
    assert detail["lifecycle_state"] == "effective"
    assert all(a["status"] != "suppressed" for a in detail["actions"])

    actions = client.get("/api/compliance/audit",
                         params={"action": "compliance.sign"},
                         headers=qa_headers).json()
    assert any(e["entity_iri"] == pending_id for e in actions["entries"])


def test_no_signature_cannot_dispatch(client, db, qa_headers, pending_id):
    """AC4：未签名前结论不生效、动作维持抑制（无越闸派发）。"""
    detail = client.get(f"/api/reasoning/conclusions/{pending_id}").json()
    assert detail["effective"] is False
    assert all(a["status"] == "suppressed" for a in detail["actions"])


def test_qa_reject_terminal_voids_actions(client, db, qa_headers, pending_id):
    """AC5：QA 拒绝 → rejected 终态 + 被抑动作 voided + 审计。"""
    r = _reject(client, qa_headers, pending_id)
    assert r.status_code == 201, r.text
    assert r.json()["lifecycle_state"] == "rejected"
    assert r.json()["voided_actions"] >= 1

    detail = client.get(f"/api/reasoning/conclusions/{pending_id}").json()
    assert detail["lifecycle_state"] == "rejected"
    assert all(a["status"] == "voided" for a in detail["actions"])

    audit = client.get("/api/compliance/audit",
                       params={"action": "compliance.reject"},
                       headers=qa_headers).json()
    assert any(e["entity_iri"] == pending_id for e in audit["entries"])


def test_rejected_cannot_be_signed_or_rejected_again(client, db, qa_headers, pending_id):
    """AC5：终态 rejected 不可再签批 / 再拒绝 → 409。"""
    assert _reject(client, qa_headers, pending_id).status_code == 201
    assert _sign(client, qa_headers, pending_id).status_code == 409
    assert _reject(client, qa_headers, pending_id).status_code == 409


def test_effective_cannot_be_rejected(client, db, qa_headers, pending_id):
    """已签批生效的结论不可再被拒绝 → 409。"""
    assert _sign(client, qa_headers, pending_id).status_code == 201
    assert _reject(client, qa_headers, pending_id).status_code == 409
