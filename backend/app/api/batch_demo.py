"""Explicit static-demo entry, independent of document recognition execution."""

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependencies import ROLE_SENIOR_ANALYST, Identity, get_current_user, require_role
from app.services.reporting import batch_demo

router = APIRouter()


class GenerateBatchDemo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    graph_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    template_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    request_key: str = Field(min_length=1, max_length=100)


@router.get("/batch-demo")
def get_demo(
    response: Response,
    document_iri: str = Query(min_length=1, max_length=1000),
    db: Session = Depends(get_db),
    identity: Identity = Depends(get_current_user),
):
    response.headers["Cache-Control"] = "private, no-store"
    data, _ = batch_demo.context(db, document_iri, identity.username)
    return data


@router.post("/batch-demo", status_code=201)
def generate_demo(
    body: GenerateBatchDemo,
    document_iri: str = Query(min_length=1, max_length=1000),
    db: Session = Depends(get_db),
    identity: Identity = Depends(require_role(ROLE_SENIOR_ANALYST)),
):
    return batch_demo.generate(db, document_iri, identity.username, body)


@router.get("/batch-demo/templates/{template_id}")
def get_demo_template(
    template_id: UUID,
    response: Response,
    db: Session = Depends(get_db),
    identity: Identity = Depends(get_current_user),
):
    response.headers["Cache-Control"] = "private, no-store"
    return batch_demo.template_context(db, template_id, identity.username)
