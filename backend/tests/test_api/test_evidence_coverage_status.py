"""Coverage configuration failures must not invalidate successful evidence reviews."""

import uuid

import pytest
from sqlalchemy import func, select

from app.models.evidence import EvidenceJobState
from app.models.extraction import AstTemplate
from app.models.reporting import ContractRevision, ReportRun
from app.services.extraction.extraction_tasks import SEMANTIC_VERSION, ExtractionRun
from app.services.fact_commit import FactCommitService
from app.services.ontology_instance_writer import EvidenceInstanceWriter
from app.services.reporting.contract_registry import ContractRegistry
from app.services.reporting.template_v2 import ReportingError
from tests.test_api.test_evidence_api import evidence_api_job as _evidence_api_job
from tests.test_api.test_evidence_api import manual_request
from tests.test_reporting.test_output_contracts import ontology, template


@pytest.fixture
def evidence_api_job(db, monkeypatch):
    return _evidence_api_job.__wrapped__(db, monkeypatch)


@pytest.fixture
def unresolved_template(db, evidence_api_job):
    schema = template()
    schema["ontology_release_ref"] = "explicit-missing-ontology"
    row = AstTemplate(name="Migrated draft", version="v2.2", schema_json=schema, status="draft")
    db.add(row)
    db.flush()
    evidence_api_job.source_config = {"template_id": str(row.id)}
    db.commit()
    return row


