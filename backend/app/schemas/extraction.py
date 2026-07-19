from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


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
    iri_pattern: str | None = None  # 015: functional doc-class resolution key
    schema_json: dict
    sample_text: str | None = None
    sample_content_json: dict | None = None  # 013: tiptap 结构化样例（忠于原文预览）


class AstTemplateUpdate(BaseModel):
    schema_json: dict
    version: str | None = None


class AstTemplateMetaUpdate(BaseModel):
    """015: in-place metadata edit — no version bump.

    基本信息 tab 可编辑 name/doc_no/owner/status；列表页 ⋮ 操作走 status/iri_pattern。
    """

    name: str | None = None
    doc_no: str | None = None
    owner: str | None = None  # 责任人
    status: str | None = None  # draft | published | archived
    iri_pattern: str | None = None


class AstTemplateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    version: str
    doc_no: str | None = None
    iri_pattern: str | None = None
    status: str = "draft"
    slot_count: int = 0
    is_default: bool = False
    created_by: str | None = None
    owner: str | None = None
    sample_docx_filename: str | None = None
    default_source_filename: str | None = None
    default_source_job_id: UUID | None = None
    created_at: datetime
    updated_at: datetime | None = None


class TrainingPairResponse(BaseModel):
    """015 训练数据：源文档→评估报告 成对样例（report 可缺省）。"""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    source_filename: str
    report_filename: str | None = None
    created_at: datetime


class TemplateMatchResponse(BaseModel):
    template_id: UUID
    template_name: str
    template_version: str
    match_source: str


class GenerateSectionPromptRequest(BaseModel):
    """015: derive a 行文 Prompt for a section from the sample + its slot labels."""

    section_title: str
    slot_labels: list[str] = []
    sample_text: str = ""


class GenerateSectionPromptResponse(BaseModel):
    prompt: str


class PreviewSectionNarrativeRequest(BaseModel):
    """015+: preview the prose one section's 行文 Prompt produces, using a matched
    document's REAL extracted facts (not sample text) — same path as the report."""

    job_id: UUID          # 已关联真实文档的抽取作业（真实事实来源）
    template_id: UUID     # 当前编辑的模板（取本节结构 + 确定性风险/覆盖）
    section_id: str
    prompt: str           # 当前（可能未保存）的行文 Prompt


class PreviewSectionNarrativeResponse(BaseModel):
    narrative: str


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


# --------------------------------------------------------------------------- #
# 013 LLM Template Design Assist — Suggest Slots
# --------------------------------------------------------------------------- #


class SuggestSlotsRequest(BaseModel):
    job_id: UUID | None = None
    document_text: str | None = None
    # 013: 结构化样例（tiptap）——首选输入，服务端派生 LLM 文本与 source_ref 锚点。
    sample_content_json: dict | None = None
    existing_template: dict | None = None
    max_suggestions: int = 50
    # 016 (D10): the document entity type is an upstream classification input; it
    # grounds ontology coverage. Optional and DELIBERATELY OUTSIDE the exactly-one-of
    # source count below — it augments a source, it is not itself a document source.
    doc_class_iri: str | None = None

    def model_post_init(self, __context: Any) -> None:
        provided = sum(
            x is not None
            for x in (self.job_id, self.document_text, self.sample_content_json)
        )
        if provided != 1:
            raise ValueError(
                "Exactly one of job_id, document_text, or sample_content_json "
                "must be provided"
            )


# --------------------------------------------------------------------------- #
# 016: section-level ontology coverage (suggester output)
# --------------------------------------------------------------------------- #


class CoverageDeclaration(BaseModel):
    """One ontology relationship a section covers (transport mirror of
    ``OntologyRelationBinding``). All three IRIs are class/predicate TYPES —
    physically incapable of naming a sample individual (FR-003)."""

    kind: Literal["ontology_relation"] = "ontology_relation"
    doc_class_iri: str
    predicate_iri: str
    range_class_iri: str
    required: bool = True  # FR-005a — required by default
    label: str | None = None


class SuggestSlotsResponse(BaseModel):
    """AI 分析输出：文档结构骨架（``sections``）+ 本体覆盖边（``coverage``）。

    Note: the ``/suggest-slots`` endpoint returns the raw ``suggest_slots()`` dict with
    **no** ``response_model``; this model is documentary (the wire-shape authority for
    ``sections`` is ``slot_suggester._ROUND1_SCHEMA``).
    """

    document_summary: str
    # 016: ontology-grounded coverage only (US1). 无法绑定到菜单关系边的位点静默忽略，
    # 不再抛出 unresolved_candidates（取代 FR-008a）。
    coverage: list[CoverageDeclaration] = Field(default_factory=list)
    # Round-1 structural skeleton, returned verbatim: sections[].groups[].candidates[]
    # {label, evidence_span?, evidence_offset?} (shape = _ROUND1_SCHEMA). 无本体 IRI 绑定
    # —— 编辑器把每个 candidate 物化为可作者填写的 semantic 槽。已删的 llm_extraction
    # 自动串匹配绑定流（defect 1 根因）不随此字段回归。
    sections: list[dict] = Field(default_factory=list)


class CoverageDocClassesRequest(BaseModel):
    """016：作者化 UI 传入候选文档类型 IRI 清单，问询哪些「已建模」可覆盖关系。"""

    doc_class_iris: list[str] = Field(default_factory=list)


class CoverageDocClassesResponse(BaseModel):
    """已建模（≥1 条 hop-1 覆盖边）的文档类型子集——UI 只启用这些（仅启用已建模类型）。"""

    capable: list[str] = Field(default_factory=list)
