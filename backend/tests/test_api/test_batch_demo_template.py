"""Shared template/report identity, source protection, and explicit specialization."""

from copy import deepcopy
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.models.document_analysis import DocumentAnalysisRun
from app.models.extraction import AnnotationExecution, AstTemplate
from app.models.reasoning import AuditLog
from app.models.reporting import ReportRun
from app.services.reporting import batch_demo
from app.services.reporting.batch_demo_template import specialize
from app.services.reporting.report_run_service import ReportRunService, schema_hash
from app.services.reporting.template_v2 import ReportingError, TemplateV2
from tests.test_api.test_batch_demo import get, post
from tests.test_api.test_batch_demo import source as source


def template_url():
    return f"/api/ast-templates/{batch_demo.TEMPLATE_ID}"


def reset_draft(db):
    row = db.get(AstTemplate, batch_demo.TEMPLATE_ID)
    schema = deepcopy(row.schema_json)
    schema.pop("demo_profile")
    row.schema_json = schema
    row.name = "批记录"
    row.default_source_path = "retained-old-source.docx"
    row.default_source_job_id = None
    row.sample_text = "原模板样例"
    db.commit()
    return row


def test_template_and_report_share_data_and_history(client, db, analyst_headers, source):
    url = f"/api/reports/batch-demo/templates/{batch_demo.TEMPLATE_ID}"
    report_data = get(client, analyst_headers, source).json()
    result = client.get(url, headers=analyst_headers)
    assert result.status_code == 200
    assert result.headers["cache-control"] == "private, no-store"
    assert result.json() == report_data
    detail = client.get(template_url(), headers=analyst_headers).json()
    assert detail["name"] == report_data["template"]["name"]
    assert detail["default_source_job_id"] == report_data["source_job_id"]
    assert detail["demo_profile"]["document_iri"] == report_data["document_iri"]
    assert detail["slot_count"] == 10
    saved = post(client, analyst_headers, source).json()
    assert client.get(url, headers=analyst_headers).json()["latest_report"]["id"] == saved["id"]
    other = {**analyst_headers, "X-User": "another"}
    assert client.get(url, headers=other).json()["latest_report"] is None
    assert client.get(f"/api/reports/batch-demo/templates/{uuid4()}",
                      headers=analyst_headers).status_code == 404
    assert db.scalar(select(func.count()).select_from(DocumentAnalysisRun)) == 0


def test_specialization_preview_cas_audit_and_replay(db, source):
    row = reset_draft(db)
    old_schema = deepcopy(row.schema_json)
    expected = schema_hash(old_schema)
    preview = specialize(db, expected_hash=expected, actor="analyst", document_iri=source[2].iri)
    db.refresh(row)
    assert row.name == "批记录" and row.schema_json == old_schema
    with pytest.raises(HTTPException) as error:
        specialize(db, expected_hash="0" * 64, actor="analyst", document_iri=source[2].iri,
                   apply=True)
    assert error.value.status_code == 409
    source_before = source[4].read_bytes()
    result = specialize(db, expected_hash=expected, actor="analyst", document_iri=source[2].iri,
                        apply=True)
    assert result["after"] == preview["after"]
    assert row.id == batch_demo.TEMPLATE_ID and row.sample_text is None
    records = db.scalars(select(AuditLog).where(AuditLog.action == "template.specialize_demo")
                         .order_by(AuditLog.seq)).all()
    assert records[-1].details["before"]["schema_json"] == old_schema
    assert records[-1].details["before"]["sample_text"] == "原模板样例"
    assert records[-1].details["after"]["default_source_job_id"] == str(source[3].id)
    replay = specialize(db, expected_hash=expected, actor="analyst", document_iri=source[2].iri,
                        apply=True)
    assert not replay["changed"]
    assert db.scalar(select(func.count()).select_from(AuditLog).where(
        AuditLog.action == "template.specialize_demo")) == len(records)
    assert source[4].read_bytes() == source_before


