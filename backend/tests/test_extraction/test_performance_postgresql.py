"""Independent-connection fault checks for immutable state and model acknowledgements.

Uses the existing dedicated-database guard and schema-revision check. These tests
require PostgreSQL tables at the current model schema; they do not claim that an
empty database can traverse every historical migration.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier, Event, get_ident, local
from time import monotonic

import pytest
from sqlalchemy import event, func, select, text, update
from sqlalchemy.orm import Session

from app.models.document_analysis import (
    DocumentAnalysisArtifact,
    DocumentAnalysisExecution,
    DocumentRecognitionEvent,
    DocumentRecognitionEventBatch,
    DocumentRunArtifact,
)
from app.services.document_analysis import execution as execution_service
from app.services.document_analysis.run_store import (
    DocumentAnalysisRunStore,
    FenceViolation,
    HeadConflict,
    content_hash,
)
from app.services.document_analysis.state_artifacts import decode_state, encode_state
from app.services.extraction.ontology_guided.executor import TaskOutcome
from app.services.extraction.ontology_guided.ontology_plan import compile_local_menu
from app.services.extraction.ontology_guided.recognition_execution import RecognitionCall
from app.services.llm.model_runtime import model_scope
from tests.test_extraction.test_document_run_execution_postgresql import (
    _seed_run,
)
from tests.test_extraction.test_document_run_execution_postgresql import (
    pg_engine as pg_engine,
)
from tests.test_extraction.test_independent_semantic_verification import _inputs
from tests.test_extraction.test_semantic_graph_closure import _ontology

FINGERPRINT = "performance-postgresql-fingerprint"


def _claim(engine):
    run_id, owner = _seed_run(engine)
    with Session(engine, expire_on_commit=False, autoflush=False) as db:
        store = DocumentAnalysisRunStore(db)
        token = store.claim(run_id, owner, actor="performance-test", worker_id="initial-worker",
                            lease_seconds=120)
        current = store.get_owned(run_id, owner)
        store.update_stage(run_id, owner, token, expected_revision=current.revision,
                           stage="recognition", run_fingerprint=FINGERPRINT)
        db.commit()
    return run_id, owner, token


def _payload(version):
    return {
        "frontier": {"schema_version": 2, "unattempted": 6000 - version},
        "reservations": {"total": version, "uncertain": 1},
        "frozen_vectors": [[index / 997 + column / 113 for column in range(64)]
                           for index in range(80)],
        "results": [{"task_id": f"task-{index}", "proof_revision": 1}
                    for index in range(version)],
    }


def _checkpoint(store, run_id, owner, token, payload, *, expected_head, batch):
    # The durable event takes the same run/execution writer lock used by the
    # production recognition batch before creating its referenced state blocks.
    entry = store.append_event(
        run_id, owner, token, expected_head=expected_head, event_key=batch,
        event_type="progress", payload={"batch": batch},
    )
    run = store.get_owned(run_id, owner)
    encoded = encode_state(store, run, token, payload)
    head = store.get_artifact_head(run_id, owner, "recognition_checkpoint")
    ref = store.update_artifact(
        run_id, owner, token, artifact_kind="recognition_checkpoint",
        expected_revision=head.revision if head else 0,
        artifact_hash=content_hash(encoded), status="ready", payload=encoded,
        event_head=entry.sequence,
    )
    store.put_event_batch(
        run_id, owner, token, batch_id=batch, batch_hash=content_hash(payload),
        first_sequence=entry.sequence, last_sequence=entry.sequence,
        checkpoint_artifact_id=ref.artifact_id,
    )
    return encoded


def _observe(engine, run_id, owner):
    with Session(engine) as db:
        store = DocumentAnalysisRunStore(db)
        run = store.get_owned(run_id, owner)
        ref = store.get_artifact(run_id, owner, "recognition_checkpoint")
        state = decode_state(store, run, db.get(DocumentAnalysisArtifact, ref.artifact_id).payload)
        return {
            "state": state,
            "head": run.event_head,
            "revision": ref.revision,
            "artifact_ids": set(db.scalars(select(DocumentAnalysisArtifact.artifact_id))),
            "linked_ids": set(db.scalars(select(DocumentRunArtifact.artifact_id))),
            "receipts": db.scalar(select(func.count()).select_from(DocumentRecognitionEventBatch)),
            "events": db.scalar(select(func.count()).select_from(DocumentRecognitionEvent)),
        }


@pytest.mark.parametrize("commit", [False, True])
def test_postgresql_checkpoint_and_new_blocks_are_one_atomic_visible_boundary(pg_engine, commit):
    run_id, owner, token = _claim(pg_engine)
    original, newer = _payload(1), _payload(2)
    with Session(pg_engine, expire_on_commit=False, autoflush=False) as db:
        store = DocumentAnalysisRunStore(db)
        frozen_reference = _checkpoint(store, run_id, owner, token, original,
                                       expected_head=0, batch="original")
        db.commit()
        before = _observe(pg_engine, run_id, owner)
        _checkpoint(store, run_id, owner, token, newer, expected_head=1, batch="newer")
        assert _observe(pg_engine, run_id, owner) == before
        if commit:
            db.commit()
        else:
            db.rollback()
    after = _observe(pg_engine, run_id, owner)
    if commit:
        assert after["state"] == newer
        assert after["head"] == after["revision"] == after["events"] == after["receipts"] == 2
        assert after["artifact_ids"] == after["linked_ids"]
        assert after["artifact_ids"] > before["artifact_ids"]
        # A newer head cannot redirect the earlier immutable checkpoint's refs.
        with Session(pg_engine) as db:
            store = DocumentAnalysisRunStore(db)
            assert decode_state(store, store.get_owned(run_id, owner), frozen_reference) == original
    else:
        assert after == before


def _replace_worker(engine, run_id, owner):
    with Session(engine, expire_on_commit=False, autoflush=False) as db:
        db.execute(update(DocumentAnalysisExecution).where(
            DocumentAnalysisExecution.recognition_run_id == run_id,
        ).values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1)))
        db.commit()
        token = DocumentAnalysisRunStore(db).claim(
            run_id, owner, actor="replacement", worker_id="replacement-worker", lease_seconds=120,
        )
        db.commit()
        return token


def test_postgresql_late_state_encoder_cannot_write_after_worker_generation_changes(pg_engine):
    run_id, owner, old_token = _claim(pg_engine)
    with Session(pg_engine, expire_on_commit=False, autoflush=False) as late_db:
        late_store = DocumentAnalysisRunStore(late_db)
        stale_run = late_store.get_owned(run_id, owner)
        assert _replace_worker(pg_engine, run_id, owner) != old_token
        with pytest.raises(FenceViolation):
            encode_state(late_store, stale_run, old_token, _payload(1))
        late_db.rollback()
    with Session(pg_engine) as db:
        assert db.scalar(select(func.count()).select_from(DocumentAnalysisArtifact)) == 0
        assert db.scalar(select(func.count()).select_from(DocumentRunArtifact)) == 0


def test_postgresql_duplicate_worker_batches_serialize_before_creating_shared_blocks(pg_engine):
    run_id, owner, token = _claim(pg_engine)
    ready = Barrier(2)

    def write(batch):
        with Session(pg_engine, expire_on_commit=False, autoflush=False) as db:
            store = DocumentAnalysisRunStore(db)
            head = store.get_owned(run_id, owner).event_head
            ready.wait(timeout=10)
            try:
                _checkpoint(store, run_id, owner, token, _payload(1),
                            expected_head=head, batch=batch)
                db.commit()
                return "committed"
            except HeadConflict:
                db.rollback()
                return "fenced_by_head"

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(write, ("worker-a", "worker-b")))
    assert sorted(results) == ["committed", "fenced_by_head"]
    observed = _observe(pg_engine, run_id, owner)
    assert observed["state"] == _payload(1)
    assert (
        observed["head"] == observed["revision"] == observed["events"] == observed["receipts"] == 1
    )
    assert observed["artifact_ids"] == observed["linked_ids"]


@pytest.mark.parametrize("contender_calls", [1, 2], ids=["stale-cost", "identical-state"])
def test_postgresql_ranking_workers_validate_costs_under_lock_before_shared_v2_blocks(
    pg_engine, contender_calls,
):
    run_id, owner, token = _claim(pg_engine)
    with Session(pg_engine, expire_on_commit=False, autoflush=False) as db:
        source = {"performance_policy": {"state_storage_version": 2}}
        DocumentAnalysisRunStore(db).update_artifact(
            run_id, owner, token, artifact_kind="source", expected_revision=0,
            artifact_hash=content_hash(source), status="ready", payload=source,
        )
        db.commit()
    vectors = _payload(1)["frozen_vectors"]

    def ranking_state(calls):
        return {
            "recognition_run_id": run_id, "run_fingerprint": FINGERPRINT,
            "service": {
                # Both snapshots must traverse the same genuinely large block
                # before their different counters can become durable.
                "cache": {"frozen_vectors": vectors},
                "policy": {"version": "semantic-ranking-v2", "mode": "semantic"},
                "model_identity": {"model": "controlled-postgresql-fixture"},
                "costs": {"model_calls": calls, "tokens": calls * 17},
                "record_call_counts": {"same-slot-record": calls},
                "epochs": [], "pending_epochs": [], "dispatch_receipts": [],
            },
            "committed_at": {},
        }

    ready_to_commit, contender_at_database = Event(), Event()
    roles = local()
    control_points = []

    def before_statement(_connection, _cursor, statement, _parameters, _context, _many):
        if getattr(roles, "worker", None) != "contender":
            return
        normalized = statement.lower()
        acquiring_fence = "for update" in normalized and (
            "document_analysis_executions" in normalized or "document_analysis_runs" in normalized
        )
        inserting_block = normalized.lstrip().startswith("insert into document_analysis_artifacts")
        if acquiring_fence or inserting_block:
            # Signal *before* the contender's blocking SQL executes. Waiting
            # after it obtains the writer lock would deadlock the correct code.
            control_points.append("fence" if acquiring_fence else "block_insert")
            contender_at_database.set()

    def persist(worker, calls):
        roles.worker = worker
        if worker == "contender":
            assert ready_to_commit.wait(timeout=5), "first writer never reached its commit boundary"
        with Session(pg_engine, expire_on_commit=False, autoflush=False) as db:
            db.execute(text("SET LOCAL lock_timeout = '5s'"))
            db.execute(text("SET LOCAL statement_timeout = '10s'"))
            store = DocumentAnalysisRunStore(db)

            def before_commit(session):
                if session.in_nested_transaction():
                    return
                ready_to_commit.set()
                assert contender_at_database.wait(timeout=5), "contender never attempted its write"

            if worker == "first":
                event.listen(db, "before_commit", before_commit)
            try:
                execution_service._persist_ranking_state(
                    db, store, store.get_owned(run_id, owner), token,
                    final_fingerprint=FINGERPRINT, state=ranking_state(calls),
                )
                return "committed"
            except (HeadConflict, execution_service.CheckpointMismatch) as exc:
                db.rollback()
                return type(exc).__name__
            except Exception as exc:
                db.rollback()
                # Keep an uncontrolled unique-key/SQL failure visible without
                # printing the full vector-bearing SQL parameter payload.
                return f"unexpected:{type(exc).__name__}"
            finally:
                if worker == "first":
                    event.remove(db, "before_commit", before_commit)

    event.listen(pg_engine, "before_cursor_execute", before_statement)
    try:
        with ThreadPoolExecutor(max_workers=2) as workers:
            first = workers.submit(persist, "first", 2)
            contender = workers.submit(persist, "contender", contender_calls)
            first_outcome = first.result(timeout=15)
            contender_outcome = contender.result(timeout=15)
    finally:
        event.remove(pg_engine, "before_cursor_execute", before_statement)
    assert first_outcome == "committed"
    assert control_points
    if contender_calls == 1:
        assert contender_outcome in {"HeadConflict", "CheckpointMismatch"}, contender_outcome
    else:
        assert contender_outcome == "committed", contender_outcome

    with Session(pg_engine) as db:
        store = DocumentAnalysisRunStore(db)
        run = store.get_owned(run_id, owner)
        head = store.get_artifact_head(run_id, owner, "ranking_state")
        encoded = db.get(DocumentAnalysisArtifact, head.artifact_id).payload
        assert encoded["storage_schema_version"] == 2
        assert decode_state(store, run, encoded) == ranking_state(2)
        assert head.revision == run.event_head == 1
        assert db.scalar(select(func.count()).select_from(DocumentRecognitionEvent)) == 1
        blocks = list(db.scalars(select(DocumentAnalysisArtifact).where(
            DocumentAnalysisArtifact.artifact_kind == "state_block",
        )))
        assert len(blocks) > 1
        by_id = {block.artifact_id: block for block in blocks}
        reachable = set()

        def visit(node):
            kind, value = node
            if kind == "ref":
                identity = value["artifact_id"]
                if identity not in reachable:
                    reachable.add(identity)
                    visit(by_id[identity].payload["node"])
            elif kind == "dict":
                for child in value.values():
                    visit(child)
            elif kind in {"items", "chunks"}:
                for child in value:
                    visit(child)

        visit(encoded["root"])
        assert set(by_id) == reachable
        artifact_ids = set(db.scalars(select(DocumentAnalysisArtifact.artifact_id)))
        linked_ids = set(db.scalars(select(DocumentRunArtifact.artifact_id)))
        assert artifact_ids == linked_ids
        # A rolled-back lower-cost snapshot must leave no extra block even when
        # its table rows would have had a run link. The successful state alone
        # must account for the complete block set, and re-encoding is read-only.
        assert encode_state(store, run, token, ranking_state(2)) == encoded
        assert set(db.scalars(select(DocumentAnalysisArtifact.artifact_id))) == artifact_ids
        assert set(db.scalars(select(DocumentRunArtifact.artifact_id))) == linked_ids
        db.rollback()


@pytest.mark.parametrize("failure", [None, "commit_failed", "lease_lost", "ack_lost"])
def test_postgresql_model_dispatch_requires_committed_owner_ack_and_preserves_uncertain_cost(
    pg_engine, tmp_path, failure,
):
    run_id, owner, token = _claim(pg_engine)
    task, context, predicate, _unused = _inputs(tmp_path)
    menu = compile_local_menu(_ontology(), task.subject)
    coordinator = get_ident()
    requesting, completed = Event(), Event()
    dispatched, before_commit_checks = [], []
    state = {
        "version": 1, "recognition_run_id": run_id, "run_fingerprint": FINGERPRINT,
        "lineage_calls": {task.claim_lineage_id: 1},
        "reservations": [{"sequence": 1, "task_id": task.task_id,
                          "lineage_id": task.claim_lineage_id, "stage": "discovery", "ordinal": 1}],
    }

    def durable_reservation():
        with Session(pg_engine) as reader:
            store = DocumentAnalysisRunStore(reader)
            return execution_service._restore_model_call_state(
                reader, store, store.get_owned(run_id, owner), final_fingerprint=FINGERPRINT,
            )

    class Adapter:
        def inspect(self, selected, worker_context, _predicate, _menu):
            assert get_ident() != coordinator
            requesting.set()
            try:
                worker_context.before_model_call("discovery", 1)
                assert durable_reservation() == state
                dispatched.append(selected.task_id)
                return TaskOutcome(
                    semantic_outcome="not_checked", reason_code="no_candidate_observed",
                    reason="Controlled no-model dispatch witness.", model_calls=1,
                )
            finally:
                completed.set()

    with Session(pg_engine, expire_on_commit=False, autoflush=False) as db:
        store = DocumentAnalysisRunStore(db)

        def before_commit(session):
            if session.in_nested_transaction():
                return
            assert get_ident() == coordinator
            assert not dispatched
            assert durable_reservation() == {}
            before_commit_checks.append(True)
            if failure == "commit_failed":
                raise RuntimeError("injected owner commit failure")

        def reserve(stage, ordinal):
            assert get_ident() == coordinator
            assert (stage, ordinal) == ("discovery", 1)
            execution_service._persist_model_call_state(
                db, store, store.get_owned(run_id, owner), token,
                final_fingerprint=FINGERPRINT, state=state,
            )
            assert durable_reservation() == state
            if failure == "ack_lost":
                raise RuntimeError("injected lost durable acknowledgement")

        event.listen(db, "before_commit", before_commit)
        with model_scope(bind=pg_engine, run_id=run_id):
            call = RecognitionCall(Adapter(), task, context, predicate, menu)
        try:
            assert requesting.wait(timeout=3)
            assert not dispatched
            if failure == "lease_lost":
                assert _replace_worker(pg_engine, run_id, owner) != token
            deadline = monotonic() + 5

            def drive():
                while not call.future.done():
                    assert monotonic() < deadline, "worker failed to acknowledge or stop"
                    call.drain(reserve)
                    completed.wait(0.01)
                return call.future.result(timeout=1)

            if failure is None:
                assert drive().model_calls == 1
            else:
                expected = FenceViolation if failure == "lease_lost" else RuntimeError
                with pytest.raises(expected):
                    drive()
                with pytest.raises(expected):
                    call.future.result(timeout=3)
        finally:
            call.close()
            event.remove(db, "before_commit", before_commit)
            db.rollback()
    assert completed.is_set()
    if failure is None:
        assert dispatched == [task.task_id]
    else:
        assert dispatched == []
    assert bool(before_commit_checks) == (failure != "lease_lost")
    assert durable_reservation() == (state if failure in (None, "ack_lost") else {})
