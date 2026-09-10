from __future__ import annotations

import copy
import json

import pytest

from app.services.extraction.evidence_identity import evidence_hash
from app.services.extraction.ontology_guided.contracts import (
    GraphNode,
    RetrievalPlan,
    SubjectRef,
    VersionedRef,
)
from app.services.extraction.ontology_guided.metadata import prepare_metadata
from app.services.extraction.ontology_guided.records import RecordIndex
from app.services.extraction.ontology_guided.retrieval import plan_slot, validate_record_universe
from app.services.extraction.ontology_guided.retrieval_fusion import (
    rank_scores,
    reciprocal_rank_fusion,
)
from app.services.extraction.ontology_guided.retrieval_query import (
    QueryMention,
    build_subject_queries,
)
from app.services.extraction.ontology_guided.retrieval_views import build_retrieval_views
from app.services.extraction.ontology_guided.semantic_reranker import (
    RankingPaused,
    RankingPersistenceError,
    RankingPolicy,
    RankingService,
    apply_epoch,
)
from app.services.extraction.ontology_guided.semantic_retrieval import (
    select_candidate_pool,
    sparse_scores,
)
from tests.test_extraction.test_ontology_guided_core import REPORT, ontology, sample


def setup_slot(tmp_path):
    analysis = sample(tmp_path)
    index = RecordIndex(analysis.ir)
    metadata = prepare_metadata(
        analysis.ir, section_tree=analysis.structure.section_tree.to_dict(), summary_version="test"
    )
    predicate = ontology().classes[REPORT].declared_relationships[0]
    subject = SubjectRef(entity_id="root", revision=1, class_iri=REPORT, is_document_root=True)
    node = GraphNode(
        entity_id="root",
        revision=1,
        class_iri=REPORT,
        class_label="报告",
        label="文件名不是产品名称.docx",
        root=True,
    )
    plan = plan_slot(subject, predicate, index, metadata, ontology_hash=ontology().ontology_hash)
    args = dict(
        plan=plan,
        index=index,
        metadata=metadata,
        subject_node=node,
        predicate=predicate,
        run_fingerprint="run",
        root_ref=VersionedRef(id="root", revision=1),
        root_class_iri=REPORT,
        permission_scope="owner:alice",
    )
    return args


