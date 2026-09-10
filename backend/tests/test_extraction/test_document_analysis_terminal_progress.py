"""Terminal execution causes overlay the last immutable recognition checkpoint."""

from copy import deepcopy

import pytest

from app.config import settings
from app.services.document_analysis import execution as execution_service
from app.services.extraction.ontology_guided.semantic_reranker import RankingPolicy, RankingService
from tests.test_extraction.test_document_analysis_execution_recovery import (
    CountingAdapter,
    _claim,
    _create_pending_run,
)
from tests.test_extraction.test_semantic_ranking import RankingModel


@pytest.mark.parametrize(
    ("max_tasks", "execution_status", "stop_reason"),
    [(1, "failed", "task_budget_exhausted"), (1000, "finished", "queue_exhausted")],
)
def test_terminal_reason_preserves_last_checkpoint_coverage_and_identity(
    client, db, analyst_headers, tmp_path, monkeypatch, max_tasks, execution_status, stop_reason,
):
    monkeypatch.setattr(settings, "semantic_ranking_enabled", False)
    monkeypatch.setattr(settings, "evidence_max_tasks", max_tasks)
    adapter = CountingAdapter()
    monkeypatch.setattr(execution_service, "configured_model_adapter", lambda: adapter)
    run_id = _create_pending_run(
        client, analyst_headers, tmp_path, monkeypatch, key=f"terminal-progress-{max_tasks}",
    )
    store, token = _claim(db, run_id)
    execution_service._execute_claimed(db, store, store.get_owned(run_id, "analyst"), token)

    run = store.get_owned(run_id, "analyst")
    assert run.execution_status == execution_status
    assert run.stop_reason == run.progress["stop_reason"] == stop_reason
    checkpoint = execution_service._restore_recognition_checkpoint(
        db, store, run, final_fingerprint=run.run_fingerprint,
    )
    assert checkpoint is not None
    graph_state = deepcopy(checkpoint.graph_state)
    assert graph_state["progress"]["stop_reason"] is None
    for key in (
        "tasks_attempted", "records_planned", "records_examined",
        "records_incomplete", "records_unattempted",
    ):
        assert run.progress[key] == graph_state["progress"][key]
    assert run.progress["records_incomplete"] == 0

    response = client.get(f"/api/document-analysis/runs/{run_id}", headers=analyst_headers)
    assert response.status_code == 200
    assert response.json()["progress"]["stop_reason"] == stop_reason
    graph_response = client.get(
        f"/api/document-analysis/runs/{run_id}/graph", headers=analyst_headers,
    )
    assert graph_response.status_code == 200
    assert graph_response.json()["coverage"]["stop_reason"] == stop_reason
    restored = execution_service._restore_recognition_checkpoint(
        db, store, store.get_owned(run_id, "analyst"), final_fingerprint=run.run_fingerprint,
    )
    assert restored.graph_state == graph_state
    assert restored.content_hash == checkpoint.content_hash


def test_ranking_pause_after_committed_work_keeps_checkpoint_and_budget_reason(
    client, db, analyst_headers, tmp_path, monkeypatch,
):
    monkeypatch.setattr(settings, "evidence_max_tasks", 1000)
    adapter = CountingAdapter()

    class PauseAfterRecognitionModel(RankingModel):
        def count_tokens(self, text):
            if adapter.calls:
                raise ValueError("ranking_call_budget_exhausted")
            return super().count_tokens(text)

    model = PauseAfterRecognitionModel()
    policy = RankingPolicy(mode="semantic", failure_policy="pause")
    monkeypatch.setattr(execution_service, "configured_model_adapter", lambda: adapter)
    monkeypatch.setattr(
        execution_service, "_configured_ranking",
        lambda: (RankingService(policy, model), {
            "policy": policy.model_dump(mode="json"), "model": model.identity,
        }),
    )
    run_id = _create_pending_run(
        client, analyst_headers, tmp_path, monkeypatch, key="ranking-pause-after-work",
    )
    store, token = _claim(db, run_id)
    execution_service._execute_claimed(db, store, store.get_owned(run_id, "analyst"), token)

    run = store.get_owned(run_id, "analyst")
    assert run.execution_status == "paused"
    assert run.stop_reason == run.progress["stop_reason"] == "ranking_paused"
    checkpoint = execution_service._restore_recognition_checkpoint(
        db, store, run, final_fingerprint=run.run_fingerprint,
    )
    assert checkpoint is not None
    assert checkpoint.graph_state["progress"]["stop_reason"] is None
    assert run.progress["tasks_attempted"] == len(adapter.calls) > 0
    for key in (
        "tasks_attempted", "records_planned", "records_examined",
        "records_incomplete", "records_unattempted",
    ):
        assert run.progress[key] == checkpoint.graph_state["progress"][key]
    graph = client.get(f"/api/document-analysis/runs/{run_id}/graph", headers=analyst_headers)
    assert graph.status_code == 200
    assert graph.json()["coverage"]["stop_reason"] == "ranking_paused"
    ranking = graph.json()["ranking"]
    assert ranking["committed_epochs"] > 0
    assert ranking["paused"]
    assert ranking["reasons"] == ["ranking_call_budget_exhausted"]
    restored = execution_service._restore_recognition_checkpoint(
        db, store, store.get_owned(run_id, "analyst"), final_fingerprint=run.run_fingerprint,
    )
    assert restored.graph_state == checkpoint.graph_state
    assert restored.content_hash == checkpoint.content_hash
