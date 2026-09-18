"""Strict, versioned contracts for ontology-guided document recognition.

The contracts keep source mentions, document-local referents, ontology
interpretations, and cross-document identity separate.  Model output is always
recorded as a proposal or decision and cannot become an accepted graph edge by
setting a single boolean flag.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_serializer, model_validator

from app.schemas.evidence import EvidenceAnchor, EvidenceModel, ExternalRecordProvenance
from app.schemas.retrieval_diagnostics import RetrievalDiagnosticCarrier
from app.services.extraction.evidence_identity import evidence_hash, stable_id
from app.services.extraction.ontology_guided.ontology_lexical import OntologyLexicalContext

CONTRACT_VERSION = "document-analysis-runs-v1"
ONTOLOGY_SNAPSHOT_VERSION = "ontology-guided-snapshot-v1"
ONTOLOGY_LEXICAL_SNAPSHOT_VERSION = "ontology-guided-snapshot-v2"
METADATA_POLICY_VERSION = "ontology-guided-metadata-v1"
RETRIEVAL_POLICY_VERSION = "ontology-guided-two-phase-v1"
SPARSE_RETRIEVAL_POLICY_VERSION = "ontology-guided-sparse-candidates-v1"
CANDIDATE_POLICY_VERSION = "sparse-candidates-v1"
PROOF_POLICY_VERSION = "ontology-guided-proof-v1"
PROJECTION_POLICY_VERSION = "ontology-guided-graph-v2"

SemanticVerdict = Literal["supported", "unsupported", "undetermined", "not_checked"]
AssertionPolarity = Literal["affirmed", "negated", "conditional", "uncertain"]
AssertionModality = Literal["asserted", "required", "possible", "planned", "unspecified"]
RelationSelection = Literal["all", "one_of", "alternatives", "undetermined"]
CoverageState = Literal["unattempted", "attempted_incomplete", "examined"]
ExecutionState = Literal[
    "queued", "running", "finished", "retryable_failure", "blocked_dependency", "paused"
]


class VersionedRef(EvidenceModel):
    id: str = Field(min_length=1)
    revision: int = Field(ge=1)


class ScopeMember(EvidenceModel):
    relation_ref: VersionedRef
    member_ref: VersionedRef


def _scope_member_key(member: ScopeMember) -> tuple[str, int, str, int]:
    return (
        member.relation_ref.id, member.relation_ref.revision,
        member.member_ref.id, member.member_ref.revision,
    )


class TraversalScope(EvidenceModel):
    scope_id: str = Field(min_length=64, max_length=64)
    members: list[ScopeMember] = Field(default_factory=list)

    @model_validator(mode="after")
    def canonical_members(self):
        members = sorted(self.members, key=_scope_member_key)
        if len({_scope_member_key(member) for member in members}) != len(members):
            raise ValueError("traversal scope cannot repeat a relation member")
        if self.scope_id != evidence_hash(members):
            raise ValueError("traversal scope identity must match its canonical members")
        # Bypass assignment validation only for this already-validated canonical order.
        object.__setattr__(self, "members", members)
        return self

    @classmethod
    def create(cls, members: list[ScopeMember] | None = None) -> "TraversalScope":
        ordered = sorted(members or [], key=_scope_member_key)
        return cls(scope_id=evidence_hash(ordered), members=ordered)


class SubjectRef(EvidenceModel):
    entity_id: str = Field(min_length=1)
    revision: int = Field(ge=1)
    class_iri: str = Field(min_length=1)
    referent_id: str | None = None
    is_document_root: bool = False


class SourceSpan(EvidenceModel):
    evidence_id: str = Field(min_length=1)
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    text: str = Field(min_length=1)

    @model_validator(mode="after")
    def valid_interval(self):
        if self.end <= self.start:
            raise ValueError("source span must be a non-empty half-open interval")
        if len(self.text) != self.end - self.start:
            raise ValueError("source span text length does not match its coordinates")
        return self


class SourceMention(EvidenceModel):
    mention_id: str = Field(min_length=1)
    analysis_id: str = Field(min_length=1)
    source_cell_id: str | None = None
    source_spans: list[SourceSpan] = Field(min_length=1)
    text: str = Field(min_length=1)
    record_view_refs: list[str] = Field(default_factory=list)


class LocalReferent(EvidenceModel):
    referent_id: str = Field(min_length=1)
    revision: int = Field(default=1, ge=1)
    mention_refs: list[str] = Field(default_factory=list)
    coreference_proof_refs: list[VersionedRef] = Field(default_factory=list)
    alternative_refs: list[str] = Field(default_factory=list)
    kind: Literal["mention", "record"] = "mention"
    record_view_refs: list[str] = Field(default_factory=list)
    composition_decision_ref: VersionedRef | None = None

    @model_validator(mode="after")
    def grounded_referent(self):
        if self.kind == "mention" and not self.mention_refs:
            raise ValueError("mention referent requires physical mention references")
        if self.kind == "record" and (
            not self.record_view_refs or self.composition_decision_ref is None
        ):
            raise ValueError("record referent requires record views and composition decision")
        return self

    @model_serializer(mode="wrap")
    def preserve_legacy_referent_shape(self, handler):
        data = handler(self)
        for name in ("kind", "record_view_refs", "composition_decision_ref"):
            if name not in self.model_fields_set:
                data.pop(name, None)
        return data


class EntityInterpretation(EvidenceModel):
    entity_id: str = Field(min_length=1)
    revision: int = Field(default=1, ge=1)
    referent_ref: VersionedRef
    class_iri: str = Field(min_length=1)
    type_decision_ref: VersionedRef | None = None
    qualifiers: dict[str, Any] = Field(default_factory=dict)
    level_evidence_refs: list[EvidenceAnchor] = Field(default_factory=list)
    identity_origin: Literal["document_local", "verified_external", "verified_key"] = (
        "document_local"
    )


class IdentityClaim(EvidenceModel):
    identity_claim_id: str = Field(min_length=1)
    entity_ref: VersionedRef
    key_predicate: str = Field(min_length=1)
    value_span: SourceSpan
    field_role_refs: list[VersionedRef] = Field(default_factory=list)
    identified_role: str | None = None
    namespace: str | None = None
    uniqueness_scope: str | None = None
    qualifiers: dict[str, Any] = Field(default_factory=dict)
    decision_ref: VersionedRef | None = None


class PredicateSpec(EvidenceModel):
    iri: str = Field(min_length=1)
    label: str = Field(min_length=1)
    description: str = ""
    declared_by: list[str] = Field(default_factory=list)
    multiplicity: Literal["unspecified", "single", "multiple"] = "unspecified"
    min_count: int | None = Field(default=None, ge=0)
    max_count: int | None = Field(default=None, ge=0)
    constraint_status: Literal["resolved", "constraint_unresolved"] = "resolved"

    @model_serializer(mode="wrap")
    def preserve_legacy_quantity_shape(self, handler):
        data = handler(self)
        # Restore old artifacts without inserting a new declaration into their
        # recorded payload/hash. New engine snapshots explicitly set both fields.
        for name in ("multiplicity", "min_count"):
            if name not in self.model_fields_set:
                data.pop(name, None)
        return data


class SlotSpec(PredicateSpec):
    kind: Literal["property"] = "property"
    datatype_iris: list[str] = Field(default_factory=list)
    canonical_unit: str | None = None
    identity_key: bool = False


class RangeClass(EvidenceModel):
    iri: str = Field(min_length=1)
    label: str = Field(min_length=1)
    description: str = ""
    parent_iris: list[str] = Field(default_factory=list)
    identity_property_iris: list[str] = Field(default_factory=list)
    direct_field_labels: list[str] = Field(default_factory=list)


class EdgeSpec(PredicateSpec):
    kind: Literal["relationship"] = "relationship"
    range_class_iris: list[str] = Field(min_length=1)
    range_classes: list[RangeClass] = Field(default_factory=list)


class OntologyClassDefinition(EvidenceModel):
    iri: str = Field(min_length=1)
    label: str = Field(min_length=1)
    description: str = ""
    parent_iris: list[str] = Field(default_factory=list)
    declared_properties: list[SlotSpec] = Field(default_factory=list)
    declared_relationships: list[EdgeSpec] = Field(default_factory=list)
    source_hash: str = Field(min_length=1)


class OntologySnapshot(EvidenceModel):
    snapshot_id: str = Field(min_length=1)
    version: str = ONTOLOGY_SNAPSHOT_VERSION
    ontology_hash: str = Field(min_length=1)
    classes: dict[str, OntologyClassDefinition]
    created_from: Literal["ontology_engine", "frozen_fixture"] = "ontology_engine"
    diagnostics: list[str] = Field(default_factory=list)
    lexical_context: OntologyLexicalContext | None = None

    @model_serializer(mode="wrap")
    def preserve_legacy_lexical_shape(self, handler):
        data = handler(self)
        if self.lexical_context is None:
            data.pop("lexical_context", None)
        return data

    @model_validator(mode="after")
    def validate_lexical_identity(self):
        if self.version == ONTOLOGY_LEXICAL_SNAPSHOT_VERSION and self.lexical_context is None:
            raise ValueError("ontology snapshot v2 requires its frozen lexical context")
        if self.lexical_context is not None:
            if self.version != ONTOLOGY_LEXICAL_SNAPSHOT_VERSION:
                raise ValueError("ontology lexical context requires snapshot v2")
            expected = evidence_hash({
                "classes": self.classes, "lexical_context": self.lexical_context,
            })
            if self.ontology_hash != expected:
                raise ValueError("ontology snapshot hash does not match its lexical content")
        return self


class LocalMenu(EvidenceModel):
    menu_id: str = Field(min_length=1)
    ontology_snapshot_id: str = Field(min_length=1)
    subject: SubjectRef
    properties: list[SlotSpec] = Field(default_factory=list)
    relationships: list[EdgeSpec] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)
    policy_version: str = "ontology-guided-local-menu-v2"

    @model_validator(mode="after")
    def unique_predicates(self):
        keys = [(item.kind, item.iri) for item in [*self.properties, *self.relationships]]
        if len(keys) != len(set(keys)):
            raise ValueError("local menu contains duplicate predicate declarations")
        return self


class MetadataNode(EvidenceModel):
    node_id: str = Field(min_length=1)
    heading: str = ""
    path: list[str] = Field(default_factory=list)
    summary: str | None = None
    summary_status: str = "pending"
    summary_source: str = "none"
    source_record_refs: list[str] = Field(default_factory=list)


class MetadataSnapshot(EvidenceModel):
    snapshot_id: str = Field(min_length=1)
    analysis_id: str = Field(min_length=1)
    document_hash: str = Field(min_length=64, max_length=64)
    structure_hash: str = Field(min_length=64, max_length=64)
    summary_version: str = Field(min_length=1)
    summary_model_identity: str | None = None
    generation_source: Literal["model_summary", "extractive_fallback", "structure_only"]
    source_record_refs: list[str] = Field(default_factory=list)
    node_summaries: list[MetadataNode] = Field(default_factory=list)
    dependency_hash: str = Field(min_length=64, max_length=64)
    policy_version: str = METADATA_POLICY_VERSION


class RetrievalHint(EvidenceModel):
    hint_id: str = Field(min_length=1)
    metadata_snapshot_id: str = Field(min_length=1)
    node_id: str | None = None
    record_ids: list[str] = Field(default_factory=list)
    subject_type_iri: str = Field(min_length=1)
    predicate_iri: str = Field(min_length=1)
    object_type_iris: list[str] = Field(default_factory=list)
    hint_kind: Literal["relation_target", "entity_role", "property_value"]
    support_origin: Literal["heading", "summary", "source_label"]
    source_record_refs: list[str] = Field(default_factory=list)
    score_components: dict[str, float] = Field(default_factory=dict)
    policy_version: str = RETRIEVAL_POLICY_VERSION
    authority: Literal["retrieval_only"] = "retrieval_only"


class FieldGroup(EvidenceModel):
    field_group_id: str = Field(min_length=1)
    section_node_id: str = Field(min_length=1)
    record_ids: list[str] = Field(min_length=1)
    label_refs: list[EvidenceAnchor] = Field(default_factory=list)
    value_refs: list[EvidenceAnchor] = Field(default_factory=list)
    owner_hypothesis_refs: list[str] = Field(default_factory=list)


class RecordView(EvidenceModel):
    record_view_id: str = Field(min_length=1)
    record_id: str = Field(min_length=1)
    source_record_kind: Literal["paragraph", "table_row"]
    section_node_id: str = Field(min_length=1)
    source_refs: list[EvidenceAnchor] = Field(min_length=1)
    header_refs: list[EvidenceAnchor] = Field(default_factory=list)
    note_refs: list[EvidenceAnchor] = Field(default_factory=list)
    parent_context_refs: list[EvidenceAnchor] = Field(default_factory=list)
    table_path: list[str] | None = None
    logical_row_id: str | None = None
    source_cell_ids: list[str] = Field(default_factory=list)


class PlannedRecord(EvidenceModel):
    record_id: str = Field(min_length=1)
    phase: Literal[1, 2]
    section_node_id: str = Field(min_length=1)
    record_kind: Literal["paragraph", "table_row"]
    rank_score: float = 0
    score_components: dict[str, float] = Field(default_factory=dict)
    rationale: list[str] = Field(default_factory=list)
    ranking_epoch_id: str | None = None
    ranking_epoch_seq: int | None = Field(default=None, ge=1)
    pool_rank: int | None = Field(default=None, ge=1)
    ranking_mode: Literal["deterministic", "semantic"] = "deterministic"


class RecallEntry(EvidenceModel):
    record_id: str = Field(min_length=1)
    phase: Literal[1, 2]
    coverage_state: CoverageState = "unattempted"
    execution_state: ExecutionState = "queued"
    semantic_outcomes: list[SemanticVerdict] = Field(default_factory=list)
    task_ids: list[str] = Field(default_factory=list)
    call_ids: list[str] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)


class RetrievalPlan(EvidenceModel):
    plan_id: str = Field(min_length=1)
    subject: SubjectRef
    predicate_iri: str = Field(min_length=1)
    predicate_kind: Literal["property", "relationship"]
    metadata_snapshot_id: str = Field(min_length=1)
    ontology_hash: str = Field(min_length=1)
    phase1_section_limit: int = Field(default=3, ge=1)
    records: list[PlannedRecord]
    ledger: dict[str, RecallEntry]
    excluded_heading_record_ids: list[str] = Field(default_factory=list)
    policy_version: str = RETRIEVAL_POLICY_VERSION
    frozen_record_ids: list[str] = Field(default_factory=list)
    frozen_record_hash: str = ""
    ranking_epoch_ids: list[str] = Field(default_factory=list)
    # The document owns the record universe once. Sparse plans reference that
    # immutable search domain; only admitted recognition work appears below.
    search_scope_ref: dict[str, Any] | None = None

    @model_serializer(mode="wrap")
    def serialize_plan(self, handler):
        result = handler(self)
        if result.get("search_scope_ref") is None:
            result.pop("search_scope_ref", None)
        return result

    @model_validator(mode="after")
    def conserved_records(self):
        ids = [item.record_id for item in self.records]
        if len(ids) != len(set(ids)):
            raise ValueError("retrieval phases must be disjoint")
        if set(ids) != set(self.ledger):
            raise ValueError("retrieval ledger must cover every planned record exactly once")
        if self.policy_version == SPARSE_RETRIEVAL_POLICY_VERSION:
            scope = self.search_scope_ref
            if (not isinstance(scope, dict)
                    or set(scope) != {"scope_id", "record_count", "record_hash"}
                    or not isinstance(scope["scope_id"], str) or not scope["scope_id"]
                    or type(scope["record_count"]) is not int or scope["record_count"] < len(ids)
                    or not isinstance(scope["record_hash"], str)
                    or len(scope["record_hash"]) != 64
                    or self.frozen_record_ids or self.frozen_record_hash):
                raise ValueError("sparse retrieval requires one shared record universe reference")
        elif self.search_scope_ref is not None:
            raise ValueError("shared search scope requires the sparse retrieval policy")
        elif self.frozen_record_hash:
            from app.services.extraction.evidence_identity import evidence_hash

            if (
                len(self.frozen_record_ids) != len(set(self.frozen_record_ids))
                or set(ids) != set(self.frozen_record_ids)
                or evidence_hash(self.frozen_record_ids) != self.frozen_record_hash
            ):
                raise ValueError("retrieval plan must conserve its frozen record universe")
        return self


class DocumentContext(EvidenceModel):
    document_hash: str = Field(min_length=64, max_length=64)
    document_class_iri: str = Field(min_length=1)
    root_ref: VersionedRef


class VerificationTarget(EvidenceModel):
    target_id: str = Field(min_length=1)
    claim_ref: VersionedRef
    task_id: str = Field(min_length=1)
    check_kind: Literal[
        "type",
        "field_role",
        "local_coreference",
        "global_identity",
        "predicate_entailment",
        "applicability",
        "selection",
        "record_composition",
        "modality",
    ]
    document_context: DocumentContext
    subject_ref: SubjectRef
    predicate_spec_ref: VersionedRef | None = None
    predicate_iri: str | None = None
    object_ref: VersionedRef | None = None
    literal_hash: str | None = None
    assertion_polarity: AssertionPolarity = "affirmed"
    applicability: dict[str, Any] = Field(default_factory=dict)
    ontology_hash: str = Field(min_length=1)
    source_scope_hash: str = Field(min_length=1)
    context_hash: str = Field(min_length=1)
    object_refs: list[VersionedRef] = Field(default_factory=list)
    selection: RelationSelection | None = None

    @model_validator(mode="after")
    def coherent_group_target(self):
        if self.object_refs:
            if self.object_ref is not None or len(self.object_refs) < 2:
                raise ValueError("group target requires multiple objects and no single object")
            if len({ref.id for ref in self.object_refs}) != len(self.object_refs):
                raise ValueError("group target objects must identify distinct entities")
            if self.selection is None:
                raise ValueError("group target requires selection semantics")
        elif self.selection is not None or self.check_kind == "selection":
            raise ValueError("selection verification requires a group target")
        return self

    @model_serializer(mode="wrap")
    def preserve_legacy_target_shape(self, handler):
        data = handler(self)
        for name in ("object_refs", "selection"):
            if name not in self.model_fields_set:
                data.pop(name, None)
        return data

    @classmethod
    def create(cls, **values: Any) -> "VerificationTarget":
        identity = {
            "run_fingerprint": values.pop("run_fingerprint"),
            "claim_ref": values["claim_ref"],
            "check_kind": values["check_kind"],
            "subject_ref": values["subject_ref"],
            "predicate_iri": values.get("predicate_iri"),
            "object_ref": values.get("object_ref"),
            "literal_hash": values.get("literal_hash"),
            "assertion_polarity": values.get("assertion_polarity", "affirmed"),
            "applicability": values.get("applicability", {}),
            "source_scope_hash": values["source_scope_hash"],
            "context_hash": values["context_hash"],
        }
        # Old targets retain exactly their frozen identity; new group inputs
        # explicitly bind both the full endpoint set and its selection meaning.
        for name in ("object_refs", "selection"):
            if name in values:
                identity[name] = values[name]
        return cls(target_id=stable_id("verification-target", identity), **values)


class SemanticDecision(EvidenceModel):
    decision_id: str = Field(min_length=1)
    target_id: str = Field(min_length=1)
    check_kind: str = Field(min_length=1)
    verdict: SemanticVerdict
    reason_code: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    reason_status: Literal["provided", "missing", "programmatic"] = "provided"
    support_refs: list[EvidenceAnchor] = Field(default_factory=list)
    counterevidence_refs: list[EvidenceAnchor] = Field(default_factory=list)
    searched_context_refs: list[EvidenceAnchor] = Field(default_factory=list)
    verifier_version: str = Field(min_length=1)
    model_identity: str | None = None
    attempt_id: str = Field(min_length=1)
    model_refusal: bool = False

    @model_validator(mode="after")
    def coherent_reason(self):
        if self.model_refusal and self.verdict != "undetermined":
            raise ValueError("model refusal must remain semantically undetermined")
        if self.verdict == "supported" and not self.support_refs:
            raise ValueError("supported decision requires replayable support")
        return self


class FieldRoleClaim(EvidenceModel):
    role_claim_id: str = Field(min_length=1)
    span_refs: list[EvidenceAnchor] = Field(min_length=1)
    header_refs: list[EvidenceAnchor] = Field(default_factory=list)
    record_view_ref: str = Field(min_length=1)
    owner_ref: VersionedRef | None = None
    owner_hypothesis: str | None = None
    role_kind: str = Field(min_length=1)
    ontology_slot_ref: VersionedRef | None = None
    verdict: SemanticVerdict = "not_checked"


class ValueObservation(EvidenceModel):
    observation_id: str = Field(min_length=1)
    slot_ref: VersionedRef
    raw_text: str
    source_refs: list[EvidenceAnchor] = Field(default_factory=list)
    presence: Literal["present", "placeholder", "not_mentioned"]
    interpretation: Literal["value", "unknown", "not_applicable", "unspecified"]
    normalized_value: Any = None
    normalizer_version: str = "ontology-guided-value-v1"


BridgeKind = Literal[
    "explicit_assertion",
    "owned_field_group",
    "role_mapped_table",
    "resolved_reference_chain",
    "document_subject_description",
    "same_document",
    "same_name",
    "adjacent_only",
    "path_reachability",
]


class BridgeStep(EvidenceModel):
    step_kind: Literal["same_referent", "predicate_assertion", "role_assignment"]
    input_refs: list[VersionedRef] = Field(default_factory=list)
    output_role: str = Field(min_length=1)
    source_refs: list[EvidenceAnchor] = Field(min_length=1)
    decision_ref: VersionedRef


class PredicateEvidence(EvidenceModel):
    proof_id: str = Field(min_length=1)
    proof_revision: int = Field(default=1, ge=1)
    target_id: str = Field(min_length=1)
    predicate_iri: str = Field(min_length=1)
    subject_role_refs: list[VersionedRef] = Field(min_length=1)
    object_role_refs: list[VersionedRef] = Field(default_factory=list)
    value_role_refs: list[VersionedRef] = Field(default_factory=list)
    predicate_support_refs: list[EvidenceAnchor] = Field(default_factory=list)
    bridge_kind: BridgeKind
    bridge_steps: list[BridgeStep] = Field(default_factory=list)
    competing_owner_refs: list[VersionedRef] = Field(default_factory=list)
    counterevidence_refs: list[EvidenceAnchor] = Field(default_factory=list)
    applicability_refs: list[VersionedRef] = Field(default_factory=list)
    dependency_refs: list[VersionedRef] = Field(default_factory=list)
    verdict: SemanticVerdict
    unit_evidence_refs: list[EvidenceAnchor] = Field(default_factory=list)
    normalization_record: dict[str, Any] = Field(default_factory=dict)
    proof_policy_version: str = PROOF_POLICY_VERSION
    selection_support_refs: list[EvidenceAnchor] = Field(default_factory=list)
    modality_support_refs: list[EvidenceAnchor] = Field(default_factory=list)

    @model_serializer(mode="wrap")
    def preserve_legacy_proof_shape(self, handler):
        data = handler(self)
        for name in ("selection_support_refs", "modality_support_refs"):
            if name not in self.model_fields_set:
                data.pop(name, None)
        return data


class VerificationBundle(EvidenceModel):
    bundle_id: str = Field(min_length=1)
    target: VerificationTarget
    decisions: list[SemanticDecision] = Field(default_factory=list)
    predicate_evidence: PredicateEvidence | None = None
    structural_valid: bool = False
    model_supported: bool = False
    policy_eligible: bool = False
    independent_review: Literal["unreviewed", "accepted", "rejected"] = "unreviewed"
    validation_issues: list[str] = Field(default_factory=list)


class GraphNode(EvidenceModel):
    entity_id: str = Field(min_length=1)
    revision: int = Field(ge=1)
    class_iri: str = Field(min_length=1)
    class_label: str = Field(min_length=1)
    label: str = Field(min_length=1)
    root: bool = False
    root_origin: Literal["user_specified", "recognized"] = "recognized"
    identity_status: Literal["document_local", "verified", "undetermined"] = "document_local"
    external_provenance: list[ExternalRecordProvenance] = Field(default_factory=list)
    identity_decision_refs: list[VersionedRef] = Field(default_factory=list)
    decision_status: SemanticVerdict = "supported"
    independent_review: Literal["unreviewed", "accepted", "rejected"] = "unreviewed"
    evidence_refs: list[EvidenceAnchor] = Field(default_factory=list)
    referent_ref: VersionedRef | None = None
    type_decision_ref: VersionedRef | None = None
    referent_decision_ref: VersionedRef | None = None
    dependency_refs: list[VersionedRef] = Field(default_factory=list)
    grounding_kind: Literal["document_root", "mention", "record"] = "mention"
    composition_decision_ref: VersionedRef | None = None

    @model_validator(mode="after")
    def coherent_grounding(self):
        if "grounding_kind" not in self.model_fields_set:
            return self
        if self.grounding_kind == "document_root":
            if not self.root:
                raise ValueError("document root grounding requires a root node")
        elif self.root or self.referent_ref is None:
            raise ValueError("mention or record grounding requires a non-root referent")
        if self.grounding_kind == "record" and self.composition_decision_ref is None:
            raise ValueError("record node requires its composition decision")
        return self

    @model_serializer(mode="wrap")
    def preserve_legacy_node_shape(self, handler):
        data = handler(self)
        for name in (
            "referent_ref", "type_decision_ref", "referent_decision_ref", "dependency_refs",
            "grounding_kind", "composition_decision_ref",
            "external_provenance", "identity_decision_refs",
        ):
            if name not in self.model_fields_set:
                data.pop(name, None)
        return data


class GraphProperty(EvidenceModel):
    candidate_id: str = Field(min_length=1)
    revision: int = Field(ge=1)
    subject_ref: VersionedRef
    predicate_iri: str = Field(min_length=1)
    predicate_label: str = Field(min_length=1)
    raw_value: str
    normalized_value: Any = None
    normalization_record: dict[str, Any] = Field(default_factory=dict)
    unit_evidence_refs: list[EvidenceAnchor] = Field(default_factory=list)
    direction: Literal["outbound"] = "outbound"
    polarity: AssertionPolarity = "affirmed"
    conditions: list[str] = Field(default_factory=list)
    applicability: dict[str, Any] = Field(default_factory=dict)
    decision_status: SemanticVerdict
    structural_valid: bool = False
    model_supported: bool = False
    policy_eligible: bool = False
    independent_review: Literal["unreviewed", "accepted", "rejected"] = "unreviewed"
    proof_ref: VersionedRef | None = None
    decision_refs: list[VersionedRef] = Field(default_factory=list)
    dependency_refs: list[VersionedRef] = Field(default_factory=list)
    evidence_refs: list[EvidenceAnchor] = Field(default_factory=list)
    subject_evidence_refs: list[EvidenceAnchor] = Field(default_factory=list)
    value_evidence_refs: list[EvidenceAnchor] = Field(default_factory=list)
    predicate_evidence_refs: list[EvidenceAnchor] = Field(default_factory=list)
    condition_evidence_refs: list[EvidenceAnchor] = Field(default_factory=list)
    counterevidence_refs: list[EvidenceAnchor] = Field(default_factory=list)
    reason_code: str = ""
    reason: str = ""
    modality: AssertionModality = "unspecified"
    scope: TraversalScope = Field(default_factory=TraversalScope.create)

    @model_serializer(mode="wrap")
    def preserve_legacy_property_shape(self, handler):
        data = handler(self)
        for name in ("modality", "scope"):
            if name not in self.model_fields_set:
                data.pop(name, None)
        return data


class GraphEdge(EvidenceModel):
    candidate_id: str = Field(min_length=1)
    revision: int = Field(ge=1)
    subject_ref: VersionedRef
    object_ref: VersionedRef
    predicate_iri: str = Field(min_length=1)
    predicate_label: str = Field(min_length=1)
    direction: Literal["outbound", "inbound"] = "outbound"
    polarity: AssertionPolarity = "affirmed"
    conditions: list[str] = Field(default_factory=list)
    applicability: dict[str, Any] = Field(default_factory=dict)
    decision_status: SemanticVerdict
    structural_valid: bool = False
    model_supported: bool = False
    policy_eligible: bool = False
    independent_review: Literal["unreviewed", "accepted", "rejected"] = "unreviewed"
    proof_ref: VersionedRef | None = None
    decision_refs: list[VersionedRef] = Field(default_factory=list)
    dependency_refs: list[VersionedRef] = Field(default_factory=list)
    evidence_refs: list[EvidenceAnchor] = Field(default_factory=list)
    subject_evidence_refs: list[EvidenceAnchor] = Field(default_factory=list)
    object_evidence_refs: list[EvidenceAnchor] = Field(default_factory=list)
    predicate_evidence_refs: list[EvidenceAnchor] = Field(default_factory=list)
    condition_evidence_refs: list[EvidenceAnchor] = Field(default_factory=list)
    counterevidence_refs: list[EvidenceAnchor] = Field(default_factory=list)
    reason_code: str = ""
    reason: str = ""
    modality: AssertionModality = "unspecified"
    scope: TraversalScope = Field(default_factory=TraversalScope.create)

    @model_serializer(mode="wrap")
    def preserve_legacy_edge_shape(self, handler):
        data = handler(self)
        for name in ("modality", "scope"):
            if name not in self.model_fields_set:
                data.pop(name, None)
        return data


class GraphRelationshipGroup(EvidenceModel):
    candidate_id: str = Field(min_length=1)
    revision: int = Field(ge=1)
    subject_ref: VersionedRef
    object_refs: list[VersionedRef] = Field(min_length=2)
    selection: RelationSelection
    selection_evidence_refs: list[EvidenceAnchor] = Field(default_factory=list)
    predicate_iri: str = Field(min_length=1)
    predicate_label: str = Field(min_length=1)
    direction: Literal["outbound", "inbound"] = "outbound"
    polarity: AssertionPolarity = "affirmed"
    modality: AssertionModality = "unspecified"
    scope: TraversalScope = Field(default_factory=TraversalScope.create)
    conditions: list[str] = Field(default_factory=list)
    applicability: dict[str, Any] = Field(default_factory=dict)
    decision_status: SemanticVerdict
    structural_valid: bool = False
    model_supported: bool = False
    policy_eligible: bool = False
    independent_review: Literal["unreviewed", "accepted", "rejected"] = "unreviewed"
    proof_ref: VersionedRef | None = None
    decision_refs: list[VersionedRef] = Field(default_factory=list)
    dependency_refs: list[VersionedRef] = Field(default_factory=list)
    evidence_refs: list[EvidenceAnchor] = Field(default_factory=list)
    subject_evidence_refs: list[EvidenceAnchor] = Field(default_factory=list)
    object_evidence_refs: list[EvidenceAnchor] = Field(default_factory=list)
    predicate_evidence_refs: list[EvidenceAnchor] = Field(default_factory=list)
    condition_evidence_refs: list[EvidenceAnchor] = Field(default_factory=list)
    counterevidence_refs: list[EvidenceAnchor] = Field(default_factory=list)
    reason_code: str = ""
    reason: str = ""

    @model_validator(mode="after")
    def coherent_selection(self):
        if len({ref.id for ref in self.object_refs}) != len(self.object_refs):
            raise ValueError("relationship group objects must identify distinct entities")
        supported = (
            self.decision_status == "supported" or self.model_supported or self.policy_eligible
        )
        if supported and (self.selection == "undetermined" or not self.selection_evidence_refs):
            raise ValueError("supported relationship group requires proven selection semantics")
        return self


class CoverageSummary(RetrievalDiagnosticCarrier):
    subject_ref: VersionedRef
    predicate_iri: str = Field(min_length=1)
    predicate_label: str = Field(min_length=1)
    phase1: int = Field(default=0, ge=0)
    phase2: int = Field(default=0, ge=0)
    examined: int = Field(default=0, ge=0)
    incomplete: int = Field(default=0, ge=0)
    unattempted: int = Field(default=0, ge=0)
    unresolved_claims: int = Field(default=0, ge=0)
    executed_phase_counts: dict[str, int] = Field(
        default_factory=lambda: {"phase1": 0, "phase2": 0}
    )
    pending_frontiers: int = Field(default=0, ge=0)
    stop_reason: str | None = None
    scope: TraversalScope | None = None

    @model_serializer(mode="wrap")
    def preserve_legacy_scope(self, handler):
        data = handler(self)
        if self.scope is None:
            data.pop("scope", None)
        return data


class RunProgress(RetrievalDiagnosticCarrier):
    tasks_attempted: int = Field(default=0, ge=0)
    model_calls: int = Field(default=0, ge=0)
    model_calls_reserved: int = Field(default=0, ge=0)
    model_calls_unresolved: int = Field(default=0, ge=0)
    records_planned: int = Field(default=0, ge=0)
    records_examined: int = Field(default=0, ge=0)
    records_incomplete: int = Field(default=0, ge=0)
    records_unattempted: int = Field(default=0, ge=0)
    phase_counts: dict[str, int] = Field(default_factory=lambda: {"phase1": 0, "phase2": 0})
    supported: int = Field(default=0, ge=0)
    unsupported: int = Field(default=0, ge=0)
    undetermined: int = Field(default=0, ge=0)
    pending_frontiers: int = Field(default=0, ge=0)
    unresolved_claims: int = Field(default=0, ge=0)
    invalidated_count: int = Field(default=0, ge=0)
    stop_reason: str | None = None
    completion: Literal["incomplete", "in_scope_complete", "policy_complete"] = "incomplete"
    contract_version: str = CONTRACT_VERSION

    @model_validator(mode="after")
    def coverage_conservation(self):
        if self.records_planned != (
            self.records_examined + self.records_incomplete + self.records_unattempted
        ):
            raise ValueError("planned records must equal examined + incomplete + unattempted")
        if self.completion in {"in_scope_complete", "policy_complete"} and (
            self.records_incomplete or self.records_unattempted or self.pending_frontiers
        ):
            raise ValueError("in-scope completion cannot hide unfinished coverage")
        if self.completion == "policy_complete" and (
            self.candidate_policy != CANDIDATE_POLICY_VERSION or self.model_calls_unresolved
        ):
            raise ValueError("candidate policy completion cannot hide unresolved work")
        return self


class GraphSnapshot(EvidenceModel):
    contract_version: str = CONTRACT_VERSION
    recognition_run_id: str = Field(min_length=1)
    run_revision: int = Field(ge=0)
    event_head: int = Field(ge=0)
    metadata_snapshot_id: str | None = None
    root_ref: VersionedRef
    projection: Literal[
        "effective", "all", "unassociated", "negated", "conditional", "undetermined", "rejected",
        "verified",
    ] = "effective"
    projection_policy: str = PROJECTION_POLICY_VERSION
    artifact_status: Literal["pending", "building", "ready", "partial", "failed"]
    nodes: list[GraphNode]
    edges: list[GraphEdge] = Field(default_factory=list)
    properties: list[GraphProperty] = Field(default_factory=list)
    coverage: list[CoverageSummary] = Field(default_factory=list)
    progress: RunProgress = Field(default_factory=RunProgress)
    generated_from_hash: str = Field(min_length=64, max_length=64)
    relationship_groups: list[GraphRelationshipGroup] = Field(default_factory=list)

    @model_serializer(mode="wrap")
    def preserve_legacy_snapshot_shape(self, handler):
        data = handler(self)
        if "relationship_groups" not in self.model_fields_set:
            data.pop("relationship_groups", None)
        return data

    @classmethod
    def empty_root(
        cls,
        *,
        recognition_run_id: str,
        root: GraphNode,
        artifact_status: Literal["pending", "building", "ready", "partial", "failed"] = ("pending"),
        metadata_snapshot_id: str | None = None,
        event_head: int = 0,
        run_revision: int = 0,
        progress: RunProgress | None = None,
    ) -> "GraphSnapshot":
        payload = {
            "recognition_run_id": recognition_run_id,
            "event_head": event_head,
            "root": root.model_dump(mode="json"),
            "metadata_snapshot_id": metadata_snapshot_id,
        }
        return cls(
            recognition_run_id=recognition_run_id,
            run_revision=run_revision,
            event_head=event_head,
            metadata_snapshot_id=metadata_snapshot_id,
            root_ref=VersionedRef(id=root.entity_id, revision=root.revision),
            artifact_status=artifact_status,
            nodes=[root],
            progress=progress or RunProgress(),
            generated_from_hash=evidence_hash(payload),
        )
