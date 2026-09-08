"""Real PostgreSQL transactions on an explicitly provisioned isolated test database."""

import os
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from threading import Barrier

import pytest
from sqlalchemy import create_engine, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.models.ontology_meta import ROLE_NAMES, AppRole, AppUser
from app.models.reporting import (
    ReportRun,
    ReportSignature,
    ReportSignatureSnapshot,
)
from app.services.reporting.report_run_service import ReportRunService
from app.services.reporting.report_signing import ReportSigning
from app.services.reporting.template_v2 import ReportingError
from tests.test_reporting.test_report_signing import (
    ready_session,
    reporting_run,  # noqa: F401
    signature_payload,
)


@pytest.fixture
def db():
    url = os.environ.get("REPORTING_TEST_DATABASE_URL")
    if not url:
        pytest.skip("requires explicitly provisioned isolated PostgreSQL")
    assert make_url(url).database.startswith("reporting_")
    engine = create_engine(url)
    with engine.begin() as connection:
        names = list(
            connection.execute(
                text(
                    "SELECT tablename FROM pg_tables WHERE schemaname='public' "
                    "AND tablename <> 'alembic_version'"
                )
            ).scalars()
        )
        connection.execute(
            text("TRUNCATE " + ",".join('"' + name + '"' for name in names) + " CASCADE")
        )
    with Session(engine, expire_on_commit=False, autoflush=False) as session:
        for name in ROLE_NAMES:
            session.add(AppRole(name=name, description=name))
        session.commit()
        role = session.scalar(select(AppRole).where(AppRole.name == "senior_analyst"))
        session.add(AppUser(username="analyst", display_name="Analyst", role_id=role.id))
        session.commit()
        yield session
    engine.dispose()


def test_independent_connections_sign_with_cas_and_replay(db, request):
    fixture_run = request.getfixturevalue("reporting_run")
    service, session, qa = ready_session(db, fixture_run)
    payload = signature_payload(session)
    barrier = Barrier(2)

    def sign():
        with Session(db.bind, expire_on_commit=False, autoflush=False) as other:
            signing = ReportSigning(other)
            barrier.wait(timeout=10)
            try:
                result = signing.sign(session.id, payload, qa)
                other.commit()
                return result.id
            except ReportingError as exc:
                other.rollback()
                assert exc.code in {"SIGNATURE_REVISION_CONFLICT", "SIGNATURE_SLOT_OCCUPIED"}
                return signing.sign(session.id, payload, qa).id

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: sign(), range(2)))
    assert len(set(results)) == 1
    assert len(list(db.scalars(select(ReportSignature)))) == 1
    assert service.get_session(session.id).revision_no == 1


def test_sql_cannot_rewrite_frozen_body_source_or_signing_context(db, request):
    fixture_run = request.getfixturevalue("reporting_run")
    service, session, _ = ready_session(db, fixture_run)
    body = service.runs.body(fixture_run.id)
    for statement, ref in [
        ("UPDATE report_bodies SET payload='{}' WHERE id=:ref", body.id),
        ("UPDATE report_runs SET source_bundle='{}' WHERE id=:ref", fixture_run.id),
        ("UPDATE report_signing_sessions SET frozen_context='{}' WHERE id=:ref", session.id),
    ]:
        with pytest.raises(DBAPIError, match="immutable"):
            db.execute(text(statement), {"ref": ref})
        db.rollback()
    assert service.runs.body(fixture_run.id).content_hash == body.content_hash


def test_frozen_envelope_recovers_in_an_independent_transaction(db, request, monkeypatch):
    fixture_run = request.getfixturevalue("reporting_run")
    service, session, qa = ready_session(db, fixture_run)
    service.sign(session.id, signature_payload(session), qa)
    db.commit()
    body = deepcopy(service.runs.body(fixture_run.id).payload)
    payload = {
        "content_hash": session.content_hash,
        "signature_revision": 1,
        "purpose": "formal",
        "idempotency_key": "envelope-crash",
    }
    from app.services.reporting import report_signing

    original = report_signing.render_docx

    def interrupted(*args):
        raise OSError("synthetic file interruption")

    monkeypatch.setattr(report_signing, "render_docx", interrupted)
    with pytest.raises(OSError):
        service.envelope(session.id, payload, qa)
    db.rollback()
    snapshot = db.scalar(select(ReportSignatureSnapshot.id))
    assert snapshot is not None
    monkeypatch.setattr(report_signing, "render_docx", original)
    with Session(db.bind, expire_on_commit=False, autoflush=False) as other:
        signing = ReportSigning(other)
        frozen = signing.get_session(session.id)
        assert frozen.envelope_request["payload"] == payload
        envelope = signing.envelope(session.id, frozen.envelope_request["payload"], qa)
        other.commit()
        assert envelope.signature_snapshot_id == snapshot
        assert envelope.payload["final_ast"]["children"][0] == body["body_ast"]
    assert len(list(db.scalars(select(ReportSignatureSnapshot)))) == 1


def test_expired_worker_recovery_reuses_original_source_and_outputs(db, request):
    fixture_run = request.getfixturevalue("reporting_run")
    run = ReportRunService(db).get(fixture_run.id)
    old_snapshot = run.input_snapshot_id
    db.execute(
        update(ReportRun)
        .where(ReportRun.id == run.id)
        .values(
            execution_status="running",
            worker_token="crashed-worker",
            lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        )
    )
    db.commit()
    with Session(db.bind, expire_on_commit=False, autoflush=False) as other:
        service = ReportRunService(other)
        current = service.get(run.id)
        service.retry(current.id, current.revision_no)
        other.commit()
        service.execute(current.id)
        assert service.get(current.id).input_snapshot_id == old_snapshot
        assert service.get(current.id).attempt == 2


def test_published_legacy_rule_is_immutable_even_for_bulk_sql(db):
    from app.models.ontology_meta import OntologyDecisionRule

    row = OntologyDecisionRule(
        rule_key="historical",
        slpra_iri="urn:rule:historical",
        label="Original",
        rule_group="risk_assessment",
        status="published",
        version=3,
        antecedent={},
        consequent={"text": "Original"},
    )
    db.add(row)
    db.commit()
    for statement in [
        "UPDATE ontology_decision_rule SET consequent='{}' WHERE id=:ref",
        "DELETE FROM ontology_decision_rule WHERE id=:ref",
    ]:
        with pytest.raises(DBAPIError, match="published rule requires a new revision"):
            db.execute(text(statement), {"ref": row.id})
        db.rollback()
    db.refresh(row)
    assert row.version == 3 and row.consequent == {"text": "Original"}
