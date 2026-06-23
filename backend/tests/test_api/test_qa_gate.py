"""US2 集成测试：高风险结论落库自动 arm QA 待签闸门（G2, FR-005~007, SC-003）。

monkeypatch `reasoning_engine.run_assessment` 返回高风险 / 低风险结论，验证落库时
据风险判据自动分流初始态与动作抑制。
"""

from __future__ import annotations

import pytest

from app.services.reasoning import engine as reasoning_engine
from app.services.reasoning.engine import AssessmentResult

_ASSESS_BODY = {"drug_iri": "D-1", "equipment_iris": ["EQ-1"], "assessment_type": "full"}


def _high_risk_result() -> AssessmentResult:
    """命中高风险判据：requires_dedication=True → 须 QA 签批 + 编排专用化动作。"""
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


def _low_risk_result() -> AssessmentResult:
    r = AssessmentResult()
    r.risk_level = "LowRisk"
    r.requires_dedication = False
    r.rules_fired = [
        {
            "rule_id": "REC-001",
            "rule_group": "contamination_risk",
            "description": "残留风险需复清洁",
            "inputs": {"pathway": "residue"},
            "conclusion": {"requires_recleaning": True, "risk_level": "LowRisk"},
        }
    ]
    r.scenarios = []
    r.maco = None
    r.recommendations = []
    return r


def _patch(monkeypatch, result):
    monkeypatch.setattr(reasoning_engine, "run_assessment",
                        lambda engine, drug_iri, equipment_iris: result)


def test_high_risk_armed_pending_with_suppressed_actions(client, db, analyst_headers, monkeypatch):
    """AC1：高风险落库 → pending_signature；动作全 suppressed、对外派发数 0（SC-003）。"""
    _patch(monkeypatch, _high_risk_result())
    body = client.post("/api/reasoning/assess", headers=analyst_headers,
                       json=_ASSESS_BODY).json()
    assert body["lifecycle_state"] == "pending_signature"
    assert body["requires_signature"] is True
    assert body["effective"] is False

    detail = client.get(f"/api/reasoning/conclusions/{body['execution_id']}").json()
    assert detail["actions"], "高风险结论仍应登记动作（仅抑制，不派发）"
    assert all(a["status"] == "suppressed" for a in detail["actions"])
    # 零对外派发：无任何非抑制态动作。
    assert not [a for a in detail["actions"] if a["status"] != "suppressed"]


def test_low_risk_effective_with_pending_actions(client, db, analyst_headers, monkeypatch):
    """AC2：低风险落库 → effective；动作 pending（可派发）。"""
    _patch(monkeypatch, _low_risk_result())
    body = client.post("/api/reasoning/assess", headers=analyst_headers,
                       json=_ASSESS_BODY).json()
    assert body["lifecycle_state"] == "effective"
    assert body["requires_signature"] is False

    detail = client.get(f"/api/reasoning/conclusions/{body['execution_id']}").json()
    assert detail["actions"]
    assert all(a["status"] == "pending" for a in detail["actions"])
