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
    ActionExecution,
    AuditLog,
    ElectronicSignature,
    ReasoningExecution,
)
from app.services import audit

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
    """待签名结论：requires_signature ∧ ¬effective（高风险/专用化/合规阻断）。"""
    rows = (
        db.query(ReasoningExecution)
        .filter(ReasoningExecution.requires_signature.is_(True))
        .filter(ReasoningExecution.effective.is_(False))
        .filter(ReasoningExecution.superseded_by.is_(None))
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
    """QA 电子签名：重认证→绑定结论→置 effective→解除 suppressed 动作→写审计链。"""
    c = db.get(ReasoningExecution, req.conclusion_id)
    if not c:
        raise HTTPException(404, "结论不存在")
    if c.signature_id is not None or c.effective:
        raise HTTPException(409, "结论已签名/已生效")
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

    # 不可分割绑定 + 生效（FR-030/VR-6）。
    c.signature_id = sig.id
    c.effective = True

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
