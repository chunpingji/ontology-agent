"""Shared online/offline ontology-guided execution core.

The core owns scheduling, coverage and projection.  A model adapter may propose
record outcomes, but it cannot mutate registries or bypass exact endpoint and
predicate checks performed here.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal, Protocol

from pydantic import Field

from app.schemas.evidence import EvidenceModel
from app.services.extraction.document_ir import DocumentIR
from app.services.extraction.evidence_identity import evidence_hash, stable_id
from app.services.extraction.ontology_guided.context import TaskContext, assemble_context
from app.services.extraction.ontology_guided.contracts import (
    CoverageSummary,
    DocumentContext,
    EdgeSpec,
    GraphEdge,
    GraphNode,
    GraphProperty,
    GraphSnapshot,
    LocalMenu,
    MetadataSnapshot,
    OntologySnapshot,
    RunProgress,
    SlotSpec,
    SubjectRef,
    VerificationTarget,
    VersionedRef,
)
from app.services.extraction.ontology_guided.dependencies import DependencyIndex
from app.services.extraction.ontology_guided.ontology_plan import compile_local_menu
from app.services.extraction.ontology_guided.projection import (
    effective_proof_gate,
    project_graph,
)
from app.services.extraction.ontology_guided.records import RecordIndex
from app.services.extraction.ontology_guided.retrieval import mark_record, plan_slot
from app.services.extraction.ontology_guided.scheduler import FrontierScheduler, RecognitionTask


class TaskOutcome(EvidenceModel):
    semantic_outcome: Literal["supported", "unsupported", "undetermined", "not_checked"]
    complete: bool = True
    reason_code: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    model_calls: int = Field(default=0, ge=0)
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)
    properties: list[GraphProperty] = Field(default_factory=list)
    proof_payloads: list[dict] = Field(default_factory=list)
    decision_payloads: list[dict] = Field(default_factory=list)


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


class ExecutionBatch(EvidenceModel):
    """Serializable safe-boundary state emitted after one logical task attempt."""

    batch_id: str = Field(min_length=1)
    task: RecognitionTask
    outcome: TaskOutcome
    task_outcomes: list[dict]
    frontier: dict
    recall_ledger: dict
    dependency_index: dict
    graph: GraphSnapshot
    diagnostics: list[str] = Field(default_factory=list)


class OntologyGuidedExecutor:
    version = "ontology-guided-executor-v2"

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
    ):
        self.ontology = ontology
        self.engine = engine
        self.adapter = adapter
        self.max_hops = max_hops
        self.max_tasks = max_tasks
        self.phase1_section_limit = phase1_section_limit
        self.progress_hook = progress_hook
        self.predicate_filter = predicate_filter

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
    ) -> ExecutionResult:
        index = RecordIndex(ir)
        root = self.root_node(
            recognition_run_id=recognition_run_id,
            document_hash=ir.document_hash,
            root_class_iri=root_class_iri,
            root_class_label=root_class_label,
            filename=filename,
        )
        root_subject = SubjectRef(
            entity_id=root.entity_id,
            revision=root.revision,
            class_iri=root.class_iri,
            is_document_root=True,
        )
        root_menu = compile_local_menu(self.ontology, root_subject, engine=self.engine)
        scheduler = FrontierScheduler(max_hops=self.max_hops, max_tasks=self.max_tasks)
        dependency_index = DependencyIndex()
        plans = {}
        predicates = {}
        menus = {root.entity_id: root_menu}
        events: list[tuple[str, dict]] = []
        diagnostics = list(root_menu.diagnostics)

        def add_subject(subject: SubjectRef, menu: LocalMenu, *, hop: int) -> None:
            for predicate in [*menu.relationships, *menu.properties]:
                if self.predicate_filter is not None and not self.predicate_filter(
                    subject, predicate, hop
                ):
                    continue
                key = (subject.entity_id, subject.revision, predicate.iri)
                if key in plans:
                    continue
                plan = plan_slot(
                    subject,
                    predicate,
                    index,
                    metadata,
                    ontology_hash=self.ontology.ontology_hash,
                    phase1_section_limit=self.phase1_section_limit,
                )
                plans[key] = plan
                predicates[key] = predicate
                events.append(("retrieval_plan_created", plan.model_dump(mode="json")))
                for record in plan.records:
                    scheduler.enqueue(
                        RecognitionTask.create(
                            subject=subject,
                            predicate_iri=predicate.iri,
                            predicate_kind=predicate.kind,
                            record_id=record.record_id,
                            phase=record.phase,
                            hop=hop,
                            dependency_hash=evidence_hash(
                                [plan.plan_id, metadata.snapshot_id, menu.menu_id]
                            ),
                        ),
                        root_branch=subject.is_document_root,
                    )

        add_subject(root_subject, root_menu, hop=0)
        nodes = {root.entity_id: root}
        edges: dict[str, GraphEdge] = {}
        properties: dict[str, GraphProperty] = {}
        model_calls = 0
        task_outcomes: list[dict] = []
        resume_outcomes = list((resume_state or {}).get("task_outcomes") or [])
        resume_cursor = 0
        resume_validated = resume_state is None

        def snapshot_graph() -> GraphSnapshot:
            ledger_entries = [entry for plan in plans.values() for entry in plan.ledger.values()]
            records_planned = len(ledger_entries)
            examined = sum(entry.coverage_state == "examined" for entry in ledger_entries)
            incomplete = sum(
                entry.coverage_state == "attempted_incomplete" for entry in ledger_entries
            )
            unattempted = sum(entry.coverage_state == "unattempted" for entry in ledger_entries)
            semantic = [value for entry in ledger_entries for value in entry.semantic_outcomes]
            pending_frontiers = len(scheduler.unexplored_frontier)
            complete = not incomplete and not unattempted and not pending_frontiers
            progress = RunProgress(
                tasks_attempted=examined + incomplete,
                model_calls=model_calls,
                records_planned=records_planned,
                records_examined=examined,
                records_incomplete=incomplete,
                records_unattempted=unattempted,
                phase_counts={
                    "phase1": sum(
                        entry.phase == 1 and entry.coverage_state != "unattempted"
                        for entry in ledger_entries
                    ),
                    "phase2": sum(
                        entry.phase == 2 and entry.coverage_state != "unattempted"
                        for entry in ledger_entries
                    ),
                },
                supported=semantic.count("supported"),
                unsupported=semantic.count("unsupported"),
                undetermined=semantic.count("undetermined"),
                pending_frontiers=pending_frontiers,
                unresolved_claims=semantic.count("undetermined"),
                stop_reason=(
                    "queue_exhausted"
                    if complete
                    else "service_failure"
                    if self.adapter is None
                    else "attempted_incomplete"
                ),
                completion="in_scope_complete" if complete else "incomplete",
            )
            coverage = []
            for key, plan in plans.items():
                entries = list(plan.ledger.values())
                predicate = predicates[key]
                coverage.append(
                    CoverageSummary(
                        subject_ref=VersionedRef(id=key[0], revision=key[1]),
                        predicate_iri=predicate.iri,
                        predicate_label=predicate.label,
                        phase1=sum(entry.phase == 1 for entry in entries),
                        phase2=sum(entry.phase == 2 for entry in entries),
                        examined=sum(entry.coverage_state == "examined" for entry in entries),
                        incomplete=sum(
                            entry.coverage_state == "attempted_incomplete" for entry in entries
                        ),
                        unattempted=sum(entry.coverage_state == "unattempted" for entry in entries),
                        unresolved_claims=sum(
                            value == "undetermined"
                            for entry in entries
                            for value in entry.semantic_outcomes
                        ),
                    )
                )
            return project_graph(
                recognition_run_id=recognition_run_id,
                run_revision=run_revision,
                event_head=event_head + len(task_outcomes),
                metadata_snapshot_id=metadata.snapshot_id,
                root_ref=VersionedRef(id=root.entity_id, revision=root.revision),
                nodes=list(nodes.values()),
                edges=list(edges.values()),
                properties=list(properties.values()),
                coverage=coverage,
                progress=progress,
                dependency_index=dependency_index,
                projection="all",
                artifact_status="ready" if complete else "partial",
            )

        def validate_resume_boundary() -> None:
            nonlocal resume_validated
            if resume_validated:
                return
            expected_frontier = (resume_state or {}).get("frontier")
            expected_ledger = (resume_state or {}).get("recall_ledger")
            expected_dependencies = (resume_state or {}).get("dependency_index")
            actual_ledger = {plan.plan_id: plan.model_dump(mode="json") for plan in plans.values()}
            if (
                expected_frontier != scheduler.snapshot()
                or expected_ledger != actual_ledger
                or expected_dependencies != dependency_index.snapshot()
            ):
                raise ValueError(
                    "checkpoint scheduler, recall ledger, or dependency index mismatch"
                )
            resume_validated = True

        def versioned_key(identifier: str, revision: int) -> str:
            return f"{identifier}@{revision}"

        def register_dependencies(outcome: TaskOutcome) -> None:
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
            for candidate in [*outcome.edges, *outcome.properties]:
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
                dependency_index.add_proof(
                    versioned_key(candidate.candidate_id, candidate.revision),
                    list(dict.fromkeys(dependencies)),
                )

        def apply_outcome(task: RecognitionTask, outcome: TaskOutcome) -> None:
            nonlocal model_calls
            key = (task.subject.entity_id, task.subject.revision, task.predicate_iri)
            plan = plans[key]
            model_calls += outcome.model_calls
            plan = mark_record(
                plan,
                task.record_id,
                coverage_state="examined" if outcome.complete else "attempted_incomplete",
                execution_state="finished" if outcome.complete else "retryable_failure",
                semantic_outcomes=[outcome.semantic_outcome],
                task_id=task.task_id,
                reason_code=outcome.reason_code,
            )
            plans[key] = plan
            payload = {
                "task": task.model_dump(mode="json"),
                "outcome": outcome.model_dump(mode="json"),
            }
            task_outcomes.append(payload)
            events.append(("task_outcome", payload))
            register_dependencies(outcome)
            if not outcome.complete and task.retry_kind is None:
                retry = RecognitionTask.create(
                    subject=task.subject,
                    predicate_iri=task.predicate_iri,
                    predicate_kind=task.predicate_kind,
                    record_id=task.record_id,
                    phase=task.phase,
                    hop=task.hop,
                    dependency_hash=task.dependency_hash,
                    claim_lineage_id=task.claim_lineage_id,
                    retry_kind="technical_once",
                )
                if scheduler.enqueue(retry, root_branch=task.subject.is_document_root):
                    diagnostics.append("bounded_technical_retry_scheduled")
            for node in outcome.nodes:
                current = nodes.get(node.entity_id)
                if current and current.revision > node.revision:
                    continue
                nodes[node.entity_id] = node
            for edge in outcome.edges:
                if (
                    edge.subject_ref
                    != VersionedRef(id=task.subject.entity_id, revision=task.subject.revision)
                    or edge.predicate_iri != task.predicate_iri
                ):
                    diagnostics.append("adapter_edge_target_mismatch")
                    continue
                object_node = nodes.get(edge.object_ref.id)
                if object_node is None or object_node.revision != edge.object_ref.revision:
                    diagnostics.append("adapter_edge_object_revision_mismatch")
                    continue
                edges[edge.candidate_id] = edge
                if (
                    edge.decision_status == "supported"
                    and edge.polarity == "affirmed"
                    and effective_proof_gate(edge)
                ):
                    subject = SubjectRef(
                        entity_id=object_node.entity_id,
                        revision=object_node.revision,
                        class_iri=object_node.class_iri,
                    )
                    try:
                        menu = compile_local_menu(self.ontology, subject, engine=self.engine)
                    except ValueError:
                        diagnostics.append(
                            f"object_class_outside_frozen_snapshot:{object_node.class_iri}"
                        )
                    else:
                        menus[subject.entity_id] = menu
                        add_subject(subject, menu, hop=task.hop + 1)
            for item in outcome.properties:
                if (
                    item.subject_ref
                    != VersionedRef(id=task.subject.entity_id, revision=task.subject.revision)
                    or item.predicate_iri != task.predicate_iri
                ):
                    diagnostics.append("adapter_property_target_mismatch")
                    continue
                properties[item.candidate_id] = item

        if self.adapter is None:
            diagnostics.append("recognition_model_not_configured")
        else:
            while True:
                if resume_cursor == len(resume_outcomes):
                    validate_resume_boundary()
                task = scheduler.next_task()
                if task is None:
                    if resume_cursor != len(resume_outcomes):
                        raise ValueError("checkpoint contains tasks outside the rebuilt frontier")
                    break
                replaying = resume_cursor < len(resume_outcomes)
                stop_after_batch = False
                if replaying:
                    saved = resume_outcomes[resume_cursor]
                    saved_task = RecognitionTask.model_validate(saved.get("task"))
                    if saved_task != task:
                        raise ValueError("checkpoint task sequence mismatch")
                    outcome = TaskOutcome.model_validate(saved.get("outcome"))
                    resume_cursor += 1
                else:
                    if self.progress_hook is not None and not self.progress_hook("before_model"):
                        diagnostics.append("execution_pause_requested")
                        break
                    key = (
                        task.subject.entity_id,
                        task.subject.revision,
                        task.predicate_iri,
                    )
                    predicate = predicates[key]
                    record = index.by_id[task.record_id]
                    scope_hash = evidence_hash(
                        [
                            record.record_id,
                            [unit.evidence_id for unit in record.source_units],
                            task.subject.model_dump(mode="json"),
                            task.predicate_iri,
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
                    context = assemble_context(target, task.record_id, index)
                    try:
                        outcome = self.adapter.inspect(
                            task,
                            context,
                            predicate,
                            menus[task.subject.entity_id],
                        )
                    except Exception as exc:  # isolate one logical record
                        diagnostics.append(f"adapter_failure:{type(exc).__name__}")
                        outcome = TaskOutcome(
                            semantic_outcome="not_checked",
                            complete=False,
                            reason_code=type(exc).__name__,
                            reason=str(exc) or type(exc).__name__,
                        )
                    if self.progress_hook is not None and not self.progress_hook("after_model"):
                        diagnostics.append("execution_pause_requested")
                        outcome = TaskOutcome(
                            semantic_outcome="not_checked",
                            complete=False,
                            reason_code="execution_pause_requested",
                            reason="execution paused after the model returned",
                            model_calls=outcome.model_calls,
                        )
                        stop_after_batch = True
                apply_outcome(task, outcome)
                if not replaying and batch_hook is not None:
                    graph = snapshot_graph()
                    batch_hook(
                        ExecutionBatch(
                            batch_id=stable_id(
                                "recognition-batch",
                                [
                                    run_fingerprint,
                                    len(task_outcomes),
                                    task.task_id,
                                    outcome.model_dump(mode="json"),
                                ],
                            ),
                            task=task,
                            outcome=outcome,
                            task_outcomes=list(task_outcomes),
                            frontier=scheduler.snapshot(),
                            recall_ledger={
                                plan.plan_id: plan.model_dump(mode="json")
                                for plan in plans.values()
                            },
                            dependency_index=dependency_index.snapshot(),
                            graph=graph,
                            diagnostics=list(dict.fromkeys(diagnostics)),
                        )
                    )
                if stop_after_batch:
                    break

        validate_resume_boundary()
        graph = snapshot_graph()
        events.append(("graph_projected", graph.model_dump(mode="json")))
        return ExecutionResult(
            ontology_snapshot=self.ontology,
            root_menu=root_menu,
            graph=graph,
            retrieval_plans=[plan.model_dump(mode="json") for plan in plans.values()],
            events=events,
            diagnostics=list(dict.fromkeys(diagnostics)),
        )
