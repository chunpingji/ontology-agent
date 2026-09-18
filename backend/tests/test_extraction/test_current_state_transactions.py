"""Current writes preserve fencing, ownership, atomicity and independent versions."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from time import perf_counter
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import Session

from app.models.document_analysis import DocumentRunResult
from app.services.document_analysis import current_state
from app.services.document_analysis.run_store import (
    DocumentAnalysisRunStore,
    FenceViolation,
    HeadConflict,
    RunNotFound,
)
from tests.test_extraction.test_document_analysis_execution_recovery import _expire_lease
from tests.test_extraction.test_document_run_execution_postgresql import pg_engine as pg_engine
from tests.test_extraction.test_document_state_artifacts import seed


@pytest.mark.parametrize("result_count", [20, 400])
def test_current_commit_does_not_read_independent_result_history(db, monkeypatch, result_count):
    store = DocumentAnalysisRunStore(db)
    run, token = seed(store, "fixed-current-write")
    run.run_fingerprint = "fixed-current-write"
    db.commit()
    current_state.put_rows(
        store,
        run,
        DocumentRunResult,
        "calls:results",
        {str(i): {"result": "synthetic-result" * 100} for i in range(result_count)},
        immutable=True,
    )
    db.commit()

    def write(version, value):
        return current_state.persist_boundary(
            store,
            run,
            token,
            changes={"control": {"current": {"value": value}}},
            fingerprint="fixed-current-write",
            expected_version=version,
        )

    write(0, 1)
    checked = []
    original = current_state._checked

    def check(row):
        checked.append(row.domain)
        return original(row)

    monkeypatch.setattr(current_state, "_checked", check)
    started = perf_counter()
    assert write(1, 2) == 2
    elapsed = (perf_counter() - started) * 1000
    assert checked == ["work:control"]
    print(
        {
            "independent_results": result_count,
            "checked_partitions": len(checked),
            "sqlite_commit_ms": round(elapsed, 3),
        }
    )


def test_current_write_rejects_conflict_foreign_owner_and_expired_worker(db):
    store = DocumentAnalysisRunStore(db)
    run, token = seed(store, "current-transactions")
    run.run_fingerprint = "current-transactions"
    db.commit()

    def write(version, value):
        return current_state.persist_boundary(
            store,
            run,
            token,
            changes={"control": {"current": {"value": value}}},
            fingerprint="current-transactions",
            expected_version=version,
        )

    assert write(0, 1) == 1
    run.revision += 1
    db.commit()
    assert write(1, 2) == 2
    with pytest.raises(HeadConflict, match="work version changed"):
        write(1, 3)
    assert current_state.get_row(store, run, "work:control") == {"value": 2}
    foreign = SimpleNamespace(recognition_run_id=run.recognition_run_id, owner_id="other-owner")
    with pytest.raises(RunNotFound):
        current_state.get_row(store, foreign, "work:control")
    _expire_lease(db, run.recognition_run_id)
    with pytest.raises(FenceViolation):
        write(2, 4)
    assert current_state.get_row(store, run, "work:control") == {"value": 2}


def test_postgresql_competing_current_work_commits_have_one_winner(pg_engine):
    with Session(pg_engine, expire_on_commit=False) as db:
        store = DocumentAnalysisRunStore(db)
        run, token = seed(store, "current-competing")
        run.run_fingerprint = "current-competing"
        db.commit()
        run_id, owner_id = run.recognition_run_id, run.owner_id
    ready = Barrier(2)

    def write(value):
        with Session(pg_engine, expire_on_commit=False, autoflush=False) as db:
            store = DocumentAnalysisRunStore(db)
            run = store.get_owned(run_id, owner_id)
            ready.wait(timeout=10)
            try:
                return current_state.persist_boundary(
                    store,
                    run,
                    token,
                    changes={"control": {"current": {"value": value}}},
                    fingerprint="current-competing",
                    expected_version=0,
                )
            except HeadConflict:
                return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(write, [1, 2]))
    assert results.count(1) == results.count("conflict") == 1
