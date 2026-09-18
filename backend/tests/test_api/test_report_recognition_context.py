"""The designated HRS report offers only the latest risk-template revision."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.models.document_analysis import DocumentAnalysisArtifact, DocumentAnalysisRun
from app.models.entity_shadow import EntityShadow
from app.models.extraction import AnnotationExecution, AstTemplate, ExtractionJob

ROOT = "https://ontology.pharma-gmp.cn/slpra/drug-development/CMCReport"
FILENAME = "HRS-1597原料临床备样生产信息表_--脱敏-原 - PDE-冲突.docx"
TEMPLATE_NAME = "风险评估文档V1版"
ENDPOINT = "/api/ast-templates/recognition-context"


@pytest.fixture
def report_context(db):
    job = ExtractionJob(
        id=uuid4(),
        source_type="docx",
        source_filename=FILENAME,
        source_config={"doc_class_iri": ROOT},
    )
    document = EntityShadow(
        iri="urn:report-context:hrs1597",
        class_iri=ROOT,
        module="document",
        properties_json={"source_job_id": str(job.id)},
    )
    created = datetime(2026, 9, 1, tzinfo=UTC)
    templates = []
    for name, version, age in (
        (TEMPLATE_NAME, "v1.9", 0),
        (TEMPLATE_NAME, "v1.10", 1),
        ("其他风险评估模板", "v3.0", 2),
    ):
        templates.append(
            AstTemplate(
                id=uuid4(),
                name=name,
                version=version,
                iri_pattern=ROOT,
                schema_json={"schema_version": 1, "sections": []},
                recognition_mode="ontology_guided",
                created_at=created + timedelta(days=age),
            )
        )
    old, latest, other = templates
    old.default_source_job_id = job.id
    job.source_config = {**job.source_config, "template_id": str(old.id)}
    db.add_all([job, document, *templates])
    db.commit()
    return document, job, old, latest, other


@pytest.mark.parametrize("requested", [None, "old", "other"])
def test_designated_report_selects_latest_risk_revision_without_starting_work(
    client, db, analyst_headers, report_context, requested
):
    document, job, old, latest, other = report_context
    params = {"document_iri": document.iri}
    if requested:
        params["template_id"] = str({"old": old, "other": other}[requested].id)
    models = (
        ExtractionJob,
        AstTemplate,
        AnnotationExecution,
        DocumentAnalysisRun,
        DocumentAnalysisArtifact,
    )
    before = {model: db.scalar(select(func.count()).select_from(model)) for model in models}
    source_config = dict(job.source_config)

    response = client.get(ENDPOINT, params=params, headers=analyst_headers)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["document_iri"] == document.iri
    assert payload["source_job_id"] == str(job.id)
    assert payload["selection_locked"] is True
    assert payload["selection_required"] is False
    assert [item["template_id"] for item in payload["templates"]] == [str(latest.id)]
    assert payload["selected"] == payload["templates"][0]
    assert payload["selected"]["name"] == TEMPLATE_NAME
    assert payload["selected"]["version"] == "v1.10"
    assert payload["selected"]["schema_version"] == 1
    assert payload["selected"]["recognition_mode"] == "ontology_guided"
    assert {model: db.scalar(select(func.count()).select_from(model)) for model in models} == before
    db.refresh(job)
    assert job.source_config == source_config


@pytest.mark.parametrize("explicit", [False, True])
def test_similarly_named_report_keeps_its_existing_template_choices(
    client, db, analyst_headers, report_context, explicit
):
    document, job, old, latest, other = report_context
    job.source_filename = FILENAME.replace("1597", "1598")
    db.commit()
    params = {"document_iri": document.iri}
    if explicit:
        params["template_id"] = str(other.id)

    response = client.get(ENDPOINT, params=params, headers=analyst_headers)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["selection_locked"] is False
    assert payload["selection_required"] is False
    assert {item["template_id"] for item in payload["templates"]} == {
        str(old.id), str(latest.id), str(other.id)
    }
    assert payload["selected"]["template_id"] == str(other.id if explicit else old.id)
    assert {item["version"] for item in payload["templates"]} == {"v1.9", "v1.10", "v3.0"}


@pytest.mark.parametrize("missing_reason", ["different_name", "different_root"])
def test_designated_report_does_not_fall_back_when_target_template_is_unavailable(
    client, db, analyst_headers, report_context, missing_reason
):
    document, job, old, latest, other = report_context
    for template in (old, latest):
        if missing_reason == "different_name":
            template.name = "其他风险评估版本"
        else:
            template.iri_pattern = "urn:another-document-class"
    db.commit()

    response = client.get(
        ENDPOINT,
        params={"document_iri": document.iri, "template_id": str(other.id)},
        headers=analyst_headers,
    )

    assert response.status_code == 404, response.text
    assert response.json()["detail"]["code"] == "TEMPLATE_NOT_FOUND"
    assert db.scalar(select(func.count()).select_from(ExtractionJob)) == 1
    assert db.scalar(select(func.count()).select_from(AnnotationExecution)) == 0
    assert db.scalar(select(func.count()).select_from(DocumentAnalysisRun)) == 0
    db.refresh(job)
    assert job.source_config["template_id"] == str(old.id)
