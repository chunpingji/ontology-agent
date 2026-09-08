from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import select

from app.models.evidence import EvidenceCandidateRecord, EvidenceJobState
from app.models.extraction import AstTemplate, ExtractionJob
from app.models.reporting import CalculationDecision, ReportInputSnapshot
from app.services.reasoning.calculation_review import decide, job_calculations
from app.services.reporting.coverage_v2 import build_report_inputs
from app.services.reporting.report_run_service import verify_frozen
from tests.test_reasoning.test_pde_calculation_v1 import candidates
from tests.test_reporting.test_calculation_pipeline import source_case


def seed(db, **kwargs):
    job = ExtractionJob(id=uuid4(), source_type="word", status="completed", source_config={})
    db.add(job)
    db.flush()
    db.add(EvidenceJobState(job_id=job.id, revision=0, extraction_run={"completion": "complete"}))
    for c in candidates(**kwargs):
        db.add(
            EvidenceCandidateRecord(
                id=c.candidate_id,
                job_id=job.id,
                source_key=c.candidate_id,
                revision=c.revision,
                kind=c.kind,
                review_status=c.review_status,
                payload=c.model_dump(mode="json"),
            )
        )
    db.commit()
    return job


def test_exact_calculation_cas_reason_audit_and_stale_inputs(client, db, analyst_headers):
    job = seed(db)
    result = job_calculations(db, job.id)[0]
    url = f"/api/extraction/jobs/{job.id}/calculations/decision"
    payload = {
        "subject_candidate_id": result["subject_candidate_id"],
        "calculation_id": result["calculation_id"],
        "expected_revision": 0,
        "choice": "derived",
        "reason": "已核对 NOAEL 与计算因子",
    }
    assert (
        client.post(url, headers=analyst_headers, json={**payload, "reason": "  "}).status_code
        == 422
    )
    assert client.post(url, json=payload).status_code == 403
    saved = client.post(url, headers=analyst_headers, json=payload)
    assert saved.status_code == 200, saved.text
    assert saved.json()["actor"] == "analyst"
    assert job_calculations(db, job.id)[0]["effective_pde_mg_day"] == "0.05"
    assert client.post(url, headers=analyst_headers, json=payload).status_code == 409
    rejected = client.post(
        url,
        headers=analyst_headers,
        json={**payload, "expected_revision": 1, "choice": "rejected", "reason": "对 F3 有异议"},
    )
    assert rejected.status_code == 200, rejected.text
    assert job_calculations(db, job.id)[0]["blocks_conclusion"]
    assert len(db.scalars(select(CalculationDecision)).all()) == 2
    row = db.get(EvidenceCandidateRecord, "noaelDuration")
    row.revision = 2
    row.payload = {**row.payload, "revision": 2}
    db.commit()
    assert job_calculations(db, job.id)[0]["stale_decision"]
    assert (
        client.post(
            url, headers=analyst_headers, json={**payload, "expected_revision": 2}
        ).status_code
        == 409
    )
    assert job_calculations(db, job.id)[0]["decision"] is None


def test_cannot_adopt_incomplete_calculation_and_reject_passed_is_respected(
    client, db, analyst_headers
):
    job = seed(db, pde="0.05", noaelDuration=None)
    result = job_calculations(db, job.id)[0]
    response = client.post(
        f"/api/extraction/jobs/{job.id}/calculations/decision",
        headers=analyst_headers,
        json={
            "subject_candidate_id": result["subject_candidate_id"],
            "calculation_id": result["calculation_id"],
            "expected_revision": 0,
            "choice": "asserted",
            "reason": "测试",
        },
    )
    assert response.status_code == 422
    assert not db.scalars(select(CalculationDecision)).all()


def test_decision_refreshes_coverage_and_frozen_prior_report_stays_unchanged(
    db, monkeypatch, tmp_path
):
    job = seed(db)
    plan, source = source_case()
    template = AstTemplate(
        name="Synthetic PDE coverage", version="v2", schema_json=plan["template"], status="draft"
    )
    db.add(template)
    db.flush()
    job.source_config = {"template_id": str(template.id)}
    db.commit()
    monkeypatch.setattr("app.services.reporting.report_run_service.ARTIFACT_ROOT", tmp_path)
    monkeypatch.setattr(
        "app.services.reporting.report_run_service.ReportRunService.load_contracts",
        lambda *args: (source["schema"], source["contracts"]),
    )
    monkeypatch.setattr(
        "app.services.fact_commit.FactCommitService.published_snapshot",
        lambda *args: deepcopy(source["sources"]["doc"]["snapshot"]),
    )

    def coverage():
        return build_report_inputs(db, job, schema=source["schema"], actor="analyst")

    original = coverage()
    assert original["required_gaps"] > 0
    assert any(t["reason"] == "CALCULATION_REVIEW_REQUIRED" for t in original["tasks"])
    result = job_calculations(db, job.id)[0]
    payload = dict(
        subject_candidate_id="study",
        calculation_id=result["calculation_id"],
        expected_revision=0,
        choice="derived",
        reason="已核对全部输入证据",
    )
    decide(db, job.id, SimpleNamespace(**payload), "analyst")
    accepted = coverage()
    assert accepted["manifest_id"] != original["manifest_id"]
    assert accepted["required_gaps"] == 0 and accepted["completion"] == "complete"
    assert coverage()["manifest_id"] == accepted["manifest_id"]
    decide(
        db,
        job.id,
        SimpleNamespace(
            **{
                **payload,
                "expected_revision": 1,
                "choice": "rejected",
                "reason": "对修正因子有异议",
            }
        ),
        "analyst",
    )
    rejected = coverage()
    assert rejected["manifest_id"] != accepted["manifest_id"]
    assert rejected["required_gaps"] > 0 and rejected["completion"] == "incomplete"
    frozen = verify_frozen(db.get(ReportInputSnapshot, accepted["manifest_id"]))
    assert frozen["calculation_checks"]["pde:doc"]["rows"][0]["effective_pde_mg_day"] == "0.05"
