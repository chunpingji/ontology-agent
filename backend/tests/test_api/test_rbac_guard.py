"""US4 RBAC 测试：operator 对写/迁移/签批/动作流转一律 403（FR-017, SC-007, AC4）。

RBAC 守卫在端点体执行前（`Depends`）拦截，故无需构造合法业务数据即可断言 403。
"""

from __future__ import annotations

from uuid import uuid4

_ASSESS_BODY = {"drug_iri": "D-1", "equipment_iris": ["EQ-1"], "assessment_type": "full"}


def test_operator_cannot_assess(client, db, operator_headers):
    r = client.post("/api/reasoning/assess", headers=operator_headers, json=_ASSESS_BODY)
    assert r.status_code == 403, r.text


def test_operator_cannot_sign(client, db, operator_headers):
    r = client.post("/api/compliance/signatures", headers=operator_headers, json={
        "conclusion_id": str(uuid4()), "username": "op",
        "password": "x", "meaning": "试图签批",
    })
    assert r.status_code == 403, r.text


def test_operator_cannot_reject(client, db, operator_headers):
    r = client.post("/api/compliance/reject", headers=operator_headers, json={
        "conclusion_id": str(uuid4()), "username": "op",
        "password": "x", "reason": "试图拒绝",
    })
    assert r.status_code == 403, r.text


def test_operator_cannot_patch_action(client, db, operator_headers):
    r = client.patch(f"/api/actions/{uuid4()}", headers=operator_headers,
                     json={"status": "done"})
    assert r.status_code == 403, r.text


def test_operator_cannot_trigger_incremental(client, db, operator_headers):
    r = client.post("/api/reasoning/incremental", headers=operator_headers,
                    json={"affected_subgraph": {"equipment": ["EQ-1"]}})
    assert r.status_code == 403, r.text
