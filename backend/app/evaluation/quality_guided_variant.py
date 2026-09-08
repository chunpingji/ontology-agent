"""Thin offline evaluator over the production ontology-guided execution core.

This module contains no extraction algorithm of its own. The active benchmark
constructs this adapter with frozen ontology and metadata snapshots, and both
online and offline recognition execute :class:`OntologyGuidedExecutor`.
Historical quality-runner replay lives in ``legacy_quality_guided_variant``.
"""

from __future__ import annotations

from collections.abc import Callable
from time import perf_counter
from typing import Any, Literal

from pydantic import Field

from app.schemas.evidence import EvidenceModel
from app.services.extraction.document_ir import DocumentIR
from app.services.extraction.evidence_identity import evidence_hash
from app.services.extraction.ontology_guided.contracts import (
    CONTRACT_VERSION,
    EdgeSpec,
    GraphSnapshot,
    LocalMenu,
    MetadataSnapshot,
    OntologySnapshot,
    SlotSpec,
    SubjectRef,
)
from app.services.extraction.ontology_guided.executor import (
    OntologyGuidedExecutor,
    RecognitionAdapter,
    TaskOutcome,
)
from app.services.extraction.ontology_guided.ontology_plan import compile_local_menu

EVALUATOR_VERSION = "ontology-guided-evaluator-v1"
RUN_SCHEMA_VERSION = "ontology-guided-evaluation-run-v1"


class AdapterCall(EvidenceModel):
    ordinal: int = Field(ge=1)
    task_id: str = Field(min_length=1)
    record_id: str = Field(min_length=1)
    subject_id: str = Field(min_length=1)
    predicate_iri: str = Field(min_length=1)
    phase: Literal[1, 2]
    hop: int = Field(ge=0)
    context_hash: str = Field(min_length=64, max_length=64)
    elapsed_seconds: float = Field(ge=0)
    status: Literal["complete", "failed"]
    semantic_outcome: str | None = None
    reason_code: str | None = None
    error_code: str | None = None


class EvaluationEvent(EvidenceModel):
    event_type: str = Field(min_length=1)
    payload: dict[str, Any]


class OntologyGuidedEvaluationResult(EvidenceModel):
    schema_version: Literal["ontology-guided-evaluation-run-v1"] = RUN_SCHEMA_VERSION
    evaluator_version: Literal["ontology-guided-evaluator-v1"] = EVALUATOR_VERSION
    core_contract_version: str = CONTRACT_VERSION
    executor_version: str = OntologyGuidedExecutor.version
    recognition_run_id: str = Field(min_length=1)
    run_fingerprint: str = Field(min_length=64, max_length=64)
    model_identity: str = Field(min_length=1)
    root_class_iri: str = Field(min_length=1)
    scope_mode: Literal["document_graph", "focus_path"]
    focus_path: list[str] = Field(default_factory=list)
    ontology_snapshot: OntologySnapshot
    metadata_snapshot: MetadataSnapshot
    root_menu: LocalMenu
    graph: GraphSnapshot
    retrieval_plans: list[dict[str, Any]] = Field(default_factory=list)
    events: list[EvaluationEvent] = Field(default_factory=list)
    adapter_calls: list[AdapterCall] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)


class _ObservedAdapter:
    def __init__(
        self,
        delegate: RecognitionAdapter,
        callback: Callable[[AdapterCall], None] | None,
    ) -> None:
        self.delegate = delegate
        self.model_identity = delegate.model_identity
        self.callback = callback
        self.calls: list[AdapterCall] = []

    def inspect(self, task, context, predicate, menu) -> TaskOutcome:
        started = perf_counter()
        common = {
            "ordinal": len(self.calls) + 1,
            "task_id": task.task_id,
            "record_id": task.record_id,
            "subject_id": task.subject.entity_id,
            "predicate_iri": task.predicate_iri,
            "phase": task.phase,
            "hop": task.hop,
            "context_hash": context.context_hash,
        }
        try:
            outcome = self.delegate.inspect(task, context, predicate, menu)
        except Exception as exc:
            call = AdapterCall(
                **common,
                elapsed_seconds=perf_counter() - started,
                status="failed",
                error_code=type(exc).__name__,
            )
            self._record(call)
            raise
        call = AdapterCall(
            **common,
            elapsed_seconds=perf_counter() - started,
            status="complete",
            semantic_outcome=outcome.semantic_outcome,
            reason_code=outcome.reason_code,
        )
        self._record(call)
        return outcome

    def _record(self, call: AdapterCall) -> None:
        self.calls.append(call)
        if self.callback is not None:
            self.callback(call)


