from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ExtractionConfigCreate(BaseModel):
    name: str
    target_class_iri: str
    source_type: str
    column_mapping: dict[str, str] | None = None
    llm_prompt_template: str | None = None
    few_shot_examples: list[dict] | None = None
    property_constraints: dict | None = None


class ExtractionConfigResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    target_class_iri: str
    source_type: str
    column_mapping: dict | None = None
    llm_prompt_template: str | None = None
    is_active: bool = True


class ExtractionJobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    source_type: str
    source_filename: str | None = None
    status: str
    total_candidates: int = 0
    approved_count: int = 0
    rejected_count: int = 0
    error_message: str | None = None
    created_at: datetime


class ExtractionCandidateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    target_class_iri: str
    extracted_properties: dict[str, Any]
    candidate_kind: str = "instance"
    group_key: str | None = None
    is_canonical: bool = False
    source_ref: str | None = None
    degraded_reason: str | None = None
    merged_into_id: UUID | None = None
    action_conditions: dict | None = None
    alignment_result: str | None = None
    aligned_iri: str | None = None
    match_score: float | None = None
    review_status: str = "pending"
    committed_iri: str | None = None


class CandidateGroup(BaseModel):
    """跨源归组视图（FR-009/SC-003）。"""

    group_key: str
    canonical_candidate_id: UUID | None = None
    candidates: list[ExtractionCandidateResponse]


class GroupedCandidatesResponse(BaseModel):
    job_id: UUID
    groups: list[CandidateGroup]
    ungrouped: list[ExtractionCandidateResponse]


class ProgressEvent(BaseModel):
    job_id: str
    stage: str
    pct: int
    status: str
    degraded: bool = False


class ReviewRequest(BaseModel):
    status: str  # confirmed, rejected, edited
    edited_properties: dict[str, Any] | None = None


class MergeRequest(BaseModel):
    source_ids: list[UUID]
    target_id: UUID


class SplitRequest(BaseModel):
    splits: list[dict[str, Any]]  # 每个元素是派生候选的 extracted_properties


class DBSourceSpec(BaseModel):
    dsn_ref: str  # 环境变量名（凭据经 env 注入，不入库, R7）
    schema_name: str | None = None
    include_tables: list[str] | None = None


class DocExtractionRequest(BaseModel):
    """文档批准/新版本事件 → 入待抽取队列（007 US2，FR-007/Q1 手动发起）。"""

    doc_ref: str       # 文档个体 IRI（facts#…，溯源锚点；版本指针经 content_ref 承载）
    content_ref: str   # 外部正文引用（按需取，不入库全文, Q2）
    config_id: UUID
