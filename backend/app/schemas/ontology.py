from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ModuleResponse(BaseModel):
    key: str
    iri: str
    label: str | None = None
    class_count: int = 0
    individual_count: int = 0


class TreeNodeResponse(BaseModel):
    iri: str
    name: str
    label: str | None = None
    individual_count: int = 0
    children: list[TreeNodeResponse] = []


class PropertyInfo(BaseModel):
    iri: str
    name: str
    label: str | None = None
    range: list[str] = []


class RestrictionInfo(BaseModel):
    property: str
    type: str
    value: str | None = None
    cardinality: int | None = None


class ClassDetailResponse(BaseModel):
    iri: str
    name: str
    label_zh: str | None = None
    label_en: str | None = None
    comment: str | None = None
    module: str | None = None
    bfo_category: str | None = None
    parent_iris: list[str] = []
    children_iris: list[str] = []
    individual_count: int = 0
    object_properties: list[PropertyInfo] = []
    data_properties: list[PropertyInfo] = []
    restrictions: list[RestrictionInfo] = []


# ===========================================================================
# T-Box workbench (能力一) — editable metadata schemas (contracts §2–§11)
# ===========================================================================


class VersionedMixin(BaseModel):
    """Carries the opt/dev/chen/ontology-agent/specsimistic-concurrency version the client last read (R4)."""

    expected_version: int = Field(..., description="客户端读取时的版本号；服务端 CAS 不匹配→409")


# --- E5 restriction summary (embedded in ClassDetail) ----------------------
class RestrictionSummary(BaseModel):
    id: str
    kind: str
    property_iri: str | None = None
    property_kind: str | None = None
    filler_iri: str | None = None
    cardinality: int | None = None
    version: int
    status: str


# --- E6 mapping ------------------------------------------------------------
class MappingCreate(BaseModel):
    mapping_type: str
    target: str
    source_system: str | None = None


class MappingUpdate(MappingCreate, VersionedMixin):
    pass


class Mapping(BaseModel):
    id: str
    class_iri: str | None = None
    mapping_type: str
    target: str
    source_system: str | None = None
    health: str = "ok"
    version: int
    status: str


class MappingHealth(BaseModel):
    ok: list[str] = []
    unmapped: list[str] = []
    drift: list[str] = []
    orphan: list[str] = []


# --- E1 class --------------------------------------------------------------
class ClassCreate(BaseModel):
    slpra_iri: str
    label: str
    comment: str | None = None
    module: str | None = None
    parent_iri: str | None = None
    bfo_category: str | None = None
    field_schema: dict | None = None


class ClassUpdate(VersionedMixin):
    label: str | None = None
    comment: str | None = None
    module: str | None = None
    parent_iri: str | None = None
    bfo_category: str | None = None
    field_schema: dict | None = None


class ClassDetail(BaseModel):
    id: str
    slpra_iri: str
    label: str
    comment: str | None = None
    module: str | None = None
    parent_iri: str | None = None
    bfo_category: str | None = None
    field_schema: dict | None = None
    status: str
    version: int
    is_reviewed: bool = False
    is_disabled: bool = False
    confidence: float | None = None
    restrictions: list[RestrictionSummary] = []
    mappings: list[Mapping] = []
    created_at: datetime | None = None
    updated_at: datetime | None = None


# --- E2 link type (object property / relation) -----------------------------
class LinkTypeCreate(BaseModel):
    slpra_iri: str
    label: str
    comment: str | None = None
    domain_iri: str | None = None
    range_iri: str | None = None
    inverse_iri: str | None = None
    min_cardinality: int | None = None
    max_cardinality: int | None = None
    is_functional: bool = False
    is_symmetric: bool = False
    is_transitive: bool = False


class LinkTypeUpdate(LinkTypeCreate, VersionedMixin):
    slpra_iri: str | None = None  # type: ignore[assignment]
    label: str | None = None  # type: ignore[assignment]


class LinkTypeDetail(BaseModel):
    id: str
    slpra_iri: str
    label: str
    comment: str | None = None
    domain_iri: str | None = None
    range_iri: str | None = None
    inverse_iri: str | None = None
    min_cardinality: int | None = None
    max_cardinality: int | None = None
    is_functional: bool = False
    is_symmetric: bool = False
    is_transitive: bool = False
    status: str
    version: int
    is_disabled: bool = False
    # 继承自祖先类时填充（直接声明在所查类上则为 None）
    inherited_from_iri: str | None = None
    inherited_from_label: str | None = None


# --- E3 data property ------------------------------------------------------
class DataPropertyCreate(BaseModel):
    slpra_iri: str
    label: str
    comment: str | None = None
    domain_iri: str | None = None
    datatype: str = "string"
    unit: str | None = None
    controlled_vocab: dict | None = None


class DataPropertyUpdate(DataPropertyCreate, VersionedMixin):
    slpra_iri: str | None = None  # type: ignore[assignment]
    label: str | None = None  # type: ignore[assignment]
    datatype: str | None = None  # type: ignore[assignment]


class RiskDataPropertyCreate(BaseModel):
    slpra_iri: str
    label: str
    domain_iri: str | None = None
    datatype: str = "string"
    vocab: str  # OEB / PDE / sensitizer ... (key into /risk-vocabularies)


class DataPropertyDetail(BaseModel):
    id: str
    slpra_iri: str
    label: str
    comment: str | None = None
    domain_iri: str | None = None
    datatype: str
    unit: str | None = None
    controlled_vocab: dict | None = None
    status: str
    version: int
    is_disabled: bool = False
    # 继承自祖先类时填充（直接声明在所查类上则为 None）
    inherited_from_iri: str | None = None
    inherited_from_label: str | None = None


