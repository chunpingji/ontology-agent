"""Recognition isolation and cancellation keep all durable writes on the owner."""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
from threading import Event, get_ident

import httpx
import pytest

from app.services.extraction.annotation_execution import ExecutionLost
from app.services.extraction.ontology_guided.executor import (
    ModelCallPersistenceFailure,
    OntologyGuidedExecutor,
    TaskOutcome,
)
from app.services.llm import local_client
from app.services.llm.local_client import LocalModelClient
from app.services.llm.model_runtime import model_scope, runtime
from tests.test_extraction.test_model_scheduler import invoke, rows
from tests.test_extraction.test_semantic_ranking_execution import FIRST, SECOND, fixture


def _outcome():
    return TaskOutcome(semantic_outcome="not_checked", reason_code="no_candidate_observed",
                       reason="No candidate was proposed; source coverage is retained.",
                       model_calls=1)


def test_worker_has_private_inputs_and_no_owner_session_context(db, tmp_path):
    ontology, arguments = fixture(tmp_path)
    owner = get_ident()
    bind = db.get_bind()
    owner_session = ContextVar("owner_session", default=None)
    token = owner_session.set(db)
    worker_threads, write_threads, batch_threads, calls = [], [], [], []
    forbidden = []

    class Adapter:
        model_identity = "private-inputs-fixture"

        def inspect(self, task, context, predicate, menu):
            worker_threads.append(get_ident())
            assert owner_session.get() is None
            assert "session" not in runtime.get() and "progress" not in runtime.get()
            assert runtime.get().get("on_model_wait") is None
            assert runtime.get()["run_id"] == arguments["recognition_run_id"]
            assert runtime.get()["bind"] is bind
            # None of these local mutations may change coordinator task/menu.
            task.predicate_iri = "urn:worker-only-predicate"
            predicate.label = "worker-only-label"
            menu.properties.clear()
            context.fragments[0].text = "worker-only-text"
            context.before_model_call("fixture_discovery", 1)
            calls.append(task.task_id)
            return _outcome()

    def reserve(state):
        write_threads.append(get_ident())
        assert state["reservations"][-1]["task_id"] not in calls

    def persist(batch):
        batch_threads.append(get_ident())
        assert batch.task.predicate_iri in {FIRST, SECOND}
        assert batch.outcome.model_calls == 1

    try:
        with model_scope(bind=bind, run_id=arguments["recognition_run_id"], session=db,
                         on_model_wait=lambda: forbidden.append("wait"),
                         progress=lambda _: forbidden.append("progress")):
            result = OntologyGuidedExecutor(
                ontology=ontology, engine=object(), adapter=Adapter(), max_tasks=1,
            ).run(**arguments, model_call_hook=reserve, batch_hook=persist)
    finally:
        owner_session.reset(token)
    assert worker_threads and owner not in worker_threads
    assert write_threads == batch_threads == [owner]
    assert forbidden == []
    assert len(result.root_menu.properties) == 2
    assert len(result.model_call_state["reservations"]) == 1
    assert sum(result.model_call_state["lineage_calls"].values()) == 1


def test_failed_reservation_wakes_worker_without_model_dispatch_or_batch(tmp_path):
    ontology, arguments = fixture(tmp_path)
    owner = get_ident()
    dispatched, finished, writes, batches = [], Event(), [], []

    class Adapter:
        model_identity = "reservation-failure-fixture"

        def inspect(self, task, context, predicate, menu):
            try:
                context.before_model_call("fixture_discovery", 1)
                dispatched.append(task.task_id)
                return _outcome()
            finally:
                finished.set()

    def reject(state):
        writes.append((get_ident(), state))
        raise ExecutionLost("execution fence changed before reservation")

    with pytest.raises(ModelCallPersistenceFailure):
        OntologyGuidedExecutor(ontology=ontology, engine=object(), adapter=Adapter()).run(
            **arguments, model_call_hook=reject, batch_hook=batches.append,
        )
    assert finished.is_set() and not dispatched and not batches
    assert len(writes) == 1 and writes[0][0] == owner
    assert len(writes[0][1]["reservations"]) == 1


def test_lost_owner_cancels_actual_http_and_keeps_paid_reservation(
    tmp_path, isolated_model_scheduler, monkeypatch,
):
    bind = isolated_model_scheduler()
    ontology, arguments = fixture(tmp_path)
    monkeypatch.setattr(local_client, "POLL_SECONDS", 0.01)
    started, closed = Event(), Event()
    reservations, batches = [], []
    owner = get_ident()

    async def transport(_request):
        started.set()
        try:
            await asyncio.sleep(10)
        finally:
            closed.set()

    client = LocalModelClient("http://model.test/v1", "test", httpx.MockTransport(transport))

    class Adapter:
        model_identity = "lost-owner-http-fixture"

        def inspect(self, task, context, predicate, menu):
            assert get_ident() != owner
            context.before_model_call("fixture_discovery", 1)
            invoke(client, timeout_s=5, total_timeout_s=5)
            pytest.fail("a lost execution must never receive a model result")

    def control(boundary):
        assert get_ident() == owner
        if boundary == "model_wait" and started.is_set():
            raise ExecutionLost("execution lease was lost")
        return True

    def reserve(state):
        assert get_ident() == owner
        reservations.append(state)

    with model_scope(bind=bind):
        with pytest.raises(ExecutionLost, match="lease was lost"):
            OntologyGuidedExecutor(
                ontology=ontology, engine=object(), adapter=Adapter(), progress_hook=control,
            ).run(**arguments, model_call_hook=reserve, batch_hook=batches.append)
    assert started.is_set() and closed.is_set()
    assert len(rows(bind)) == 1 and rows(bind)[0].status == "cancelled"
    assert len(reservations) == 1 and not batches
    assert sum(reservations[0]["lineage_calls"].values()) == 1
