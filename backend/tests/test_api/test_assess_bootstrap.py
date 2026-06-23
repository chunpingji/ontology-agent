"""US1 集成测试：评估即落库、流水线自举（G1, FR-001~004, quickstart US1）。

`FakeOntologyEngine.get_individual` 返回 None，真实 `run_assessment` 会 400；故此处
monkeypatch `reasoning_engine.run_assessment` 返回构造的低风险 `AssessmentResult`，
聚焦验证落库自举行为（标识、初始态、动作编排、报告导出、RBAC）。
"""

from __future__ import annotations

import pytest

from app.services.reasoning import engine as reasoning_engine
from app.services.reasoning.engine import AssessmentResult


def _low_risk_result() -> AssessmentResult:
    """低风险结论（不触发 QA 闸门），含一条 requires_recleaning 规则 → 编排一个动作。"""
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


@pytest.fixture
def patch_assess(monkeypatch):
    monkeypatch.setattr(reasoning_engine, "run_assessment",
                        lambda engine, drug_iri, equipment_iris: _low_risk_result())


_ASSESS_BODY = {"drug_iri": "D-100", "equipment_iris": ["EQ-1"], "assessment_type": "full"}


def test_assess_persists_and_returns_identity(client, db, analyst_headers, patch_assess):
    """AC1：评估返回 201 + execution_id + 初始 lifecycle_state（低风险 → effective）。"""
    r = client.post("/api/reasoning/assess", headers=analyst_headers, json=_ASSESS_BODY)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["execution_id"]
    assert body["lifecycle_state"] == "effective"
    assert body["requires_signature"] is False
    assert body["effective"] is True


def test_assess_conclusion_retrievable_with_actions(client, db, analyst_headers, patch_assess):
    """AC2/AC3：落库结论可按标识检索，返回状态 + 结果 + 已编排动作清单。"""
    cid = client.post("/api/reasoning/assess", headers=analyst_headers,
                      json=_ASSESS_BODY).json()["execution_id"]

    r = client.get(f"/api/reasoning/conclusions/{cid}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["lifecycle_state"] == "effective"
    assert body["results"]["requires_recleaning"] is True
    # AC3：隐含动作已编排登记；低风险 → 动作可派发（pending）。
    assert body["actions"], "应至少编排一个动作"
    assert {a["action_type"] for a in body["actions"]} == {"recleaning_task"}
    assert all(a["status"] == "pending" for a in body["actions"])


def test_assess_then_export_report(client, db, analyst_headers, patch_assess):
    """AC4：落库后无需补数据即可直接导出报告。"""
    cid = client.post("/api/reasoning/assess", headers=analyst_headers,
                      json=_ASSESS_BODY).json()["execution_id"]

    r = client.get(f"/api/reports/{cid}")
    assert r.status_code == 200, r.text


def test_assess_requires_identity_header(client, db, patch_assess):
    """无身份头（缺 X-Role）→ 403（FR-017 评估即落库限 senior_analyst）。"""
    r = client.post("/api/reasoning/assess", json=_ASSESS_BODY)
    assert r.status_code == 403, r.text


def test_assess_operator_forbidden(client, db, operator_headers, patch_assess):
    """operator 角色越权 → 403。"""
    r = client.post("/api/reasoning/assess", headers=operator_headers, json=_ASSESS_BODY)
    assert r.status_code == 403, r.text
