"""Versioned evidence contracts shared by extraction, review and published facts.

Validation records whether the *assertion*, including its polarity and conditions,
is supported. Positive projection is a separate decision; a supported negative
assertion is not a rejected positive assertion.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

NonEmpty = Annotated[str, Field(min_length=1)]
Digest = Annotated[str, Field(min_length=64, max_length=64)]
AssertionStatus = Literal["affirmed", "negated", "conditional", "hypothetical", "uncertain"]
PRODUCTION_ROLES = frozenset({"analysis_source", "default_source"})


class EvidenceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class EvidenceAnchor(EvidenceModel):
    document_hash: Digest
    parser_version: NonEmpty
    structure_hash: Digest
    evidence_id: NonEmpty
    section_node_id: NonEmpty
    block_id: NonEmpty
    paragraph_index: int | None = Field(default=None, ge=0)
    fragment_index: int | None = Field(default=None, ge=0)
    table_path: list[str] | None = None
    row_index: int | None = Field(default=None, ge=0)
    column_index: int | None = Field(default=None, ge=0)
    span_start: int | None = Field(default=None, ge=0)
    span_end: int | None = Field(default=None, ge=0)
    physical_page_number: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def valid_span(self):
        if (self.span_start is None) != (self.span_end is None):
            raise ValueError("both span bounds must be supplied, or both omitted")
        if self.span_start is not None and self.span_end <= self.span_start:
            raise ValueError("source spans must be nonempty half-open intervals")
        for digest in (self.document_hash, self.structure_hash):
            if any(char not in "0123456789abcdef" for char in digest):
                raise ValueError("source identities must be lowercase SHA256 digests")
        return self


class DocumentProvenance(EvidenceModel):
    kind: Literal["document"] = "document"
    document_role: Literal[
        "analysis_source", "default_source", "template_sample",
        "training_source", "training_report",
    ] = "analysis_source"
    anchors: list[EvidenceAnchor] = Field(min_length=1)
    excerpts: list[str] = Field(default_factory=list)


class ExternalRecordProvenance(EvidenceModel):
    kind: Literal["external_record"] = "external_record"
    system: NonEmpty
    dataset: NonEmpty
    record_key: NonEmpty
    record_version: NonEmpty
    field_path: NonEmpty
    value: Any
    fetched_at: str | None = None
    applicable_at: str | None = None
    identity_match_evidence: list[EvidenceAnchor] = Field(default_factory=list)
    record_snapshot: dict[str, Any] = Field(default_factory=dict)


class ManualProvenance(EvidenceModel):
    kind: Literal["manual"] = "manual"
    actor: NonEmpty
    review_id: NonEmpty
    value: Any
    reason: NonEmpty
    entered_at: str | None = None
    previous_value: Any = None


class DerivedProvenance(EvidenceModel):
    kind: Literal["derived"] = "derived"
    rule_id: NonEmpty
    rule_version: NonEmpty
    input_fact_ids: list[str] = Field(min_length=1)
    snapshot_id: NonEmpty
    parameters: dict[str, Any] = Field(default_factory=dict)
    value: Any
    conversion_record: dict[str, Any] = Field(default_factory=dict)


FactProvenance = Annotated[
    DocumentProvenance | ExternalRecordProvenance | ManualProvenance | DerivedProvenance,
    Field(discriminator="kind"),
]


class CandidateRef(EvidenceModel):
    candidate_id: NonEmpty
    revision: int = Field(ge=1)
    class_iri: str | None = None
    instance_iri: str | None = None


class BindingEvidence(EvidenceModel):
    method: Literal[
        "explicit_assertion", "table_record", "section_record",
        "identity_reference", "external_mapping", "manual_decision", "derivation",
    ]
    subject_candidate_id: NonEmpty
    predicate_iri: NonEmpty
    object_candidate_id: str | None = None
    anchors: list[EvidenceAnchor] = Field(default_factory=list)
    provenance_indexes: list[int] = Field(default_factory=list)
    record_mapping: dict[str, Any] = Field(default_factory=dict)
    resolver_version: str = "semantic-binding-v1"

    @model_validator(mode="after")
    def has_replayable_evidence(self):
        if not self.anchors and not self.provenance_indexes:
            raise ValueError("binding requires source anchors or provenance references")
        if any(index < 0 for index in self.provenance_indexes):
            raise ValueError("provenance indexes must be nonnegative")
        if self.method in {"table_record", "section_record", "external_mapping"}:
            if not self.record_mapping:
                raise ValueError("record binding requires an explicit record mapping")
        return self


class EvidenceRange(EvidenceModel):
    evidence_id: NonEmpty
    start: int | None = Field(default=None, ge=0)
    end: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def valid_interval(self):
        if (self.start is None) != (self.end is None):
            raise ValueError("range bounds must both be supplied")
        if self.start is not None and self.end <= self.start:
            raise ValueError("range must be a nonempty half-open interval")
        return self


class ScopeExpansion(EvidenceModel):
    reason: NonEmpty
    added_ranges: list[EvidenceRange] = Field(min_length=1)
    evidence: list[EvidenceAnchor] = Field(min_length=1)
    policy_version: str = "evidence-scope-v1"


class EvidenceScope(EvidenceModel):
    scope_id: NonEmpty
    revision: int = Field(default=1, ge=1)
    document_hash: Digest
    subject: CandidateRef
    ranges: list[EvidenceRange] = Field(min_length=1)
    excluded_ranges: list[EvidenceRange] = Field(default_factory=list)
    record_ids: list[str] = Field(default_factory=list)
    parent_scope_id: str | None = None
    construction_evidence: list[EvidenceAnchor] = Field(default_factory=list)
    reference_ranges: list[EvidenceRange] = Field(default_factory=list)
    expansion_history: list[ScopeExpansion] = Field(default_factory=list)
    policy_version: str = "evidence-scope-v1"


class LiteralValue(EvidenceModel):
    kind: Literal["text", "boolean", "date", "number", "range", "comparison"]
    raw_value: str
    normalized_value: str | bool | None = None
    datatype_iri: str = "http://www.w3.org/2001/XMLSchema#string"
    lower: str | None = None
    upper: str | None = None
    lower_inclusive: bool = True
    upper_inclusive: bool = True
    operator: Literal["eq", "lt", "le", "gt", "ge", "approx"] = "eq"
    raw_unit: str | None = None
    canonical_unit: str | None = None
    dimension: str | None = None
    normalizer_version: str = "literal-v2"
    conversion_record: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid_numeric_value(self):
        values = []
        if self.kind in {"number", "comparison"}:
            if not isinstance(self.normalized_value, str):
                raise ValueError("numbers must use exact decimal strings")
            values = [self.normalized_value]
        elif self.kind == "range":
            if self.lower is None or self.upper is None:
                raise ValueError("range requires both endpoints")
            values = [self.lower, self.upper]
        try:
            numbers = [Decimal(value) for value in values]
        except InvalidOperation as exc:
            raise ValueError("invalid decimal literal") from exc
        if any(not number.is_finite() for number in numbers):
            raise ValueError("non-finite numeric literals are not facts")
        if len(numbers) == 2 and numbers[0] > numbers[1]:
            raise ValueError("range lower endpoint exceeds upper endpoint")
        return self


class ValidationIssue(EvidenceModel):
    code: NonEmpty
    message: str = ""
    stage: str = "validation"


class TypeVerification(EvidenceModel):
    supported: bool
    reason: NonEmpty
    identity_supported: bool = False
    verifier_version: str = "entity-types-v1"


class Candidate(EvidenceModel):
    schema_version: Literal[1] = 1
    candidate_id: NonEmpty
    revision: int = Field(default=1, ge=1)
    kind: Literal["entity", "property", "relationship"]
    class_iri: str | None = None
    text: str = ""
    identity: dict[str, str] = Field(default_factory=dict)
    type_verification: TypeVerification | None = None
    subject: CandidateRef | None = None
    object: CandidateRef | None = None
    predicate_iri: str | None = None
    literal: LiteralValue | None = None
    assertion_status: AssertionStatus = "affirmed"
    condition_anchors: list[EvidenceAnchor] = Field(default_factory=list)
    condition_provenance_indexes: list[int] = Field(default_factory=list)
    applicable_at: str | None = None
    provenance: list[FactProvenance] = Field(min_length=1)
    bindings: list[BindingEvidence] = Field(default_factory=list)
    scope: EvidenceScope | None = None
    confidence: float = Field(default=0.0, ge=0, le=1)
    validation_status: Literal["pending", "passed", "rejected", "conflict"] = "pending"
    validation_issues: list[ValidationIssue] = Field(default_factory=list)
    review_status: Literal["pending", "confirmed", "rejected"] = "pending"
    review_source: Literal["automatic", "manual"] | None = None
    review_reason: str = ""
    commit_status: Literal[
        "not_requested", "queued", "applying", "succeeded", "failed",
    ] = "not_requested"
    task_id: str | None = None
    extractor_version: str = "generic-semantic-v1"
    ontology_release: str = ""
    model_identity: str = ""
    dependency_refs: list[CandidateRef] = Field(default_factory=list)
    path_root: CandidateRef | None = None
    relationship_path: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def coherent_assertion(self):
        if self.kind == "entity":
            if not self.class_iri or not self.text:
                raise ValueError("entity requires a class and a source label")
            if self.literal is not None or self.predicate_iri is not None:
                raise ValueError("entity cannot contain a flattened property assertion")
        else:
            if self.subject is None or not self.predicate_iri:
                raise ValueError("property/relationship requires an explicit subject and predicate")
            if self.kind == "property" and (self.literal is None or self.object is not None):
                raise ValueError("property requires one literal and no object endpoint")
            if self.kind == "relationship" and (self.object is None or self.literal is not None):
                raise ValueError("relationship requires an object endpoint and no literal")
            if self.validation_status == "passed" and not self.bindings:
                raise ValueError("validated assertion requires independent binding evidence")
        for source in self.provenance:
            if isinstance(source, DocumentProvenance):
                if source.document_role not in PRODUCTION_ROLES:
                    raise ValueError("sample/report/training evidence is not a production fact")
        for binding in self.bindings:
            if self.subject is None or binding.subject_candidate_id != self.subject.candidate_id:
                raise ValueError("binding belongs to a different subject")
            if binding.predicate_iri != self.predicate_iri:
                raise ValueError("binding supports a different predicate")
            expected_object = self.object.candidate_id if self.object else None
            if binding.object_candidate_id != expected_object:
                raise ValueError("binding belongs to a different object")
            if any(index >= len(self.provenance) for index in binding.provenance_indexes):
                raise ValueError("binding references missing provenance")
        if self.assertion_status == "conditional" and self.validation_status == "passed":
            if not self.condition_anchors and not self.condition_provenance_indexes:
                raise ValueError("conditional assertion requires evidence of its condition")
        if any(index < 0 or index >= len(self.provenance)
               for index in self.condition_provenance_indexes):
            raise ValueError("condition references missing provenance")
        return self

    @property
    def positive_eligible(self) -> bool:
        return (
            self.validation_status == "passed"
            and self.assertion_status == "affirmed"
            and not self.condition_anchors
            and not self.condition_provenance_indexes
            and (self.kind == "entity" or bool(self.bindings))
        )


class TaskBudget(EvidenceModel):
    max_input_tokens: int = Field(default=4096, ge=1)
    max_output_tokens: int = Field(default=2048, ge=1)
    max_tasks: int = Field(default=2048, ge=1)
    max_hops: int = Field(default=4, ge=1, le=16)
    max_gap_rounds: int = Field(default=2, ge=0, le=2)
    max_scope_expansions: int = Field(default=1, ge=0, le=1)
    max_regions_per_task: int = Field(default=1, ge=1, le=32)
    max_objects_per_task: int = Field(default=8, ge=1, le=128)
    timeout_s: float = Field(default=600.0, gt=0)
    timeout_retries: int = Field(default=3, ge=0)


class ExtractionTask(EvidenceModel):
    task_id: NonEmpty
    task_kind: Literal["entity", "property", "relationship"]
    subject: CandidateRef | None = None
    predicate_iri: str | None = None
    predicate_definition: dict[str, Any] = Field(default_factory=dict)
    target_class_iris: list[str] = Field(default_factory=list)
    target_evidence_ids: list[str] = Field(min_length=1)
    target_ranges: list[EvidenceRange] = Field(default_factory=list)
    object_candidates: list[CandidateRef] = Field(default_factory=list)
    competing_subjects: list[CandidateRef] = Field(default_factory=list)
    scope: EvidenceScope | None = None
    relationship_path: list[str] = Field(default_factory=list)
    path_root: CandidateRef | None = None
    dependency_refs: list[CandidateRef] = Field(default_factory=list)
    ontology_release: str = ""
    context_policy_version: str = "hierarchical-context-v1"
    semantic_spec_version: str = "semantic-spec-v1"
    budget: TaskBudget = Field(default_factory=TaskBudget)
    trigger: Literal["initial", "coverage_gap", "review_revision"] = "initial"

    @model_validator(mode="after")
    def subject_required(self):
        if self.task_kind != "entity" and (self.subject is None or not self.predicate_iri):
            raise ValueError(
                "property/relationship tasks require an explicit subject and predicate"
            )
        return self
