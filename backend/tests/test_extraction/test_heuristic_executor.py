"""Shared-executor acceptance tests for opt-in heuristic admission."""

from __future__ import annotations

from threading import Event

import pytest

from app.services.extraction.evidence_identity import evidence_hash
from app.services.extraction.ontology_guided.contracts import SlotSpec
from app.services.extraction.ontology_guided.executor import OntologyGuidedExecutor, TaskOutcome
from app.services.extraction.ontology_guided.heuristic_search import HeuristicSearchPolicy
from app.services.extraction.ontology_guided.metadata import prepare_metadata
from app.services.extraction.ontology_guided.records import RecordIndex
from app.services.extraction.ontology_guided.semantic_reranker import RankingPolicy, RankingService
from tests.test_extraction.test_ontology_guided_core import (
    APPEARANCE,
    REPORT,
    FakeAdapter,
    ontology,
    sample,
)
from tests.test_extraction.test_semantic_ranking_execution import (
    FIRST,
    SECOND,
    ControlledRanking,
    NoFactsAdapter,
    fixture,
)


def _product_arguments(tmp_path):
    analysis = sample(tmp_path)
    return dict(
        recognition_run_id="heuristic-integration-run",
        run_fingerprint="heuristic-integration-fingerprint",
        ir=analysis.ir,
        metadata=prepare_metadata(
            analysis.ir, section_tree=analysis.structure.section_tree.to_dict(),
            summary_version="heuristic-test",
        ),
        root_class_iri=REPORT, root_class_label="报告", filename="source.docx",
    )


def _search_summary(result):
    return next(value for kind, value in result.events if kind == "heuristic_complete")


def _semantic_service(model, *, pool_size=2):
    return RankingService(
        RankingPolicy(mode="semantic", pool_size=pool_size, ranking_timeout=10), model,
    )


def test_existing_default_executes_full_universe_without_heuristic_policy(tmp_path):
    snapshot, arguments = fixture(tmp_path)
    executor = OntologyGuidedExecutor(
        ontology=snapshot, engine=object(), adapter=NoFactsAdapter(), max_tasks=100,
    )
    result = executor.run(**arguments)
    record_count = len(RecordIndex(arguments["ir"]).records)

    assert executor.heuristic_policy is None
    assert not any(kind.startswith("heuristic_") for kind, _ in result.events)
    assert result.graph.progress.records_planned == record_count * 2
    assert result.graph.progress.records_examined == record_count * 2
    assert result.graph.progress.completion == "in_scope_complete"


def test_proved_local_relation_expands_child_without_activating_semantic(tmp_path):
    arguments = _product_arguments(tmp_path)
    ranking = ControlledRanking()
    result = OntologyGuidedExecutor(
        ontology=ontology(), engine=object(), adapter=FakeAdapter(), max_tasks=100,
        heuristic_policy=HeuristicSearchPolicy(initial_page_size=1),
        ranking_service=_semantic_service(ranking),
    ).run(**arguments)
    record_ids = set(RecordIndex(arguments["ir"]).record_positions)

    assert ranking.calls == []
    assert result.ranking_state["service"]["epochs"] == []
    assert any(prop.predicate_iri == APPEARANCE for prop in result.graph.properties)
    assert any(slot.predicate_iri == APPEARANCE for slot in result.graph.coverage)
    for plan in result.retrieval_plans:
        assert set(plan["ledger"]) == record_ids
        assert set(plan["frozen_record_ids"]) == record_ids
    assert result.graph.progress.records_unattempted > 0
    assert result.graph.progress.completion == "incomplete"
    assert result.graph.progress.stop_reason == "adaptive_search_saturated"
    summary = _search_summary(result)
    assert summary["resume_supported"] is False
    assert all(slot["status"] == "local_results_only" for slot in summary["slots"])


