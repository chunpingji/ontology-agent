"""Strict public schemas for the document-analysis runs v1 API.

These models describe only the HTTP/SSE boundary.  In particular, the
ontology-guided domain ``GraphSnapshot`` is an internal persistence/projection
object and must be explicitly mapped into ``GraphArtifactResponse``.  It is
never nested directly in an external response.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from app.schemas.evidence import EvidenceAnchor

CONTRACT_VERSION = "document-analysis-runs-v1"

NonEmpty = Annotated[str, Field(min_length=1)]
RequestKey = Annotated[str, Field(min_length=1, max_length=200)]
Reason = Annotated[str, Field(min_length=1, max_length=4000)]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
FullIri = Annotated[
    str,
    Field(
        min_length=3,
        max_length=2000,
        pattern=r"^[A-Za-z][A-Za-z0-9+.-]*:[^\s]+$",
    ),
]

ContractVersion = Literal["document-analysis-runs-v1"]
MetadataMode = Literal["cached_summary", "generate_summary", "structure_only"]
ScopeMode = Literal["document_graph", "focus_path"]
RunStatus = Literal[
    "queued",
    "running",
    "finished",
    "retryable_failure",
    "blocked_dependency",
    "paused",
    "cancelled",
    "deleting",
    "deleted",
    "expired",
]
RunStage = Literal[
    "accepted",
    "storing_source",
    "converting",
    "parsing",
    "preparing_metadata",
    "freezing_inputs",
    "planning",
    "extracting",
    "projecting",
    "finalizing",
    "complete",
]
ArtifactAvailability = Literal["pending", "ready", "partial", "failed"]
GraphProjection = Literal[
    "effective_affirmed",
    "all_candidates",
    "unassociated",
    "negated",
    "conditional",
    "undetermined",
    "rejected",
]
AvailableAction = Literal["pause", "resume", "cancel", "delete"]
RunOperation = Literal["pause", "resume", "cancel", "delete"]
AssertionPolarity = Literal["affirmed", "negated", "conditional", "uncertain"]
IndependentReview = Literal["unreviewed", "accepted", "rejected"]


class ApiModel(BaseModel):
    """Fail-closed Pydantic base for public API payloads."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class RunWatermark(ApiModel):
    contract_version: ContractVersion = CONTRACT_VERSION
    recognition_run_id: UUID
    run_revision: int = Field(ge=0)
    event_head: int = Field(ge=0)
    artifact_revision: int = Field(ge=0)


class CreateRunRequest(ApiModel):
    """Validated multipart form fields; ``UploadFile`` remains an API parameter."""

    root_class_iri: FullIri
    request_key: RequestKey
    metadata_mode: MetadataMode = "generate_summary"


class CreateRunInput(ApiModel):
    filename: NonEmpty
    root_class_iri: FullIri
    root_class_label: NonEmpty
    metadata_mode: MetadataMode


class RunLinks(ApiModel):
    self: NonEmpty
    metadata: NonEmpty
    graph: NonEmpty
    source: NonEmpty
    events: NonEmpty


class CreateRunResponse(RunWatermark):
    run_revision: int = Field(default=1, ge=1)
    event_head: int = Field(default=1, ge=1)
    artifact_revision: int = Field(default=1, ge=1)
    status: RunStatus = "queued"
    stage: RunStage = "accepted"
    idempotent_replay: bool = False
    input: CreateRunInput
    created_at: AwareDatetime
    expires_at: AwareDatetime | None = None
    links: RunLinks


class RunInput(ApiModel):
    filename: NonEmpty
    root_class_iri: FullIri
    root_class_label: NonEmpty
    metadata_mode: MetadataMode
    scope_mode: ScopeMode = "document_graph"


class RunIdentities(ApiModel):
    analysis_id: str | None = None
    ontology_snapshot_id: str | None = None
    metadata_snapshot_id: str | None = None
    graph_snapshot_id: str | None = None
    fingerprint_status: Literal["provisional", "frozen"]


class RunArtifacts(ApiModel):
    source: ArtifactAvailability = "pending"
    structure: ArtifactAvailability = "pending"
    metadata: ArtifactAvailability = "pending"
    graph: ArtifactAvailability = "pending"


class PhaseCounts(ApiModel):
    phase1: int = Field(default=0, ge=0)
    phase2: int = Field(default=0, ge=0)


