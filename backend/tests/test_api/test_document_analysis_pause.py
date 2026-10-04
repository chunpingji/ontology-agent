"""Pause intent must remain valid while a worker advances run progress."""

import pytest
from sqlalchemy import func, select

from app.models.document_analysis import (
    DocumentAnalysisControlOperation,
    DocumentAnalysisExecution,
)
from app.services.document_analysis.run_store import DocumentAnalysisRunStore
from tests.test_extraction.test_document_harness_runtime import _run


def _advance_progress(db, run, token):
    store = DocumentAnalysisRunStore(db)
    store.append_event(
        run.recognition_run_id, run.owner_id, token,
        expected_head=run.event_head, event_key="background-progress", event_type="progress",
        payload={"status": "running"},
    )
    db.commit()


def test_pause_accepts_intent_after_worker_progress_and_replays_original_receipt(
    client, db, analyst_headers,
):
    run, token = _run(db)
    _advance_progress(db, run, token)
    url = f"/api/document-analysis/runs/{run.recognition_run_id}/pause"
    body = {"request_key": "pause-intent", "reason": "检查当前结果"}

    accepted = client.post(url, headers=analyst_headers, json=body)

    assert accepted.status_code == 202, accepted.text
    assert accepted.json()["operation"] == "pause"
    assert "pause" not in accepted.json()["available_actions"]
    store = DocumentAnalysisRunStore(db)
    current = store.get_owned(run.recognition_run_id, run.owner_id)
    execution = db.get(DocumentAnalysisExecution, run.recognition_run_id)
    assert current.execution_status == "pausing"
    assert execution.pause_requested
    assert current.control_version == 1

    store.complete_pause(
        run.recognition_run_id, run.owner_id, token, expected_revision=current.revision,
    )
    db.commit()
    replay = client.post(url, headers=analyst_headers, json=body)
    assert replay.status_code == 202, replay.text
    assert replay.json() == accepted.json()
    assert store.get_owned(run.recognition_run_id, run.owner_id).execution_status == "paused"
    assert db.scalar(select(func.count()).select_from(DocumentAnalysisControlOperation)) == 1

    changed = client.post(url, headers=analyst_headers, json={**body, "reason": "不同原因"})
    assert changed.status_code == 409
    assert changed.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


def test_pause_ignores_progress_versions_in_internal_callers(db):
    run, token = _run(db)
    stale_revision = run.revision
    _advance_progress(db, run, token)
    store = DocumentAnalysisRunStore(db)

    paused = store.request_control(
        run.recognition_run_id, run.owner_id, action="pause",
        expected_revision=stale_revision, expected_version=99,
        request_key="pause-intent", reason="检查当前结果",
    )
    assert paused.execution_status == "pausing"
    revision = paused.revision
    replay = store.request_control(
        run.recognition_run_id, run.owner_id, action="pause",
        expected_revision=revision, expected_version=100,
        request_key="pause-intent", reason="检查当前结果",
    )
    assert replay.revision == revision
    assert db.scalar(select(func.count()).select_from(DocumentAnalysisControlOperation)) == 1


def test_queued_pause_needs_no_version(client, db, analyst_headers):
    run, _token = _run(db)
    run.execution_status = "queued"
    db.commit()

    response = client.post(
        f"/api/document-analysis/runs/{run.recognition_run_id}/pause",
        headers=analyst_headers, json={"request_key": "pause-queued", "reason": "暂缓处理"},
    )

    assert response.status_code == 202, response.text
    assert response.json()["status"] == "paused"
    execution = db.get(DocumentAnalysisExecution, run.recognition_run_id)
    assert execution.status == "revoked"
    assert execution.execution_token_hash is None


@pytest.mark.parametrize("status", ["finished", "cancelled", "paused"])
def test_pause_still_rejects_ineligible_states(client, db, analyst_headers, status):
    run, _token = _run(db)
    run.execution_status = status
    db.commit()

    response = client.post(
        f"/api/document-analysis/runs/{run.recognition_run_id}/pause",
        headers=analyst_headers, json={"request_key": "pause-ineligible", "reason": "暂停"},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "RUN_STATE_CONFLICT"
    assert not db.get(DocumentAnalysisExecution, run.recognition_run_id).pause_requested
    assert db.scalar(select(func.count()).select_from(DocumentAnalysisControlOperation)) == 0


@pytest.mark.parametrize(
    ("headers", "status"),
    [
        ({"X-User": "someone-else", "X-Role": "senior_analyst"}, 404),
        ({"X-User": "analyst", "X-Role": "operator"}, 403),
    ],
)
def test_pause_keeps_owner_and_role_checks(client, db, headers, status):
    run, _token = _run(db)

    response = client.post(
        f"/api/document-analysis/runs/{run.recognition_run_id}/pause",
        headers=headers, json={"request_key": "pause-forbidden", "reason": "暂停"},
    )

    assert response.status_code == status
    assert not db.get(DocumentAnalysisExecution, run.recognition_run_id).pause_requested
    assert db.scalar(select(func.count()).select_from(DocumentAnalysisControlOperation)) == 0


@pytest.mark.parametrize("action", ["resume", "cancel"])
def test_other_controls_still_require_progress_version(client, db, analyst_headers, action):
    run, _token = _run(db)
    response = client.post(
        f"/api/document-analysis/runs/{run.recognition_run_id}/{action}",
        headers=analyst_headers, json={"request_key": "other-control", "reason": "操作"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


def test_cancel_still_rejects_stale_progress_version(client, db, analyst_headers):
    run, token = _run(db)
    stale_revision = run.revision
    _advance_progress(db, run, token)

    response = client.post(
        f"/api/document-analysis/runs/{run.recognition_run_id}/cancel",
        headers=analyst_headers, json={
            "expected_revision": stale_revision, "request_key": "cancel-stale", "reason": "取消",
        },
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "RUN_REVISION_CONFLICT"
