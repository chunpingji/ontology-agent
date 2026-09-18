"""A known unissued reservation can resume once without erasing its gross charge."""

from __future__ import annotations

from copy import deepcopy
from time import perf_counter

import pytest

from app.services.extraction.ontology_guided import semantic_reranker
from app.services.extraction.ontology_guided.ranking_execution import RankingPreparation
from app.services.extraction.ontology_guided.semantic_reranker import (
    RankingPersistenceError,
    RankingPolicy,
    RankingService,
)
from tests.test_extraction.test_semantic_ranking import RankingModel, setup_slot


def paused_before_last_dispatch(tmp_path, monkeypatch):
    arguments = setup_slot(tmp_path)
    clock = [0.0]
    monkeypatch.setattr(semantic_reranker.time, "monotonic", lambda: clock[0])
    policy = RankingPolicy(
        mode="semantic", failure_policy="pause", enable_dense=False,
        pool_size=2, batch_size=1, ranking_timeout=10,
    )
    model, durable = RankingModel(), []

    def persist(snapshot):
        durable.append(deepcopy(snapshot))
        if snapshot["costs"]["input_pairs"] == 4 and not snapshot["dispatch_receipts"]:
            # The owner acknowledges this pre-dispatch reservation after other
            # recognition work has already used the remainder of the deadline.
            clock[0] = 11.0

    preparation = RankingPreparation(
        RankingService(policy, model), task_id="receipt-test", arguments=arguments,
    )
    wall_deadline = perf_counter() + 5
    try:
        while not preparation.future.done():
            assert perf_counter() < wall_deadline, "ranking barrier did not finish"
            preparation.drain(persist)
            preparation.wait(0.01)
        paused = preparation.future.result()
        saved = preparation.service.snapshot()
    finally:
        preparation.close()
    assert paused.reason == "ranking_timeout" and paused.status == "paused"
    assert len(model.calls) == 3
    assert saved["costs"]["model_calls"] == saved["costs"]["input_pairs"] == 4
    assert saved["costs"]["technical_retries"] == 0
    assert set(saved["record_call_counts"].values()) == {2}
    assert len(saved["score_cache"]) == 3
    assert durable[-1]["dispatch_receipts"] == saved["dispatch_receipts"]
    assert [item["status"] for item in saved["dispatch_receipts"]] == ["not_dispatched"]
    return arguments, policy, saved, clock


def assert_same_gross_charges(saved, restored):
    for key in ("request_attempts", "record_call_counts", "slot_costs"):
        assert restored[key] == saved[key]
    for key in ("tokens", "model_calls", "input_pairs", "embedding_inputs", "technical_retries"):
        assert restored["costs"][key] == saved["costs"][key]


def test_owner_barrier_timeout_resumes_exact_unused_reservation_under_frozen_limits(
    tmp_path, monkeypatch,
):
    arguments, policy, saved, clock = paused_before_last_dispatch(tmp_path, monkeypatch)
    clock[0] = 0.0
    model, durable = RankingModel(), []
    restored = RankingService(policy, model, state=saved, before_model_hook=durable.append)
    ready = restored.prepare_next_epoch(**arguments)
    assert ready.status == "ready" and ready.actual_ranking_mode == "semantic"
    assert model.calls == [("score", 1)]
    assert ready.costs["model_calls"] == ready.costs["input_pairs"] == 0
    current = restored.snapshot()
    assert_same_gross_charges(saved, current)
    assert current["policy"] == saved["policy"]
    assert current["model_identity"] == saved["model_identity"]
    assert len(current["score_cache"]) == 4
    assert [item["status"] for item in durable[-1]["dispatch_receipts"]] == [
        "not_dispatched", "dispatch_claimed",
    ]
    committed = restored.commit_epoch(ready)
    replay_model = RankingModel()
    replay = RankingService(policy, replay_model, state=restored.snapshot())
    replay.validate_epoch(committed, **arguments)
    assert replay.commit_epoch(committed) == committed
    assert replay_model.calls == []


def test_repeated_barrier_expiry_appends_receipts_without_more_reservations(tmp_path, monkeypatch):
    arguments, policy, saved, clock = paused_before_last_dispatch(tmp_path, monkeypatch)
    clock[0] = 0.0
    model, durable = RankingModel(), []

    def persist(snapshot):
        durable.append(deepcopy(snapshot))
        if snapshot["dispatch_receipts"][-1]["status"] == "dispatch_claimed":
            clock[0] = 11.0

    service = RankingService(policy, model, state=saved, before_model_hook=persist)
    paused = service.prepare_next_epoch(**arguments)
    assert paused.reason == "ranking_timeout"
    assert model.calls == []
    current = service.snapshot()
    assert_same_gross_charges(saved, current)
    assert [item["status"] for item in current["dispatch_receipts"]] == [
        "not_dispatched", "dispatch_claimed", "not_dispatched",
    ]
    assert durable[-1]["dispatch_receipts"] == current["dispatch_receipts"]
    clock[0] = 0.0
    final_model = RankingModel()
    resumed = RankingService(policy, final_model, state=current)
    assert resumed.prepare_next_epoch(**arguments).status == "ready"
    assert final_model.calls == [("score", 1)]
    assert_same_gross_charges(saved, resumed.snapshot())


