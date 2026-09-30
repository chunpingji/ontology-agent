"""Read-only ontology targets, separate from the run's asserted graph."""

from typing import Literal

from pydantic import Field

from app.schemas.document_analysis import (
    ApiModel,
    ArtifactAvailability,
    EntityRef,
    FullIri,
    GraphArtifactResponse,
    NonEmpty,
    RevisionRef,
    RunWatermark,
)


class TargetRoot(EntityRef):
    class_iri: FullIri
    label: NonEmpty


class TargetRangeType(ApiModel):
    iri: FullIri
    label: NonEmpty


class TargetCoverage(ApiModel):
    records_planned: int = Field(default=0, ge=0)
    records_examined: int = Field(default=0, ge=0)
    records_incomplete: int = Field(default=0, ge=0)
    records_unattempted: int = Field(default=0, ge=0)
    pending_frontiers: int = Field(default=0, ge=0)
    scope_checked: bool = False


class GraphTarget(ApiModel):
    target_id: NonEmpty
    subject_ref: EntityRef
    subject_label: NonEmpty
    subject_class_iri: FullIri
    kind: Literal["relationship", "property"]
    predicate_iri: FullIri
    predicate_label: NonEmpty
    range_types: list[TargetRangeType] = Field(default_factory=list)
    datatype_iris: list[str] = Field(default_factory=list)
    multiplicity: Literal["single", "multiple", "unspecified"]
    state: Literal[
        "pending", "partial", "supported", "not_found", "undetermined", "rejected", "negated"
    ]
    supported_count: int = Field(default=0, ge=0)
    negated_count: int = Field(default=0, ge=0)
    completed: bool = False
    assertion_refs: list[RevisionRef] = Field(default_factory=list)
    # Proof-qualified assertions include separately labelled negative statements.
    supported_assertion_refs: list[RevisionRef] = Field(default_factory=list)
    coverage: TargetCoverage = Field(default_factory=TargetCoverage)
    scope_id: str | None = None
    reason: str


class TargetCounts(ApiModel):
    total: int = Field(default=0, ge=0)
    supported: int = Field(default=0, ge=0)
    completed: int = Field(default=0, ge=0)
    percent: float | None = Field(default=None, ge=0, le=100)


class TargetSummary(ApiModel):
    relationships: TargetCounts = Field(default_factory=TargetCounts)
    properties: TargetCounts = Field(default_factory=TargetCounts)
    pending_expansion_count: int = Field(default=0, ge=0)
    notes: list[str] = Field(default_factory=list)


class DiscoverySource(ApiModel):
    text: str
    selection_ref: str | None = None


class SavedDiscoveryItem(ApiModel):
    id: str
    kind: Literal["entity", "property", "relation", "observation", "failure"]
    label: str
    class_iri: str | None = None
    predicate_iri: str | None = None
    subject_id: str | None = None
    object_ids: list[str] = Field(default_factory=list)
    state: Literal["pending", "rejected", "accepted", "observation", "failed"]
    reasons: list[str] = Field(default_factory=list)
    sources: list[DiscoverySource] = Field(default_factory=list)


class SavedDiscoverySummary(ApiModel):
    completed_calls: int = 0
    inflight_calls: int = 0
    candidate_count: int = 0
    items: list[SavedDiscoveryItem] = Field(default_factory=list)


class DocumentTargetGraphResponse(RunWatermark):
    phase: Literal["candidate_graph", "evidence_review", "evidence_verification"]
    availability: ArtifactAvailability
    ontology_snapshot_id: str | None
    root: TargetRoot
    graph: GraphArtifactResponse
    targets: list[GraphTarget] = Field(default_factory=list)
    summary: TargetSummary
    discovery: SavedDiscoverySummary = Field(default_factory=SavedDiscoverySummary)
