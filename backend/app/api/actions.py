"""Action 编排 API（能力四, FR-020–023, contracts/action-report-api §1-3）。"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependencies import ROLE_QA, ROLE_SENIOR_ANALYST, require_role
from app.models.reasoning import (
    ACTION_SUPPRESSED,
    ACTION_TERMINAL,
    ActionExecution,
)
from app.schemas.reporting import (
    ActionListResponse,
    ActionPatch,
    ActionResponse,
    WritebackResultRequest,
)
from app.services import audit

router = APIRouter()

_actor = require_role(ROLE_SENIOR_ANALYST, ROLE_QA)  # 流转限维护者/QA（契约 §0，operator 只读）


@router.get("", response_model=ActionListResponse)
def list_actions(
    conclusion_id: UUID | None = None,
    action_type: str | None = None,
    status: str | None = None,
    db: Session = Depends(get_db),
):
    q = db.query(ActionExecution)
    if conclusion_id:
        q = q.filter(ActionExecution.conclusion_id == conclusion_id)
    if action_type:
        q = q.filter(ActionExecution.action_type == action_type)
    if status:
        q = q.filter(ActionExecution.status == status)
    rows = q.order_by(ActionExecution.created_at.desc()).all()
    return ActionListResponse(actions=[ActionResponse.model_validate(a) for a in rows])


@router.patch("/{action_id}", response_model=ActionResponse)
def patch_action(
    action_id: UUID,
    req: ActionPatch,
    db: Session = Depends(get_db),
    identity: object = Depends(_actor),
):
    """人工流转工单/任务状态（平台内部记录, FR-020/021）。

    from-status 守卫（T025, FR-003/009 动作早派发防护）：
    - 终态（`voided`/`done`/`failed`）不可再外迁 → `409`；
    - `suppressed` 须经 QA 签批解抑（→`pending`）后方可流转，不可直接人工流转 → `409`。
    """
    a = db.get(ActionExecution, action_id)
    if not a:
        raise HTTPException(404)
    if a.status in ACTION_TERMINAL:
        raise HTTPException(409, f"动作处于终态 {a.status}，不可再流转")
    if a.status == ACTION_SUPPRESSED:
        raise HTTPException(409, "动作处于 suppressed，须经 QA 签批解抑后方可流转")
    from_status = a.status
    a.status = req.status
    audit.append(db, "action.transition", actor=getattr(identity, "username", "system"),
                 entity_iri=str(a.id),
                 details={"from": from_status, "status": req.status},
                 commit=False)
    db.commit()
    db.refresh(a)
    return a


@router.post("/{action_id}/writeback-result", response_model=ActionResponse)
def writeback_result(
    action_id: UUID,
    req: WritebackResultRequest,
    db: Session = Depends(get_db),
    identity: object = Depends(_actor),
):
    """外部排期方反馈是否采纳建议性回写；`not_accepted` 不视为失败（FR-022/VR-7）。"""
    a = db.get(ActionExecution, action_id)
    if not a:
        raise HTTPException(404)
    a.writeback_status = req.writeback_status
    # not_accepted 不置 failed：结论与告警保留（原则 II，仅建议性回写）。
    audit.append(db, "action.writeback", actor=getattr(identity, "username", "system"),
                 entity_iri=str(a.id), details={"writeback_status": req.writeback_status},
                 commit=False)
    db.commit()
    db.refresh(a)
    return a