class DecisionCounts(ApiModel):
    supported: int = Field(default=0, ge=0)
    unsupported: int = Field(default=0, ge=0)
    undetermined: int = Field(default=0, ge=0)
    not_checked: int = Field(default=0, ge=0)


class RunProgress(ApiModel):
    tasks_attempted: int = Field(default=0, ge=0)
    model_calls: int = Field(default=0, ge=0)
    records_planned: int = Field(default=0, ge=0)
    records_examined: int = Field(default=0, ge=0)
    records_incomplete: int = Field(default=0, ge=0)
    records_unattempted: int = Field(default=0, ge=0)
    phase_counts: PhaseCounts = Field(default_factory=PhaseCounts)
    decisions: DecisionCounts = Field(default_factory=DecisionCounts)
    pending_frontiers: int = Field(default=0, ge=0)
    stop_reason: str | None = None
    contract_version: ContractVersion = CONTRACT_VERSION
    event_head: int = Field(ge=0)
    artifact_revision: int = Field(ge=0)

    @model_validator(mode="after")
    def conserve_record_coverage(self) -> Self:
        accounted = self.records_examined + self.records_incomplete + self.records_unattempted
        if self.records_planned != accounted:
            raise ValueError("records_planned must equal examined + incomplete + unattempted")
        return self


class RunFailure(ApiModel):
    code: NonEmpty
    stage: RunStage
    retryable: bool
    safe_detail: NonEmpty
    occurred_at: AwareDatetime


class DocumentAnalysisRunResponse(RunWatermark):
    status: RunStatus
    stage: RunStage
    input: RunInput
    identities: RunIdentities
    artifacts: RunArtifacts
    progress: RunProgress
    error: RunFailure | None = None
    available_actions: list[AvailableAction] = Field(default_factory=list)
    created_at: AwareDatetime
    started_at: AwareDatetime | None = None
    paused_at: AwareDatetime | None = None
    finished_at: AwareDatetime | None = None
    expires_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def validate_watermarks_and_actions(self) -> Self:
        if self.progress.event_head != self.event_head:
            raise ValueError("progress.event_head must match the run event_head")
        if self.progress.artifact_revision != self.artifact_revision:
            raise ValueError("progress.artifact_revision must match the run artifact_revision")
        if len(self.available_actions) != len(set(self.available_actions)):
            raise ValueError("available_actions must not contain duplicates")
        return self


class AnalysisIdentity(ApiModel):
    analysis_id: NonEmpty
    document_hash: Digest
    structure_hash: Digest
    parser_version: NonEmpty
    structure_policy_version: NonEmpty


class MetadataSnapshotHeader(ApiModel):
    snapshot_id: NonEmpty
    generation_source: Literal["model_summary", "extractive_fallback", "structure_only", "mixed"]
    summary_version: str | None = None
    summary_model_identity: str | None = None
    dependency_hash: Digest
    frozen: Literal[True] = True


class DocumentContent(ApiModel):
    type: Literal["doc"] = "doc"
    attrs: dict[str, Any] | None = None
    content: list[dict[str, Any]] = Field(default_factory=list)
    analysis: dict[str, Any] | None = None


class Pagination(ApiModel):
    mode: NonEmpty
    physical_page_numbers_available: bool
    is_estimated: bool
    warning: str | None = None


class MetadataArtifactResponse(RunWatermark):
    availability: ArtifactAvailability
    stage: RunStage
    retry_after_ms: int | None = Field(default=None, ge=1)
    metadata: None = None
    analysis: AnalysisIdentity | None = None
    metadata_snapshot: MetadataSnapshotHeader | None = None
    filename: str | None = None
    content: DocumentContent | None = None
    section_tree: dict[str, Any] | None = None
    pagination: Pagination | None = None
    warnings: list[str] = Field(default_factory=list)
    error: RunFailure | None = None

    @model_validator(mode="after")
    def validate_availability_shape(self) -> Self:
        available_fields = (
            self.analysis,
            self.filename,
            self.content,
            self.section_tree,
            self.pagination,
        )
        if self.availability == "pending":
            if any(value is not None for value in available_fields):
                raise ValueError("pending metadata cannot expose an uncommitted artifact")
            if self.error is not None:
                raise ValueError("pending metadata cannot carry a terminal error")
        elif self.availability == "ready":
            if any(value is None for value in available_fields):
                raise ValueError("ready metadata requires the complete structure artifact")
            if self.metadata_snapshot is None:
                raise ValueError("ready metadata requires a frozen metadata snapshot")
            if self.error is not None:
                raise ValueError("ready metadata cannot carry an error")
        elif self.availability == "partial":
            if any(value is None for value in available_fields):
                raise ValueError("partial metadata still requires the committed structure")
        elif self.error is None:
            raise ValueError("failed metadata requires a safe error object")
        return self


