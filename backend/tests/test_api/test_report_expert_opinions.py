"""Expert opinions preserve provenance without starting models or accepting facts."""

from copy import deepcopy
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.models.document_analysis import (
    DocumentAnalysisArtifact,
    DocumentAnalysisRun,
    DocumentRunArtifact,
)
from app.models.extraction import GeneratedReport
from app.models.reasoning import AuditLog
from app.models.report_expert_opinion import ReportExpertOpinion
from app.services import audit

from .test_report_document_runs import _post
from .test_report_document_runs import report_source as source_fixture

URL = "/api/report-center/expert-opinions"


@pytest.fixture
def report_source(db, tmp_path, monkeypatch):
    return source_fixture.__wrapped__(db, tmp_path, monkeypatch)


def listing(client, headers, document, **params):
    return client.get(URL, headers=headers, params={"document_iri": document.iri, **params})


def body(context, **changes):
    return {"request_key": str(uuid4()), "context": context, "category": "relationship",
            "opinion": "关系对象挂接有误，参见设备表", "location": "设备表第 2 行",
            "suggestion": "核对设备与产品关系", **changes}


def count(db, model):
    return db.scalar(select(func.count()).select_from(model))


def test_save_read_export_idempotency_and_identity(
    client, db, analyst_headers, qa_headers, operator_headers, report_source, monkeypatch,
):
    document, _, path, dispatched = report_source

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Expert feedback must never start a model")

    monkeypatch.setattr("app.services.llm.local_client.get_local_llm", forbidden)
    read = listing(client, analyst_headers, document)
    assert read.status_code == 200, read.text
    assert read.headers["cache-control"] == "private, no-store"
    assert read.json()["can_submit"] and read.json()["items"] == []
    assert count(db, ReportExpertOpinion) == count(db, DocumentAnalysisRun) == 0
    original = path.read_bytes()
    request = body(read.json()["context"])
    created = client.post(URL, headers=analyst_headers, json=request)
    assert created.status_code == 201, created.text
    saved = created.json()
    assert saved["author"] == "analyst" and saved["author_role"] == "senior_analyst"
    assert saved["revision"] == 1 and saved["context"] == request["context"]
    assert client.post(URL, headers=analyst_headers, json=request).json() == saved
    assert count(db, ReportExpertOpinion) == count(db, AuditLog) == 1
    assert audit.verify(db)["ok"]
    conflict = client.post(URL, headers=analyst_headers, json={**request, "opinion": "其他内容"})
    assert conflict.status_code == 409
    assert listing(client, analyst_headers, document).json()["items"] == [saved]
    exported = client.get(URL + "/export", headers=analyst_headers,
                          params={"document_iri": document.iri})
    assert exported.json()["opinions"] == [saved]
    assert exported.json()["calibration_approved"] is False
    assert exported.headers["cache-control"] == "private, no-store"
    assert listing(client, qa_headers, document).json()["items"] == []
    assert client.get(URL + "/export", headers=qa_headers,
                      params={"document_iri": document.iri}).json()["opinions"] == []
    assert client.post(URL, headers=qa_headers, json=request).status_code == 201
    assert not listing(client, operator_headers, document).json()["can_submit"]
    assert client.post(URL, headers=operator_headers, json=request).status_code == 403
    assert count(db, ReportExpertOpinion) == 2
    assert count(db, DocumentAnalysisRun) == 0 and not dispatched
    assert path.read_bytes() == original
    paged = listing(client, analyst_headers, document, offset=1, limit=1).json()
    assert paged["total"] == 1 and paged["items"] == []


@pytest.mark.parametrize("field,value", [
    ("source_hash", "a" * 64), ("filename", "伪造.docx"), ("source_version", "999"),
    ("graph_content_hash", "b" * 64),
])
def test_forged_context_cannot_be_saved(client, db, analyst_headers, report_source, field, value):
    document, *_ = report_source
    context = listing(client, analyst_headers, document).json()["context"]
    context[field] = value
    assert client.post(URL, headers=analyst_headers, json=body(context)).status_code == 409
    assert count(db, ReportExpertOpinion) == 0