class OntologyGuidedEvaluationRunner:
    """Evaluation lifecycle adapter; recognition stays in the shared executor."""

    version = EVALUATOR_VERSION

    def __init__(
        self,
        *,
        ontology: OntologySnapshot,
        adapter: RecognitionAdapter,
        engine: object | None = None,
        focus_path: tuple[str, ...] = (),
        max_hops: int = 4,
        max_tasks: int = 2048,
        phase1_section_limit: int = 3,
        progress_hook: Callable[[str], bool] | None = None,
        call_hook: Callable[[AdapterCall], None] | None = None,
    ) -> None:
        if not adapter.model_identity:
            raise ValueError("evaluation adapter requires a frozen model identity")
        if len(focus_path) > max_hops:
            raise ValueError("focus path exceeds the frozen hop limit")
        self.ontology = ontology
        self.focus_path = tuple(focus_path)
        self.observed_adapter = _ObservedAdapter(adapter, call_hook)

        def predicate_filter(
            _subject: SubjectRef, predicate: SlotSpec | EdgeSpec, hop: int
        ) -> bool:
            if not self.focus_path or isinstance(predicate, SlotSpec):
                return True
            return hop < len(self.focus_path) and predicate.iri == self.focus_path[hop]

        self.executor = OntologyGuidedExecutor(
            ontology=ontology,
            engine=engine or object(),
            adapter=self.observed_adapter,
            max_hops=max_hops,
            max_tasks=max_tasks,
            phase1_section_limit=phase1_section_limit,
            progress_hook=progress_hook,
            predicate_filter=predicate_filter,
        )

    def run(
        self,
        *,
        recognition_run_id: str,
        ir: DocumentIR,
        metadata: MetadataSnapshot,
        root_class_iri: str,
        root_class_label: str,
        filename: str,
    ) -> OntologyGuidedEvaluationResult:
        if self.focus_path:
            current_classes = {root_class_iri}
            for hop, predicate_iri in enumerate(self.focus_path):
                next_classes = set()
                for class_iri in current_classes:
                    menu = compile_local_menu(
                        self.ontology,
                        SubjectRef(
                            entity_id=f"focus-validation-{hop}-{class_iri}",
                            revision=1,
                            class_iri=class_iri,
                            is_document_root=hop == 0,
                        ),
                        engine=self.executor.engine,
                    )
                    next_classes.update(
                        range_iri
                        for edge in menu.relationships
                        if edge.iri == predicate_iri
                        for range_iri in edge.range_class_iris
                    )
                if not next_classes:
                    raise ValueError(
                        f"focus path predicate is outside the frozen local menu at hop {hop}"
                    )
                current_classes = next_classes
        run_fingerprint = evidence_hash(
            {
                "evaluator_version": self.version,
                "executor_version": self.executor.version,
                "contract_version": CONTRACT_VERSION,
                "recognition_run_id": recognition_run_id,
                "document_hash": ir.document_hash,
                "structure_hash": ir.structure_hash,
                "ontology_hash": self.ontology.ontology_hash,
                "metadata_dependency_hash": metadata.dependency_hash,
                "root_class_iri": root_class_iri,
                "model_identity": self.observed_adapter.model_identity,
                "scope_mode": "focus_path" if self.focus_path else "document_graph",
                "focus_path": self.focus_path,
            }
        )
        execution = self.executor.run(
            recognition_run_id=recognition_run_id,
            run_fingerprint=run_fingerprint,
            ir=ir,
            metadata=metadata,
            root_class_iri=root_class_iri,
            root_class_label=root_class_label,
            filename=filename,
        )
        return OntologyGuidedEvaluationResult(
            recognition_run_id=recognition_run_id,
            run_fingerprint=run_fingerprint,
            model_identity=self.observed_adapter.model_identity,
            root_class_iri=root_class_iri,
            scope_mode="focus_path" if self.focus_path else "document_graph",
            focus_path=list(self.focus_path),
            ontology_snapshot=execution.ontology_snapshot,
            metadata_snapshot=metadata,
            root_menu=execution.root_menu,
            graph=execution.graph,
            retrieval_plans=execution.retrieval_plans,
            events=[
                EvaluationEvent(event_type=event_type, payload=payload)
                for event_type, payload in execution.events
            ],
            adapter_calls=self.observed_adapter.calls,
            diagnostics=execution.diagnostics,
        )


def build_quality_guided_variant(
    *,
    ontology: OntologySnapshot,
    adapter: RecognitionAdapter,
    engine: object | None = None,
    focus_path: tuple[str, ...] = (),
    max_hops: int = 4,
    max_tasks: int = 2048,
    phase1_section_limit: int = 3,
    progress_hook: Callable[[str], bool] | None = None,
    call_hook: Callable[[AdapterCall], None] | None = None,
) -> OntologyGuidedEvaluationRunner:
    return OntologyGuidedEvaluationRunner(
        ontology=ontology,
        adapter=adapter,
        engine=engine,
        focus_path=focus_path,
        max_hops=max_hops,
        max_tasks=max_tasks,
        phase1_section_limit=phase1_section_limit,
        progress_hook=progress_hook,
        call_hook=call_hook,
    )
