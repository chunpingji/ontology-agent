"""Exact batching, immutable checkpoints and versioned retry accounting."""

from __future__ import annotations

import json
import sys
from collections import Counter
from copy import deepcopy
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.model_request import LocalModelRequest
from app.services.extraction.ontology_guided.retrieval import plan_slot
from app.services.extraction.ontology_guided.retrieval_views import build_retrieval_views
from app.services.extraction.ontology_guided.semantic_reranker import (
    RankingEpoch,
    RankingPolicy,
    RankingService,
)
from app.services.llm.model_runtime import model_scope
from app.services.llm.semantic_ranking import (
    LocalSemanticRanking,
    _worker_operation,
    configured_ranking_service,
)
from tests.test_extraction.test_semantic_ranking import RankingModel, setup_slot
from tests.test_extraction.test_semantic_ranking_adapter import _config


def _counting_worker(connection, config):
    try:
        while True:
            operation, inputs = connection.recv()
            assert operation == "count_tokens_batch"
            connection.send({"result": [len(text.encode()) + 2 for text in inputs],
                             "input_tokens": 0})
    except (EOFError, BrokenPipeError):
        pass
    finally:
        connection.close()


def test_batch_tokenization_matches_scalars_with_complete_inputs_and_special_tokens(monkeypatch):
    class Tokenizer:
        def __init__(self, byte_tokens):
            self.byte_tokens = byte_tokens

        def __call__(self, inputs, **kwargs):
            assert kwargs["truncation"] is False and kwargs["add_special_tokens"] is True

            def encode(text):
                return [0, *list(text.encode() if self.byte_tokens else text), 1]

            if isinstance(inputs, list):
                assert kwargs["padding"] is False
                return {"input_ids": [encode(text) for text in inputs]}
            return {"input_ids": encode(inputs)}

    monkeypatch.setitem(sys.modules, "transformers", SimpleNamespace(AutoTokenizer=None))
    tokenizers = {"embedding": Tokenizer(False), "reranker": Tokenizer(True)}
    texts = ["", "中文与🙂", "same", "same", "long" * 2000]
    scalars = [_worker_operation("count_tokens", text, {"mode": "rerank"}, {}, tokenizers)[0]
               for text in texts]
    batch, tokens = _worker_operation(
        "count_tokens_batch", texts, {"mode": "rerank"}, {}, tokenizers,
    )
    assert batch == scalars == [len(text.encode()) + 2 for text in texts]
    assert tokens == 0  # Tokenization itself is not another inference charge.


def test_unique_token_batches_reduce_real_scheduler_admissions(tmp_path, isolated_model_scheduler):
    bind = isolated_model_scheduler()
    model = LocalSemanticRanking(_config(tmp_path), worker_target=_counting_worker)
    texts = ["a", "中文", "a", "", "中文"]
    try:
        with model_scope(bind=bind):
            assert model.count_tokens_batch(texts) == [len(text.encode()) + 2 for text in texts]
            assert model.count_tokens_batch([]) == []
        with Session(bind) as db:
            requests = list(db.scalars(select(LocalModelRequest)))
        assert len(requests) == 2  # ceil(3 unique inputs / batch size 2)
        assert sorted(request.metrics["input_count"] for request in requests) == [1, 2]
        assert all(request.stage == "ranking_count_tokens_batch" for request in requests)
        assert all(request.status == "completed" for request in requests)
    finally:
        model.close()


def test_retrieval_batch_preserves_views_and_field_group_roles(tmp_path):
    args = setup_slot(tmp_path)
    calls = []

    def batch(texts):
        calls.append(texts)
        return [len(text.encode()) + 2 for text in texts]

    def scalar(text):
        return len(text.encode()) + 2

    expected = build_retrieval_views(
        args["index"], args["metadata"], count_tokens=scalar, max_record_tokens=300,
    )
    actual = build_retrieval_views(
        args["index"], args["metadata"], count_tokens=lambda _: pytest.fail("scalar call"),
        count_tokens_batch=batch, max_record_tokens=300,
    )
    assert actual == expected and len(calls) == 1
    assert len(calls[0]) == len(args["index"].records)


