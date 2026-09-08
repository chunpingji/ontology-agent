"""Calculation decisions and formal publication against isolated PostgreSQL."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.models.extraction import AstTemplate
from app.models.ontology_meta import AppRole, AppUser
from app.models.reporting import CalculationDecision, ReportRun, TemplateCompilation
from app.services.extraction.evidence_identity import evidence_hash
from app.services.reasoning.calculation_review import decide, job_calculations
from app.services.reporting.report_run_service import ReportRunService, frozen_row
from app.services.reporting.report_signing import ReportSigning
from app.services.reporting.template_v2 import ReportingError
from tests.test_api.test_calculation_review import seed
from tests.test_reporting.test_calculation_pipeline import source_case
from tests.test_reporting.test_report_signing import ready_session
from tests.test_reporting.test_reporting_postgresql import db as db


def test_concurrent_decisions_have_one_winner_and_immutable_history(db):
    job = seed(db)
    check = job_calculations(db, job.id)[0]
    job_id, calculation_id = job.id, check["calculation_id"]
    db.commit()
    barrier = Barrier(2)

    def submit(choice):
        with Session(db.bind, expire_on_commit=False) as other:
            barrier.wait(timeout=10)
            try:
                decide(
                    other,
                    job_id,
                    SimpleNamespace(
                        subject_candidate_id="study",
                        calculation_id=calculation_id,
                        expected_revision=0,
                        choice=choice,
                        reason="isolated concurrency test",
                    ),
                    "analyst",
                )
                return "saved"
            except ReportingError as exc:
                other.rollback()
                return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(submit, ["derived", "asserted"]))
    assert sorted(outcomes) == ["CALCULATION_DECISION_CONFLICT", "saved"]
    decisions = db.scalars(select(CalculationDecision)).all()
    assert len(decisions) == 1
    with pytest.raises(DBAPIError, match="immutable"):
        db.execute(
            text("UPDATE calculation_decisions SET payload='{}' WHERE id=:id"),
            {"id": decisions[0].id},
        )
    db.rollback()


def test_pending_calculation_cannot_be_published_as_formal_report(db, tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.reporting.report_run_service.ARTIFACT_ROOT", tmp_path)
    plan, source = source_case()
    source["template_status"] = "published"
    source["source_bundle_id"] = evidence_hash(source)
    template = AstTemplate(
        id=uuid4(), name="isolated PDE report", version="v2", schema_json=plan["template"]
    )
    db.add(template)
    db.flush()
    frozen_row(
        db,
        TemplateCompilation,
        plan["compilation_id"],
        plan,
        "analyst",
        template_id=template.id,
        schema_hash=plan["schema_hash"],
    )
    run = ReportRun(
        id=uuid4().hex,
        actor="analyst",
        idempotency_key=uuid4().hex,
        request_hash=evidence_hash({}),
        template_id=template.id,
        compilation_id=plan["compilation_id"],
        source_bundle=source,
        source_hash=evidence_hash(source),
        purpose="draft",
    )
    db.add(run)
    db.commit()
    ReportRunService(db).execute(run.id)
    assert run.material_status == "conflict"
    qa_role = db.scalar(select(AppRole).where(AppRole.name == "qa"))
    db.add(AppUser(username="qa01", role_id=qa_role.id))
    db.commit()
    _, session, actor = ready_session(db, run)
    with pytest.raises(ReportingError, match="FORMAL_PUBLICATION_BLOCKED"):
        ReportSigning(db).envelope(
            session.id,
            {
                "content_hash": session.content_hash,
                "purpose": "formal",
                "idempotency_key": "formal",
            },
            actor,
        )
