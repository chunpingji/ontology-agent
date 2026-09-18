"""Restored run-level ranking pauses precede unrelated scheduler opportunities."""

from __future__ import annotations

from copy import deepcopy

import pytest

from app.services.extraction.ontology_guided import semantic_reranker
from app.services.extraction.ontology_guided.contracts import (
    RetrievalPlan,
    SubjectRef,
    VersionedRef,
)
from app.services.extraction.ontology_guided.executor import OntologyGuidedExecutor
from app.services.extraction.ontology_guided.records import RecordIndex
from app.services.extraction.ontology_guided.retrieval import plan_slot
from app.services.extraction.ontology_guided.semantic_reranker import RankingPolicy, RankingService
from tests.test_extraction.test_semantic_ranking_execution import (
    FIRST,
    ROOT,
    SECOND,
    ControlledRanking,
    NoFactsAdapter,
    SimulatedCrash,
    fixture,
    run,
)


def slot_arguments(ontology, arguments, predicate_iri, *, orphan=False):
    root = OntologyGuidedExecutor.root_node(
        recognition_run_id=arguments["recognition_run_id"],
        document_hash=arguments["ir"].document_hash,
        root_class_iri=ROOT, root_class_label="报告", filename=arguments["filename"],
    )
    node = (
        root.model_copy(update={"entity_id": "obsolete-subject", "root": False})
        if orphan else root
    )
    subject = SubjectRef(
        entity_id=node.entity_id, revision=node.revision, class_iri=ROOT,
        is_document_root=node.root,
    )
    predicate = next(
        item for item in ontology.classes[ROOT].declared_properties if item.iri == predicate_iri
    )
    index = RecordIndex(arguments["ir"])
    return dict(
        plan=plan_slot(subject, predicate, index, arguments["metadata"],
                       ontology_hash=ontology.ontology_hash),
        index=index, metadata=arguments["metadata"], subject_node=node, predicate=predicate,
        run_fingerprint=arguments["run_fingerprint"],
        root_ref=VersionedRef(id=root.entity_id, revision=root.revision),
        root_class_iri=ROOT, permission_scope=arguments["recognition_run_id"],
    )


def durable_state(arguments, service, *, committed_at=None):
    return {
        "recognition_run_id": arguments["recognition_run_id"],
        "run_fingerprint": arguments["run_fingerprint"],
        "service": service.snapshot(), "committed_at": committed_at or {},
        "discarded_epochs": [],
    }


def test_checkpoint_replay_stops_at_existing_budget_pause_without_new_model_calls(tmp_path):
    ontology, arguments = fixture(tmp_path)
    policy = RankingPolicy(
        mode="semantic", failure_policy="pause", enable_dense=False,
        pool_size=2, batch_size=2,
    )
    initial = RankingService(policy, ControlledRanking())
    slots = {iri: slot_arguments(ontology, arguments, iri) for iri in (FIRST, SECOND)}
    for slot in slots.values():
        initial.commit_epoch(initial.prepare_next_epoch(**slot))
    initial_state = durable_state(
        arguments, initial, committed_at={epoch.epoch_id: 0 for epoch in initial.epochs},
    )
    batches = []

    def checkpoint(batch):
        batches.append(batch)
        if len(batches) == 2:
            raise SimulatedCrash("checkpoint before the next scheduler opportunity")

    with pytest.raises(SimulatedCrash):
        run(ontology, arguments, NoFactsAdapter(), ControlledRanking(), policy,
            ranking_state=initial_state, batch_hook=checkpoint)
    saved_checkpoint = batches[-1].model_dump(mode="json")
    assert [batch.task.predicate_iri for batch in batches] == [FIRST, SECOND]

    class FailOnce(ControlledRanking):
        def score_pairs(self, pairs):
            result = super().score_pairs(pairs)
            if len(self.calls) == 1:
                raise RuntimeError("first request failed after dispatch")
            return result

    paused_service = RankingService(policy, FailOnce(), state=initial.snapshot())
    second_slot = dict(slots[SECOND])
    second_slot["plan"] = RetrievalPlan.model_validate(
        saved_checkpoint["recall_ledger"][second_slot["plan"].plan_id]
    )
    paused = paused_service.prepare_next_epoch(**second_slot)
    assert paused.reason == "ranking_call_budget_exhausted"
    assert paused.status == "paused"
    state = durable_state(arguments, paused_service, committed_at=initial_state["committed_at"])
    ranking_model, recognition = ControlledRanking(), NoFactsAdapter()
    resumed = run(
        ontology, arguments, recognition, ranking_model, policy,
        resume_state=saved_checkpoint, ranking_state=state,
    )
    assert resumed.graph.progress.stop_reason == "ranking_paused"
    assert resumed.graph.progress.tasks_attempted == 2
    assert ranking_model.calls == recognition.calls == []
    assert resumed.ranking_state["service"]["costs"] == state["service"]["costs"]
    assert resumed.ranking_state["service"]["pending_epochs"] == [paused.model_dump(mode="json")]


