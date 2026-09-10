"""Operational budget suspension preserves history while stopping budget accounting."""

from __future__ import annotations

from copy import deepcopy

import pytest

from app.services.extraction.ontology_guided.executor import OntologyGuidedExecutor
from app.services.extraction.ontology_guided.semantic_reranker import (
    RankingPersistenceError,
    RankingPolicy,
    RankingService,
)
from tests.test_extraction.test_semantic_ranking import RankingModel, setup_slot
from tests.test_extraction.test_semantic_ranking_dispatch_receipts import (
    paused_before_last_dispatch,
)
from tests.test_extraction.test_semantic_ranking_execution import (
    SECOND,
    ControlledRanking,
    NoFactsAdapter,
    fixture,
)
from tests.test_extraction.test_semantic_ranking_restore_priority import (
    durable_state,
    slot_arguments,
)

ACCOUNTING_KEYS = (
    "costs", "slot_costs", "record_call_counts", "request_attempts",
    "dispatch_receipts", "model_observations",
)


class ObservedRanking(RankingModel):
    def __init__(self):
        super().__init__()
        self.observations = []

    def score_pairs(self, pairs):
        result = super().score_pairs(pairs)
        self.observations.append({
            "operation": "score_pairs", "input_count": len(pairs),
            "sequence": len(self.observations) + 1, "input_tokens": len(pairs) * 2,
        })
        return result


def assert_accounting_unchanged(before, after):
    assert {key: after[key] for key in ACCOUNTING_KEYS} == {
        key: before[key] for key in ACCOUNTING_KEYS
    }


def test_disable_resumes_old_record_pause_reuses_cache_and_preserves_every_budget_value(tmp_path):
    args = setup_slot(tmp_path)
    policy = RankingPolicy(
        mode="semantic", failure_policy="pause", enable_dense=False,
        pool_size=2, batch_size=2, max_model_calls_per_record=1,
    )
    model = ObservedRanking()
    service = RankingService(policy, model)
    paused = service.prepare_next_epoch(**args)
    assert paused.reason == "ranking_call_budget_exhausted"
    saved = service.snapshot()
    assert len(saved["score_cache"]) == len(saved["model_observations"]) == 1
    assert saved["costs"]["model_calls"] == 1
    disabled = RankingService(policy, model, state=saved, budget_enabled=False)
    ready = disabled.prepare_next_epoch(**args)
    current = disabled.snapshot()
    assert ready.status == "ready" and ready.actual_ranking_mode == "semantic"
    assert ready.policy_hash == paused.policy_hash and ready.model_identity == paused.model_identity
    assert current["policy"] == saved["policy"] and current["budget_enabled"] is False
    assert current["paused_attempts"] == [paused.model_dump(mode="json")]
    assert ready.costs["budget_accounted"] is False
    assert all(value == 0 for key, value in ready.costs.items() if key != "budget_accounted")
    assert len(current["score_cache"]) == 2
    assert len(model.calls) == 2  # Only the missing second intent actually executes.
    assert_accounting_unchanged(saved, current)

    committed = disabled.commit_epoch(ready)
    enabled = RankingService(policy, model, state=disabled.snapshot(), budget_enabled=True)
    assert enabled.commit_epoch(committed) == committed
    enabled.validate_epoch(committed, **args)
    assert len(model.calls) == 2  # Replaying the completed epoch needs no model call.
    next_epoch = enabled.prepare_next_epoch(**args)
    assert next_epoch.reason == "ranking_call_budget_exhausted"
    assert enabled.costs["model_calls"] == saved["costs"]["model_calls"] + 1
    # Off-period observations are never imported when accounting resumes.
    assert [item["sequence"] for item in enabled.snapshot()["model_observations"]] == [1, 3]


@pytest.mark.parametrize("limit", ["max_ranking_tokens_per_slot", "max_ranking_tokens_per_run"])
def test_reenabled_token_limit_uses_pre_disable_accumulated_amount(tmp_path, limit):
    args = setup_slot(tmp_path)
    policy = RankingPolicy(
        mode="semantic", failure_policy="pause", enable_dense=False,
        pool_size=2, batch_size=2, **{limit: 4},
    )
    model = RankingModel()
    model.count_tokens = lambda _text: 1
    enabled = RankingService(policy, model)
    paused = enabled.prepare_next_epoch(**args)
    assert paused.reason == "ranking_token_budget_exhausted"
    saved = enabled.snapshot()
    assert saved["costs"]["tokens"] == 4
    disabled = RankingService(policy, model, state=saved, budget_enabled=False)
    ready = disabled.prepare_next_epoch(**args)
    assert ready.status == "ready"
    disabled.commit_epoch(ready)
    assert_accounting_unchanged(saved, disabled.snapshot())
    calls_before_enable = len(model.calls)
    reenabled = RankingService(policy, model, state=disabled.snapshot(), budget_enabled=True)
    assert reenabled.prepare_next_epoch(**args).reason == "ranking_token_budget_exhausted"
    assert len(model.calls) == calls_before_enable
    assert reenabled.costs["tokens"] == 4


