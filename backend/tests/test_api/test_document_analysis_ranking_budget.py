"""Audited ranking-budget controls preserve source identity and the accounting baseline."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.api import document_analysis
from app.config import settings
from app.models.document_analysis import DocumentAnalysisRun, DocumentRecognitionEvent
from app.services.document_analysis import execution as execution_service
from app.services.document_analysis.run_store import (
    DocumentAnalysisRunStore,
    FenceViolation,
    InvalidRunState,
    content_hash,
)
from app.services.extraction.ontology_guided.contracts import RunProgress
from tests.test_extraction.test_document_analysis_execution_recovery import (
    _claim,
    _create_pending_run,
)


@pytest.fixture
def paused_budget_run(client, db, analyst_headers, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "semantic_ranking_budget_enabled", True)
    wakes = []
    monkeypatch.setattr(
        document_analysis, "_wake_dispatcher_or_fallback", lambda *_a, **_k: wakes.append(True),
    )
    run_id = _create_pending_run(
        client, analyst_headers, tmp_path, monkeypatch, key="ranking-budget-control",
    )
    wakes.clear()
    store, token = _claim(db, run_id)
    run = store.get_owned(run_id, "analyst")
    run = store.update_stage(
        run_id, "analyst", token, expected_revision=run.revision,
        stage="recognition", run_fingerprint="f" * 64,
    )
    db.commit()
    ranking = {
        "recognition_run_id": run_id, "run_fingerprint": run.run_fingerprint,
        "service": {
            "policy": {"mode": "semantic"}, "epochs": [], "pending_epochs": [],
            "costs": {"model_calls": 2, "tokens": 100, "input_pairs": 2},
            "request_attempts": {"request": 1}, "record_call_counts": {"record": 2},
            "slot_costs": {"slot": 100},
        },
    }
    execution_service._persist_ranking_state(
        db, store, run, token, final_fingerprint=run.run_fingerprint, state=ranking,
    )
    run = store.get_owned(run_id, "analyst")
    execution_service._finish(
        db, store, run, token, status="paused", public_status="paused", public_stage="extracting",
        progress=RunProgress(
            tasks_attempted=2, records_planned=3, records_examined=2, records_unattempted=1,
        ),
        stop_reason="ranking_paused",
    )
    return run_id, store, ranking, wakes, token


def _request(run, key, *, reason="调整本次运行排序预算开关"):
    return {"expected_revision": run.revision, "request_key": key, "reason": reason}


def test_budget_toggle_is_audited_idempotent_and_does_not_resume_or_rewrite_history(
    client, db, analyst_headers, paused_budget_run,
):
    run_id, store, ranking, wakes, _token = paused_budget_run
    run = store.get_owned(run_id, "analyst")
    before = (run.run_fingerprint, deepcopy(run.progress), run.artifact_revision)
    request = _request(run, "disable-once")
    endpoint = f"/api/document-analysis/runs/{run_id}/ranking-budget"
    disabled = client.post(f"{endpoint}/disable", headers=analyst_headers, json=request)
    assert disabled.status_code == 202, disabled.text
    result = disabled.json()
    assert result["operation"] == "ranking_budget_disable"
    assert result["status"] == "paused" and result["ranking_budget_enabled"] is False
    assert "ranking_budget_enable" in result["available_actions"]
    assert "ranking_budget_disable" not in result["available_actions"]
    assert not wakes
    run = store.get_owned(run_id, "analyst")
    assert (run.run_fingerprint, run.progress, run.artifact_revision) == before
    assert execution_service._restore_ranking_state(
        db, store, run, final_fingerprint=run.run_fingerprint,
    ) == ranking
    event = db.scalar(select(DocumentRecognitionEvent).where(
        DocumentRecognitionEvent.recognition_run_id == run_id,
        DocumentRecognitionEvent.event_key == "control:ranking_budget_disable:disable-once",
    ))
    assert event.payload["actor"] == "analyst"
    assert event.payload["actor_role"] == "senior_analyst"
    assert event.payload["previous_ranking_budget_enabled"] is True
    assert event.payload["ranking_budget_enabled"] is False
    assert event.payload["reason"] == request["reason"]
    assert client.get(f"/api/document-analysis/runs/{run_id}", headers=analyst_headers).json()[
        "ranking_budget_enabled"
    ] is False
    graph = client.get(f"/api/document-analysis/runs/{run_id}/graph", headers=analyst_headers)
    assert graph.json()["ranking"]["budget_enabled"] is False
    assert graph.json()["ranking"]["cost"]["model_calls"] == 2

    replay = client.post(f"{endpoint}/disable", headers=analyst_headers, json=request)
    assert replay.json() == result
    conflict = client.post(
        f"{endpoint}/disable", headers=analyst_headers,
        json={**request, "reason": "same key with changed reason"},
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    stale = client.post(
        f"{endpoint}/enable", headers=analyst_headers,
        json={**request, "request_key": "stale-enable"},
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "RUN_REVISION_CONFLICT"
    enabled = client.post(
        f"{endpoint}/enable", headers=analyst_headers,
        json=_request(store.get_owned(run_id, "analyst"), "enable-once"),
    )
    assert enabled.status_code == 202 and enabled.json()["ranking_budget_enabled"] is True
    assert "ranking_budget_disable" in enabled.json()["available_actions"]
    late_replay = client.post(f"{endpoint}/disable", headers=analyst_headers, json=request)
    assert late_replay.json() == result
    current = store.get_owned(run_id, "analyst")
    assert current.ranking_budget_enabled is True
    assert current.revision == enabled.json()["run_revision"]
    assert current.control_version == 2 and not wakes


def test_budget_disable_then_explicit_resume_preserves_frozen_run(
    client, analyst_headers, paused_budget_run,
):
    run_id, store, _ranking, wakes, _token = paused_budget_run
    run = store.get_owned(run_id, "analyst")
    fingerprint = run.run_fingerprint
    disabled = client.post(
        f"/api/document-analysis/runs/{run_id}/ranking-budget/disable", headers=analyst_headers,
        json=_request(run, "disable-before-resume"),
    )
    assert disabled.status_code == 202 and not wakes
    resumed = client.post(
        f"/api/document-analysis/runs/{run_id}/resume", headers=analyst_headers,
        json=_request(store.get_owned(run_id, "analyst"), "resume-without-budget"),
    )
    assert resumed.status_code == 202
    assert resumed.json()["status"] == "queued"
    assert resumed.json()["ranking_budget_enabled"] is False
    assert wakes == [True]
    assert store.get_owned(run_id, "analyst").run_fingerprint == fingerprint


def test_budget_change_revokes_a_late_worker_even_if_its_lease_has_not_expired(
    client, db, analyst_headers, paused_budget_run,
):
    run_id, store, ranking, wakes, token = paused_budget_run
    run = store.get_owned(run_id, "analyst")
    store.assert_fence(run_id, "analyst", token)
    response = client.post(
        f"/api/document-analysis/runs/{run_id}/ranking-budget/disable", headers=analyst_headers,
        json=_request(run, "revoke-old-budget-owner"),
    )
    assert response.status_code == 202
    with pytest.raises(FenceViolation):
        execution_service._persist_ranking_state(
            db, store, store.get_owned(run_id, "analyst"), token,
            final_fingerprint=run.run_fingerprint, state=ranking,
        )
    assert not wakes


@pytest.mark.parametrize("status", [
    "queued", "running", "pausing", "finished", "cancelled", "blocked_dependency",
])
def test_budget_control_rejects_active_or_ineligible_runs(
    client, db, analyst_headers, paused_budget_run, status,
):
    run_id, store, _ranking, wakes, _token = paused_budget_run
    run = store.get_owned(run_id, "analyst")
    run.execution_status = status
    db.commit()
    response = client.post(
        f"/api/document-analysis/runs/{run_id}/ranking-budget/disable", headers=analyst_headers,
        json=_request(run, "ineligible-budget"),
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "RUN_STATE_CONFLICT"
    assert store.get_owned(run_id, "analyst").ranking_budget_enabled is True
    assert not wakes


def test_failed_run_can_change_budget_but_expired_run_cannot(
    client, db, analyst_headers, paused_budget_run,
):
    run_id, store, _ranking, _wakes, _token = paused_budget_run
    run = store.get_owned(run_id, "analyst")
    run.execution_status = "failed"
    db.commit()
    response = client.post(
        f"/api/document-analysis/runs/{run_id}/ranking-budget/disable", headers=analyst_headers,
        json=_request(run, "failed-disable"),
    )
    assert response.status_code == 202 and response.json()["status"] == "retryable_failure"
    run = store.get_owned(run_id, "analyst")
    run.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    denied = client.post(
        f"/api/document-analysis/runs/{run_id}/ranking-budget/enable", headers=analyst_headers,
        json=_request(run, "expired-enable"),
    )
    assert denied.status_code == 409
    assert store.get_owned(run_id, "analyst").ranking_budget_enabled is False


@pytest.mark.parametrize(("headers", "expected"), [
    ({"X-User": "another-analyst", "X-Role": "senior_analyst"}, 404),
    ({"X-User": "analyst", "X-Role": "qa"}, 403),
])
def test_budget_control_requires_owner_and_write_role(client, paused_budget_run, headers, expected):
    run_id, store, _ranking, wakes, _token = paused_budget_run
    run = store.get_owned(run_id, "analyst")
    revision = run.revision
    response = client.post(
        f"/api/document-analysis/runs/{run_id}/ranking-budget/disable", headers=headers,
        json=_request(run, "unauthorized-disable"),
    )
    assert response.status_code == expected
    assert store.get_owned(run_id, "analyst").revision == revision
    assert not wakes


@pytest.mark.parametrize("enabled", [True, False])
def test_new_runs_freeze_the_configured_budget_default_without_changing_replay(
    client, db, analyst_headers, tmp_path, monkeypatch, enabled,
):
    monkeypatch.setattr(settings, "semantic_ranking_budget_enabled", enabled)
    run_id = _create_pending_run(
        client, analyst_headers, tmp_path, monkeypatch, key="configured-budget-default",
    )
    assert db.get(DocumentAnalysisRun, run_id).ranking_budget_enabled is enabled
    monkeypatch.setattr(settings, "semantic_ranking_budget_enabled", not enabled)
    response = client.get(f"/api/document-analysis/runs/{run_id}", headers=analyst_headers)
    assert response.json()["ranking_budget_enabled"] is enabled


def test_old_delete_receipt_remains_safe_and_unchanged_with_optional_budget_field():
    payload = {
        "contract_version": "document-analysis-runs-v1", "recognition_run_id": "old-run",
        "run_revision": 2, "event_head": 1, "artifact_revision": 1,
        "status": "deleting", "stage": "accepted", "operation": "delete",
        "operation_status": "accepted", "available_actions": [],
    }
    for value in (payload, {**payload, "ranking_budget_enabled": False}):
        assert DocumentAnalysisRunStore._verified_delete_result_payload(
            "old-run", value, content_hash(value),
        ) == value
    unsafe = {**payload, "ranking_budget_enabled": "private uploaded content"}
    with pytest.raises(InvalidRunState, match="unsafe fields"):
        DocumentAnalysisRunStore._verified_delete_result_payload(
            "old-run", unsafe, content_hash(unsafe),
        )
