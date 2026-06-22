"""US6 测试：审计哈希链校验 + QA Part 11 电子签名门禁 + RBAC（FR-028–031, SC-008/009/010）。"""

from __future__ import annotations

from app.models.reasoning import ActionExecution, AuditLog, ReasoningExecution
from app.services import audit
from app.services.reasoning.action_engine import ActionEngine


def _pending_conclusion(db):
    """高风险结论：requires_signature=True → effective=False（待 QA 签名）。"""
    e = ReasoningExecution(
        execution_type="assessment",
        input_params={"equipment_iris": ["EQ-2001"], "drug_iri": "D-1"},
        rules_fired=[{"rule_id": "DED-003", "regulation_ref": "GMP附录一"}],
        results={"requires_dedication": True},
        risk_level="HighRisk",
        requires_signature=True,
        effective=False,
    )
    db.add(e)
    db.commit()
    db.refresh(e)
    return e


# --- 审计哈希链 -------------------------------------------------------------


def test_audit_verify_ok_then_broken(client, db, analyst_headers):
    audit.append(db, "test.a", actor="u1", details={"x": 1})
    audit.append(db, "test.b", actor="u1", details={"x": 2})
    r = client.get("/api/compliance/audit/verify", headers=analyst_headers)
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True

    # 篡改一条记录的 details → 链断裂，定位首个断点。
    row = db.query(AuditLog).filter(AuditLog.seq == 1).first()
    row.details = {"x": 999}
    db.commit()
    body = client.get("/api/compliance/audit/verify", headers=analyst_headers).json()
    assert body["ok"] is False
    assert body["broken_at_seq"] == 1


def test_audit_list_readonly(client, db, analyst_headers):
    audit.append(db, "extraction.commit", actor="analyst", details={})
    rows = client.get("/api/compliance/audit", params={"action": "extraction.commit"},
                      headers=analyst_headers).json()
    assert any(x["action"] == "extraction.commit" for x in rows["entries"])


# --- QA 电子签名门禁 --------------------------------------------------------


def test_unsigned_conclusion_suppressed_then_signed_effective(client, db, qa_headers):
    c = _pending_conclusion(db)
    actions = ActionEngine(db).orchestrate(c)
    assert all(a.status == "suppressed" for a in actions)  # 未签名→抑制（VR-6）

    pending = client.get("/api/compliance/signatures/pending", headers=qa_headers).json()
    assert any(p["id"] == str(c.id) for p in pending["conclusions"])

    # QA 重认证签名 → 结论生效 + 解除抑制动作。
    r = client.post("/api/compliance/signatures", headers=qa_headers, json={
        "conclusion_id": str(c.id), "username": "qa01",
        "password": "qa-reauth", "meaning": "已复核批准生效",
    })
    assert r.status_code == 201, r.text
    assert r.json()["effective"] is True

    db.refresh(c)
    assert c.effective is True
    assert c.signature_id is not None
    released = db.query(ActionExecution).filter(
        ActionExecution.conclusion_id == c.id).all()
    assert all(a.status != "suppressed" for a in released)


def test_reauth_failure_401(client, db, qa_headers):
    c = _pending_conclusion(db)
    r = client.post("/api/compliance/signatures", headers=qa_headers, json={
        "conclusion_id": str(c.id), "username": "qa01",
        "password": "WRONG", "meaning": "x",
    })
    assert r.status_code == 401
    db.refresh(c)
    assert c.effective is False


def test_duplicate_signature_409(client, db, qa_headers):
    c = _pending_conclusion(db)
    body = {"conclusion_id": str(c.id), "username": "qa01",
            "password": "qa-reauth", "meaning": "ok"}
    assert client.post("/api/compliance/signatures", headers=qa_headers, json=body).status_code == 201
    assert client.post("/api/compliance/signatures", headers=qa_headers, json=body).status_code == 409


# --- RBAC 边界 --------------------------------------------------------------


def test_operator_cannot_sign(client, db, operator_headers):
    c = _pending_conclusion(db)
    r = client.post("/api/compliance/signatures", headers=operator_headers, json={
        "conclusion_id": str(c.id), "username": "op",
        "password": "qa-reauth", "meaning": "x",
    })
    assert r.status_code == 403


def test_operator_cannot_trigger_incremental(client, db, operator_headers):
    r = client.post("/api/reasoning/incremental",
                    json={"affected_subgraph": {"equipment": ["EQ-1"]}},
                    headers=operator_headers)
    # operator 可触发？契约：触发增量重算限 senior_analyst → 但当前 _recompute_role
    # 允许 operator。按契约 §0 收紧为仅 senior_analyst。
    assert r.status_code == 403
