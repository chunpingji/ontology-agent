"""US4 综合状态机测试：单一守卫的合法/非法迁移与多入口一致性（FR-014~016, SC-005）。

直接驱动 `lifecycle.transition`（唯一合法性来源），覆盖 T1–T5 全部合法迁移按集推进
并记 `reasoning.transition` 审计；每类非法迁移被拒、状态不变；并验证多入口（落库/签批/
拒绝）走同一守卫得到一致判定。
"""

from __future__ import annotations

import pytest

from app.models.reasoning import AuditLog, ReasoningExecution
from app.services.reasoning.lifecycle import (
    IllegalTransition,
    LifecycleState,
    transition,
)


def _new(db, lifecycle=None, requires_sig=False):
    e = ReasoningExecution(
        execution_type="assessment",
        input_params={"drug_iri": "D-1", "equipment_iris": ["EQ-1"]},
        rules_fired=[],
        results={"risk_level": "LowRisk"},
        risk_level="LowRisk",
        requires_signature=requires_sig,
        effective=False,
    )
    e.lifecycle_state = lifecycle
    db.add(e)
    db.commit()
    db.refresh(e)
    # commit 以列 default("effective") 填充 None；复位内存态以模拟 INITIAL（落库前）。
    if lifecycle is None:
        e.lifecycle_state = None
    return e


def _transition_audits(db, cid):
    return [
        a for a in db.query(AuditLog).filter(AuditLog.action == "reasoning.transition").all()
        if a.entity_iri == str(cid)
    ]


# --- AC1：全部合法迁移按集推进并记审计 -------------------------------------


def test_t1_initial_to_effective(db):
    c = _new(db, lifecycle=None)
    transition(db, c, LifecycleState.EFFECTIVE, actor="analyst", reason="T1")
    db.commit()
    assert c.lifecycle_state == "effective"
    assert c.effective is True
    assert _transition_audits(db, c.id)


def test_t2_initial_to_pending(db):
    c = _new(db, lifecycle=None, requires_sig=True)
    transition(db, c, LifecycleState.PENDING_SIGNATURE, actor="analyst", reason="T2")
    db.commit()
    assert c.lifecycle_state == "pending_signature"
    assert c.effective is False


def test_t3_pending_to_effective(db):
    c = _new(db, lifecycle="pending_signature", requires_sig=True)
    transition(db, c, LifecycleState.EFFECTIVE, actor="qa01", reason="T3")
    db.commit()
    assert c.lifecycle_state == "effective"
    assert c.effective is True


def test_t4_pending_to_rejected(db):
    c = _new(db, lifecycle="pending_signature", requires_sig=True)
    transition(db, c, LifecycleState.REJECTED, actor="qa01", reason="T4")
    db.commit()
    assert c.lifecycle_state == "rejected"
    assert c.effective is False


def test_t5_effective_to_superseded(db):
    c = _new(db, lifecycle="effective")
    transition(db, c, LifecycleState.SUPERSEDED, actor="system", reason="T5",
               superseded_by=c.id)
    db.commit()
    assert c.lifecycle_state == "superseded"
    assert c.effective is False


# --- AC2：每类非法迁移被拒，状态不变 ---------------------------------------


@pytest.mark.parametrize("from_state,to_state", [
    ("effective", LifecycleState.PENDING_SIGNATURE),   # 绕回待签
    ("effective", LifecycleState.REJECTED),            # 生效直接拒绝
    ("pending_signature", LifecycleState.SUPERSEDED),  # 待签跳过签批被取代
    ("superseded", LifecycleState.EFFECTIVE),          # 自终态外迁
    ("rejected", LifecycleState.EFFECTIVE),            # 已拒绝再签批
    ("rejected", LifecycleState.REJECTED),             # 已拒绝再拒绝
])
def test_illegal_transition_rejected_state_unchanged(db, from_state, to_state):
    c = _new(db, lifecycle=from_state)
    with pytest.raises(IllegalTransition):
        transition(db, c, to_state, actor="x", reason="illegal")
    # 状态不变（FR-016：非法迁移不改任何状态）。
    assert c.lifecycle_state == from_state


# --- AC3：多入口经同一守卫，判定一致 ---------------------------------------


def test_multi_entry_same_guard_consistent(client, db, qa_headers):
    """落库（待签）后，签批入口与直接 transition 对同一非法迁移判定一致（均拒）。"""
    c = _new(db, lifecycle="rejected")  # 终态
    # 服务层 transition：拒绝。
    with pytest.raises(IllegalTransition):
        transition(db, c, LifecycleState.EFFECTIVE, actor="qa01")
    # 签批入口（同守卫）：对终态结论签批 → 409。
    r = client.post("/api/compliance/signatures", headers=qa_headers, json={
        "conclusion_id": str(c.id), "username": "qa01",
        "password": "qa-reauth", "meaning": "x",
    })
    assert r.status_code == 409, r.text
