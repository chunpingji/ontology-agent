"""风险评估报告 API（能力四, FR-024, contracts/action-report-api §4-5）。"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.reasoning import ReasoningExecution
from app.schemas.reporting import RiskReportResponse
from app.services.reporting.risk_report import build_report_json, render_report_pdf

router = APIRouter()


def _get_conclusion(conclusion_id: UUID, db: Session) -> ReasoningExecution:
    c = db.get(ReasoningExecution, conclusion_id)
    if not c:
        raise HTTPException(404, "结论不存在")
    return c


@router.get("/{conclusion_id}", response_model=RiskReportResponse)
def get_report(conclusion_id: UUID, db: Session = Depends(get_db)):
    c = _get_conclusion(conclusion_id, db)
    return build_report_json(db, c)


@router.get("/{conclusion_id}/pdf")
def get_report_pdf(conclusion_id: UUID, db: Session = Depends(get_db)):
    c = _get_conclusion(conclusion_id, db)
    pdf = render_report_pdf(db, c)
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="report-{conclusion_id}.pdf"'},
    )
