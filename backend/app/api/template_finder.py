"""Authenticated, template-scoped Finder display endpoints."""

from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request
from sqlalchemy.orm import Session

from app.api.pde_conflict import DecisionOut
from app.db import get_db
from app.dependencies import get_current_user, get_ontology_engine, require_role
from app.models.extraction import ExtractionJob
from app.schemas.template_finder import FinderPdeDecisionRequest, StartFinderRequest
from app.services.template_finder.pde_review import review
from app.services.template_finder.policy import error
from app.services.template_finder.service import FinderService, context, is_private

router = APIRouter(dependencies=[Depends(get_current_user)])
PREFIX = "/{template_id}/sources/{source_job_id}/finder"


def reject_private_job(request: Request, db: Session = Depends(get_db)):
    raw = request.path_params.get("job_id")
    if raw:
        try:
            job_id = UUID(str(raw))
        except ValueError:
            return
        if is_private(db.get(ExtractionJob, job_id)):
            raise error("SOURCE_NOT_FOUND", "作业不存在", 404)


@router.get("/recognition-context")
def recognition_context(
    document_iri: str, template_id: UUID | None = None, db: Session = Depends(get_db)
):
    return context(db, document_iri, template_id)


@router.get(PREFIX)
def status(
    template_id: UUID,
    source_job_id: UUID,
    db: Session = Depends(get_db),
    engine=Depends(get_ontology_engine),
    identity=Depends(get_current_user),
):
    return FinderService(db, engine).status(identity.username, template_id, source_job_id)


@router.post(PREFIX, status_code=202)
def start(
    template_id: UUID,
    source_job_id: UUID,
    body: StartFinderRequest,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    engine=Depends(get_ontology_engine),
    identity=Depends(require_role("senior_analyst")),
):
    return FinderService(db, engine).start(
        identity.username, template_id, source_job_id, body, background
    )


@router.get(PREFIX + "/graph")
def graph(
    template_id: UUID,
    source_job_id: UUID,
    execution_id: UUID = Query(...),
    db: Session = Depends(get_db),
    identity=Depends(get_current_user),
):
    return FinderService(db).result(
        identity.username, template_id, source_job_id, str(execution_id), "graph"
    )


@router.get(PREFIX + "/pde-conflict/decision", response_model=DecisionOut)
def pde_decision(
    template_id: UUID,
    source_job_id: UUID,
    execution_id: UUID = Query(...),
    db: Session = Depends(get_db),
    identity=Depends(get_current_user),
):
    return review(db, identity.username, template_id, source_job_id, execution_id)


@router.post(PREFIX + "/pde-conflict/decision", response_model=DecisionOut)
def decide_pde(
    template_id: UUID,
    source_job_id: UUID,
    body: FinderPdeDecisionRequest,
    execution_id: UUID = Query(...),
    db: Session = Depends(get_db),
    engine=Depends(get_ontology_engine),
    identity=Depends(require_role("senior_analyst")),
):
    return review(db, identity.username, template_id, source_job_id, execution_id,
                  body=body, engine=engine)


@router.get(PREFIX + "/source")
def original(
    template_id: UUID,
    source_job_id: UUID,
    execution_id: UUID | None = None,
    db: Session = Depends(get_db),
    identity=Depends(get_current_user),
):
    service = FinderService(db)
    if execution_id is not None:
        return service.result(
            identity.username, template_id, source_job_id, str(execution_id), "source"
        )
    return service.preview(template_id, source_job_id)
