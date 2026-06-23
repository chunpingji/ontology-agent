"""结论生命周期状态机——单一迁移合法性来源（003, data-model §2, FR-014/015/016）。

把 002 隐式散在 ``effective``/``superseded_by``/``ActionExecution.status`` 三处的状态
固化为 ``reasoning_executions.lifecycle_state`` 显式四态，并以 ``LEGAL_TRANSITIONS`` 作为
**唯一**合法性来源。所有入口（落库 T1/T2、签批 T3、拒绝 T4、增量取代 T5）均经
``transition()`` 校验：非法迁移一致抛 ``IllegalTransition`` 且**不改任何状态**。

``transition()`` 自身 ``commit=False``——由各调用入口统一提交，保证「迁移 + 动作作废 +
签名/取代」同事务原子化。
"""

from __future__ import annotations

from enum import Enum

from sqlalchemy.orm import Session

from app.models.reasoning import (
    LIFECYCLE_EFFECTIVE,
    LIFECYCLE_PENDING_SIGNATURE,
    LIFECYCLE_REJECTED,
    LIFECYCLE_SUPERSEDED,
    ReasoningExecution,
)
from app.services import audit


class LifecycleState(str, Enum):
    PENDING_SIGNATURE = LIFECYCLE_PENDING_SIGNATURE
    EFFECTIVE = LIFECYCLE_EFFECTIVE
    SUPERSEDED = LIFECYCLE_SUPERSEDED
    REJECTED = LIFECYCLE_REJECTED


# 落库（T1/T2）以「创建即置态」形式经同一合法集校验：from 为初始无前态（None）。
INITIAL: None = None

# 唯一合法迁移集（data-model §2.2）。(from, to) ∉ 集合 → 非法（FR-016）。
LEGAL_TRANSITIONS: set[tuple[str | None, str]] = {
    (INITIAL, LifecycleState.EFFECTIVE.value),            # T1 落库（不需签批）
    (INITIAL, LifecycleState.PENDING_SIGNATURE.value),    # T2 落库（需签批）
    (LifecycleState.PENDING_SIGNATURE.value, LifecycleState.EFFECTIVE.value),   # T3 QA 签批
    (LifecycleState.PENDING_SIGNATURE.value, LifecycleState.REJECTED.value),    # T4 QA 拒绝
    (LifecycleState.EFFECTIVE.value, LifecycleState.SUPERSEDED.value),          # T5 增量取代
}


class IllegalTransition(Exception):
    """非法状态迁移：``(from, to) ∉ LEGAL_TRANSITIONS``。映射为 HTTP 409（FR-016）。"""

    def __init__(self, from_state: str | None, to_state: str) -> None:
        self.from_state = from_state
        self.to_state = to_state
        super().__init__(
            f"非法状态迁移：{from_state or '(初始)'} → {to_state}；"
            f"合法迁移仅限 {sorted((str(f), t) for f, t in LEGAL_TRANSITIONS)}"
        )


def transition(
    db: Session,
    conclusion: ReasoningExecution,
    to_state: LifecycleState | str,
    *,
    actor: str,
    reason: str | None = None,
    superseded_by=None,
) -> None:
    """校验并执行一次状态迁移（不 commit；写 ``reasoning.transition`` 审计）。

    1. ``(from, to) ∉ LEGAL_TRANSITIONS`` → 抛 ``IllegalTransition``、**不改任何状态**。
    2. 置 ``lifecycle_state``，并同步既有布尔 ``effective``/``superseded_by``（向后兼容）。
    3. 写一条 ``reasoning.transition`` 审计（``commit=False``，由调用方统一提交）。
    """
    to = to_state.value if isinstance(to_state, LifecycleState) else str(to_state)
    frm = conclusion.lifecycle_state or None

    if (frm, to) not in LEGAL_TRANSITIONS:
        raise IllegalTransition(frm, to)

    conclusion.lifecycle_state = to
    # 同步 002 既有布尔（§1.1 不变式），使旧查询无需重写继续正确。
    if to == LifecycleState.EFFECTIVE.value:
        conclusion.effective = True
    elif to == LifecycleState.SUPERSEDED.value:
        conclusion.effective = False
        if superseded_by is not None:
            conclusion.superseded_by = superseded_by
    else:  # pending_signature / rejected
        conclusion.effective = False

    audit.append(
        db,
        "reasoning.transition",
        actor=actor,
        entity_iri=str(conclusion.id),
        details={"from": frm, "to": to, "reason": reason},
        commit=False,
    )
