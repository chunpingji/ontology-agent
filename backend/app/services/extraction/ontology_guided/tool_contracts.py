"""Typed local contracts and Responses definitions for ontology extraction tools.

The catalog is generated from these types, without runtime access to Spec Kit
artifacts. Stage authorization and execution belong to the tool runtime.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Generic, Literal, Mapping, TypeVar

from pydantic import ConfigDict, Field, model_validator

from app.schemas.evidence import EvidenceAnchor, EvidenceModel
from app.services.extraction.ontology_guided.claim_protocol import (
    ConstraintIssue,
    ExternalCandidate,
    FacetName,
    QuantityValue,
    Quote,
    SchemaCard,
)
from app.services.extraction.ontology_guided.contracts import VersionedRef

ToolName = Literal[
    "get_schema_card", "inspect_evidence", "resolve_source_anchor", "propose_mentions",
    "check_claim_binding", "query_instances", "retrieve_evidence", "propose_repair",
    "validate_metric", "validate_graph", "find_referent_candidates",
]
ToolStage = Literal["discovery", "verification", "finalize"]
ToolStatus = Literal["ok", "no_match", "blocked", "error"]
ValidationStatus = Literal["passed", "failed", "incomplete"]
T = TypeVar("T")


class _ToolModel(EvidenceModel):
    model_config = ConfigDict(strict=True, allow_inf_nan=False)


class ToolIssue(_ToolModel):
    code: str
    field_path: str | None
    message: str
    evidence_ids: list[str]


class ToolResult(_ToolModel, Generic[T]):
    status: ToolStatus
    data: T | None
    evidence_refs: list[str]
    issues: list[ToolIssue]

    @model_validator(mode="after")
    def validate_envelope(self):
        if self.status in ("ok", "no_match") and self.data is None:
            raise ValueError("completed tool results require typed data")
        if self.status in ("blocked", "error") and not self.issues:
            raise ValueError("blocked/error results require actionable issues")
        if self.status == "no_match":
            if isinstance(self.data, MentionData):
                complete = (
                    not self.data.mentions
                    and not self.data.omissions
                    and not self.data.coverage.unprocessed_units
                    and set(self.data.coverage.processed_units)
                    == set(self.data.coverage.requested_units)
                )
            elif isinstance(self.data, InstanceData):
                complete = (
                    not self.data.candidates
                    and not self.data.incomplete_sources
                    and self.data.excluded_count == 0
                )
            elif isinstance(self.data, RetrievalData):
                complete = not self.data.new_evidence
            elif isinstance(self.data, ReferentCandidateData):
                complete = (
                    not self.data.candidates and not self.data.truncated
                    and self.data.excluded_count == 0
                )
            else:
                complete = False
            if not complete:
                raise ValueError("no_match requires a completed empty recall/query result")
        return self


class ToolErrorResult(ToolResult[None]):
    status: Literal["blocked", "error"]
    data: None


class GetSchemaCardArgs(_ToolModel):
    subject_id: str
    predicate_iri: str | None


class InspectEvidenceArgs(_ToolModel):
    evidence_ids: list[str]


class ResolveSourceAnchorArgs(_ToolModel):
    evidence_id: str
    quote: str
    context_text: str | None = Field(description=(
        "无歧义时填 null，不能填空字符串；需要消歧时填写同一原文单元中包含 quote 的逐字片段。"
    ))


class ProposeMentionsArgs(_ToolModel):
    evidence_ids: list[str] = Field(description=(
        "选择本次需要识别的已授权原文单元；结果超预算时缩小列表，不必一次传入全部上下文。"
    ))
    schema_card_id: str


class CheckClaimBindingArgs(_ToolModel):
    claim_id: str


class QueryInstancesArgs(_ToolModel):
    mention_ref: str = Field(description=(
        "使用成功的 resolve_source_anchor 或 propose_mentions 返回的已登记 mention_ref；"
        "不能填写原文名称、编号或自行生成的 ID。前置工具失败时须先取得有效引用。"
    ))
    class_iri: str
    source_ids: list[str]


class FindReferentCandidatesArgs(_ToolModel):
    mention_refs: list[str] = Field(min_length=1, description=(
        "已登记的原文 mention_ref；名称、代词或自行生成的 ID 不能代替引用。"
    ))
    class_iris: list[str] = Field(min_length=1, description="当前本体菜单中的候选类型。")
    scope_id: str = Field(description="当前任务的 scope_id；不能扩大到其他运行或作用域。")


class RetrieveEvidenceArgs(_ToolModel):
    subject_id: str
    predicate_iri: str
    missing_facets: list[FacetName]


class ProposeRepairArgs(_ToolModel):
    claim_id: str
    issue_code: str


class ValidateMetricArgs(_ToolModel):
    claim_id: str
    target_unit_id: str | None


class ValidateGraphArgs(_ToolModel):
    claim_id: str
    shape_profile_id: str


class SchemaCardData(_ToolModel):
    cards: list[SchemaCard]
    unsupported_constraints: list[ConstraintIssue]


class EvidenceTable(_ToolModel):
    table_path: list[str]
    source_cell_id: str | None
    logical_rows: list[int]
    logical_columns: list[int]
    column_header_refs: list[str]
    cell_structure_valid: bool


class EvidenceUnit(_ToolModel):
    evidence_id: str
    record_id: str | None
    text: str
    span_start: int = Field(ge=0)
    span_end: int = Field(gt=0)
    role: Literal["target", "header", "note", "parent", "binding", "counterevidence"]
    fact_eligible: bool
    table: EvidenceTable | None

    @model_validator(mode="after")
    def validate_span(self):
        if self.span_end <= self.span_start or len(self.text) != self.span_end - self.span_start:
            raise ValueError("evidence text must match its nonempty half-open source span")
        return self


class EvidenceData(_ToolModel):
    units: list[EvidenceUnit]
    omitted_ids: list[str]
    coverage_complete: bool


class AnchorData(_ToolModel):
    anchor: EvidenceAnchor
    text: str
    mention_ref: str


class MentionSuggestion(_ToolModel):
    mention_ref: str
    evidence_id: str
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    text: str
    class_iris: list[str]
    predicate_iris: list[str]
    role: Literal["entity", "field_label", "field_value", "unit"]
    score: float

    @model_validator(mode="after")
    def validate_span(self):
        if self.end <= self.start or len(self.text) != self.end - self.start:
            raise ValueError("mention text must match its nonempty half-open source span")
        return self


class MentionCoverage(_ToolModel):
    requested_units: list[str]
    processed_units: list[str]
    unprocessed_units: list[str]


class MentionLimits(_ToolModel):
    labels_per_batch: int
    window_chars: int
    overlap_chars: int
    word_limit: int
    encoder_token_limit: int
    candidate_pool_limit: int
    max_returned_mentions: int


class MentionData(_ToolModel):
    mentions: list[MentionSuggestion]
    coverage: MentionCoverage
    limits: MentionLimits
    omissions: list[ToolIssue]


class BindingData(_ToolModel):
    claim_ref: VersionedRef
    validation_status: ValidationStatus
    resolved_role_refs: list[EvidenceAnchor]
    source_unit: str | None
    issues: list[ToolIssue]


class InstanceData(_ToolModel):
    candidates: list[ExternalCandidate]
    searched_sources: list[str]
    incomplete_sources: list[str]
    excluded_count: int | None = Field(ge=0)
    identity_status: Literal["not_checked"]


class ReferentCandidate(_ToolModel):
    candidate_entity_ref: VersionedRef
    class_iri: str
    mention_refs: list[str]
    source_refs: list[EvidenceAnchor] = Field(min_length=1)
    source_texts: list[str] = Field(min_length=1)
    reason: Literal["physical_mention", "normalized_name", "anaphora_context"]
    role: Literal["binding"]
    fact_eligible: Literal[False]
    distinctions: list[str]

    @model_validator(mode="after")
    def validate_source_pairs(self):
        if len(self.source_refs) != len(self.source_texts):
            raise ValueError("referent_candidate_source_texts_must_match_refs")
        return self


class ReferentCandidateData(_ToolModel):
    candidates: list[ReferentCandidate] = Field(max_length=8)
    truncated: bool
    excluded_count: int = Field(ge=0)
    identity_status: Literal["not_checked"]

    @model_validator(mode="after")
    def validate_truncation(self):
        if self.truncated != (self.excluded_count > 0):
            raise ValueError("referent_candidate_truncation_count_mismatch")
        return self


class RetrievalCoverage(_ToolModel):
    examined_records: list[str]
    unattempted_records: list[str]
    stop_reason: str | None


class RetrievalData(_ToolModel):
    record_ids: list[str]
    evidence_ids: list[str]
    context_hash: str
    new_evidence: bool
    coverage: RetrievalCoverage


class RepairData(_ToolModel):
    claim_ref: VersionedRef
    proposed_quotes: list[Quote]
    reason_code: str


class MetricData(_ToolModel):
    claim_ref: VersionedRef
    validation_status: ValidationStatus
    quantity: QuantityValue | None
    normalized_literal: str | bool | None
    issues: list[ToolIssue]


class ShaclIssue(_ToolModel):
    focus_node: str
    path: str | None
    value: str | None
    source_shape: str
    constraint_component: str
    severity: str
    messages: list[str]


class FocusCoverage(_ToolModel):
    expected: list[str]
    actual: list[str]
    missing: list[str]
    executed_shapes: list[str]
    complete: bool


class ShaclData(_ToolModel):
    profile: str
    evaluated: bool
    conforms: bool | None
    validation_status: ValidationStatus
    report: list[ShaclIssue]
    coverage: FocusCoverage


RELATION_PROFILE = "ontology-relation-v1"


class RelationValidationData(_ToolModel):
    profile: Literal["ontology-relation-v1"]
    claim_ref: VersionedRef
    content_hash: str
    context_hash: str
    ontology_snapshot_id: str
    menu_hash: str
    ontology_status: ValidationStatus
    identity_status: ValidationStatus
    validation_status: ValidationStatus
    semantic_status: Literal["not_checked"]
    checked_constraints: list[Literal[
        "task_subject", "predicate_menu", "range", "source_binding", "exact_mention_identity",
    ]]
    duplicate_entities: dict[str, VersionedRef]
    issues: list[ToolIssue]


class ToolCall(_ToolModel):
    call_id: str
    name: str
    arguments_json: str


@dataclass(frozen=True)
class ToolDefinition:
    name: ToolName
    description: str
    args_type: type[EvidenceModel]
    result_type: type[EvidenceModel]
    allowed_stages: frozenset[ToolStage]
    model_callable: bool

    def to_openai(self, *, strict: bool = False) -> dict:
        return {
            "type": "function",
            "name": self.name,
            "description": self.description,
            "parameters": self.args_type.model_json_schema(),
            "strict": strict,
        }


TOOL_DEFINITIONS: Mapping[ToolName, ToolDefinition] = MappingProxyType({
    definition.name: definition
    for definition in (
        ToolDefinition(
            "get_schema_card", "读取当前主体的本体菜单或指定谓词卡片；定义约束，不证明原文事实。",
            GetSchemaCardArgs, ToolResult[SchemaCardData],
            frozenset({"discovery", "verification"}), True,
        ),
        ToolDefinition(
            "inspect_evidence", "读取当前任务授权的完整原文单元及记录角色；不自动授予事实资格。",
            InspectEvidenceArgs, ToolResult[EvidenceData],
            frozenset({"discovery", "verification"}), True,
        ),
        ToolDefinition(
            "resolve_source_anchor",
            "以逐字文本和可选同源上下文唯一定位原文；歧义时不选择最近匹配。",
            ResolveSourceAnchorArgs, ToolResult[AnchorData],
            frozenset({"discovery", "verification"}), True,
        ),
        ToolDefinition(
            "propose_mentions",
            "依据当前本体卡片及受控词表建议提及跨度和类型角色；不确认实体身份。",
            ProposeMentionsArgs, ToolResult[MentionData], frozenset({"discovery"}), True,
        ),
        ToolDefinition(
            "check_claim_binding",
            "检查已冻结声明的主体、字段、对象和单位引用归属；不替代语义核验。",
            CheckClaimBindingArgs, ToolResult[BindingData],
            frozenset({"verification", "finalize"}), True,
        ),
        ToolDefinition(
            "query_instances", "以文档提及在已授权来源查询实例候选；命中不等于身份或文档关系成立。",
            QueryInstancesArgs, ToolResult[InstanceData], frozenset({"discovery"}), True,
        ),
        ToolDefinition(
            "find_referent_candidates",
            "从当前运行已授权实体召回物理提及、名称变体或指代候选；"
            "返回绑定核验材料，不证明共指、不合并实体、不授予新事实发现权限。",
            FindReferentCandidatesArgs, ToolResult[ReferentCandidateData],
            frozenset({"discovery", "verification"}), True,
        ),
        ToolDefinition(
            "retrieve_evidence", "针对当前主体谓词和缺失维度检索完整补充记录；摘要只作定位。",
            RetrieveEvidenceArgs, ToolResult[RetrievalData],
            frozenset({"discovery", "verification"}), True,
        ),
        ToolDefinition(
            "propose_repair",
            "针对当前冻结声明实际存在的错误提出局部修复，不直接改写声明或接纳状态。",
            ProposeRepairArgs, ToolResult[RepairData],
            frozenset({"discovery", "verification"}), True,
        ),
        ToolDefinition(
            "validate_metric", "校验声明数量表示及单位；语义前置条件未满足时不输出可信规范化结果。",
            ValidateMetricArgs, ToolResult[MetricData], frozenset({"finalize"}), False,
        ),
        ToolDefinition(
            "validate_graph", "校验冻结关系的本体约束与实体身份；不证明原文语义。"
            "关系使用 ontology-relation-v1；属性表示校验仅供控制器执行。",
            ValidateGraphArgs, ToolResult[ShaclData | RelationValidationData],
            frozenset({"verification", "finalize"}), True,
        ),
    )
})


def get_tool_definitions(*, strict: bool = False) -> list[dict]:
    """Export the capability catalog; the runtime selects permitted tools per turn."""
    return [definition.to_openai(strict=strict) for definition in TOOL_DEFINITIONS.values()]


@dataclass(frozen=True)
class ToolObservation:
    request_attempt: int
    call: ToolCall
    parsed_arguments: EvidenceModel | None
    result_ref: str
    result: ToolResult

    def __post_init__(self) -> None:
        if type(self.request_attempt) is not int or self.request_attempt < 1:
            raise ValueError("request_attempt must be a positive integer")
        if self.parsed_arguments is None:
            if not isinstance(self.result, ToolErrorResult):
                raise ValueError("unparsed calls require the common error result")
            return
        definition = TOOL_DEFINITIONS.get(self.call.name)
        if definition is None or not isinstance(self.parsed_arguments, definition.args_type):
            raise ValueError("parsed arguments must match the registered tool")
        if not isinstance(self.result, definition.result_type):
            raise ValueError("result data must use the registered tool's concrete result type")