class RankingModel:
    identity = {"model": "fixed-test-model"}

    def __init__(self):
        self.calls = []

    def count_tokens(self, text):
        return max(1, len(text) // 4)

    def embed(self, texts):
        self.calls.append(("embed", len(texts)))
        return [[float("产品甲" in text) + 1, 1.0] for text in texts]

    def score_pairs(self, pairs):
        self.calls.append(("score", len(pairs)))
        # Negative values remain valid scores; relation polarity is not a verdict.
        return [-1.0 if "产品甲" in text else -4.0 for _, text in pairs]


def test_root_and_local_queries_keep_unverified_identifier_out_of_model_text(tmp_path):
    args = setup_slot(tmp_path)
    root_queries = build_subject_queries(
        **{
            key: value
            for key, value in args.items()
            if key
            in {
                "subject_node",
                "predicate",
                "index",
                "run_fingerprint",
                "root_ref",
                "root_class_iri",
            }
        },
        subject=args["plan"].subject,
    )
    assert all("文件名不是产品名称" not in query.model_text for query in root_queries)
    index = args["index"]
    unit = next(item for item in index.ir.evidence_units if "产品甲" in item.text)
    anchor = index.ir.anchor(
        unit.evidence_id, unit.text.index("产品甲"), unit.text.index("产品甲") + 3
    )
    node = args["subject_node"].model_copy(
        update={"root": False, "entity_id": "product", "evidence_refs": [anchor]}
    )
    subject = args["plan"].subject.model_copy(
        update={"entity_id": "product", "is_document_root": False}
    )
    queries = build_subject_queries(
        subject=subject,
        subject_node=node,
        predicate=args["predicate"],
        index=index,
        run_fingerprint="new-run",
        root_ref=args["root_ref"],
        root_class_iri=REPORT,
        mentions=[QueryMention(text="试验来源-1", trust_state="quarantined")],
    )
    assert all(
        "产品甲" in query.model_text and "试验来源-1" not in query.model_text for query in queries
    )
    assert queries[0].excluded_sources[0].text == "试验来源-1"
    assert "new-run" not in queries[0].model_text
    assert json.loads(queries[0].model_text)["allowed_object_types"] == [{"iri": "urn:Product"}]


def test_view_keeps_table_roles_and_long_records_untruncated(tmp_path):
    args = setup_slot(tmp_path)
    views = build_retrieval_views(
        args["index"], args["metadata"], count_tokens=len, max_record_tokens=1
    )
    table = next(item for item in views.values() if "试验来源-1" in item.model_text)
    assert "API" in table.model_text and "来源" in table.model_text
    assert table.source_cell_ids and table.binding_refs
    assert table.status == "not_rerankable"
    assert table.omitted_refs == []
    for anchor in table.source_refs:
        assert args["index"].ir.resolve(anchor) in table.model_text


def test_independent_universe_rejects_plan_and_ledger_losing_same_record(tmp_path):
    args = setup_slot(tmp_path)
    raw = args["plan"].model_dump(mode="json")
    lost = raw["records"].pop()["record_id"]
    raw["ledger"].pop(lost)
    with pytest.raises(ValueError, match="frozen record universe"):
        RetrievalPlan.model_validate(raw)
    raw["frozen_record_ids"].remove(lost)
    raw["frozen_record_hash"] = evidence_hash(raw["frozen_record_ids"])
    forged = RetrievalPlan.model_validate(raw)
    with pytest.raises(ValueError, match="RecordIndex"):
        validate_record_universe(forged, args["index"])


def test_pool_deduplicates_and_empty_channels_yield_without_inventing_hits():
    ids = [str(index) for index in range(12)]
    pool, reasons = select_candidate_pool(
        ids,
        {"structure": ["0", "1"], "dense": ["0", "2", "3", "4"]},
        pool_size=8,
        protected_ids=["0"],
        exploration_ids=["0", "8", "9"],
        quotas={"protected": 1, "exploration": 2, "structure": 3, "dense": 2},
    )
    assert len(pool) == len(set(pool)) == 8
    assert {"0", "8", "9", "1", "2"}.issubset(pool)
    assert sparse_scores({"0": "无匹配"}, ["组成"]) == {}
    assert "structure" not in reasons["8"]


def test_pool_ranks_are_global_and_do_not_depend_on_batch_completion_order():
    ids = [str(index) for index in range(64)]
    scores = {rid: -float(index) for index, rid in enumerate(ids)}
    returned = dict(reversed(list(scores.items())))
    assert rank_scores(scores, ids) == rank_scores(returned, ids)
    ranks = {
        "discover": rank_scores(scores, ids),
        "counterevidence": rank_scores({rid: -score for rid, score in scores.items()}, ids),
    }
    order, _ = reciprocal_rank_fusion(ranks, ids, require_complete=True)
    assert set(order) == set(ids)
    ranks["counterevidence"].pop("9")
    with pytest.raises(ValueError, match="scope"):
        reciprocal_rank_fusion(ranks, ids, require_complete=True)


def test_semantic_epoch_counts_both_intents_and_restores_without_model_calls(tmp_path):
    args = setup_slot(tmp_path)
    model = RankingModel()
    service = RankingService(RankingPolicy(mode="semantic", batch_size=2), model)
    epoch = service.prepare_next_epoch(**args)
    assert epoch.actual_ranking_mode == "semantic"
    assert epoch.costs["input_pairs"] == len(epoch.record_ids) * 2
    assert len(epoch.observations) == len(epoch.record_ids) * 2
    assert all(item["raw_rerank_score"] < 0 for item in epoch.observations)
    assert all(entry.coverage_state == "unattempted" for entry in args["plan"].ledger.values())
    committed = service.commit_epoch(epoch)
    revised = apply_epoch(args["plan"], committed)
    assert revised.plan_id == args["plan"].plan_id
    assert revised.frozen_record_ids == args["plan"].frozen_record_ids
    assert revised.ledger == args["plan"].ledger
    restored_model = RankingModel()
    restored = RankingService(service.policy, restored_model, state=service.snapshot())
    assert restored.prepare_next_epoch(**args) is None
    assert restored_model.calls == []
    with pytest.raises(ValueError, match="permission"):
        restored.prepare_next_epoch(**{**args, "permission_scope": "owner:bob"})


@pytest.mark.parametrize("failure", ["missing", "nan", "tokenizer", "identity"])
def test_any_batch_failure_degrades_the_whole_pool_and_preserves_coverage(tmp_path, failure):
    args = setup_slot(tmp_path)
    model = RankingModel()
    original = model.score_pairs
    if failure == "tokenizer":

        def count(_text):
            raise RuntimeError("/private/path/to/model or private input")

        model.count_tokens = count
    else:

        def score(pairs):
            if failure == "missing":
                return original(pairs)[:-1]
            if failure == "nan":
                return [float("nan") for _ in pairs]
            model.identity = {"model": "changed"}
            return original(pairs)

        model.score_pairs = score
    service = RankingService(RankingPolicy(mode="semantic", technical_retry_limit=0), model)
    epoch = service.prepare_next_epoch(**args)
    assert epoch.degraded and epoch.actual_ranking_mode == "deterministic"
    assert epoch.ordered_record_ids == epoch.record_ids
    assert all(item["intent_rank"] is None for item in epoch.observations)
    assert "private" not in epoch.reason
    assert sum(entry.coverage_state == "unattempted" for entry in args["plan"].ledger.values()) == (
        len(args["index"].records)
    )


def test_budget_exhaustion_and_pause_never_commit_a_partial_ranking(tmp_path):
    args = setup_slot(tmp_path)
    service = RankingService(
        RankingPolicy(mode="semantic", failure_policy="pause", max_ranking_tokens_per_run=1),
        RankingModel(),
    )
    epoch = service.prepare_next_epoch(**args)
    assert epoch.status == "paused"
    with pytest.raises(RankingPaused):
        service.commit_epoch(epoch)
    assert not service.epochs
    assert service.costs["tokens"] == 0
    restored_model = RankingModel()
    restored = RankingService(service.policy, restored_model, state=service.snapshot())
    assert restored.prepare_next_epoch(**args) == epoch
    assert restored_model.calls == []
    assert len(restored.snapshot()["pending_epochs"]) == 1


def test_dense_only_ablation_uses_same_views_without_reranking_calls(tmp_path):
    args = setup_slot(tmp_path)
    model = RankingModel()
    service = RankingService(RankingPolicy(mode="semantic", enable_reranker=False), model)
    epoch = service.prepare_next_epoch(**args)
    assert epoch.actual_ranking_mode == "semantic" and not epoch.degraded
    assert model.calls and all(kind == "embed" for kind, _ in model.calls)
    assert epoch.costs["input_pairs"] == 0
    assert epoch.costs["embedding_inputs"] > 0
    assert all(item["raw_rerank_score"] is None for item in epoch.observations)


def test_same_lease_old_query_cannot_reuse_pending_or_committed_epoch(tmp_path):
    args = setup_slot(tmp_path)
    service = RankingService(RankingPolicy())
    epoch = service.prepare_next_epoch(**args)
    changed = {**args, "dependency_refs": [VersionedRef(id="changed-proof", revision=2)]}
    with pytest.raises(ValueError, match="dependencies"):
        service.prepare_next_epoch(**changed)
    committed = service.commit_epoch(epoch)
    with pytest.raises(ValueError, match="dependencies"):
        service.validate_epoch(
            committed,
            **{key: value for key, value in changed.items() if key != "required_record_ids"},
        )


def test_committed_rank_cannot_be_overwritten_and_model_changes_block_restore(tmp_path):
    args = setup_slot(tmp_path)
    service = RankingService(RankingPolicy(), RankingModel())
    epoch = service.commit_epoch(service.prepare_next_epoch(**args))
    changed = epoch.model_copy(update={"ordered_record_ids": list(reversed(epoch.record_ids))})
    if changed != epoch:
        with pytest.raises(ValueError, match="cannot change"):
            service.commit_epoch(changed)
    state = copy.deepcopy(service.snapshot())
    state["model_identity"] = {"model": "other"}
    with pytest.raises(ValueError, match="identity"):
        RankingService(service.policy, RankingModel(), state=state)


def test_full_epoch_deadline_includes_tokenizer_and_stops_remaining_calls(tmp_path, monkeypatch):
    from app.services.extraction.ontology_guided import semantic_reranker

    args = setup_slot(tmp_path)
    stamp = [0.0]
    monkeypatch.setattr(semantic_reranker.time, "monotonic", lambda: stamp[0])
    model = RankingModel()
    token_calls = []
    deadlines = []

    def slow_tokens(text):
        token_calls.append(text)
        stamp[0] += 0.6
        return len(text)

    model.count_tokens = slow_tokens
    model.set_deadline = deadlines.append
    service = RankingService(RankingPolicy(mode="semantic", ranking_timeout=0.5), model)
    epoch = service.prepare_next_epoch(**args)
    assert epoch.degraded and epoch.reason == "ranking_timeout"
    assert len(token_calls) == 1
    assert model.calls == []
    assert deadlines == [0.5, None]
    assert epoch.costs["tokens"] == 0


def test_token_counts_are_cached_by_exact_input_permission_and_identity(tmp_path):
    args = setup_slot(tmp_path)
    model = RankingModel()
    token_calls = []
    original = model.count_tokens

    def count(text):
        token_calls.append(text)
        return original(text)

    model.count_tokens = count
    service = RankingService(RankingPolicy(mode="semantic"), model)
    epoch = service.commit_epoch(service.prepare_next_epoch(**args))
    views = {item["model_text"] for item in epoch.retrieval_views}
    assert all(token_calls.count(text) == 1 for text in views)
    second_predicate = args["predicate"].model_copy(update={"iri": "urn:other"})
    second_plan = plan_slot(
        args["plan"].subject,
        second_predicate,
        args["index"],
        args["metadata"],
        ontology_hash=ontology().ontology_hash,
    )
    service.prepare_next_epoch(**{**args, "plan": second_plan, "predicate": second_predicate})
    assert all(token_calls.count(text) == 1 for text in views)
    assert service.snapshot()["token_cache"]
    assert service.costs["token_cache_hits"] > 0


def test_cost_barrier_runs_before_inference_and_failed_write_never_retries(tmp_path):
    args = setup_slot(tmp_path)
    model = RankingModel()
    persisted = []

    def barrier(state):
        assert state["costs"]["model_calls"] > len(model.calls)
        persisted.append(state)
        raise RuntimeError("database commit failed")

    service = RankingService(
        RankingPolicy(mode="semantic", technical_retry_limit=2), model, before_model_hook=barrier
    )
    with pytest.raises(RankingPersistenceError, match="cost_commit_failed"):
        service.prepare_next_epoch(**args)
    assert len(persisted) == 1
    assert model.calls == []
    assert not service.epochs


def test_forked_service_preserves_observation_prefix_without_double_counting():
    model = RankingModel()
    model.observations = [{"operation": "embed", "input_tokens": 10}]
    first = RankingService(RankingPolicy(), model)
    state = first.snapshot()
    fork = RankingService(first.policy, model, state=state)
    model.observations.append({"operation": "score_pairs", "input_tokens": 20})
    assert fork.snapshot()["model_observations"] == model.observations


def test_crash_after_cost_barrier_does_not_reset_exact_request_retry_allowance(tmp_path):
    args = setup_slot(tmp_path)
    model = RankingModel()
    snapshots = []

    def crash_after_durable_cost(state):
        snapshots.append(state)
        raise RuntimeError("simulated process loss after durable reservation")

    policy = RankingPolicy(mode="semantic", technical_retry_limit=0)
    service = RankingService(policy, model, before_model_hook=crash_after_durable_cost)
    with pytest.raises(RankingPersistenceError):
        service.prepare_next_epoch(**args)
    recovered_model = RankingModel()
    recovered = RankingService(policy, recovered_model, state=snapshots[-1])
    epoch = recovered.prepare_next_epoch(**args)
    assert epoch.degraded and epoch.reason == "ranking_call_budget_exhausted"
    assert recovered_model.calls == []
    assert recovered.costs["tokens"] == snapshots[-1]["costs"]["tokens"]
