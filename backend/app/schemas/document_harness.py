"""Independent public contracts for source-first document harness results."""

from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.evidence import LiteralValue

NonEmpty = Annotated[str, Field(min_length=1)]
Count = Annotated[int, Field(ge=0)]
Iri = Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9+.-]*:[^\s]+$")]
CandidateState = Literal["candidate", "accepted", "rejected", "unresolved"]
Stage = Literal[
    "ingest",
    "parse",
    "discover",
    "type_alignment",
    "referent_alignment",
    "referent_candidates",
    "referent_selection",
    "entity_review",
    "planning",
    "property_alignment",
    "relation_alignment",
    "group_interpretation",
    "evidence_review",
    "coreference_review",
    "identifier_check",
    "literal_normalization",
    "shacl_check",
    "complete",
]
Phase = Literal["discovery", "skeleton", "semantic", "deterministic", "done"]
WorkStatus = Literal["ready", "waiting", "pruned", "done", "failed"]


class HarnessModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HarnessSourceRef(HarnessModel):
    source_id: NonEmpty
    text: NonEmpty
    start: Count
    end: Count
    page: int | None
    section_id: NonEmpty
    block_id: NonEmpty

    @model_validator(mode="after")
    def exact_span_length(self):
        if self.end <= self.start or self.end - self.start != len(self.text):
            raise ValueError("source_span_length_mismatch")
        return self


class HarnessCoreference(HarnessModel):
    id: NonEmpty
    left_mention_id: NonEmpty
    right_mention_id: NonEmpty
    verdict: Literal["same", "different", "unresolved"]
    basis: Literal[
        "explicit_alias", "scoped_identifier", "explicit_reference", "distinct", "insufficient",
    ]
    reason: NonEmpty
    evidence: list[HarnessSourceRef]
    proof: list[HarnessSourceRef]
    applied: bool


class HarnessCardRef(HarnessModel):
    iri: Iri
    label: NonEmpty


class HarnessDiscoveryCard(HarnessCardRef):
    role: Literal["reading", "document_properties"]


class HarnessPredicateRef(HarnessCardRef):
    namespace: str
    domain_text: str


class HarnessVerification(HarnessModel):
    method: Literal["rule", "llm"] | None
    rule_id: NonEmpty | None
    rule_version: NonEmpty | None
    semantic_verdict: Literal["accepted", "rejected", "unresolved"] | None


class HarnessCheckResult(HarnessModel):
    status: Literal["not_run", "passed", "invalid", "incomplete", "not_applicable", "error"]
    reason_code: str | None
    details: dict[str, Any]


class HarnessChecks(HarnessModel):
    identifier: HarnessCheckResult
    datatype: HarnessCheckResult
    unit: HarnessCheckResult
    shacl: HarnessCheckResult


class HarnessCalibration(HarnessModel):
    input_hash: NonEmpty
    checks: HarnessChecks
    literal: LiteralValue | None


class HarnessMention(HarnessModel):
    id: NonEmpty
    label: NonEmpty
    role: NonEmpty
    class_iri: Iri | None
    class_label: str | None
    state: CandidateState
    reason: NonEmpty
    evidence: list[HarnessSourceRef]
    parent_mention_id: str | None
    refined_member_ids: list[str]
    verification: HarnessVerification
    calibration: HarnessCalibration | None


class HarnessEntity(HarnessMention):
    mentions: list[HarnessMention]


class HarnessProperty(HarnessModel):
    id: NonEmpty
    field_id: str | None
    subject_id: NonEmpty
    subject_mention_id: NonEmpty
    card: HarnessCardRef | None
    predicate: HarnessPredicateRef | None
    predicate_iri: Iri | None
    label: NonEmpty
    value: Any
    source_value: str
    source_unit: str | None
    source_unit_evidence: list[HarnessSourceRef]
    value_component: Literal["whole", "span", "lower", "upper"]
    value_evidence: list[HarnessSourceRef]
    state: CandidateState
    reason: NonEmpty
    evidence: list[HarnessSourceRef]
    verification: HarnessVerification
    calibration: HarnessCalibration | None