class RevisionRef(ApiModel):
    id: NonEmpty
    revision: int = Field(ge=1)


class EntityRef(ApiModel):
    entity_id: NonEmpty
    revision: int = Field(ge=1)


class GraphSnapshotHeader(ApiModel):
    """Public identity header, not the internal domain snapshot payload."""

    snapshot_id: NonEmpty
    analysis_id: NonEmpty
    metadata_snapshot_id: NonEmpty
    ontology_snapshot_id: NonEmpty
    root_ref: EntityRef
    projection_policy: NonEmpty
    generated_at: AwareDatetime


class RoleSourceSelectionRefs(ApiModel):
    subject: list[str] = Field(default_factory=list)
    object: list[str] = Field(default_factory=list)
    value: list[str] = Field(default_factory=list)
    predicate_bridge: list[str] = Field(default_factory=list)
    condition: list[str] = Field(default_factory=list)
    counterevidence: list[str] = Field(default_factory=list)


class GraphEntity(ApiModel):
    entity_id: NonEmpty
    revision: int = Field(ge=1)
    class_iri: FullIri
    class_label: NonEmpty
    label: NonEmpty
    seed_origin: Literal["user_selected", "recognized"] = "recognized"
    identity_state: Literal[
        "document_local", "verified_key", "verified_external", "undetermined"
    ] = "document_local"
    independent_review: IndependentReview = "unreviewed"
    source_selection_refs: list[str] = Field(default_factory=list)


class GraphAssertion(ApiModel):
    candidate_id: NonEmpty
    revision: int = Field(ge=1)
    subject_ref: EntityRef
    predicate_iri: FullIri
    predicate_label: NonEmpty
    polarity: AssertionPolarity = "affirmed"
    conditions: list[dict[str, Any]] = Field(default_factory=list)
    applicability: dict[str, Any] = Field(default_factory=dict)
    structural_valid: bool
    model_supported: bool
    policy_eligible: bool
    independent_review: IndependentReview = "unreviewed"
    proof_ref: RevisionRef | None = None
    decision_refs: list[RevisionRef] = Field(default_factory=list)
    dependency_refs: list[RevisionRef] = Field(default_factory=list)
    invalidated: bool = False
    reason_code: str | None = None
    reason: str | None = None
    source_selection_refs: RoleSourceSelectionRefs


class GraphProperty(GraphAssertion):
    direction: Literal["subject_to_value"] = "subject_to_value"
    raw_value: str
    normalized_value: Any = None
    datatype_iri: str | None = None
    unit: str | None = None


class GraphRelationship(GraphAssertion):
    object_ref: EntityRef
    direction: Literal["subject_to_object", "object_to_subject"] = "subject_to_object"


class CoverageSubject(ApiModel):
    subject_ref: EntityRef
    predicate_iri: FullIri
    predicate_label: NonEmpty
    records_planned: int = Field(ge=0)
    records_examined: int = Field(ge=0)
    records_incomplete: int = Field(ge=0)
    records_unattempted: int = Field(ge=0)
    phase_counts: PhaseCounts = Field(default_factory=PhaseCounts)
    pending_frontiers: int = Field(default=0, ge=0)
    stop_reason: str | None = None

    @model_validator(mode="after")
    def conserve_records(self) -> Self:
        accounted = self.records_examined + self.records_incomplete + self.records_unattempted
        if self.records_planned != accounted:
            raise ValueError("coverage subject record counts must be conserved")
        return self


class GraphCoverage(ApiModel):
    subjects: list[CoverageSubject] = Field(default_factory=list)
    records_planned: int = Field(default=0, ge=0)
    records_examined: int = Field(default=0, ge=0)
    records_incomplete: int = Field(default=0, ge=0)
    records_unattempted: int = Field(default=0, ge=0)
    phase2_started: bool = False
    pending_frontiers: int = Field(default=0, ge=0)
    stop_reason: str | None = None

    @model_validator(mode="after")
    def conserve_records(self) -> Self:
        accounted = self.records_examined + self.records_incomplete + self.records_unattempted
        if self.records_planned != accounted:
            raise ValueError("graph coverage record counts must be conserved")
        return self