def test_retryable_technical_pause_finishes_before_new_recognition_or_other_slot(
    tmp_path, monkeypatch,
):
    ontology, arguments = fixture(tmp_path)
    clock = [0.0]
    monkeypatch.setattr(semantic_reranker.time, "monotonic", lambda: clock[0])

    class LateRanking(ControlledRanking):
        def score_pairs(self, pairs):
            result = super().score_pairs(pairs)
            clock[0] = 11.0
            return result

    policy = RankingPolicy(
        mode="semantic", failure_policy="pause", enable_dense=False,
        ranking_timeout=10, max_model_calls_per_record=4,
    )
    service = RankingService(policy, LateRanking())
    paused = service.prepare_next_epoch(**slot_arguments(ontology, arguments, SECOND))
    assert paused.reason == "ranking_timeout"
    state = durable_state(arguments, service)
    clock[0] = 0.0
    scored_slots, committed = [], []

    class ResumedRanking(ControlledRanking):
        def score_pairs(self, pairs):
            scored_slots.append(SECOND if f'"iri":"{SECOND}"' in pairs[0][0] else FIRST)
            return super().score_pairs(pairs)

    class WatchingRecognition(NoFactsAdapter):
        def inspect(self, task, context, predicate, menu):
            assert paused.plan_id in committed, "recognition bypassed the restored ranking pause"
            return super().inspect(task, context, predicate, menu)

    def persist(current):
        committed[:] = [item["plan_id"] for item in current["service"]["epochs"]]

    recognition = WatchingRecognition()
    result = run(
        ontology, arguments, recognition, ResumedRanking(), policy,
        ranking_state=state, ranking_hook=persist,
    )
    assert scored_slots[0] == SECOND
    assert recognition.calls
    assert result.graph.progress.completion == "in_scope_complete"
    assert result.ranking_state["service"]["paused_attempts"] == [paused.model_dump(mode="json")]


def test_obsolete_subject_pause_is_archived_without_blocking_current_subjects(tmp_path):
    ontology, arguments = fixture(tmp_path)

    class UnavailableRanking(ControlledRanking):
        def count_tokens(self, text):
            raise ValueError("ranking_call_budget_exhausted")

    policy = RankingPolicy(mode="semantic", failure_policy="pause", enable_dense=False)
    service = RankingService(policy, UnavailableRanking())
    paused = service.prepare_next_epoch(**slot_arguments(ontology, arguments, SECOND, orphan=True))
    assert paused.status == "paused"
    state = durable_state(arguments, service)
    result = run(ontology, arguments, NoFactsAdapter(), ControlledRanking(), policy,
                 ranking_state=state)
    assert result.graph.progress.completion == "in_scope_complete"
    assert result.ranking_state["service"]["pending_epochs"] == []
    assert result.ranking_state["discarded_epochs"] == [paused.model_dump(mode="json")]


def test_active_pending_dependencies_are_validated_before_any_new_model_call(tmp_path):
    ontology, arguments = fixture(tmp_path)
    policy = RankingPolicy(mode="semantic", failure_policy="pause", max_ranking_tokens_per_run=1)
    service = RankingService(policy, ControlledRanking())
    service.prepare_next_epoch(**slot_arguments(ontology, arguments, SECOND))
    state = deepcopy(durable_state(arguments, service))
    state["service"]["pending_epochs"][0]["query_dependency_hash"] = "stale-query"
    model, recognition = ControlledRanking(), NoFactsAdapter()
    with pytest.raises(ValueError, match="dependencies or permission scope changed"):
        run(ontology, arguments, recognition, model, policy, ranking_state=state)
    assert model.calls == recognition.calls == []


def test_later_restored_budget_pause_is_not_bypassed_after_first_pause_recovers(tmp_path):
    ontology, arguments = fixture(tmp_path)

    class PausedTokenizer(ControlledRanking):
        def count_tokens(self, text):
            if f'"iri":"{SECOND}"' in text:
                raise ValueError("ranking_timeout")
            if f'"iri":"{FIRST}"' in text:
                raise ValueError("ranking_call_budget_exhausted")
            return super().count_tokens(text)

    policy = RankingPolicy(mode="semantic", failure_policy="pause", enable_dense=False)
    service = RankingService(policy, PausedTokenizer())
    second = service.prepare_next_epoch(**slot_arguments(ontology, arguments, SECOND))
    first = service.prepare_next_epoch(**slot_arguments(ontology, arguments, FIRST))
    assert second.reason == "ranking_timeout"
    assert first.reason == "ranking_call_budget_exhausted"
    scored_slots = []

    class WatchingRanking(ControlledRanking):
        def score_pairs(self, pairs):
            scored_slots.append(SECOND if f'"iri":"{SECOND}"' in pairs[0][0] else FIRST)
            return super().score_pairs(pairs)

    recognition = NoFactsAdapter()
    result = run(
        ontology, arguments, recognition, WatchingRanking(), policy,
        ranking_state=durable_state(arguments, service),
    )
    assert result.graph.progress.stop_reason == "ranking_paused"
    assert result.graph.progress.tasks_attempted == 0
    assert recognition.calls == []
    assert scored_slots and set(scored_slots) == {SECOND}
    assert result.ranking_state["service"]["pending_epochs"] == [first.model_dump(mode="json")]
