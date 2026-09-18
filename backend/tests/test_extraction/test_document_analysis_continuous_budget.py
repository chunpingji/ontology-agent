"""Continuous budgets stop at unpaid boundaries and survive worker replacement."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models.document_analysis import DocumentAnalysisExecution, DocumentRunCurrentState
from app.services.document_analysis import current_state, execution, state_artifacts
from app.services.document_analysis.run_store import DocumentAnalysisRunStore
from app.services.extraction.ontology_guided.executor import TaskOutcome
from app.services.extraction.ontology_guided.records import RecordIndex
from tests.test_extraction import test_document_analysis_execution_recovery as recovery
from tests.test_extraction.test_document_state_artifacts import seed
from tests.test_extraction.test_tool_engine_execution import LINK, setup

pytest_plugins = ["tests.test_extraction.test_tool_engine_resume"]


def window(*, baseline=0, reserved=0, limit=32):
    return execution._ExecutionBudget(
        {"max_seconds": 1800, "max_model_calls": limit},
        {"started_at": datetime.now(UTC).isoformat(), "model_calls_baseline": baseline},
        reserved,
    )


@pytest.mark.parametrize("limit,expire_during_request", [(1, False), (2, False), (3, False),
                                                       (32, True)])
def test_paid_results_survive_budget_pause_and_cold_resume(
    tmp_path, monkeypatch, current_run, limit, expire_during_request,
):
    store, run, token = current_run
    args, executor, requests, _contexts, load = setup(tmp_path, monkeypatch, current_run)
    budget = window(limit=limit)
    if expire_during_request:
        from app.services.llm import local_client

        transport = local_client.responses_create

        def expire(*values, **kwargs):
            result = transport(*values, **kwargs)
            budget.deadline = 0
            return result

        monkeypatch.setattr(local_client, "responses_create", expire)

    def calls(state):
        current_state.persist_calls(store, run, token, run.run_fingerprint, state)
        budget.observe_calls(state)

    hooks = {
        "model_call_hook": calls,
        "protocol_result_loader": load,
        "work_hook": lambda changes: current_state.persist_boundary(
            store, run, token, changes=changes, fingerprint=run.run_fingerprint,
            expected_version=run.work_version,
        ),
        "batch_hook": lambda batch: current_state.persist_batch(
            store, run, token, batch=batch, fingerprint=run.run_fingerprint,
            ontology=executor().ontology, ir=args["ir"], metadata=args["metadata"],
            index=RecordIndex(args["ir"]), expected_version=run.work_version,
        ),
    }
    result = executor(progress_hook=budget.permits).run(**args, **hooks)
    assert len(requests) == (1 if expire_during_request else limit)
    assert budget.reason == ("execution_time_budget_exhausted" if expire_during_request
                             else "execution_model_call_budget_exhausted")
    assert result.graph.progress.completion == "incomplete"
    assert result.graph.progress.model_calls_unresolved == 0
    saved = current_state.restore_calls(store, run, run.run_fingerprint)
    before = deepcopy(saved["reservations"])
    assert sum(saved["lineage_calls"].values()) == len(requests)
    assert all(item["pending_request"] is None for item in saved["protocols"].values())
    if limit == 2:
        assert any(item["completed_tool_results"] for item in saved["protocols"].values())

    if expire_during_request:
        monkeypatch.setattr(local_client, "responses_create", transport)
    # Only this explicit continuation resets the execution window. The restored
    # protocols, receipts and each lineage's four-call allowance remain intact.
    budget = window(baseline=len(requests), reserved=len(requests))
    rows = current_state.restore_work(store, run, run.run_fingerprint).work_state
    control = rows["control"]["current"]
    resumed = executor(progress_hook=budget.permits).run(
        **args, **hooks, model_call_state=saved,
        resume_state={"work_state": rows, "frontier": control["frontier_policy"],
                      "diagnostics": control["diagnostics"]},
    )
    assert len([request for request in requests if request["predicate_iri"] == LINK]) == 3
    assert len(resumed.graph.relationship_groups) == 1
    final_calls = current_state.restore_calls(store, run, run.run_fingerprint)
    assert final_calls["reservations"][:len(before)] == before
    assert sum(final_calls["lineage_calls"].values()) == len(requests)
    assert max(final_calls["lineage_calls"].values()) <= 4
    assert resumed.graph.progress.model_calls_unresolved == 0


@pytest.mark.parametrize("max_seconds", [None, 18000])
def test_lease_replacement_does_not_reset_window_but_explicit_resume_does(
    db, monkeypatch, max_seconds,
):
    monkeypatch.setattr(state_artifacts, "performance_policy", lambda *_: {
        "state_storage_version": 4,
        "execution_budget": {"max_seconds": 1800, "max_model_calls": 32},
    })
    store = DocumentAnalysisRunStore(db)
    run, token = seed(store, "continuous-window")
    baseline = current_state.get_row(store, run, "execution:budget")
    assert baseline["model_calls_baseline"] == 0
    if max_seconds is not None:
        baseline["max_seconds"] = max_seconds
        current_state.put_rows(
            store, run, DocumentRunCurrentState, "execution:budget",
            {"current": baseline}, work_version=run.work_version,
        )
    run.progress = {"model_calls_reserved": 5}
    lease = db.get(DocumentAnalysisExecution, run.recognition_run_id)
    lease.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    token = store.claim(run.recognition_run_id, run.owner_id, actor="replace", worker_id="replace")
    db.commit()
    assert current_state.get_row(store, run, "execution:budget") == baseline
    assert run.progress["model_calls_reserved"] == 5
    store.update_stage(
        run.recognition_run_id, run.owner_id, token, expected_revision=run.revision,
        stage="recognition", execution_status="paused",
    )
    db.commit()
    store.request_control(run.recognition_run_id, run.owner_id, action="resume",
                          expected_revision=run.revision)
    db.commit()
    store.claim(run.recognition_run_id, run.owner_id, actor="continue", worker_id="continue")
    db.commit()
    resumed = current_state.get_row(store, run, "execution:budget")
    assert resumed["model_calls_baseline"] == 5
    assert resumed["started_at"] != baseline["started_at"]
    assert resumed.get("max_seconds") == max_seconds
    assert run.progress["model_calls_reserved"] == 5
    assert len(list(db.scalars(select(DocumentRunCurrentState).where(
        DocumentRunCurrentState.domain == "execution:budget",
        DocumentRunCurrentState.recognition_run_id == run.recognition_run_id,
    )))) == 1


@pytest.mark.parametrize("enabled", [False, True])
def test_worker_reports_budget_as_pause_and_preserves_legacy_policy(
    client, db, analyst_headers, tmp_path, monkeypatch, enabled,
):
    frozen = recovery.frozen_legacy_policy
    monkeypatch.setattr(recovery, "frozen_legacy_policy", lambda **kwargs: {
        **frozen(**kwargs),
        **({"execution_budget": {"max_seconds": 1800, "max_model_calls": 1}}
           if enabled else {}),
    })
    monkeypatch.setattr(execution.settings, "evidence_max_tasks", 2)

    class Adapter:
        model_identity = "controlled-budget"

        def inspect(self, _task, context, _predicate, _menu):
            context.before_model_call("discovery", 1)
            return TaskOutcome(
                semantic_outcome="unsupported", model_calls=1,
                reason_code="record_not_supporting_target", reason="预算测试中的确定性否定结果。",
            )

    monkeypatch.setattr(execution, "configured_model_adapter", Adapter)
    run_id = recovery._create_pending_run(
        client, analyst_headers, tmp_path, monkeypatch, key=f"continuous-{enabled}",
        current_state=True,
    )
    store, token = recovery._claim(db, run_id)
    execution._execute_claimed(db, store, store.get_owned(run_id, "analyst"), token)
    run = store.get_owned(run_id, "analyst")
    if enabled:
        assert run.execution_status == "paused"
        assert run.stop_reason == "execution_model_call_budget_exhausted"
        assert run.progress["model_calls_reserved"] == 1
        assert run.error is None
        response = client.get(f"/api/document-analysis/runs/{run_id}", headers=analyst_headers)
        assert response.status_code == 200
        assert "resume" in response.json()["available_actions"]
    else:
        assert run.stop_reason == "task_budget_exhausted"
        assert run.progress["model_calls_reserved"] == 2
        assert current_state.get_row(store, run, "execution:budget") is None


def test_failure_reservations_spend_budget_and_cannot_be_cleared_by_result():
    budget = window(baseline=7, reserved=7, limit=1)
    assert budget.permits("before_model_reservation")
    budget.observe_calls({"current_calls": 1, "reservations": [{"sequence": 8}]})
    # Saving a failed or paid response does not refund its reservation.
    budget.observe_calls({"current_calls": 1, "reservations": []})
    assert budget.permits("after_protocol_result")
    assert not budget.permits("before_model_reservation")
    assert budget.reason == "execution_model_call_budget_exhausted"
    assert budget.reserved_calls == 8


def test_new_run_policy_freezes_the_reference_protocol_and_budget(monkeypatch):
    monkeypatch.setattr(execution.settings, "document_analysis_execution_max_seconds", 30)
    monkeypatch.setattr(execution.settings, "document_analysis_execution_max_model_calls", 2)
    policy = execution.freeze_tool_engine_policy()
    monkeypatch.setattr(execution.settings, "document_analysis_execution_max_seconds", 60)
    assert policy["reference_resolution_version"] == 1
    assert policy["execution_budget"] == {"max_seconds": 30, "max_model_calls": 2}
    assert policy["max_lineage_calls"] == 4


def test_adjusted_time_limit_preserves_frozen_policy_and_call_budget(monkeypatch):
    policy = {"max_seconds": 1800, "max_model_calls": 32}
    baseline = {
        "started_at": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
        "model_calls_baseline": 55,
        "max_seconds": 18000,
    }
    monkeypatch.setattr(execution.time, "monotonic", lambda: 100.0)
    budget = execution._ExecutionBudget(policy, baseline, 56)
    assert budget.permits("before_model_reservation")
    assert budget.deadline == pytest.approx(100 + 4 * 3600, abs=1)
    assert policy == {"max_seconds": 1800, "max_model_calls": 32}
    budget.reserved_calls = 87
    assert not budget.permits("before_model_reservation")
    assert budget.reason == "execution_model_call_budget_exhausted"

    baseline["started_at"] = (datetime.now(UTC) - timedelta(hours=5, seconds=1)).isoformat()
    expired = execution._ExecutionBudget(policy, baseline, 56)
    assert not expired.permits("before_task")
    assert expired.reason == "execution_time_budget_exhausted"


@pytest.mark.parametrize("max_seconds", [0, -1, True, "18000", float("inf"), float("nan")])
def test_invalid_time_limit_adjustment_is_rejected(max_seconds):
    with pytest.raises(execution.CheckpointMismatch, match="execution time limit"):
        execution._ExecutionBudget(
            {"max_seconds": 1800, "max_model_calls": 32},
            {"started_at": datetime.now(UTC).isoformat(), "model_calls_baseline": 0,
             "max_seconds": max_seconds},
            0,
        )