class GraphUnresolved(ApiModel):
    unsupported: int = Field(default=0, ge=0)
    undetermined: int = Field(default=0, ge=0)
    not_checked: int = Field(default=0, ge=0)
    unassociated_entities: int = Field(default=0, ge=0)


class GraphArtifactResponse(RunWatermark):
    availability: ArtifactAvailability
    projection: GraphProjection = "effective_affirmed"
    graph_snapshot: GraphSnapshotHeader | None = None
    entities: list[GraphEntity] = Field(default_factory=list)
    properties: list[GraphProperty] = Field(default_factory=list)
    relationships: list[GraphRelationship] = Field(default_factory=list)
    invalidated_refs: list[RevisionRef] = Field(default_factory=list)
    coverage: GraphCoverage = Field(default_factory=GraphCoverage)
    unresolved: GraphUnresolved = Field(default_factory=GraphUnresolved)
    error: RunFailure | None = None

    @model_validator(mode="after")
    def validate_projection(self) -> Self:
        if self.availability == "pending":
            if self.graph_snapshot is not None:
                raise ValueError("pending graph cannot expose an uncommitted snapshot")
            if self.entities or self.properties or self.relationships:
                raise ValueError("pending graph cannot expose projected graph items")
        elif self.availability in {"ready", "partial"} and self.graph_snapshot is None:
            raise ValueError("available graph requires a public snapshot header")

        if self.projection == "effective_affirmed":
            for item in [*self.properties, *self.relationships]:
                if item.polarity != "affirmed":
                    raise ValueError("effective_affirmed cannot include non-affirmed assertions")
                if not (
                    item.structural_valid
                    and item.model_supported
                    and item.policy_eligible
                    and item.proof_ref is not None
                    and bool(item.decision_refs)
                    and not item.invalidated
                    and item.independent_review != "rejected"
                ):
                    raise ValueError(
                        "effective_affirmed can include only current proof-gate results"
                    )
        return self


SourceSelectionRole = Literal[
    "entity",
    "subject",
    "object",
    "value",
    "predicate_bridge",
    "condition",
    "counterevidence",
]


class SourceQuery(ApiModel):
    """Only opaque selection refs are accepted; never paths, anchors or coordinates."""

    selection_ref: str | None = Field(default=None, min_length=1)
    format: Literal["original"] | None = None

    @model_validator(mode="after")
    def separate_preview_from_download(self) -> Self:
        if self.selection_ref is not None and self.format == "original":
            raise ValueError("selection_ref cannot be combined with original download")
        return self


class SourceSelection(ApiModel):
    selection_ref: NonEmpty
    section_node_id: NonEmpty
    source_record_ref: str | None = None
    record_view_ref: str | None = None
    source_cell_id: str | None = None
    span_refs: list[str] = Field(min_length=1)
    selection_role: SourceSelectionRole

    @model_validator(mode="after")
    def require_record_identity(self) -> Self:
        if self.source_record_ref is None and self.record_view_ref is None:
            raise ValueError("source selection requires a record or record-view reference")
        return self


class SourceArtifactResponse(ApiModel):
    contract_version: ContractVersion = CONTRACT_VERSION
    recognition_run_id: UUID
    analysis_id: NonEmpty
    document_hash: Digest
    structure_hash: Digest
    filename: NonEmpty
    content: DocumentContent
    selection: SourceSelection | None = None
    anchors: list[EvidenceAnchor] = Field(default_factory=list)

    @model_validator(mode="after")
    def anchors_belong_to_source(self) -> Self:
        for anchor in self.anchors:
            if anchor.document_hash != self.document_hash:
                raise ValueError("source anchor belongs to another document")
            if anchor.structure_hash != self.structure_hash:
                raise ValueError("source anchor belongs to another structure revision")
        return self


class RunControlRequest(ApiModel):
    expected_revision: int = Field(ge=0)
    request_key: RequestKey
    reason: Reason


class DeleteRunRequest(ApiModel):
    expected_revision: int = Field(ge=0)
    request_key: RequestKey


