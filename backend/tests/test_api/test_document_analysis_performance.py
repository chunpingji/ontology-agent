"""Display artifacts remain equivalent, read-only and private before cache hits."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import update

from app.config import settings
from app.models.document_analysis import DocumentAnalysisRun
from app.services.document_analysis.application import DocumentAnalysisApplication
from app.services.document_analysis.public_projection import PUBLIC_TO_INTERNAL_PROJECTION

from .test_document_analysis import _create, _word_bytes


def test_repair_creation_freezes_required_policies_without_upgrading_old_runs(
    client, db, analyst_headers, tmp_path, monkeypatch,
):
    monkeypatch.setattr(settings, "document_analysis_storage_dir", tmp_path / "artifacts")
    monkeypatch.setattr(settings, "document_analysis_performance_enabled", False)
    monkeypatch.setattr(settings, "document_analysis_template_interleaving", False)
    monkeypatch.setattr(settings, "document_analysis_evidence_repair_enabled", False)
    original = _word_bytes(tmp_path)
    old = _create(client, analyst_headers, original, request_key="before-repair")
    application = DocumentAnalysisApplication(db, ontology_engine=object())
    old_run = application.get_run(old.json()["recognition_run_id"], "analyst")
    assert application._artifact_payload(old_run, "source")[0]["performance_policy"] == {}
    monkeypatch.setattr(settings, "document_analysis_evidence_repair_enabled", True)
    created = _create(client, analyst_headers, original, request_key="with-repair")
    run = application.get_run(created.json()["recognition_run_id"], "analyst")
    frozen = application._artifact_payload(run, "source")[0]["performance_policy"]
    assert frozen["evidence_repair"] == "evidence-repair-v1"
    assert frozen["scope_protocol"] == "source-quoted-scope-v1"
    assert frozen["evidence_work"] == "evidence-work-v2"
    assert frozen["literal_quotes"] == "source-integer-quotes-v2"
    assert frozen["state_storage_version"] == 3 and frozen["max_lineage_calls"] == 8
    assert frozen["incremental_performance"] == "incremental-performance-v1"
    assert frozen["heuristic_policy"] == "heuristic-first-v3"
    assert frozen["semantic_expansion"] == "bounded-semantic-v1"
    assert frozen["process_granularity"] == "whole-method-field-v1"
    assert frozen["attribute_priority"] == "source-field-priority-v1"
    assert frozen["template_interleaving"] is True
    assert application._artifact_payload(old_run, "source")[0]["performance_policy"] == {}


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