# --- E4 action -------------------------------------------------------------
class ActionCreate(BaseModel):
    slpra_iri: str
    label: str
    comment: str | None = None
    actor_iri: str | None = None
    target_iri: str | None = None
    precondition: dict | None = None
    postcondition: dict | None = None
    params: dict | None = None


class ActionUpdate(ActionCreate, VersionedMixin):
    slpra_iri: str | None = None  # type: ignore[assignment]
    label: str | None = None  # type: ignore[assignment]


class ActionDetail(BaseModel):
    id: str
    slpra_iri: str
    label: str
    comment: str | None = None
    actor_iri: str | None = None
    target_iri: str | None = None
    precondition: dict | None = None
    postcondition: dict | None = None
    params: dict | None = None
    status: str
    version: int
    is_disabled: bool = False


# --- E5 restriction --------------------------------------------------------
class RestrictionCreate(BaseModel):
    kind: str
    property_iri: str | None = None
    property_kind: str | None = None
    filler_iri: str | None = None
    cardinality: int | None = None


class RestrictionUpdate(RestrictionCreate, VersionedMixin):
    kind: str | None = None  # type: ignore[assignment]


# --- §8 validation ---------------------------------------------------------
class ValidationIssue(BaseModel):
    code: str
    message: str
    entity_iri: str | None = None


class ReasonerStatus(BaseModel):
    ran: bool = False
    consistent: bool | None = None
    note: str | None = None


class ValidationReport(BaseModel):
    blocking: list[ValidationIssue] = []
    warnings: list[ValidationIssue] = []
    reasoner: ReasonerStatus = Field(default_factory=ReasonerStatus)


# --- §9 import / export / diff ---------------------------------------------
class ImportResult(BaseModel):
    added: int = 0
    updated: int = 0
    conflicts: list[str] = []


class DiffResult(BaseModel):
    turtle_preview: str = ""
    triples_added: list[str] = []
    triples_removed: list[str] = []


# --- §10 release -----------------------------------------------------------
class ReleaseCreate(BaseModel):
    title: str


class ChangeLogItem(BaseModel):
    id: str
    entity_table: str
    entity_id: str
    change_kind: str
    before: dict | None = None
    after: dict | None = None


class ReleaseSummary(BaseModel):
    id: str
    release_no: str
    title: str
    status: str
    ttl_commit_sha: str | None = None
    published_at: datetime | None = None
    created_at: datetime | None = None


class ReleaseDetail(ReleaseSummary):
    ttl_diff: str | None = None
    validation_report: dict | None = None
    change_log: list[ChangeLogItem] = []


# --- §11 audit -------------------------------------------------------------
class AuditEntry(BaseModel):
    id: int
    action: str
    entity_iri: str | None = None
    actor: str | None = None
    release_id: str | None = None
    details: dict | None = None
    created_at: datetime | None = None


# --- §4b risk vocabularies -------------------------------------------------
class RiskVocabulary(BaseModel):
    key: str
    label: str
    values: list[str] = []


# ===========================================================================
# 声明式规则层 (能力六 / spec 006) — E11/E12/E13 可版本化规则数据 (US3, T035)
# ===========================================================================


# --- E11 分类判据 (充要定义) ------------------------------------------------
class ClassificationCriterionCreate(BaseModel):
    criterion_key: str
    target_class_iri: str = Field(..., description="判据点亮的目标风险类 slpra_iri")
    pattern: dict = Field(..., description="解释器模式 AST（受限词汇）")
    regulation_ref: str | None = None
    logic_role: str = "defined"


class ClassificationCriterionUpdate(VersionedMixin):
    target_class_iri: str | None = None
    pattern: dict | None = None
    regulation_ref: str | None = None
    logic_role: str | None = None
    is_disabled: bool | None = None


class ClassificationCriterionDetail(BaseModel):
    id: str
    criterion_key: str
    target_class_iri: str | None = None
    target_class_label: str | None = None
    pattern: dict
    regulation_ref: str | None = None
    logic_role: str
    status: str
    version: int
    is_disabled: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None


# --- E12 决策规则 (产生式 R-ED / R-SC / R-CP) -------------------------------
class DecisionRuleCreate(BaseModel):
    rule_key: str
    rule_group: str = Field(
        ..., description="equipment_dedication | scenario_identification | contamination_risk"
    )
    antecedent: dict = Field(..., description="解释器前件模式 AST")
    consequent: dict = Field(..., description="命中结论（逐字镜像 RuleResult.conclusion）")
    priority: int = 100
    regulation_ref: str | None = None
    label: str | None = None
    comment: str | None = None


class DecisionRuleUpdate(VersionedMixin):
    rule_group: str | None = None
    antecedent: dict | None = None
    consequent: dict | None = None
    priority: int | None = None
    regulation_ref: str | None = None
    label: str | None = None
    comment: str | None = None
    is_disabled: bool | None = None


class DecisionRuleDetail(BaseModel):
    id: str
    slpra_iri: str
    rule_key: str
    rule_group: str
    antecedent: dict
    consequent: dict
    priority: int
    regulation_ref: str | None = None
    label: str
    comment: str | None = None
    status: str
    version: int
    is_disabled: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None


# --- E13 冲突消解策略 (固定维度集，仅 GET/PUT) ------------------------------
class ConflictPolicyUpdate(VersionedMixin):
    strategy: str | None = None
    priority_lattice: dict | None = None
    override_direction: str | None = None
    regulation_ref: str | None = None
    comment: str | None = None
    is_disabled: bool | None = None


class ConflictPolicyDetail(BaseModel):
    id: str
    slpra_iri: str
    dimension: str
    strategy: str
    priority_lattice: dict | None = None
    override_direction: str | None = None
    regulation_ref: str | None = None
    label: str
    comment: str | None = None
    status: str
    version: int
    is_disabled: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None