def test_empty_discovery_runs_committed_semantic_then_distinct_exploration(tmp_path):
    snapshot, arguments = fixture(tmp_path)
    ranking = ControlledRanking()
    result = OntologyGuidedExecutor(
        ontology=snapshot, engine=object(), adapter=NoFactsAdapter(), max_tasks=100,
        heuristic_policy=HeuristicSearchPolicy(
            semantic_page_size=1, exploration_page_size=2, max_exploration_pages=1,
        ),
        ranking_service=_semantic_service(ranking),
    ).run(**arguments)

    assert any(operation == "embed" for operation, _ in ranking.calls)
    assert any(operation == "score_pairs" for operation, _ in ranking.calls)
    events = result.events
    commits = [(position, payload) for position, (kind, payload) in enumerate(events)
               if kind == "ranking_epoch_committed"]
    admissions = [(position, payload) for position, (kind, payload) in enumerate(events)
                  if kind == "heuristic_admission"]
    assert {payload["stage"] for _, payload in admissions} == {"H2", "H3"}
    for position, admission in admissions:
        if admission["stage"] == "H2":
            assert any(commit_position < position and epoch["plan_id"] == admission["plan_id"]
                       for commit_position, epoch in commits)
    tasks = [payload["task"] for kind, payload in events if kind == "task_outcome"]
    identities = [(task["subject"]["entity_id"], task["predicate_iri"], task["record_id"])
                  for task in tasks]
    assert len(identities) == len(set(identities))
    assert result.graph.progress.records_examined > 0
    assert result.graph.progress.records_unattempted > 0
    assert result.graph.progress.records_incomplete == 0
    assert result.graph.progress.completion == "incomplete"


def test_disabled_semantic_explores_without_fictitious_epoch(tmp_path):
    snapshot, arguments = fixture(tmp_path)
    result = OntologyGuidedExecutor(
        ontology=snapshot, engine=object(), adapter=NoFactsAdapter(), max_tasks=100,
        heuristic_policy=HeuristicSearchPolicy(exploration_page_size=2),
    ).run(**arguments)
    summary = _search_summary(result)

    assert not any(kind == "ranking_epoch_committed" for kind, _ in result.events)
    assert {payload["stage"] for kind, payload in result.events
            if kind == "heuristic_admission"} == {"H3"}
    assert all(slot["semantic_epoch_id"] is None for slot in summary["slots"])
    assert all(slot["semantic_skip_reason"] == "ranking_policy_deterministic"
               for slot in summary["slots"])
    assert result.graph.progress.records_unattempted > 0


def test_technical_failure_retries_same_record_without_becoming_empty_search(tmp_path):
    arguments = _product_arguments(tmp_path)
    ranking = ControlledRanking()

    class FailureAdapter:
        model_identity = "controlled-model-timeout"

        def inspect(self, task, context, predicate, menu):
            return TaskOutcome(
                semantic_outcome="not_checked", complete=False,
                reason_code="model_timeout", reason="Controlled failure before verification.",
            )

    result = OntologyGuidedExecutor(
        ontology=ontology(), engine=object(), adapter=FailureAdapter(), max_tasks=100,
        heuristic_policy=HeuristicSearchPolicy(initial_page_size=1),
        ranking_service=_semantic_service(ranking),
    ).run(**arguments)

    assert ranking.calls == []
    tasks = [payload["task"] for kind, payload in result.events if kind == "task_outcome"]
    assert len(tasks) == 2
    assert len({task["record_id"] for task in tasks}) == 1
    assert tasks[-1]["retry_kind"] == "technical_once"
    assert result.graph.progress.records_examined == 0
    assert result.graph.progress.records_incomplete == 1
    assert result.graph.progress.records_unattempted > 0
    assert result.graph.progress.stop_reason == "attempted_incomplete"
    assert _search_summary(result)["slots"][0]["status"] == "technical_blocked"


def test_task_budget_exhaustion_does_not_start_new_semantic_work(tmp_path):
    snapshot, arguments = fixture(tmp_path)
    product_arguments = _product_arguments(tmp_path)
    arguments.update(ir=product_arguments["ir"], metadata=product_arguments["metadata"])
    snapshot.classes[arguments["root_class_iri"]].declared_properties = [
        SlotSpec(iri=FIRST, label="外观"),
    ]
    snapshot.ontology_hash = evidence_hash(snapshot.classes)
    ranking, adapter = ControlledRanking(), NoFactsAdapter()
    result = OntologyGuidedExecutor(
        ontology=snapshot, engine=object(), adapter=adapter, max_tasks=1,
        heuristic_policy=HeuristicSearchPolicy(initial_page_size=1),
        ranking_service=_semantic_service(ranking),
    ).run(**arguments)

    assert len(adapter.calls) == 1
    assert ranking.calls == []
    assert result.graph.progress.stop_reason == "task_budget_exhausted"
    assert result.graph.progress.completion == "incomplete"


