"""The template panel creates owned new-kernel runs, never legacy executions."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.api import document_analysis
from app.config import settings
from app.models.document_analysis import DocumentAnalysisArtifact, DocumentAnalysisRun
from app.models.extraction import AnnotationExecution, AstTemplate, ExtractionJob

from .test_document_analysis import ROOT_IRI, _word_bytes


@pytest.fixture
def template_source(db, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "document_analysis_storage_dir", tmp_path / "runs")
    raw = _word_bytes(tmp_path)
    path = tmp_path / "registered.docx"
    path.write_bytes(raw)
    job = ExtractionJob(source_type="docx", source_filename="registered.docx",
                        document_path=str(path), source_config={"doc_class_iri": ROOT_IRI})
    template = AstTemplate(name="模板", version="1.0", iri_pattern=ROOT_IRI,
                           schema_json={"sections": [{"coverage": [{
                               "kind": "ontology_relation", "doc_class_iri": ROOT_IRI,
                               "predicate_iri": "urn:describes",
                               "required_properties": ["urn:formula"],
                           }]}]})
    db.add_all([job, template])
    db.commit()
    dispatched = []
    monkeypatch.setattr(document_analysis, "_wake_dispatcher_or_fallback",
                        lambda _tasks, run_id, **_kw: dispatched.append(run_id))
    return template, job, raw, dispatched


def route(template, job):
    return f"/api/document-analysis/templates/{template.id}/sources/{job.id}/runs"


def test_read_never_starts_and_create_is_immutable_idempotent_and_owner_scoped(
    client, db, analyst_headers, template_source,
):
    template, job, raw, dispatched = template_source
    url = route(template, job)
    before_legacy = db.scalar(select(func.count()).select_from(AnnotationExecution))
    assert client.get(url, headers=analyst_headers).json() == {"run": None}
    assert not dispatched
    created = client.post(url, headers=analyst_headers, json={"request_key": "same"})
    assert created.status_code == 202, created.text
    run_id = created.json()["recognition_run_id"]
    run = db.get(DocumentAnalysisRun, run_id)
    source = db.get(DocumentAnalysisArtifact, run.source_artifact_ref)
    assert source.payload["origin"]["source_job_id"] == str(job.id)
    assert source.payload["origin"]["priority_paths"] == [
        ["urn:describes", "urn:formula"], ["urn:describes"]]
    assert run.document_hash == source.payload["document_hash"]
    replay = client.post(url, headers=analyst_headers, json={"request_key": "same"})
    assert replay.json()["recognition_run_id"] == run_id
    assert replay.json()["idempotent_replay"] is True
    assert len(dispatched) == 1
    latest = client.get(url, headers=analyst_headers).json()["run"]
    assert latest["recognition_run_id"] == run_id
    assert client.get(url, headers={**analyst_headers, "X-User": "someone-else"}).json() == {
        "run": None}
    assert db.scalar(select(func.count()).select_from(AnnotationExecution)) == before_legacy
    # Changing the live template cannot silently reinterpret the existing run.
    template.schema_json = {"sections": []}
    db.commit()
    conflict = client.post(url, headers=analyst_headers, json={"request_key": "same"})
    assert conflict.status_code == 409
    assert source.payload["origin"]["priority_paths"]
    assert job.source_config == {"doc_class_iri": ROOT_IRI}


def test_source_and_role_validation_and_deleted_or_expired_run_filter(
    client, db, analyst_headers, template_source,
):
    template, job, _raw, dispatched = template_source
    url = route(template, job)
    assert client.post(url, headers={**analyst_headers, "X-Role": "operator"},
                       json={"request_key": "denied"}).status_code == 403
    assert client.post(url, headers=analyst_headers, json={
        "request_key": "extra", "priority_paths": [["urn:injected"]],
    }).status_code == 400
    assert not dispatched
    result = client.post(url, headers=analyst_headers, json={"request_key": "allowed"})
    run = db.get(DocumentAnalysisRun, result.json()["recognition_run_id"])
    run.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    assert client.get(url, headers=analyst_headers).json() == {"run": None}
    run.expires_at = None
    run.deletion_state = "requested"
    db.commit()
    assert client.get(url, headers=analyst_headers).json() == {"run": None}
    job.source_config = {"doc_class_iri": "urn:WrongDocument"}
    db.commit()
    response = client.post(url, headers=analyst_headers, json={"request_key": "wrong-type"})
    assert response.status_code == 422
    assert len(dispatched) == 1
