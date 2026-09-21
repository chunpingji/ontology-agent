"""Display artifacts remain equivalent, read-only and private before cache hits."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update

from app.api import document_analysis
from app.config import settings
from app.models.document_analysis import DocumentAnalysisRun
from app.services.document_analysis.application import DocumentAnalysisApplication

from .test_document_analysis import _create, _word_bytes


@pytest.mark.parametrize("output_ceiling", [4096, 20480])
def test_document_creation_freezes_current_tool_engine_performance_policy(
    client, db, analyst_headers, tmp_path, monkeypatch, output_ceiling,
):
    monkeypatch.setattr(settings, "document_analysis_storage_dir", tmp_path / "artifacts")
    monkeypatch.setattr(settings, "evidence_max_output_tokens", output_ceiling)
    monkeypatch.setattr(settings, "document_analysis_performance_enabled", False)
    monkeypatch.setattr(document_analysis, "dispatch_run", lambda *_args, **_kwargs: None)
    created = _create(client, analyst_headers, _word_bytes(tmp_path), request_key="current-policy")
    application = DocumentAnalysisApplication(db, ontology_engine=object())
    run = application.get_run(created.json()["recognition_run_id"], "analyst")
    frozen = application._artifact_payload(run, "source")[0]["performance_policy"]
    assert frozen["extraction_protocol"] == "ontology-tool-extraction-v1"
    assert frozen["state_storage_version"] == 4 and frozen["max_lineage_calls"] == 6
    assert frozen["responses"]["reasoning"] == {"effort": "none"}
    maximum = frozen["request_budget"]["max_output_tokens"]
    assert frozen["request_budget"]["stage_output_tokens"] == {
        "discovery": min(8192, maximum), "verification": min(16384, maximum),
    }
    assert frozen["record_discovery"]["max_feedback_reopens"] == 1
    routing = frozen["record_discovery"]["schema_region_routing"]
    assert routing["max_group_chars"] == 1200 and routing["max_group_records"] == 8
    assert routing["execution_mode"] == routing["property_field_mode"] == "region_batch"

    monkeypatch.setattr(settings, "evidence_max_output_tokens", 1024)
    assert application._artifact_payload(run, "source")[0]["performance_policy"] == frozen


def test_graph_cache_checks_projection_budget_owner_expiry_and_deletion(
    client, db, analyst_headers, tmp_path, monkeypatch,
):
    monkeypatch.setattr(settings, "document_analysis_storage_dir", tmp_path / "artifacts")
    monkeypatch.setattr(document_analysis, "dispatch_run", lambda *_args, **_kwargs: None)
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
    summary_id = ""
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
    monkeypatch.setattr(document_analysis, "dispatch_run", lambda *_args, **_kwargs: None)
    run_id = _create(client, analyst_headers, _word_bytes(tmp_path)).json()["recognition_run_id"]
    summary_id = ""
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
        assert "ranking_summary" not in run.artifact_manifest
        return response

    monkeypatch.setattr(DocumentAnalysisApplication, "ranking_summary_response", concurrent_read)
    response = client.get(f"/api/document-analysis/runs/{run_id}/ranking-summary",
                          headers=analyst_headers, params={"expected_summary_id": summary_id})
    assert response.status_code == 409