def test_lexical_backlog_cannot_starve_another_slots_semantic_search(tmp_path):
    snapshot, arguments = fixture(tmp_path)
    snapshot.classes[arguments["root_class_iri"]].declared_properties = [
        SlotSpec(iri=FIRST, label="来源记录"),
        SlotSpec(iri=SECOND, label="第二属性"),
    ]
    snapshot.ontology_hash = evidence_hash(snapshot.classes)
    quota = 2
    policy = HeuristicSearchPolicy(
        initial_page_size=1, expanded_page_size=1, exploration_page_size=1,
        max_cheap_tasks_before_semantic=quota,
    )
    started = Event()
    first_ranking_after = []

    class WatchingAdapter(NoFactsAdapter):
        def __init__(self):
            super().__init__()
            self.cheap_calls = 0
            self.quota_boundary_started = None

        def inspect(self, task, context, predicate, menu):
            if predicate.iri == FIRST:
                self.cheap_calls += 1
                if self.cheap_calls == quota + 1:
                    # Yield to the actual background preparation thread; the
                    # model request is not blocked behind a synthetic sleep.
                    self.quota_boundary_started = started.wait(0.5)
            return super().inspect(task, context, predicate, menu)

    adapter = WatchingAdapter()

    class WatchingRanking(ControlledRanking):
        def count_tokens(self, text):
            if not started.is_set():
                first_ranking_after.append(adapter.cheap_calls)
                started.set()
            return super().count_tokens(text)

    ranking = WatchingRanking()
    result = OntologyGuidedExecutor(
        ontology=snapshot, engine=object(), adapter=adapter, max_tasks=100,
        heuristic_policy=policy, ranking_service=_semantic_service(ranking),
    ).run(**arguments)

    assert adapter.quota_boundary_started is True
    assert first_ranking_after and first_ranking_after[0] <= quota + 1
    assert any(kind == "heuristic_admission" and payload["predicate_iri"] == SECOND
               and payload["stage"] == "H2" for kind, payload in result.events)
    assert policy.snapshot()["max_cheap_tasks_before_semantic"] == quota


@pytest.mark.parametrize("quota", [0, -1, 1.5, True])
def test_semantic_fairness_quota_is_a_positive_frozen_integer(quota):
    with pytest.raises(ValueError, match="max_cheap_tasks_before_semantic"):
        HeuristicSearchPolicy(max_cheap_tasks_before_semantic=quota)


@pytest.mark.parametrize("state_key", ["resume_state", "ranking_state", "model_call_state"])
@pytest.mark.parametrize("state", [{}, {"version": "prior-state"}])
def test_experimental_resume_is_rejected_before_any_adapter_or_ranking_call(
    tmp_path, state_key, state,
):
    snapshot, arguments = fixture(tmp_path)
    adapter, ranking = NoFactsAdapter(), ControlledRanking()
    executor = OntologyGuidedExecutor(
        ontology=snapshot, engine=object(), adapter=adapter,
        heuristic_policy=HeuristicSearchPolicy(), ranking_service=_semantic_service(ranking),
    )
    with pytest.raises(ValueError, match="fresh run"):
        executor.run(**arguments, **{state_key: state})
    assert adapter.calls == []
    assert ranking.calls == []


@pytest.mark.parametrize("rejected_field,rejected_value", [
    ("policy_eligible", False), ("polarity", "conditional"), ("polarity", "negated"),
])
def test_unproved_or_nonaffirmed_incoming_edge_cannot_expand_child_properties(
    tmp_path, rejected_field, rejected_value,
):
    arguments = _product_arguments(tmp_path)

    class UngatedAdapter(FakeAdapter):
        def inspect(self, task, context, predicate, menu):
            outcome = super().inspect(task, context, predicate, menu)
            return outcome.model_copy(update={"edges": [
                edge.model_copy(update={rejected_field: rejected_value}) for edge in outcome.edges
            ]})

    result = OntologyGuidedExecutor(
        ontology=ontology(), engine=object(), adapter=UngatedAdapter(), max_tasks=100,
        heuristic_policy=HeuristicSearchPolicy(initial_page_size=1, exploration_page_size=1),
    ).run(**arguments)

    assert result.graph.edges
    assert not any(slot.predicate_iri == APPEARANCE for slot in result.graph.coverage)
    assert not any(prop.predicate_iri == APPEARANCE for prop in result.graph.properties)
    assert all(plan["subject"]["is_document_root"] for plan in result.retrieval_plans)
