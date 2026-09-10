"""Paused ranking retries retain failed evidence, successful work and spent limits."""

from __future__ import annotations

from collections import Counter
from copy import deepcopy

import pytest
from sqlalchemy.exc import DataError

from app.services.document_analysis import execution as execution_service
from app.services.extraction.ontology_guided import semantic_reranker
from app.services.extraction.ontology_guided.semantic_reranker import RankingPolicy, RankingService
from tests.test_extraction.test_document_analysis_execution_recovery import (
    _claim,
    _create_pending_run,
)
from tests.test_extraction.test_semantic_ranking import RankingModel, setup_slot


def test_tokenizer_admission_failure_retries_after_restore_and_persists_prior_evidence(
    client, db, analyst_headers, tmp_path, monkeypatch,
):
    args = setup_slot(tmp_path)
    run_id = _create_pending_run(
        client, analyst_headers, tmp_path, monkeypatch, key="ranking-paused-retry",
    )
    store, token = _claim(db, run_id)
    run = store.get_owned(run_id, "analyst")
    store.update_stage(
        run_id, "analyst", token, expected_revision=run.revision,
        stage="recognition", run_fingerprint=args["run_fingerprint"],
    )
    db.commit()

    def persist(snapshot, committed_at=None):
        execution_service._persist_ranking_state(
            db, store, store.get_owned(run_id, "analyst"), token,
            final_fingerprint=args["run_fingerprint"],
            state={
                "recognition_run_id": run_id,
                "run_fingerprint": args["run_fingerprint"],
                "service": snapshot,
                "committed_at": committed_at or {},
                "discarded_epochs": [],
            },
        )

    broken = RankingModel()

    def failed_admission(_text):
        raise DataError("request admission", {}, ValueError("invalid run id length"))

    broken.count_tokens = failed_admission
    policy = RankingPolicy(mode="semantic", failure_policy="pause", batch_size=2)
    service = RankingService(policy, broken)
    paused = service.prepare_next_epoch(**args)
    assert paused.status == "paused"
    assert paused.reason == "ranking_technical_failure:DataError"
    assert paused.costs["model_calls"] == paused.costs["tokens"] == 0
    assert service.prepare_next_epoch(**args) is paused
    assert broken.calls == []
    persist(service.snapshot())

    saved = execution_service._restore_ranking_state(
        db, store, store.get_owned(run_id, "analyst"),
        final_fingerprint=args["run_fingerprint"],
    )["service"]
    fixed = RankingModel()
    recovered = RankingService(policy, fixed, state=saved, before_model_hook=persist)
    ready = recovered.prepare_next_epoch(**args)
    assert ready.status == "ready" and ready.actual_ranking_mode == "semantic"
    assert recovered.snapshot()["paused_attempts"] == [paused.model_dump(mode="json")]
    assert fixed.calls
    committed = recovered.commit_epoch(ready)
    persist(recovered.snapshot(), {committed.epoch_id: 0})
    durable = execution_service._restore_ranking_state(
        db, store, store.get_owned(run_id, "analyst"),
        final_fingerprint=args["run_fingerprint"],
    )["service"]
    assert durable == recovered.snapshot()
    replay = RankingModel()
    restored = RankingService(policy, replay, state=durable)
    assert restored.prepare_next_epoch(**args) is None
    assert replay.calls == []


def test_partial_request_timeout_retries_only_unfinished_pairs_and_preserves_costs(
    tmp_path, monkeypatch,
):
    args = setup_slot(tmp_path)
    clock = [0.0]
    monkeypatch.setattr(semantic_reranker.time, "monotonic", lambda: clock[0])

    class TimedFailure(RankingModel):
        def __init__(self, fail=False):
            super().__init__()
            self.fail = fail
            self.pairs = []

        def score_pairs(self, pairs):
            self.pairs.extend(pairs)
            if self.fail and len(self.pairs) == 2:
                clock[0] = 11.0
                raise RuntimeError("temporary request failure")
            return super().score_pairs(pairs)

    policy = RankingPolicy(
        mode="semantic", failure_policy="pause", batch_size=1,
        ranking_timeout=10, technical_retry_limit=1, max_model_calls_per_record=4,
    )
    broken = TimedFailure(fail=True)
    service = RankingService(policy, broken)
    paused = service.prepare_next_epoch(**args)
    assert paused.status == "paused" and paused.reason == "ranking_timeout"
    saved = service.snapshot()
    assert len(saved["score_cache"]) == 1
    assert saved["cache"] and saved["token_cache"]
    assert saved["record_call_counts"] and saved["request_attempts"]
    clock[0] = 0.0
    fixed = TimedFailure()
    recovered = RankingService(policy, fixed, state=saved)
    ready = recovered.prepare_next_epoch(**args)
    assert ready.status == "ready"
    assert all(kind == "score" for kind, _ in fixed.calls)
    all_pairs = Counter([*broken.pairs, *fixed.pairs])
    assert all_pairs[broken.pairs[0]] == 1
    assert all_pairs[broken.pairs[1]] == 2
    assert set(all_pairs.values()) == {1, 2}
    current = recovered.snapshot()
    assert current["paused_attempts"] == [paused.model_dump(mode="json")]
    assert current["costs"]["tokens"] > saved["costs"]["tokens"]
    assert current["costs"]["model_calls"] == (
        saved["costs"]["model_calls"] + len(fixed.calls)
    )
    assert current["costs"]["technical_retries"] == 1
    for key in ("slot_costs", "request_attempts", "record_call_counts"):
        assert all(current[key][item] >= value for item, value in saved[key].items())
    assert all(current["cache"][key] == value for key, value in saved["cache"].items())
    assert all(current["score_cache"][key] == value
               for key, value in saved["score_cache"].items())


