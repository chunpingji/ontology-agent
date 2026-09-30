"""Shared online/offline ontology-guided execution core.

The core owns scheduling, coverage and projection.  A model adapter may propose
record outcomes, but it cannot mutate registries or bypass exact endpoint and
predicate checks performed here.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable
from copy import deepcopy
from dataclasses import asdict
from threading import get_ident
from typing import Literal, Protocol

from pydantic import Field, model_serializer

from app.schemas.attribute_calibration import AttributeCalibrationCandidate
from app.schemas.evidence import EvidenceAnchor, EvidenceModel
from app.services.extraction.annotation_execution import ExecutionLost
from app.services.extraction.document_ir import DocumentIR
from app.services.extraction.evidence_identity import evidence_hash, stable_id
from app.services.extraction.ontology_guided.candidate_planning import admit_records, shared_scope
from app.services.extraction.ontology_guided.claim_protocol import compile_schema_card
from app.services.extraction.ontology_guided.context import (
    TaskContext,
    assemble_context,
    covers_required_sources,
)
from app.services.extraction.ontology_guided.contracts import (
    CANDIDATE_POLICY_VERSION,
    CoverageSummary,
    DocumentContext,
    EdgeSpec,
    GraphEdge,
    GraphNode,
    GraphProperty,
    GraphRelationshipGroup,
    GraphSnapshot,
    LocalMenu,
    MetadataSnapshot,
    OntologySnapshot,
    RetrievalPlan,
    RunProgress,
    SlotSpec,
    SubjectRef,
    TraversalScope,
    VerificationTarget,
    VersionedRef,
)
from app.services.extraction.ontology_guided.current_work import (
    TOOL_BATCH_PROTOCOL_VERSION,
    TOOL_PROTOCOL_VERSION,
    SlotParts,
    WorkMap,
    WorkSet,
    call_request_key,
    enable_plan_parts,
    enable_search_parts,
    json_value,
    member_result_version,
    plan_header,
    protocol_result_ref,
    restore_search_parts,
    search_current_changes,
)
from app.services.extraction.ontology_guided.dependencies import DependencyIndex
from app.services.extraction.ontology_guided.evidence_work import EvidenceWorkQueue
from app.services.extraction.ontology_guided.expert_review import (
    MAX_REPAIR_CALLS,
    ReviewReplay,
    apply_property_review,
    assertion_identity,
    local_repair_tasks,
    repair_feedback,
)
from app.services.extraction.ontology_guided.heuristic_search import (
    HeuristicSearchIndex,
    HeuristicSearchPolicy,
    HeuristicSlotSearch,
)
from app.services.extraction.ontology_guided.lazy_frontier import slot_key, subject_key
from app.services.extraction.ontology_guided.ontology_plan import (
    compile_local_menu,
)
from app.services.extraction.ontology_guided.projection import (
    effective_proof_gate,
    frontier_eligibility,
    project_graph,
)
from app.services.extraction.ontology_guided.ranking_execution import RankingPreparation
from app.services.extraction.ontology_guided.recognition_batch import (
    MemberContext,
    RecognitionBatchContext,
    RecognitionWorkUnit,
    pack_work_unit,
)
from app.services.extraction.ontology_guided.recognition_execution import RecognitionCall
from app.services.extraction.ontology_guided.record_discovery import (
    RECORD_PIPELINE,
    RECORD_PROTOCOL,
    CandidateChanges,
    RecordDiscoveryPolicy,
    RecordDiscoveryTask,
    compile_discovery_catalog,
    compile_partitioned_record_schema_cards,
    reference_pages,
    resolve_record_predicate,
)
from app.services.extraction.ontology_guided.records import RecordIndex
from app.services.extraction.ontology_guided.retrieval import (
    mark_record,
    plan_slot,
    validate_record_universe,
)
from app.services.extraction.ontology_guided.retrieval_query import QueryMention
from app.services.extraction.ontology_guided.scheduler import FrontierScheduler, RecognitionTask
from app.services.extraction.ontology_guided.schema_region_routing import (
    compile_schema_region_routing,
)
from app.services.extraction.ontology_guided.semantic_reranker import (
    RankingPaused,
    RankingPolicy,
    RankingService,
    apply_epoch,
)
from app.services.extraction.ontology_guided.slot_completion import (
    DEPENDENCY_READY_VERSION,
    LAYERED_RECOGNITION_VERSION,
    SlotCompletionIndex,
    single_value_candidate,
)
from app.services.extraction.ontology_guided.slot_replay import SlotSearchReplay
from app.services.llm.model_runtime import ModelCancelled, ModelWaitFailure


class ModelCallPersistenceFailure(RuntimeError):
    """A reservation could not be durably fenced; no request may follow."""


class ModelCallPauseRequested(RuntimeError):
    """The owner requested a pause between independent model stages."""


class ExpertRepairBudgetExhausted(RuntimeError):
    """A bounded repair has no authority to reserve another model request."""


def semantic_retrieval_plan_key(
    plan: RetrievalPlan, *, dependency_refs: list[VersionedRef], generation: int,
) -> str:
    """Identify plans that would perform the same authorized semantic search.

    Traversal scope IDs are intentionally absent. Their material effect is the
    exact endpoint/proof dependency set below; a different condition, incoming
    assertion or proof revision therefore remains a distinct plan.
    """
    record_hash = (
        plan.search_scope_ref["record_hash"]
        if plan.search_scope_ref is not None else plan.frozen_record_hash
    )
    dependencies = sorted({(ref.id, ref.revision) for ref in dependency_refs})
    return evidence_hash({
        "subject_ref": {
            "id": plan.subject.entity_id, "revision": plan.subject.revision,
        },
        "predicate_iri": plan.predicate_iri,
        "record_hash": record_hash,
        "endpoint_refs": [(plan.subject.entity_id, plan.subject.revision)],
        "dependency_refs": dependencies,
        "proof_generation": generation,
    })


class TaskOutcome(EvidenceModel):
    semantic_outcome: Literal["supported", "unsupported", "undetermined", "not_checked"]
    complete: bool = True
    reason_code: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    model_calls: int = Field(default=0, ge=0)
    controller_checks: dict[str, int | float] = Field(default_factory=dict)
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)
    properties: list[GraphProperty] = Field(default_factory=list)
    relationship_groups: list[GraphRelationshipGroup] = Field(default_factory=list)
    proof_payloads: list[dict] = Field(default_factory=list)
    decision_payloads: list[dict] = Field(default_factory=list)
    reference_resolutions: list[dict] = Field(default_factory=list)
    attribute_candidates: list[AttributeCalibrationCandidate] = Field(default_factory=list)

    @model_serializer(mode="wrap")
    def preserve_legacy_shape(self, handler):
        data = handler(self)
        if "relationship_groups" not in self.model_fields_set:
            data.pop("relationship_groups", None)
        if "controller_checks" not in self.model_fields_set:
            data.pop("controller_checks", None)
        if "reference_resolutions" not in self.model_fields_set:
            data.pop("reference_resolutions", None)
        if not self.attribute_candidates:
            data.pop("attribute_candidates", None)
        return data


class RecognitionAdapter(Protocol):
    model_identity: str

    def inspect(
        self,
        task: RecognitionTask,
        context: TaskContext,
        predicate: SlotSpec | EdgeSpec,
        menu: LocalMenu,
    ) -> TaskOutcome: ...


class ExecutionResult(EvidenceModel):
    ontology_snapshot: OntologySnapshot
    root_menu: LocalMenu
    graph: GraphSnapshot
    retrieval_plans: list[dict]
    events: list[tuple[str, dict]]
    diagnostics: list[str] = Field(default_factory=list)
    ranking_state: dict = Field(default_factory=dict)
    model_call_state: dict = Field(default_factory=dict)
    evidence_repair_summary: dict = Field(default_factory=dict)


class ExecutionBatch(EvidenceModel):
    """Serializable safe-boundary state emitted after one logical task attempt."""

    batch_id: str = Field(min_length=1)
    task: RecognitionTask | RecordDiscoveryTask | None = None
    outcome: TaskOutcome | None = None
    work_unit: RecognitionWorkUnit | None = None
    member_outcomes: dict[str, TaskOutcome] = Field(default_factory=dict)
    protocol_state_changes: dict[str, dict] = Field(default_factory=dict)
    protocol_result_changes: dict[str, dict] = Field(default_factory=dict)
    task_outcomes: list[dict]
    frontier: dict
    recall_ledger: dict
    dependency_index: dict
    graph: GraphSnapshot
    diagnostics: list[str] = Field(default_factory=list)
    ranking_state: dict = Field(default_factory=dict)
    model_call_state: dict = Field(default_factory=dict)
    evidence_repair_summary: dict = Field(default_factory=dict)
    work_changes: dict | None = None

    def incremental_payload(self):
        states = {"task_outcomes", "frontier", "recall_ledger", "dependency_index",
                  "ranking_state", "model_call_state", "evidence_repair_summary"}
        return {**self.model_dump(mode="json", exclude=states | {"work_changes"}),
                **{name: getattr(self, name) for name in states}}


class OntologyGuidedExecutor:
    version = "ontology-guided-executor-v5.1-ranking-durability"

    @classmethod
    def pipeline_version(cls, pipeline=None):
        if pipeline not in {None, RECORD_PIPELINE}:
            raise ValueError("unknown_recognition_pipeline")
        return cls.version + ("+" + pipeline if pipeline else "")

    def __init__(
        self,
        *,
        ontology: OntologySnapshot,
        engine: object,
        adapter: RecognitionAdapter | None,
        max_hops: int = 4,
        max_tasks: int = 2048,
        phase1_section_limit: int = 3,
        progress_hook: Callable[[str], bool] | None = None,
        predicate_filter: Callable[[SubjectRef, SlotSpec | EdgeSpec, int], bool] | None = None,
        ranking_service: RankingService | None = None,
        phase_interleaving: bool | None = None,
        max_model_calls_per_record: int = 6,
        priority_paths: list[tuple[str, ...]] | None = None,
        lazy_frontier: bool = True,
        template_interleaving: bool = False,
        heuristic_policy: HeuristicSearchPolicy | None = None,
        search_hook: Callable[[str, dict], None] | None = None,
        evidence_repair: bool = False,
        incremental_performance: bool = False,
        adaptive_policy=None,
        candidate_policy: str | None = None,
        layered_recognition: bool = False,
        source_object_recognition: bool = False,
        current_state: bool = False,
        record_focus_paths=(),
    ):
        self.current_state = current_state
        self.recognition_pipeline = getattr(adapter, "recognition_pipeline", None)
        self.record_pipeline = self.recognition_pipeline == RECORD_PIPELINE
        self.record_policy = (getattr(adapter, "record_discovery", None)
                              or RecordDiscoveryPolicy()) if self.record_pipeline else None
        self.candidate_graph = bool(
            self.record_policy and self.record_policy.graph_phase == "candidate_graph"
        )
        self.evidence_review = bool(
            self.record_policy and self.record_policy.graph_phase == "evidence_review"
        )
        self.independent_entity_traversal = self.candidate_graph or self.evidence_review
        self.record_focus_paths = tuple(tuple(path) for path in record_focus_paths)
        if self.recognition_pipeline not in {None, RECORD_PIPELINE}:
            raise ValueError("unknown_recognition_pipeline")
        self.tool_protocol = getattr(adapter, "protocol_version", None) == TOOL_PROTOCOL_VERSION
        self.batch_policy = getattr(adapter, "recognition_batching", None)
        if self.batch_policy is not None and not self.tool_protocol:
            raise ValueError("recognition batching requires the tool protocol")
        self.reference_resolution = self.tool_protocol and bool(
            getattr(adapter, "reference_resolution", False)
        )
        if self.tool_protocol and (not current_state or evidence_repair):
            raise ValueError("tool protocol requires current state without legacy evidence repair")
        self.ontology = ontology
        self.engine = engine
        self.adapter = adapter
        self.max_hops = max_hops
        self.max_tasks = max_tasks
        self.phase1_section_limit = phase1_section_limit
        self.progress_hook = progress_hook
        self.predicate_filter = predicate_filter
        self.ranking_service = ranking_service
        self.phase_interleaving = phase_interleaving
        if max_model_calls_per_record < 1:
            raise ValueError("per-record model budget must be positive")
        self.max_model_calls_per_record = max_model_calls_per_record
        self.priority_paths = list(dict.fromkeys(tuple(path) for path in priority_paths or []))
        self.lazy_frontier = lazy_frontier
        self.template_interleaving = template_interleaving
        self.heuristic_policy = heuristic_policy
        self.search_hook = search_hook
        self.evidence_repair = evidence_repair
        self.incremental_performance = incremental_performance
        self.adaptive_policy = adaptive_policy
        self.layered_recognition = layered_recognition
        self.source_object_recognition = source_object_recognition
        if source_object_recognition and not (
            layered_recognition and evidence_repair and incremental_performance
            and candidate_policy and heuristic_policy
            and heuristic_policy.source_object_candidates
        ):
            raise ValueError("source object recognition requires its frozen candidate policy")
        if candidate_policy not in {None, CANDIDATE_POLICY_VERSION}:
            raise ValueError("unsupported candidate planning policy")
        if candidate_policy and (
            not (evidence_repair or self.tool_protocol)
            or not incremental_performance or heuristic_policy is None
            or heuristic_policy.version not in {"heuristic-first-v3", "heuristic-first-v4"}
        ):
            raise ValueError("sparse candidate planning requires durable incremental search")
        self.candidate_policy = candidate_policy
        if adaptive_policy is not None and (
            not incremental_performance or heuristic_policy is None
            or heuristic_policy.version != "heuristic-first-v4"
        ):
            raise ValueError("adaptive retrieval requires v4 and incremental evidence repair")
        if heuristic_policy is not None and heuristic_policy.version == "heuristic-first-v4" \
                and adaptive_policy is None:
            raise ValueError("v4 requires a frozen adaptive policy")
        if incremental_performance and not (evidence_repair or self.tool_protocol):
            raise ValueError("incremental performance requires evidence repair")
        if evidence_repair:
            self.version = type(self).version + "+evidence-repair-v1"
        if incremental_performance:
            self.version += "+incremental-performance-v1"
        if adaptive_policy:
            self.version += "+adaptive-retrieval-v1"
        if candidate_policy:
            self.version += "+sparse-candidates-v1"
        if heuristic_policy is not None and not evidence_repair and not self.tool_protocol:
            self.version = type(self).version + "+heuristic-first-experiment-v1"
        if layered_recognition:
            self.version += "+" + (DEPENDENCY_READY_VERSION if source_object_recognition
                                   else LAYERED_RECOGNITION_VERSION)
        if self.tool_protocol:
            self.version += "+" + TOOL_PROTOCOL_VERSION
        if self.record_pipeline:
            if not self.tool_protocol or self.batch_policy is None or not self.reference_resolution:
                raise ValueError("record pipeline requires current tool batching and references")
            self.version += "+" + RECORD_PIPELINE

    @staticmethod
    def root_node(
        *,
        recognition_run_id: str,
        document_hash: str,
        root_class_iri: str,
        root_class_label: str,
        filename: str,
    ) -> GraphNode:
        return GraphNode(
            entity_id=stable_id(
                "document-root",
                [recognition_run_id, document_hash, root_class_iri],
            ),
            revision=1,
            class_iri=root_class_iri,
            class_label=root_class_label,
            label=filename,
            root=True,
            root_origin="user_specified",
            identity_status="document_local",
            decision_status="supported",
        )

    def run(
        self,
        *,
        recognition_run_id: str,
        run_fingerprint: str,
        ir: DocumentIR,
        metadata: MetadataSnapshot,
        root_class_iri: str,
        root_class_label: str,
        filename: str,
        run_revision: int = 0,
        event_head: int = 0,
        resume_state: dict | None = None,
        batch_hook: Callable[[ExecutionBatch], None] | None = None,
        ranking_state: dict | None = None,
        ranking_hook: Callable[[dict], None] | None = None,
        model_call_state: dict | None = None,
        model_call_hook: Callable[[dict], None] | None = None,
        property_reviews: list[dict] | None = None,
        property_repairs: list[dict] | None = None,
        repair_only: bool = False,
        work_hook: Callable[[dict], None] | None = None,
        protocol_result_loader: Callable[[str, str, str], dict] | None = None,
        protocol_record_loader: Callable[..., dict] | None = None,
    ) -> ExecutionResult:
        has_protocol = self.evidence_repair or self.tool_protocol
        call_state_version = 3 if self.batch_policy is not None else (2 if has_protocol else 1)
        active_unit_ref = None
        # A fresh record run first registers the leading root relationship as
        # waiting for endpoints.  Its exact range can then prioritize the first
        # entity discovery instead of spending a call on an unrelated region.
        discovery_turn = not self.record_pipeline
        root_scope = TraversalScope.create() if self.tool_protocol else None
        if self.adaptive_policy is not None:
            self.adaptive_policy.validate_ontology_context(self.ontology)
        if self.heuristic_policy is not None and not has_protocol and (
            resume_state is not None or ranking_state is not None or model_call_state is not None
        ):
            raise ValueError("heuristic experiment requires a fresh run; resume is not implemented")
        if resume_state is not None and (
            (resume_state.get("frontier") or {}).get("candidate_planning", {}).get("policy")
            != self.candidate_policy
        ):
            raise ValueError("candidate planning policy changed; a new run is required")
        direct = (resume_state or {}).get("work_state")
        if direct is not None and not self.current_state:
            raise ValueError("current work state requires its frozen execution policy")
        work_map = WorkMap if self.current_state else dict
        completed_tasks = 0
        work_digest = evidence_hash([recognition_run_id, run_fingerprint])
        pause_counts = work_map()
        applied_model_results = work_map()
        entity_origins = work_map()
        reference_resolutions = work_map()
        record_discovery = work_map()
        record_relation_inputs = work_map()
        search_started = time.perf_counter()
        index = RecordIndex(ir)
        search_index = (
            HeuristicSearchIndex(index, metadata) if self.heuristic_policy is not None else None
        )
        if search_index is not None and self.search_hook is not None:
            self.search_hook("heuristic_index_ready", {
                "elapsed_seconds": time.perf_counter() - search_started,
                "records": len(index.records),
            })
        restored_calls = deepcopy(
            model_call_state or (resume_state or {}).get("model_call_state") or {}
        )
        if restored_calls and (
            restored_calls.get("version") != call_state_version
            or restored_calls.get("recognition_run_id") != recognition_run_id
            or restored_calls.get("run_fingerprint") != run_fingerprint
        ):
            raise ValueError("model call state belongs to a different run or fingerprint")
        reserved_calls = work_map(restored_calls.get("lineage_calls") or {})
        reservation_sequence = restored_calls.get("reservation_sequence",
                                                  len(restored_calls.get("reservations") or []))
        reservations = list(restored_calls.get("reservations") or [])
        reservation_saved = len(reservations) if self.current_state and self.batch_policy else 0
        protocols = deepcopy(restored_calls.get("protocols") or {})
        local_protocol_results = {}
        member_owners = {
            member["task_id"]: unit_id
            for unit_id, protocol in protocols.items()
            if protocol.get("version") == TOOL_BATCH_PROTOCOL_VERSION
            for member in protocol["work_unit"]["members"]
        }
        lineage_tool_calls = {}

        def remember_tool_costs(protocol):
            if protocol.get("version") == TOOL_BATCH_PROTOCOL_VERSION:
                for member in protocol["work_unit"]["members"]:
                    lineage = member["claim_lineage_id"]
                    used = protocol["member_states"][member["task_id"]]["tool_calls_used"]
                    lineage_tool_calls[lineage] = max(lineage_tool_calls.get(lineage, 0), used)
            else:
                lineage = protocol.get("lineage_id")
                if lineage:
                    lineage_tool_calls[lineage] = max(
                        lineage_tool_calls.get(lineage, 0), protocol.get("tool_calls_used", 0),
                    )

        for saved_protocol in protocols.values():
            remember_tool_costs(saved_protocol)

        def task_protocol(task):
            owner = member_owners.get(task.task_id)
            if owner is not None:
                from app.services.extraction.ontology_guided.context import member_protocol_view

                return member_protocol_view(json_value(protocols[owner]), task.task_id)
            return protocols.get(task.claim_lineage_id, {})

        def review_waiting(task):
            if not self.evidence_review or isinstance(task, RecordDiscoveryTask):
                return False
            protocol = task_protocol(task)
            return bool(protocol.get("discovery_ref") and not protocol.get("verification_ref"))

        def task_result_owner(task):
            owner = member_owners.get(task.task_id)
            return (owner or task.claim_lineage_id,
                    {"member_task_id": task.task_id} if owner is not None else {})

        def load_record(lineage, reference, field, *, member_task_id=None):
            saved = local_protocol_results.get(reference)
            if saved is not None:
                record = deepcopy(saved)
            elif protocol_record_loader is not None:
                record = protocol_record_loader(
                    lineage, reference, field, member_task_id=member_task_id,
                )
            elif protocol_result_loader is not None:
                record = {"lineage_id": lineage, "field": field,
                          "value": deepcopy(protocol_result_loader(lineage, reference, field))}
            else:
                raise ValueError("protocol_result_loader_required")
            if (record["lineage_id"] != lineage or record["field"] != field
                    or record.get("member_task_id") != member_task_id):
                raise ValueError("protocol_result_reference_mismatch")
            kwargs = ({"member_task_id": member_task_id,
                       "result_version": record["result_version"]}
                      if member_task_id is not None else {})
            if protocol_result_ref(lineage, field, record["value"], **kwargs) != reference:
                raise ValueError("protocol_result_reference_mismatch")
            return record

        def load_result(lineage, reference, field, *, member_task_id=None):
            return load_record(lineage, reference, field, member_task_id=member_task_id)["value"]
        from app.services.extraction.ontology_guided.state_delta import freeze_json, thaw_json

        if self.incremental_performance:
            protocols = {key: freeze_json(value) for key, value in protocols.items()}
            reservations = [freeze_json(value) for value in reservations]
        if self.current_state:
            protocols = WorkMap(protocols)
            protocols.changed.clear()
            reserved_calls.changed.clear()
        evidence_work = EvidenceWorkQueue(index)
        if self.current_state:
            evidence_work.items = WorkMap()
        if any(type(count) is not int or count < 0 for count in reserved_calls.values()):
            raise ValueError("invalid reserved model call count")
        reservation_counts: dict[str, int] = {}
        for sequence, reservation in enumerate(reservations, 1):
            if (
                not isinstance(reservation, dict)
                or reservation.get("sequence") != sequence
                or not isinstance(reservation.get("lineage_id"), str)
                or not isinstance(reservation.get("task_id"), str)
                or not isinstance(reservation.get("stage"), str)
                or type(reservation.get("ordinal")) is not int
                or reservation["ordinal"] < 1
            ):
                raise ValueError("invalid model call reservation")
            for lineage in reservation.get("member_lineage_ids", [reservation["lineage_id"]]):
                reservation_counts[lineage] = reservation_counts.get(lineage, 0) + 1
        if any(count > reserved_calls.get(key, 0) for key, count in reservation_counts.items()):
            raise ValueError("model call reservation count mismatch")
        restored_ranking = ranking_state or (resume_state or {}).get("ranking_state") or {}
        if restored_ranking and (
            restored_ranking.get("recognition_run_id") != recognition_run_id
            or restored_ranking.get("run_fingerprint") != run_fingerprint
        ):
            raise ValueError("ranking state belongs to a different run or fingerprint")
        configured_ranking = self.ranking_service or RankingService(RankingPolicy())
        ranking = RankingService(
            configured_ranking.policy,
            model=configured_ranking.model,
            state=restored_ranking.get("service"),
            budget_enabled=configured_ranking.budget_enabled,
            adaptive_policy=self.adaptive_policy,
            current_state=self.current_state,
        )
        committed_at = work_map(restored_ranking.get("committed_at") or {})
        if self.current_state:
            committed_at.changed.clear()
        discarded_saved = len(restored_ranking.get("discarded_epochs") or [])
        applied_epochs = WorkSet() if self.current_state else set()
        unapplied_epochs = {}
        ranking_paused = False
        review_paused = False
        pending_ranking: RankingPreparation | None = None
        pending_ranking_key: tuple | None = None
        pending_service_state: dict | None = None
        restored_paused_epochs = [
            epoch for epoch in ranking.pending_epochs()
            if epoch["status"] == "paused"
        ]
        recovering_ranking_pause = False
        discarded_epochs: list[dict] = list(restored_ranking.get("discarded_epochs") or [])
        root = self.root_node(
            recognition_run_id=recognition_run_id,
            document_hash=ir.document_hash,
            root_class_iri=root_class_iri,
            root_class_label=root_class_label,
            filename=filename,
        )
        if self.tool_protocol:
            root = root.model_copy(update={"grounding_kind": "document_root"})
        root_subject = SubjectRef(
            entity_id=root.entity_id,
            revision=root.revision,
            class_iri=root.class_iri,
            is_document_root=True,
        )
        frozen_frontier = (resume_state or {}).get("frontier") or {}
        root_menu = compile_local_menu(
            self.ontology, root_subject, engine=self.engine,
        )
        discovery_catalog = None
        discovery_cards = {}
        routing_plan = None
        if self.record_pipeline:
            discovery_catalog = compile_discovery_catalog(
                self.ontology, root_class_iri, max_hops=self.max_hops,
                focus_paths=self.record_focus_paths,
            )
            route_scopes = []
            if self.record_policy.schema_region_routing is not None:
                routing_plan = compile_schema_region_routing(
                    self.ontology,
                    root_class_iri,
                    analysis_scope_ref=discovery_catalog.analysis_scope_ref,
                    max_hops=self.max_hops,
                    focus_paths=self.record_focus_paths,
                )
                route_scopes = [
                    # Independent exploration includes descendant classes; each
                    # registered entity later receives its own frozen menu.
                    (card.routing_card_id,
                     card.class_iris if self.independent_entity_traversal
                     else card.range_class_iris)
                    for card in routing_plan.routing_cards
                ]
            else:
                route_scopes = [(None, sorted(discovery_catalog.class_depths))]

            execution_ids_by_route = {}
            for route_id, classes in route_scopes:
                route_card_ids = []
                for card in compile_partitioned_record_schema_cards(
                    self.ontology,
                    class_iris=classes,
                    analysis_scope_ref=discovery_catalog.analysis_scope_ref,
                    profile=self.adapter.profile,
                    max_classes_per_card=self.record_policy.max_classes_per_card,
                    max_card_tokens=self.adapter.max_input_tokens // 3,
                    token_counter=self.adapter.token_counter,
                ):
                    discovery_cards[card.schema_card_id] = card
                    route_card_ids.append(card.schema_card_id)
                if route_id is not None:
                    execution_ids_by_route[route_id] = sorted(set(route_card_ids))
            if routing_plan is not None:
                routing_plan = routing_plan.model_copy(update={
                    "routing_cards": [
                        card.model_copy(update={
                            "execution_card_ids": execution_ids_by_route[card.routing_card_id],
                        })
                        for card in routing_plan.routing_cards
                    ],
                })
        contextual = self.record_policy.contextual if self.record_policy else None
        region_property_fields = self.evidence_review or bool(
            self.record_policy
            and self.record_policy.schema_region_routing
            and self.record_policy.schema_region_routing.property_field_mode == "region_batch"
        )
        ordinary_cards = list(discovery_cards)
        discovery_slots = []
        attribute_fields = {}
        deferred_fields = {}
        reading_sources = {}
        reading_sections = {}
        if contextual is not None:
            from .attribute_disambiguation import compile_attribute_card, extract_attribute_fields
            from .reading_groups import build_reading_groups, build_section_paragraph_groups
            from .table_reading import table_reading_groups

            ordinary_cards = list(discovery_cards)
            labels = {prop.label for card in discovery_cards.values() for prop in card.predicates}
            groups = build_reading_groups(
                index, field_labels=labels, max_section_chars=contextual.max_section_chars,
                max_group_chars=contextual.max_group_chars,
                max_records=contextual.max_group_records,
            )
            for group in groups:
                for identity in group.record_ids:
                    reading_sources[identity] = list(group.record_ids)
                    reading_sections[identity] = list(group.section_node_ids)
            reserved = {
                identity for group in groups for identity in group.record_ids
            } | {
                identity for identities in index.table_lead_ins.values()
                for identity in identities
            }
            section_groups = build_section_paragraph_groups(
                index,
                max_group_chars=min(
                    contextual.max_group_chars,
                    self.record_policy.schema_region_routing.max_group_chars,
                ) if self.record_policy.schema_region_routing else contextual.max_group_chars,
                max_records=min(
                    contextual.max_group_records,
                    self.record_policy.schema_region_routing.max_group_records,
                ) if self.record_policy.schema_region_routing else contextual.max_group_records,
                excluded_record_ids=reserved,
            )
            for group in section_groups:
                for identity in group.record_ids:
                    reading_sources[identity] = list(group.record_ids)
                    reading_sections[identity] = list(group.section_node_ids)
            for sources in table_reading_groups(
                index, max_group_chars=contextual.max_group_chars,
                max_records=contextual.max_group_records,
                max_data_rows=contextual.max_table_rows_per_group,
            ):
                for identity in sources:
                    reading_sources[identity] = list(sources)
                    # Only the bounded table batch is authorized, never the
                    # whole section or rows outside this group.
                    reading_sections[identity] = []
            for record in index.records:
                reading_sources.setdefault(record.record_id, [record.record_id])
            for field in ([] if self.candidate_graph else extract_attribute_fields(
                index, property_labels=labels, reading_groups=groups,
                include_unmapped=self.evidence_review,
            )):
                attribute_fields[field.field_id] = field
                # A merged table cell may belong to multiple logical rows. All
                # those views must defer the same physical value, not just its
                # canonical field record.
                field_records = {field.record_id}
                for ref in field.value_refs:
                    field_records.update(
                        record.record_id
                        for record in index.records_by_evidence.get(ref.evidence_id, [])
                        if any(unit.evidence_id == ref.evidence_id for unit in record.source_units)
                    )
                for identity in sorted(field_records):
                    deferred_fields.setdefault(identity, []).append(field)
            exclusive = ({field.record_id for field in attribute_fields.values()
                          if field.exclusive_record} if not region_property_fields else set())
            # Legacy frozen runs schedule a physical field separately. New
            # region-batch runs attach its exact field card to the owning source
            # group so entity discovery and property ownership share one call.
            field_slots_by_group = {}
            field_cards_by_group = {}
            for field in attribute_fields.values():
                if self.evidence_review:
                    # Reading aids do not add classes or predicates to the routed card.
                    continue
                card = compile_attribute_card(
                    field, self.ontology, class_iris=list(discovery_catalog.class_depths),
                    analysis_scope_ref=discovery_catalog.analysis_scope_ref,
                    profile=self.adapter.profile,
                )
                discovery_cards[card.schema_card_id] = card
                group_key = tuple(reading_sources[field.record_id])
                if region_property_fields:
                    field_cards_by_group.setdefault(group_key, []).append(card.schema_card_id)
                else:
                    slot = (
                        [field.record_id], [card.schema_card_id], field.field_id,
                    )
                    field_slots_by_group.setdefault(group_key, []).append(slot)
            # Only source groups and unique fields exist before retrieval; the
            # ordinary groups contain no preallocated class-card combinations.
            admitted = set()
            pending_field_slots = []
            for record in index.records:
                group_key = tuple(reading_sources[record.record_id])
                if group_key in admitted:
                    continue
                admitted.add(group_key)
                sources = [identity for identity in group_key if identity not in exclusive]
                if sources:
                    discovery_slots.append((
                        sources,
                        sorted(set(field_cards_by_group.pop(group_key, []))),
                        None,
                    ))
                    if not region_property_fields:
                        discovery_slots.extend(pending_field_slots)
                        pending_field_slots = []
                        discovery_slots.extend(field_slots_by_group.pop(group_key, []))
                else:
                    if not region_property_fields:
                        pending_field_slots.extend(field_slots_by_group.pop(group_key, []))
            if not region_property_fields:
                discovery_slots.extend(pending_field_slots)
                for slots in field_slots_by_group.values():
                    discovery_slots.extend(slots)
        discovery_search = None
        if self.record_pipeline:
            from .record_search import RecordSearch

            if contextual is None:
                discovery_slots = [([record.record_id], [], None) for record in index.records]
            discovery_search = RecordSearch(
                discovery_slots, cards=discovery_cards, ordinary_cards=ordinary_cards,
                ontology=self.ontology, index=index, policy=self.record_policy,
                routing_cards=routing_plan.routing_cards if routing_plan is not None else (),
                metadata=metadata if routing_plan is not None else None,
            )
        frozen_layered = frozen_frontier.get("layered_recognition")
        if frozen_layered and frozen_layered.get("version") not in {
            LAYERED_RECOGNITION_VERSION, DEPENDENCY_READY_VERSION,
        }:
            raise ValueError("unsupported layered recognition policy")
        layered_recognition = (self.layered_recognition if resume_state is None
                               else frozen_layered is not None)
        if self.independent_entity_traversal:
            # Candidate exploration does not depend on proving an ancestor edge.
            layered_recognition = False
        dependency_ready = (self.source_object_recognition if resume_state is None else
                            (frozen_layered or {}).get("version") == DEPENDENCY_READY_VERSION)
        recognition_order = (DEPENDENCY_READY_VERSION if dependency_ready
                             else LAYERED_RECOGNITION_VERSION)
        frontier_version = frozen_frontier.get("schema_version", 1)
        if frontier_version not in (1, 2):
            raise ValueError("unsupported scheduler snapshot version")
        lazy_frontier = (
            self.lazy_frontier if resume_state is None else frontier_version == 2
        )
        template_interleaving = (
            self.template_interleaving if resume_state is None
            else frozen_frontier.get("template_interleaving", False)
        )
        scheduler = FrontierScheduler(
            max_hops=self.max_hops,
            max_tasks=self.max_tasks,
            phase_interleaving=(
                False if self.heuristic_policy is not None else
                ranking.policy.phase_interleaving
                if self.phase_interleaving is None
                else self.phase_interleaving
            ),
            template_interleaving=template_interleaving,
        )
        if self.current_state:
            scheduler.enable_current_state()
        task_budget_stopped_slots: set[tuple[str, int, str]] = set()

        def budget_blocked_slots() -> set[tuple[str, int, str]]:
            blocked = set(task_budget_stopped_slots)
            if scheduler.dispatched >= scheduler.max_tasks:
                blocked.update(scheduler.pending_slots)
                if search_index is not None:
                    blocked.update(
                        key for key, plan in plans.items()
                        if any(item.coverage_state != "examined" for item in plan.ledger.values())
                        or (self.candidate_policy and searches[key].status
                            not in {"pass_exhausted", "local_results_only"})
                    )
            return blocked

        dependency_index = DependencyIndex()
        if self.current_state:
            dependency_index.enable_current_state()
        plans = (WorkMap(encode=plan_header) if self.current_state else {})
        predicates = work_map()
        menus = work_map({root.entity_id: root_menu})
        subject_priorities = work_map({root.entity_id: self.priority_paths})
        events: list[tuple[str, dict]] = []
        diagnostics = [
            *root_menu.diagnostics,
            *((resume_state or {}).get("diagnostics") or []),
        ]
        proof_generations = work_map()
        ranking_contexts = (WorkMap(encode=lambda value: json_value({
            k: v for k, v in value.items() if k != "ontology"
        })) if self.current_state else {})
        searches = WorkMap(encode=lambda search: {
            "subject_mentions": search.subject_mentions, "seed_record_ids": search.seed_record_ids,
            "priority_record_ids": search.priority_record_ids,
            **({"permission_scope_hash": search.permission_scope_hash,
                "query_dependency_hash": search.query_dependency_hash}
               if self.adaptive_policy else {}),
        }) if self.current_state else {}
        search_tasks = work_map()
        semantic_plan_keys: dict[str, tuple] = {}
        active_hop = 0
        layer_phase = "ready" if dependency_ready else "property"
        registered_subjects = work_map({
            subject_key(root_subject, root_scope): {
                "subject": root_subject, "hop": 0, "binding_refs": [], "reopen": False,
                **({"scope": root_scope} if self.tool_protocol else {}),
            },
        })
        def scope_for_key(key):
            return registered_subjects[key[:-1]]["scope"] if self.tool_protocol else None

        def slot_subject_is_active(key):
            return subject_is_active(plans[key].subject, scope_for_key(key))

        def task_key(task):
            return slot_key(task.subject, task.predicate_iri, task.scope)

        def subject_dependency_ref(subject, scope):
            identity = (stable_id("subject-scope", [subject.entity_id, scope.scope_id])
                        if self.tool_protocol else f"subject:{subject.entity_id}")
            return VersionedRef(id=identity, revision=subject.revision)

        def scoped_subject_dependency(subject, scope):
            ref = subject_dependency_ref(subject, scope)
            return f"{ref.id}@{ref.revision}"

        slot_completion = SlotCompletionIndex(index)
        if self.current_state:
            slot_completion.receipts = WorkMap()
        slot_search_replay = SlotSearchReplay()
        task_outcomes: list[dict] = []
        nodes = work_map({root.entity_id: root})
        edges = work_map()
        properties = work_map()
        relationship_groups = work_map()
        admission_log: list[dict] = []
        frozen_repair = frozen_frontier.get("evidence_repair", {})
        review_replay = ReviewReplay(frozen_frontier.get("expert_review"))
        expert_pending: list[RecognitionTask] = []
        expert_operations = work_map()
        expert_lineages = work_map()
        expert_rejected = work_map()
        expert_reopened_slots: set[tuple] = set()
        expert_review_heads = work_map()
        reviews_admitted = False
        semantic_wait_key: tuple | None = None
        semantic_wait_started_at = 0

        def search_event(kind: str, payload: dict) -> None:
            events.append((kind, payload))
            if self.search_hook is not None:
                self.search_hook(kind, payload)

        def slot_is_active(key):
            if dependency_ready or not layered_recognition or key in expert_reopened_slots:
                return True
            registration = registered_subjects.get(key[:-1])
            if (
                self.record_pipeline
                and registration
                and registration["binding_refs"]
                and predicates[key].kind == "property"
            ):
                # Entity-only region discovery intentionally omits attributes.
                # Once an exact incoming relation proves the entity's graph
                # membership, its still-missing local properties may run
                # without waiting for every unrelated root relation to finish.
                return subject_is_active(plans[key].subject, registration.get("scope"))
            return bool(registration and registration["hop"] == active_hop
                        and predicates[key].kind == layer_phase)

        def prepare_slot_completion(key):
            if not layered_recognition or not self.candidate_policy:
                return False, None
            plan, search = plans[key], searches[key]
            candidate = single_value_candidate(
                predicates[key], plan.subject, properties.values(), dependency_index,
            )
            receipt = slot_completion.get(key)
            if receipt is not None:
                if (candidate is not None
                        and receipt["candidate_ref"] == {
                            "id": candidate.candidate_id, "revision": candidate.revision,
                        }
                        and slot_subject_is_active(key)):
                    return True, None
                slot_completion.reopen(key)
                search.reopen_satisfied()
                events.append(("slot_completion_reopened", {
                    "slot": list(key), "reason": "bound_proof_invalidated",
                }))
            if candidate is None:
                return False, None
            if not slot_subject_is_active(key):
                return False, None
            # Already-admitted work is an actual obligation. Wait for its real
            # verification, preserving failures/retries and coverage unchanged.
            if (key in scheduler.pending_slots
                    or any(plan.ledger[rid].coverage_state != "examined"
                           for rid in search.admitted)):
                return True, None
            if any(
                item.get("slot") == [key[0], key[-1]]
                and item["status"] in {"queued", "incomplete", "deferred"}
                for item in evidence_work.items.values()
            ) or any(
                candidate_tasks.get(claim) is not None
                and candidate_tasks[claim].subject == plan.subject
                and candidate_tasks[claim].predicate_iri == key[-1]
                for claim in conflict_claims
            ):
                return True, None
            required = list(dict.fromkeys([
                *slot_completion.conflict_records(predicates[key]),
                *sorted(search.admitted, key=index.record_positions.__getitem__),
            ]))
            missing = [rid for rid in required if rid not in search.admitted]
            if missing:
                return True, search.admit_conflict_check(
                    missing, trigger_ref=f"{candidate.candidate_id}@{candidate.revision}",
                )
            receipt = slot_completion.close(
                key, plan=plan, candidate=candidate, checked_record_ids=required,
                after_outcomes=completed_tasks,
            )
            search.close_satisfied()
            expert_reopened_slots.discard(key)
            events.append(("slot_search_satisfied", receipt))
            return True, None

        def admit_search_page(key: tuple) -> bool:
            if not slot_is_active(key):
                return False
            started = time.perf_counter()
            handled, page = prepare_slot_completion(key)
            if not handled:
                page = searches[key].next_admission()
            if page is None:
                return False
            plan = plans[key]
            if self.candidate_policy:
                epoch = ranking.epochs_by_id.get(page.epoch_id)
                plan = admit_records(
                    plan, index, page.record_ids, phase=1 if page.stage in {"H0", "H1"} else 2,
                    epoch=epoch,
                )
                plans[key] = plan
                searches[key].plan = plan
            by_id = {record.record_id: record for record in plan.records}
            search_event("heuristic_admission", {
                "plan_id": plan.plan_id, "subject_id": key[0], "predicate_iri": key[-1],
                "stage": page.stage, "source": page.source, "reason": page.reason,
                "record_ids": list(page.record_ids),
                "context_record_ids": page.context_record_ids,
                "elapsed_seconds": time.perf_counter() - started,
            })
            page_tasks = []
            source_priorities = {}
            for position, record_id in enumerate(page.record_ids, 1):
                record = by_id[record_id]
                task = RecognitionTask.create(
                    subject=plan.subject, predicate_iri=key[-1],
                    predicate_kind=predicates[key].kind, record_id=record_id,
                    phase=record.phase, section_node_id=record.section_node_id,
                    source_position=index.record_positions[record_id], **search_tasks[key],
                )
                task.pool_rank = position
                scheduler.enqueue(
                    task, root_branch=plan.subject.is_document_root,
                    template_priority=bool(self.evidence_repair and template_interleaving and any(
                        path and path[0] == key[-1]
                        for path in subject_priorities.get(plan.subject.entity_id, [])
                    )),
                )
                if self.incremental_performance:
                    sources = attribute_sources.get(key, {}).get(record_id)
                    if sources:
                        scheduler.prioritize_source(task, sources)
                        source_priorities[task.task_id] = sources
                page_tasks.append(task.model_dump(mode="json"))
            if self.evidence_repair and not self.current_state:
                admission_log.append({
                    "batch_id": page.batch_id, "after_outcomes": completed_tasks,
                    "tasks": page_tasks,
                    **({"ranking_epoch_id": page.epoch_id} if self.candidate_policy else {}),
                    **({"source_priorities": source_priorities}
                       if self.incremental_performance else {}),
                })
            return True

        attribute_sources = work_map()

        def add_subject(
            subject: SubjectRef,
            menu: LocalMenu,
            *,
            hop: int,
            reopen: bool = False,
            binding_edge: GraphEdge | GraphRelationshipGroup | None = None,
            scope: TraversalScope | None = None,
        ) -> None:
            if reopen:
                scheduler.discard_subject(subject, scope=scope)
                proof_generations[subject.entity_id] = (
                    proof_generations.get(subject.entity_id, 0) + 1
                )
                for old_key in list(plans):
                    if old_key[:-1] == subject_key(subject, scope):
                        previous = plans.pop(old_key)
                        if self.current_state:
                            searches.pop(old_key, None)
                            predicates.pop(old_key, None)
                            search_tasks.pop(old_key, None)
                            ranking_contexts.pop(previous.plan_id, None)
                            slot_completion.receipts.pop(old_key, None)
                        events.append(
                            (
                                "retrieval_plan_superseded",
                                {
                                    "plan": previous.model_dump(mode="json"),
                                    "reason": "subject_proof_revalidated",
                                },
                            )
                        )
            if binding_edge is not None:
                subject_priorities[subject.entity_id] = list(dict.fromkeys([
                    *subject_priorities.get(subject.entity_id, []),
                    *(path[1:] for path in subject_priorities.get(binding_edge.subject_ref.id, [])
                      if len(path) > 1 and path[0] == binding_edge.predicate_iri),
                ]))
            paths = subject_priorities.get(subject.entity_id, [])
            preferred = {path[0]: i for i, path in reversed(list(enumerate(paths))) if path}
            if self.incremental_performance and binding_edge is not None:
                from app.services.extraction.ontology_guided.source_priorities import (
                    explicit_attribute_sources,
                )

                owner_refs = nodes[subject.entity_id].evidence_refs
                source_records = dict.fromkeys(
                    record.record_id for anchor in owner_refs
                    for record in index.records_by_evidence.get(anchor.evidence_id, [])
                )
                if dependency_ready:
                    source_records = dict.fromkeys(
                        [*source_records, *(rid for owner_record in source_records
                          for group in index.field_groups_by_record.get(owner_record, ())
                          for rid in group.record_ids)]
                    )
                for slot in menu.properties:
                    attribute_sources[slot_key(subject, slot.iri, scope)] = {
                        rid: sources for rid in source_records
                        if (sources := explicit_attribute_sources(index, rid, slot, owner_refs))
                    }
            subject_ref = VersionedRef(id=subject.entity_id, revision=subject.revision)

            def pending_calibration(predicate_iri: str) -> bool:
                """Leave source observations to their existing calibration lineage."""
                expected = subject_ref.model_dump(mode="json")
                return any(
                    candidate.get("status") in {"pending", "rejected_mapping"}
                    and any(
                        option.get("subject_ref") == expected
                        and option.get("predicate_iri") == predicate_iri
                        for option in candidate.get("options", [])
                    )
                    for row in record_discovery.values()
                    for candidate in row.get("attribute_candidates", [])
                )

            missing_properties = [
                predicate for predicate in menu.properties
                if not any(
                    candidate.subject_ref == subject_ref
                    and candidate.predicate_iri == predicate.iri
                    and candidate.decision_status == "supported"
                    and effective_proof_gate(candidate)
                    and dependency_index.is_valid(versioned_key(
                        candidate.candidate_id, candidate.revision,
                    ))
                    for candidate in properties.values()
                )
                and not pending_calibration(predicate.iri)
            ]
            ordered_menu = sorted([
                *menu.relationships,
                *(
                    missing_properties
                    if not self.candidate_graph
                    and (self.evidence_review or not self.record_pipeline
                         or binding_edge is not None)
                    else []
                ),
            ],
                                  key=lambda item: (
                                      not bool(attribute_sources.get(
                                          slot_key(subject, item.iri, scope))),
                                      preferred.get(item.iri, len(paths)),
                                  ))
            # Preferences only determine the first opportunity in each rotation;
            # the full menu, both phases and source exploration remain scheduled.
            for predicate in ordered_menu:
                if self.record_pipeline and predicate.kind == "relationship" and not any(
                    source == subject.class_iri and iri == predicate.iri
                    for source, iri, _target, _depth in discovery_catalog.relation_routes
                ):
                    continue
                bound_record_property = (
                    self.record_pipeline
                    and binding_edge is not None
                    and predicate.kind == "property"
                )
                if (layered_recognition and not dependency_ready
                        and predicate.kind != layer_phase and not bound_record_property):
                    continue
                if self.predicate_filter is not None and not self.predicate_filter(
                    subject, predicate, hop
                ):
                    continue
                key = slot_key(subject, predicate.iri, scope)
                if key in plans:
                    if template_interleaving and predicate.iri in preferred:
                        scheduler.prioritize_slot(subject, predicate.iri, scope=scope)
                    continue
                plan = plan_slot(
                    subject,
                    predicate,
                    index,
                    metadata,
                    ontology_hash=self.ontology.ontology_hash,
                    phase1_section_limit=self.phase1_section_limit,
                    sparse_candidates=bool(self.candidate_policy),
                )
                generation = proof_generations.get(subject.entity_id, 0)
                if self.tool_protocol:
                    plan.plan_id = stable_id("retrieval-plan-scope", [plan.plan_id, scope.scope_id])
                if generation:
                    plan.plan_id = stable_id(
                        "retrieval-plan-proof-generation",
                        [
                            plan.plan_id,
                            generation,
                        ],
                    )
                validate_record_universe(plan, index)
                if self.current_state:
                    plan = enable_plan_parts(plan)
                plan_dependencies = (
                    [
                        VersionedRef(
                            id=binding_edge.candidate_id, revision=binding_edge.revision,
                        ),
                        binding_edge.subject_ref,
                        *(
                            binding_edge.object_refs
                            if isinstance(binding_edge, GraphRelationshipGroup)
                            else [binding_edge.object_ref]
                        ),
                        *([binding_edge.proof_ref] if binding_edge.proof_ref else []),
                        *binding_edge.decision_refs,
                        *binding_edge.dependency_refs,
                    ]
                    if binding_edge else []
                )
                if self.tool_protocol:
                    plan_dependencies.extend(
                        ref for step in scope.members
                        for ref in (step.relation_ref, step.member_ref)
                    )
                semantic_key = semantic_retrieval_plan_key(
                    plan, dependency_refs=plan_dependencies, generation=generation,
                )
                equivalent = semantic_plan_keys.get(semantic_key)
                if equivalent is not None and equivalent != key:
                    events.append(("retrieval_plan_deduplicated", {
                        "plan_id": plan.plan_id,
                        "canonical_slot": list(equivalent),
                        "duplicate_slot": list(key),
                        "semantic_plan_key": semantic_key,
                    }))
                    continue
                semantic_plan_keys[semantic_key] = key
                plans[key] = plan
                # Physical mentions keep their original immutable observation.
                # Admission through a complete exact incoming assertion supplies
                # the trust for this semantic plan, including its proof lineage.
                ranking_contexts[plan.plan_id] = {
                    **({"ontology": self.ontology} if self.ontology.lexical_context is not None
                       else {}),
                    "mentions": [
                        QueryMention(
                            text=index.ir.resolve(anchor),
                            source_refs=[anchor],
                            trust_state="supported",
                        )
                        for anchor in (
                            nodes[subject.entity_id].evidence_refs
                            if binding_edge or self.record_pipeline else []
                        )
                    ],
                    "dependency_refs": list(plan_dependencies),
                }
                predicates[key] = predicate
                events.append(("retrieval_plan_created", plan.model_dump(mode="json")))
                dependency_hash = evidence_hash([
                    subject.model_dump(mode="json"), predicate.model_dump(mode="json"),
                    ir.analysis_id, menu.menu_id, generation,
                    *([scope.scope_id] if self.tool_protocol else []),
                ])
                if search_index is not None:
                    started = time.perf_counter()
                    search_type = HeuristicSlotSearch
                    adaptive_arguments = {}
                    if self.adaptive_policy:
                        from app.services.extraction.ontology_guided.adaptive_search import (
                            AdaptiveSlotSearch,
                        )
                        from app.services.extraction.ontology_guided.retrieval_query import (
                            build_subject_queries,
                        )

                        search_type = AdaptiveSlotSearch
                        queries = build_subject_queries(
                            subject=plan.subject, subject_node=(root if subject.is_document_root
                                                              else nodes[subject.entity_id]),
                            predicate=predicate, index=index, run_fingerprint=run_fingerprint,
                            root_ref=VersionedRef(id=root.entity_id, revision=root.revision),
                            root_class_iri=root_class_iri, **ranking_contexts[plan.plan_id],
                        )
                        adaptive_arguments = {
                            "adaptive_policy": self.adaptive_policy,
                            "permission_scope_hash": evidence_hash(recognition_run_id),
                            "query_dependency_hash": queries[0].query_dependency_hash,
                        }
                    tool_attribute = self.tool_protocol and predicate.kind == "property"
                    seed_record_ids = list(dict.fromkeys(
                        record.record_id
                        for anchor in (nodes[subject.entity_id].evidence_refs
                                       if binding_edge and (self.evidence_repair or tool_attribute)
                                       else [])
                        for record in index.records_by_evidence.get(anchor.evidence_id, [])
                    ))
                    # A proved entity's exact source can express several fields
                    # without their ontology labels. Prioritize it for discovery;
                    # each property still requires its own claim and verification.
                    priority_record_ids = list(dict.fromkeys([
                        *attribute_sources.get(key, {}),
                        *(seed_record_ids if tool_attribute else []),
                    ]))
                    searches[key] = search_type(
                        plan=plan, predicate=predicate, search_index=search_index,
                        policy=self.heuristic_policy,
                        priority_record_ids=priority_record_ids,
                        run_fingerprint=run_fingerprint,
                        seed_record_ids=seed_record_ids,
                        subject_mentions=[
                            item.text for item in ranking_contexts[plan.plan_id]["mentions"]
                        ],
                        **adaptive_arguments,
                    )
                    if self.current_state:
                        enable_search_parts(searches[key])
                        searches[key].on_work_change = lambda k=key: searches.touch(k)
                    search_event("heuristic_slot_prepared", {
                        "plan_id": plan.plan_id, "predicate_iri": predicate.iri,
                        "elapsed_seconds": time.perf_counter() - started,
                    })
                    search_tasks[key] = {"hop": hop, "dependency_hash": dependency_hash,
                                         **({"scope": scope} if self.tool_protocol else {})}
                    admit_search_page(key)
                    continue
                if lazy_frontier:
                    scheduler.enqueue_plan(
                        plan, hop=hop, dependency_hash=dependency_hash,
                        source_positions=index.record_positions,
                        root_branch=subject.is_document_root,
                        template_priority=predicate.iri in preferred,
                        scope=scope,
                    )
                    continue
                for record in plan.records:
                    scheduler.enqueue(
                        RecognitionTask.create(
                            subject=subject,
                            predicate_iri=predicate.iri,
                            predicate_kind=predicate.kind,
                            record_id=record.record_id,
                            phase=record.phase,
                            hop=hop,
                            dependency_hash=dependency_hash,
                            section_node_id=record.section_node_id,
                            source_position=index.record_positions[record.record_id],
                            scope=scope,
                        ),
                        root_branch=subject.is_document_root,
                    )

        if direct is None:
            add_subject(root_subject, root_menu, hop=0, scope=root_scope)
        model_calls = 0
        lineage_calls = work_map()
        candidate_tasks = work_map()
        required_by_lineage = work_map()
        recheck_counts = work_map()
        conflict_events: set[str] = set()
        conflict_claims: set[str] = set()
        resume_outcomes = list((resume_state or {}).get("task_outcomes") or [])
        resume_cursor = 0
        resume_validated = resume_state is None or direct is not None
        plan_exports = {}

        def current_recall_ledger():
            if not self.incremental_performance:
                return {p.plan_id: p.model_dump(mode="json") for p in plans.values()}
            # Plans are revised by copy-on-write mark_record/apply_epoch. Hold
            # the actual previous object (not id alone) so object-id reuse is safe.
            for plan in plans.values():
                cached = plan_exports.get(plan.plan_id)
                if cached is None or cached[0] is not plan:
                    plan_exports[plan.plan_id] = (plan, freeze_json(plan.model_dump(mode="json")))
            return {p.plan_id: plan_exports[p.plan_id][1] for p in plans.values()}

        def current_frontier():
            state = scheduler.snapshot()
            if layered_recognition:
                state["layered_recognition"] = {
                    "version": recognition_order,
                    "active_hop": active_hop,
                    "phase": layer_phase,
                    "subjects": [
                        {**item, "subject": item["subject"].model_dump(mode="json")}
                        for _key, item in sorted(registered_subjects.items())
                    ],
                    "slot_completion": slot_completion.snapshot(),
                }
            if review_replay.events:
                state["expert_review"] = {
                    **review_replay.snapshot(),
                    "pending": [t.model_dump(mode="json") for t in expert_pending],
                    "operations": deepcopy(expert_operations),
                    "reopened_slots": [list(key) for key in sorted(expert_reopened_slots)],
                }
            if self.candidate_policy:
                state["candidate_planning"] = {
                    "policy": self.candidate_policy, "search_scope_ref": shared_scope(index)[2],
                }
            if self.evidence_repair:
                state["evidence_repair"] = {
                    "version": "evidence-repair-v1", "work": evidence_work.snapshot(),
                    "admissions": deepcopy(admission_log),
                    "searches": {s.plan.plan_id: s.snapshot() for s in searches.values()},
                }
            return state

        def persist_call_boundary(result_changes=None, *, work_changes=None):
            nonlocal reservation_saved
            if not self.current_state:
                model_call_hook(current_model_call_state())
                return
            model_call_hook({
                "current_calls": 1, "version": call_state_version,
                "recognition_run_id": recognition_run_id, "run_fingerprint": run_fingerprint,
                "lineage_calls": reserved_calls.drain(), "protocols": protocols.drain(),
                "reservations": reservations[reservation_saved:],
                "reservation_sequence": reservation_sequence,
                **({"result_changes": result_changes} if result_changes else {}),
                **({"work_changes": work_changes} if work_changes is not None else {}),
            })
            reservation_saved = len(reservations)

        def current_model_call_state() -> dict:
            return {
                "version": call_state_version,
                "recognition_run_id": recognition_run_id,
                "run_fingerprint": run_fingerprint,
                "lineage_calls": dict(reserved_calls),
                **({"reservation_sequence": reservation_sequence} if self.batch_policy else {}),
                "reservations": list(reservations) if self.incremental_performance else
                deepcopy(reservations),
                **({"protocols": dict(protocols) if self.incremental_performance else
                    deepcopy(protocols)} if has_protocol else {}),
            }

        call_totals = [0, 0, 0, 0]  # reserved, confirmed, unresolved, deferred types
        call_lineage_totals = {}

        def update_call_totals(lineage):
            if self.batch_policy is not None:
                confirmed = len(protocols.get(lineage, {}).get("completed_attempts", []))
                call_totals[1] += confirmed - call_lineage_totals.get(lineage, 0)
                call_lineage_totals[lineage] = confirmed
                call_totals[0] = reservation_sequence
                call_totals[2] = max(0, reservation_sequence - call_totals[1])
                return
            if not self.current_state:
                return
            protocol = protocols.get(lineage, {})
            reserved = reserved_calls.get(lineage, 0)
            confirmed = max(lineage_calls.get(lineage, 0),
                            len(protocol.get("completed_attempts", [])))
            values = (reserved, confirmed, max(0, reserved - confirmed),
                      len(protocol.get("deferred_types", [])))
            old = call_lineage_totals.get(lineage, (0, 0, 0, 0))
            for i, value in enumerate(values):
                call_totals[i] += value - old[i]
            call_lineage_totals[lineage] = values

        def protocol_checkpoint(task, state, *, durable=True):
            if isinstance(task, RecognitionWorkUnit):
                batch_protocol_checkpoint(task, state)
                return
            if not has_protocol or state.get("lineage_id") != task.claim_lineage_id:
                raise ModelCallPersistenceFailure("protocol checkpoint lineage mismatch")
            state = dict(state)
            result_changes = state.pop("result_changes", None)
            if self.tool_protocol and state.get("pending_request") is not None:
                if result_changes:
                    raise ModelCallPersistenceFailure("pending request cannot publish results")
                pending = state["pending_request"]
                reserve_model_call(task, pending["stage"], pending["attempt"], protocol_state=state)
                return
            protocols[task.claim_lineage_id] = (freeze_json(state) if self.incremental_performance
                                              else deepcopy(state))
            update_call_totals(task.claim_lineage_id)
            if durable and model_call_hook is not None:
                try:
                    persist_call_boundary(result_changes)
                except Exception as exc:
                    raise ModelCallPersistenceFailure(
                        "protocol checkpoint persistence failed"
                    ) from exc
            if self.tool_protocol and result_changes:
                if protocol_result_loader is None or not durable:
                    local_protocol_results.update(deepcopy(result_changes))
                if (durable and self.progress_hook is not None
                        and not self.progress_hook("after_protocol_result")):
                    raise ModelCallPauseRequested("paused after confirmed protocol result")

        def remaining_calls(task: RecognitionTask) -> int:
            used = max(
                lineage_calls.get(task.claim_lineage_id, 0),
                reserved_calls.get(task.claim_lineage_id, 0),
            )
            remaining = max(0, self.max_model_calls_per_record - used)
            operation_id = expert_lineages.get(task.claim_lineage_id)
            if operation_id:
                operation = expert_operations[operation_id]
                spent = sum(max(lineage_calls.get(lineage, 0), reserved_calls.get(lineage, 0))
                            for lineage, op in expert_lineages.items() if op == operation_id)
                remaining = min(remaining, max(0, operation["max_model_calls"] - spent))
            return remaining

        def reserve_model_call(
            task: RecognitionTask, stage: str, ordinal: int, *, protocol_state=None,
        ) -> None:
            nonlocal reservation_sequence
            if isinstance(task, RecognitionWorkUnit):
                reserve_batch_model_call(task, stage, ordinal, protocol_state=protocol_state)
                return
            if self.tool_protocol:
                state = protocol_state or protocols.get(task.claim_lineage_id, {})
                pending = state.get("pending_request")
                if (pending is None or pending["attempt"] != ordinal or pending["stage"] != stage
                        or state["lineage_id"] != task.claim_lineage_id):
                    raise ModelCallPersistenceFailure("request reservation identity mismatch")
                prior = next((receipt for receipt in reservations
                              if receipt["lineage_id"] == task.claim_lineage_id
                              and receipt.get("protocol_attempt") == ordinal), None)
                if prior is not None:
                    if (prior["task_id"] != task.task_id or prior["stage"] != stage
                            or prior.get("input_hash") != pending["request_hash"]
                            or call_request_key(prior) != pending["reservation_key"]
                            or (protocol_state is not None and json_value(
                                protocols.get(task.claim_lineage_id, {})
                            ) != json_value(protocol_state))):
                        raise ModelCallPersistenceFailure("request reservation identity changed")
                    return
                if protocol_state is None:
                    raise ModelCallPersistenceFailure("request reservation was not confirmed")
            if (self.progress_hook is not None
                    and not self.progress_hook("before_model_reservation" if self.tool_protocol
                                               else "before_model")):
                raise ModelCallPauseRequested("execution paused before the model request")
            if not remaining_calls(task):
                if task.claim_lineage_id in expert_lineages:
                    raise ExpertRepairBudgetExhausted("expert repair model call budget exhausted")
                raise ModelCallPauseRequested("record model call budget exhausted")
            lineage = task.claim_lineage_id
            if self.tool_protocol:
                # Publish the exact pending wire and its reservation together. A
                # denied budget cannot leave a durable, unreserved pending attempt.
                protocols[lineage] = (freeze_json(protocol_state) if self.incremental_performance
                                      else deepcopy(protocol_state))
            reserved_calls[lineage] = max(
                reserved_calls.get(lineage, 0), lineage_calls.get(lineage, 0)
            ) + 1
            reservation_sequence += 1
            update_call_totals(lineage)
            reservations.append(
                {
                    "sequence": reservation_sequence,
                    "task_id": task.task_id,
                    "stage": stage,
                    "ordinal": ordinal,
                    "lineage_id": lineage,
                    **({"input_hash": protocols.get(lineage, {}).get(
                        "pending_request", {}).get("request_hash"),
                        **({"record_task_id": task.task_id, "record_id": task.record_id,
                            "schema_card_id": task.schema_card_id}
                           if isinstance(task, RecordDiscoveryTask) else
                           {"subject_ref": task.subject.model_dump(mode="json")}),
                        "run_fingerprint": run_fingerprint}
                       if self.current_state else {}),
                    **({"protocol_attempt": protocols.get(lineage, {}).get("request_attempt")}
                       if has_protocol else {}),
                }
            )
            if self.incremental_performance:
                reservations[-1] = freeze_json(reservations[-1])
            if model_call_hook is not None:
                try:
                    persist_call_boundary()
                except Exception as exc:
                    raise ModelCallPersistenceFailure(
                        "model call reservation persistence failed"
                    ) from exc

        def batch_protocol_checkpoint(unit, state, *, durable=True):
            if state.get("lineage_id") != unit.work_unit_id:
                raise ModelCallPersistenceFailure("batch checkpoint owner mismatch")
            state = deepcopy(state)
            result_changes = state.pop("result_changes", {})
            if state.get("pending_request") is not None:
                if result_changes or not durable:
                    raise ModelCallPersistenceFailure("pending batch cannot publish results")
                pending = state["pending_request"]
                reserve_batch_model_call(unit, pending["stage"], pending["attempt"],
                                         protocol_state=state)
                return
            protocols[unit.work_unit_id] = (freeze_json(state) if self.incremental_performance
                                            else deepcopy(state))
            remember_tool_costs(state)
            local_protocol_results.update(deepcopy(result_changes))
            update_call_totals(unit.work_unit_id)
            if durable and model_call_hook is not None:
                try:
                    persist_call_boundary(result_changes)
                except Exception as exc:
                    raise ModelCallPersistenceFailure(
                        "batch checkpoint persistence failed"
                    ) from exc
                if protocol_record_loader is not None:
                    for reference in result_changes:
                        local_protocol_results.pop(reference, None)
            if (durable and result_changes and self.progress_hook is not None
                    and not self.progress_hook("after_protocol_result")):
                raise ModelCallPauseRequested("paused after confirmed batch result")

        def reserve_batch_model_call(unit, stage, ordinal, *, protocol_state=None):
            nonlocal reservation_sequence
            state = protocol_state or protocols.get(unit.work_unit_id, {})
            pending = state.get("pending_request")
            if (pending is None or pending["attempt"] != ordinal or pending["stage"] != stage
                    or state["lineage_id"] != unit.work_unit_id):
                raise ModelCallPersistenceFailure("batch reservation identity mismatch")
            member_ids = list(state["stage_member_ids"])
            members = {member.task_id: member for member in unit.members}
            if not member_ids or not set(member_ids) <= set(members):
                raise ModelCallPersistenceFailure("batch reservation members invalid")
            prior = next((r for r in reservations if r["lineage_id"] == unit.work_unit_id
                          and r.get("protocol_attempt") == ordinal), None)
            if prior is not None:
                if (prior["member_task_ids"] != member_ids or prior["stage"] != stage
                        or prior.get("input_hash") != pending["request_hash"]
                        or prior["stage_group_seq"] != state["stage_group_seq"]
                        or call_request_key(prior) != pending["reservation_key"]
                        or protocol_state is not None and json_value(
                            protocols.get(unit.work_unit_id, {})
                        ) != json_value(protocol_state)):
                    raise ModelCallPersistenceFailure("batch reservation identity changed")
                return
            if protocol_state is None:
                raise ModelCallPersistenceFailure("batch reservation was not confirmed")
            if (self.progress_hook is not None
                    and not self.progress_hook("before_model_reservation")):
                raise ModelCallPauseRequested("execution paused before batch request")
            if any(not remaining_calls(members[identity]) for identity in member_ids):
                raise ModelCallPauseRequested("member model call budget exhausted")
            protocols[unit.work_unit_id] = (freeze_json(state) if self.incremental_performance
                                            else deepcopy(state))
            for identity in member_ids:
                lineage = members[identity].claim_lineage_id
                reserved_calls[lineage] = reserved_calls.get(lineage, 0) + 1
            reservation_sequence += 1
            receipt = {
                "sequence": reservation_sequence, "task_id": unit.work_unit_id,
                "stage": stage, "ordinal": ordinal, "lineage_id": unit.work_unit_id,
                "protocol_attempt": ordinal, "input_hash": pending["request_hash"],
                "subject_ref": unit.members[0].subject.model_dump(mode="json"),
                "run_fingerprint": run_fingerprint,
                "member_task_ids": member_ids,
                "member_lineage_ids": [members[i].claim_lineage_id for i in member_ids],
                "stage_group_seq": state["stage_group_seq"],
            }
            reservations.append(freeze_json(receipt) if self.incremental_performance else receipt)
            update_call_totals(unit.work_unit_id)
            if model_call_hook is not None:
                try:
                    persist_call_boundary()
                except Exception as exc:
                    raise ModelCallPersistenceFailure(
                        "batch reservation persistence failed"
                    ) from exc

        def persist_ranking_boundary():
            nonlocal discarded_saved
            if not self.current_state:
                ranking_hook(current_ranking_state())
                return
            payload = pending_service_state or ranking.current_changes()
            payload = {**payload, "recognition_run_id": recognition_run_id,
                       "run_fingerprint": run_fingerprint,
                       "committed_at": committed_at.drain(),
                       "discarded_epochs": {
                           str(i): {"key": i, "value": discarded_epochs[i]}
                           for i in range(discarded_saved, len(discarded_epochs))}}
            ranking_hook(payload)
            discarded_saved = len(discarded_epochs)

        def current_ranking_state() -> dict:
            return {
                "recognition_run_id": recognition_run_id,
                "run_fingerprint": run_fingerprint,
                "service": ((pending_service_state["changes"]["control"]["current"]
                             if pending_service_state.get("current_ranking")
                             else pending_service_state)
                            if pending_service_state else ranking.snapshot()),
                "committed_at": dict(committed_at),
                "discarded_epochs": list(discarded_epochs),
            }

        def apply_ranking_epoch(epoch) -> None:
            if epoch.epoch_id in applied_epochs:
                return
            key = next((key for key, item in plans.items()
                        if item.plan_id == epoch.plan_id
                        and item.subject.entity_id == epoch.subject_ref["entity_id"]
                        and item.subject.revision == epoch.subject_ref["revision"]), None)
            if key not in plans:
                raise ValueError("ranking epoch references an unavailable subject or plan")
            arguments = ranking_arguments(key)
            if self.adaptive_policy:
                arguments["candidate_record_ids"] = None
                arguments["expansion_boundary"] = epoch.expansion_boundary
            elif self.incremental_performance and not resume_validated:
                saved = frozen_repair["searches"][epoch.plan_id]["resume_state"]
                history = saved["semantic_epochs"]
                number = history.index(epoch.epoch_id) + 1 if epoch.epoch_id in history else (
                    len(history) + 1)
                if not 1 <= number <= 4:
                    raise ValueError("restored semantic epoch is outside its batch history")
                arguments["expansion_boundary"] = {
                    "version": "bounded-semantic-v1", "round": number,
                    "pool_limit": (8, 16, 32, 64)[number - 1],
                }
            ranking.validate_epoch(epoch, **arguments)
            plans[key] = apply_epoch(plans[key], epoch, index=index)
            if self.candidate_policy:
                searches[key].plan = plans[key]
            scheduler.reorder_slot(
                plans[key].subject,
                key[-1],
                epoch.ordered_record_ids,
                epoch_seq=epoch.epoch_seq,
                scope=scope_for_key(key),
            )
            applied_epochs.add(epoch.epoch_id)
            unapplied_epochs.pop(epoch.epoch_id, None)
            if epoch.degraded:
                diagnostics.append(f"ranking_degraded:{epoch.reason}")

        def blocked_ranking_slots() -> set[tuple[str, int, str]]:
            if search_index is not None:
                # Heuristic admissions never depend on an unstarted semantic epoch.
                return set()
            if pending_ranking is None:
                return set()
            blocked = set()
            for key, plan in plans.items():
                upcoming = scheduler.peek_fresh_slot(
                    plan.subject, key[-1], scope=scope_for_key(key),
                )
                if upcoming is None:
                    continue
                # A historical epoch does not make its unranked tail ready.
                # Only a committed record in the next phase/section can take
                # an ordinary turn. Ledger exploration remains independent.
                if scheduler.is_exploration_due(upcoming):
                    continue
                record = next(item for item in plan.records if item.record_id == upcoming.record_id)
                if key == pending_ranking_key or record.ranking_epoch_id not in applied_epochs:
                    blocked.add(key)
            return blocked

        def ranking_arguments(key) -> dict:
            plan = plans[key]
            return dict(
                plan=plan.model_copy(deep=True),
                index=index,
                metadata=metadata,
                subject_node=nodes[key[0]].model_copy(deep=True),
                predicate=predicates[key],
                run_fingerprint=run_fingerprint,
                root_ref=VersionedRef(id=root.entity_id, revision=root.revision),
                root_class_iri=root_class_iri,
                permission_scope=recognition_run_id,
                **({"candidate_record_ids": searches[key].needs_evaluation_ids()
                    if self.adaptive_policy and self.adaptive_policy.active_search
                    else searches[key].deferred_record_ids}
                   if search_index is not None else {}),
                **({"expansion_attempt_id": searches[key].expansion_attempt_id,
                    "required_record_ids": list(dict.fromkeys([
                        *searches[key].seed_record_ids, *searches[key].priority_record_ids,
                    ]))} if self.adaptive_policy else {}),
                **({"expansion_boundary": searches[key].semantic_boundary}
                   if self.incremental_performance and search_index is not None else {}),
                **ranking_contexts[plan.plan_id],
            )

        def publish_preparation(snapshot: dict) -> None:
            nonlocal pending_service_state
            pending_service_state = snapshot
            if ranking_hook is not None:
                persist_ranking_boundary()

        owner_thread = get_ident()

        def drain_ranking_during_model() -> None:
            # The synchronous worker's HTTP poll runs on this same thread.
            # Never move its Session to a compatibility client's helper thread.
            if get_ident() != owner_thread or pending_ranking is None:
                return
            try:
                pending_ranking.drain(publish_preparation)
            except (ModelCancelled, ExecutionLost):
                raise
            except Exception as exc:
                raise ModelWaitFailure("ranking_persistence_failed") from exc

        def commit_ranking(epoch) -> None:
            if self.progress_hook is not None and not self.progress_hook("after_ranking"):
                raise RankingPaused("execution_pause_requested")
            committed = ranking.commit_epoch(epoch)
            committed_at[committed.epoch_id] = completed_tasks
            if ranking_hook is not None:
                persist_ranking_boundary()
            apply_ranking_epoch(committed)
            events.append(("ranking_epoch_committed", committed.model_dump(mode="json")))
            if search_index is not None:
                key = next(key for key, plan in plans.items() if plan.plan_id == epoch.plan_id)
                accept_ranked_search(key, committed)
                admit_search_page(key)

        def accept_ranked_search(key, epoch):
            if not self.adaptive_policy:
                searches[key].accept_semantic(epoch.ordered_record_ids, epoch.epoch_id,
                                             committed=True)
                return
            from app.services.extraction.ontology_guided.adaptive_retrieval import (
                GateEvaluation,
                epoch_decision,
                gate_decision,
            )

            search = searches[key]
            gates = []
            for gate in ranking.gate_evaluations.values():
                if (gate["plan_id"] == epoch.plan_id
                        and gate["expansion_attempt_id"] == search.expansion_attempt_id):
                    gates.append(gate)
                    decision = gate_decision(GateEvaluation.model_validate(gate),
                                             self.adaptive_policy)
                    ranking.admission_decisions[decision.decision_id] = freeze_json(
                        decision.model_dump(mode="json"))
            decision = epoch_decision(epoch, self.adaptive_policy)
            ranking.admission_decisions[decision.decision_id] = freeze_json(
                decision.model_dump(mode="json"))
            if ranking_hook is not None:
                persist_ranking_boundary()
            for gate in gates:
                search.apply_gate(gate)
            search.accept_ranked(epoch, decision)

        def accept_preparation_result(result):
            from app.services.extraction.ontology_guided.adaptive_retrieval import (
                GateEvaluation,
                gate_decision,
            )

            key = next(k for k, plan in plans.items() if plan.plan_id == result.plan_id)
            identity = result.expansion_attempt_id
            ranking.preparation_results[identity] = freeze_json({
                "result": result.model_dump(mode="json"), "after_outcomes": completed_tasks,
            })
            for ref in result.evaluation_refs:
                decision = gate_decision(
                    GateEvaluation.model_validate(ranking.gate_evaluations[ref]),
                    self.adaptive_policy,
                )
                ranking.admission_decisions[decision.decision_id] = freeze_json(
                    decision.model_dump(mode="json"))
            if ranking_hook is not None:
                persist_ranking_boundary()
            searches[key].accept_result(result, ranking.gate_evaluations)
            admit_search_page(key)

        def prepare_ranking() -> None:
            nonlocal pending_ranking, pending_ranking_key, pending_service_state, ranking
            nonlocal recovering_ranking_pause
            nonlocal semantic_wait_key, semantic_wait_started_at
            if layered_recognition and resume_cursor < len(resume_outcomes):
                # A layer transition is a deterministic, model-free event at the
                # completed prefix. Recreate its plans before replaying admissions
                # whose after_outcomes points at that same boundary.
                while not scheduler.pending and advance_layer():
                    pass
            if self.adaptive_policy and resume_validated:
                from app.services.extraction.ontology_guided.adaptive_retrieval import (
                    SearchPreparationResult,
                )

                saved_results = (
                    [ranking.preparation_results[s.expansion_attempt_id]
                     for key, s in searches.items()
                     if slot_is_active(key) and s.needs_semantic
                     and s.expansion_attempt_id in ranking.preparation_results]
                    if self.current_state else list(ranking.preparation_results.values())
                )
                for saved_result in saved_results:
                    result = SearchPreparationResult.model_validate(saved_result["result"])
                    key = next((k for k, p in plans.items() if p.plan_id == result.plan_id), None)
                    if (key is not None and slot_is_active(key) and searches[key].needs_semantic
                            and slot_subject_is_active(key)
                            and searches[key].expansion_attempt_id == result.expansion_attempt_id):
                        searches[key].accept_result(result, ranking.gate_evaluations)
                        admit_search_page(key)
            ready = list(unapplied_epochs.values()) if self.current_state else ranking.epochs
            for epoch in ready:
                if committed_at.get(epoch.epoch_id) == completed_tasks:
                    apply_ranking_epoch(epoch)
                    if self.incremental_performance and resume_validated:
                        # A ranking commit may be newer than the last task
                        # checkpoint. Admit its exact paid pool after replay.
                        key = next(k for k, p in plans.items() if p.plan_id == epoch.plan_id)
                        search = searches[key]
                        if (slot_is_active(key) and search.needs_semantic
                                and search.semantic_boundary == epoch.expansion_boundary):
                            accept_ranked_search(key, epoch)
                            admit_search_page(key)
            if self.evidence_repair and resume_cursor < len(resume_outcomes):
                logged = {entry["batch_id"] for entry in admission_log}
                for admission in frozen_repair.get("admissions", []):
                    if (admission["after_outcomes"] != resume_cursor
                            or admission["batch_id"] in logged):
                        continue
                    for raw in admission["tasks"]:
                        admitted_task = RecognitionTask.model_validate(raw)
                        slot = task_key(admitted_task)
                        if slot in plans and self.candidate_policy:
                            epoch = next((item for item in ranking.epochs
                                          if item.epoch_id == admission.get("ranking_epoch_id")),
                                         None)
                            if admission.get("ranking_epoch_id") and epoch is None:
                                raise ValueError(
                                    "restored admission has no committed ranking epoch"
                                )
                            plans[slot] = admit_records(
                                plans[slot], index, [admitted_task.record_id],
                                phase=admitted_task.phase, epoch=epoch,
                            )
                            searches[slot].plan = plans[slot]
                        if slot not in plans or admitted_task.record_id not in plans[slot].ledger:
                            raise ValueError("restored admission target mismatch")
                        scheduler.enqueue(
                            admitted_task, root_branch=admitted_task.subject.is_document_root,
                            template_priority=bool(template_interleaving and any(
                                path and path[0] == admitted_task.predicate_iri
                                for path in subject_priorities.get(
                                    admitted_task.subject.entity_id, []
                                )
                            )),
                        )
                        if self.incremental_performance:
                            sources = admission.get("source_priorities", {}).get(
                                admitted_task.task_id,
                            )
                            if sources:
                                if sources != attribute_sources.get(slot, {}).get(
                                    admitted_task.record_id,
                                ):
                                    raise ValueError("restored attribute priority source mismatch")
                                scheduler.prioritize_source(admitted_task, sources)
                    admission_log.append(deepcopy(admission))
                return
            if pending_ranking is not None:
                invalidated = (pending_ranking_key not in plans
                               or not slot_is_active(pending_ranking_key)
                               or slot_completion.get(pending_ranking_key) is not None
                               or not slot_subject_is_active(pending_ranking_key))
                if self.incremental_performance and invalidated:
                    pending_ranking.cancel()
                pending_ranking.drain(publish_preparation)
                if not pending_ranking.future.done():
                    return
                try:
                    epoch = pending_ranking.future.result()
                except ModelCancelled:
                    if not (self.incremental_performance and invalidated):
                        raise
                    epoch = None
                    events.append(("ranking_preparation_cancelled", {
                        "slot": list(pending_ranking_key),
                        "reason": "subject_dependency_invalidated", "costs_retained": True,
                    }))
                ranking = pending_ranking.service
                pending_ranking.close()
                pending_ranking = None
                recovering_ranking_pause = False
                pending_service_state = None
                key = pending_ranking_key
                pending_ranking_key = None
                if self.incremental_performance and invalidated and ranking_hook is not None:
                    persist_ranking_boundary()
                if epoch is not None:
                    from app.services.extraction.ontology_guided.adaptive_retrieval import (
                        SearchPreparationResult,
                    )

                    if isinstance(epoch, SearchPreparationResult):
                        if (key in plans and slot_is_active(key)
                                and plans[key].plan_id == epoch.plan_id
                                and slot_subject_is_active(key)):
                            accept_preparation_result(epoch)
                        else:
                            ranking.preparation_results[epoch.expansion_attempt_id] = freeze_json({
                                "result": epoch.model_dump(mode="json"),
                                "after_outcomes": completed_tasks,
                                "discarded_reason": "subject_dependency_invalidated",
                            })
                            if ranking_hook is not None:
                                persist_ranking_boundary()
                        return
                    if (
                        key not in plans
                        or not slot_is_active(key)
                        or plans[key].plan_id != epoch.plan_id
                        or not slot_subject_is_active(key)
                    ):
                        # Source changes while scoring keep their cost and audit,
                        # but never drive the successor semantic plan.
                        discarded_epochs.append(epoch.model_dump(mode="json"))
                        if self.current_state:
                            ranking.discard_pending()
                        else:
                            saved = ranking.snapshot()
                            saved["pending_epochs"] = []
                            ranking = RankingService(ranking.policy, ranking.model, state=saved,
                                                     adaptive_policy=self.adaptive_policy)
                        if ranking_hook is not None:
                            persist_ranking_boundary()
                    else:
                        ranking.validate_epoch(epoch, **ranking_arguments(key))
                        commit_ranking(epoch)
                return
            if resume_cursor < len(resume_outcomes) and restored_ranking:
                return
            key = None
            if search_index is not None:
                if scheduler.dispatched >= scheduler.max_tasks:
                    if self.candidate_policy:
                        # Close an actually exhausted last page before deciding
                        # whether the dispatch limit left further obligations.
                        for search_key in searches:
                            if slot_is_active(search_key) and slot_subject_is_active(search_key):
                                admit_search_page(search_key)
                    return
                for search_key in list(searches):
                    if not slot_is_active(search_key):
                        continue
                    if slot_subject_is_active(search_key):
                        admit_search_page(search_key)
                    elif self.adaptive_policy:
                        searches[search_key].exhaust_dependency()
                for search_key, search in searches.items():
                    if (slot_is_active(search_key) and search.needs_semantic
                            and slot_subject_is_active(search_key)):
                        key = search_key
                        break
                if key is None:
                    semantic_wait_key = None
                    return
                if semantic_wait_key != key:
                    semantic_wait_key = key
                    semantic_wait_started_at = completed_tasks
                if self.evidence_repair and ranking.policy.mode != "semantic":
                    # Disabled semantic search has no expensive work to defer.
                    # Advance its fallback immediately instead of waiting eight
                    # unrelated model tasks before allowing broader recall.
                    semantic_wait_key = None
                    searches[key].skip_semantic("ranking_policy_deterministic")
                    admit_search_page(key)
                    return
                # Keep cheap work first, with a finite turn bound for slots that
                # need broader recall. One large lexical list must not starve H2.
                if (
                    scheduler.peek_task() is not None
                    and completed_tasks - semantic_wait_started_at
                    < self.heuristic_policy.max_cheap_tasks_before_semantic
                ):
                    return
                semantic_wait_key = None
                if ranking.policy.mode != "semantic":
                    searches[key].skip_semantic("ranking_policy_deterministic")
                    admit_search_page(key)
                    return
            while restored_paused_epochs:
                paused = restored_paused_epochs.pop(0)
                key = next(
                    (slot for slot, plan in plans.items() if plan.plan_id == paused["plan_id"]),
                    None,
                )
                if key is not None and slot_subject_is_active(key):
                    # prepare_next_epoch validates the exact restored epoch's
                    # dependencies before returning a budget pause or retrying
                    # an incomplete technical attempt. No unrelated task may
                    # bypass this existing run-level pause during recovery.
                    recovering_ranking_pause = True
                    break
                discarded_epochs.append(paused)
                if self.current_state:
                    ranking.discard_pending(paused["plan_id"])
                else:
                    saved = ranking.snapshot()
                    saved["pending_epochs"] = [
                        item for item in saved["pending_epochs"]
                        if item["plan_id"] != paused["plan_id"]
                    ]
                    ranking = RankingService(ranking.policy, ranking.model, state=saved,
                                             adaptive_policy=self.adaptive_policy)
                if ranking_hook is not None:
                    persist_ranking_boundary()
                key = None
            if key is None:
                upcoming = scheduler.peek_task()
                if upcoming is None or upcoming.retry_kind:
                    return
                key = task_key(upcoming)
                plan = plans[key]
                if any(
                    epoch.plan_id == plan.plan_id
                    and any(
                        plan.ledger[rid].coverage_state == "unattempted"
                        for rid in epoch.record_ids
                    )
                    for epoch in ranking.epochs_by_plan.get(plan.plan_id, [])
                ):
                    return
                if not subject_is_active(upcoming.subject, upcoming.scope):
                    return
            if self.progress_hook is not None and not self.progress_hook("before_ranking"):
                raise RankingPaused("execution_pause_requested")
            if ranking.policy.mode == "semantic":
                pending_ranking_key = key
                pending_ranking = RankingPreparation(
                    ranking,
                    task_id=stable_id("ranking-slot", list(key)),
                    arguments=ranking_arguments(key),
                )
                return
            epoch = ranking.prepare_next_epoch(**ranking_arguments(key))
            if epoch is not None:
                from app.services.extraction.ontology_guided.adaptive_retrieval import (
                    SearchPreparationResult,
                )

                if isinstance(epoch, SearchPreparationResult):
                    accept_preparation_result(epoch)
                else:
                    commit_ranking(epoch)

        def subject_is_active(subject: SubjectRef, scope: TraversalScope | None = None) -> bool:
            if subject.is_document_root:
                return True
            if self.tool_protocol:
                node = nodes.get(subject.entity_id)
                if (node is None or node.revision != subject.revision
                        or node.decision_status != "supported" or node.type_decision_ref is None
                        or node.referent_decision_ref is None or node.referent_ref is None
                        or not dependency_index.is_valid_references([
                            VersionedRef(id=node.entity_id, revision=node.revision),
                            node.type_decision_ref, node.referent_decision_ref, node.referent_ref,
                            *node.dependency_refs,
                        ])):
                    return False
                scopes = [scope] if scope is not None else [
                    item["scope"] for key, item in registered_subjects.items()
                    if key[:2] == (subject.entity_id, subject.revision)
                ]
                return any(
                    subject_key(subject, value) in registered_subjects
                    and dependency_index.is_valid(scoped_subject_dependency(subject, value))
                    and all(
                        (relation := edges.get(step.relation_ref.id)
                         or relationship_groups.get(step.relation_ref.id)) is not None
                        and relation.revision == step.relation_ref.revision
                        and (member := nodes.get(step.member_ref.id)) is not None
                        and member.revision == step.member_ref.revision
                        and dependency_index.is_valid_references(
                            [step.relation_ref, step.member_ref]
                        )
                        for step in value.members
                    ) for value in scopes
                )
            node_heads = {item.entity_id: item.revision for item in nodes.values()}
            reached = {root.entity_id}
            changed = True
            while changed:
                changed = False
                for edge in edges.values():
                    if (
                        edge.subject_ref.id in reached
                        and edge.object_ref.id not in reached
                        and node_heads.get(edge.subject_ref.id) == edge.subject_ref.revision
                        and node_heads.get(edge.object_ref.id) == edge.object_ref.revision
                        and edge.decision_status == "supported"
                        and edge.polarity == "affirmed"
                        and not edge.conditions
                        and not edge.applicability
                        and effective_proof_gate(edge)
                        and dependency_index.is_valid(f"{edge.candidate_id}@{edge.revision}")
                    ):
                        reached.add(edge.object_ref.id)
                        changed = True
            return (
                subject.entity_id in reached
                and nodes[subject.entity_id].revision == subject.revision
            )

        def snapshot_graph(*, terminal: bool = False) -> GraphSnapshot:
            if self.current_state:
                from collections import Counter

                totals = Counter()
                for plan in plans.values():
                    totals.update(plan.ledger.counts)
                records_planned = sum(len(plan.ledger) for plan in plans.values())
                examined, incomplete, unattempted = (totals[k] for k in (
                    "examined", "attempted_incomplete", "unattempted"))
                def semantic_count(key):
                    return totals["semantic:" + key]
                ledger_entries = ()
            else:
                for plan in plans.values():
                    validate_record_universe(plan, index)
                ledger_entries = [entry for plan in plans.values()
                                  for entry in plan.ledger.values()]
                records_planned = len(ledger_entries)
                examined = sum(entry.coverage_state == "examined" for entry in ledger_entries)
                incomplete = sum(entry.coverage_state == "attempted_incomplete"
                                 for entry in ledger_entries)
                unattempted = sum(entry.coverage_state == "unattempted" for entry in ledger_entries)
                semantic = [value for entry in ledger_entries for value in entry.semantic_outcomes]
                semantic_count = semantic.count
            if self.record_pipeline:
                discovery_coverage = discovery_search.coverage_counts(record_discovery.values())
                records_planned += discovery_coverage["planned"]
                examined += discovery_coverage["examined"]
                incomplete += discovery_coverage["incomplete"]
                unattempted += discovery_coverage["unattempted"]
            pending_frontiers = sum(
                item.get("logical_records", 1) for item in scheduler.unexplored_frontier
            )
            if active_unit_ref is not None:
                pending_frontiers += 1
            pending_layers = 0
            if layered_recognition and not dependency_ready:
                pending_layers = sum(
                    active_hop < item["hop"] <= self.max_hops
                    and subject_is_active(item["subject"], item.get("scope"))
                    for item in registered_subjects.values()
                )
                if layer_phase == "property":
                    pending_layers += sum(
                        len(menus[item["subject"].entity_id].relationships)
                        for item in registered_subjects.values()
                        if item["hop"] == active_hop
                        and item["subject"].entity_id in menus
                        and subject_is_active(item["subject"], item.get("scope"))
                    )
                pending_frontiers += pending_layers
            if self.evidence_repair:
                pending_frontiers += call_totals[3] if self.current_state else sum(
                    len(p.get("deferred_types", [])) for p in protocols.values()
                )
            budget_stopped_slots = budget_blocked_slots()
            paused_plan_ids = {
                epoch["plan_id"]
                for epoch in current_ranking_state().get("service", {}).get("pending_epochs", [])
                if epoch.get("status") == "paused"
            } if terminal and ranking_paused else set()
            complete = (
                not incomplete
                and not unattempted
                and not pending_frontiers
                and not conflict_claims
                and not ranking_paused
                and not (discovery_search and discovery_search.unselected)
                and not any(w["status"] in {"queued", "incomplete", "deferred"}
                            for w in evidence_work.items.values())
            )
            confirmed = {} if self.current_state else {
                         lineage: len(protocol.get("completed_attempts", []))
                         for lineage, protocol in protocols.items()}
            unresolved_calls = call_totals[2] if self.current_state else sum(
                max(0, reserved - max(lineage_calls.get(lineage, 0), confirmed.get(lineage, 0)))
                for lineage, reserved in reserved_calls.items()
            )
            search_incomplete = any(
                search.status not in {"pass_exhausted", "local_results_only"}
                or bool(getattr(search, "_unavailable", ()))
                for search in searches.values()
            ) if self.candidate_policy else False
            if self.candidate_policy:
                # Policy execution and semantic truth are separate dimensions.
                # A completed comparison may legitimately report a conflict or
                # undetermined fact; only actual missing work blocks this end.
                complete = bool(
                    terminal and self.adapter is not None
                    and not incomplete and not unattempted and not pending_frontiers
                    and not ranking_paused and not unresolved_calls and not scheduler.pending
                    and not search_incomplete and not budget_stopped_slots
                    and not any(w["status"] in {"queued", "incomplete", "deferred"}
                                for w in evidence_work.items.values())
                )
            if self.tool_protocol:
                complete = bool(complete and terminal and self.adapter is not None
                                and not unresolved_calls and not scheduler.pending
                                and not budget_stopped_slots)
            retrieval_diagnostics = {}
            if self.adaptive_policy:
                from app.schemas.retrieval_diagnostics import RetrievalDiagnostics

                summaries = [search.diagnostics() for search in searches.values()]
                fields = ("records_soft_pruned", "records_pending_disposition",
                          "records_reactivatable", "records_dependency_exhausted")
                retrieval_diagnostics = {"retrieval_diagnostics": RetrievalDiagnostics(
                    pruning_quality=("unvalidated" if self.adaptive_policy.mode == "trial"
                                     else None),
                    **{name: sum(getattr(item, name) for item in summaries) for name in fields},
                    search_status=("searching" if not terminal else "complete" if complete
                                   else "blocked" if ranking_paused or incomplete
                                   else "budget_limited" if budget_stopped_slots
                                   else "saturated"),
                    reason_counts={"low_relevance": sum(s.records_soft_pruned for s in summaries)},
                )}
            progress = RunProgress(
                **retrieval_diagnostics,
                **({"candidate_policy": self.candidate_policy} if self.candidate_policy else {}),
                tasks_attempted=examined + incomplete,
                model_calls=(call_totals[1] if self.current_state and has_protocol else
                             sum(max(lineage_calls.get(key, 0), confirmed.get(key, 0))
                                 for key in set(lineage_calls) | set(confirmed))
                             if has_protocol else model_calls),
                model_calls_reserved=call_totals[0] if self.current_state else sum(
                    reserved_calls.values()),
                model_calls_unresolved=unresolved_calls,
                records_planned=records_planned,
                records_examined=examined,
                records_incomplete=incomplete,
                records_unattempted=unattempted,
                record_discovery=(discovery_search.diagnostics(ranking.policy.mode)
                                  if discovery_search else None),
                phase_counts={
                    "phase1": totals["executed_phase1"] if self.current_state else sum(
                        entry.phase == 1 and entry.coverage_state != "unattempted"
                        for entry in ledger_entries
                    ),
                    "phase2": totals["executed_phase2"] if self.current_state else sum(
                        entry.phase == 2 and entry.coverage_state != "unattempted"
                        for entry in ledger_entries
                    ),
                },
                supported=semantic_count("supported"),
                unsupported=semantic_count("unsupported"),
                undetermined=semantic_count("undetermined"),
                pending_frontiers=pending_frontiers,
                unresolved_claims=semantic_count("undetermined") + len(conflict_claims),
                invalidated_count=len(dependency_index.invalidated),
                stop_reason=(
                    None
                    if not terminal
                    else "ranking_paused"
                    if ranking_paused
                    else "model_calls_unresolved"
                    if self.tool_protocol and unresolved_calls
                    else "evidence_review_pending" if review_paused
                    else "layered_policy_complete" if complete and self.candidate_policy
                    and layered_recognition and not dependency_ready
                    else "candidate_search_exhausted" if complete and self.candidate_policy
                    else "counterevidence_unresolved"
                    if conflict_claims
                    else "queue_exhausted"
                    if complete
                    else "service_failure"
                    if self.adapter is None
                    else "task_budget_exhausted"
                    if budget_stopped_slots or (
                        pending_layers and scheduler.dispatched >= scheduler.max_tasks
                    )
                    else "attempted_incomplete"
                    if incomplete
                    else "evidence_recheck_incomplete"
                    if any(w["status"] in {"queued", "incomplete", "deferred"}
                           for w in evidence_work.items.values())
                    else "model_calls_unresolved"
                    if self.candidate_policy and unresolved_calls
                    else "candidate_search_incomplete"
                    if self.candidate_policy and search_incomplete
                    else "adaptive_search_saturated"
                    if search_index is not None
                    else "unattempted"
                ),
                completion=("policy_complete" if complete and self.candidate_policy
                            else "in_scope_complete" if complete else "incomplete"),
            )
            if terminal and repair_only:
                requested = {item["operation_id"] for item in property_repairs or []
                             if item["status"] in {"queued", "running"}}
                if not requested and expert_operations:
                    requested = {next(reversed(expert_operations))}
                done = bool(requested) and all(
                    expert_operations[key]["status"] == "completed" for key in requested
                )
                progress = progress.model_copy(update={
                    "stop_reason": ("expert_repair_completed" if done
                                    else "expert_repair_unresolved"),
                    **({"completion": "incomplete"} if not done else {}),
                })
            coverage = []
            for key, plan in plans.items():
                entries = () if self.current_state else list(plan.ledger.values())
                counts = plan.ledger.counts if self.current_state else {}
                predicate = predicates[key]
                coverage.append(
                    CoverageSummary(
                        **({"candidate_policy": self.candidate_policy}
                           if self.candidate_policy else {}),
                        **({"retrieval_diagnostics": searches[key].diagnostics()}
                           if self.adaptive_policy else {}),
                        subject_ref=VersionedRef(id=key[0], revision=key[1]),
                        predicate_iri=predicate.iri,
                        predicate_label=predicate.label,
                        **({"scope": scope_for_key(key)} if self.tool_protocol else {}),
                        phase1=counts["phase1"] if self.current_state else sum(
                            entry.phase == 1 for entry in entries),
                        phase2=counts["phase2"] if self.current_state else sum(
                            entry.phase == 2 for entry in entries),
                        examined=counts["examined"] if self.current_state else sum(
                            entry.coverage_state == "examined" for entry in entries),
                        incomplete=counts["attempted_incomplete"] if self.current_state else sum(
                            entry.coverage_state == "attempted_incomplete" for entry in entries
                        ),
                        unattempted=counts["unattempted"] if self.current_state else sum(
                            entry.coverage_state == "unattempted" for entry in entries),
                        executed_phase_counts={
                            f"phase{phase}": counts[f"executed_phase{phase}"]
                            if self.current_state else sum(
                                entry.phase == phase and entry.coverage_state != "unattempted"
                                for entry in entries
                            )
                            for phase in (1, 2)
                        },
                        pending_frontiers=sum(
                            item.get("logical_records", 1)
                            for item in scheduler.unexplored_frontier
                            if item.get("slot") == list(key) or item.get("task_id")
                            in (plan.ledger.task_ids if self.current_state else
                                {task_id for entry in entries for task_id in entry.task_ids})
                        ),
                        stop_reason=(
                            None
                            if not terminal
                            else "ranking_paused"
                            if plan.plan_id in paused_plan_ids
                            else "counterevidence_unresolved"
                            if any(
                                candidate_tasks.get(claim) is not None
                                and candidate_tasks[claim].subject == plan.subject
                                and candidate_tasks[claim].predicate_iri == predicate.iri
                                for claim in conflict_claims
                            )
                            else "task_budget_exhausted"
                            if key in budget_stopped_slots
                            else "attempted_incomplete"
                            if (bool(counts["attempted_incomplete"]) if self.current_state else any(
                                entry.coverage_state == "attempted_incomplete" for entry in entries
                            ))
                            else "unattempted"
                            if (bool(counts["unattempted"]) if self.current_state else
                                any(entry.coverage_state == "unattempted" for entry in entries))
                            else "single_value_satisfied"
                            if slot_completion.get(key) is not None
                            else "semantic_undetermined"
                            if self.candidate_policy and (bool(counts["semantic:undetermined"])
                                if self.current_state else any(
                                "undetermined" in entry.semantic_outcomes for entry in entries
                            ))
                            else "candidate_search_exhausted"
                            if self.candidate_policy and searches[key].status in {
                                "pass_exhausted", "local_results_only",
                            } and not getattr(searches[key], "_unavailable", ())
                            else "candidate_search_incomplete"
                            if self.candidate_policy
                            else "queue_exhausted"
                        ),
                        unresolved_claims=counts["semantic:undetermined"]
                        if self.current_state else sum(
                            value == "undetermined"
                            for entry in entries
                            for value in entry.semantic_outcomes
                        ),
                    )
                )
            return project_graph(
                recognition_run_id=recognition_run_id,
                run_revision=run_revision,
                event_head=event_head + completed_tasks,
                metadata_snapshot_id=metadata.snapshot_id,
                root_ref=VersionedRef(id=root.entity_id, revision=root.revision),
                nodes=list(nodes.values()),
                edges=list(edges.values()),
                properties=list(properties.values()),
                coverage=coverage,
                progress=progress,
                dependency_index=dependency_index,
                projection=("verified" if self.tool_protocol
                            and not self.independent_entity_traversal else "all"),
                **({"relationship_groups": list(relationship_groups.values()),
                    "extraction_protocol": TOOL_PROTOCOL_VERSION} if self.tool_protocol else {}),
                artifact_status="ready" if complete else "partial",
                current_content_hash=evidence_hash([work_digest, progress.model_dump(mode="json"),
                                                    run_revision, event_head + completed_tasks])
                if self.current_state else None,
            ).model_copy(update={"attribute_candidates": [
                AttributeCalibrationCandidate.model_validate(candidate)
                for row in record_discovery.values()
                for candidate in row.get("attribute_candidates", [])
                if candidate["status"] != "resolved"
            ]})

        def validate_resume_boundary() -> None:
            nonlocal resume_validated
            if resume_validated:
                return
            expected_frontier = (resume_state or {}).get("frontier")
            expected_ledger = (resume_state or {}).get("recall_ledger")
            expected_dependencies = (resume_state or {}).get("dependency_index")
            if self.evidence_repair:
                if frozen_repair.get("version") != "evidence-repair-v1":
                    raise ValueError("missing evidence repair checkpoint")
                for search in searches.values():
                    search.restore(frozen_repair["searches"][search.plan.plan_id])
            actual_ledger = {plan.plan_id: plan.model_dump(mode="json") for plan in plans.values()}
            if (
                expected_frontier != current_frontier()
                or expected_ledger != actual_ledger
                or expected_dependencies != dependency_index.snapshot()
            ):
                raise ValueError(
                    "checkpoint scheduler, recall ledger, or dependency index mismatch"
                )
            resume_validated = True
            if self.evidence_repair:
                for search in searches.values():
                    search.continue_search()

        def versioned_key(identifier: str, revision: int) -> str:
            return f"{identifier}@{revision}"

        def register_dependencies(task: RecognitionTask, outcome: TaskOutcome) -> None:
            decisions_by_target: dict[str, list[str]] = {}
            for decision in outcome.decision_payloads:
                target_id = str(decision.get("target_id") or "")
                decision_id = str(decision.get("decision_id") or "")
                if not target_id or not decision_id:
                    continue
                decisions_by_target.setdefault(target_id, []).append(
                    versioned_key(decision_id, int(decision.get("revision", 1)))
                )
            for proof in outcome.proof_payloads:
                proof_id = str(proof.get("proof_id") or "")
                if not proof_id:
                    continue
                dependencies = list(decisions_by_target.get(str(proof.get("target_id")), []))
                for reference in proof.get("dependency_refs") or []:
                    if isinstance(reference, dict) and reference.get("id"):
                        dependencies.append(
                            versioned_key(str(reference["id"]), int(reference.get("revision", 1)))
                        )
                dependency_index.add_proof(
                    versioned_key(proof_id, int(proof.get("proof_revision", 1))),
                    list(dict.fromkeys(dependencies)),
                )
            if self.tool_protocol:
                for resolution in outcome.reference_resolutions:
                    for field, dependencies_field in (
                        ("claim_ref", "entity_dependency_refs"),
                        ("binding_ref", "binding_dependency_refs"),
                    ):
                        reference = resolution.get(field)
                        if reference:
                            dependency_index.add_proof(
                                versioned_key(reference["id"], reference["revision"]),
                                [versioned_key(ref["id"], ref["revision"])
                                 for ref in resolution.get(dependencies_field, [])],
                            )
                for node in outcome.nodes:
                    refs = [ref for ref in (node.type_decision_ref, node.referent_decision_ref,
                                             node.composition_decision_ref, node.referent_ref,
                                             *node.dependency_refs) if ref is not None]
                    dependency_index.add_proof(
                        versioned_key(node.entity_id, node.revision),
                        [versioned_key(ref.id, ref.revision) for ref in refs],
                    )
            for candidate in [*outcome.edges, *outcome.properties, *outcome.relationship_groups]:
                dependencies = [
                    *(
                        [versioned_key(candidate.proof_ref.id, candidate.proof_ref.revision)]
                        if candidate.proof_ref is not None
                        else []
                    ),
                    *[
                        versioned_key(reference.id, reference.revision)
                        for reference in candidate.decision_refs
                    ],
                    *[
                        versioned_key(reference.id, reference.revision)
                        for reference in candidate.dependency_refs
                    ],
                ]
                if not isinstance(task, RecordDiscoveryTask) and not task.subject.is_document_root:
                    dependencies.append(scoped_subject_dependency(task.subject, task.scope))
                if self.tool_protocol:
                    dependencies.append(versioned_key(
                        candidate.subject_ref.id, candidate.subject_ref.revision,
                    ))
                    endpoints = (candidate.object_refs
                                 if isinstance(candidate, GraphRelationshipGroup)
                                 else [candidate.object_ref] if isinstance(candidate, GraphEdge)
                                 else [])
                    dependencies.extend(versioned_key(ref.id, ref.revision) for ref in endpoints)
                    dependencies.extend(versioned_key(ref.id, ref.revision)
                                        for step in candidate.scope.members
                                        for ref in (step.relation_ref, step.member_ref))
                dependency_index.add_proof(
                    versioned_key(candidate.candidate_id, candidate.revision),
                    list(dict.fromkeys(dependencies)),
                )

        def conflict_scope(candidate) -> tuple[str, str, str]:
            group = evidence_hash(
                [
                    candidate.subject_ref.model_dump(mode="json"),
                    candidate.predicate_iri,
                ]
            )
            if isinstance(candidate, GraphEdge):
                node = nodes.get(candidate.object_ref.id)
                # This is a *potential conflict* subscription, not an identity
                # merge: equally named local mentions remain separate nodes.
                role = evidence_hash([node.class_iri, node.label] if node else candidate.object_ref)
            else:
                predicate = predicates.get(
                    (
                        candidate.subject_ref.id,
                        candidate.subject_ref.revision,
                        candidate.predicate_iri,
                    )
                )
                role = evidence_hash(
                    ["functional-property"]
                    if predicate and predicate.max_count == 1
                    else ["property-value", candidate.raw_value]
                )
            applicability = evidence_hash([sorted(candidate.conditions), candidate.applicability])
            return group, role, applicability

        def process_conflicts(task: RecognitionTask, candidate) -> None:
            suspected_counter = (
                self.evidence_repair and candidate.polarity in {"negated", "conditional"}
                and bool(candidate.evidence_refs) and bool(candidate.predicate_evidence_refs)
            )
            if not suspected_counter and (
                candidate.decision_status != "supported" or not effective_proof_gate(candidate)
            ):
                return
            # A source-backed negative hypothesis suspends the affected closure;
            # it is not promoted to a negative fact before independent review.
            group, role, applicability = conflict_scope(candidate)
            candidate_key = versioned_key(candidate.candidate_id, candidate.revision)
            if candidate.polarity == "affirmed":
                dependency_index.subscribe(
                    candidate_key,
                    record_or_group=group,
                    owner_role=role,
                    applicability=applicability,
                )
            required = required_by_lineage.get(task.claim_lineage_id, [])
            if (
                task.retry_kind
                and task.retry_kind.startswith("evidence_recheck")
                and (required and covers_required_sources(candidate.counterevidence_refs, required))
            ):
                conflict_claims.discard(candidate.candidate_id)
                return
            candidates = edges.values() if isinstance(candidate, GraphEdge) else properties.values()
            for other in list(candidates):
                if (
                    other.candidate_id == candidate.candidate_id
                    or other.decision_status != "supported"
                    or not effective_proof_gate(other)
                    or conflict_scope(other)[:2] != (group, role)
                    or any(
                        candidate.applicability[name] != other.applicability[name]
                        for name in candidate.applicability.keys() & other.applicability.keys()
                    )
                ):
                    continue
                opposite = {candidate.polarity, other.polarity} == {"affirmed", "negated"}
                competing_value = (
                    isinstance(candidate, GraphProperty)
                    and candidate.polarity == other.polarity == "affirmed"
                    and candidate.raw_value != other.raw_value
                )
                scope_restriction = any(
                    affirmative.polarity == "affirmed"
                    and not affirmative.conditions
                    and not affirmative.applicability
                    and (
                        qualified.polarity == "conditional"
                        or bool(qualified.conditions or qualified.applicability)
                    )
                    for affirmative, qualified in ((candidate, other), (other, candidate))
                )
                if not opposite and not competing_value and not scope_restriction:
                    continue
                event_key = evidence_hash(
                    sorted(
                        [
                            versioned_key(candidate.candidate_id, candidate.revision),
                            versioned_key(other.candidate_id, other.revision),
                        ]
                    )
                )
                if event_key in conflict_events:
                    continue
                conflict_events.add(event_key)
                affected: set[str] = set()
                for compared in (candidate, other):
                    compared_group, compared_role, compared_scope = conflict_scope(compared)
                    affected.update(
                        dependency_index.notify_competitor(
                            record_or_group=compared_group,
                            owner_role=compared_role,
                            applicability=compared_scope,
                        )
                    )
                events.append(
                    (
                        "counterevidence_detected",
                        {
                            "conflict_id": event_key,
                            "invalidated_refs": sorted(affected),
                            "source_refs": [
                                anchor.model_dump(mode="json") for anchor in candidate.evidence_refs
                            ],
                            "semantic_status": "undetermined",
                        },
                    )
                )
                diagnostics.append("late_counterevidence_requires_revalidation")
                for positive, counter in ((candidate, other), (other, candidate)):
                    if positive.polarity != "affirmed":
                        continue
                    original = candidate_tasks.get(positive.candidate_id)
                    conflict_claims.add(positive.candidate_id)
                    if original is None:
                        continue
                    source_refs = required_by_lineage.setdefault(original.claim_lineage_id, [])
                    for reference in counter.evidence_refs:
                        if reference not in source_refs:
                            source_refs.append(reference)
                    count = recheck_counts.get(original.claim_lineage_id, 0)
                    # Ranking changes never enter this branch. Only a new
                    # source-backed conflict can consume the lineage's quota.
                    if count >= 2 or not source_refs:
                        continue
                    count += 1
                    recheck_counts[original.claim_lineage_id] = count
                    recheck = RecognitionTask.create(
                        subject=original.subject,
                        predicate_iri=original.predicate_iri,
                        predicate_kind=original.predicate_kind,
                        record_id=original.record_id,
                        phase=original.phase,
                        hop=original.hop,
                        dependency_hash=evidence_hash([original.dependency_hash, source_refs]),
                        claim_lineage_id=original.claim_lineage_id,
                        retry_kind=f"evidence_recheck:{count}",
                        section_node_id=original.section_node_id,
                        source_position=original.source_position,
                    )
                    scheduler.enqueue(recheck, root_branch=original.subject.is_document_root)

        def exact_identity_enrichment(task, current, node, new_refs, outcome):
            """Bind the append to this lineage's finalized, source-checked link targets."""
            from app.services.extraction.ontology_guided.claim_protocol import (
                FrozenClaimSet,
                VerifiedClaimSet,
                claim_content_hash,
            )

            state = task_protocol(task)
            if not all(state.get(field + "_ref") for field in (
                "discovery", "verification", "outcome",
            )) or any(ref.revision != 1 for ref in new_refs):
                return False
            owner, owner_args = task_result_owner(task)
            frozen = FrozenClaimSet.model_validate(load_result(
                owner, state["discovery_ref"], "discovery", **owner_args,
            ), strict=True)
            verified = VerifiedClaimSet.model_validate(load_result(
                owner, state["verification_ref"], "verification", **owner_args,
            ), strict=True)
            finalized = TaskOutcome.model_validate(load_result(
                owner, state["outcome_ref"], "outcome", **owner_args,
            ))
            # Reuse the finalizer's deterministic identity/key/provenance checks;
            # the coordinator must not accept edited metadata after finalization.
            if node not in finalized.nodes:
                return False
            reference = VersionedRef(id=current.entity_id, revision=current.revision)
            proposed_decisions = {item["decision_id"]: item for item in outcome.decision_payloads}
            requested = {ref.id for ref in new_refs}
            matched = set()
            for link in frozen.external_links:
                source_ref = frozen.local_ref_map.get(link.subject_id)
                resolved = next((item for item in finalized.reference_resolutions
                                 if item["claim_ref"] == json_value(source_ref)
                                 and item["entity_ref"] == json_value(reference)), None)
                if (link.local_id in frozen.claim_issues or source_ref is None
                        or (source_ref != reference and resolved is None)):
                    continue
                dependencies = [source_ref, *(step.relation_ref for step in task.scope.members)]
                dependencies = [VersionedRef(id=identity, revision=revision)
                                for identity, revision in sorted({
                                    (ref.id, ref.revision) for ref in dependencies
                                })]
                digest = claim_content_hash("external_link", link, task.scope, dependencies)
                target_id = evidence_hash({
                    "claim_ref": frozen.local_ref_map[link.local_id], "content_hash": digest,
                })
                target = next((value for value in verified.targets
                               if value.target_id == target_id and value.content_hash == digest),
                              None)
                if (target is None or target.missing_facets or target.validation_issues
                        or len(target.decisions) != 3
                        or {value.check_kind for value in target.decisions} != {
                            "identity_owner", "identity_key", "identity_scope",
                        }):
                    continue
                identities = {value.decision_id for value in target.decisions}
                if identities <= requested and all(
                    value.target_id == target_id and value.verdict == "supported"
                    and value.model_dump(mode="json") == proposed_decisions.get(value.decision_id)
                    for value in target.decisions
                ):
                    matched.update(identities)
            return bool(requested) and matched == requested

        def apply_outcome(
            task: RecognitionTask, outcome: TaskOutcome, excluded_slots: set[tuple],
            *, batch_member=False, result_version=None, outcome_ref=None,
        ) -> None:
            nonlocal model_calls, completed_tasks, work_digest
            is_record = isinstance(task, RecordDiscoveryTask)
            key = None if is_record else task_key(task)
            plan = None if is_record else plans[key]
            previous_nodes = {n.entity_id: nodes.get(n.entity_id) for n in outcome.nodes}
            if is_record:
                card = discovery_cards[task.schema_card_id]
                if not self.evidence_review and (outcome.edges or outcome.relationship_groups):
                    raise ValueError("record_discovery_cannot_publish_relations")
                if (outcome.nodes or outcome.properties or outcome.edges
                        or outcome.relationship_groups):
                    state = task_protocol(task)
                    if not state.get("outcome_ref") or outcome.model_dump(
                        mode="json",
                    ) != load_result(
                        task.claim_lineage_id, state["outcome_ref"], "outcome",
                    ):
                        raise ValueError("record_outcome_not_finalized")
                    if any(node.class_iri not in card.class_iris for node in outcome.nodes):
                        raise ValueError("record_entity_class_outside_card")
            conflicting_node_refs: set[tuple[str, int]] = set()
            canonical_node_refs: dict[tuple[str, int], VersionedRef] = {}
            accepted_nodes: dict[str, GraphNode] = {}
            for node in outcome.nodes:
                current = accepted_nodes.get(node.entity_id) or nodes.get(node.entity_id)
                proposed_ref = (node.entity_id, node.revision)
                if self.tool_protocol and current is not None and node != current:
                    identity_fields = {
                        "identity_status", "external_provenance", "identity_decision_refs",
                    }
                    new_decisions = [ref for ref in node.identity_decision_refs
                                     if ref not in current.identity_decision_refs]
                    if (node.model_dump(exclude=identity_fields)
                            == current.model_dump(exclude=identity_fields)
                            and node.identity_status == "verified"
                            and node.external_provenance
                            and all(item in node.external_provenance
                                    for item in current.external_provenance)
                            and all(ref in node.identity_decision_refs
                                    for ref in current.identity_decision_refs)
                            and exact_identity_enrichment(
                                task, current, node, new_decisions, outcome,
                            )):
                        # Independent identity metadata enriches the same referent;
                        # it never reinterprets its class, root or local identity.
                        accepted_nodes[node.entity_id] = node
                        canonical_node_refs[proposed_ref] = VersionedRef(
                            id=node.entity_id, revision=node.revision,
                        )
                        continue
                if current is not None and current.root:
                    if node != current:
                        conflicting_node_refs.add(proposed_ref)
                        diagnostics.append("adapter_document_root_overwrite_rejected")
                    continue
                if node.root or node.root_origin == "user_specified":
                    conflicting_node_refs.add(proposed_ref)
                    diagnostics.append("adapter_document_root_proposal_rejected")
                    continue
                if self.tool_protocol:
                    if current and (node.revision < current.revision or (
                        node.revision == current.revision and node != current
                    )):
                        conflicting_node_refs.add(proposed_ref)
                        diagnostics.append("adapter_node_revision_conflict")
                        continue
                    if current and node.revision > current.revision:
                        dependency_index.invalidate(versioned_key(
                            current.entity_id, current.revision,
                        ))
                        for registration in registered_subjects.values():
                            old_subject = registration["subject"]
                            if (old_subject.entity_id == current.entity_id
                                    and old_subject.revision == current.revision):
                                dependency_index.invalidate(scoped_subject_dependency(
                                    old_subject, registration["scope"],
                                ))
                    accepted_nodes[node.entity_id] = node
                    continue
                if self.evidence_repair and current and (
                    current.class_iri != node.class_iri
                    or any(e.object_ref.id == node.entity_id and e.reason_code == "type_ambiguous"
                           for e in outcome.edges)
                ):
                    if current.label != node.label or current.evidence_refs != node.evidence_refs:
                        conflicting_node_refs.add(proposed_ref)
                        diagnostics.append("physical_mention_revision_mismatch")
                        continue
                    if node.decision_status == "supported" or any(
                        e.object_ref.id == node.entity_id and e.reason_code == "type_ambiguous"
                        for e in outcome.edges
                    ):
                        # An interpretation revision cannot carry forward old
                        # subject/menu qualifications. Child lineages omit node
                        # revision and therefore keep their original paid budget.
                        dependency_index.invalidate(
                            f"subject:{current.entity_id}@{current.revision}"
                        )
                        for old_edge in edges.values():
                            if old_edge.object_ref == VersionedRef(
                                id=current.entity_id, revision=current.revision,
                            ):
                                dependency_index.invalidate(versioned_key(
                                    old_edge.candidate_id, old_edge.revision,
                                ))
                        node = node.model_copy(update={"revision": current.revision + 1})
                    else:
                        node = current
                if current and node.revision <= current.revision:
                    # A predicate-specific verdict is an observation on its
                    # assertion, not a new interpretation of this mention.
                    # Keep the exact persisted node, including its first verdict.
                    observation_fields = {"revision", "decision_status", "independent_review"}
                    if current.model_dump(exclude=observation_fields) != node.model_dump(
                        exclude=observation_fields
                    ):
                        conflicting_node_refs.add(proposed_ref)
                        diagnostics.append("adapter_node_revision_conflict")
                        continue
                    node = current
                accepted_nodes[node.entity_id] = node
                canonical_node_refs[proposed_ref] = VersionedRef(
                    id=node.entity_id, revision=node.revision
                )
            outcome.nodes = list(accepted_nodes.values())
            nodes.update(accepted_nodes)
            if self.reference_resolution:
                discovery_ref = task_protocol(task).get("discovery_ref")
                for resolution in outcome.reference_resolutions:
                    reference = VersionedRef.model_validate(resolution["entity_ref"])
                    node = nodes.get(reference.id)
                    if (discovery_ref is None or node is None
                            or node.revision != reference.revision
                            or node.class_iri != resolution["class_iri"]):
                        raise ValueError("reference_resolution_endpoint_invalid")
                    stored_resolution = deepcopy(resolution)
                    if is_record and resolution.get("binding_ref"):
                        state = json_value(task_protocol(task))
                        stored_resolution["binding_origin"] = {
                            "lineage_id": task.claim_lineage_id,
                            "task": task.model_dump(mode="json"),
                            "discovery_ref": state["discovery_ref"],
                            "verification_ref": state["verification_ref"],
                            "target": state["base_target"],
                            "entity_refs": state["reference_context"]["entity_refs"],
                        }
                    reference_resolutions[resolution["claim_ref"]["id"]] = stored_resolution
                    entity_origins.setdefault(reference.id, {
                        "lineage_id": member_owners.get(task.task_id, task.claim_lineage_id),
                        **({"member_task_id": task.task_id} if batch_member else {}),
                        "discovery_ref": discovery_ref,
                        "local_id": resolution["local_id"],
                        "claim_ref": resolution["claim_ref"], "scope": resolution["scope"],
                    })
            for collection, existing, kind in (
                (outcome.edges, edges, "edge"),
                (outcome.properties, properties, "property"),
                *(((outcome.relationship_groups, relationship_groups, "relationship_group"),)
                  if self.tool_protocol else ()),
            ):
                accepted = []
                heads = dict(existing)
                for candidate in collection:
                    references = [candidate.subject_ref]
                    if isinstance(candidate, GraphEdge):
                        references.append(candidate.object_ref)
                    elif isinstance(candidate, GraphRelationshipGroup):
                        references.extend(candidate.object_refs)
                    if any((ref.id, ref.revision) in conflicting_node_refs for ref in references):
                        diagnostics.append(f"adapter_{kind}_node_revision_conflict")
                        continue
                    if self.tool_protocol and (
                        candidate.scope != task.scope
                        or any(nodes.get(ref.id) is None or nodes[ref.id].revision != ref.revision
                               for ref in references)
                    ):
                        diagnostics.append(f"adapter_{kind}_scope_or_endpoint_mismatch")
                        continue
                    if is_record:
                        try:
                            record_predicate = resolve_record_predicate(
                                card, nodes[candidate.subject_ref.id].class_iri,
                                candidate.predicate_iri,
                            )
                            if isinstance(candidate, GraphProperty):
                                if not isinstance(record_predicate, SlotSpec):
                                    raise ValueError("record_predicate_kind_mismatch")
                            elif (not isinstance(record_predicate, EdgeSpec)
                                  or any(nodes[ref.id].class_iri
                                         not in record_predicate.range_class_iris
                                         for ref in references[1:])):
                                raise ValueError("record_relation_range_mismatch")
                        except ValueError:
                            diagnostics.append(f"adapter_{kind}_target_mismatch")
                            continue
                    elif (
                        candidate.subject_ref
                        != VersionedRef(id=task.subject.entity_id, revision=task.subject.revision)
                        or candidate.predicate_iri != task.predicate_iri
                    ):
                        diagnostics.append(f"adapter_{kind}_target_mismatch")
                        continue
                    if isinstance(candidate, GraphEdge):
                        object_ref = canonical_node_refs.get(
                            (candidate.object_ref.id, candidate.object_ref.revision),
                            candidate.object_ref,
                        )
                        object_node = nodes.get(object_ref.id)
                        if object_node is None or object_node.revision != object_ref.revision:
                            diagnostics.append("adapter_edge_object_revision_mismatch")
                            continue
                        candidate = candidate.model_copy(update={"object_ref": object_ref})
                    if isinstance(candidate, GraphProperty):
                        rejection = expert_rejected.get(assertion_identity(candidate))
                        if rejection:
                            candidate = candidate.model_copy(update={
                                "independent_review": "rejected", "reason_code": "expert_rejected",
                                "reason": rejection,
                            })
                    current = heads.get(candidate.candidate_id)
                    if self.tool_protocol:
                        if current and (candidate.revision < current.revision or (
                            candidate.revision == current.revision and candidate != current
                        )):
                            diagnostics.append(f"adapter_{kind}_revision_conflict")
                            continue
                        if current and candidate.revision > current.revision:
                            dependency_index.invalidate(versioned_key(
                                current.candidate_id, current.revision,
                            ))
                        heads[candidate.candidate_id] = candidate
                        accepted.append(candidate)
                        continue
                    if current is not None:
                        unchanged = current.model_dump(exclude={"revision"}) == (
                            candidate.model_dump(exclude={"revision"})
                        )
                        candidate = (
                            current if unchanged else candidate.model_copy(
                                update={"revision": current.revision + 1}
                            )
                        )
                        semantic_fields = {
                            "subject_ref", "predicate_iri", "polarity", "conditions",
                            "applicability", "object_ref", "raw_value", "normalized_value",
                        }
                        qualifies = (
                            candidate.decision_status == "supported"
                            and candidate.polarity == "affirmed"
                            and not candidate.conditions
                            and not candidate.applicability
                            and effective_proof_gate(candidate)
                        )
                        meaning_changed = current.model_dump(include=semantic_fields) != (
                            candidate.model_dump(include=semantic_fields)
                        )
                        was_usable = (
                            current.decision_status == "supported"
                            and current.polarity == "affirmed"
                            and not current.conditions
                            and not current.applicability
                            and effective_proof_gate(current)
                        )
                        if was_usable and not unchanged and (not qualifies or meaning_changed):
                            # Superseding a usable assertion cannot leave its
                            # old subject/descendant binding available for a
                            # later path to revive without actual revalidation.
                            dependency_index.invalidate(
                                versioned_key(current.candidate_id, current.revision)
                            )
                    heads[candidate.candidate_id] = candidate
                    accepted.append(candidate)
                collection[:] = accepted
            if not batch_member:
                model_calls += outcome.model_calls
                lineage_calls[task.claim_lineage_id] = (
                    lineage_calls.get(task.claim_lineage_id, 0) + outcome.model_calls
                )
            else:
                model_calls = call_totals[1]
            update_call_totals(task.claim_lineage_id)
            if is_record:
                from .attribute_calibration import merge_candidates

                prior = record_discovery[task.claim_lineage_id]
                record_discovery[task.claim_lineage_id] = {
                    **prior, "status": "examined" if outcome.complete else "incomplete",
                    "reason_code": outcome.reason_code,
                    "outcome_ref": task_protocol(task).get("outcome_ref"),
                    "attribute_candidates": [candidate.model_dump(mode="json") for candidate
                                             in merge_candidates(
                                                 prior.get("attribute_candidates", []), outcome,
                                             )],
                    **({"attribute_status": "resolved" if any(
                        prop.decision_status == "supported" and effective_proof_gate(prop)
                        for prop in outcome.properties
                    ) else "unresolved",
                        "disambiguation_attempts": prior.get("disambiguation_attempts", 0)
                        + int(task_protocol(task).get("request_attempt", 0)
                              > prior.get("attribute_request_attempt", 0)),
                        "attribute_request_attempt": task_protocol(task).get("request_attempt", 0),
                        } if task.purpose == "property_disambiguation" else {}),
                }
            else:
                semantic_outcomes = ([] if outcome.reason_code == "record_no_claims"
                                     else [outcome.semantic_outcome])
                if self.record_pipeline and task.predicate_kind == "relationship":
                    row = record_relation_inputs.get(task.claim_lineage_id)
                    if row is not None and row["task"]["task_id"] == task.task_id:
                        semantic_outcomes = list(dict.fromkeys([
                            *row.get("semantic_outcomes", []), *semantic_outcomes,
                        ]))
                        record_relation_inputs[task.claim_lineage_id] = {
                            **row, "semantic_outcomes": semantic_outcomes,
                        }
                plan = mark_record(
                    plan,
                    task.record_id,
                    coverage_state="examined" if outcome.complete or (
                        self.evidence_repair and not self.candidate_policy and task.retry_kind
                        and plan.ledger[task.record_id].coverage_state == "examined"
                    ) else "attempted_incomplete",
                    execution_state="finished" if outcome.complete else "retryable_failure",
                    semantic_outcomes=semantic_outcomes,
                    task_id=task.task_id,
                    reason_code=outcome.reason_code,
                )
                plans[key] = plan
                if self.candidate_policy:
                    searches[key].plan = plan
            payload = {
                "task": task.model_dump(mode="json"),
                "outcome": outcome.model_dump(mode="json"),
                "ranking_excluded_slots": [list(key) for key in sorted(excluded_slots)],
            }
            if self.current_state:
                work_digest = evidence_hash([work_digest, payload])
            if self.current_state:
                protocol = task_protocol(task)
                applied_model_results[task.claim_lineage_id] = {
                    "task_id": task.task_id,
                    "request_attempt": protocol.get("request_attempt", 0),
                    "evidence_revision": protocol.get("evidence_revision", 0),
                    "assertion_generation": protocol.get("assertion_generation", 0),
                    "outcome_hash": evidence_hash(outcome.model_dump(mode="json")),
                    **({"work_unit_id": member_owners[task.task_id],
                        "outcome_ref": outcome_ref, "result_version": result_version}
                       if batch_member else {}),
                }
            completed_tasks += 1
            if self.current_state:
                task_outcomes[:] = [payload]
            else:
                task_outcomes.append(payload)
            events.append(("task_outcome", payload))
            register_dependencies(task, outcome)
            paused = not outcome.complete and (
                outcome.reason_code == "execution_pause_requested" or review_waiting(task)
            )
            if paused:
                pause_counts[task.claim_lineage_id] = pause_counts.get(task.claim_lineage_id, 0) + 1
            if (not is_record and not batch_member
                    and task.claim_lineage_id not in expert_lineages) and (paused or (
                not outcome.complete
                and (not self.tool_protocol or not protocols.get(
                    task.claim_lineage_id, {},
                ).get("outcome_ref"))
                and (
                    task.retry_kind is None
                    or task.retry_kind.startswith("pause_continuation:")
                )
                and outcome.reason_code
                not in {
                    "subject_dependency_invalidated",
                    "record_model_call_budget_exhausted",
                    "context_budget_exceeded",
                    "model_budget_exhausted",
                    "tool_budget_exhausted",
                    "evidence_record_ambiguous",
                }
            )):
                retry = RecognitionTask.create(
                    subject=task.subject,
                    predicate_iri=task.predicate_iri,
                    predicate_kind=task.predicate_kind,
                    record_id=task.record_id,
                    phase=task.phase,
                    hop=task.hop,
                    dependency_hash=task.dependency_hash,
                    claim_lineage_id=task.claim_lineage_id,
                    retry_kind=(
                        "pause_continuation:"
                        + str(pause_counts[task.claim_lineage_id] if self.current_state else sum(
                            item["task"]["claim_lineage_id"] == task.claim_lineage_id
                            and item["outcome"]["reason_code"] == "execution_pause_requested"
                            for item in task_outcomes
                        ))
                        if paused
                        else "technical_once"
                    ),
                    section_node_id=task.section_node_id,
                    source_position=task.source_position,
                    scope=task.scope,
                )
                if self.tool_protocol:
                    retry = retry.model_copy(update={"task_id": task.task_id})
                if scheduler.enqueue(retry, root_branch=task.subject.is_document_root):
                    diagnostics.append(
                        "pause_continuation_scheduled"
                        if paused else "bounded_technical_retry_scheduled"
                    )
            for edge in outcome.edges:
                object_node = nodes[edge.object_ref.id]
                edges[edge.candidate_id] = edge
                candidate_tasks[edge.candidate_id] = task
                if self.tool_protocol:
                    continue
                process_conflicts(task, edge)
                if (
                    edge.decision_status == "supported"
                    and edge.polarity == "affirmed"
                    and not edge.conditions
                    and not edge.applicability
                    and effective_proof_gate(edge)
                    and dependency_index.is_valid(versioned_key(edge.candidate_id, edge.revision))
                ):
                    subject_key = f"subject:{object_node.entity_id}@{object_node.revision}"
                    was_invalid = not dependency_index.is_valid(subject_key)
                    dependency_index.add_proof(
                        subject_key,
                        [versioned_key(edge.candidate_id, edge.revision)],
                    )
                    restored = was_invalid and dependency_index.restore(subject_key)
                    subject = SubjectRef(
                        entity_id=object_node.entity_id,
                        revision=object_node.revision,
                        class_iri=object_node.class_iri,
                    )
                    if layered_recognition:
                        subject_key = (subject.entity_id, subject.revision)
                        registered = registered_subjects.get(subject_key)
                        if registered is None:
                            registered = {
                                "subject": subject, "hop": task.hop + 1,
                                "binding_refs": [], "reopen": restored,
                            }
                            registered_subjects[subject_key] = registered
                            if registered["hop"] > self.max_hops:
                                scheduler.unexplored_frontier.append({
                                    "subject": subject.model_dump(mode="json"),
                                    "reason": "max_hops", "hop": registered["hop"],
                                    "logical_records": 1,
                                })
                        else:
                            if self.current_state:
                                registered_subjects.touch(subject_key)
                            registered["hop"] = min(registered["hop"], task.hop + 1)
                            registered["reopen"] = registered["reopen"] or restored
                        reference = {"id": edge.candidate_id, "revision": edge.revision}
                        if reference not in registered["binding_refs"]:
                            registered["binding_refs"].append(reference)
                    if not layered_recognition or dependency_ready:
                        if dependency_ready and task.hop + 1 > self.max_hops:
                            continue
                        try:
                            menu = compile_local_menu(
                                self.ontology, subject, engine=self.engine,
                            )
                        except ValueError:
                            diagnostics.append(
                                f"object_class_outside_frozen_snapshot:{object_node.class_iri}"
                            )
                        else:
                            menus[subject.entity_id] = menu
                            add_subject(
                                subject, menu, hop=task.hop + 1, reopen=restored, binding_edge=edge
                            )
            for item in outcome.properties:
                properties[item.candidate_id] = item
                candidate_tasks[item.candidate_id] = task
                if not self.tool_protocol:
                    process_conflicts(task, item)
            if self.tool_protocol:
                for group in outcome.relationship_groups:
                    relationship_groups[group.candidate_id] = group
                    candidate_tasks[group.candidate_id] = task
                if not batch_member and not is_record:
                    expand_tool_relations(task, outcome)
            if is_record:
                changed = [node for node in outcome.nodes
                           if previous_nodes.get(node.entity_id) != node]
                created_ids = {node.entity_id for node in changed
                               if previous_nodes[node.entity_id] is None}
                changed_ids = {node.entity_id for node in changed}
                # A new, independently proved alias also changes endpoint evidence.
                changed_ids.update(item["entity_ref"]["id"]
                                   for item in outcome.reference_resolutions
                                   if item.get("binding_ref"))
                apply_candidate_changes(CandidateChanges(
                    created_entity_refs=[VersionedRef(id=n.entity_id, revision=n.revision)
                                         for n in changed if previous_nodes[n.entity_id] is None],
                    changed_entity_refs=[
                        VersionedRef(id=identity, revision=nodes[identity].revision)
                        for identity in sorted(changed_ids)
                        if identity not in created_ids and identity in nodes
                    ],
                    property_refs=[VersionedRef(id=p.candidate_id, revision=p.revision)
                                   for p in outcome.properties],
                    affected_record_ids=[task.record_id],
                ))
            if self.record_pipeline:
                observe_record_gaps(task)
                if not is_record and task.predicate_kind == "relationship":
                    finish_relation_page(task, outcome)

        def discovery_feedback(task, source_refs, entity_refs, reason, *, input_signature=None):
            if remaining_calls(task) < 2 or not source_refs:
                return False
            row = record_discovery.get(task.claim_lineage_id)
            state = task_protocol(task)
            if (row is None or row["status"] == "active" or not row.get("outcome_ref")
                    or state.get("pending_request") is not None
                    or row.get("feedback_reopens", 0)
                    >= self.record_policy.max_feedback_reopens):
                return False
            units = {unit.evidence_id for record_id in (task.source_record_ids or [task.record_id])
                     for unit in index.by_id[record_id].source_units}
            if any(anchor.evidence_id not in units for anchor in source_refs):
                return False
            for anchor in source_refs:
                index.ir.resolve(anchor)
            feedback_hash = evidence_hash([
                task.record_id, source_refs, entity_refs, reason,
                *([input_signature] if input_signature is not None else []),
            ])
            if feedback_hash in {row.get("feedback_hash"), row.get("consumed_feedback_hash")}:
                return False
            record_discovery[task.claim_lineage_id] = {
                **row, "status": "pending", "feedback_hash": feedback_hash,
                "feedback_reason": reason,
                "feedback_refs": json_value(entity_refs),
                "feedback_source_refs": json_value(source_refs),
                "feedback_reopens": row.get("feedback_reopens", 0) + 1,
            }
            return True

        def observe_record_gaps(task):
            from .claim_protocol import FrozenClaimSet
            from .reference_context import has_reference_hint
            from .source_citations import resolve_fragment_quote

            state = task_protocol(task)
            if not state.get("discovery_ref"):
                return
            owner, owner_args = task_result_owner(task)
            frozen = FrozenClaimSet.model_validate(load_result(
                owner, state["discovery_ref"], "discovery", **owner_args,
            ))
            observations = [item for item in frozen.observations
                            if item.kind in {"missing", "unbound", "ambiguous", "unknown"}]
            if self.independent_entity_traversal and isinstance(task, RecordDiscoveryTask):
                # Keep late-owner/entity reference discovery, without reopening
                # the source for attribute completeness in the candidate phase.
                observations = [item for item in observations if item.kind == "unbound"]
            # Semantic ordering can visit an anaphoric paragraph before its
            # antecedent. Retain that explicit source hint for the existing
            # bounded late-owner feedback; it is not an identity decision.
            reference_hints = [
                index.ir.anchor(unit.evidence_id, 0, len(unit.text))
                for rid in (task.source_record_ids or [task.record_id])
                for unit in index.by_id[rid].source_units
                if has_reference_hint(unit.text)
            ] if (isinstance(task, RecordDiscoveryTask) and not frozen.reference_bindings
                  and task.purpose == "entity_discovery") else []
            if not observations and not reference_hints:
                return
            context = (prepare_record_context(task)[0] if isinstance(task, RecordDiscoveryTask)
                       else prepare_member_context(task))
            anchors = list(reference_hints)
            for item in observations:
                try:
                    anchor, _ = resolve_fragment_quote(
                        item.quote.evidence_id, item.quote.text, context.fragments,
                        context_text=item.quote.context_text, fact_required=True,
                    )
                except ValueError:
                    continue
                anchors.append(anchor)
            if isinstance(task, RecordDiscoveryTask):
                if task.purpose == "property_disambiguation":
                    return  # Field work is awakened by its exact candidate/input signature.
                row = record_discovery[task.claim_lineage_id]
                record_discovery[task.claim_lineage_id] = {
                    **row, "gap_refs": json_value(anchors),
                }
                return
            predicate = predicates[task_key(task)]
            if not isinstance(predicate, EdgeSpec):
                return
            attempted_cards = attempted_discovery_cards(task.record_id)
            if anchors:
                discovery_search.admit_feedback(
                    task.record_id, set(predicate.range_class_iris), attempted_cards,
                )
            # Relation tasks run only after a registered range endpoint exists.
            # Their observations may prioritize a different unattempted card,
            # but cannot reopen an already paid entity-discovery lineage.  A
            # replay sees the same fact scope and produces duplicate entities;
            # bridge/predicate proof gaps belong to the relation outcome.

        def attempted_discovery_cards(record_id):
            return {
                row["task"]["schema_card_id"]
                for row in record_discovery.values()
                if record_id in (
                    row["task"].get("source_record_ids") or [row["task"]["record_id"]]
                )
            }

        def register_entity_subject(reference):
            node = nodes[reference.id]
            if node.root:
                return
            subject = SubjectRef(entity_id=node.entity_id, revision=node.revision,
                                 class_iri=node.class_iri)
            refs = [ref for ref in (node.type_decision_ref, node.referent_decision_ref,
                                   node.referent_ref, node.composition_decision_ref,
                                   *node.dependency_refs) if ref is not None]
            if (node.decision_status != "supported" or node.independent_review == "rejected"
                    or not node.type_decision_ref or not node.referent_decision_ref
                    or not node.referent_ref
                    or (node.grounding_kind == "record" and not node.composition_decision_ref)
                    or not dependency_index.is_valid_references([reference, *refs])):
                raise ValueError("record_entity_identity_unverified")
            origin = entity_origins.get(node.entity_id)
            if origin is None:
                raise ValueError("record_entity_origin_missing")
            key = subject_key(subject, root_scope)
            if key not in registered_subjects:
                registered_subjects[key] = {
                    "subject": subject, "scope": root_scope,
                    "hop": discovery_catalog.class_depths[node.class_iri],
                    "binding_refs": [], "reopen": False, "origin": origin,
                }
            dependency_index.add_proof(scoped_subject_dependency(subject, root_scope), [
                versioned_key(reference.id, reference.revision),
                *(versioned_key(ref.id, ref.revision) for ref in refs),
            ])
            # Keep the frozen local menu available for an explicit property
            # review/repair on this proved entity.  Merely compiling the menu
            # does not admit any traversal work.
            menus[node.entity_id] = compile_local_menu(
                self.ontology, subject, engine=self.engine,
            )
            if self.independent_entity_traversal:
                add_subject(
                    subject, menus[node.entity_id],
                    hop=registered_subjects[key]["hop"], scope=root_scope,
                )
            # Independent entity exploration needs no incoming relationship
            # proof. The legacy verification policy still admits traversal via
            # expand_tool_relations after an incoming proof is available.

        def relation_input_signature(task):
            from .reference_context import select_reference_entities

            predicate = predicates[task_key(task)]
            selected = select_reference_entities(
                task, index, nodes, entity_origins, reference_resolutions,
                {task.subject.class_iri, *predicate.range_class_iris}, limit=None,
            )
            references = [VersionedRef(id=identity, revision=nodes[identity].revision)
                          for identity in selected]
            bindings = [item["binding_ref"] for item in reference_resolutions.values()
                        if item.get("binding_ref") and item["entity_ref"]["id"] in selected
                        and any(ref["evidence_id"] in {
                            unit.evidence_id for unit in index.by_id[task.record_id].source_units
                        } for ref in item["source_refs"])]
            return evidence_hash([task.subject, task.predicate_iri, task.record_id,
                                  task.scope, references, bindings])

        def relation_pages(task):
            predicate = predicates[task_key(task)]
            candidates = {identity: node for identity, node in nodes.items()
                          if node.decision_status == "supported"
                          and node.independent_review != "rejected"
                          and dependency_index.is_valid_references([
                              VersionedRef(id=node.entity_id, revision=node.revision),
                              *node.dependency_refs,
                          ])}
            pages = reference_pages(
                task, index, candidates, entity_origins, reference_resolutions,
                {task.subject.class_iri, *predicate.range_class_iris},
                page_size=self.record_policy.endpoint_page_size,
            )
            result = [[{"id": identity, "revision": nodes[identity].revision}
                       for identity in dict.fromkeys([
                           root.entity_id, task.subject.entity_id, *page,
                       ])] for page in pages]
            return [page for page in result if any(
                nodes[ref["id"]].class_iri in predicate.range_class_iris for ref in page
            )]

        def hold_relation_for_endpoints(task):
            """Keep dependency-blocked work without spending a recognition call."""
            key = task_key(task)
            predicate = predicates[key]
            previous = record_relation_inputs.get(task.claim_lineage_id, {})
            record_relation_inputs[task.claim_lineage_id] = {
                **previous, "task": task.model_dump(mode="json"),
                "dependency_hash": relation_input_signature(task),
                "page_refs": [], "remaining_pages": [], "status": "waiting_endpoints",
            }
            plans[key] = mark_record(
                plans[key], task.record_id,
                coverage_state=plans[key].ledger[task.record_id].coverage_state,
                execution_state="blocked_dependency", reason_code="relation_endpoint_missing",
            )
            if self.candidate_policy:
                searches[key].plan = plans[key]
            classes = set(predicate.range_class_iris)
            if discovery_search.prioritize(task.record_id, classes):
                return
            # Every selected region has already had its entity pass.  A missing
            # endpoint leaves this relation blocked; replaying the same entity
            # request does not add source authority and repeatedly rereads the
            # same paragraph.  A distinct unattempted card may still be moved
            # forward by prioritize() above.

        def defer_unready_relation(excluded_slots):
            if not self.record_pipeline or active_unit_ref is not None or expert_pending:
                return False
            task = scheduler.peek_task(excluded_slots)
            if (task is None or task.predicate_kind != "relationship"
                    or relation_pages(task)):
                return False
            deferred = scheduler.next_task(excluded_slots=excluded_slots)
            if deferred.task_id != task.task_id:
                raise ValueError("relation_admission_task_changed")
            # Only actual recognition work consumes the logical task budget.
            scheduler.dispatched -= 1
            hold_relation_for_endpoints(task)
            if work_hook is not None:
                work_hook(current_work_changes())
            return True

        def queue_relation_page(task, *, refresh=False):
            row = record_relation_inputs[task.claim_lineage_id]
            pages = relation_pages(task) if refresh else row.get("remaining_pages", [])
            if not pages:
                if refresh:
                    hold_relation_for_endpoints(task)
                else:
                    record_relation_inputs[task.claim_lineage_id] = {**row, "status": "examined"}
                return
            signature = relation_input_signature(task) if refresh else row["dependency_hash"]
            dependency_hash = evidence_hash([signature, pages[0]])
            retry = RecognitionTask.create(
                subject=task.subject, predicate_iri=task.predicate_iri,
                predicate_kind="relationship", record_id=task.record_id,
                phase=task.phase, hop=task.hop, scope=task.scope,
                dependency_hash=dependency_hash, claim_lineage_id=task.claim_lineage_id,
                retry_kind="candidate_page:" + dependency_hash,
                section_node_id=task.section_node_id, source_position=task.source_position,
            )
            admitted = remaining_calls(task) > 0 and scheduler.enqueue(
                retry, root_branch=retry.subject.is_document_root,
            )
            semantic_outcomes = row.get("semantic_outcomes", [])
            if refresh:
                # New endpoints reopen remaining work, not already proved edges.
                # Preserve supported coverage only while its exact proof is valid.
                semantic_outcomes = ["supported"] if any(
                    candidate_tasks.get(edge.candidate_id) is not None
                    and candidate_tasks[edge.candidate_id].claim_lineage_id == task.claim_lineage_id
                    and edge.decision_status == "supported" and effective_proof_gate(edge)
                    and dependency_index.is_valid(versioned_key(edge.candidate_id, edge.revision))
                    for edge in edges.values()
                ) else []
            record_relation_inputs[task.claim_lineage_id] = {
                "task": retry.model_dump(mode="json"), "dependency_hash": signature,
                "page_refs": pages[0], "remaining_pages": pages[1:],
                "status": "pending" if admitted else "incomplete",
                "semantic_outcomes": semantic_outcomes,
            }
            key = task_key(task)
            plans[key] = mark_record(
                plans[key], task.record_id,
                coverage_state=("unattempted" if plans[key].ledger[
                    task.record_id].coverage_state == "unattempted" else "attempted_incomplete"),
                execution_state="queued" if admitted else "retryable_failure",
                semantic_outcomes=semantic_outcomes,
                task_id=retry.task_id,
                reason_code=("candidate_page_pending" if admitted
                             else "candidate_page_budget_exhausted"),
            )
            if self.candidate_policy:
                searches[key].plan = plans[key]

        def finish_relation_page(task, outcome):
            row = record_relation_inputs.get(task.claim_lineage_id)
            if row is None or row["task"]["task_id"] != task.task_id:
                return
            if not outcome.complete:
                record_relation_inputs[task.claim_lineage_id] = {**row, "status": "incomplete"}
                return
            refresh = relation_input_signature(task) != row["dependency_hash"]
            queue_relation_page(task, refresh=refresh)

        def prioritize_waiting_endpoint_discovery():
            """Close one planned relation dependency before unrelated discovery."""
            if any(
                relation.subject_ref.id == root.entity_id
                and relation.subject_ref.revision == root.revision
                and relation.decision_status == "supported"
                and relation.polarity == "affirmed"
                and not relation.conditions
                and not relation.applicability
                and effective_proof_gate(relation)
                and dependency_index.is_valid(versioned_key(
                    relation.candidate_id, relation.revision,
                ))
                for relation in [*edges.values(), *relationship_groups.values()]
            ):
                return None
            for row in record_relation_inputs.values():
                if row.get("status") != "waiting_endpoints":
                    continue
                task = RecognitionTask.model_validate(row["task"])
                if relation_pages(task):
                    queue_relation_page(task, refresh=True)
                    return "relation_ready"
                predicate = predicates[task_key(task)]
                if discovery_search.admit_dependency(
                    task.record_id,
                    set(predicate.range_class_iris),
                    attempted_discovery_cards(task.record_id),
                ):
                    return "discovery_ready"
            return None

        def apply_candidate_changes(changes):
            references = [*changes.created_entity_refs, *changes.changed_entity_refs]
            for reference in references:
                register_entity_subject(reference)
            affected_classes = {nodes[ref.id].class_iri for ref in references}
            affected_ids = {ref.id for ref in references}
            for lineage, row in list(record_relation_inputs.items()):
                original = RecognitionTask.model_validate(row["task"])
                predicate = predicates[task_key(original)]
                if (original.subject.entity_id not in affected_ids
                        and not affected_classes.intersection(predicate.range_class_iris)):
                    continue
                if (row.get("status") in {"pending", "active"}
                        or relation_input_signature(original) == row["dependency_hash"]):
                    continue
                queue_relation_page(original, refresh=True)
            if references:
                # Attribute retries are coalesced at the end of normal discovery.
                for row in list(record_discovery.values()):
                    if not row.get("gap_refs") or row["status"] not in {"examined", "incomplete"}:
                        continue
                    task = RecordDiscoveryTask.model_validate(row["task"])
                    if task.purpose == "property_disambiguation":
                        continue
                    if not affected_classes.intersection(
                        discovery_cards[task.schema_card_id].class_iris,
                    ):
                        continue
                    inputs = tool_dependencies(task, ignore_pinned=True, include_bindings=False)
                    refs = [VersionedRef.model_validate(entity["entity_ref"])
                            for entity in inputs["entity_dependencies"]]
                    if affected_ids.intersection(ref.id for ref in refs):
                        discovery_feedback(
                            task, [EvidenceAnchor.model_validate(ref) for ref in row["gap_refs"]],
                            refs, "late_owner_for_unbound_source",
                        )
                events.append(("candidate_changes", changes.model_dump(mode="json")))

        def expand_tool_relations(task, outcome):
            if self.independent_entity_traversal:
                # Registered entities already own their legal candidate menus.
                # An unverified edge grants no proof or inherited fact scope.
                return
            for relation in [*outcome.edges, *outcome.relationship_groups]:
                members = (relation.object_refs if isinstance(relation, GraphRelationshipGroup)
                           else [relation.object_ref])
                for member in members:
                    eligibility = frontier_eligibility(
                        relation, member=member, inherited_scope=task.scope,
                        dependencies=dependency_index,
                    )
                    if not eligibility.eligible:
                        scheduler.unexplored_frontier.append({
                            "relation_ref": {"id": relation.candidate_id,
                                             "revision": relation.revision},
                            "member_ref": member.model_dump(mode="json"),
                            "scope": task.scope.model_dump(mode="json"),
                            "reason": eligibility.reason_code, "logical_records": 0,
                        })
                        continue
                    node = nodes[member.id]
                    refs = [ref for ref in (node.type_decision_ref, node.referent_decision_ref,
                                             node.referent_ref, node.composition_decision_ref,
                                             *node.dependency_refs) if ref is not None]
                    if (node.root or node.decision_status != "supported"
                            or not node.type_decision_ref or not node.referent_decision_ref
                            or not node.referent_ref
                            or not dependency_index.is_valid_references([member, *refs])):
                        diagnostics.append("frontier_member_identity_unverified")
                        continue
                    subject = SubjectRef(entity_id=node.entity_id, revision=node.revision,
                                         class_iri=node.class_iri)
                    scope = eligibility.scope
                    key = subject_key(subject, scope)
                    registration = registered_subjects.get(key)
                    if registration is None:
                        origin = entity_origins.get(node.entity_id) or next((
                            item["origin"] for item in registered_subjects.values()
                            if item["subject"] == subject and "origin" in item
                        ), None)
                        if origin is None and node.entity_id in {
                            item.entity_id for item in outcome.nodes
                        }:
                            discovery_ref = task_protocol(task).get(
                                "discovery_ref"
                            )
                            if discovery_ref:
                                origin = {"lineage_id": member_owners.get(
                                    task.task_id, task.claim_lineage_id),
                                          **({"member_task_id": task.task_id}
                                             if task.task_id in member_owners else {}),
                                          "discovery_ref": discovery_ref}
                        if origin is None:
                            diagnostics.append("frontier_entity_origin_missing")
                            continue
                        registration = {"subject": subject, "scope": scope, "hop": task.hop + 1,
                                        "binding_refs": [], "reopen": False, "origin": origin}
                        registered_subjects[key] = registration
                    else:
                        registered_subjects.touch(key)
                    reference = {"id": relation.candidate_id, "revision": relation.revision}
                    if reference not in registration["binding_refs"]:
                        registration["binding_refs"].append(reference)
                    dependency_index.add_proof(scoped_subject_dependency(subject, scope), [
                        versioned_key(relation.candidate_id, relation.revision),
                        versioned_key(node.entity_id, node.revision),
                        *[versioned_key(ref.id, ref.revision) for step in scope.members
                          for ref in (step.relation_ref, step.member_ref)],
                    ])
                    if layered_recognition and not dependency_ready:
                        continue
                    try:
                        menu = compile_local_menu(self.ontology, subject, engine=self.engine)
                    except ValueError:
                        diagnostics.append(f"object_class_outside_frozen_snapshot:{node.class_iri}")
                        continue
                    menus[subject.entity_id] = menu
                    add_subject(subject, menu, hop=task.hop + 1, binding_edge=relation, scope=scope)

        def tool_dependencies(task, *, selected_refs=None, include_bindings=True,
                              ignore_pinned=False):
            from app.services.extraction.ontology_guided.claim_protocol import (
                EntityDependencyView,
                FrozenClaimSet,
                VerificationScopeResolution,
                VerificationScopeStep,
            )

            is_record = isinstance(task, RecordDiscoveryTask)
            attribute_bindings = None
            if is_record and task.purpose == "property_disambiguation":
                sections = {index.by_id[identity].section_node_id for identity in
                            reading_sources.get(task.record_id, [task.record_id])}
                attribute_bindings = []
                for resolution in reference_resolutions.values():
                    node = nodes.get(resolution["entity_ref"]["id"])
                    binding = resolution.get("binding_ref")
                    if (node is None or not binding
                            or resolution["entity_ref"]["revision"] != node.revision
                            or resolution["scope"] != task.scope.model_dump(mode="json")
                            or not any(ref["section_node_id"] in sections
                                       for ref in resolution["source_refs"])
                            or not dependency_index.is_valid_references([
                                VersionedRef.model_validate(ref) for ref in [
                                    resolution["entity_ref"], binding,
                                    *resolution["dependency_refs"],
                                ]
                            ])):
                        continue
                    attribute_bindings.append(resolution)
            selected_nodes = ({root.entity_id: root} if is_record else {
                root.entity_id: root, task.subject.entity_id: nodes[task.subject.entity_id],
            })
            if is_record and root.class_iri not in discovery_cards[task.schema_card_id].class_iris:
                selected_nodes = {}
            steps = []
            for step in task.scope.members:
                relation = (edges.get(step.relation_ref.id)
                            or relationship_groups.get(step.relation_ref.id))
                member = nodes.get(step.member_ref.id)
                if (relation is None or relation.revision != step.relation_ref.revision
                        or member is None or member.revision != step.member_ref.revision
                        or not dependency_index.is_valid_references([
                            step.relation_ref, step.member_ref,
                        ])):
                    raise ValueError("scope_dependency_invalidated")
                endpoints = ([relation.object_ref] if isinstance(relation, GraphEdge)
                             else relation.object_refs)
                if step.member_ref not in endpoints:
                    raise ValueError("scope_member_mismatch")
                for reference in [relation.subject_ref, *endpoints]:
                    node = nodes.get(reference.id)
                    if node is None or node.revision != reference.revision:
                        raise ValueError("scope_endpoint_revision_mismatch")
                    selected_nodes[node.entity_id] = node
                steps.append(VerificationScopeStep(
                    relation_ref=step.relation_ref, member_ref=step.member_ref,
                    selection=(relation.selection if isinstance(relation, GraphRelationshipGroup)
                               else None), polarity=relation.polarity, modality=relation.modality,
                    conditions=relation.conditions,
                    applicability=relation.applicability.get("qualifiers", []),
                    evidence_refs=relation.evidence_refs,
                ))
            if self.reference_resolution:
                pinned = ({"entity_refs": selected_refs} if selected_refs is not None else
                          None if ignore_pinned else task_protocol(task).get("reference_context"))
                if pinned is not None:
                    selected_nodes = {}
                    for reference in pinned["entity_refs"]:
                        node = nodes.get(reference["id"])
                        if node is None or node.revision != reference["revision"]:
                            raise ValueError("reference_context_revision_changed")
                        selected_nodes[node.entity_id] = node
                else:
                    from app.services.extraction.ontology_guided.reference_context import (
                        select_reference_entities,
                    )

                    predicate = None if is_record else predicates[task_key(task)]
                    classes = (set(discovery_cards[task.schema_card_id].class_iris) if is_record
                               else {task.subject.class_iri})
                    if isinstance(predicate, EdgeSpec):
                        classes.update(predicate.range_class_iris)
                    candidates = {
                        identity: node for identity, node in nodes.items()
                        if node.decision_status == "supported"
                        and node.independent_review != "rejected"
                        and dependency_index.is_valid_references([
                            VersionedRef(id=node.entity_id, revision=node.revision),
                            *node.dependency_refs,
                        ])
                    }
                    if is_record and task.purpose == "property_disambiguation":
                        sections = {index.by_id[r].section_node_id
                                    for r in reading_sources.get(task.record_id, [task.record_id])}
                        for identity, candidate in candidates.items():
                            if candidate.class_iri in classes and (any(
                                ref.section_node_id in sections for ref in candidate.evidence_refs
                            ) or any(resolution["entity_ref"]["id"] == identity
                                     for resolution in attribute_bindings)):
                                selected_nodes[identity] = candidate
                    else:
                        records = (task.source_record_ids or [task.record_id]) if is_record else [
                            task.record_id,
                        ]
                        for record_id in records:
                            local = task.model_copy(update={"record_id": record_id})
                            for identity in select_reference_entities(
                                local, index, candidates, entity_origins, reference_resolutions,
                                classes, limit=None if self.record_pipeline else 8,
                            ):
                                selected_nodes[identity] = nodes[identity]
            views = []
            endpoint_attributes = []
            for node in selected_nodes.values():
                reference = VersionedRef(id=node.entity_id, revision=node.revision)
                proposal = None
                references = []
                if not node.root:
                    references = [ref for ref in (
                        node.type_decision_ref, node.referent_decision_ref, node.referent_ref,
                        node.composition_decision_ref, *node.dependency_refs,
                    ) if ref is not None]
                    if (node.decision_status != "supported" or not node.type_decision_ref
                            or not node.referent_decision_ref or not node.referent_ref
                            or (node.grounding_kind == "record"
                                and not node.composition_decision_ref)
                            or not dependency_index.is_valid_references([reference, *references])):
                        raise ValueError("entity_dependency_invalidated")
                    origin = entity_origins.get(node.entity_id) or next((item["origin"]
                                   for item in registered_subjects.values()
                                   if item["subject"].entity_id == node.entity_id
                                   and item["subject"].revision == node.revision
                                   and "origin" in item), None)
                    if origin is None:
                        raise ValueError("entity_dependency_origin_missing")
                    frozen = FrozenClaimSet.model_validate(load_result(
                        origin["lineage_id"], origin["discovery_ref"], "discovery",
                        member_task_id=origin.get("member_task_id"),
                    ))
                    matches = [item for item in frozen.entities if (
                        item.local_id == origin["local_id"]
                        and frozen.local_ref_map.get(item.local_id).model_dump(mode="json")
                        == origin["claim_ref"]
                    )] if "local_id" in origin else [
                        item for item in frozen.entities
                        if frozen.local_ref_map.get(item.local_id) == reference
                    ]
                    if len(matches) != 1:
                        raise ValueError("entity_dependency_origin_mismatch")
                    proposal = matches[0]
                    if self.evidence_review:
                        # Read the original, immutable discovery observations.
                        # Current property verdicts never become a prerequisite
                        # for exploring this entity's relationships.
                        endpoint_attributes.extend({
                            "subject_ref": reference.model_dump(mode="json"),
                            "predicate_iri": prop.predicate_iri,
                            "value_quote": prop.value_quote.model_dump(mode="json"),
                            "field_support": [q.model_dump(mode="json")
                                              for q in prop.field_support],
                            "unit_support": [q.model_dump(mode="json") for q in prop.unit_support],
                            "qualifiers": prop.qualifiers.model_dump(mode="json"),
                        } for prop in frozen.properties if prop.subject_id == proposal.local_id
                            and prop.local_id not in frozen.claim_issues)
                payload = {
                    "entity_ref": reference.model_dump(mode="json"),
                    "class_iri": node.class_iri, "grounding_kind": node.grounding_kind,
                    "root_origin": node.root_origin if node.root else None,
                    "proposal": proposal.model_dump(mode="json") if proposal else None,
                    "source_refs": [ref.model_dump(mode="json") for ref in node.evidence_refs],
                    "dependency_refs": [ref.model_dump(mode="json") for ref in references],
                }
                views.append(EntityDependencyView.model_validate({
                    **payload, "content_hash": evidence_hash(payload),
                }))
            resolution = VerificationScopeResolution(scope_id=task.scope.scope_id, steps=steps)
            binding_inputs = {}
            if self.record_pipeline and not is_record and include_bindings:
                from .context import assemble_record_discovery_context
                from .record_binding_evidence import restore_record_binding

                binding_views, binding_evidence, source_refs = [], {}, []
                units = {unit.evidence_id for unit in index.by_id[task.record_id].source_units}
                for saved in reference_resolutions.values():
                    origin = saved.get("binding_origin")
                    if (not origin or not saved.get("binding_ref")
                            or saved["entity_ref"]["id"] not in selected_nodes
                            or not any(ref["evidence_id"] in units
                                       for ref in saved["source_refs"])):
                        continue
                    ref = VersionedRef.model_validate(saved["binding_ref"])
                    if not dependency_index.is_valid_references([
                        ref, *(VersionedRef.model_validate(value)
                               for value in saved["dependency_refs"]),
                    ]):
                        continue
                    origin_task = RecordDiscoveryTask.model_validate(origin["task"])
                    original = tool_dependencies(
                        origin_task, selected_refs=origin["entity_refs"], include_bindings=False,
                    )
                    origin_dependencies = [EntityDependencyView.model_validate(value)
                                           for value in original["entity_dependencies"]]
                    card = discovery_cards[origin_task.schema_card_id]
                    original_context = assemble_record_discovery_context(
                        origin_task, card, index,
                        document_context=DocumentContext.model_validate(
                            origin["target"]["document_context"],
                        ),
                        ontology_hash=self.ontology.ontology_hash,
                        entity_dependencies=origin_dependencies,
                    )
                    view, evidence = restore_record_binding(
                        ref, origin={**origin, "task_scope": origin_task.scope},
                        context=original_context, card=card, dependencies=origin_dependencies,
                        nodes=[GraphNode.model_validate(value)
                               for value in original["entity_nodes"]],
                        load_result=load_result,
                    )
                    if not dependency_index.is_valid_references(view.dependency_refs):
                        continue
                    binding_views.append(view.model_dump(mode="json"))
                    binding_evidence[(ref.id, ref.revision)] = evidence
                    source_refs.extend(fragment.bounded_anchor().model_dump(mode="json")
                                       for fragment in original_context.fragments)
                valid_refs = frozenset(
                    (reference["id"], reference["revision"]) for view in binding_views
                    for reference in view["dependency_refs"]
                ) | frozenset(
                    (reference.id, reference.revision) for entity in views
                    for reference in entity.dependency_refs
                    if dependency_index.is_valid_references([reference])
                )
                binding_inputs = {
                    "reference_dependencies": binding_views,
                    "reference_evidence": binding_evidence,
                    "reference_is_valid": lambda ref: (ref.id, ref.revision) in valid_refs,
                    "reference_source_refs": source_refs,
                }
            return {
                **binding_inputs,
                "endpoint_attributes": endpoint_attributes,
                "entity_dependencies": [view.model_dump(mode="json") for view in views],
                "entity_nodes": [node.model_dump(mode="json") for node in selected_nodes.values()],
                "bridge_dependencies": [],
                "scope_resolutions": [resolution.model_dump(mode="json")],
                **({"reference_resolutions": [
                    resolution for resolution in (
                        reference_resolutions.values() if attribute_bindings is None
                        else attribute_bindings
                    )
                    if resolution["entity_ref"]["id"] in selected_nodes
                    and resolution["scope"] == task.scope.model_dump(mode="json")
                    and dependency_index.is_valid_references([
                        VersionedRef.model_validate(ref)
                        for ref in resolution["dependency_refs"]
                    ])
                ]} if self.reference_resolution else {}),
            }

        def attach_protocol_results(context, task):
            state = context.protocol_state
            references = {ref: "model_turn" for ref in state.get("turn_refs", [])}
            references.update({ref: "tool_result"
                               for ref in state.get("completed_tool_results", [])})
            references.update({entry["result_ref"]: "tool_result"
                               for entry in state.get("materialized_refs", {}).values()})
            for field in ("discovery", "verification", "outcome"):
                if state.get(field + "_ref"):
                    references[state[field + "_ref"]] = field
            for batch in (state.get("verification_batches") or {}).get("batches", []):
                if batch.get("result_ref"):
                    references[batch["result_ref"]] = "verification"
            context.protocol_results = {
                reference: {"lineage_id": task.claim_lineage_id, "field": field,
                            "value": load_result(task.claim_lineage_id, reference, field)}
                for reference, field in references.items()
            }

        def prepare_member_context(task):
            key = task_key(task)
            predicate = predicates[key]
            record = index.by_id[task.record_id]
            scope_hash = evidence_hash(
                [
                    record.record_id,
                    [unit.evidence_id for unit in record.source_units],
                    task.subject.model_dump(mode="json"),
                    task.predicate_iri,
                    *([task.scope.scope_id] if self.tool_protocol else []),
                ]
            )
            context_hash = evidence_hash(
                [index.source_text(record.record_id, include_context=True), scope_hash]
            )
            target = VerificationTarget.create(
                run_fingerprint=run_fingerprint,
                claim_ref=VersionedRef(id=task.claim_lineage_id, revision=1),
                task_id=task.task_id,
                check_kind="predicate_entailment",
                document_context=DocumentContext(
                    document_hash=ir.document_hash,
                    document_class_iri=root_class_iri,
                    root_ref=VersionedRef(id=root.entity_id, revision=root.revision),
                ),
                subject_ref=task.subject,
                predicate_iri=task.predicate_iri,
                assertion_polarity="affirmed",
                ontology_hash=self.ontology.ontology_hash,
                source_scope_hash=scope_hash,
                context_hash=context_hash,
            )
            target_seed = target.model_dump(mode="json")
            selected_refs = None
            if self.record_pipeline and task.predicate_kind == "relationship":
                row = record_relation_inputs.get(task.claim_lineage_id)
                if row is None:
                    pages = relation_pages(task)
                    row = {"task": task.model_dump(mode="json"),
                           "dependency_hash": relation_input_signature(task),
                           "page_refs": pages[0], "remaining_pages": pages[1:], "status": "active"}
                    record_relation_inputs[task.claim_lineage_id] = row
                if row["task"]["task_id"] == task.task_id:
                    selected_refs = row["page_refs"]
                # A restored/older paid task always retains its frozen authorization.
                pinned = task_protocol(task).get("reference_context")
                if pinned is not None:
                    selected_refs = pinned["entity_refs"]
            tool_inputs = (tool_dependencies(task, selected_refs=selected_refs)
                           if self.tool_protocol else {})
            subject_sources = list(nodes[task.subject.entity_id].evidence_refs)
            incoming = [
                edge
                for edge in edges.values()
                if edge.object_ref.id == task.subject.entity_id
                and edge.object_ref.revision == task.subject.revision
                and edge.decision_status == "supported"
                and edge.polarity == "affirmed"
                and effective_proof_gate(edge)
                and dependency_index.is_valid(
                    versioned_key(edge.candidate_id, edge.revision)
                )
            ]
            dependencies = (
                [
                    subject_dependency_ref(task.subject, task.scope)
                ]
                if not task.subject.is_document_root
                else []
            )
            required = [anchor for edge in incoming for anchor in edge.evidence_refs]
            if self.tool_protocol:
                dependencies.extend(ref for step in task.scope.members
                                    for ref in (step.relation_ref, step.member_ref))
                required.extend(
                    EvidenceAnchor.model_validate(anchor)
                    for entity in tool_inputs["entity_dependencies"]
                    for anchor in entity["source_refs"]
                )
                required.extend(EvidenceAnchor.model_validate(anchor)
                                for anchor in tool_inputs.get("reference_source_refs", []))
                required.extend(
                    EvidenceAnchor.model_validate(anchor)
                    for resolution in tool_inputs["scope_resolutions"]
                    for step in resolution["steps"] for anchor in step["evidence_refs"]
                )
            if self.evidence_repair:
                required.extend(evidence_work.source_refs(task))
            counters = required_by_lineage.get(task.claim_lineage_id, [])
            context_started = time.perf_counter()
            context = assemble_context(
                target,
                task.record_id,
                index,
                subject_evidence_refs=subject_sources,
                required_context_refs=required,
                counterevidence_refs=counters,
                retrieval_context_refs=(searches[key].group_context_refs(task.record_id)
                                        if self.adaptive_policy else None),
                proof_dependencies=dependencies,
                subject_label=nodes[task.subject.entity_id].label,
                predicate=predicate,
                ontology=self.ontology,
                repair_enabled=self.evidence_repair,
            )
            operation_id = expert_lineages.get(task.claim_lineage_id)
            if operation_id:
                operation = expert_operations[operation_id]
                context.expert_feedback = repair_feedback(
                    operation["target"], operation,
                )
            context.incremental_performance = self.incremental_performance
            context.compact_recognition = dependency_ready
            if has_protocol:
                context.protocol_state = (thaw_json if self.incremental_performance
                                          else deepcopy)(
                    task_protocol(task))
            if self.tool_protocol:
                context.tool_inputs = {
                    **tool_inputs, "target_seed": target_seed,
                    **({"recognition_pipeline": RECORD_PIPELINE,
                        "graph_phase": self.record_policy.graph_phase}
                       if self.record_pipeline else {}),
                    "run_fingerprint": run_fingerprint,
                    "subject_node": nodes[task.subject.entity_id].model_dump(
                        mode="json",
                    ),
                }
                if not self.batch_policy:
                    attach_protocol_results(context, task)
            if search_index is not None:
                search_event("context_assembly", {
                    "task_id": task.task_id,
                    "elapsed_seconds": time.perf_counter() - context_started,
                })
            context.remaining_model_calls = remaining_calls(task)
            if not self.batch_policy:
                context.bind_model_call_hook(
                    lambda stage, ordinal, bound_task=task: reserve_model_call(
                        bound_task, stage, ordinal
                    )
                )
            return context

        def advance_layer():
            nonlocal active_hop, layer_phase
            if (dependency_ready or not layered_recognition or scheduler.pending
                    or pending_ranking is not None):
                return False
            active_keys = [key for key in plans if slot_is_active(key)]
            if any(
                any(entry.coverage_state != "examined" for entry in plans[key].ledger.values())
                or (key in searches and searches[key].status
                    not in {"pass_exhausted", "local_results_only"})
                for key in active_keys
                if slot_subject_is_active(key)
            ) or any(item["status"] in {"queued", "incomplete", "deferred"}
                     for item in evidence_work.items.values()):
                return False
            if layer_phase == "property":
                layer_phase = "relationship"
            else:
                following = [item["hop"] for item in registered_subjects.values()
                             if active_hop < item["hop"] <= self.max_hops
                             and subject_is_active(item["subject"], item.get("scope"))]
                if not following:
                    return False
                active_hop = min(following)
                layer_phase = "property"
            events.append(("recognition_layer_activated", {
                "hop": active_hop, "phase": layer_phase, "after_outcomes": completed_tasks,
            }))
            for registration in registered_subjects.values():
                subject = registration["subject"]
                scope = registration.get("scope")
                if (registration["hop"] != active_hop
                        or not subject_is_active(subject, scope)):
                    continue
                binding = None
                if not subject.is_document_root:
                    relations = {**edges, **relationship_groups} if self.tool_protocol else edges
                    binding = next((relations[ref["id"]] for ref in registration["binding_refs"]
                                    if ref["id"] in relations
                                    and relations[ref["id"]].revision == ref["revision"]
                                    and dependency_index.is_valid(
                                        f"{ref['id']}@{ref['revision']}"
                                    )), None)
                    if binding is None:
                        continue
                try:
                    menu = compile_local_menu(
                        self.ontology, subject, engine=self.engine,
                    )
                except ValueError:
                    diagnostics.append(f"object_class_outside_frozen_snapshot:{subject.class_iri}")
                    continue
                menus[subject.entity_id] = menu
                add_subject(subject, menu, hop=active_hop, reopen=registration["reopen"],
                            binding_edge=binding, scope=scope)
                registration["reopen"] = False
                if self.current_state:
                    registered_subjects.touch(subject_key(subject, scope))
            return True

        def repair_summary():
            summary = evidence_work.summary() if self.evidence_repair else {}
            if expert_operations:
                summary["expert_review"] = {"operations": {
                    key: {"status": value["status"], "result": deepcopy(value["result"])}
                    for key, value in expert_operations.items()
                }}
            return summary

        def apply_review_events():
            nonlocal work_digest
            for event in review_replay.at(completed_tasks):
                value = event["payload"]
                if self.current_state:
                    work_digest = evidence_hash([work_digest, event])
                if event["kind"] == "review":
                    base_key = (value["candidate_id"], value["candidate_revision"])
                    target = {**value, "candidate_revision": expert_review_heads.get(
                        base_key, value["candidate_revision"],
                    )}
                    updated = apply_property_review(target, properties, dependency_index)
                    expert_review_heads[base_key] = updated.revision
                    identity = assertion_identity(updated)
                    if value["decision"] == "rejected":
                        expert_rejected[identity] = value["reason"]
                        key = (updated.subject_ref.id, updated.subject_ref.revision,
                               updated.predicate_iri)
                        slot_completion.reopen(key)
                        expert_reopened_slots.add(key)
                        if key in searches:
                            searches[key].reopen_satisfied()
                        conflict_claims.discard(updated.candidate_id)
                    else:
                        expert_rejected.pop(identity, None)
                        # A confirmation changes the candidate reference, not its
                        # valid conflict-survey scope. Update the receipt exactly.
                        key = (updated.subject_ref.id, updated.subject_ref.revision,
                               updated.predicate_iri)
                        receipt = slot_completion.get(key)
                        if receipt:
                            slot_completion.close(key, plan=plans[key], candidate=updated,
                                                  checked_record_ids=receipt["checked_record_ids"],
                                                  after_outcomes=completed_tasks)
                elif event["kind"] == "repair_stop":
                    operation_id = value["operation_id"]
                    operation = expert_operations[operation_id]
                    if self.current_state:
                        expert_operations.touch(operation_id)
                    operation["status"] = value["status"]
                    operation["result"] = deepcopy(value["result"])
                    expert_pending[:] = [t for t in expert_pending
                                         if expert_lineages[t.claim_lineage_id] != operation_id]
                else:
                    operation_id = value["operation_id"]
                    target = value["target"]
                    menu = menus.get(target["subject_ref"]["id"])
                    if menu is None:
                        raise ValueError("expert repair subject has no frozen local menu")
                    tasks, reason = local_repair_tasks(target, value, menu, index)
                    operation = {**deepcopy(value), "status": "running" if tasks else "unresolved",
                                 "max_model_calls": min(MAX_REPAIR_CALLS,
                                                        value.get("max_model_calls", 32)),
                                 "result": {"tasks_attempted": 0, "model_calls": 0,
                                            "replacement_candidate_refs": [],
                                            "reason_code": reason}}
                    expert_operations[operation_id] = operation
                    for task in tasks:
                        key = task_key(task)
                        if key not in plans:
                            if not self.record_pipeline or target["original_task"].get(
                                "kind",
                            ) != "record_discovery":
                                raise ValueError(
                                    "expert repair predicate was not admitted for subject",
                                )
                            predicate = next(p for p in menu.properties
                                             if p.iri == task.predicate_iri)
                            plan = plan_slot(
                                task.subject, predicate, index, metadata,
                                ontology_hash=self.ontology.ontology_hash, sparse_candidates=True,
                            )
                            plans[key] = enable_plan_parts(plan)
                            predicates[key] = predicate
                        plans[key] = admit_records(plans[key], index, [task.record_id],
                                                   phase=task.phase)
                        if key in searches:
                            searches[key].plan = plans[key]
                            searches[key].admit_expert_record(task.record_id, operation_id)
                        expert_lineages[task.claim_lineage_id] = operation_id
                        expert_pending.append(task)
                review_replay.applied.add(event["event_id"])
                events.append(("expert_" + event["kind"], deepcopy(value)))

        def observe_expert_task(task, outcome):
            operation_id = expert_lineages[task.claim_lineage_id]
            operation = expert_operations[operation_id]
            if self.current_state:
                expert_operations.touch(operation_id)
            result = operation["result"]
            result["tasks_attempted"] += 1
            if self.batch_policy:
                # Expert repairs are singleton work units with their own paid
                # lineages; result.model_calls=0 is not their physical cost.
                result["model_calls"] = sum(
                    reserved_calls.get(lineage, 0)
                    for lineage, owner in expert_lineages.items() if owner == operation_id
                )
            else:
                result["model_calls"] += outcome.model_calls
            result["replacement_candidate_refs"] = [
                {"id": candidate.candidate_id, "revision": candidate.revision}
                for candidate in properties.values()
                if candidate_tasks.get(candidate.candidate_id) is not None
                and expert_lineages.get(candidate_tasks[candidate.candidate_id].claim_lineage_id)
                == operation_id
                and candidate.decision_status == "supported" and effective_proof_gate(candidate)
                and dependency_index.is_valid(f"{candidate.candidate_id}@{candidate.revision}")
                and assertion_identity(candidate) not in expert_rejected
            ]
            if not outcome.complete:
                result["reason_code"] = outcome.reason_code
            elif result["reason_code"] == "execution_pause_requested":
                result["reason_code"] = None
            if result["tasks_attempted"] >= min(16, operation.get("max_tasks", 16)):
                if any(expert_lineages[t.claim_lineage_id] == operation_id for t in expert_pending):
                    expert_pending[:] = [t for t in expert_pending
                                         if expert_lineages[t.claim_lineage_id] != operation_id]
                    result["reason_code"] = "expert_repair_task_limit"
            if (outcome.reason_code == "execution_pause_requested"
                    and result["tasks_attempted"] < min(16, operation.get("max_tasks", 16))):
                retry = task.model_copy(update={"task_id": stable_id("expert-pause", [
                    task.task_id, result["tasks_attempted"],
                ])})
                expert_pending.insert(0, retry)
            if not any(expert_lineages[t.claim_lineage_id] == operation_id for t in expert_pending):
                if result["replacement_candidate_refs"] and result["reason_code"] is None:
                    operation["status"] = "completed"
                else:
                    operation["status"] = "unresolved"
                    result["reason_code"] = result["reason_code"] or "expert_repair_no_replacement"

        part_generations = {
            tuple(row["key"]): row["value"]["plan_id"]
            for row in (direct or {}).get("plans", {}).values()
        }
        current_plan_parts = SlotParts((direct or {}).get("plan_parts", {}), part_generations)
        current_search_parts = SlotParts((direct or {}).get("search_parts", {}), part_generations)

        def current_work_changes():
            maps = {
                "nodes": nodes,
                "edges": edges,
                "properties": properties,
                "plans": plans,
                "predicates": predicates,
                "menus": menus,
                "subject_priorities": subject_priorities,
                "proof_generations": proof_generations,
                "ranking_contexts": ranking_contexts,
                "search_tasks": search_tasks,
                "attribute_sources": attribute_sources,
                "lineage_calls": lineage_calls,
                "candidate_tasks": candidate_tasks,
                "required_by_lineage": required_by_lineage,
                "recheck_counts": recheck_counts,
                "pause_counts": pause_counts,
                "applied_model_results": applied_model_results,
                "expert_lineages": expert_lineages,
                "expert_rejected": expert_rejected,
                "expert_review_heads": expert_review_heads,
                "registered_subjects": registered_subjects,
                "expert_operations": expert_operations,
                "evidence_work": evidence_work.items,
                "slot_completion": slot_completion.receipts,
            }
            if self.tool_protocol:
                maps["relationship_groups"] = relationship_groups
            if self.record_pipeline:
                maps["record_discovery"] = record_discovery
                maps["record_relation_inputs"] = record_relation_inputs
                maps["record_searches"] = discovery_search.rows
            if self.reference_resolution:
                maps["entity_origins"] = entity_origins
                maps["reference_resolutions"] = reference_resolutions
            plan_rows = {}
            for key in list(plans.changed):
                plan = plans.get(key)
                plan_rows.update(current_plan_parts.changes(
                    key, plan.plan_id if plan else None,
                    {"ledger": plan.ledger.drain(), "records": plan.records.rows.drain()}
                    if plan else {},
                ))
            counts = {name: len(values) for name, values in maps.items()}
            counts["searches"] = len(searches)
            result = {name: values.drain() for name, values in maps.items()}
            result["plan_parts"] = plan_rows

            search_rows = {}
            for key in list(searches.changed):
                search = searches.get(key)
                search_rows.update(current_search_parts.changes(
                    key, search.plan.plan_id if search else None,
                    search_current_changes(search) if search else {},
                ))
            result["search_parts"] = search_rows
            result["searches"] = searches.drain()
            result.update(scheduler.current_changes())
            result.update(dependency_index.current_changes())
            result["applied_epochs"] = applied_epochs.drain()
            result["control"] = {
                "current": {
                    "version": 1,
                    **({"extraction_protocol": TOOL_PROTOCOL_VERSION}
                       if self.tool_protocol else {}),
                    "completed_tasks": completed_tasks,
                    **({"record_discovery": {
                        "turn": discovery_turn,
                        "catalog_hash": discovery_catalog.analysis_scope_ref,
                        "card_ids": list(discovery_cards),
                        "search_scope_hash": discovery_search.scope_hash,
                        "total": len(record_discovery) + discovery_search.pending,
                    }} if self.record_pipeline else {}),
                    "model_calls": model_calls,
                    **({"active_unit_ref": active_unit_ref} if self.batch_policy else {}),
                    "partition_counts": counts,
                    "work_digest": work_digest,
                    "run_fingerprint": run_fingerprint,
                    "recognition_run_id": recognition_run_id,
                    "active_hop": active_hop,
                    "layer_phase": layer_phase,
                    "conflict_events": sorted(conflict_events),
                    "conflict_claims": sorted(conflict_claims),
                    "budget_stopped_slots": [list(k) for k in task_budget_stopped_slots],
                    "expert_pending": json_value(expert_pending),
                    "expert_reopened_slots": json_value(expert_reopened_slots),
                    "review_replay": review_replay.snapshot(),
                    "applied_reviews": sorted(review_replay.applied),
                    "review_results": json_value(review_replay.results),
                    "semantic_wait_key": json_value(semantic_wait_key),
                    "semantic_wait_started_at": semantic_wait_started_at,
                    "diagnostics": list(dict.fromkeys(diagnostics)),
                    "frontier_policy": {
                        "schema_version": 2 if lazy_frontier else 1,
                        "template_interleaving": template_interleaving,
                        **(
                            {"candidate_planning": {"policy": self.candidate_policy}}
                            if self.candidate_policy
                            else {}
                        ),
                        **(
                            {"layered_recognition": {"version": recognition_order}}
                            if layered_recognition
                            else {}
                        ),
                    },
                }
            }
            return result

        if direct is not None:
            control = direct["control"]["current"]
            active_unit_ref = control.get("active_unit_ref")
            if self.record_pipeline:
                saved_discovery = control.get("record_discovery", {})
                # Region execution cards are deterministic unions recorded in
                # the search rows. Rebuild them before validating the frozen
                # card set so a cold resume does not repeat ranking or paid work.
                discovery_search.restore(direct.get("record_searches", {}))
                if (saved_discovery.get("catalog_hash") != discovery_catalog.analysis_scope_ref
                        or set(saved_discovery.get("card_ids", [])) != set(discovery_cards)
                        or saved_discovery.get("search_scope_hash") != discovery_search.scope_hash):
                    raise ValueError("record_discovery_scope_changed")
                discovery_turn = saved_discovery["turn"]
            if active_unit_ref is not None and (
                not self.batch_policy or active_unit_ref not in protocols
            ):
                raise ValueError("active_work_unit_missing")
            if (
                control["version"] != 1
                or control["run_fingerprint"] != run_fingerprint
                or control["recognition_run_id"] != recognition_run_id
                or control.get("extraction_protocol") != (
                    TOOL_PROTOCOL_VERSION if self.tool_protocol else None
                )
            ):
                raise ValueError("current work state identity mismatch")
            for name, count in control["partition_counts"].items():
                if len(direct.get(name, {})) != count:
                    raise ValueError("current work partition is missing records")

            def loaded(name, decode=lambda value: value, encode=json_value):
                return WorkMap.load(direct.get(name, {}), decode=decode, encode=encode)

            nodes = loaded("nodes", GraphNode.model_validate)
            edges = loaded("edges", GraphEdge.model_validate)
            properties = loaded("properties", GraphProperty.model_validate)
            if self.tool_protocol:
                relationship_groups = loaded(
                    "relationship_groups", GraphRelationshipGroup.model_validate,
                )
            plan_parts = {}
            for item in sorted(
                direct.get("plan_parts", {}).values(), key=lambda r: r.get("position", 0)
            ):
                plan_parts.setdefault(tuple(item["slot"]), {}).setdefault(item["name"], {})[
                    item["value"]["key"]
                ] = item["value"]["value"]
            plans = WorkMap(encode=plan_header)
            for row in sorted(direct.get("plans", {}).values(), key=lambda r: r.get("position", 0)):
                key, value = tuple(row["key"]), row["value"]
                parts = plan_parts.get(key, {})
                value = dict(value)
                if value.pop("current_record_count") != len(parts.get("records", {})):
                    raise ValueError("current plan records are missing")
                plans[key] = enable_plan_parts(
                    RetrievalPlan.model_validate(
                        {
                            **value,
                            "ledger": parts.get("ledger", {}),
                            "records": list(parts.get("records", {}).values()),
                        }
                    ),
                    restored=True,
                )
            plans.changed.clear()
            predicates = loaded(
                "predicates",
                lambda v: (SlotSpec if v["kind"] == "property" else EdgeSpec).model_validate(v),
            )
            menus = loaded("menus", LocalMenu.model_validate)
            subject_priorities = loaded("subject_priorities", lambda v: [tuple(p) for p in v])
            proof_generations = loaded("proof_generations")
            ranking_contexts = loaded(
                "ranking_contexts",
                lambda v: {
                    **(
                        {"ontology": self.ontology}
                        if self.ontology.lexical_context is not None
                        else {}
                    ),
                    "mentions": [QueryMention.model_validate(m) for m in v["mentions"]],
                    "dependency_refs": [
                        VersionedRef.model_validate(r) for r in v["dependency_refs"]
                    ],
                },
                encode=lambda v: json_value({k: x for k, x in v.items() if k != "ontology"}),
            )
            for restored_key, restored_plan in plans.items():
                dependencies = ranking_contexts.get(restored_plan.plan_id, {}).get(
                    "dependency_refs", [],
                )
                identity = semantic_retrieval_plan_key(
                    restored_plan,
                    dependency_refs=dependencies,
                    generation=proof_generations.get(restored_plan.subject.entity_id, 0),
                )
                previous = semantic_plan_keys.get(identity)
                if previous is not None and previous != restored_key:
                    raise ValueError("duplicate semantic retrieval plan in current state")
                semantic_plan_keys[identity] = restored_key
            search_tasks = loaded("search_tasks", lambda v: {
                **v, **({"scope": TraversalScope.model_validate(v["scope"])}
                        if self.tool_protocol else {}),
            })
            attribute_sources = loaded("attribute_sources")
            lineage_calls = loaded("lineage_calls")
            candidate_tasks = loaded(
                "candidate_tasks", lambda value: (RecordDiscoveryTask if
                    value.get("kind") == "record_discovery"
                    else RecognitionTask).model_validate(value),
            )
            record_discovery = loaded("record_discovery")
            record_relation_inputs = loaded("record_relation_inputs")
            required_by_lineage = loaded(
                "required_by_lineage", lambda v: [EvidenceAnchor.model_validate(a) for a in v]
            )
            recheck_counts = loaded("recheck_counts")
            pause_counts = loaded("pause_counts")
            applied_model_results = loaded("applied_model_results")
            entity_origins = loaded("entity_origins")
            reference_resolutions = loaded("reference_resolutions")
            expert_lineages = loaded("expert_lineages")
            expert_rejected = loaded("expert_rejected")
            expert_review_heads = loaded("expert_review_heads")
            scheduler = FrontierScheduler.from_current(
                direct, max_hops=self.max_hops, max_tasks=self.max_tasks
            )
            dependency_index = DependencyIndex.from_current(direct)
            evidence_work.items = loaded("evidence_work")
            completed_tasks, model_calls = control["completed_tasks"], control["model_calls"]
            work_digest = control["work_digest"]
            registered_subjects = loaded(
                "registered_subjects",
                lambda item: {**item, "subject": SubjectRef.model_validate(item["subject"]),
                              **({"scope": TraversalScope.model_validate(item["scope"])}
                                 if self.tool_protocol else {})},
            )
            active_hop, layer_phase = control["active_hop"], control["layer_phase"]
            slot_completion.receipts = loaded("slot_completion")
            applied_epochs = WorkSet.load(direct.get("applied_epochs", {}))
            unapplied_epochs = {
                e.epoch_id: e for e in ranking.epochs if e.epoch_id not in applied_epochs
            }
            conflict_events, conflict_claims = (
                set(control["conflict_events"]),
                set(control["conflict_claims"]),
            )
            task_budget_stopped_slots = {tuple(k) for k in control["budget_stopped_slots"]}
            expert_pending = [RecognitionTask.model_validate(t) for t in control["expert_pending"]]
            expert_operations = loaded("expert_operations")
            expert_reopened_slots = {tuple(k) for k in control["expert_reopened_slots"]}
            review_replay = ReviewReplay(control["review_replay"])
            review_replay.applied = set(control["applied_reviews"])
            review_replay.results = deepcopy(control["review_results"])
            semantic_wait_key = (
                tuple(control["semantic_wait_key"])
                if control["semantic_wait_key"] is not None
                else None
            )
            semantic_wait_started_at = control["semantic_wait_started_at"]
            searches = WorkMap(
                encode=lambda search: {
                    "subject_mentions": search.subject_mentions,
                    "seed_record_ids": search.seed_record_ids,
                    "priority_record_ids": search.priority_record_ids,
                    **(
                        {
                            "permission_scope_hash": search.permission_scope_hash,
                            "query_dependency_hash": search.query_dependency_hash,
                        }
                        if self.adaptive_policy
                        else {}
                    ),
                }
            )
            for row in sorted(
                direct.get("searches", {}).values(), key=lambda r: r.get("position", 0)
            ):
                key, value = tuple(row["key"]), row["value"]
                search_type, extra = HeuristicSlotSearch, {}
                if self.adaptive_policy:
                    from app.services.extraction.ontology_guided.adaptive_search import (
                        AdaptiveSlotSearch,
                    )

                    search_type = AdaptiveSlotSearch
                    saved = value
                    extra = {
                        "adaptive_policy": self.adaptive_policy,
                        "permission_scope_hash": saved["permission_scope_hash"],
                        "query_dependency_hash": saved["query_dependency_hash"],
                    }
                search = search_type(
                    plan=plans[key],
                    predicate=predicates[key],
                    search_index=search_index,
                    policy=self.heuristic_policy,
                    run_fingerprint=run_fingerprint,
                    **{
                        k: value[k]
                        for k in ("subject_mentions", "seed_record_ids", "priority_record_ids")
                    },
                    **extra,
                )
                parts = {}
                for item in sorted(
                    direct.get("search_parts", {}).values(), key=lambda r: r.get("position", 0)
                ):
                    if tuple(item["slot"]) == key:
                        parts.setdefault(item["name"], {})[item["key"]] = item["value"]
                restore_search_parts(search, parts)
                search.on_work_change = lambda k=key: searches.touch(k)
                searches[key] = search
                search.continue_search()
            searches.changed.clear()
            if root.entity_id not in nodes or nodes[root.entity_id] != root:
                raise ValueError("current work state root mismatch")
            for plan in plans.values():
                validate_record_universe(plan, index)

        def prepare_batch_context(unit, inputs=None):
            inputs = inputs or {}
            members = []
            for task in unit.members:
                member = inputs.get(task.task_id)
                if member is None:
                    member = MemberContext(
                        task_id=task.task_id, context=prepare_member_context(task),
                        card=compile_schema_card(
                            menus[task.subject.entity_id], predicate_iri=task.predicate_iri,
                            profile=self.adapter.profile, scope=task.scope,
                        ),
                    )
                members.append(member)
            state = json_value(protocols.get(unit.work_unit_id, {}))
            records = {}
            shared = {ref: "model_turn" for ref in state.get("turn_refs", [])}
            shared.update({ref: "tool_result" for ref in state.get("completed_tool_results", [])})
            for member_state in state.get("member_states", {}).values():
                shared.update({entry["result_ref"]: "tool_result"
                               for entry in member_state["materialized_refs"].values()})
            for ref, field in shared.items():
                records[ref] = load_record(unit.work_unit_id, ref, field)
            for field in ("discovery", "verification", "outcome"):
                for task_id, ref in state.get(field + "_refs", {}).items():
                    records[ref] = load_record(
                        unit.work_unit_id, ref, field, member_task_id=task_id,
                    )
            return RecognitionBatchContext(
                work_unit_id=unit.work_unit_id, members=members,
                protocol_state=state, protocol_results=records,
                remaining_model_calls_by_member={
                    t.task_id: remaining_calls(t) for t in unit.members
                },
            )

        def select_record_task():
            nonlocal active_unit_ref
            if active_unit_ref is not None:
                state = protocols[active_unit_ref]
                if state["version"] != RECORD_PROTOCOL:
                    return None
                return RecordDiscoveryTask.model_validate(json_value(state)["task"])
            if scheduler.dispatched >= scheduler.max_tasks:
                return None
            for row in record_discovery.values():
                if row["status"] == "pending" and row.get("feedback_hash"):
                    scheduler.dispatched += 1
                    return RecordDiscoveryTask.model_validate(row["task"])
            discovery_search.prepare(
                ranking, persist_ranking_boundary if ranking_hook is not None else lambda: None,
            )
            dependency_state = prioritize_waiting_endpoint_discovery()
            if dependency_state == "relation_ready":
                return None
            selected = discovery_search.take()
            if selected is None:
                return None
            source_ids, card_id, field_id = selected
            record = index.by_id[source_ids[0]]
            task = RecordDiscoveryTask.create(
                run_fingerprint=run_fingerprint, record_id=record.record_id,
                schema_card_id=card_id, analysis_scope_ref=discovery_catalog.analysis_scope_ref,
                dependency_hash=evidence_hash([ir.document_hash, record.record_id,
                                               index.source_text(record.record_id,
                                                                 include_context=True), card_id,
                                               *([source_ids, field_id] if contextual else [])]),
                source_record_ids=source_ids, field_id=field_id,
                reading_section_ids=reading_sections.get(record.record_id, []),
            )
            record_discovery[task.claim_lineage_id] = {
                "task": task.model_dump(mode="json"), "status": "pending",
                "outcome_ref": None, "reason_code": None,
            }
            scheduler.dispatched += 1
            return task

        def calibration_state(task, inputs, candidates):
            from .attribute_calibration import calibration_inputs

            return calibration_inputs(
                task, candidates, inputs, [*edges.values(), *relationship_groups.values()], index,
                is_valid=lambda relation: (
                    relation.decision_status == "supported" and effective_proof_gate(relation)
                    and dependency_index.is_valid(versioned_key(
                        relation.candidate_id, relation.revision,
                    ))
                ),
            )

        def schedule_attribute_calibration():
            if self.independent_entity_traversal:
                return False
            """Coalesce relevant changes after discovery; retain the original paid lineage."""
            if not self.record_pipeline or scheduler.dispatched >= scheduler.max_tasks:
                return False
            from .attribute_disambiguation import attribute_options
            from .claim_protocol import EntityDependencyView

            queued = False
            for lineage, row in list(record_discovery.items()):
                if (row["status"] not in {"examined", "incomplete"}
                        or not row.get("attribute_candidates") or row.get("calibration_attempts", 0)
                        or remaining_calls(RecordDiscoveryTask.model_validate(row["task"])) < 2):
                    continue
                task = RecordDiscoveryTask.model_validate(row["task"])
                candidates = [AttributeCalibrationCandidate.model_validate(value)
                              for value in row["attribute_candidates"]]
                inputs = tool_dependencies(task, ignore_pinned=True, include_bindings=False)
                signature, related, source_context = calibration_state(task, inputs, candidates)
                if signature == row.get("calibration_input_signature"):
                    continue
                refs = [VersionedRef.model_validate(entity["entity_ref"])
                        for entity in inputs["entity_dependencies"]]
                sources = [ref for candidate in candidates for ref in candidate.value_refs]
                if task.purpose == "property_disambiguation":
                    field = attribute_fields[task.field_id]
                    options, reason, _ = attribute_options(
                        field, discovery_cards[task.schema_card_id], index,
                        [EntityDependencyView.model_validate(value)
                         for value in inputs["entity_dependencies"]],
                        context_record_ids=reading_sources[field.record_id],
                        max_candidates=contextual.max_attribute_candidates,
                        reference_resolutions=inputs.get("reference_resolutions", []),
                    )
                    if reason or row.get("disambiguation_attempts", 0) >= (
                        contextual.max_disambiguation_attempts
                    ):
                        continue
                    refs = list({option["subject_ref"]["id"]:
                                 VersionedRef.model_validate(option["subject_ref"])
                                 for option in options}.values())
                if discovery_feedback(task, sources, refs, "attribute_calibration_ready",
                                      input_signature=signature):
                    record_discovery[lineage] = {
                        **record_discovery[lineage], "calibration_attempts": 1,
                        "calibration_context_refs": json_value(source_context),
                        "calibration_relations": related,
                    }
                    queued = True
            if queued:
                # Publish pending feedback before the following active generation.
                if model_call_hook is not None:
                    persist_call_boundary(work_changes=current_work_changes())
                elif work_hook is not None:
                    work_hook(current_work_changes())
            return queued

        def prepare_record_context(task):
            from .claim_protocol import EntityDependencyView
            from .context import assemble_record_discovery_context

            card = discovery_cards[task.schema_card_id]
            row = record_discovery.get(task.claim_lineage_id, {})
            feedback_hash = row.get("feedback_hash")
            pending_feedback = bool(feedback_hash and feedback_hash != task_protocol(task).get(
                "record_feedback_hash",
            ))
            feedback_refs = None
            if pending_feedback:
                frozen_refs = task_protocol(task).get(
                    "reference_context", {},
                ).get("entity_refs", [])
                feedback_refs = list({ref["id"]: ref
                                      for ref in [*frozen_refs, *row["feedback_refs"]]}.values())
            inputs = tool_dependencies(
                task,
                selected_refs=(row["feedback_refs"] if pending_feedback
                               and task.purpose == "property_disambiguation" else feedback_refs),
            )
            candidates = [AttributeCalibrationCandidate.model_validate(value)
                          for value in row.get("attribute_candidates", [])]
            if not row.get("calibration_input_signature") or pending_feedback:
                signature, related, relation_sources = calibration_state(task, inputs, candidates)
                row = {**row, "calibration_input_signature": signature}
                if row.get("calibration_attempts"):
                    row.update(calibration_relations=related,
                               calibration_context_refs=json_value(relation_sources))
                record_discovery[task.claim_lineage_id] = row
            if row.get("calibration_attempts"):
                current_relations = {**edges, **relationship_groups}
                for source in row.get("calibration_relations", []):
                    current = current_relations.get(source["candidate_id"])
                    if (current is None or current.revision != source["revision"]
                            or not effective_proof_gate(current)
                            or not dependency_index.is_valid(versioned_key(
                                current.candidate_id, current.revision,
                            ))):
                        raise ExecutionLost("calibration_dependency_invalidated")
                inputs["attribute_calibration"] = {
                    "candidates": [candidate.model_dump(mode="json") for candidate in candidates],
                    "related_relations": row.get("calibration_relations", []),
                }
            if task.purpose == "property_disambiguation":
                from .attribute_calibration import field_candidate
                from .attribute_disambiguation import attribute_options, field_payload

                field = attribute_fields[task.field_id]
                options, reason, signature = attribute_options(
                    field, card, index,
                    [EntityDependencyView.model_validate(value)
                     for value in inputs["entity_dependencies"]],
                    context_record_ids=reading_sources[task.record_id],
                    max_candidates=contextual.max_attribute_candidates,
                    reference_resolutions=inputs.get("reference_resolutions", []),
                )
                inputs["attribute_disambiguation"] = {
                    **field_payload(field), "options": options, "reason_code": reason,
                    "signature": signature,
                }
                record_discovery[task.claim_lineage_id] = {
                    **row, "attribute_field": field_payload(field), "attribute_options": options,
                    "attribute_signature": signature,
                    "attribute_status": row.get("attribute_status", "pending"),
                    "disambiguation_attempts": row.get("disambiguation_attempts", 0),
                    "attribute_candidates": row.get("attribute_candidates", [field_candidate(
                        task, field_payload(field), options, reason=reason, card=card,
                    ).model_dump(mode="json")]),
                }
            elif contextual:
                from .attribute_disambiguation import field_payload

                fields = {
                    field.field_id: field
                    for record_id in task.source_record_ids
                    for field in deferred_fields.get(record_id, [])
                }
                inputs[
                    "property_fields" if region_property_fields else "deferred_property_fields"
                ] = [field_payload(fields[identity]) for identity in sorted(fields)]
            context = assemble_record_discovery_context(
                task, card, index,
                document_context=DocumentContext(
                    document_hash=ir.document_hash, document_class_iri=root_class_iri,
                    root_ref=VersionedRef(id=root.entity_id, revision=root.revision),
                ),
                ontology_hash=self.ontology.ontology_hash,
                entity_dependencies=[EntityDependencyView.model_validate(value)
                                     for value in inputs["entity_dependencies"]],
                binding_context_refs=[*[
                    EvidenceAnchor.model_validate(ref)
                    for ref in row.get("calibration_context_refs", [])
                ], *(
                    [EvidenceAnchor.model_validate(ref)
                     for resolution in inputs.get("reference_resolutions", [])
                     for ref in [*resolution["source_refs"],
                                 *resolution.get("binding_source_refs", [])]]
                    if task.purpose == "property_disambiguation" else []
                )],
            )
            context.protocol_state = json_value(task_protocol(task))
            context.tool_inputs = {
                **inputs, "schema_card": card.model_dump(mode="json"),
                "target_seed": context.target.model_dump(mode="json"),
                "run_fingerprint": run_fingerprint, "recognition_pipeline": RECORD_PIPELINE,
                "graph_phase": self.record_policy.graph_phase,
                **({"record_feedback_hash": feedback_hash, "record_feedback": {
                    "hash": feedback_hash, "refs": row["feedback_refs"],
                    "source_refs": row["feedback_source_refs"],
                    "reason": row.get("feedback_reason"),
                }} if feedback_hash else {}),
            }
            context.remaining_model_calls = remaining_calls(task)
            attach_protocol_results(context, task)
            return context, card

        def execute_record_task(task):
            nonlocal active_unit_ref, discovery_turn, model_calls
            context, card = prepare_record_context(task)
            prior_calls = reserved_calls.get(task.claim_lineage_id, 0)
            row = record_discovery[task.claim_lineage_id]
            reopening = bool(row.get("feedback_hash") and row["feedback_hash"]
                             != context.protocol_state.get("record_feedback_hash"))
            record_discovery[task.claim_lineage_id] = {
                **row, "status": "active",
                **({"consumed_feedback_hash": row["feedback_hash"],
                    "generation": context.protocol_state["assertion_generation"] + 1}
                   if reopening else {}),
            }
            # The first protocol checkpoint includes this admitted current task.
            first_checkpoint = not bool(context.protocol_state) or reopening

            def checkpoint(state):
                nonlocal first_checkpoint, active_unit_ref
                active_unit_ref = task.claim_lineage_id
                if first_checkpoint:
                    if state.get("pending_request") is not None:
                        raise ModelCallPersistenceFailure("record admission must precede request")
                    protocol_checkpoint(task, state, durable=False)
                    first_checkpoint = False
                    if model_call_hook is not None:
                        persist_call_boundary(work_changes=current_work_changes())
                    elif work_hook is not None:
                        work_hook(current_work_changes())
                else:
                    protocol_checkpoint(task, state)

            try:
                call = RecognitionCall(self.adapter, task, context, None, card)
                try:
                    checked_at = time.monotonic()
                    while not call.future.done():
                        call.drain(
                            lambda stage, ordinal: reserve_model_call(task, stage, ordinal),
                            checkpoint,
                        )
                        drain_ranking_during_model()
                        if time.monotonic() - checked_at >= 0.5:
                            if self.progress_hook is not None:
                                self.progress_hook("model_wait")
                            checked_at = time.monotonic()
                        call.cancelled.wait(0.01)
                    outcome = call.future.result()
                finally:
                    call.close()
            except (ModelCallPersistenceFailure, ModelCancelled, ExecutionLost, ModelWaitFailure):
                raise
            except ModelCallPauseRequested:
                diagnostics.append("execution_pause_requested")
                return True
            except Exception as exc:
                diagnostics.append(f"adapter_failure:{type(exc).__name__}")
                outcome = TaskOutcome(
                    semantic_outcome="not_checked", complete=False,
                    reason_code=getattr(exc, "reason_code", None) or str(exc) or type(exc).__name__,
                    reason="当前记录的实体和属性识别未完成。",
                    model_calls=reserved_calls.get(task.claim_lineage_id, 0) - prior_calls,
                )
            if protocols.get(task.claim_lineage_id, {}).get("pending_request") is not None:
                diagnostics.append("model_request_outcome_unknown")
                return True
            staged_protocols, staged_results = {}, {}
            state = json_value(task_protocol(task))
            if not state.get("outcome_ref"):
                # Technical failures have an exact owned outcome too; publishing
                # it and the incomplete work row is one graph transaction.
                value = outcome.model_dump(mode="json")
                reference = protocol_result_ref(task.claim_lineage_id, "outcome", value)
                staged_results[reference] = {
                    "lineage_id": task.claim_lineage_id, "field": "outcome", "value": value,
                }
                # Stage-local inputs and response refs must move together. Paid
                # responses and tool results remain in the independent ledger.
                state.update(
                    stage="finalize", outcome_ref=reference,
                    active_instructions="", stage_input_items=[],
                    turn_refs=[], completed_tool_results=[],
                )
                staged_protocols[task.claim_lineage_id] = state
                protocol_checkpoint(
                    task, {**state, "result_changes": staged_results}, durable=False,
                )
            finalized = outcome.model_copy(deep=True)
            apply_outcome(task, outcome, set())
            model_calls = call_totals[1]
            active_unit_ref = None
            discovery_turn = False
            if batch_hook is not None:
                batch_hook(ExecutionBatch(
                    batch_id=stable_id("record-discovery-batch", [
                        run_fingerprint, task.task_id,
                        task_protocol(task).get("outcome_ref"), finalized,
                    ]), task=task, outcome=finalized,
                    protocol_state_changes=staged_protocols, protocol_result_changes=staged_results,
                    task_outcomes=[], frontier={}, recall_ledger={}, dependency_index={},
                    graph=snapshot_graph(), diagnostics=list(dict.fromkeys(diagnostics)),
                    evidence_repair_summary=repair_summary(),
                    work_changes=current_work_changes(),
                ))
                protocols.changed.pop(task.claim_lineage_id, None)
            elif model_call_hook is not None:
                persist_call_boundary(staged_results, work_changes=current_work_changes())
            elif work_hook is not None:
                work_hook(current_work_changes())
            return self.progress_hook is not None and not self.progress_hook("after_model")

        def select_work_unit(excluded_slots):
            nonlocal active_unit_ref
            if active_unit_ref is not None:
                unit = RecognitionWorkUnit.model_validate(
                    json_value(protocols[active_unit_ref])["work_unit"],
                )
                return unit, prepare_batch_context(unit)
            candidates = ([expert_pending[0]] if expert_pending else
                          scheduler.peek_work_unit_candidates(
                              excluded_slots=excluded_slots, limit=self.batch_policy.max_members,
                          ))
            if self.record_pipeline and not expert_pending:
                candidates = [task for task in candidates if task.predicate_kind != "relationship"
                              or relation_pages(task)]
            if not candidates:
                return None, None
            inputs = {}
            for task in candidates:
                inputs[task.task_id] = MemberContext(
                    task_id=task.task_id, context=prepare_member_context(task),
                    card=compile_schema_card(
                        menus[task.subject.entity_id], predicate_iri=task.predicate_iri,
                        profile=self.adapter.profile, scope=task.scope,
                    ),
                )

            def fits(members):
                selected = {m.task_id for m in members}
                unit = RecognitionWorkUnit.create(
                    run_fingerprint=run_fingerprint, policy=self.batch_policy,
                    members=[task for task in candidates if task.task_id in selected],
                )
                context = prepare_batch_context(unit, inputs)
                return self.adapter.work_unit_fits(
                    unit, context, menus[unit.members[0].subject.entity_id],
                )

            selected = pack_work_unit(
                candidates, inputs, policy=self.batch_policy, measure_request=fits,
                readiness=lambda task: subject_is_active(task.subject, task.scope),
            )
            # A seed that exceeds capacity must become an explicit incomplete
            # member; leaving it in the queue would stall every later opportunity.
            selected = selected or [candidates[0].task_id]
            if expert_pending:
                unit = RecognitionWorkUnit.create(
                    run_fingerprint=run_fingerprint, policy=self.batch_policy,
                    members=[expert_pending.pop(0)],
                )
            else:
                unit = scheduler.consume_work_unit(
                    selected, run_fingerprint=run_fingerprint, policy=self.batch_policy,
                    excluded_slots=excluded_slots,
                )
            for task in unit.members:
                member_owners[task.task_id] = unit.work_unit_id
            context = prepare_batch_context(unit, inputs)
            if not context.protocol_state:
                initial = self.adapter.initialize_work_unit(
                    unit, context, menus[unit.members[0].subject.entity_id],
                )
                for task in unit.members:
                    initial["member_states"][task.task_id]["tool_calls_used"] = (
                        lineage_tool_calls.get(task.claim_lineage_id, 0)
                    )
                batch_protocol_checkpoint(unit, initial, durable=False)
                context.protocol_state = deepcopy(initial)
            active_unit_ref = unit.work_unit_id
            if model_call_hook is not None:
                persist_call_boundary(
                    work_changes=current_work_changes() if self.current_state else None,
                )
            elif self.current_state and work_hook is not None:
                work_hook(current_work_changes())
            return unit, context

        def execute_work_unit(unit, context, excluded_slots):
            nonlocal active_unit_ref, review_paused
            errors = {}
            try:
                call = RecognitionCall(
                    self.adapter, unit, context, None,
                    menus[unit.members[0].subject.entity_id], work_unit=True,
                )
                try:
                    checked_at = time.monotonic()
                    while not call.future.done():
                        call.drain(
                            lambda stage, ordinal: reserve_batch_model_call(unit, stage, ordinal),
                            lambda state: batch_protocol_checkpoint(unit, state),
                        )
                        drain_ranking_during_model()
                        if time.monotonic() - checked_at >= 0.5:
                            if self.progress_hook is not None:
                                self.progress_hook("model_wait")
                            checked_at = time.monotonic()
                        call.cancelled.wait(0.01)
                    reviewed = call.future.result()
                    errors = reviewed.member_errors
                finally:
                    call.close()
            except (ModelCallPersistenceFailure, ModelCancelled, ExecutionLost, ModelWaitFailure):
                raise
            except ModelCallPauseRequested:
                diagnostics.append("execution_pause_requested")
                return True
            except Exception as exc:
                diagnostics.append(f"adapter_failure:{type(exc).__name__}")
                reason = getattr(exc, "reason_code", None) or type(exc).__name__
                errors = {task.task_id: reason for task in unit.members}
            if protocols[unit.work_unit_id].get("pending_request") is not None:
                # A worker may raise or return member errors. Neither confirms
                # a pending request or authorizes publication of member outcomes.
                diagnostics.append("model_request_outcome_unknown")
                return True
            if any(not subject_is_active(t.subject, t.scope) for t in unit.members):
                raise ExecutionLost("recognition dependency changed")
            # Workers only publish stage artifacts. Finalization sees the current
            # canonical graph and stages outcomes for one atomic graph boundary.
            context = prepare_batch_context(
                unit, {member.task_id: member for member in context.members},
            )
            staged_protocols, staged_results, outcomes, accepted_outcomes = {}, {}, {}, {}

            def stage_checkpoint(state):
                saved = deepcopy(state)
                changes = saved.pop("result_changes", {})
                batch_protocol_checkpoint(unit, {**saved, "result_changes": changes}, durable=False)
                staged_protocols[unit.work_unit_id] = saved
                staged_results.update(changes)
                context.protocol_results.update(deepcopy(changes))

            context.bind_protocol_hook(stage_checkpoint)

            def stage_member_outcome(task, outcome, *, close_recovery=False):
                saved = deepcopy(context.protocol_state)
                version = member_result_version(saved, task.task_id)
                value = outcome.model_dump(mode="json")
                ref = protocol_result_ref(
                    unit.work_unit_id, "outcome", value,
                    member_task_id=task.task_id, result_version=version,
                )
                saved["outcome_refs"][task.task_id] = ref
                if close_recovery and saved["member_states"][task.task_id]["recovery_used"]:
                    saved["member_states"][task.task_id]["recovery_kind"] = "none"
                context.save_protocol(saved, result_changes={ref: {
                    "lineage_id": unit.work_unit_id, "member_task_id": task.task_id,
                    "result_version": version, "field": "outcome", "value": value,
                }})

            for task in unit.members:
                protocol = context.protocol_state
                existing = protocol["outcome_refs"].get(task.task_id)
                version = member_result_version(protocol, task.task_id)
                applied = applied_model_results.get(task.claim_lineage_id, {})
                existing_is_current = (
                    existing is not None
                    and version == context.protocol_results[existing]["result_version"]
                )
                if (existing_is_current
                        and applied.get("work_unit_id") == unit.work_unit_id
                        and applied.get("outcome_ref") == existing
                        and applied.get("result_version") == context.protocol_results[
                            existing]["result_version"]):
                    if task.task_id in errors:
                        # A requested recovery can become impossible after the member has
                        # exhausted its model-call allowance.  No paid request participated,
                        # so keep the last owned outcome and only close the recovery state.
                        saved = deepcopy(protocol)
                        state = saved["member_states"][task.task_id]
                        if state["recovery_used"] and state["recovery_kind"] != "none":
                            state["recovery_kind"] = "none"
                            context.save_protocol(saved)
                    continue
                pending_review = (
                    self.evidence_review and task.task_id in protocol["discovery_refs"]
                    and task.task_id not in protocol["verification_refs"]
                )
                if pending_review:
                    member = next(item for item in context.members if item.task_id == task.task_id)
                    member.context.tool_inputs["review_failure_reason"] = errors.get(
                        task.task_id, "review_pending",
                    )
                if (pending_review or (
                    (task.task_id in protocol["verification_refs"]
                     or (self.candidate_graph and task.predicate_kind == "relationship"
                         and task.task_id in protocol["discovery_refs"]))
                    and task.task_id not in errors
                )):
                    resolutions = [r for r in reference_resolutions.values()
                                   if dependency_index.is_valid_references([
                                       VersionedRef.model_validate(ref)
                                       for ref in r["dependency_refs"]
                                   ])]
                    outcome = self.adapter.finalize_reviewed_member(
                        task, context,
                        current_entities={(n.entity_id, n.revision): n for n in nodes.values()},
                        current_resolutions=resolutions,
                    )
                elif existing is not None and task.task_id not in errors:
                    outcome = TaskOutcome.model_validate(
                        context.protocol_results[existing]["value"],
                    )
                else:
                    reason = errors.get(task.task_id, "member_answer_missing")
                    outcome = TaskOutcome(
                        semantic_outcome="not_checked", complete=False,
                        reason_code=reason, reason="该成员尚未完成独立原文核验。",
                    )
                    stage_member_outcome(task, outcome, close_recovery=True)
                ref = context.protocol_state["outcome_refs"][task.task_id]
                if context.protocol_results[ref]["result_version"] != member_result_version(
                    context.protocol_state, task.task_id,
                ):
                    # A paid reproposal may produce identical invalid content.
                    # Record its actual attempt while retaining the same facts;
                    # an old applied marker cannot erase this new observation.
                    stage_member_outcome(task, outcome, close_recovery=True)
                ref = context.protocol_state["outcome_refs"][task.task_id]
                version = context.protocol_results[ref]["result_version"]
                outcomes[task.task_id] = outcome.model_copy(deep=True)
                apply_outcome(task, outcome, excluded_slots, batch_member=True,
                              outcome_ref=ref, result_version=version)
                accepted_outcomes[task.task_id] = outcome
                if task.claim_lineage_id in expert_lineages:
                    observe_expert_task(task, outcome)
                if search_index is not None and task_key(task) in searches:
                    key = task_key(task)
                    supported = sum(
                        item.decision_status == "supported" and item.polarity == "affirmed"
                        and not item.conditions and not item.applicability
                        and effective_proof_gate(item)
                        and dependency_index.is_valid(versioned_key(
                            item.candidate_id, item.revision,
                        ))
                        for item in [*outcome.edges, *outcome.properties,
                                     *outcome.relationship_groups]
                    )
                    searches[key].observe(
                        task.record_id, semantic_outcome=outcome.semantic_outcome,
                        complete=outcome.complete, reason_code=outcome.reason_code,
                        supported_count=supported,
                        attempt_id=stable_id("member-result", [unit.work_unit_id, task.task_id,
                                                               version, ref]),
                    )
                    if layered_recognition and task.claim_lineage_id not in expert_lineages:
                        admit_search_page(key)
            # Child work is admitted only after every member uses the canonical
            # entities established by the preceding member's accepted outcome.
            for task in unit.members:
                if task.task_id in accepted_outcomes:
                    expand_tool_relations(task, accepted_outcomes[task.task_id])
            review_paused = any(review_waiting(task) for task in unit.members)
            if not review_paused and not self.adapter.work_unit_pending_recovery(context):
                active_unit_ref = None
            if outcomes and batch_hook is not None:
                batch_hook(ExecutionBatch(
                    batch_id=stable_id("recognition-batch", [
                        run_fingerprint, unit.work_unit_id, context.protocol_state["outcome_refs"],
                    ]), work_unit=unit, member_outcomes=outcomes,
                    protocol_state_changes=staged_protocols, protocol_result_changes=staged_results,
                    task_outcomes=[] if self.current_state else list(task_outcomes),
                    frontier={} if self.current_state else current_frontier(),
                    recall_ledger={} if self.current_state else current_recall_ledger(),
                    dependency_index={} if self.current_state else dependency_index.snapshot(),
                    graph=snapshot_graph(), diagnostics=list(dict.fromkeys(diagnostics)),
                    ranking_state={} if self.current_state else current_ranking_state(),
                    model_call_state={} if self.current_state else current_model_call_state(),
                    evidence_repair_summary=repair_summary(),
                    work_changes=current_work_changes() if self.current_state else None,
                ))
                if self.current_state:
                    # The batch transaction persisted these exact staged rows.
                    protocols.changed.pop(unit.work_unit_id, None)
                if protocol_record_loader is not None:
                    for reference in staged_results:
                        local_protocol_results.pop(reference, None)
            elif staged_protocols and model_call_hook is not None:
                persist_call_boundary(staged_results)
            if self.progress_hook is not None and not self.progress_hook("after_model"):
                diagnostics.append("execution_pause_requested")
                return True
            return review_paused

        def publish_review_boundary():
            if batch_hook is None or not review_replay.events:
                return
            original = review_replay.events[-1]["payload"]
            source = original.get("original_task") or original["target"]["original_task"]
            task = (RecordDiscoveryTask if source.get("kind") == "record_discovery"
                    else RecognitionTask).model_validate(source)
            batch_hook(ExecutionBatch(
                batch_id=stable_id("expert-review-boundary", [
                    run_fingerprint, completed_tasks, review_replay.snapshot(),
                ]), task=task,
                outcome=TaskOutcome(semantic_outcome="not_checked", complete=False,
                                    reason_code="expert_review_boundary",
                                    reason="人工审核操作边界；不增加原文检查或模型调用"),
                task_outcomes=[] if self.current_state else list(task_outcomes),
                frontier={} if self.current_state else current_frontier(),
                recall_ledger={} if self.current_state else current_recall_ledger(),
                dependency_index={} if self.current_state else dependency_index.snapshot(),
                graph=snapshot_graph(), diagnostics=list(dict.fromkeys(diagnostics)),
                ranking_state={} if self.current_state else current_ranking_state(),
                model_call_state={} if self.current_state else current_model_call_state(),
                evidence_repair_summary=repair_summary(),
                work_changes=current_work_changes() if self.current_state else None,
            ))

        if self.current_state:
            for lineage in set(lineage_calls) | set(reserved_calls) | set(protocols):
                update_call_totals(lineage)

        if self.current_state and direct is None:
            unapplied_epochs = dict(ranking.epochs_by_id)

        if self.current_state and work_hook is not None and direct is None:
            work_hook(current_work_changes())

        try:
            if self.adapter is None and not (resume_outcomes or property_reviews
                                              or review_replay.events):
                diagnostics.append("recognition_model_not_configured")
            else:
                while True:
                    apply_review_events()
                    if resume_cursor == len(resume_outcomes):
                        validate_resume_boundary()
                        if not reviews_admitted:
                            reviews_admitted = True
                            before = len(review_replay.events)
                            review_replay.admit(property_reviews or [], property_repairs or [],
                                                after_outcomes=completed_tasks)
                            apply_review_events()
                            if len(review_replay.events) != before:
                                publish_review_boundary()
                        if repair_only and not expert_pending and active_unit_ref is None:
                            break
                        if self.adapter is None and not expert_pending:
                            diagnostics.append("recognition_model_not_configured")
                            break
                    record_active = (active_unit_ref is not None
                                     and protocols[active_unit_ref]["version"] == RECORD_PROTOCOL)
                    if self.record_pipeline and not expert_pending and not repair_only and (
                        record_active or active_unit_ref is None and (
                            discovery_turn or not scheduler.pending
                        )
                    ):
                        if self.progress_hook is not None and not self.progress_hook("before_task"):
                            diagnostics.append("execution_pause_requested")
                            break
                        try:
                            record_task = select_record_task()
                        except RankingPaused as exc:
                            ranking_paused = True
                            diagnostics.append(f"ranking_paused:{exc}")
                            if ranking_hook is not None:
                                persist_ranking_boundary()
                            break
                        if record_task is not None:
                            if execute_record_task(record_task):
                                break
                            continue
                    try:
                        if not expert_pending:
                            prepare_ranking()
                    except RankingPaused as exc:
                        ranking_paused = True
                        diagnostics.append(f"ranking_paused:{exc}")
                        if ranking_hook is not None:
                            persist_ranking_boundary()
                        break
                    if (
                        restored_paused_epochs and pending_ranking is None
                        and resume_cursor == len(resume_outcomes)
                        and not expert_pending and not repair_only
                    ):
                        # A completed recovery attempt may reveal another saved
                        # pause. Resolve the entire prefix before fresh work.
                        continue
                    excluded_slots = (
                        {tuple(key) for key in resume_outcomes[resume_cursor].get(
                            "ranking_excluded_slots", []
                        )}
                        if resume_cursor < len(resume_outcomes) else blocked_ranking_slots()
                    )
                    if (pending_ranking is not None and not expert_pending
                        and active_unit_ref is None and (
                            recovering_ranking_pause or scheduler.peek_task(excluded_slots) is None
                        )
                    ):
                        if self.progress_hook is not None and not self.progress_hook(
                            "ranking_wait"
                        ):
                            ranking_paused = True
                            break
                        pending_ranking.wait()
                        continue
                    # Record stopped slots before compacting the queues. Legacy
                    # summaries retain task IDs; v2 retains slot identities/counts.
                    task_budget_stopped_slots.update(budget_blocked_slots())
                    if self.current_state and self.progress_hook is not None and not (
                        self.progress_hook("before_task")
                    ):
                        diagnostics.append("execution_pause_requested")
                        break
                    if defer_unready_relation(excluded_slots):
                        discovery_turn = True
                        continue
                    if self.batch_policy and resume_cursor == len(resume_outcomes):
                        unit, batch_context = select_work_unit(excluded_slots)
                        if unit is not None:
                            if execute_work_unit(unit, batch_context, excluded_slots):
                                break
                            if self.record_pipeline:
                                discovery_turn = True
                            continue
                    task = (expert_pending.pop(0) if expert_pending else
                            scheduler.next_task(excluded_slots=excluded_slots))
                    if task is None:
                        if resume_cursor != len(resume_outcomes):
                            raise ValueError(
                                "checkpoint contains tasks outside the rebuilt frontier"
                            )
                        # A consumed empty gate/epoch can leave another slot ready
                        # for H2 without creating recognition tasks. Let the owner
                        # dispatch that work before deciding the run is saturated.
                        if self.adaptive_policy and scheduler.dispatched < scheduler.max_tasks:
                            if any(search.needs_semantic and slot_is_active(key)
                                   and slot_subject_is_active(key)
                                   for key, search in searches.items()):
                                continue
                        if scheduler.dispatched < scheduler.max_tasks and advance_layer():
                            continue
                        if schedule_attribute_calibration():
                            discovery_turn = True
                            continue
                        break
                    replaying = resume_cursor < len(resume_outcomes)
                    context = None
                    stop_after_batch = False
                    if replaying:
                        if self.progress_hook is not None and not self.progress_hook(
                            "checkpoint_replay"
                        ):
                            raise ExecutionLost("execution interrupted during checkpoint replay")
                        saved = resume_outcomes[resume_cursor]
                        saved_task = RecognitionTask.model_validate(saved.get("task"))
                        if saved_task != task:
                            raise ValueError("checkpoint task sequence mismatch")
                        outcome = TaskOutcome.model_validate(saved.get("outcome"))
                        resume_cursor += 1
                    else:
                        if self.progress_hook is not None and not self.progress_hook(
                            "before_model"
                        ):
                            diagnostics.append("execution_pause_requested")
                            if self.current_state:
                                scheduler._retries.appendleft(task)
                                scheduler.dispatched -= 1
                            break
                        key = task_key(task)
                        predicate = predicates[key]
                        context = prepare_member_context(task)
                        reservations_before = len(reservations)
                        try:
                            if self.adapter is None:
                                outcome = TaskOutcome(
                                    semantic_outcome="not_checked", complete=False,
                                    reason_code="recognition_model_not_configured",
                                    reason="识别模型不可用，局部修复未完成。",
                                )
                            elif not subject_is_active(task.subject, task.scope):
                                outcome = TaskOutcome(
                                    semantic_outcome="not_checked",
                                    complete=False,
                                    reason_code="subject_dependency_invalidated",
                                    reason="当前主体的肯定可达证明已失效，后续任务保持未完成。",
                                )
                            elif not remaining_calls(task) and not (
                                has_protocol and (
                                    protocols.get(task.claim_lineage_id, {}).get("outcome")
                                    or protocols.get(task.claim_lineage_id, {}).get("verification")
                                    or self.tool_protocol and protocols.get(task.claim_lineage_id)
                                )
                            ):
                                outcome = TaskOutcome(
                                    semantic_outcome="not_checked",
                                    complete=False,
                                    reason_code="record_model_call_budget_exhausted",
                                    reason="该原文语义任务的模型调用预算耗尽，重验仍未完成。",
                                )
                            else:
                                call = RecognitionCall(
                                    self.adapter, task, context, predicate,
                                    menus[task.subject.entity_id],
                                )
                                try:
                                    checked_at = time.monotonic()
                                    while not call.future.done():
                                        call.drain(
                                            lambda stage, ordinal: reserve_model_call(
                                                task, stage, ordinal,
                                            ),
                                            lambda state: protocol_checkpoint(task, state),
                                        )
                                        drain_ranking_during_model()
                                        if time.monotonic() - checked_at >= 0.5:
                                            # Pause is soft: the next before_model
                                            # or completed result is its boundary.
                                            if self.progress_hook is not None:
                                                self.progress_hook("model_wait")
                                            checked_at = time.monotonic()
                                        call.cancelled.wait(0.01)
                                    outcome = call.future.result()
                                finally:
                                    call.close()
                                if not subject_is_active(task.subject, task.scope):
                                    raise ExecutionLost("recognition dependency changed")
                        except (
                            ModelCallPersistenceFailure, ModelCancelled, ExecutionLost,
                            ModelWaitFailure,
                        ):
                            raise
                        except ExpertRepairBudgetExhausted:
                            outcome = TaskOutcome(
                                semantic_outcome="not_checked", complete=False,
                                reason_code="expert_repair_model_call_budget_exhausted",
                                reason="局部修复的模型调用预算已耗尽，保留未完成结果。",
                                model_calls=len(reservations) - reservations_before,
                            )
                        except ModelCallPauseRequested:
                            outcome = TaskOutcome(
                                semantic_outcome="not_checked",
                                complete=False,
                                reason_code="execution_pause_requested",
                                reason="execution paused between independent model stages",
                                model_calls=len(reservations) - reservations_before,
                            )
                            diagnostics.append("execution_pause_requested")
                            stop_after_batch = True
                        except Exception as exc:  # isolate one logical record
                            diagnostics.append(f"adapter_failure:{type(exc).__name__}")
                            outcome = TaskOutcome(
                                semantic_outcome="not_checked",
                                complete=False,
                                reason_code=getattr(exc, "reason_code", type(exc).__name__),
                                reason=f"原文验证技术失败：{type(exc).__name__}",
                                model_calls=getattr(exc, "model_calls", 0),
                            )
                        if self.progress_hook is not None and not self.progress_hook("after_model"):
                            diagnostics.append("execution_pause_requested")
                            # A completed verification is a durable safe boundary.
                            # Pausing must not discard it and repeat paid requests.
                            stop_after_batch = True
                    key = task_key(task)
                    is_expert_task = task.claim_lineage_id in expert_lineages
                    outcome_started = time.perf_counter()
                    apply_outcome(task, outcome, excluded_slots)
                    if not outcome.complete and review_waiting(task):
                        review_paused = True
                        stop_after_batch = True
                    if self.evidence_repair:
                        if replaying:
                            evidence_work.restore(thaw_json(saved["evidence_work"]))
                            recheck = (RecognitionTask.model_validate(saved["evidence_recheck"])
                                       if saved.get("evidence_recheck") else None)
                        else:
                            recheck = None if is_expert_task else evidence_work.observe(
                                task, outcome, context, predicates[key],
                                protocols.get(task.claim_lineage_id, {}),
                            ) if context is not None else None
                            if recheck is None and not is_expert_task:
                                recheck = evidence_work.next_deferred(subject_is_active)
                        if recheck is not None:
                            scheduler.enqueue(recheck, root_branch=recheck.subject.is_document_root)
                        if not self.current_state:
                            task_outcomes[-1]["evidence_work"] = evidence_work.snapshot()
                        task_outcomes[-1]["evidence_recheck"] = (
                            recheck.model_dump(mode="json") if recheck else None
                        )
                    if (search_index is not None
                            and outcome.reason_code != "execution_pause_requested"
                            and not (self.evidence_repair and replaying)):
                        search_event("outcome_application", {
                            "task_id": task.task_id,
                            "elapsed_seconds": time.perf_counter() - outcome_started,
                        })
                        search_key = task_key(task)
                        supported_count = sum(
                            candidate.decision_status == "supported"
                            and candidate.polarity == "affirmed"
                            and not candidate.conditions and not candidate.applicability
                            and effective_proof_gate(candidate)
                            and dependency_index.is_valid(
                                versioned_key(candidate.candidate_id, candidate.revision)
                            )
                            for candidate in [*outcome.edges, *outcome.properties]
                        )
                        searches[search_key].observe(
                            task.record_id, semantic_outcome=outcome.semantic_outcome,
                            complete=outcome.complete, reason_code=outcome.reason_code,
                            supported_count=supported_count,
                            attempt_id=task.task_id,
                        )
                        search_event("heuristic_feedback", {
                            "task_id": task.task_id, "record_id": task.record_id,
                            "predicate_iri": task.predicate_iri,
                            "semantic_outcome": outcome.semantic_outcome,
                            "reason_code": outcome.reason_code,
                            "state": ({"stage": searches[search_key].stage,
                                       "status": searches[search_key].status} if self.current_state
                                      else searches[search_key].snapshot()),
                        })
                    if search_index is not None and (layered_recognition or is_expert_task):
                        search_key = task_key(task)
                        search = searches[search_key]
                        delta_key = ("layered_search_delta" if layered_recognition
                                     else "expert_search_delta")
                        if replaying and self.evidence_repair:
                            delta = thaw_json(saved[delta_key])
                            search.restore(slot_search_replay.restore(search.plan.plan_id, delta))
                        elif not self.current_state:
                            delta = slot_search_replay.capture(
                                search.plan.plan_id, search.snapshot(),
                            )
                        if not self.current_state:
                            task_outcomes[-1][delta_key] = delta
                        if not is_expert_task:
                            admit_search_page(search_key)
                    if is_expert_task:
                        observe_expert_task(task, outcome)
                        if (layered_recognition and key in searches
                                and set(slot_completion.conflict_records(predicates[key]))
                                <= searches[key].admitted):
                            # Reuse only already admitted conflict obligations;
                            # a local repair never admits wider source work.
                            prepare_slot_completion(key)
                        if not replaying and outcome.reason_code == "execution_pause_requested":
                            stop_after_batch = True
                    if self.incremental_performance:
                        task_outcomes[-1] = freeze_json(task_outcomes[-1])
                    if not replaying and batch_hook is not None:
                        graph = snapshot_graph()
                        batch_hook(
                            ExecutionBatch(
                                batch_id=stable_id(
                                    "recognition-batch",
                                    [
                                        run_fingerprint,
                                        completed_tasks,
                                        task.task_id,
                                        outcome.model_dump(mode="json"),
                                    ],
                                ),
                                task=task,
                                outcome=outcome,
                                task_outcomes=[] if self.current_state else list(task_outcomes),
                                frontier={} if self.current_state else current_frontier(),
                                recall_ledger={} if self.current_state else current_recall_ledger(),
                                dependency_index=({} if self.current_state
                                                  else dependency_index.snapshot()),
                                graph=graph,
                                diagnostics=list(dict.fromkeys(diagnostics)),
                                ranking_state={} if self.current_state else current_ranking_state(),
                                model_call_state=({} if self.current_state
                                                  else current_model_call_state()),
                                evidence_repair_summary=repair_summary(),
                                work_changes=current_work_changes() if self.current_state else None,
                            )
                        )
                    if stop_after_batch:
                        break
        finally:
            if pending_ranking is not None:
                pending_ranking.close()
                pending_service_state = pending_ranking.service.durability_state()
                if ranking_paused and ranking_hook is not None and sys.exc_info()[0] is None:
                    # Retain actual cancellation/failure observations after
                    # teardown; a revoked fence still rejects this last write.
                    persist_ranking_boundary()

        validate_resume_boundary()
        if self.current_state and work_hook is not None:
            work_hook(current_work_changes())
        graph = snapshot_graph(terminal=True)
        if search_index is not None:
            search_event("heuristic_complete", {
                "policy": asdict(self.heuristic_policy),
                "slots": [{"plan_id": search.plan.plan_id, "stage": search.stage,
                           "status": search.status} if self.current_state else search.snapshot()
                          for search in searches.values()],
                "elapsed_seconds": time.perf_counter() - search_started,
                "resume_supported": self.evidence_repair,
            })
        events.append(("graph_projected", graph.model_dump(mode="json")))
        return ExecutionResult(
            ontology_snapshot=self.ontology,
            root_menu=root_menu,
            graph=graph,
            retrieval_plans=[] if self.current_state else [
                plan.model_dump(mode="json") for plan in plans.values()],
            events=events,
            diagnostics=list(dict.fromkeys(diagnostics)),
            ranking_state={} if self.current_state else current_ranking_state(),
            model_call_state={} if self.current_state else current_model_call_state(),
            evidence_repair_summary=repair_summary(),
        )
