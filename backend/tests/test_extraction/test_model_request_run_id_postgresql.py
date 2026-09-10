"""Run-ID width and migration safety on an explicitly isolated PostgreSQL DB.

Set MODEL_REQUEST_TEST_DATABASE_URL to a disposable local database named
document_analysis_ranking_test. Each test owns one random schema and creates
only the two scheduler tables; it never falls back to the application database.
"""

from __future__ import annotations

import importlib.util
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from queue import Queue
from time import monotonic, sleep
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DataError
from sqlalchemy.orm import Session

from app.models.model_request import LocalModelRequest
from app.services.llm.model_runtime import model_scope
from app.services.llm.model_scheduler import RequestTicket

_VERSIONS = Path(__file__).parents[2] / "alembic/versions"
_OLD_MIGRATION = "0032_local_model_requests"
_NEW_MIGRATION = "0034_model_request_run_id"
_DOCUMENT_RUN_ID = "12345678-1234-4234-8234-123456789abc"
_RUN_IDS = ("a" * 32, _DOCUMENT_RUN_ID, f"eval-{_DOCUMENT_RUN_ID}", "b" * 64)
_TASK_ID = "c" * 64


def _migration(name):
    spec = importlib.util.spec_from_file_location(name, _VERSIONS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _apply(engine, revision, operation="upgrade"):
    migration = _migration(revision)
    with engine.begin() as connection:
        migration.op = Operations(MigrationContext.configure(connection))
        getattr(migration, operation)()


@pytest.fixture
def request_engine():
    raw_url = os.environ.get("MODEL_REQUEST_TEST_DATABASE_URL")
    if not raw_url:
        pytest.skip("requires an explicit isolated PostgreSQL scheduler migration database")
    url = make_url(raw_url)
    assert url.get_backend_name() == "postgresql"
    assert url.host in {"127.0.0.1", "localhost", "::1"}
    assert url.database == "document_analysis_ranking_test"
    schema = f"request_id_{uuid4().hex}"
    admin = create_engine(raw_url)
    with admin.begin() as connection:
        assert connection.scalar(text("SELECT current_database()")) == url.database
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(
        raw_url,
        connect_args={
            "options": f"-csearch_path={schema} -cstatement_timeout=15000 -clock_timeout=10000"
        },
    )
    try:
        _apply(engine, _OLD_MIGRATION)
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def _complete_request(engine, run_id, task_id=_TASK_ID):
    with model_scope(bind=engine, run_id=run_id, task_id=task_id, stage="ranking_embed"):
        ticket = RequestTicket("http://isolated-ranking.test:80", uuid4().hex, 0, 30, capacity=1)
        assert ticket.admit()
        ticket.start()
        ticket.finish("completed", input_tokens=7)
    with Session(engine) as session:
        row = session.scalar(select(LocalModelRequest).where(
            LocalModelRequest.request_id == ticket.request_id,
            LocalModelRequest.run_id == run_id,
            LocalModelRequest.task_id == task_id,
        ))
        assert row is not None
        assert row.status == "completed"
        assert row.started_at is not None and row.finished_at is not None
        assert row.metrics["input_tokens"] == 7
        return row.request_id, row.run_id, row.task_id


def _column_length(engine):
    columns = inspect(engine).get_columns("local_model_requests")
    return next(column["type"].length for column in columns if column["name"] == "run_id")


@pytest.mark.parametrize("run_id", _RUN_IDS[1:3], ids=("document-uuid-36", "evaluation-41"))
def test_old_postgresql_column_rejects_real_run_ids_before_inference(request_engine, run_id):
    assert _column_length(request_engine) == 32
    with pytest.raises(DataError) as error:
        _complete_request(request_engine, run_id)
    assert error.value.orig.pgcode == "22001"
    with Session(request_engine) as session:
        assert list(session.scalars(select(LocalModelRequest))) == []


@pytest.mark.parametrize("run_id", _RUN_IDS, ids=("legacy-32", "document-36", "eval-41", "full-64"))
def test_upgrade_preserves_existing_rows_and_complete_request_association(request_engine, run_id):
    legacy = _complete_request(request_engine, "f" * 32)
    _apply(request_engine, _NEW_MIGRATION)
    assert _column_length(request_engine) == 64
    assert LocalModelRequest.__table__.c.run_id.type.length == 64
    result = _complete_request(request_engine, run_id)
    assert result[1:] == (run_id, _TASK_ID)
    with Session(request_engine) as session:
        row = session.scalar(select(LocalModelRequest).where(
            LocalModelRequest.request_id == legacy[0],
        ))
        assert (row.request_id, row.run_id, row.task_id) == legacy
        assert row.status == "completed" and row.metrics["input_tokens"] == 7


def test_downgrade_refuses_long_run_ids_without_changing_rows_or_type(request_engine):
    _apply(request_engine, _NEW_MIGRATION)
    before = _complete_request(request_engine, _DOCUMENT_RUN_ID)
    with pytest.raises(RuntimeError, match="identities longer than 32 characters"):
        _apply(request_engine, _NEW_MIGRATION, "downgrade")
    assert _column_length(request_engine) == 64
    with Session(request_engine) as session:
        row = session.scalar(select(LocalModelRequest).where(
            LocalModelRequest.request_id == before[0],
        ))
        assert (row.request_id, row.run_id, row.task_id) == before


def test_safe_downgrade_preserves_legacy_data_and_restores_original_limit(request_engine):
    before = _complete_request(request_engine, "f" * 32)
    _apply(request_engine, _NEW_MIGRATION)
    _apply(request_engine, _NEW_MIGRATION, "downgrade")
    assert _column_length(request_engine) == 32
    with Session(request_engine) as session:
        row = session.scalar(select(LocalModelRequest).where(
            LocalModelRequest.request_id == before[0],
        ))
        assert (row.request_id, row.run_id, row.task_id) == before
    with pytest.raises(DataError) as error:
        _complete_request(request_engine, _DOCUMENT_RUN_ID)
    assert error.value.orig.pgcode == "22001"


def test_downgrade_checks_after_waiting_for_concurrent_writer(request_engine):
    _apply(request_engine, _NEW_MIGRATION)
    before = _complete_request(request_engine, "f" * 32)
    backend_pid = Queue()

    def downgrade():
        migration = _migration(_NEW_MIGRATION)
        with request_engine.begin() as connection:
            backend_pid.put(connection.scalar(text("SELECT pg_backend_pid()")))
            migration.op = Operations(MigrationContext.configure(connection))
            migration.downgrade()

    with request_engine.connect() as writer:
        transaction = writer.begin()
        writer.execute(update(LocalModelRequest).where(
            LocalModelRequest.request_id == before[0],
        ).values(run_id=_DOCUMENT_RUN_ID))
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(downgrade)
            try:
                pid = backend_pid.get(timeout=5)
                deadline = monotonic() + 5
                waiting = False
                with request_engine.connect() as observer:
                    while monotonic() < deadline:
                        waiting = observer.scalar(text(
                            "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE pid=:pid "
                            "AND relation='local_model_requests'::regclass "
                            "AND mode='AccessExclusiveLock' AND NOT granted)"
                        ), {"pid": pid})
                        if waiting:
                            break
                        sleep(0.02)
                assert waiting, "downgrade did not wait for the concurrent writer"
            finally:
                transaction.commit()
            with pytest.raises(RuntimeError, match="identities longer than 32 characters"):
                future.result(timeout=5)
    assert _column_length(request_engine) == 64
    with Session(request_engine) as session:
        row = session.scalar(select(LocalModelRequest).where(
            LocalModelRequest.request_id == before[0],
        ))
        assert row.run_id == _DOCUMENT_RUN_ID
