"""Independent-connection execution checks for an explicit PostgreSQL test DB.

SQLite tests cover the domain contract quickly, but they cannot establish row
locking semantics.  This module is intentionally skipped unless the caller
provides a dedicated database whose name identifies it as document-analysis
test storage.  It never falls back to the application's configured database.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier, Event
from time import monotonic, sleep

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, select, text, update
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session

from app.models.document_analysis import (
    DocumentAnalysisExecution,
    DocumentAnalysisRun,
    DocumentRecognitionEvent,
)
from app.services.document_analysis.run_store import (
    DocumentAnalysisRunStore,
    FenceViolation,
    HeadConflict,
    LeaseBusy,
)

_TABLES = (
    "document_analysis_control_operations",
    "document_analysis_proof_heads",
    "document_analysis_verification_proofs",
    "document_analysis_candidate_heads",
    "document_analysis_run_candidates",
    "document_analysis_event_batches",
    "document_analysis_events",
    "document_analysis_artifact_heads",
    "document_analysis_run_artifacts",
    "document_analysis_artifacts",
    "document_analysis_executions",
    "document_analysis_tombstones",
    "document_analysis_runs",
)


@pytest.fixture
def pg_engine() -> Engine:
    raw_url = os.environ.get("DOCUMENT_ANALYSIS_TEST_DATABASE_URL")
    if not raw_url:
        pytest.skip("requires explicitly provisioned isolated document-analysis PostgreSQL")
    url = make_url(raw_url)
    database = url.database or ""
    assert url.get_backend_name() == "postgresql"
    assert "document_analysis" in database and database.endswith("_test")
    engine = create_engine(raw_url, pool_pre_ping=True)
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parents[2] / "alembic"))
    expected_revision = ScriptDirectory.from_config(config).get_current_head()
    with engine.begin() as connection:
        revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
        assert revision == expected_revision
        present = set(
            connection.execute(
                text(
                    "SELECT tablename FROM pg_tables "
                    "WHERE schemaname='public' AND tablename LIKE 'document_analysis_%'"
                )
            ).scalars()
        )
        assert set(_TABLES).issubset(present)
        quoted = ",".join(f'"{name}"' for name in _TABLES)
        connection.execute(text(f"TRUNCATE {quoted} CASCADE"))
    try:
        yield engine
    finally:
        engine.dispose()


def _seed_run(engine: Engine) -> tuple[str, str]:
    with Session(engine, expire_on_commit=False, autoflush=False) as db:
        run, created = DocumentAnalysisRunStore(db).create_run(
            owner_id="pg-owner",
            request_key="pg-concurrency",
            filename="source.docx",
            document_hash="d" * 64,
            root_class_iri="https://ontology.example/CMCReport",
        )
        assert created
        db.commit()
        return str(run.recognition_run_id), run.owner_id


@pytest.mark.parametrize("lock_wait", [5, 31])
def test_postgresql_heartbeat_rechecks_clock_after_actual_row_lock(pg_engine, lock_wait):
    run_id, owner_id = _seed_run(pg_engine)
    clock = [datetime.now(UTC)]
    with Session(pg_engine, expire_on_commit=False) as db:
        store = DocumentAnalysisRunStore(db, clock=lambda: clock[0])
        token = store.claim(run_id, owner_id, actor="test", worker_id="owner", lease_seconds=30)
        db.commit()
    entered, backend_pid = Event(), []

    def heartbeat():
        with Session(pg_engine, expire_on_commit=False) as db:
            backend_pid.append(db.scalar(text("SELECT pg_backend_pid()")))
            store = DocumentAnalysisRunStore(db, clock=lambda: clock[0])
            original = store._lock_fence

            def wait_on_lock(*args):
                entered.set()
                return original(*args)

            store._lock_fence = wait_on_lock
            try:
                renewed = store.heartbeat(run_id, owner_id, token, lease_seconds=30)
                db.commit()
                return renewed.lease_expires_at
            except FenceViolation:
                db.rollback()
                return "expired"

    with ThreadPoolExecutor(max_workers=1) as pool:
        with Session(pg_engine, expire_on_commit=False) as locked:
            DocumentAnalysisRunStore(locked, clock=lambda: clock[0]).assert_fence(
                run_id, owner_id, token, for_update=True,
            )
            future = pool.submit(heartbeat)
            assert entered.wait(5)
            deadline, waiting = monotonic() + 5, False
            with pg_engine.connect() as observer:
                while monotonic() < deadline:
                    waiting = observer.scalar(text(
                        "SELECT wait_event_type = 'Lock' FROM pg_stat_activity WHERE pid = :pid"
                    ), {"pid": backend_pid[0]})
                    observer.commit()
                    if waiting:
                        break
                    sleep(0.01)
            assert waiting, "heartbeat did not reach the independent row-lock wait"
            clock[0] += timedelta(seconds=lock_wait)
            locked.rollback()
        result = future.result(timeout=5)
    assert result == ("expired" if lock_wait > 30 else clock[0] + timedelta(seconds=30))


def test_postgresql_cold_state_preparation_does_not_block_heartbeat(pg_engine, monkeypatch):
    from app.services.document_analysis import execution

    run_id, owner_id = _seed_run(pg_engine)
    with Session(pg_engine, expire_on_commit=False) as db:
        store = DocumentAnalysisRunStore(db)
        token = store.claim(run_id, owner_id, actor="test", worker_id="owner", lease_seconds=30)
        run = store.get_owned(run_id, owner_id)
        run.run_fingerprint = "f" * 64
        db.commit()
    preparing, release = Event(), Event()
    original = execution._restore_ranking_state

    def slow_restore(*args, **kwargs):
        value = original(*args, **kwargs)
        preparing.set()
        assert release.wait(5)
        return value

    monkeypatch.setattr(execution, "_restore_ranking_state", slow_restore)

    def publish():
        with Session(pg_engine, expire_on_commit=False) as db:
            store = DocumentAnalysisRunStore(db)
            execution._persist_ranking_state(
                db, store, store.get_owned(run_id, owner_id), token,
                final_fingerprint="f" * 64,
                state={"recognition_run_id": run_id, "run_fingerprint": "f" * 64,
                       "service": {"costs": {"model_calls": 1}, "cache": {}}},
            )

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(publish)
        try:
            assert preparing.wait(5)
            with Session(pg_engine) as heartbeat_db:
                heartbeat_db.execute(text("SET LOCAL lock_timeout = '250ms'"))
                DocumentAnalysisRunStore(heartbeat_db).heartbeat(run_id, owner_id, token)
                heartbeat_db.commit()
        finally:
            release.set()
        future.result(timeout=5)


def test_postgresql_claim_is_unique_and_expired_generation_fences_late_writes(
    pg_engine: Engine,
):
    run_id, owner_id = _seed_run(pg_engine)
    claim_barrier = Barrier(2)

    def claim(worker: str):
        with Session(pg_engine, expire_on_commit=False, autoflush=False) as db:
            claim_barrier.wait(timeout=10)
            try:
                token = DocumentAnalysisRunStore(db).claim(
                    run_id,
                    owner_id,
                    actor="postgresql-test",
                    worker_id=worker,
                    lease_seconds=30,
                )
                db.commit()
                return token
            except LeaseBusy:
                db.rollback()
                return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        tokens = list(pool.map(claim, ("worker-a", "worker-b")))
    issued = [token for token in tokens if token is not None]
    assert len(issued) == 1
    old_token = issued[0]

    append_barrier = Barrier(2)

    def append_once(key: str) -> str:
        with Session(pg_engine, expire_on_commit=False, autoflush=False) as db:
            store = DocumentAnalysisRunStore(db)
            run = store.get_owned(run_id, owner_id)
            append_barrier.wait(timeout=10)
            try:
                store.append_event(
                    run_id,
                    owner_id,
                    old_token,
                    expected_head=run.event_head,
                    event_key=key,
                    event_type="progress",
                    payload={"worker_key": key},
                )
                db.commit()
                return "committed"
            except HeadConflict as exc:
                assert str(exc) == "stale event head"
                assert exc.details == {"expected": 0, "actual": 1}
                db.rollback()
                return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(append_once, ("batch-a", "batch-b")))
    assert sorted(outcomes) == ["committed", "conflict"]

    with Session(pg_engine, expire_on_commit=False, autoflush=False) as db:
        db.execute(
            update(DocumentAnalysisExecution)
            .where(DocumentAnalysisExecution.recognition_run_id == run_id)
            .values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        db.commit()
        store = DocumentAnalysisRunStore(db)
        new_token = store.claim(
            run_id,
            owner_id,
            actor="postgresql-test-recovery",
            worker_id="worker-recovered",
            lease_seconds=30,
        )
        db.commit()
        assert new_token != old_token
        with pytest.raises(FenceViolation):
            store.assert_fence(run_id, owner_id, old_token)
        current = store.get_owned(run_id, owner_id)
        store.append_event(
            run_id,
            owner_id,
            new_token,
            expected_head=current.event_head,
            event_key="recovered-batch",
            event_type="progress",
            payload={"worker_key": "recovered"},
        )
        db.commit()

    with Session(pg_engine) as db:
        run = db.get(DocumentAnalysisRun, run_id)
        events = list(
            db.scalars(
                select(DocumentRecognitionEvent)
                .where(DocumentRecognitionEvent.recognition_run_id == run_id)
                .order_by(DocumentRecognitionEvent.sequence)
            )
        )
        assert run is not None and run.event_head == 2
        assert [event.sequence for event in events] == [1, 2]
        assert events[-1].event_key == "recovered-batch"
