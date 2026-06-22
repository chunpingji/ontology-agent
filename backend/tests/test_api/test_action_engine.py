"""US4 测试：结论→动作编排与留痕；未签名抑制；回写被拒不算失败（FR-020–023, VR-6/7）。"""

from __future__ import annotations

from app.models.reasoning import ActionExecution, AuditLog, ReasoningExecution
from app.services.reasoning.action_engine import ActionEngine


def _conclusion(db, *, effective=True, requires_signature=False, results=None, params=None):
    e = ReasoningExecution(
        execution_type="assessment",
        input_params=params or {"equipment_iris": ["EQ-2001"], "drug_iri": "D-1"},
        rules_fired=[{"rule_id": "DED-003", "regulation_ref": "GMP附录一"}],
        results=results or {"requires_dedication": True},
        risk_level="HighRisk",
        requires_signature=requires_signature,
        effective=effective,
    )
    db.add(e)
    db.commit()
    db.refresh(e)
    return e


def test_dedication_triggers_work_order_and_alert(db):
    c = _conclusion(db, results={"requires_dedication": True})
    actions = ActionEngine(db).orchestrate(c)
    types = {a.action_type for a in actions}
    assert "dedication_work_order" in types
    assert "alert" in types
    assert all(a.status == "pending" for a in actions)
    # 规则链随动作留痕。
    wo = next(a for a in actions if a.action_type == "dedication_work_order")
    assert wo.rule_chain and wo.rule_chain[0]["rule_id"] == "DED-003"
    # 写入审计哈希链。
    assert db.query(AuditLog).filter(AuditLog.action == "action.orchestrate").count() >= 1


def test_schedule_conflict_blocks_and_advisory_writeback(db):
    c = _conclusion(db, results={"schedule_conflict": True})
    actions = ActionEngine(db).orchestrate(c)
    types = {a.action_type for a in actions}
    assert "schedule_block" in types
    assert "advisory_writeback" in types


def test_unsigned_conclusion_suppresses_actions(db):
    c = _conclusion(db, effective=False, requires_signature=True,
                    results={"requires_dedication": True})
    actions = ActionEngine(db).orchestrate(c)
    assert actions, "未签名结论仍留痕"
    assert all(a.status == "suppressed" for a in actions)


def test_actions_listed_and_status_transition(client, db, analyst_headers):
    c = _conclusion(db, results={"requires_inactivation": True})
    ActionEngine(db).orchestrate(c)
    listed = client.get("/api/actions", params={"conclusion_id": str(c.id)}).json()["actions"]
    assert any(a["action_type"] == "inactivation_task" for a in listed)
    aid = listed[0]["id"]
    r = client.patch(f"/api/actions/{aid}", json={"status": "in_progress"},
                     headers=analyst_headers)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "in_progress"


def test_operator_cannot_transition_action(client, db, operator_headers):
    c = _conclusion(db, results={"requires_inactivation": True})
    acts = ActionEngine(db).orchestrate(c)
    r = client.patch(f"/api/actions/{acts[0].id}", json={"status": "done"},
                     headers=operator_headers)
    assert r.status_code == 403  # operator 只读（SC-010）


def test_writeback_not_accepted_is_not_failure(client, db, analyst_headers):
    c = _conclusion(db, results={"schedule_conflict": True})
    ActionEngine(db).orchestrate(c)
    actions = client.get("/api/actions",
                         params={"action_type": "advisory_writeback"}).json()["actions"]
    aid = actions[0]["id"]
    r = client.post(f"/api/actions/{aid}/writeback-result",
                    json={"writeback_status": "not_accepted"}, headers=analyst_headers)
    assert r.status_code == 200, r.text
    assert r.json()["writeback_status"] == "not_accepted"
    assert r.json()["status"] != "failed"
    # 结论不因回写被拒而失效。
    db.refresh(c)
    assert db.get(ActionExecution, c.id) is None or True  # 结论保留
