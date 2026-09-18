"""Experimental candidate admission keeps baseline replay and coverage intact."""

import pytest

from app.services.extraction.evidence_identity import evidence_hash
from app.services.extraction.ontology_guided.semantic_reranker import RankingPolicy, RankingService
from app.services.extraction.ontology_guided.semantic_retrieval import select_candidate_pool
from tests.test_extraction.test_semantic_ranking import RankingModel, setup_slot


def test_default_policy_retains_historical_serialization_and_hash():
    policy = RankingPolicy()
    historical = policy.model_dump(mode="json")
    assert "fill_candidate_pool" not in historical
    assert evidence_hash(policy) == evidence_hash(historical)
    restored = RankingPolicy.model_validate(historical)
    assert restored.fill_candidate_pool is True
    experimental = policy.model_copy(update={"fill_candidate_pool": False})
    assert experimental.model_dump(mode="json")["fill_candidate_pool"] is False
    assert evidence_hash(experimental) != evidence_hash(policy)
    state = RankingService(policy).snapshot()
    assert RankingService(restored, state=state).snapshot()["policy"] == historical


def test_empty_recall_does_not_fill_with_unrelated_tail():
    args = dict(pool_size=16, protected_ids=[], exploration_ids=[], quotas={"dense": 8})
    assert select_candidate_pool(["a", "b"], {"dense": []}, fill_pool=False, **args) == (
        [], {},
    )
    assert select_candidate_pool(["a", "b"], {"dense": []}, **args)[0] == ["a", "b"]


def test_semantic_candidate_scope_keeps_full_universe_and_rejects_foreign_records(tmp_path):
    args = setup_slot(tmp_path)
    plan = args["plan"]
    original = plan.model_dump(mode="json")
    scope = plan.frozen_record_ids[:1]
    service = RankingService(RankingPolicy(mode="semantic", pool_size=2), RankingModel())
    epoch = service.prepare_next_epoch(**args, candidate_record_ids=scope)
    assert epoch is not None
    assert epoch.record_ids == scope
    assert plan.model_dump(mode="json") == original
    service.validate_epoch(epoch, **args, candidate_record_ids=scope)
    with pytest.raises(ValueError, match="dependencies"):
        service.validate_epoch(epoch, **args, candidate_record_ids=[])
    with pytest.raises(ValueError, match="frozen record universe"):
        service.prepare_next_epoch(**args, candidate_record_ids=["foreign"])


def test_empty_selected_pool_is_completed_search_not_model_failure(tmp_path):
    args = setup_slot(tmp_path)
    model = RankingModel()
    service = RankingService(RankingPolicy(
        mode="semantic", enable_dense=False, fill_candidate_pool=False,
        protected_quota=0, exploration_quota=0, structure_quota=0, dense_quota=0,
        metadata_quota=0, failure_policy="pause",
    ), model)
    epoch = service.prepare_next_epoch(**args)
    assert epoch is not None and epoch.record_ids == []
    assert epoch.reason is None and epoch.status == "ready"
    assert not model.calls
    assert service.commit_epoch(epoch).status == "committed"
