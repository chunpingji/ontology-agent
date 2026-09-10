"""An audited budget switch resumes the same paused run without accounting while off."""

from copy import deepcopy

from app.config import settings
from app.services.document_analysis import execution as execution_service
from app.services.extraction.ontology_guided.semantic_reranker import RankingPolicy, RankingService
from tests.test_extraction.test_document_analysis_execution_recovery import (
    CountingAdapter,
    _claim,
    _create_pending_run,
)
from tests.test_extraction.test_semantic_ranking import RankingModel


def test_disable_resumes_original_budget_pause_and_freezes_budget_accounting(
    client, db, analyst_headers, tmp_path, monkeypatch,
):
    monkeypatch.setattr(settings, "semantic_ranking_budget_enabled", True)
    monkeypatch.setattr(settings, "evidence_max_tasks", 2)
    adapter = CountingAdapter()
    model = RankingModel()
    model.count_tokens = lambda _text: 1
    policy = RankingPolicy(
        mode="semantic", failure_policy="pause", batch_size=1, pool_size=2,
        max_ranking_tokens_per_slot=1, max_ranking_tokens_per_run=1,
    )
    monkeypatch.setattr(execution_service, "configured_model_adapter", lambda: adapter)
    monkeypatch.setattr(execution_service, "_configured_ranking", lambda: (
        RankingService(policy, model), {
            "policy": policy.model_dump(mode="json"), "model": model.identity,
        },
    ))
    run_id = _create_pending_run(
        client, analyst_headers, tmp_path, monkeypatch, key="switch-budget-execution",
    )
    store, token = _claim(db, run_id)
    execution_service._execute_claimed(db, store, store.get_owned(run_id, "analyst"), token)
    run = store.get_owned(run_id, "analyst")
    fingerprint = run.run_fingerprint
    assert run.execution_status == "paused" and run.stop_reason == "ranking_paused"
    assert adapter.calls == []
    saved = execution_service._restore_ranking_state(
        db, store, run, final_fingerprint=fingerprint,
    )["service"]
    baseline = deepcopy(saved)
    assert saved["costs"]["tokens"] == 1
    assert saved["pending_epochs"][0]["reason"] == "ranking_token_budget_exhausted"
    graph_url = f"/api/document-analysis/runs/{run_id}/graph"
    old_graph = client.get(graph_url, headers=analyst_headers).json()

    def control(path, key):
        db.expire_all()
        current = store.get_owned(run_id, "analyst")
        response = client.post(
            f"/api/document-analysis/runs/{run_id}/{path}", headers=analyst_headers,
            json={"expected_revision": current.revision, "request_key": key,
                  "reason": "验证排序预算操作，不修改原运行依赖。"},
        )
        assert response.status_code == 202, response.text
        return response.json()

    disabled = control("ranking-budget/disable", "disable-budget")
    assert disabled["ranking_budget_enabled"] is False and disabled["status"] == "paused"
    disabled_graph = client.get(graph_url, headers=analyst_headers).json()
    assert disabled_graph["ranking"]["budget_enabled"] is False
    assert disabled_graph["graph_snapshot"] == old_graph["graph_snapshot"]
    assert disabled_graph["ranking"]["cost"] == old_graph["ranking"]["cost"]
    model_calls_before = len(model.calls)
    control("resume", "resume-without-budget")
    store, token = _claim(db, run_id)
    execution_service._execute_claimed(db, store, store.get_owned(run_id, "analyst"), token)
    run = store.get_owned(run_id, "analyst")
    assert run.run_fingerprint == fingerprint
    assert run.stop_reason == "task_budget_exhausted"  # Independent recognition budget.
    assert len(adapter.calls) == run.progress["tasks_attempted"] == 2
    assert len(model.calls) > model_calls_before
    after = execution_service._restore_ranking_state(
        db, store, run, final_fingerprint=fingerprint,
    )["service"]
    assert after["budget_enabled"] is False
    for key in (
        "costs", "slot_costs", "record_call_counts", "request_attempts",
        "dispatch_receipts", "model_observations",
    ):
        assert after[key] == baseline[key]
    assert after["epochs"] and all(
        epoch["costs"]["budget_accounted"] is False for epoch in after["epochs"]
    )
    assert baseline["pending_epochs"][0] in after["paused_attempts"]
    public = client.get(graph_url, headers=analyst_headers).json()
    assert public["ranking"]["cost"] == old_graph["ranking"]["cost"]
    assert all(not epoch["budget_accounted"] for epoch in public["ranking"]["epochs"])
    enabled = control("ranking-budget/enable", "reenable-budget")
    assert enabled["ranking_budget_enabled"] is True
    assert client.get(graph_url, headers=analyst_headers).json()["ranking"]["budget_enabled"]
    assert store.get_owned(run_id, "analyst").run_fingerprint == fingerprint