class RunControlResponse(RunWatermark):
    status: RunStatus
    stage: RunStage
    operation: RunOperation
    operation_status: Literal["accepted"] = "accepted"
    available_actions: list[AvailableAction] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_available_actions(self) -> Self:
        if len(self.available_actions) != len(set(self.available_actions)):
            raise ValueError("available_actions must not contain duplicates")
        return self


EventType = Literal[
    "run_state", "progress", "artifact", "warning", "error", "heartbeat", "tombstone"
]
ArtifactKind = Literal["source", "structure", "metadata", "graph"]


class RunEventResponse(RunWatermark):
    """Safe JSON carried in one SSE ``data:`` line."""

    event_id: NonEmpty
    status: RunStatus
    stage: RunStage
    artifact_kind: ArtifactKind | None = None
    availability: ArtifactAvailability | None = None

    @model_validator(mode="after")
    def pair_artifact_fields(self) -> Self:
        if (self.artifact_kind is None) != (self.availability is None):
            raise ValueError("artifact_kind and availability must be supplied together")
        return self


class SseEvent(ApiModel):
    """Typed representation used to render an SSE frame."""

    id: int = Field(ge=1)
    event: EventType
    data: RunEventResponse

    @model_validator(mode="after")
    def sequence_matches_event_head(self) -> Self:
        if self.id != self.data.event_head:
            raise ValueError("SSE id must equal the durable event sequence")
        if self.event == "artifact" and self.data.artifact_kind is None:
            raise ValueError("artifact events require artifact metadata")
        return self


ErrorCode = Literal[
    "INVALID_REQUEST",
    "UNAUTHENTICATED",
    "ROLE_FORBIDDEN",
    "RUN_NOT_FOUND",
    "IDEMPOTENCY_CONFLICT",
    "RUN_REVISION_CONFLICT",
    "RUN_STATE_CONFLICT",
    "FINGERPRINT_MISMATCH",
    "RUN_DELETED",
    "RUN_EXPIRED",
    "SOURCE_TOO_LARGE",
    "UNSUPPORTED_SOURCE_TYPE",
    "EMPTY_SOURCE",
    "INVALID_WORD",
    "INVALID_ROOT_CLASS",
    "ONTOLOGY_UNAVAILABLE",
]


class ApiError(ApiModel):
    code: ErrorCode
    message: NonEmpty
    retryable: bool
    current_revision: int | None = Field(default=None, ge=0)


class ApiErrorResponse(ApiModel):
    contract_version: ContractVersion = CONTRACT_VERSION
    error: ApiError


# Descriptive aliases for callers that prefer resource-qualified names.
DocumentAnalysisRunCreateRequest = CreateRunRequest
DocumentAnalysisRunCreateResponse = CreateRunResponse
DocumentAnalysisRunStatusResponse = DocumentAnalysisRunResponse
DocumentAnalysisMetadataResponse = MetadataArtifactResponse
DocumentAnalysisGraphResponse = GraphArtifactResponse
DocumentAnalysisSourceQuery = SourceQuery
DocumentAnalysisSourceResponse = SourceArtifactResponse
DocumentAnalysisDeleteRequest = DeleteRunRequest
DocumentAnalysisRunEvent = RunEventResponse


__all__ = [
    "CONTRACT_VERSION",
    "ApiError",
    "ApiErrorResponse",
    "ApiModel",
    "ArtifactAvailability",
    "CreateRunRequest",
    "CreateRunResponse",
    "DeleteRunRequest",
    "DocumentAnalysisDeleteRequest",
    "DocumentAnalysisGraphResponse",
    "DocumentAnalysisMetadataResponse",
    "DocumentAnalysisRunCreateRequest",
    "DocumentAnalysisRunCreateResponse",
    "DocumentAnalysisRunEvent",
    "DocumentAnalysisRunResponse",
    "DocumentAnalysisRunStatusResponse",
    "DocumentAnalysisSourceQuery",
    "DocumentAnalysisSourceResponse",
    "EntityRef",
    "GraphArtifactResponse",
    "GraphCoverage",
    "GraphEntity",
    "GraphProjection",
    "GraphProperty",
    "GraphRelationship",
    "GraphSnapshotHeader",
    "MetadataArtifactResponse",
    "RoleSourceSelectionRefs",
    "RunControlRequest",
    "RunControlResponse",
    "RunEventResponse",
    "RunProgress",
    "RunStage",
    "RunStatus",
    "SourceArtifactResponse",
    "SourceQuery",
    "SourceSelection",
    "SseEvent",
]
