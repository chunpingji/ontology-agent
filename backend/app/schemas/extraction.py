from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel


class ExtractionConfigCreate(BaseModel):
    name: str
    target_class_iri: str
    source_type: str
    column_mapping: dict[str, str] | None = None
    llm_prompt_template: str | None = None
    few_shot_examples: list[dict] | None = None
    property_constraints: dict | None = None


class ExtractionConfigResponse(BaseModel):
    id: UUID
    name: str
    target_class_iri: str
    source_type: str
    column_mapping: dict | None = None
    llm_prompt_template: str | None = None
    is_active: bool = True

    class Config:
        from_attributes = True


class ExtractionJobResponse(BaseModel):
    id: UUID
    source_type: str
    source_filename: str | None = None
    status: str
    total_candidates: int = 0
    approved_count: int = 0
    rejected_count: int = 0
    error_message: str | None = None
    created_at: datetime

    class Config:
        from_attributes = True


class ExtractionCandidateResponse(BaseModel):
    id: UUID
    target_class_iri: str
    extracted_properties: dict[str, Any]
    alignment_result: str | None = None
    aligned_iri: str | None = None
    match_score: float | None = None
    review_status: str = "pending"

    class Config:
        from_attributes = True


class ReviewRequest(BaseModel):
    status: str  # approved, rejected, edited
    edited_properties: dict[str, Any] | None = None
