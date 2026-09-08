"""Synthetic account and evidence fixtures exercise immutable signing transactions."""

from copy import deepcopy
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.auth import hash_password
from app.dependencies import Identity
from app.models.extraction import AstTemplate
from app.models.ontology_meta import AppRole, AppUser
from app.models.reporting import (
    ReportRequest,
    ReportRun,
    ReportSignature,
    ReportSigningEnvelope,
    TemplateCompilation,
)
from app.services import audit
from app.services.extraction.evidence_identity import evidence_hash
from app.services.reporting.report_run_service import ReportRunService, frozen_row
from app.services.reporting.report_signing import ReportSigning
from app.services.reporting.template_compiler import require_valid
from app.services.reporting.template_v2 import ReportingError
from tests.test_reporting.test_fact_selector import property_value
from tests.test_reporting.test_output_contracts import compile_example, template
from tests.test_reporting.test_output_resolution import bundle


@pytest.fixture
def reporting_run(db, tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.reporting.report_run_service.ARTIFACT_ROOT", tmp_path)
    value = template()
    plan = require_valid(compile_example(value))
    source = bundle(property_value("code", "E", "urn:code", "E-01"))
    source.update(template=plan["template"], template_status="published")
    source["contracts"]["policy-1"]["definition"].update(
        review_roles=["qa"],
        signature_slots=[
            {
                "signature_slot_id": "qa",
                "region_id": "approval",
                "role": "qa",
                "allowed_signers": ["qa01"],
                "meanings": ["审核确认"],
                "required": True,
            }
        ],
    )
    source["source_bundle_id"] = evidence_hash(source)
    template_row = AstTemplate(id=uuid4(), name="synthetic", version="v2", schema_json=value)
    db.add(template_row)
    db.flush()
    frozen_row(
        db,
        TemplateCompilation,
        plan["compilation_id"],
        plan,
        "analyst",
        template_id=template_row.id,
        schema_hash=plan["schema_hash"],
    )
    run = ReportRun(
        id=uuid4().hex,
        actor="analyst",
        idempotency_key=uuid4().hex,
        request_hash=evidence_hash({}),
        template_id=template_row.id,
        compilation_id=plan["compilation_id"],
        source_bundle=source,
        source_hash=evidence_hash(source),
        purpose="draft",
    )
    db.add(run)
    db.commit()
    role = db.scalar(select(AppRole).where(AppRole.name == "qa"))
    db.add(
        AppUser(username="qa01", role_id=role.id, password_hash=hash_password("fixture-password"))
    )
    db.commit()
    ReportRunService(db).execute(run.id)
    return run


def ready_session(db, run):
    service = ReportSigning(db)
    actor, qa = Identity("analyst", "senior_analyst"), Identity("qa01", "qa")
    body = service.runs.body(run.id)
    content = service.content(
        run.id,
        {
            "expected_body_hash": body.payload["body_hash"],
            "attempt": 1,
            "idempotency_key": "content",
        },
        actor,
    )
    review = service.review(
        content.id,
        {
            "expected_content_hash": content.content_hash,
            "decision": "approved",
            "reason": "Synthetic test review",
            "idempotency_key": "review",
        },
        qa,
    )
    session = service.session(
        content.id,
        {
            "expected_content_hash": content.content_hash,
            "review_id": review.id,
            "idempotency_key": "session",
        },
        actor,
    )
    db.commit()
    return service, session, qa


def signature_payload(session, **overrides):
    return {
        "content_hash": session.content_hash,
        "signature_slot_id": "qa",
        "meaning": "审核确认",
        "expected_signature_revision": 0,
        "password": "fixture-password",
        "idempotency_key": "sign",
        **overrides,
    }


def test_reauthentication_cas_and_idempotency(db, reporting_run):
    service, session, qa = ready_session(db, reporting_run)
    with pytest.raises(ReportingError, match="REAUTHENTICATION_FAILED"):
        service.sign(session.id, signature_payload(session, password="wrong"), qa)
    assert service.get_session(session.id).revision_no == 0
    original = deepcopy(service.runs.body(reporting_run.id).payload)
    signed = service.sign(session.id, signature_payload(session), qa)
    db.commit()
    assert service.sign(session.id, signature_payload(session), qa).id == signed.id
    with pytest.raises(ReportingError, match="SIGNATURE_SLOT_OCCUPIED"):
        service.sign(session.id, signature_payload(session, idempotency_key="another"), qa)
    assert service.runs.body(reporting_run.id).payload == original
    receipts = list(db.scalars(select(ReportRequest)))
    assert all("fixture-password" not in str(r.payload) for r in receipts)
    assert audit.verify(db)["ok"]


def test_frozen_envelope_preserves_body_and_replays_artifact(db, reporting_run):
    service, session, qa = ready_session(db, reporting_run)
    body = deepcopy(service.runs.body(reporting_run.id).payload)
    service.sign(session.id, signature_payload(session), qa)
    db.commit()
    payload = {
        "content_hash": session.content_hash,
        "signature_revision": 1,
        "purpose": "formal",
        "idempotency_key": "envelope",
    }
    envelope = service.envelope(session.id, payload, qa)
    db.commit()
    artifact = service.envelope_artifact(envelope)
    saved_bytes = service.runs.download(reporting_run.id, artifact.id).file_hash
    assert envelope.payload["final_ast"]["children"][0] == body["body_ast"]
    assert service.runs.body(reporting_run.id).payload == body
    assert service.envelope(session.id, payload, qa).id == envelope.id
    assert service.envelope_artifact(envelope).file_hash == saved_bytes
    with pytest.raises(ReportingError, match="SIGNATURE_REVISION_CONFLICT"):
        service.revoke(
            session.id,
            {
                "signature_ref": db.scalar(select(ReportSignature.id)),
                "expected_signature_revision": 2,
                "password": "fixture-password",
                "reason": "revoke",
                "idempotency_key": "event",
            },
            qa,
        )
    assert len(list(db.scalars(select(ReportSigningEnvelope)))) == 1


def test_signature_cannot_use_spoofed_or_inactive_account(db, reporting_run):
    service, session, qa = ready_session(db, reporting_run)
    with pytest.raises(ReportingError, match="SIGNER_IDENTITY_INVALID"):
        service.sign(session.id, signature_payload(session), Identity("qa01", "senior_analyst"))
    user = db.scalar(select(AppUser).where(AppUser.username == "qa01"))
    user.is_active = False
    db.commit()
    with pytest.raises(ReportingError, match="SIGNER_IDENTITY_INVALID"):
        service.sign(session.id, signature_payload(session), qa)


def test_frozen_body_cannot_be_updated(db, reporting_run):
    body = ReportRunService(db).body(reporting_run.id)
    body.payload = {**body.payload, "body_hash": "tampered"}
    with pytest.raises(ValueError, match="immutable"):
        db.flush()
    db.rollback()


def test_retry_reuses_snapshot_and_completed_outputs(db, reporting_run):
    service = ReportRunService(db)
    run = service.get(reporting_run.id)
    initial = service.response(run)
    source = deepcopy(run.source_bundle)
    service.retry(run.id, run.revision_no)
    db.commit()
    service.execute(run.id, provider=lambda *args: pytest.fail("completed output was regenerated"))
    retried = service.response(service.get(run.id))
    assert retried["input_snapshot_id"] == initial["input_snapshot_id"]
    assert retried["body_hash"] == initial["body_hash"]
    assert service.get(run.id).source_bundle == source
