"""合规 API：审计哈希链 / QA 电子签名 / RBAC（跨切, FR-028–031, contracts/compliance-audit-api）。"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.dependencies import ROLE_QA, ROLE_SENIOR_ANALYST, require_role
from app.models.reasoning import (
    ACTION_NON_TERMINAL,
    ACTION_VOIDED,
    LIFECYCLE_PENDING_SIGNATURE,
    ActionExecution,
    AuditLog,
    ElectronicSignature,
    ReasoningExecution,
)
from app.services import audit
from app.services.reasoning.lifecycle import (
    IllegalTransition,
    LifecycleState,
    transition,
)

router = APIRouter()

_qa = require_role(ROLE_QA)
_reader = require_role(ROLE_SENIOR_ANALYST, ROLE_QA)  # 审计只读：维护者与 QA 可查


# --- DTO --------------------------------------------------------------------


class AuditEntry(BaseModel):
    seq: int | None = None
    action: str
    actor: str | None = None
    entity_iri: str | None = None
    prev_hash: str | None = None
    entry_hash: str | None = None
    details: dict | None = None
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class AuditListResponse(BaseModel):
    entries: list[AuditEntry]


class PendingConclusion(BaseModel):
    id: UUID
    risk_level: str | None = None
    execution_type: str

    model_config = {"from_attributes": True}


class PendingResponse(BaseModel):
    conclusions: list[PendingConclusion]


class SignRequest(BaseModel):
    conclusion_id: UUID
    username: str
    password: str
    meaning: str


class SignResponse(BaseModel):
    signature_id: UUID
    conclusion_id: UUID
    effective: bool
    signed_at: datetime


# --- 1. 审计哈希链 ----------------------------------------------------------


@router.get("/audit/verify")
def verify_audit(db: Session = Depends(get_db), _: object = Depends(_reader)):
    """完整性校验：定位首个断裂记录，不静默续写（FR-029/SC-008）。"""
    return audit.verify(db)


@router.get("/audit", response_model=AuditListResponse)
def list_audit(
    actor: str | None = None,
    action: str | None = None,
    entity_iri: str | None = None,
    db: Session = Depends(get_db),
    _: object = Depends(_reader),
):
    """审计记录只读查询（append-only，无 UPDATE/DELETE 端点, VR-5）。"""
    q = db.query(AuditLog).filter(AuditLog.seq.isnot(None))
    if actor:
        q = q.filter(AuditLog.actor == actor)
    if action:
        q = q.filter(AuditLog.action == action)
    if entity_iri:
        q = q.filter(AuditLog.entity_iri == entity_iri)
    rows = q.order_by(AuditLog.seq.asc()).all()
    return AuditListResponse(entries=[AuditEntry.model_validate(r) for r in rows])


# --- 2. QA 电子签名 ---------------------------------------------------------


@router.get("/signatures/pending", response_model=PendingResponse)
def pending_signatures(db: Session = Depends(get_db), _: object = Depends(_qa)):
    """待签名结论：以显式 `lifecycle_state == pending_signature` 为唯一判据（T017，
    contracts/lifecycle-guard §3）——已 `rejected`/`superseded`/`effective` 一律不入列表。"""
    rows = (
        db.query(ReasoningExecution)
        .filter(ReasoningExecution.lifecycle_state == LIFECYCLE_PENDING_SIGNATURE)
        .all()
    )
    return PendingResponse(conclusions=[PendingConclusion.model_validate(r) for r in rows])


def _reauthenticate(username: str, password: str) -> bool:
    """Part 11 重认证（占位：经 env 注入共享密钥；SSO 接入前的可插拔门禁, R7/R10）。"""
    return bool(password) and password == settings.qa_reauth_secret


@router.post("/signatures", response_model=SignResponse, status_code=201)
def sign_conclusion(
    req: SignRequest,
    db: Session = Depends(get_db),
    identity: object = Depends(_qa),
):
    """QA 电子签名（T3, FR-008/009）：重认证→不可分割绑定签名→经 `transition` 落
    `pending_signature → effective`（单一守卫）→解除 suppressed 动作→写审计链。对
    `superseded`/`rejected`/已生效结论签批：transition 非法 → `409`（签批竞态）。"""
    c = db.get(ReasoningExecution, req.conclusion_id)
    if not c:
        raise HTTPException(404, "结论不存在")
    if c.signature_id is not None:
        raise HTTPException(409, "结论已签名")
    if not _reauthenticate(req.username, req.password):
        raise HTTPException(401, "重认证失败")

    sig = ElectronicSignature(
        conclusion_id=c.id,
        signer=req.username,
        signer_role=ROLE_QA,
        meaning=req.meaning,
        reauth_verified=True,
        signed_at=datetime.now(timezone.utc),
    )
    db.add(sig)
    db.flush()  # 取得 sig.id

    # 经单一守卫迁移（T3）：非法（非待签态）→ 不改状态、回滚本事务 → 409。
    try:
        transition(db, c, LifecycleState.EFFECTIVE, actor=req.username,
                   reason="QA 电子签批生效")
    except IllegalTransition as exc:
        db.rollback()
        raise HTTPException(409, str(exc))

    # 不可分割绑定（FR-030/VR-6）。
    c.signature_id = sig.id

    # 解除该结论被抑制的对外动作。
    released = (
        db.query(ActionExecution)
        .filter(ActionExecution.conclusion_id == c.id)
        .filter(ActionExecution.status == "suppressed")
        .all()
    )
    for a in released:
        a.status = "pending"

    entry = audit.append(
        db, "compliance.sign", actor=req.username, entity_iri=str(c.id),
        details={"signature_id": str(sig.id), "meaning": req.meaning,
                 "released_actions": len(released)},
        commit=False,
    )
    sig.audit_seq = entry.seq
    db.commit()
    db.refresh(sig)
    return SignResponse(
        signature_id=sig.id, conclusion_id=c.id,
        effective=True, signed_at=sig.signed_at,
    )


class RejectRequest(BaseModel):
    conclusion_id: UUID
    username: str
    password: str
    reason: str


class RejectResponse(BaseModel):
    conclusion_id: UUID
    lifecycle_state: str
    voided_actions: int


@router.post("/reject", response_model=RejectResponse, status_code=201)
def reject_conclusion(
    req: RejectRequest,
    db: Session = Depends(get_db),
    identity: object = Depends(_qa),
):
    """QA 拒绝（T4, FR-020）：重认证→经 `transition` 落 `pending_signature → rejected`
    （终态）→被抑制的**非终态**动作置 `voided`（每条 `action.void` 审计）→写
    `compliance.reject` 审计。对非待签态拒绝：transition 非法 → `409`；终态不可再拒绝。"""
    c = db.get(ReasoningExecution, req.conclusion_id)
    if not c:
        raise HTTPException(404, "结论不存在")
    if not _reauthenticate(req.username, req.password):
        raise HTTPException(401, "重认证失败")

    try:
        transition(db, c, LifecycleState.REJECTED, actor=req.username,
                   reason=req.reason)
    except IllegalTransition as exc:
        db.rollback()
        raise HTTPException(409, str(exc))

    # 作废该结论尚未终结的动作（被抑/待派发等非终态 → voided, FR-012/020）。
    voided = (
        db.query(ActionExecution)
        .filter(ActionExecution.conclusion_id == c.id)
        .filter(ActionExecution.status.in_(ACTION_NON_TERMINAL))
        .all()
    )
    for a in voided:
        a.status = ACTION_VOIDED
        audit.append(
            db, "action.void", actor=req.username, entity_iri=str(a.id),
            details={"conclusion_id": str(c.id), "reason": "结论被 QA 拒绝"},
            commit=False,
        )

    audit.append(
        db, "compliance.reject", actor=req.username, entity_iri=str(c.id),
        details={"reason": req.reason, "voided_actions": len(voided)},
        commit=False,
    )
    db.commit()
    db.refresh(c)
    return RejectResponse(
        conclusion_id=c.id, lifecycle_state=c.lifecycle_state,
        voided_actions=len(voided),
    )
