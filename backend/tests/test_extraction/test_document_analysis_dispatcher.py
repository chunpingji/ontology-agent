"""Liveness checks for the persistent document-analysis dispatcher."""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import BackgroundTasks
from sqlalchemy import create_engine, select, update
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.orm import Session, sessionmaker

from app.api import document_analysis
from app.config import settings
from app.models.document_analysis import (
    DocumentAnalysisExecution,
    DocumentAnalysisRun,
)
from app.services.document_analysis import execution as execution_service
from app.services.document_analysis.run_store import (
    DocumentAnalysisRunStore,
    LeaseBusy,
)


class _EmptyResult:
    def first(self):
        return None


class _StatementCapture:
    def __init__(self, dialect_name: str) -> None:
        self.statement = None
        self._bind = type("Bind", (), {"dialect": type("Dialect", (), {"name": dialect_name})()})()

    def begin_nested(self):
        return nullcontext()

    def get_bind(self):
        return self._bind

    def execute(self, statement):
        self.statement = statement
        return _EmptyResult()


def _dispatcher_database(path: Path):
    engine = create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False, "timeout": 5},
    )
    DocumentAnalysisRun.__table__.create(engine)
    DocumentAnalysisExecution.__table__.create(engine)
    return engine, sessionmaker(bind=engine, expire_on_commit=False)


def test_claim_next_uses_skip_locked_only_on_postgresql():
    postgres_session = _StatementCapture("postgresql")
    sqlite_session = _StatementCapture("sqlite")

    assert (
        DocumentAnalysisRunStore(postgres_session).claim_next(actor="test", worker_id="one") is None
    )
    assert (
        DocumentAnalysisRunStore(sqlite_session).claim_next(actor="test", worker_id="two") is None
    )

    postgres_sql = str(postgres_session.statement.compile(dialect=postgresql.dialect()))
    sqlite_sql = str(sqlite_session.statement.compile(dialect=sqlite.dialect()))
    assert "FOR UPDATE OF document_analysis_executions SKIP LOCKED" in postgres_sql
    assert "FOR UPDATE" not in sqlite_sql


@pytest.mark.asyncio
async def test_api_delivery_only_wakes_an_active_lifespan_dispatcher(monkeypatch):
    monkeypatch.setattr(execution_service, "dispatch_next_run", lambda **_kwargs: False)
    dispatcher = execution_service.DocumentAnalysisDispatcher(
        poll_interval_seconds=60,
        max_concurrency=1,
    )
    dispatcher.start()
    try:
        background_tasks = BackgroundTasks()
        document_analysis._wake_dispatcher_or_fallback(
            background_tasks,
            uuid4(),
            bind=object(),
        )
        assert background_tasks.tasks == []
    finally:
        await dispatcher.stop()


def _enqueue(factory: sessionmaker[Session], request_key: str):
    with factory() as db:
        run, created = DocumentAnalysisRunStore(db).create_run(
            owner_id="dispatcher-test",
            request_key=request_key,
            filename=f"{request_key}.docx",
            document_hash=request_key.ljust(64, "0")[:64],
            root_class_iri="https://example.test/Root",
        )
        db.commit()
        assert created
        return run.recognition_run_id