class HarnessRelation(HarnessModel):
    id: NonEmpty
    card: HarnessCardRef | None
    predicate: HarnessPredicateRef | None
    subject_id: NonEmpty
    object_id: NonEmpty
    subject_mention_id: NonEmpty
    object_mention_id: NonEmpty
    predicate_iri: Iri | None
    label: NonEmpty
    state: CandidateState
    reason: NonEmpty
    evidence: list[HarnessSourceRef]
    polarity: Literal["positive", "negative", "uncertain"]
    conditions: list[str]
    verification: HarnessVerification
    calibration: HarnessCalibration | None


class HarnessRelationGroup(HarnessModel):
    id: NonEmpty
    card: HarnessCardRef | None
    predicate: HarnessPredicateRef | None
    subject_id: NonEmpty
    subject_mention_id: NonEmpty
    object_ids: list[NonEmpty]
    object_mention_ids: list[NonEmpty]
    predicate_iri: Iri | None
    label: NonEmpty
    state: CandidateState
    reason: NonEmpty
    evidence: list[HarnessSourceRef]
    polarity: Literal["positive", "negative", "uncertain"]
    conditions: list[str]
    participation: Literal["options", "all", "unknown"]
    selection: Literal["exactly_one", "unspecified"]
    timing: Literal["parallel", "sequential", "unspecified"]
    timing_state: CandidateState
    timing_reason: NonEmpty
    ordered_object_ids: list[NonEmpty] | None
    order_evidence: list[HarnessSourceRef]
    verification: HarnessVerification
    calibration: HarnessCalibration | None


class HarnessAlignmentAttempt(HarnessModel):
    state: Literal["mapped", "unmatched", "invalid"]
    reason: NonEmpty
    predicates: list[HarnessPredicateRef]


class HarnessObservationAlignment(HarnessModel):
    subject_id: NonEmpty
    card: HarnessCardRef | None
    property_ids: list[str]
    attempts: list[HarnessAlignmentAttempt]


class HarnessObservation(HarnessModel):
    id: NonEmpty
    kind: Literal["field", "entity", "relation", "scope", "validation", "failure"]
    label: NonEmpty
    reason: NonEmpty
    evidence: list[HarnessSourceRef]
    field_id: str | None
    value: str | None
    candidate_subject_ids: list[str]
    object_id: str | None
    discovery_cards: list[HarnessDiscoveryCard]
    alignments: list[HarnessObservationAlignment]


class HarnessTarget(HarnessModel):
    id: NonEmpty
    subject_id: NonEmpty
    predicate_iri: Iri
    label: NonEmpty
    kind: Literal["property", "relation"]
    range_labels: list[str]
    state: Literal["pending", "candidate", "accepted"]


class HarnessStageCost(HarnessModel):
    phase: Phase
    stage: Stage
    calls: Count
    seconds: float = Field(ge=0)
    # Missing provider measurements remain unknown, including after a retry
    # whose earlier failed attempt did not return usage.
    input_tokens: Count | None
    output_tokens: Count | None
    unmeasured_attempts: Count


class HarnessReading(HarnessModel):
    total_characters: Count
    processed_characters: Count
    complete_characters: Count
    complete: bool

    @model_validator(mode="after")
    def processed_within_source(self):
        if not self.complete_characters <= self.processed_characters <= self.total_characters:
            raise ValueError("reading_exceeds_source_length")
        return self


class HarnessWorkCounts(HarnessModel):
    ready: Count
    waiting: Count
    pruned: Count
    done: Count
    failed: Count


class HarnessPhaseWorkCounts(HarnessModel):
    discovery: HarnessWorkCounts
    skeleton: HarnessWorkCounts
    semantic: HarnessWorkCounts
    deterministic: HarnessWorkCounts


class HarnessCheckCounts(HarnessModel):
    not_run: Count
    passed: Count
    invalid: Count
    incomplete: Count
    not_applicable: Count
    error: Count


