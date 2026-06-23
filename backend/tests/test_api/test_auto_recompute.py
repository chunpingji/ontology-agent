"""US3 集成测试：事实变更自动召回增量重算（G3, FR-010~013, AC1~4）。

直接驱动订阅者回调（注入测试会话工厂 + engine=None），验证「发布事件 → 自动重算」
桥接：相交生效结论被取代刷新、不相交/失效/待签不被触动、旧动作 voided。
"""

from __future__ import annotations

from app.models.reasoning import ActionExecution, ReasoningExecution
from app.services.reasoning.recompute_subscriber import make_recompute_subscriber


def _conclusion(db, *, lifecycle, equipment, superseded_by=None, requires_sig=False):
    e = ReasoningExecution(
        execution_type="assessment",
        input_params={"drug_iri": "D-1", "equipment_iris": [equipment]},
        rules_fired=[],
        results={"risk_level": "LowRisk"},
        risk_level="LowRisk",
        affected_subgraph={"equipment": [equipment]},
        requires_signature=requires_sig,
        effective=(lifecycle == "effective"),
        superseded_by=superseded_by,
        lifecycle_state=lifecycle,
    )
    db.add(e)
    db.commit()
    db.refresh(e)
    return e


def _action(db, conclusion_id, status="pending"):
    a = ActionExecution(conclusion_id=conclusion_id, action_type="recleaning_task",
                        status=status, payload={})
    db.add(a)
    db.commit()
    db.refresh(a)
    return a


def _fire(db, subgraph):
    cb = make_recompute_subscriber(session_factory=lambda: db, engine=None,
                                   close_session=False)
    cb({"id": "evt-1", "affected_subgraph": subgraph})


def test_intersecting_effective_is_recomputed_and_superseded(db):
    """AC1/AC3：相交生效结论自动重算 → 旧 superseded + superseded_by 链接到新结论。"""
    old = _conclusion(db, lifecycle="effective", equipment="EQ-1")

    _fire(db, {"equipment": ["EQ-1"]})

    db.refresh(old)
    assert old.lifecycle_state == "superseded"
    assert old.superseded_by is not None
    new = db.get(ReasoningExecution, old.superseded_by)
    assert new is not None
    assert new.lifecycle_state == "effective"


def test_old_actions_voided_on_supersede(db):
    """AC4：被取代旧结论的非终态动作置 voided。"""
    old = _conclusion(db, lifecycle="effective", equipment="EQ-1")
    act = _action(db, old.id, status="pending")

    _fire(db, {"equipment": ["EQ-1"]})

    db.refresh(act)
    assert act.status == "voided"


def test_non_intersecting_untouched(db):
    """AC2：不相交结论不被触动。"""
    other = _conclusion(db, lifecycle="effective", equipment="EQ-99")

    _fire(db, {"equipment": ["EQ-1"]})

    db.refresh(other)
    assert other.lifecycle_state == "effective"
    assert other.superseded_by is None


def test_pending_and_superseded_skipped(db):
    """AC2：待签 / 已失效结论不参与重算（仅显式 effective 入选, FR-011）。"""
    pending = _conclusion(db, lifecycle="pending_signature", equipment="EQ-1",
                          requires_sig=True)
    gone = _conclusion(db, lifecycle="superseded", equipment="EQ-1")

    _fire(db, {"equipment": ["EQ-1"]})

    db.refresh(pending)
    db.refresh(gone)
    assert pending.lifecycle_state == "pending_signature"
    assert gone.lifecycle_state == "superseded"
