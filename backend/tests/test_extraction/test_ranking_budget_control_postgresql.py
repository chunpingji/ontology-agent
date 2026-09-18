"""Budget controls use the same PostgreSQL owner lock and revision fence as resume."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.document_analysis import DocumentAnalysisControlOperation, DocumentAnalysisRun
from app.services.document_analysis.application import (
    DocumentAnalysisApplication,
    DocumentAnalysisError,
)
from app.services.document_analysis.run_store import DocumentAnalysisRunStore
from tests.test_extraction.test_document_run_execution_postgresql import (
    _seed_run,
    pg_engine,  # noqa: F401 — explicit isolated PostgreSQL fixture
)


def test_concurrent_budget_switches_allow_only_one_revision_and_replay_the_winner(
    pg_engine,  # noqa: F811 — imported pytest fixture
):
    run_id, owner = _seed_run(pg_engine)
    with Session(pg_engine, expire_on_commit=False) as db:
        store = DocumentAnalysisRunStore(db)
        run = store.get_owned(run_id, owner)
        run = store.request_control(
            run_id, owner, action="pause", expected_revision=run.revision,
        )
        db.commit()
        revision = run.revision
        original_progress = dict(run.progress)
    barrier = Barrier(2)

    def toggle(mode):
        with Session(pg_engine, expire_on_commit=False) as db:
            app = DocumentAnalysisApplication(db, ontology_engine=object())
            run = app.store.get_owned(run_id, owner)
            barrier.wait(timeout=10)
            try:
                updated, replay = app.control(
                    run, action=f"ranking_budget_{mode}", expected_revision=revision,
                    request_key=f"switch-{mode}", reason="isolated concurrency test",
                    role="senior_analyst",
                )
                return mode, updated.ranking_budget_enabled, replay
            except DocumentAnalysisError as exc:
                assert exc.status_code == 409
                return mode, "conflict", False

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(toggle, ("enable", "disable")))
    assert sum(result[1] == "conflict" for result in results) == 1
    winner = next(result for result in results if result[1] != "conflict")
    with Session(pg_engine, expire_on_commit=False) as db:
        app = DocumentAnalysisApplication(db, ontology_engine=object())
        run = db.get(DocumentAnalysisRun, run_id)
        final_revision = run.revision
        assert run.execution_status == "paused" and run.progress == original_progress
        assert run.ranking_budget_enabled is (winner[0] == "enable")
        updated, replay = app.control(
            run, action=f"ranking_budget_{winner[0]}", expected_revision=revision,
            request_key=f"switch-{winner[0]}", reason="isolated concurrency test",
            role="senior_analyst",
        )
        assert replay and updated.revision == final_revision
        receipts = list(db.scalars(select(DocumentAnalysisControlOperation).where(
            DocumentAnalysisControlOperation.recognition_run_id == run_id,
            DocumentAnalysisControlOperation.action.like("ranking_budget_%"),
        )))
        assert len(receipts) == 1