class HarnessCalibrationCounts(HarnessModel):
    identifier: HarnessCheckCounts
    datatype: HarnessCheckCounts
    unit: HarnessCheckCounts
    shacl: HarnessCheckCounts


class HarnessReadingWindows(HarnessModel):
    total: Count
    saved: Count
    complete: Count
    incomplete: Count
    active: Annotated[int, Field(ge=0, le=2)]

    @model_validator(mode="after")
    def consistent_counts(self):
        if self.saved != self.complete + self.incomplete or self.saved > self.total:
            raise ValueError("reading_window_counts_invalid")
        if self.active > self.total - self.saved:
            raise ValueError("reading_window_active_invalid")
        return self


class HarnessProgress(HarnessModel):
    completed_calls: Count
    candidate_count: Count
    fact_count: Count
    phase: Phase
    phase_work_counts: HarnessPhaseWorkCounts
    calibration_counts: HarnessCalibrationCounts
    reading_windows: HarnessReadingWindows
    scope_complete: bool
    reading: HarnessReading
    work_counts: HarnessWorkCounts
    candidate_scope_limited: bool
    rule_verified_count: Count
    llm_verified_count: Count
    stage_costs: list[HarnessStageCost]

    @model_validator(mode="after")
    def scope_matches_reading(self):
        if self.phase != "discovery" and self.reading_windows.active:
            raise ValueError("reading_windows_active_after_reading")
        if self.scope_complete != self.reading.complete:
            raise ValueError("reading_scope_mismatch")
        return self


class HarnessCandidateWork(HarnessModel):
    id: NonEmpty
    kind: Literal["relation_alignment", "coreference_review"]
    subject_id: NonEmpty
    object_ids: list[NonEmpty]
    predicate_iri: Iri | None
    status: WorkStatus
    reason_code: NonEmpty | None
    evidence: list[HarnessSourceRef]


class InterpretationAnswer(HarnessModel):
    meaning: Literal["alternatives", "parallel", "joint_unspecified", "unresolved"]
    scope: Literal["occurrence", "document", "platform"]
    revision: int = Field(ge=1)


class InterpretationQuestion(HarnessModel):
    prompt: NonEmpty
    options: list[dict[str, str]]


class InterpretationTask(HarnessModel):
    id: NonEmpty
    subject_label: NonEmpty
    object_labels: list[NonEmpty]
    relation_label: NonEmpty
    evidence: list[HarnessSourceRef]
    questions: list[InterpretationQuestion]
    answer: InterpretationAnswer | None
    scope_revisions: dict[str, int]


class SubmitInterpretationAnswer(HarnessModel):
    meaning: Literal["alternatives", "parallel", "joint_unspecified", "unresolved"]
    scope: Literal["occurrence", "document", "platform"]
    expected_revision: Count

    @model_validator(mode="after")
    def unresolved_is_local(self):
        if self.meaning == "unresolved" and self.scope != "occurrence":
            raise ValueError("unresolved_interpretation_must_be_local")
        return self


class HarnessGraph(HarnessModel):
    protocol: Literal["document-harness-v2"]
    run_id: UUID
    revision: Count
    status: Literal[
        "queued", "running", "pausing", "paused", "finished", "failed",
        "blocked_dependency", "cancelled",
    ]
    stage: Stage
    progress: HarnessProgress
    entities: list[HarnessEntity]
    coreferences: list[HarnessCoreference]
    properties: list[HarnessProperty]
    relations: list[HarnessRelation]
    relation_groups: list[HarnessRelationGroup]
    interpretation_tasks: list[InterpretationTask]
    observations: list[HarnessObservation]
    targets: list[HarnessTarget]
    candidate_work: list[HarnessCandidateWork]


class HarnessSource(HarnessModel):
    source_id: NonEmpty
    text: str
    page: int | None
    section_id: NonEmpty
    block_id: NonEmpty
    row: Count | None
    column: Count | None