@pytest.mark.parametrize("limit_field", [
    "max_ranking_tokens_per_slot", "max_ranking_tokens_per_run",
])
def test_spent_token_budget_does_not_reset_when_paused_state_is_restored(tmp_path, limit_field):
    args = setup_slot(tmp_path)
    model = RankingModel()
    model.count_tokens = lambda _text: 1
    policy = RankingPolicy(
        mode="semantic", failure_policy="pause", batch_size=1, **{limit_field: 1},
    )
    service = RankingService(policy, model)
    paused = service.prepare_next_epoch(**args)
    assert paused.status == "paused" and paused.reason == "ranking_token_budget_exhausted"
    saved = service.snapshot()
    assert saved["costs"]["tokens"] == saved["costs"]["model_calls"] == 1
    assert saved["cache"]
    fixed = RankingModel()
    recovered = RankingService(policy, fixed, state=saved)
    assert recovered.prepare_next_epoch(**args) == paused
    assert recovered.snapshot() == saved
    assert fixed.calls == []


@pytest.mark.parametrize("incomplete", [False, True])
def test_failed_or_partial_batch_is_not_cached_and_exhausted_retries_do_not_reset(
    tmp_path, incomplete,
):
    args = setup_slot(tmp_path)
    model = RankingModel()
    pair_calls = []

    def score(pairs):
        pair_calls.append(deepcopy(pairs))
        if len(pair_calls) == 1:
            return [1.0 for _ in pairs]
        if incomplete:
            return []
        raise RuntimeError("request failed after dispatch")

    model.score_pairs = score
    policy = RankingPolicy(
        mode="semantic", failure_policy="pause", enable_dense=False,
        batch_size=1, technical_retry_limit=0,
    )
    service = RankingService(policy, model)
    paused = service.prepare_next_epoch(**args)
    assert paused.status == "paused" and len(pair_calls) == 2
    saved = service.snapshot()
    assert len(saved["score_cache"]) == 1
    fixed = RankingModel()
    recovered = RankingService(policy, fixed, state=saved)
    stopped = recovered.prepare_next_epoch(**args)
    assert stopped.status == "paused" and stopped.reason == "ranking_call_budget_exhausted"
    assert fixed.calls == []
    current = recovered.snapshot()
    assert current["score_cache"] == saved["score_cache"]
    for key in ("slot_costs", "request_attempts", "record_call_counts"):
        assert current[key] == saved[key]
    for key in ("tokens", "model_calls", "input_pairs", "technical_retries"):
        assert current["costs"][key] == saved["costs"][key]
    assert current["paused_attempts"] == [paused.model_dump(mode="json")]
    assert RankingService(policy, RankingModel(), state=current).prepare_next_epoch(
        **args,
    ) == stopped


def test_record_call_limit_remains_exhausted_despite_unused_technical_retries(tmp_path):
    args = setup_slot(tmp_path)
    policy = RankingPolicy(
        mode="semantic", failure_policy="pause", enable_dense=False,
        batch_size=1, technical_retry_limit=3, max_model_calls_per_record=1,
    )
    service = RankingService(policy, RankingModel())
    paused = service.prepare_next_epoch(**args)
    assert paused.status == "paused" and paused.reason == "ranking_call_budget_exhausted"
    saved = service.snapshot()
    assert len(saved["score_cache"]) == len(paused.record_ids)
    assert set(saved["record_call_counts"].values()) == {1}
    fixed = RankingModel()
    restored = RankingService(policy, fixed, state=saved)
    assert restored.prepare_next_epoch(**args) == paused
    assert restored.snapshot() == saved
    assert fixed.calls == []
