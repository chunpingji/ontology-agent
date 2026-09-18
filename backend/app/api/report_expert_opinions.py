from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.api.document_analysis import DocumentAnalysisRoute
from app.db import get_db
from app.dependencies import Identity, get_current_user
from app.schemas.report_expert_opinion import (
    CreateExpertOpinion,
    ExpertOpinion,
    ExpertOpinionList,
    OpinionTarget,
)
from app.services.report_expert_opinions import ExpertOpinionService

router = APIRouter(route_class=DocumentAnalysisRoute)
PRIVATE = {"Cache-Control": "private, no-store"}


def target_query(document_iri: str | None = Query(default=None, max_length=500),
                 job_id: UUID | None = None, report_id: UUID | None = None):
    try:
        return OpinionTarget(document_iri=document_iri, job_id=job_id, report_id=report_id)
    except ValidationError as exc:
        raise HTTPException(422, "请选择一个文档或生成报告") from exc


@router.get("", response_model=ExpertOpinionList)
def list_opinions(response: Response, target: OpinionTarget = Depends(target_query),
                  recognition_run_id: UUID | None = None,
                  offset: int = Query(default=0, ge=0),
                  limit: int = Query(default=50, ge=1, le=100),
                  identity: Identity = Depends(get_current_user), db: Session = Depends(get_db)):
    response.headers.update(PRIVATE)
    return ExpertOpinionService(db, identity).listing(
        target, recognition_run_id, offset=offset, limit=limit,
    )


@router.get("/export")
def export_opinions(target: OpinionTarget = Depends(target_query),
                    identity: Identity = Depends(get_current_user), db: Session = Depends(get_db)):
    return JSONResponse(jsonable_encoder(ExpertOpinionService(db, identity).export(target)),
                        headers={**PRIVATE, "Content-Disposition":
                                 'attachment; filename="expert-opinions.json"'})


@router.post("", response_model=ExpertOpinion, status_code=201)
def create_opinion(body: CreateExpertOpinion, response: Response,
                   identity: Identity = Depends(get_current_user), db: Session = Depends(get_db)):
    response.headers.update(PRIVATE)
    return ExpertOpinionService(db, identity).create(body)