def test_changed_original_and_invalid_input(client, db, analyst_headers, report_source):
    document, job, path, _ = report_source
    context = listing(client, analyst_headers, document).json()["context"]
    # The shared document API rejects malformed bodies as 400.
    for changes in ({"opinion": "  "}, {"author": "forged"}, {"category": "unknown"}):
        invalid = client.post(URL, headers=analyst_headers, json=body(context, **changes))
        assert invalid.status_code == 400
    assert client.get(URL, headers=analyst_headers).status_code == 422
    assert listing(client, analyst_headers, document, job_id=str(job.id)).status_code == 422
    path.write_bytes(path.read_bytes() + b"changed")
    assert client.post(URL, headers=analyst_headers, json=body(context)).status_code == 409
    assert count(db, ReportExpertOpinion) == 0
    # Non-Word original documents also support feedback.
    job.source_type = "excel"
    db.commit()
    assert listing(client, analyst_headers, document).status_code == 200


def test_owned_run_and_historical_graph_provenance(
    client, db, analyst_headers, qa_headers, report_source,
):
    document, _, path, _ = report_source
    run_id = _post(client, analyst_headers, document, request_key="graph-review").json()[
        "recognition_run_id"]
    run = db.get(DocumentAnalysisRun, run_id)
    for revision in (1, 2):
        artifact = DocumentAnalysisArtifact(artifact_id=f"review-graph-{revision}",
            artifact_kind="graph", content_hash=str(revision) * 64, payload={})
        db.add(artifact)
        db.flush()
        db.add(DocumentRunArtifact(recognition_run_id=run.recognition_run_id,
            artifact_kind="graph", revision=revision, artifact_id=artifact.artifact_id,
            content_hash=artifact.content_hash, status="partial"))
    run.artifact_manifest = {"graph": {"artifact_id": "review-graph-1"}}
    db.commit()
    context = listing(client, analyst_headers, document,
                      recognition_run_id=run_id).json()["context"]
    assert context["graph_artifact_id"] == "review-graph-1"
    run.artifact_manifest = {"graph": {"artifact_id": "review-graph-2"}}
    db.commit()
    request = body(context)
    saved = client.post(URL, headers=analyst_headers, json=request)
    assert saved.status_code == 201, saved.text
    assert saved.json()["context"]["graph_content_hash"] == "1" * 64
    assert listing(client, qa_headers, document, recognition_run_id=run_id).status_code == 404
    assert client.post(URL, headers=qa_headers, json=request).status_code == 404
    forged = deepcopy(context)
    forged["graph_artifact_id"] = "not-in-this-run"
    assert client.post(URL, headers=analyst_headers, json=body(forged)).status_code == 404
    source = db.get(DocumentAnalysisArtifact, run.source_artifact_ref)
    source.payload = {**source.payload, "origin": {"document_iri": "urn:another-document"}}
    db.commit()
    assert listing(client, analyst_headers, document, recognition_run_id=run_id).status_code == 409
    source.payload = {**source.payload, "origin": {"document_iri": document.iri}}
    db.commit()
    path.write_bytes(b"a different document")
    assert listing(client, analyst_headers, document, recognition_run_id=run_id).status_code == 409


def test_generated_report_target_and_access(client, db, analyst_headers, qa_headers, report_source):
    _, job, path, _ = report_source
    report = GeneratedReport(job_id=job.id, file_path=str(path), report_type="batch_record_demo",
                             actor="analyst", report_status="completed")
    db.add(report)
    db.commit()
    target = {"job_id": str(job.id), "report_id": str(report.id)}
    read = client.get(URL, headers=analyst_headers, params=target)
    assert read.status_code == 200, read.text
    request = body(read.json()["context"])
    assert client.post(URL, headers=analyst_headers, json=request).status_code == 201
    assert client.get(URL, headers=qa_headers, params=target).status_code == 404
    assert client.get(URL, headers=analyst_headers,
                      params={**target, "job_id": str(uuid4())}).status_code == 404
    report.report_status = "running"
    db.commit()
    assert client.get(URL, headers=analyst_headers, params=target).status_code == 409
    report.report_status = "completed"
    report.deleted_at = datetime.now(UTC)
    db.commit()
    assert client.get(URL, headers=analyst_headers, params=target).status_code == 404


def test_audit_failure_rolls_back_opinion(client, db, analyst_headers, report_source, monkeypatch):
    document, *_ = report_source
    context = listing(client, analyst_headers, document).json()["context"]

    def unavailable(*_args, **_kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(audit, "append", unavailable)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        client.post(URL, headers=analyst_headers, json=body(context))
    assert count(db, ReportExpertOpinion) == 0
