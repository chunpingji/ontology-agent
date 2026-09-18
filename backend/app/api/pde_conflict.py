"""CMCReport PDE 冲突的人工决策端点（GET 回显 / POST CAS upsert）。

关系图谱在共线评估端点上暴露「推导 vs 原文」PDE 冲突（见 ``services/reasoning/pde_conflict``）。
本路由持久化用户对该冲突的一次人工裁决：采纳推导 / 采纳原文 / 待复核。以 ``(job_id, conflict_key)``
唯一，``version`` 乐观并发（``expected_version`` 不匹配 → 409）。
``actor`` 取自网关身份头 ``X-User``。

挂载于 ``/api/extraction``（与 annotated-document 同前缀）：
``GET|POST /api/extraction/jobs/{job_id}/pde-conflict/decision``。
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependencies import Identity, get_current_user
from app.models.extraction import ExtractionJob
from app.models.pde_conflict import DECISION_CHOICES, PdeConflictDecision
from app.services.reasoning.pde_conflict import CONFLICT_KEY

router = APIRouter()


class DecisionIn(BaseModel):
    chosen: str
    note: str = ""
    expected_version: int = 0


class DecisionOut(BaseModel):
    job_id: UUID
    conflict_key: str
    chosen: str
    note: str
    actor: str
    version: int
    decided_at: datetime | None = None


def _out(job_id: UUID, row: PdeConflictDecision | None) -> DecisionOut:
    """ORM 行 → 响应；无记录时返回 version 0、chosen ``pending`` 的空决策。

    首次 POST 使用 expected_version=0。
    """
    if row is None:
        return DecisionOut(
            job_id=job_id, conflict_key=CONFLICT_KEY,
            chosen="pending", note="", actor="", version=0, decided_at=None,
        )
    return DecisionOut(
        job_id=row.job_id, conflict_key=row.conflict_key, chosen=row.chosen,
        note=row.note, actor=row.actor, version=row.version, decided_at=row.decided_at,
    )


def _get_row(db: Session, job_id: UUID) -> PdeConflictDecision | None:
    return (
        db.query(PdeConflictDecision)
        .filter(
            PdeConflictDecision.job_id == job_id,
            PdeConflictDecision.conflict_key == CONFLICT_KEY,
        )
        .one_or_none()
    )


def _require_job(db: Session, job_id: UUID) -> ExtractionJob:
    job = db.get(ExtractionJob, job_id)
    if job is None:
        raise HTTPException(404, "extraction job not found")
    from app.api.extraction import _require_result_capability

    _require_result_capability(job)
    return job


@router.get("/jobs/{job_id}/pde-conflict/decision", response_model=DecisionOut)
def get_pde_conflict_decision(job_id: UUID, db: Session = Depends(get_db)) -> DecisionOut:
    """回显该文档 PDE 冲突的人工决策；无记录 → version 0、chosen "pending"。"""
    _require_job(db, job_id)
    return _out(job_id, _get_row(db, job_id))


@router.post("/jobs/{job_id}/pde-conflict/decision", response_model=DecisionOut)
def decide_pde_conflict(
    job_id: UUID,
    body: DecisionIn,
    db: Session = Depends(get_db),
    identity: Identity = Depends(get_current_user),
) -> DecisionOut:
    """CAS upsert 人工决策（``expected_version`` 不匹配 → 409）。actor 取自 ``X-User``。"""
    _require_job(db, job_id)
    if body.chosen not in DECISION_CHOICES:
        raise HTTPException(422, f"chosen 取值须为 {DECISION_CHOICES} 之一")

    row = _get_row(db, job_id)
    current_version = row.version if row else 0
    if body.expected_version != current_version:
        raise HTTPException(
            409,
            f"版本冲突：expected_version={body.expected_version}，当前={current_version}，请刷新后重试。",
        )

    now = datetime.now(timezone.utc)
    if row is None:
        row = PdeConflictDecision(
            job_id=job_id, conflict_key=CONFLICT_KEY, chosen=body.chosen,
            note=body.note or "", actor=identity.username, version=1, decided_at=now,
        )
        db.add(row)
    else:
        row.chosen = body.chosen
        row.note = body.note or ""
        row.actor = identity.username
        row.version += 1
        row.decided_at = now
    db.commit()
    db.refresh(row)
    return _out(job_id, row)