def test_published_and_failed_audit_preserve_original(db, source, monkeypatch):
    row = reset_draft(db)
    expected = schema_hash(row.schema_json)
    row.status = "published"
    db.commit()
    with pytest.raises(HTTPException):
        specialize(db, expected_hash=expected, actor="analyst", document_iri=source[2].iri,
                   apply=True)
    row.status = "draft"
    db.commit()

    def fail(*args, **kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr("app.services.reporting.batch_demo_template.audit.append", fail)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        specialize(db, expected_hash=expected, actor="analyst", document_iri=source[2].iri,
                   apply=True)
    db.refresh(row)
    assert row.name == "批记录" and "demo_profile" not in row.schema_json


@pytest.mark.parametrize("changed", ["profile", "source", "calculation"])
def test_registry_mismatch_blocks_both_entry_points(client, db, analyst_headers, source, changed):
    row = db.get(AstTemplate, batch_demo.TEMPLATE_ID)
    schema = deepcopy(row.schema_json)
    if changed == "profile":
        schema["demo_profile"]["fixture_id"] = "invalid"
    elif changed == "source":
        row.default_source_job_id = None
    else:
        schema["calculation_checks"] = [{"check_id": "risk", "source_slot": "cmc",
                                          "contract_ref": "urn:old:pde"}]
    row.schema_json = schema
    db.commit()
    assert get(client, analyst_headers, source).status_code == 409
    assert client.get(f"/api/reports/batch-demo/templates/{row.id}",
                      headers=analyst_headers).status_code == 409


def test_generic_paths_cannot_modify_source_or_start_models(client, db, analyst_headers, source):
    before = source[4].read_bytes()
    for method, suffix, kwargs in [
        ("delete", "", {}), ("delete", "/default-source", {}),
        ("patch", "", {"json": {"name": "changed"}}),
        ("post", "/default-source", {"files": {"file": ("changed.docx", b"bad")}}),
        ("post", "/compile", {"json": {"expected_hash": "0" * 64}}),
    ]:
        result = getattr(client, method)(template_url() + suffix, headers=analyst_headers, **kwargs)
        assert result.status_code == 409, result.text
    created = client.post(
        f"/api/document-analysis/templates/{batch_demo.TEMPLATE_ID}/sources/{source[3].id}/runs",
        headers=analyst_headers, json={"request_key": "must-not-run"},
    )
    assert created.status_code == 409, created.text
    assert created.json()["error"]["code"] == "RUN_STATE_CONFLICT"
    assert "静态图谱" in created.json()["error"]["message"]
    source[3].source_config = {**source[3].source_config, "mode": "template_default",
                                "recognition_template_id": str(batch_demo.TEMPLATE_ID)}
    db.commit()
    resumed = client.post(f"/api/extraction/jobs/{source[3].id}/annotation/resume",
                          headers=analyst_headers)
    assert resumed.status_code == 409 and "静态图谱" in resumed.text
    with pytest.raises(ReportingError) as error:
        ReportRunService(db).start({"idempotency_key": "no-normal-run",
                                    "template_id": str(batch_demo.TEMPLATE_ID)}, "analyst")
    assert error.value.code == "STATIC_DEMO_TEMPLATE"
    ordinary_schema = deepcopy(db.get(AstTemplate, batch_demo.TEMPLATE_ID).schema_json)
    ordinary_schema.pop("demo_profile")
    with pytest.raises(ReportingError) as error:
        ReportRunService(db).start({"idempotency_key": "no-preview-bypass",
                                    "template_id": str(batch_demo.TEMPLATE_ID),
                                    "draft_schema": ordinary_schema}, "analyst", preview=True)
    assert error.value.code == "STATIC_DEMO_TEMPLATE"
    preview = client.post("/api/report-previews", headers=analyst_headers, json={
        "mode": "layout", "template_id": str(batch_demo.TEMPLATE_ID),
        "draft_schema": ordinary_schema, "idempotency_key": "no-layout-bypass",
    })
    assert preview.status_code == 409, preview.text
    assert source[4].read_bytes() == before
    for model in [AnnotationExecution, DocumentAnalysisRun, ReportRun]:
        assert db.scalar(select(func.count()).select_from(model)) == 0


def test_ordinary_template_serialization_keeps_previous_hash():
    # The optional profile must not alter canonical bytes of existing V2 schemas.
    schema = TemplateV2(schema_version=2, template_family_id="family",
                        template_revision_id="revision", revision_no=1).model_dump(mode="json")
    assert "demo_profile" not in schema
    assert schema_hash({**schema, "demo_profile": None}) == schema_hash(schema)
