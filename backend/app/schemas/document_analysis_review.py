"""Explicit property-review and bounded-repair API contracts."""

from typing import Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from app.schemas.document_analysis import (
    ApiModel,
    DocumentAnalysisRunResponse,
    EntityRef,
    RequestKey,
)

ReviewReason = Literal[
    "incorrect_value", "incorrect_property", "incorrect_subject", "incorrect_scope",
    "unsupported", "other",
]
RepairStatus = Literal["queued", "running", "completed", "unresolved", "failed", "cancelled"]


class CreatePropertyReview(ApiModel):
    request_key: RequestKey
    expected_run_revision: int = Field(ge=0, strict=True)
    graph_snapshot_id: str = Field(min_length=1, max_length=200)
    candidate_id: str = Field(min_length=1, max_length=200)
    candidate_revision: int = Field(ge=1, strict=True)
    expected_review_revision: int = Field(ge=0, strict=True)
    decision: Literal["accepted", "rejected"]
    reason: str = Field(default="", max_length=4000)
    reason_code: ReviewReason = "other"

    @model_validator(mode="after")
    def validate_reason(self):
        if not self.request_key.strip():
            raise ValueError("request key must not be blank")
        if self.decision == "rejected" and not self.reason.strip():
            raise ValueError("rejection requires a reason")
        return self


class PropertyReview(ApiModel):
    review_id: UUID
    revision: int = Field(ge=1)
    candidate_id: str
    candidate_revision: int = Field(ge=1)
    graph_snapshot_id: str
    decision: Literal["accepted", "rejected"]
    reason_code: ReviewReason
    reason: str
    author: str
    author_role: str
    created_at: AwareDatetime


class PropertyReviewList(ApiModel):
    run_revision: int
    items: list[PropertyReview]
    heads: list[PropertyReview]
    can_review: bool
    can_repair: bool


class PropertyReviewResponse(ApiModel):
    review: PropertyReview
    run: DocumentAnalysisRunResponse


class CreatePropertyRepair(ApiModel):
    request_key: RequestKey
    expected_run_revision: int = Field(ge=0, strict=True)
    review_id: UUID

    @model_validator(mode="after")
    def validate_key(self):
        if not self.request_key.strip():
            raise ValueError("request key must not be blank")
        return self


class PropertyRepair(ApiModel):
    operation_id: UUID
    review_id: UUID
    candidate_id: str
    candidate_revision: int
    status: RepairStatus
    subject_ref: EntityRef
    predicate_iri: str
    record_ids: list[str]
    max_tasks: Literal[16] = 16
    max_model_calls: Literal[32] = 32
    result: dict[str, Any]
    created_at: AwareDatetime
    updated_at: AwareDatetime


class PropertyRepairList(ApiModel):
    run_revision: int
    items: list[PropertyRepair]
    can_repair: bool


class PropertyRepairResponse(ApiModel):
    operation: PropertyRepair
    run: DocumentAnalysisRunResponse
