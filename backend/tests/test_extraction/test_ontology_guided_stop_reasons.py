"""Execution stop causes remain separate from per-record coverage states."""

from app.services.extraction.ontology_guided.executor import OntologyGuidedExecutor, TaskOutcome
from app.services.extraction.ontology_guided.records import RecordIndex
from app.services.extraction.ontology_guided.semantic_reranker import RankingPolicy, RankingService
from tests.test_extraction.test_semantic_ranking_execution import (
    ControlledRanking,
    NoFactsAdapter,
    fixture,
)


def test_task_budget_stops_with_examined_records_and_unattempted_slots(tmp_path):
    ontology, arguments = fixture(tmp_path)
    adapter, batches = NoFactsAdapter(), []
    result = OntologyGuidedExecutor(
        ontology=ontology, engine=object(), adapter=adapter, max_tasks=4,
    ).run(**arguments, batch_hook=batches.append)

    progress = result.graph.progress
    assert len(adapter.calls) == progress.tasks_attempted == progress.records_examined == 4
    assert progress.records_incomplete == 0
    assert progress.records_unattempted == progress.records_planned - 4 > 0
    assert progress.stop_reason == "task_budget_exhausted"
    assert progress.completion == "incomplete"
    assert all(batch.graph.progress.stop_reason is None for batch in batches)
    assert all(slot.stop_reason is None for batch in batches for slot in batch.graph.coverage)
    assert len(result.graph.coverage) == 2
    for slot in result.graph.coverage:
        assert slot.examined > 0
        assert slot.incomplete == 0
        assert slot.unattempted > 0
        assert slot.stop_reason == "task_budget_exhausted"
    for plan in result.retrieval_plans:
        assert all(
            entry["coverage_state"] in {"examined", "unattempted"}
            for entry in plan["ledger"].values()
        )


def test_budget_does_not_relabel_completed_slots(tmp_path):
    ontology, arguments = fixture(tmp_path)
    total = len(RecordIndex(arguments["ir"]).records) * 2
    result = OntologyGuidedExecutor(
        ontology=ontology, engine=object(), adapter=NoFactsAdapter(), max_tasks=total - 1,
    ).run(**arguments)

    assert result.graph.progress.stop_reason == "task_budget_exhausted"
    assert result.graph.progress.records_unattempted == 1
    assert sorted(slot.stop_reason for slot in result.graph.coverage) == [
        "queue_exhausted", "task_budget_exhausted",
    ]
    for slot in result.graph.coverage:
        assert slot.incomplete == 0
        assert slot.stop_reason == (
            "task_budget_exhausted" if slot.unattempted else "queue_exhausted"
        )


def test_finishing_at_task_limit_is_queue_exhaustion(tmp_path):
    ontology, arguments = fixture(tmp_path)
    total = len(RecordIndex(arguments["ir"]).records) * 2
    result = OntologyGuidedExecutor(
        ontology=ontology, engine=object(), adapter=NoFactsAdapter(), max_tasks=total,
    ).run(**arguments)

    assert result.graph.progress.tasks_attempted == total
    assert result.graph.progress.records_incomplete == 0
    assert result.graph.progress.records_unattempted == 0
    assert result.graph.progress.stop_reason == "queue_exhausted"
    assert result.graph.progress.completion == "in_scope_complete"
    assert all(slot.stop_reason == "queue_exhausted" for slot in result.graph.coverage)


def test_running_batches_are_not_incomplete_attempts_when_only_work_remains(tmp_path):
    ontology, arguments = fixture(tmp_path)
    batches = []
    result = OntologyGuidedExecutor(
        ontology=ontology, engine=object(), adapter=NoFactsAdapter(), max_tasks=1000,
    ).run(**arguments, batch_hook=batches.append)

    assert batches[0].graph.progress.records_unattempted > 0
    assert batches[0].graph.progress.records_incomplete == 0
    assert all(batch.graph.progress.stop_reason is None for batch in batches)
    assert all(slot.stop_reason is None for batch in batches for slot in batch.graph.coverage)
    assert result.graph.progress.stop_reason == "queue_exhausted"


def test_ranking_budget_pause_is_not_record_incompletion(tmp_path):
    ontology, arguments = fixture(tmp_path)
    result = OntologyGuidedExecutor(
        ontology=ontology, engine=object(), adapter=NoFactsAdapter(), max_tasks=1000,
        ranking_service=RankingService(
            RankingPolicy(
                mode="semantic", failure_policy="pause", max_model_calls_per_record=1,
            ),
            ControlledRanking(),
        ),
    ).run(**arguments)

    pending = result.ranking_state["service"]["pending_epochs"]
    assert pending and pending[0]["reason"] == "ranking_call_budget_exhausted"
    assert result.graph.progress.stop_reason == "ranking_paused"
    assert result.graph.progress.records_incomplete == 0
    assert result.graph.progress.records_unattempted > 0
    assert sum(slot.stop_reason == "ranking_paused" for slot in result.graph.coverage) == 1
    assert all(slot.stop_reason != "attempted_incomplete" for slot in result.graph.coverage)


def test_technical_incompletion_without_task_limit_keeps_failure_reason(tmp_path):
    ontology, arguments = fixture(tmp_path)

    class FailedAdapter:
        model_identity = "controlled-technical-failure"

        def inspect(self, task, context, predicate, menu):
            return TaskOutcome(
                semantic_outcome="not_checked", complete=False,
                reason_code="model_timeout", reason="Controlled incomplete verification.",
            )

    result = OntologyGuidedExecutor(
        ontology=ontology, engine=object(), adapter=FailedAdapter(), max_tasks=1000,
    ).run(**arguments)

    assert result.graph.progress.records_examined == 0
    assert result.graph.progress.records_incomplete == result.graph.progress.records_planned > 0
    assert result.graph.progress.records_unattempted == 0
    assert result.graph.progress.stop_reason == "attempted_incomplete"
    assert all(slot.stop_reason == "attempted_incomplete" for slot in result.graph.coverage)
