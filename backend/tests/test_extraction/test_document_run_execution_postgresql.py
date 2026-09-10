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
from threading import Barrier

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
