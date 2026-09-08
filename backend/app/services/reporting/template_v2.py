"""Strict authoring contracts. Labels and free text never select business data."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator

Id = Annotated[str, Field(min_length=1, max_length=500)]
State = Literal[
    "ready",
    "missing",
    "confirmed_absent",
    "not_applicable",
    "pending_review",
    "conflict",
    "invalid",
    "incomplete",
    "unavailable",
]
Action = Literal[
    "block",
    "annotate",
    "omit",
    "render_absence",
    "render_not_applicable",
    "parameter_default",
]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Diagnostic(Model):
    code: str
    severity: Literal["error", "warning", "info"] = "error"
    schema_path: str = ""
    message: str = ""
    input_ref: str | None = None
    binding_ref: str | None = None
    output_ref: str | None = None
    execution_scope: str | None = None
    expected: Any = None
    actual: Any = None
    evidence_refs: list[str] = Field(default_factory=list)
    remediation: str = ""


class ReportingError(ValueError):
    def __init__(self, code, message="", *, status=422, **details):
        super().__init__(message or code)
        self.code, self.status = code, status
        self.detail = {"code": code, "message": message or code, **details}


class Cardinality(Model):
    min_count: int = Field(default=0, ge=0)
    max_count: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def bounds(self):
        if self.max_count is not None and self.max_count < self.min_count:
            raise ValueError("cardinality bounds contradict")
        return self


class Constraints(Cardinality):
    quantifier: Literal["exists", "all"] = "exists"
    require_complete_set: bool = False
    allow_absence: bool = False
    allow_not_applicable: bool = False
    nonempty: bool = False
    minimum: str | None = None
    maximum: str | None = None
    unit: str | None = None
    vocabulary_ref: str | None = None

    @model_validator(mode="after")
    def numeric_bounds(self):
        try:
            limits = [Decimal(v) for v in (self.minimum, self.maximum) if v is not None]
            if any(not v.is_finite() for v in limits) or (
                len(limits) == 2 and limits[0] > limits[1]
            ):
                raise ValueError("invalid numeric constraint bounds")
        except InvalidOperation as exc:
            raise ValueError("invalid numeric constraint bounds") from exc
        return self


class TypeSpec(Model):
    kind: Literal[
        "string",
        "boolean",
        "integer",
        "decimal",
        "date",
        "datetime",
        "year_month",
        "enum",
        "quantity",
        "range",
        "entity",
        "record",
        "list",
    ]
    datatype_iri: str | None = None
    type_contract_ref: str | None = None
    class_iris: list[str] = Field(default_factory=list)
    display_property_iri: str | None = None
    vocabulary_ref: str | None = None
    enum_values: list[str] = Field(default_factory=list)
    dimension: str | None = None
    unit: str | None = None
    item_type: TypeSpec | None = None
    fields: dict[str, TypeField] = Field(default_factory=dict)
    item_identity: str | None = None
    cardinality: Cardinality = Field(default_factory=Cardinality)
    lower_inclusive: bool | None = None
    upper_inclusive: bool | None = None

    @model_validator(mode="after")
    def complete(self):
        if self.kind in {"list", "range"} and self.item_type is None:
            raise ValueError("list/range requires item_type")
        if self.kind == "range" and self.item_type.kind not in {
            "integer",
            "decimal",
            "quantity",
            "date",
            "datetime",
            "year_month",
        }:
            raise ValueError("range requires ordered endpoint types")
        if self.kind == "quantity" and (not self.dimension or not self.unit):
            raise ValueError("quantity requires explicit dimension and unit")
        if self.kind == "entity" and not self.class_iris:
            raise ValueError("entity requires class constraints")
        if self.kind == "enum" and not self.vocabulary_ref:
            raise ValueError("enum requires a vocabulary contract")
        if self.kind != "record" and self.fields:
            raise ValueError("only record types have fields")
        return self


class TypeField(Model):
    type: TypeSpec
    semantic_ref: str
    required: bool = False
    constraints: Constraints = Field(default_factory=Constraints)


class Step(Model):
    predicate_iri: Id
    direction: Literal["forward", "inverse"] = "forward"


class Dependency(Model):
    kind: Literal["binding", "input", "condition"]
    ref: Id
    field_path: list[str] = Field(default_factory=list)


class Root(Model):
    kind: Literal["source_root", "entity_ref", "binding", "repeat_item"]
    entity_id: str | None = None
    binding_ref: str | None = None
    repeat_ref: str | None = None

    @model_validator(mode="after")
    def reference(self):
        field = {
            "entity_ref": "entity_id",
            "binding": "binding_ref",
            "repeat_item": "repeat_ref",
        }.get(self.kind)
        if field and not getattr(self, field):
            raise ValueError(f"{self.kind} requires {field}")
        return self


class Scope(Model):
    source_slot: Id
    root: Root = Field(default_factory=lambda: Root(kind="source_root"))
    predicate_path: list[Step] = Field(default_factory=list, max_length=16)
    fact_source_refs: list[str] = Field(default_factory=list)
    object_ids: list[str] | None = None
    cardinality: Cardinality = Field(default_factory=Cardinality)
    applicable_at: str | None = None
    require_complete_set: bool = False
    discovery_ref: str | None = None
    condition_refs: list[str] = Field(default_factory=list)


class OntologyContract(Model):
    kind: Literal["ontology"] = "ontology"
    release_ref: Id = "template.ontology_release_ref"
    root_class_iri: Id = "auto:class"
    result_class_iri: Id = "auto:class"


class BaseBinding(Model):
    binding_id: Id
    label: str = ""
    dependencies: list[Dependency] = Field(default_factory=list)
    output_type: TypeSpec | None = None


class FactsBinding(BaseBinding):
    kind: Literal["facts"]
    contract_ref: OntologyContract
    scope: Scope
    assertion_nature: Literal["observed_fact", "planned_control"] = "observed_fact"


class RecordScope(Model):
    record_slot: Id
    subject_ref: str | None = None
    applicable_at: str | None = None


class ContextBinding(BaseBinding):
    kind: Literal["context"]
    contract_ref: Id
    scope: RecordScope


class WorkflowBinding(BaseBinding):
    kind: Literal["workflow"]
    contract_ref: Id
    scope: RecordScope


class InputRef(Model):
    kind: Literal["input_ref"] = "input_ref"
    input_id: Id
    field_path: list[str] = Field(default_factory=list)
    scope: Literal["input", "item"] = "input"


class Expression(Model):
    op: Literal[
        "input",
        "parameter",
        "literal",
        "and",
        "or",
        "not",
        "eq",
        "ne",
        "lt",
        "le",
        "gt",
        "ge",
        "exists",
        "all",
    ]
    input_ref: InputRef | None = None
    parameter: str | None = None
    value: str | int | bool | None = None
    literal_type: TypeSpec | None = None
    args: list[Expression] = Field(default_factory=list)

    @model_validator(mode="after")
    def arity(self):
        if self.op == "input" and self.input_ref is None:
            raise ValueError("input expression requires input_ref")
        if self.op == "parameter" and not self.parameter:
            raise ValueError("parameter expression requires parameter")
        arity = {
            "not": 1,
            "exists": 1,
            "all": 1,
            "eq": 2,
            "ne": 2,
            "lt": 2,
            "le": 2,
            "gt": 2,
            "ge": 2,
        }
        if self.op in arity and len(self.args) != arity[self.op]:
            raise ValueError("invalid expression arity")
        if self.op in {"and", "or"} and not self.args:
            raise ValueError("logical expression requires operands")
        if self.op in {"input", "parameter", "literal"} and self.args:
            raise ValueError("leaf expressions cannot have operands")
        return self


class Order(Model):
    field_ref: str | None = None
    kind: Literal["field", "entity_id"] = "field"
    direction: Literal["asc", "desc"] = "asc"


class ViewOperation(Model):
    kind: Literal["project", "filter", "sort", "group", "join", "range", "unit_convert"]
    source: InputRef
    right: InputRef | None = None
    fields: dict[str, list[str]] = Field(default_factory=dict)
    predicate: Expression | None = None
    order_by: list[Order] = Field(default_factory=list)
    keys: list[str] = Field(default_factory=list)
    right_keys: list[str] = Field(default_factory=list)
    cardinality: Literal["one_to_one", "many_to_one"] = "one_to_one"
    unmatched: Literal["missing", "block"] = "missing"
    lower_field: str | None = None
    upper_field: str | None = None
    lower_inclusive: bool | None = None
    upper_inclusive: bool | None = None
    conversion_ref: str | None = None

    @model_serializer(mode="wrap")
    def preserve_legacy_identity(self, handler):
        data = handler(self)
        for key in ("lower_inclusive", "upper_inclusive"):
            if getattr(self, key) is None:
                data.pop(key, None)
        return data

    @model_validator(mode="after")
    def well_formed(self):
        if self.kind == "join" and (
            not self.right or not self.keys or len(self.keys) != len(self.right_keys)
        ):
            raise ValueError("join requires explicit matching keys")
        if self.kind == "filter" and self.predicate is None:
            raise ValueError("filter requires predicate")
        if self.kind == "range" and (not self.lower_field or not self.upper_field):
            raise ValueError("range requires explicit endpoint fields")
        if self.kind == "unit_convert" and not self.conversion_ref:
            raise ValueError("conversion requires a versioned contract")
        return self


class DerivedBinding(BaseBinding):
    kind: Literal["derived"]
    provider: Literal["rule_result", "view", "calculation"]
    contract_ref: Id
    parameters: dict[str, InputRef] = Field(default_factory=dict)
    operation: ViewOperation | None = None
    check_ref: Id | None = None

    @model_validator(mode="after")
    def provider_contract(self):
        if self.provider == "view" and self.operation is None:
            raise ValueError("view requires a structured operation")
        if self.provider == "rule_result" and self.operation is not None:
            raise ValueError("rule_result cannot execute a view")
        if self.provider == "calculation" and (
            not self.check_ref or self.parameters or self.operation
        ):
            raise ValueError("calculation requires exactly one versioned check reference")
        if self.provider != "calculation" and self.check_ref:
            raise ValueError("check_ref is only valid for calculation providers")
        return self


Binding = Annotated[
    FactsBinding | ContextBinding | WorkflowBinding | DerivedBinding,
    Field(discriminator="kind"),
]


class ProjectionField(Model):
    value: Projection
    required: bool = False
    constraints: Constraints = Field(default_factory=Constraints)


class Projection(Model):
    kind: Literal[
        "identity",
        "property",
        "entity",
        "entities",
        "record",
        "records",
        "field",
        "relation_presence",
    ]
    property_iri: str | None = None
    type_contract_ref: str | None = None
    predicate_path: list[Step] = Field(default_factory=list, max_length=16)
    class_iri: str | None = None
    fields: dict[str, ProjectionField] = Field(default_factory=dict)
    field_path: list[str] = Field(default_factory=list)
    item_identity: str = "entity_id"
    display_property_iri: str | None = None
    languages: list[str] = Field(default_factory=lambda: ["zh", "en"])

    @model_validator(mode="after")
    def shape(self):
        if self.type_contract_ref and self.kind != "property":
            raise ValueError("a property type supplement requires a property projection")
        if self.kind == "property" and not self.property_iri:
            raise ValueError("property projection requires property_iri")
        if self.kind in {"record", "records"} and not self.fields:
            raise ValueError("record projection requires explicit fields")
        if self.kind == "field" and not self.field_path:
            raise ValueError("field projection requires field_path")
        if self.kind == "relation_presence" and not self.predicate_path:
            raise ValueError("relation presence requires an explicit path")
        return self


class StatusPolicy(Model):
    missing: Action = "annotate"
    incomplete: Action = "annotate"
    pending_review: Action = "annotate"
    unavailable: Action = "annotate"
    conflict: Action = "block"
    invalid: Action = "block"
    confirmed_absent: Action = "annotate"
    not_applicable: Action = "annotate"

    @model_validator(mode="after")
    def no_bad_values(self):
        if "parameter_default" in self.model_dump().values():
            raise ValueError(
                "PARAMETER_DEFAULT_CONTRACT_REQUIRED: use a registered parameter binding"
            )
        if self.conflict not in {"block", "annotate"} or self.invalid not in {"block", "annotate"}:
            raise ValueError("conflict and invalid cannot be omitted or defaulted")
        return self


class InputDefinition(Model):
    input_id: Id
    name: Id
    label: str = ""
    binding_ref: Id
    projection: Projection
    expected_type: TypeSpec | None = None
    constraints: Constraints = Field(default_factory=Constraints)
    required: bool = False
    status_policy: StatusPolicy = Field(default_factory=StatusPolicy)
    origin: dict | None = None


class Format(Model):
    kind: Literal["text", "decimal", "date", "entity_labels", "entity_id", "quantity"] = "text"
    decimal_places: int | None = Field(default=None, ge=0, le=18)
    label_property_iri: str | None = None
    languages: list[str] = Field(default_factory=lambda: ["zh", "en"])
    separator: str = "、"


class ContentNode(Model):
    kind: Literal[
        "text",
        "input_ref",
        "claim_ref",
        "paragraph",
        "if",
        "repeat",
        "signature_region",
    ]
    text: str = ""
    input_id: str | None = None
    field_path: list[str] = Field(default_factory=list)
    scope: Literal["input", "item"] = "input"
    format: Format = Field(default_factory=Format)
    claim_id: str | None = None
    condition: Expression | None = None
    children: list[ContentNode] = Field(default_factory=list)
    otherwise: list[ContentNode] = Field(default_factory=list)
    unknown: list[ContentNode] = Field(default_factory=list)
    signature_region_id: str | None = None

    @model_validator(mode="after")
    def references(self):
        if self.kind in {"input_ref", "repeat"} and not self.input_id:
            raise ValueError("input reference requires stable input_id")
        if self.kind == "claim_ref" and not self.claim_id:
            raise ValueError("claim reference requires claim_id")
        if self.kind == "if" and self.condition is None:
            raise ValueError("conditional node requires condition")
        if self.kind == "signature_region" and not self.signature_region_id:
            raise ValueError("signature region requires stable identity")
        return self


class PromptSpec(Model):
    instructions: str = ""
    input_refs: list[InputRef] = Field(default_factory=list)
    required_refs: list[InputRef] = Field(default_factory=list)
    claim_refs: list[str] = Field(default_factory=list)
    policy_ref: Id


class NarrativeRender(Model):
    kind: Literal["narrative"]
    mode: Literal["composed", "assisted"] = "composed"
    nodes: list[ContentNode] = Field(default_factory=list)
    prompt: PromptSpec | None = None
    fallback: list[ContentNode] | None = None

    @model_validator(mode="after")
    def explicit_mode(self):
        if self.mode == "assisted" and self.prompt is None:
            raise ValueError("assisted requires prompt policy")
        return self


class Column(Model):
    column_id: Id
    title: str
    field_ref: str | None = None
    value: RowNumber | None = None
    format: Format = Field(default_factory=Format)

    @model_validator(mode="after")
    def one_source(self):
        if (self.field_ref is None) == (self.value is None):
            raise ValueError("column requires exactly one field_ref or row_number")
        return self


class RowNumber(Model):
    kind: Literal["row_number"]


class TableRender(Model):
    kind: Literal["table"]
    rows: InputRef
    row_key: Id = "entity_id"
    columns: list[Column] = Field(min_length=1)
    order_by: list[Order] = Field(default_factory=list)
    group_by: list[str] = Field(default_factory=list)


class FormField(Model):
    field_id: Id
    label: str
    value: InputRef | None = None
    format: Format = Field(default_factory=Format)
    signature_region_id: str | None = None


class FormRender(Model):
    kind: Literal["form"]
    fields: list[FormField]


class ListRender(Model):
    kind: Literal["list"]
    items: InputRef
    nodes: list[ContentNode] = Field(default_factory=list)
    ordered: bool = False


class StaticRender(Model):
    kind: Literal["static"]
    nodes: list[ContentNode]
    approval_ref: Id


Render = Annotated[
    NarrativeRender | TableRender | FormRender | ListRender | StaticRender,
    Field(discriminator="kind"),
]


class BindingUse(Model):
    binding_ref: Id


class InputUse(Model):
    input_ref: Id
    alias: Id
    required: bool = False


class OutputUnit(Model):
    output_id: Id
    title: str = ""
    origin: dict | None = None
    bindings: list[BindingUse] = Field(default_factory=list)
    inputs: list[InputUse] = Field(default_factory=list)
    render: Render
    when: Expression | None = None


class Repeat(Model):
    repeat_id: Id
    input_ref: Id
    item_key: Id = "entity_id"
    order_by: list[Order] = Field(default_factory=list)


class Group(Model):
    group_id: Id
    title: str = ""
    origin: dict | None = None
    layout: Literal["flow", "grid"] = "flow"
    repeat: Repeat | None = None
    units: list[OutputUnit] = Field(default_factory=list)
    groups: list[Group] = Field(default_factory=list)


class CompletenessRequirement(Model):
    requirement_id: Id
    input_ref: Id
    field_path: list[str] = Field(default_factory=list)
    constraints: Constraints = Field(default_factory=Constraints)
    when: Expression | None = None


class Section(Model):
    section_id: Id
    title: str = ""
    origin: dict | None = None
    completeness_requirements: list[CompletenessRequirement] = Field(default_factory=list)
    groups: list[Group] = Field(default_factory=list)


class SourceSlot(Model):
    source_slot_id: Id
    kind: Literal["document", "external"]
    class_iri: Id
    required: bool = True
    allowed_role: Literal["analysis_source", "default_source"] = "analysis_source"
    mapping_refs: list[str] = Field(default_factory=list)


class Definitions(Model):
    bindings: dict[str, Binding] = Field(default_factory=dict)
    inputs: dict[str, InputDefinition] = Field(default_factory=dict)


class Budget(Model):
    max_hops: int = Field(default=16, ge=1, le=16)
    max_records: int = Field(default=1000, ge=1, le=10000)
    max_units: int = Field(default=256, ge=1, le=1024)
    max_repeat_depth: int = Field(default=4, ge=1, le=8)
    max_nodes: int = Field(default=10000, ge=1, le=100000)
    max_input_tokens: int = Field(default=8192, ge=1, le=131072)
    max_output_tokens: int = Field(default=2048, ge=1, le=16384)
    timeout_s: int = Field(default=120, ge=1, le=900)
    max_attempts: int = Field(default=1, ge=1, le=3)


class MigrationIssue(Model):
    code: str
    old_id: str
    message: str
    resolved_by: str | None = None
    resolution_ref: str | None = None


class LegacyOrigin(Model):
    template_id: str
    version: str
    schema_hash: str
    migration_plan_ref: str


class CalculationCheck(Model):
    check_id: Id
    source_slot: Id
    contract_ref: Id


class TemplateStyle(Model):
    font: str = "Arial"
    font_size_pt: int = Field(default=11, ge=6, le=32)
    assets: list[str] = Field(default_factory=list)


class TemplateV2(Model):
    schema_version: Literal[2]
    template_family_id: Id
    template_revision_id: Id
    revision_no: int = Field(ge=1)
    document_revision: str = ""
    doc_no: str = ""
    ontology_release_ref: Id = "auto:ontology"
    source_slots: list[SourceSlot] = Field(default_factory=list)
    definitions: Definitions = Field(default_factory=Definitions)
    calculation_checks: list[CalculationCheck] = Field(default_factory=list)
    sections: list[Section] = Field(default_factory=list)
    style_profile_ref: Id = "auto:style"
    publication_policy_ref: Id = "auto:policy"
    style: TemplateStyle | None = None
    budget: Budget = Field(default_factory=Budget)
    legacy: LegacyOrigin | None = None
    migration_issues: list[MigrationIssue] = Field(default_factory=list)

    @model_serializer(mode="wrap")
    def preserve_legacy_identity(self, handler):
        data = handler(self)
        if self.style is None:
            data.pop("style", None)
        return data


for _model in (TypeSpec, TypeField, Projection, ProjectionField, Column, ContentNode, TemplateV2):
    _model.model_rebuild()