async def _eventually(assertion: Callable[[], bool], *, timeout: float = 3.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if await asyncio.to_thread(assertion):
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition was not satisfied before timeout")


def _finished(factory: sessionmaker[Session], run_ids: set) -> bool:
    with factory() as db:
        statuses = dict(
            db.execute(
                select(
                    DocumentAnalysisRun.recognition_run_id,
                    DocumentAnalysisRun.execution_status,
                ).where(DocumentAnalysisRun.recognition_run_id.in_(run_ids))
            ).all()
        )
    return len(statuses) == len(run_ids) and set(statuses.values()) == {"finished"}


@pytest.mark.asyncio
async def test_dispatcher_continuously_drains_and_polls_for_new_work(tmp_path, monkeypatch):
    engine, factory = _dispatcher_database(tmp_path / "dispatcher.sqlite")
    initial = {_enqueue(factory, f"initial-{index}") for index in range(7)}
    observed: list = []
    active = 0
    peak_active = 0
    lock = threading.Lock()

    def execute(db, store, run, token, **_kwargs):
        nonlocal active, peak_active
        with lock:
            active += 1
            peak_active = max(peak_active, active)
            observed.append(run.recognition_run_id)
        time.sleep(0.03)
        current = store.get_owned(run.recognition_run_id, run.owner_id)
        store.update_stage(
            current.recognition_run_id,
            current.owner_id,
            token,
            expected_revision=current.revision,
            stage="complete",
            execution_status="finished",
        )
        db.commit()
        with lock:
            active -= 1

    monkeypatch.setattr(execution_service, "_execute_claimed", execute)
    dispatcher = execution_service.DocumentAnalysisDispatcher(
        bind=engine,
        poll_interval_seconds=0.03,
        # SQLite has a single writer; PostgreSQL multi-worker locking is
        # verified separately by the dialect assertion above.
        max_concurrency=1,
    )
    runner = dispatcher.start()
    try:
        await _eventually(lambda: _finished(factory, initial))
        late = _enqueue(factory, "created-after-start")
        # No explicit notification: periodic sweeping is the durable fallback
        # for a lost API wake-up.
        await _eventually(lambda: _finished(factory, {late}))
    finally:
        await dispatcher.stop()
        engine.dispose()

    assert set(observed) == {*initial, late}
    assert len(observed) == len(initial) + 1
    assert peak_active == 1
    assert runner.done()
    assert dispatcher.worker_task_count == 0


@pytest.mark.asyncio
async def test_dispatcher_reclaims_lease_that_expires_after_start(tmp_path, monkeypatch):
    engine, factory = _dispatcher_database(tmp_path / "expired.sqlite")
    run_id = _enqueue(factory, "expires-after-start")
    with factory() as db:
        store = DocumentAnalysisRunStore(db)
        store.claim(
            run_id,
            "dispatcher-test",
            actor="crashed-worker",
            worker_id="crashed-worker",
            lease_seconds=30,
        )
        db.execute(
            update(DocumentAnalysisExecution)
            .where(DocumentAnalysisExecution.recognition_run_id == run_id)
            .values(lease_expires_at=datetime.now(UTC) + timedelta(seconds=0.2))
        )
        db.commit()

    claimed = threading.Event()

    def execute(db, store, run, token, **_kwargs):
        claimed.set()
        current = store.get_owned(run.recognition_run_id, run.owner_id)
        store.update_stage(
            current.recognition_run_id,
            current.owner_id,
            token,
            expected_revision=current.revision,
            stage="complete",
            execution_status="finished",
        )
        db.commit()

    monkeypatch.setattr(execution_service, "_execute_claimed", execute)
    dispatcher = execution_service.DocumentAnalysisDispatcher(
        bind=engine,
        poll_interval_seconds=0.02,
        max_concurrency=1,
    )
    dispatcher.start()
    try:
        await asyncio.sleep(0.08)
        assert not claimed.is_set()
        await _eventually(claimed.is_set)
        await _eventually(lambda: _finished(factory, {run_id}))
        with factory() as db:
            execution = db.get(DocumentAnalysisExecution, run_id)
            # initial claim + replacement claim + terminal revocation
            assert execution.generation == 3
    finally:
        await dispatcher.stop()
        engine.dispose()


@pytest.mark.asyncio
async def test_lease_keeper_prevents_reclaim_during_a_long_stage(tmp_path, monkeypatch):
    engine, factory = _dispatcher_database(tmp_path / "heartbeat.sqlite")
    run_id = _enqueue(factory, "long-stage")
    calls: list = []
    entered = threading.Event()
    returned = threading.Event()

    def execute(_db, _store, run, _token, **_kwargs):
        calls.append(run.recognition_run_id)
        entered.set()
        time.sleep(0.55)
        returned.set()

    monkeypatch.setattr(settings, "document_analysis_lease_seconds", 0.2)
    monkeypatch.setattr(execution_service, "_execute_claimed", execute)
    dispatcher = execution_service.DocumentAnalysisDispatcher(
        bind=engine,
        poll_interval_seconds=0.02,
        max_concurrency=1,
    )
    dispatcher.start()
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        with factory() as db:
            first_heartbeat = db.get(DocumentAnalysisExecution, run_id).heartbeat_at
        await asyncio.sleep(0.3)
        with factory() as db, pytest.raises(LeaseBusy):
            DocumentAnalysisRunStore(db).claim(
                run_id,
                "dispatcher-test",
                actor="competing-dispatcher",
                worker_id="competing-dispatcher",
                lease_seconds=0.2,
            )
        assert await asyncio.to_thread(returned.wait, 2)
        await dispatcher.stop()
        with factory() as db:
            execution = db.get(DocumentAnalysisExecution, run_id)
            assert execution.generation == 1
            assert execution.heartbeat_at > first_heartbeat
    finally:
        if dispatcher.is_running:
            await dispatcher.stop()
        engine.dispose()

    assert calls == [run_id]
    assert not any(
        thread.name == f"document-analysis-lease-{run_id}" for thread in threading.enumerate()
    )