def test_missing_contract_is_explicitly_unavailable_without_inventing_coverage(
    client, db, analyst_headers, evidence_api_job, unresolved_template
):
    response = client.get(
        f"/api/extraction/jobs/{evidence_api_job.id}/evidence/coverage", headers=analyst_headers
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["availability"] == "unavailable"
    assert result["template_id"] == str(unresolved_template.id)
    assert result["manifest_id"] is result["required_gaps"] is None
    assert result["error"]["code"] == "CONTRACT_NOT_FOUND"
    assert result["error"]["actual"] == "explicit-missing-ontology"
    assert "契约不存在" in result["error"]["message"]
    assert "tasks" not in result
    assert db.scalar(select(func.count()).select_from(ContractRevision)) == 0
    assert db.scalar(select(func.count()).select_from(ReportRun)) == 0
    assert client.get(
        f"/api/extraction/jobs/{uuid.uuid4()}/evidence/coverage", headers=analyst_headers
    ).status_code == 404


@pytest.mark.parametrize("operation", ["discovery", "fill-gaps"])
def test_coverage_mutations_preserve_reporting_status_and_details(
    client, analyst_headers, evidence_api_job, unresolved_template, operation
):
    response = client.post(
        f"/api/extraction/jobs/{evidence_api_job.id}/evidence/{operation}",
        headers=analyst_headers,
        json={"manifest_id": "nonexistent", "reason": "Synthetic test"},
    )
    assert response.status_code == 404, response.text
    assert response.json()["detail"] == {
        "code": "CONTRACT_NOT_FOUND", "message": "CONTRACT_NOT_FOUND",
        "actual": "explicit-missing-ontology",
    }


@pytest.mark.parametrize("kind", ["missing", "legacy", "multiple_sources"])
def test_other_configuration_requirements_are_actionable(
    client, db, analyst_headers, evidence_api_job, unresolved_template, kind
):
    if kind == "missing":
        evidence_api_job.source_config = {}
        code, target = "TEMPLATE_NOT_FOUND", None
    elif kind == "legacy":
        unresolved_template.schema_json = {"sections": []}
        code, target = "TEMPLATE_MIGRATION_REQUIRED", str(unresolved_template.id)
    else:
        schema = template()
        schema["source_slots"].append({
            "source_slot_id": "second", "kind": "document", "class_iri": "urn:Report"
        })
        unresolved_template.schema_json = schema
        code, target = "EXPLICIT_SOURCE_BINDINGS_REQUIRED", str(unresolved_template.id)
    db.commit()
    response = client.get(
        f"/api/extraction/jobs/{evidence_api_job.id}/evidence/coverage", headers=analyst_headers
    )
    assert response.status_code == 200, response.text
    assert response.json()["availability"] == "unavailable"
    assert response.json()["error"]["code"] == code
    assert response.json()["template_id"] == target


def test_non_configuration_errors_remain_errors(
    client, analyst_headers, evidence_api_job, unresolved_template, monkeypatch
):
    def missing_snapshot(*args, **kwargs):
        raise ReportingError("SOURCE_SNAPSHOT_NOT_FOUND", status=404, actual="missing-snapshot")

    monkeypatch.setattr(
        "app.services.reporting.coverage_v2.build_report_inputs", missing_snapshot
    )
    response = client.get(
        f"/api/extraction/jobs/{evidence_api_job.id}/evidence/coverage", headers=analyst_headers
    )
    assert response.status_code == 404
    assert response.json()["detail"]["actual"] == "missing-snapshot"


@pytest.mark.parametrize("published", [False, True])
def test_unpublished_contract_and_invalid_template_do_not_start_a_preview(
    client, db, analyst_headers, evidence_api_job, unresolved_template, published
):
    registry = ContractRegistry(db)
    row = registry.create(
        kind="ontology", family_id="synthetic-ontology", revision_no=1,
        definition={"classes": ontology()}, actor="analyst", server_ontology=True,
    )
    if published:
        for decision in ("reviewed", "published"):
            registry.decide(
                row.id, decision, actor="analyst", reason="Synthetic fixture",
                expected_hash=row.content_hash,
            )
    schema = template()
    schema["ontology_release_ref"] = row.id
    schema["definitions"]["bindings"]["equipment"]["contract_ref"]["release_ref"] = row.id
    unresolved_template.schema_json = schema
    db.commit()
    response = client.get(
        f"/api/extraction/jobs/{evidence_api_job.id}/evidence/coverage", headers=analyst_headers
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["availability"] == "unavailable"
    assert result["error"]["code"] == (
        "TEMPLATE_COMPILATION_FAILED" if published else "CONTRACT_NOT_PUBLISHED"
    )
    if published:
        assert result["error"]["diagnostics"]
    else:
        assert registry.load(row.id, published=False)["status"] == "draft"
    assert db.scalar(select(func.count()).select_from(ReportRun)) == 0


def test_review_and_commit_work_without_report_contracts(
    client, db, analyst_headers, evidence_api_job, unresolved_template, real_world,
    tmp_path, monkeypatch,
):
    service = FactCommitService(db, EvidenceInstanceWriter(real_world, tmp_path / "world"))
    monkeypatch.setattr("app.api.evidence._service", lambda *args: service)
    path = f"/api/extraction/jobs/{evidence_api_job.id}/evidence"
    created = client.post(path + "/candidates", headers=analyst_headers, json=manual_request())
    assert created.status_code == 201, created.text
    candidate = created.json()
    response = client.put(
        f"/api/extraction/evidence/candidates/{candidate['candidate_id']}/review",
        headers=analyst_headers,
        json={"expected_revision": 1, "decision": "confirmed", "reason": "Synthetic review"},
    )
    assert response.status_code == 200, response.text
    assert client.get(path + "/coverage", headers=analyst_headers).json()[
        "availability"
    ] == "unavailable"
    reviewed = client.get(path, headers=analyst_headers).json()["candidates"][0]
    assert reviewed["review_status"] == "confirmed"
    assert reviewed["commit_status"] == "not_requested"
    response = client.post(
        path + "/commits", headers=analyst_headers,
        json={"idempotency_key": "synthetic-commit", "items": [{
            "candidate_id": reviewed["candidate_id"], "revision": reviewed["revision"],
        }]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "succeeded", response.text
    assert client.get(path + "/coverage", headers=analyst_headers).json()[
        "required_gaps"
    ] is None
    evidence = client.get(path, headers=analyst_headers).json()
    assert evidence["snapshot_id"] == response.json()["snapshot_id"]
    assert evidence["candidates"][0]["commit_status"] == "succeeded"


@pytest.mark.parametrize("version", ["generic-semantic-v7", SEMANTIC_VERSION])
def test_historical_diagnostics_keep_their_extractor_version(
    client, db, analyst_headers, evidence_api_job, version
):
    run = {
        "completion": "incomplete", "diagnostics": ["ambiguous_source_quote"] * 4,
        "candidates": [{"extractor_version": version}],
    }
    db.add(EvidenceJobState(job_id=evidence_api_job.id, extraction_run=run))
    db.commit()
    response = client.get(
        f"/api/extraction/jobs/{evidence_api_job.id}/evidence", headers=analyst_headers
    ).json()
    assert response["run"] == {key: value for key, value in run.items() if key != "candidates"}
    debug = client.get(
        f"/api/extraction/jobs/{evidence_api_job.id}/evidence?debug=true", headers=analyst_headers
    ).json()
    assert debug["run"] == run
    assert response["extraction_version"] == {
        "current": SEMANTIC_VERSION, "stored": [version], "outdated": version != SEMANTIC_VERSION,
    }


def test_empty_current_run_has_version_without_candidates(
    client, db, analyst_headers, evidence_api_job
):
    run = ExtractionRun(input_id="empty-run", diagnostics=["model_unavailable"])
    db.add(EvidenceJobState(job_id=evidence_api_job.id, extraction_run=run.model_dump(mode="json")))
    db.commit()
    response = client.get(
        f"/api/extraction/jobs/{evidence_api_job.id}/evidence", headers=analyst_headers
    ).json()
    assert response["extraction_version"] == {
        "current": SEMANTIC_VERSION, "stored": [SEMANTIC_VERSION], "outdated": False,
    }
