"""Ranking write barriers can advance during HTTP waits without moving the Session."""

import asyncio
from threading import Event, get_ident

import httpx
import pytest

from app.services.extraction.ontology_guided.executor import OntologyGuidedExecutor, TaskOutcome
from app.services.extraction.ontology_guided.semantic_reranker import RankingPolicy, RankingService
from app.services.llm import local_client
from app.services.llm.local_client import LocalModelClient
from app.services.llm.model_runtime import ModelWaitFailure, model_scope, runtime

from .test_model_scheduler import invoke, reply, rows
from .test_semantic_ranking_execution import FIRST, SECOND, ControlledRanking, fixture


def test_failed_wait_barrier_cancels_http_and_propagates_without_retry(
    isolated_model_scheduler, monkeypatch,
):
    bind = isolated_model_scheduler()
    monkeypatch.setattr(local_client, "POLL_SECONDS", 0.01)
    sent, closed = [], []

    async def transport(_request):
        sent.append(True)
        try:
            await asyncio.sleep(10)
        finally:
            assert rows(bind)[-1].status == "running"
            closed.append(True)

    def barrier():
        if sent:
            raise ModelWaitFailure("durability_failed")

    client = LocalModelClient("http://model.test/v1", "test", httpx.MockTransport(transport))
    with model_scope(bind=bind, on_model_wait=barrier):
        with pytest.raises(ModelWaitFailure, match="durability_failed"):
            invoke(client, timeout_s=2, total_timeout_s=2)
    assert sent == closed == [True]
    assert len(rows(bind)) == 1
    assert rows(bind)[0].status == "cancelled"


def test_multiple_ranking_batches_finish_while_recognition_waits_on_owner_thread(
    tmp_path, isolated_model_scheduler, monkeypatch,
):
    bind = isolated_model_scheduler()
    monkeypatch.setattr(local_client, "POLL_SECONDS", 0.01)
    ontology, arguments = fixture(tmp_path)
    owner = get_ident()
    persistence_threads = []
    reservation_threads = []
    reservations = []
    completed = Event()

    class Ranking(ControlledRanking):
        second_scores = 0

        def score_pairs(self, pairs):
            result = super().score_pairs(pairs)
            if any(f'"iri":"{SECOND}"' in query for query, _text in pairs):
                self.second_scores += 1
                if self.second_scores >= 2:
                    completed.set()
            return result

    ranking = Ranking()

    async def transport(_request):
        # Only the coordinator can drain ranking barriers while the separate
        # recognition worker waits for HTTP. Completion must not depend on it.
        while not completed.is_set():
            await asyncio.sleep(0.01)
        return httpx.Response(200, json=reply())

    client = LocalModelClient("http://model.test/v1", "test", httpx.MockTransport(transport))

    class Adapter:
        model_identity = "wait-barrier-test"
        probed = False

        def inspect(self, task, context, predicate, menu):
            assert get_ident() != owner
            assert runtime.get().get("on_model_wait") is None
            context.before_model_call("recognition_wait_fixture", 1)
            assert any(item["task_id"] == task.task_id
                       for state in reservations for item in state["reservations"])
            if predicate.iri == FIRST and ranking.second_preparing.is_set() and not self.probed:
                self.probed = True
                assert invoke(client, timeout_s=3, total_timeout_s=3) == {"ok": True}
                assert completed.is_set()
            return TaskOutcome(semantic_outcome="not_checked", reason_code="no_candidate_observed",
                               reason="没有提出候选，不代表全文否定。", model_calls=1)

    adapter = Adapter()

    def reserve(state):
        reservation_threads.append(get_ident())
        reservations.append(state)

    with model_scope(bind=bind):
        result = OntologyGuidedExecutor(
            ontology=ontology, engine=object(), adapter=adapter,
            ranking_service=RankingService(RankingPolicy(
                mode="semantic", batch_size=2, ranking_timeout=5), ranking),
        ).run(**arguments, ranking_hook=lambda _state: persistence_threads.append(get_ident()),
              model_call_hook=reserve)
    assert adapter.probed
    assert result.graph.progress.records_examined > 0
    assert set(persistence_threads) == {owner}
    assert set(reservation_threads) == {owner}
    assert len(reservations[-1]["reservations"]) == 24
    assert sum(reservations[-1]["lineage_calls"].values()) == 24
    assert result.graph.progress.completion == "in_scope_complete"
    assert result.ranking_state["service"]["costs"]["model_calls"] == len(ranking.calls)
    assert rows(bind)[0].status == "complete"