class BatchModel(RankingModel):
    def __init__(self):
        super().__init__()
        self.token_batches = []

    def count_tokens(self, text):
        pytest.fail("ranking should use exact batched tokenizer")

    def count_tokens_batch(self, texts):
        self.token_batches.append(list(texts))
        return [max(1, len(text) // 4) for text in texts]


def test_ranking_batches_unique_texts_and_reuses_views_across_slots(tmp_path, monkeypatch):
    args = setup_slot(tmp_path)
    model = BatchModel()
    service = RankingService(RankingPolicy(mode="semantic", batch_size=2), model)
    epoch = service.prepare_next_epoch(**args)
    assert epoch.status == "ready"
    service.commit_epoch(epoch)
    counted = [text for batch in model.token_batches for text in batch]
    assert len(counted) == len(set(counted))
    assert max(map(len, model.token_batches)) <= 2
    records = {view["model_text"] for view in epoch.retrieval_views}

    from app.services.extraction.ontology_guided import semantic_reranker

    original = semantic_reranker.build_retrieval_views
    builds = []

    def build(*args, **kwargs):
        builds.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(semantic_reranker, "build_retrieval_views", build)
    other = args["predicate"].model_copy(update={"iri": "urn:differentPredicate"})
    plan = plan_slot(args["plan"].subject, other, args["index"], args["metadata"],
                     ontology_hash=args["plan"].ontology_hash)
    previous_batches = len(model.token_batches)
    next_epoch = service.prepare_next_epoch(**{**args, "predicate": other, "plan": plan})
    assert next_epoch.status == "ready" and builds == []
    assert records.isdisjoint(text for batch in model.token_batches[previous_batches:]
                              for text in batch)
    changed = args["metadata"].model_copy(update={"summary_version": "changed"})
    service._views(args["index"], changed, scope_hash="changed-owner", count_tokens=len)
    assert builds == [1]


def test_snapshots_share_only_immutable_payloads_and_do_not_reserialize_history(
    tmp_path, monkeypatch,
):
    args = setup_slot(tmp_path)
    service = RankingService(RankingPolicy(mode="semantic"), RankingModel())
    epoch = service.prepare_next_epoch(**args)
    service.commit_epoch(epoch)
    first = service.snapshot()
    monkeypatch.setattr(RankingEpoch, "model_dump", lambda *a, **k: pytest.fail("history copied"))
    second = service.snapshot()
    assert first["cache"] is second["cache"]
    assert first["epochs"][0] is second["epochs"][0]
    assert json.loads(json.dumps(first)) == first
    vector = next(iter(first["cache"].values()))
    with pytest.raises(TypeError, match="immutable"):
        vector.append(0.0)
    with pytest.raises(TypeError, match="immutable"):
        first["cache"].clear()
    with pytest.raises(TypeError, match="immutable"):
        first["epochs"][0]["record_ids"].clear()
    second["costs"]["tokens"] += 1
    assert first["costs"] == service.costs
    fork = service.fork()
    assert next(iter(fork._cache.values())) is vector
    fork.costs["tokens"] += 3
    assert service.costs == first["costs"]


class RetryEachIntent(RankingModel):
    def __init__(self):
        super().__init__()
        self.intent_calls = Counter()

    def score_pairs(self, pairs):
        self.intent_calls[pairs[0][0]] += 1
        if self.intent_calls[pairs[0][0]] == 1:
            raise RuntimeError("temporary model failure")
        return super().score_pairs(pairs)


def test_v2_reserves_both_base_intents_and_independent_technical_retries(tmp_path):
    args = setup_slot(tmp_path)
    model = RetryEachIntent()
    policy = RankingPolicy(mode="semantic", enable_dense=False, batch_size=128,
                           technical_retry_limit=1, policy_version="semantic-ranking-v2")
    assert policy.max_model_calls_per_record == 4
    service = RankingService(policy, model)
    epoch = service.prepare_next_epoch(**args)
    assert epoch.status == "ready" and epoch.actual_ranking_mode == "semantic"
    assert sorted(model.intent_calls.values()) == [2, 2]
    saved = service.snapshot()
    assert saved["costs"]["model_calls"] == 4
    assert saved["costs"]["input_pairs"] == 4 * len(epoch.record_ids)
    assert saved["costs"]["technical_retries"] == 2
    assert set(saved["record_call_counts"].values()) == {4}
    assert set(saved["record_intent_call_counts"].values()) == {2}
    service.commit_epoch(epoch)
    restored = RankingService(policy, RankingModel(), state=service.snapshot())
    assert restored.prepare_next_epoch(**args) is None
    assert restored.costs == service.costs


def test_v1_freezes_legacy_record_limit_and_rejects_automatic_upgrade(tmp_path):
    args = setup_slot(tmp_path)
    policy = RankingPolicy(mode="semantic", enable_dense=False, batch_size=128)
    assert policy.policy_version == "semantic-ranking-v1" and policy.max_model_calls_per_record == 2
    service = RankingService(policy, RetryEachIntent())
    epoch = service.prepare_next_epoch(**args)
    assert epoch.reason == "ranking_call_budget_exhausted"
    legacy = deepcopy(service.snapshot())
    assert "record_intent_call_counts" not in legacy
    assert RankingPolicy.model_validate(legacy["policy"]) == policy
    with pytest.raises(ValueError, match="policy changed"):
        RankingService(RankingPolicy(policy_version="semantic-ranking-v2"), RankingModel(),
                       state=legacy)


def test_factory_creates_v2_but_explicit_legacy_configuration_stays_v1():
    from app.config import Settings

    settings = Settings(_env_file=None, semantic_ranking_enabled=False)
    fresh, frozen = configured_ranking_service(settings)
    assert fresh.policy.policy_version == frozen["policy"]["policy_version"]
    assert fresh.policy.policy_version == "semantic-ranking-v2"
    assert fresh.policy.max_model_calls_per_record == 2 * (1 + fresh.policy.technical_retry_limit)
    legacy, _ = configured_ranking_service(
        settings, policy_overrides={"policy_version": "semantic-ranking-v1"},
    )
    assert legacy.policy.policy_version == "semantic-ranking-v1"
    assert legacy.policy.max_model_calls_per_record == 2


def test_v2_pause_restore_keeps_uncertain_dispatch_costs_and_exhausted_intent(tmp_path):
    args = setup_slot(tmp_path)
    model = RankingModel()
    model.score_pairs = lambda _: (_ for _ in ()).throw(RuntimeError("unknown after dispatch"))
    policy = RankingPolicy(mode="semantic", enable_dense=False, batch_size=128,
                           failure_policy="pause", policy_version="semantic-ranking-v2")
    service = RankingService(policy, model)
    paused = service.prepare_next_epoch(**args)
    assert paused.status == "paused"
    saved = service.snapshot()
    assert saved["costs"]["model_calls"] == 2
    restored = RankingService(policy, RankingModel(), state=saved)
    stopped = restored.prepare_next_epoch(**args)
    assert stopped.reason == "ranking_call_budget_exhausted" and stopped.status == "paused"
    current = restored.snapshot()
    for key in (
        "record_intent_call_counts", "record_call_counts", "request_attempts", "slot_costs",
    ):
        assert current[key] == saved[key]
    for key in ("tokens", "model_calls", "input_pairs", "technical_retries"):
        assert current["costs"][key] == saved["costs"][key]
    assert restored.model.calls == []
