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
    ner_columns: list[str] | None = None      # 008 US3：自由文本列白名单（本地 NER 富化）
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
    ner_columns: list[str] | None = None      # 008 US3：自由文本列白名单（本地 NER 富化）
    llm_prompt_template: str | None = None
    is_active: bool = True


class ExtractionJobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    source_type: str
    source_filename: str | None = None
    document_path: str | None = None
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


class GeneratedReportResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    job_id: UUID
    report_type: str
    file_path: str
    file_size: int | None = None
    rules_fired_count: int = 0
    rules_summary: dict | None = None
    actor: str
    created_at: datetime


# --------------------------------------------------------------------------- #
# 012 AST Template Management
# --------------------------------------------------------------------------- #


class AstTemplateCreate(BaseModel):
    name: str
    version: str = "v1"
    doc_no: str | None = None
    schema_json: dict


class AstTemplateUpdate(BaseModel):
    schema_json: dict
    version: str | None = None


class AstTemplateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    version: str
    doc_no: str | None = None
    slot_count: int = 0
    is_default: bool = False
    created_by: str | None = None
    created_at: datetime
    updated_at: datetime | None = None


class TemplateMatchResponse(BaseModel):
    template_id: UUID
    template_name: str
    template_version: str
    match_source: str


class DocumentTypeMappingCreate(BaseModel):
    doc_class_iri_pattern: str
    template_id: UUID
    priority: int = 0


class DocumentTypeMappingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    doc_class_iri_pattern: str
    template_id: UUID
    template_name: str = ""
    template_version: str = ""
    priority: int = 0
    created_at: datetime


# --------------------------------------------------------------------------- #
# 011 AST Coverage (extended for 012 template switching + LLM gap filling)
# --------------------------------------------------------------------------- #


class SlotCoverageResponse(BaseModel):
    slot_id: str
    label: str
    status: str
    source_kind: str
    value: str | None = None
    source_ref: str | None = None
    rule_key: str | None = None
    hazid: str | None = None
    note: str | None = None
    source_span: str | None = None
    is_llm_sourced: bool = False


class GroupCoverageResponse(BaseModel):
    group_id: str
    title: str
    kind: str
    slots: list[SlotCoverageResponse]
    is_dynamic: bool = False


class SectionCoverageResponse(BaseModel):
    section_id: str
    title: str
    groups: list[GroupCoverageResponse]


class ASTCoverageResponse(BaseModel):
    template_id: str
    template_name: str = ""
    template_version: str = ""
    total_slots: int
    filled: int
    inferred: int
    missing_required: int
    blank_optional: int
    manual: int
    dismissed: int
    sections: list[SectionCoverageResponse]


class SlotDismissRequest(BaseModel):
    slot_id: str


class SlotDismissalResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    job_id: UUID
    slot_id: str
    dismissed_by: str
    dismissed_at: datetime
