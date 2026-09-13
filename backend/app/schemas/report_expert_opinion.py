from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.schemas.evidence import EvidenceModel


class OpinionTarget(EvidenceModel):
    document_iri: str | None = Field(default=None, min_length=1, max_length=500)
    job_id: UUID | None = None
    report_id: UUID | None = None

    @model_validator(mode="after")
    def exclusive_target(self):
        if self.document_iri:
            if self.job_id or self.report_id:
                raise ValueError("select one document or generated report")
        elif not (self.job_id and self.report_id):
            raise ValueError("a document or job/report pair is required")
        return self


class OpinionContext(EvidenceModel):
    target: OpinionTarget
    filename: str
    source_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_version: str
    recognition_run_id: UUID | None = None
    graph_artifact_id: str | None = None
    graph_content_hash: str | None = None


OpinionCategory = Literal[
    "general", "relationship", "attribute", "missing", "subject_binding", "negation", "pruning"
]


class CreateExpertOpinion(EvidenceModel):
    request_key: str = Field(min_length=1, max_length=200)
    context: OpinionContext
    category: OpinionCategory = "general"
    location: str = Field(default="", max_length=500)
    opinion: str = Field(min_length=1, max_length=6000)
    suggestion: str = Field(default="", max_length=4000)

    @field_validator("opinion", "request_key")
    @classmethod
    def nonblank(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


class ExpertOpinion(EvidenceModel):
    opinion_id: UUID
    revision: Literal[1] = 1
    author: str
    author_role: str
    created_at: datetime
    context: OpinionContext
    category: OpinionCategory
    location: str
    opinion: str
    suggestion: str


class ExpertOpinionList(EvidenceModel):
    context: OpinionContext
    can_submit: bool
    items: list[ExpertOpinion]
    total: int
    offset: int
    limit: int