def test_crash_after_durable_claim_does_not_invent_a_second_no_dispatch_receipt(
    tmp_path, monkeypatch,
):
    arguments, policy, saved, clock = paused_before_last_dispatch(tmp_path, monkeypatch)
    clock[0] = 0.0
    model, durable = RankingModel(), []

    def crash(snapshot):
        durable.append(deepcopy(snapshot))
        raise RuntimeError("simulated crash after the claim was persisted")

    service = RankingService(policy, model, state=saved, before_model_hook=crash)
    with pytest.raises(RankingPersistenceError):
        service.prepare_next_epoch(**arguments)
    assert model.calls == []
    assert durable[-1]["dispatch_receipts"][-1]["status"] == "dispatch_claimed"
    recovered_model = RankingModel()
    recovered = RankingService(policy, recovered_model, state=durable[-1])
    paused = recovered.prepare_next_epoch(**arguments)
    assert paused.reason == "ranking_call_budget_exhausted"
    assert recovered_model.calls == []
    assert_same_gross_charges(saved, recovered.snapshot())


def test_legacy_snapshot_without_receipts_keeps_ambiguous_spent_reservations(tmp_path, monkeypatch):
    arguments, policy, saved, clock = paused_before_last_dispatch(tmp_path, monkeypatch)
    saved.pop("dispatch_receipts")
    clock[0] = 0.0
    model = RankingModel()
    recovered = RankingService(policy, model, state=saved)
    paused = recovered.prepare_next_epoch(**arguments)
    assert paused.reason == "ranking_call_budget_exhausted"
    assert model.calls == []
    assert recovered.snapshot()["dispatch_receipts"] == []
    assert_same_gross_charges(saved, recovered.snapshot())


def test_response_after_actual_dispatch_is_not_an_unused_reservation(tmp_path, monkeypatch):
    arguments = setup_slot(tmp_path)
    clock = [0.0]
    monkeypatch.setattr(semantic_reranker.time, "monotonic", lambda: clock[0])
    model = RankingModel()
    score_pairs = model.score_pairs

    def late_result(pairs):
        result = score_pairs(pairs)
        if len(model.calls) == 4:
            clock[0] = 11.0
        return result

    model.score_pairs = late_result
    policy = RankingPolicy(
        mode="semantic", failure_policy="pause", enable_dense=False,
        pool_size=2, batch_size=1, ranking_timeout=10, technical_retry_limit=0,
    )
    service = RankingService(policy, model)
    assert service.prepare_next_epoch(**arguments).reason == "ranking_timeout"
    saved = service.snapshot()
    assert saved["dispatch_receipts"] == []
    assert len(model.calls) == 4 and len(saved["score_cache"]) == 3
    clock[0] = 0.0
    recovered_model = RankingModel()
    recovered = RankingService(policy, recovered_model, state=saved)
    assert recovered.prepare_next_epoch(**arguments).reason == "ranking_call_budget_exhausted"
    assert recovered_model.calls == []
    assert_same_gross_charges(saved, recovered.snapshot())


def test_no_dispatch_receipt_cannot_authorize_a_different_exact_input(tmp_path, monkeypatch):
    arguments, policy, saved, clock = paused_before_last_dispatch(tmp_path, monkeypatch)
    clock[0] = 0.0
    saved["dispatch_receipts"][-1]["reservation_identity"] = "different-input"
    model = RankingModel()
    recovered = RankingService(policy, model, state=saved)
    assert recovered.prepare_next_epoch(**arguments).reason == "ranking_call_budget_exhausted"
    assert model.calls == []
    assert_same_gross_charges(saved, recovered.snapshot())


@pytest.mark.parametrize("invalid_transition", [
    "claim_without_receipt", "duplicate_receipt", "claim_other_input", "claim_other_attempt",
])
def test_restored_dispatch_receipts_require_a_valid_reservation_chain(
    tmp_path, monkeypatch, invalid_transition,
):
    _arguments, policy, saved, _clock = paused_before_last_dispatch(tmp_path, monkeypatch)
    receipt = saved["dispatch_receipts"][0]
    if invalid_transition == "claim_without_receipt":
        receipt.update(status="dispatch_claimed", reason=None)
    else:
        next_receipt = deepcopy(receipt)
        next_receipt["sequence"] = 2
        if invalid_transition != "duplicate_receipt":
            next_receipt.update(status="dispatch_claimed", reason=None)
        if invalid_transition == "claim_other_input":
            next_receipt["reservation_identity"] = "other-input"
        if invalid_transition == "claim_other_attempt":
            next_receipt["reservation_attempt"] += 1
            saved["request_attempts"][receipt["request_key"]] += 1
        saved["dispatch_receipts"].append(next_receipt)
    with pytest.raises(ValueError, match="dispatch receipt"):
        RankingService(policy, RankingModel(), state=saved)