def test_disable_bypasses_spent_request_attempt_allowance_without_refunding_or_charging(tmp_path):
    args = setup_slot(tmp_path)
    policy = RankingPolicy(
        mode="semantic", failure_policy="pause", enable_dense=False,
        pool_size=2, batch_size=2, technical_retry_limit=0,
    )

    class Broken(RankingModel):
        def score_pairs(self, pairs):
            super().score_pairs(pairs)
            raise RuntimeError("request failed after dispatch")

    failed = RankingService(policy, Broken())
    failed.prepare_next_epoch(**args)
    spent = RankingService(policy, RankingModel(), state=failed.snapshot())
    assert spent.prepare_next_epoch(**args).reason == "ranking_call_budget_exhausted"
    saved = spent.snapshot()
    model = RankingModel()
    disabled = RankingService(policy, model, state=saved, budget_enabled=False)
    assert disabled.prepare_next_epoch(**args).status == "ready"
    assert len(model.calls) == 2
    assert_accounting_unchanged(saved, disabled.snapshot())


def test_disabled_retries_remain_bounded_without_collecting_budget_costs(tmp_path):
    args = setup_slot(tmp_path)

    class Broken(ObservedRanking):
        def score_pairs(self, pairs):
            super().score_pairs(pairs)
            raise RuntimeError("model unavailable")

    policy = RankingPolicy(
        mode="semantic", failure_policy="pause", enable_dense=False,
        technical_retry_limit=2, max_model_calls_per_record=1,
        max_ranking_tokens_per_slot=1, max_ranking_tokens_per_run=1,
    )
    model = Broken()
    service = RankingService(policy, model, budget_enabled=False)
    saved = service.snapshot()
    paused = service.prepare_next_epoch(**args)
    assert paused.status == "paused" and paused.reason == "ranking_technical_failure:RuntimeError"
    assert len(model.calls) == 3
    assert service.prepare_next_epoch(**args) is paused
    assert len(model.calls) == 3
    assert_accounting_unchanged(saved, service.snapshot())
    assert model.observations and service.snapshot()["model_observations"] == []


def test_disabled_budget_keeps_complete_input_limits(tmp_path):
    args = setup_slot(tmp_path)
    model = RankingModel()
    service = RankingService(RankingPolicy(
        mode="semantic", failure_policy="pause", max_tokens_per_pair=1,
    ), model, budget_enabled=False)
    saved = service.snapshot()
    epoch = service.prepare_next_epoch(**args)
    assert model.calls == []
    assert epoch.reason == "no_rerankable_records"
    assert epoch.actual_ranking_mode == "deterministic"
    assert all(item["view_status"] == "not_rerankable" for item in epoch.observations)
    assert_accounting_unchanged(saved, service.snapshot())


def test_disabled_budget_keeps_epoch_timeout_and_does_not_write_dispatch_receipts(
    tmp_path, monkeypatch,
):
    args, policy, saved, clock = paused_before_last_dispatch(tmp_path, monkeypatch)
    clock[0] = 0.0
    model = RankingModel()

    def slow_acknowledgement(_snapshot):
        clock[0] = 11.0

    service = RankingService(
        policy, model, state=saved, budget_enabled=False,
        before_model_hook=slow_acknowledgement,
    )
    assert service.prepare_next_epoch(**args).reason == "ranking_timeout"
    assert model.calls == []
    assert_accounting_unchanged(saved, service.snapshot())


def test_disabled_budget_still_requires_owned_durable_dispatch_barrier(tmp_path):
    args = setup_slot(tmp_path)
    model = RankingModel()

    def reject(_snapshot):
        raise RuntimeError("execution fence lost")

    service = RankingService(
        RankingPolicy(mode="semantic"), model,
        budget_enabled=False, before_model_hook=reject,
    )
    saved = service.snapshot()
    with pytest.raises(RankingPersistenceError):
        service.prepare_next_epoch(**args)
    assert model.calls == []
    assert_accounting_unchanged(saved, service.snapshot())


def test_executor_and_async_preparation_use_explicit_control_over_old_snapshot(tmp_path):
    ontology, arguments = fixture(tmp_path)
    policy = RankingPolicy(
        mode="semantic", failure_policy="pause", max_ranking_tokens_per_run=1,
    )
    prior = RankingService(policy, ControlledRanking())
    paused = prior.prepare_next_epoch(**slot_arguments(ontology, arguments, SECOND))
    assert paused.reason == "ranking_token_budget_exhausted"
    saved = prior.snapshot()
    model = ControlledRanking()
    result = OntologyGuidedExecutor(
        ontology=ontology, engine=object(), adapter=NoFactsAdapter(), max_tasks=100,
        ranking_service=RankingService(policy, model, budget_enabled=False),
    ).run(**arguments, ranking_state=durable_state(arguments, prior))
    current = result.ranking_state["service"]
    assert model.calls and result.graph.progress.completion == "in_scope_complete"
    assert current["budget_enabled"] is False
    assert all(epoch["costs"]["budget_accounted"] is False for epoch in current["epochs"])
    assert_accounting_unchanged(saved, current)


def test_budget_control_defaults_to_enabled_and_requires_a_new_service_to_change():
    policy = RankingPolicy()
    original = RankingService(policy)
    legacy = deepcopy(original.snapshot())
    legacy.pop("budget_enabled")
    assert RankingService(policy, state=legacy).budget_enabled is True
    disabled = RankingService(policy, state=legacy, budget_enabled=False)
    assert RankingService(policy, state=disabled.snapshot()).budget_enabled is False
    reenabled = RankingService(policy, state=disabled.snapshot(), budget_enabled=True)
    assert reenabled.budget_enabled is True
    with pytest.raises(AttributeError):
        disabled.budget_enabled = True
    with pytest.raises(ValueError, match="must be boolean"):
        RankingService(policy, budget_enabled="false")
    assert disabled.snapshot()["policy"] == original.snapshot()["policy"]
