"""Display artifacts remain equivalent, read-only and private before cache hits."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import update

from app.config import settings
from app.models.document_analysis import DocumentAnalysisRun
from app.services.document_analysis.application import DocumentAnalysisApplication
from app.services.document_analysis.public_projection import PUBLIC_TO_INTERNAL_PROJECTION

from .test_document_analysis import _create, _word_bytes


def test_compact_graph_matches_all_legacy_projections_without_loading_internal_state(
    client, db, analyst_headers, tmp_path, monkeypatch,
):
    monkeypatch.setattr(settings, "document_analysis_storage_dir", tmp_path / "artifacts")
    created = _create(client, analyst_headers, _word_bytes(tmp_path), request_key="compact")
    run_id = created.json()["recognition_run_id"]
    application = DocumentAnalysisApplication(db, ontology_engine=object())
    run = application.get_run(run_id, "analyst")
    read = application._artifact_payload
    compact = {projection: application.graph_response(run, projection=projection)
               for projection in PUBLIC_TO_INTERNAL_PROJECTION}

    def legacy_reader(run, kind):
        if kind in {"public_graph", "ranking_summary"}:
            return None
        return read(run, kind)

    monkeypatch.setattr(application, "_artifact_payload", legacy_reader)
    for projection, response in compact.items():
        assert application.graph_response(run, projection=projection) == response

    def compact_reader(run, kind):
        assert kind not in {"graph", "ranking_state", "ontology_snapshot", "recognition_checkpoint"}
        return read(run, kind)

    monkeypatch.setattr(application, "_artifact_payload", compact_reader)
    for projection, response in compact.items():
        assert application.graph_response(run, projection=projection) == response


def test_graph_cache_checks_projection_budget_owner_expiry_and_deletion(
    client, db, analyst_headers, tmp_path, monkeypatch,
):
    monkeypatch.setattr(settings, "document_analysis_storage_dir", tmp_path / "artifacts")
    run_id = _create(client, analyst_headers, _word_bytes(tmp_path)).json()["recognition_run_id"]
    path = f"/api/document-analysis/runs/{run_id}"
    first = client.get(path + "/graph", headers=analyst_headers)
    assert first.status_code == 200
    cached_headers = {**analyst_headers, "If-None-Match": first.headers["etag"]}
    assert client.get(path + "/graph", headers=cached_headers).status_code == 304
    assert client.get(path + "/graph", params={"projection": "all_candidates"},
                      headers=cached_headers).status_code == 200
    assert client.get(path + "/graph", headers={**cached_headers, "X-User": "stranger"}
                      ).status_code == 404
    run = db.get(DocumentAnalysisRun, run_id)
    run.ranking_budget_enabled = False
    db.commit()
    updated = client.get(path + "/graph", headers=cached_headers)
    assert updated.status_code == 200
    assert updated.json()["ranking"]["budget_enabled"] is False
    assert client.get(path + "/ranking-summary", headers=analyst_headers).json() == updated.json()[
        "ranking"
    ]
    stale = client.get(path + "/ranking-summary", headers=analyst_headers,
                       params={"expected_summary_id": "old", "expected_budget_enabled": "true"})
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "RUN_REVISION_CONFLICT"
    summary_id = run.artifact_manifest["ranking_summary"]["artifact_id"]
    assert client.get(path + "/ranking-summary", headers=analyst_headers, params={
        "expected_summary_id": summary_id, "expected_budget_enabled": "false",
    }).status_code == 200
    run.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    assert client.get(path + "/graph", headers=cached_headers).status_code == 410
    assert client.get(path + "/ranking-summary", headers=analyst_headers).status_code == 410
    assert client.get(path + "/metadata", headers=cached_headers).status_code == 410
    run.expires_at = None
    run.deletion_state = "requested"
    db.commit()
    assert client.get(path + "/graph", headers=cached_headers).status_code == 410


def test_ranking_summary_detects_version_changed_during_payload_read(
    client, db, analyst_headers, tmp_path, monkeypatch,
):
    monkeypatch.setattr(settings, "document_analysis_storage_dir", tmp_path / "artifacts")
    run_id = _create(client, analyst_headers, _word_bytes(tmp_path)).json()["recognition_run_id"]
    run = db.get(DocumentAnalysisRun, run_id)
    summary_id = run.artifact_manifest["ranking_summary"]["artifact_id"]
    read = DocumentAnalysisApplication.ranking_summary_response

    def concurrent_read(application, run):
        response = read(application, run)
        # Simulate a committed successor without refreshing the reader's ORM row.
        db.execute(update(DocumentAnalysisRun).where(
            DocumentAnalysisRun.recognition_run_id == run.recognition_run_id,
        ).values(artifact_manifest={
            **run.artifact_manifest, "ranking_summary": {"artifact_id": "successor"},
        }).execution_options(synchronize_session=False))
        db.commit()
        assert run.artifact_manifest["ranking_summary"]["artifact_id"] == summary_id
        return response

    monkeypatch.setattr(DocumentAnalysisApplication, "ranking_summary_response", concurrent_read)
    response = client.get(f"/api/document-analysis/runs/{run_id}/ranking-summary",
                          headers=analyst_headers, params={"expected_summary_id": summary_id})
    assert response.status_code == 409
