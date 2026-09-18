"""Retained task discovery is owner-scoped and never starts analysis work."""

from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest

from app.api import document_analysis
from app.config import settings
from app.dependencies import get_ontology_engine
from app.main import app
from app.models.document_analysis import DocumentAnalysisRun, DocumentRecognitionEvent
from app.services.document_analysis.run_store import DocumentAnalysisRunStore
from tests.test_api.test_document_analysis import ROOT_IRI, _create, _word_bytes


@pytest.fixture(autouse=True)
def history_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "document_analysis_storage_dir", tmp_path / "history")


def seed_run(db, number, *, owner="analyst", **changes):
    run, _ = DocumentAnalysisRunStore(db).create_run(
        owner_id=owner,
        request_key=f"history-{number}",
        recognition_run_id=UUID(int=number),
        filename="同名报告.docx",
        document_hash="a" * 64,
        root_class_iri=ROOT_IRI,
        root_class_label="CMC 报告",
    )
    run.created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for name, value in changes.items():
        setattr(run, name, value)
    db.commit()
    return run


def test_history_is_owner_scoped_paginated_and_excludes_unavailable_runs(
    client, db, analyst_headers
):
    seed_run(db, 1)
    seed_run(db, 2, execution_status="finished")
    seed_run(db, 3, execution_status="failed")
    seed_run(db, 4, deletion_state="requested")
    seed_run(db, 5, created_at=datetime(2026, 2, 1, tzinfo=timezone.utc))
    seed_run(db, 6, owner="another-analyst")
    seed_run(db, 7, deletion_state="deleted")
    seed_run(db, 8, expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
    seed_run(db, 9, scope_mode="focus_path")
    seed_run(db, 10, execution_status="expired")
    before_events = db.query(DocumentRecognitionEvent).count()
    pages = []
    for offset in (0, 2, 4, 6):
        response = client.get(
            f"/api/document-analysis/runs?limit=2&offset={offset}", headers=analyst_headers
        )
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store"
        pages.append(response.json())
    items = [item for page in pages for item in page["items"]]
    assert [item["recognition_run_id"] for item in items] == [
        str(UUID(int=number)) for number in (5, 4, 3, 2, 1)
    ]
    assert [page["has_more"] for page in pages] == [True, True, False, False]
    assert [item["status"] for item in items] == [
        "queued", "deleting", "retryable_failure", "finished", "queued"
    ]
    for item in items:
        assert set(item) == {
            "contract_version", "recognition_run_id", "run_revision", "event_head",
            "artifact_revision", "status", "stage", "input", "created_at", "expires_at",
        }
        assert item["input"]["filename"] == "同名报告.docx"
    assert db.query(DocumentRecognitionEvent).count() == before_events
    assert db.query(DocumentAnalysisRun).count() == 10

    foreign = client.get(
        "/api/document-analysis/runs",
        headers={"X-User": "another-analyst", "X-Role": "senior_analyst"},
    )
    assert foreign.status_code == 200, foreign.text
    assert [item["recognition_run_id"] for item in foreign.json()["items"]] == [str(UUID(int=6))]


def test_uploaded_run_remains_discoverable_without_url_or_model_dispatch(
    client, db, analyst_headers, tmp_path, monkeypatch
):
    dispatched = []
    monkeypatch.setattr(
        document_analysis, "_wake_dispatcher_or_fallback",
        lambda _tasks, run_id, **_kwargs: dispatched.append(run_id),
    )
    created = _create(client, analyst_headers, _word_bytes(tmp_path))
    assert created.status_code == 202, created.text
    accepted = created.json()
    assert len(dispatched) == 1
    events_before = db.query(DocumentRecognitionEvent).count()

    def forbidden_ontology():
        raise AssertionError("History must not load the ontology or start analysis")

    app.dependency_overrides[get_ontology_engine] = forbidden_ontology
    for _ in range(2):
        history = client.get("/api/document-analysis/runs", headers=analyst_headers)
        assert history.status_code == 200, history.text
        assert history.json()["items"][0]["recognition_run_id"] == accepted["recognition_run_id"]
        assert history.json()["items"][0]["input"] == accepted["input"]
        status = client.get(
            f"/api/document-analysis/runs/{accepted['recognition_run_id']}",
            headers=analyst_headers,
        )
        assert status.status_code == 200, status.text
        assert status.json()["status"] == "queued"
    assert len(dispatched) == 1
    assert db.query(DocumentRecognitionEvent).count() == events_before


def test_empty_history_and_authentication(client, analyst_headers):
    response = client.get("/api/document-analysis/runs", headers=analyst_headers)
    assert response.json() == {
        "contract_version": "document-analysis-runs-v1", "items": [], "has_more": False,
    }
    assert client.get("/api/document-analysis/runs").status_code == 401


@pytest.mark.parametrize("query", ["limit=0", "limit=101", "offset=-1", "limit=abc"])
def test_history_rejects_invalid_pagination(client, analyst_headers, query):
    response = client.get(f"/api/document-analysis/runs?{query}", headers=analyst_headers)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_REQUEST"
