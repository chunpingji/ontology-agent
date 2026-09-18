"""Responses continuation and atomic evidence grants on disposable PostgreSQL."""

import pytest
from sqlalchemy.orm import Session

from app.services.document_analysis.run_store import DocumentAnalysisRunStore
from tests.test_extraction.test_document_run_execution_postgresql import pg_engine as pg_engine
from tests.test_extraction.test_document_state_artifacts import seed
from tests.test_extraction.test_tool_engine_execution import (
    test_group_scope_and_exact_entity_source_survive_cold_pause as check_cold_resume,
)
from tests.test_extraction.test_tool_engine_execution import (
    test_identity_append_preserves_entity_and_requires_exact_finalized_target as check_identity,
)
from tests.test_extraction.test_tool_engine_execution import (
    test_unreserved_request_does_not_become_unknown_on_cold_continue as check_unreserved_resume,
)
from tests.test_extraction.test_tool_engine_resume import (
    test_result_and_authorization_reference_commit_or_rollback_together as check_atomic_grant,
)


def current_run(db):
    store = DocumentAnalysisRunStore(db)
    run, token = seed(store, "responses-postgresql")
    run.run_fingerprint = "responses-postgresql-fingerprint"
    db.commit()
    return store, run, token


@pytest.mark.parametrize("pause", ["pending_request", "root_batch"])
def test_postgresql_scoped_responses_cold_continue(pg_engine, tmp_path, monkeypatch, pause):
    with Session(pg_engine, expire_on_commit=False) as db:
        check_cold_resume(tmp_path, monkeypatch, current_run(db), pause)


def test_postgresql_evidence_result_and_authorization_are_atomic(pg_engine, monkeypatch):
    with Session(pg_engine, expire_on_commit=False) as db:
        check_atomic_grant(current_run(db), monkeypatch)


@pytest.mark.parametrize("failure", ["reservation_hook", "reservation_transaction", "budget"])
def test_postgresql_unreserved_request_cold_continue(pg_engine, tmp_path, monkeypatch, failure):
    with Session(pg_engine, expire_on_commit=False) as db:
        check_unreserved_resume(tmp_path, monkeypatch, current_run(db), failure)


@pytest.mark.parametrize("tamper", [None, "decision_target", "provenance"])
def test_postgresql_identity_append_is_exact_and_survives_restore(
    pg_engine, tmp_path, monkeypatch, tamper,
):
    with Session(pg_engine, expire_on_commit=False) as db:
        check_identity(tmp_path, monkeypatch, current_run(db), tamper)
