"""Independent score failures: wrong proof, conditions, unknowns and fixed pools."""

from __future__ import annotations

from copy import deepcopy

import pytest

from app.evaluation.ontology_guided_scorer import (
    EntityMatcher,
    EvidenceSpan,
    ExpertReview,
    OntologyGuidedReference,
    QualityThresholds,
    ReferenceEntity,
    ReferenceEntityPartition,
    ReferencePath,
    ReferenceProperty,
    ReferenceRelationship,
    score_evaluation,
)
from app.evaluation.semantic_ranking_evaluation import (
    score_retrieval_query,
    validate_ablation_pair,
)
from tests.test_extraction.test_ontology_guided_core import DESCRIBES, REPORT
from tests.test_extraction.test_ontology_guided_evaluation import _evaluation


def _reference_run(tmp_path):
    analysis, _, run, _ = _evaluation(tmp_path, focus_path=(DESCRIBES,))
    unit = next(unit for unit in analysis.ir.evidence_units if "明确描述产品甲" in unit.text)
    anchor = analysis.ir.anchor(unit.evidence_id, 0, len(unit.text))
    run = run.model_copy(update={"graph": run.graph.model_copy(update={"edges": [
        edge.model_copy(update={"structural_valid": True, "policy_eligible": True,
                                "evidence_refs": [anchor]})
        for edge in run.graph.edges
    ], "nodes": [node.model_copy(update={"evidence_refs": [anchor]}) for node in run.graph.nodes],
        "properties": [item.model_copy(update={"evidence_refs": [anchor]})
                       for item in run.graph.properties]})})
    nodes = {node.entity_id: node for node in run.graph.nodes}

    def matcher(node):
        return EntityMatcher(class_iri=node.class_iri, label=node.label, document_root=node.root)

    def evidence(item):
        return [[EvidenceSpan(evidence_id=anchor.evidence_id,
                              start=anchor.span_start, end=anchor.span_end)
                 for anchor in item.evidence_refs]]

    reference = OntologyGuidedReference(
        reference_id="test-reference", document_hash=analysis.ir.document_hash,
        ontology_hash=run.ontology_snapshot.ontology_hash, root_class_iri=REPORT,
        scope_mode="focus_path", focus_path=[DESCRIBES], annotation_complete=True,
        entities=[ReferenceEntity(entity=matcher(node), allowed_evidence_sets=evidence(node))
                  for node in nodes.values() if not node.root],
        relationships=[ReferenceRelationship(
            subject=matcher(nodes[edge.subject_ref.id]), predicate_iri=edge.predicate_iri,
            object=matcher(nodes[edge.object_ref.id]), allowed_evidence_sets=evidence(edge),
        ) for edge in run.graph.edges],
        properties=[ReferenceProperty(
            subject=matcher(nodes[item.subject_ref.id]), predicate_iri=item.predicate_iri,
            value=item.raw_value, allowed_evidence_sets=evidence(item),
        ) for item in run.graph.properties],
        expert_review=ExpertReview(status="approved", reviewer="test-domain-expert",
                                   reviewed_at="2026-09-08", reference_hash="a" * 64),
        quality_thresholds=QualityThresholds(source="test-only thresholds",
            minimum_overall_precision=1, minimum_overall_recall=1, minimum_overall_f1=1),
    )
    return analysis, run, reference


def test_correct_tuple_with_wrong_original_proof_is_fp_and_fn(tmp_path):
    analysis, run, reference = _reference_run(tmp_path)
    reference.relationships[0].allowed_evidence_sets = [[
        EvidenceSpan(evidence_id="other-record", start=0, end=1),
    ]]
    score = score_evaluation(run, reference, ir=analysis.ir)
    assert score["metrics"]["relationship"]["tp"] == 0
    assert score["metrics"]["relationship"]["fp"] == 1
    assert score["metrics"]["relationship"]["fn"] == 1
    assert score["semantic_match_metrics"]["relationship"]["tp"] == 1
    assert score["formal_quality_gate"] == "fail"


@pytest.mark.parametrize("change", [{"conditions": ["only in batch A"]},
                                    {"applicability": {"batch": "A"}},
                                    {"direction": "inbound"}])
def test_conditions_scope_and_direction_are_part_of_the_tuple(tmp_path, change):
    analysis, run, reference = _reference_run(tmp_path)
    reference.relationships[0] = reference.relationships[0].model_copy(update=change)
    score = score_evaluation(run, reference, ir=analysis.ir)
    assert score["metrics"]["relationship"]["tp"] == 0
    assert score["metrics"]["relationship"]["fp"] == 1
    assert score["metrics"]["relationship"]["fn"] == 1


def test_missing_reference_proof_and_unadjudicated_prediction_block_pass(tmp_path):
    analysis, run, reference = _reference_run(tmp_path)
    reference.relationships[0].allowed_evidence_sets = []
    score = score_evaluation(run, reference, ir=analysis.ir)
    assert score["unscored_predictions"]["relationships"] == 1
    assert score["precision_bounds"] == {"lower": 2 / 3, "upper": 1.0}
    assert score["formal_quality_gate"] == "fail"
    reference.relationships = []
    reference.annotation_complete = False
    score = score_evaluation(run, reference, ir=analysis.ir)
    assert score["unscored_predictions"]["relationships"] == 1
    assert score["metrics"]["relationship"]["fp"] == 0
    assert score["formal_quality_gate"] == "fail"


def test_empty_graph_with_expected_positive_has_na_precision_and_zero_f1(tmp_path):
    analysis, run, reference = _reference_run(tmp_path)
    run.graph.nodes = [node for node in run.graph.nodes if node.root]
    run.graph.edges = []
    run.graph.properties = []
    score = score_evaluation(run, reference, ir=analysis.ir)
    assert score["metrics"]["overall"]["precision"] is None
    assert score["metrics"]["overall"]["recall"] == 0
    assert score["metrics"]["overall"]["f1"] == 0


def test_duplicate_edge_cannot_earn_second_tp_and_path_requires_proof(tmp_path):
    analysis, run, reference = _reference_run(tmp_path)
    reference.paths = [ReferencePath(path_id="root-product", edges=reference.relationships)]
    score = score_evaluation(run, reference, ir=analysis.ir)
    assert score["paths"]["tp"] == 1
    duplicate = run.graph.edges[0].model_copy(update={"candidate_id": "duplicate"})
    run.graph.edges.append(duplicate)
    score = score_evaluation(run, reference, ir=analysis.ir)
    assert score["metrics"]["relationship"]["tp"] == 1
    assert score["metrics"]["relationship"]["fp"] == 1
    assert score["duplicate_eligible_predictions"]["relationships"] == 1
    reference.paths[0].edges[0].allowed_evidence_sets = [[
        EvidenceSpan(evidence_id="missing", start=0, end=1),
    ]]
    assert score_evaluation(run, reference, ir=analysis.ir)["paths"]["tp"] == 0


def test_entity_partition_detects_wrong_merge_and_split_from_independent_mentions(tmp_path):
    analysis, run, reference = _reference_run(tmp_path)
    node = next(node for node in run.graph.nodes if not node.root)
    first = node.evidence_refs[0]
    unit = next(unit for unit in analysis.ir.evidence_units if "产品乙" in unit.text)
    second = analysis.ir.anchor(unit.evidence_id, 0, len(unit.text))
    reference.entity_partitions = [ReferenceEntityPartition(
        local_id=label, class_iri=node.class_iri,
        mention_spans=[EvidenceSpan(evidence_id=anchor.evidence_id,
                                   start=anchor.span_start, end=anchor.span_end)],
    ) for label, anchor in (("product-a", first), ("product-b", second))]
    node.evidence_refs = [first, second]
    score = score_evaluation(run, reference, ir=analysis.ir)
    assert score["entity_partitions"]["wrong_merge_count"] == 1
    assert score["formal_quality_gate"] == "fail"
    node.evidence_refs = [first]
    run.graph.nodes.append(node.model_copy(update={"entity_id": "product-copy"}))
    score = score_evaluation(run, reference, ir=analysis.ir)
    assert score["entity_partitions"]["wrong_split_count"] == 1


def test_path_enumeration_limit_is_explicit_incomplete_and_cannot_pass(tmp_path):
    analysis, run, reference = _reference_run(tmp_path)
    reference.paths = [ReferencePath(path_id="root-product", edges=reference.relationships)]
    reference.max_path_traversals = 1
    score = score_evaluation(run, reference, ir=analysis.ir)
    assert score["paths"]["status"] == "truncated"
    assert "path_enumeration_incomplete" in score["gate_reasons"]
    assert score["formal_quality_gate"] == "fail"


def test_strong_mention_constraint_cannot_be_bypassed_by_weak_endpoint_matcher(tmp_path):
    analysis, run, reference = _reference_run(tmp_path)
    # The relationship repeats the same entity by label alone. It must inherit
    # the independently required owner mention instead of bypassing that check.
    reference.entities[0].entity.mention_evidence_sets = [[
        EvidenceSpan(evidence_id="a-different-owner", start=0, end=1),
    ]]
    score = score_evaluation(run, reference, ir=analysis.ir)
    assert score["metrics"]["entity"]["tp"] == 0
    assert score["metrics"]["relationship"]["tp"] == 0


def test_stale_endpoint_revision_is_not_a_correct_tuple_or_path(tmp_path):
    analysis, run, reference = _reference_run(tmp_path)
    reference.paths = [ReferencePath(path_id="root-product", edges=reference.relationships)]
    edge = run.graph.edges[0]
    edge.object_ref = edge.object_ref.model_copy(update={"revision": edge.object_ref.revision + 1})
    score = score_evaluation(run, reference, ir=analysis.ir)
    assert score["metrics"]["relationship"]["tp"] == 0
    assert score["metrics"]["relationship"]["fp"] == 1
    assert score["paths"]["tp"] == 0


def test_multihop_proof_cannot_be_borrowed_from_a_disconnected_same_name_path(tmp_path):
    from app.services.extraction.ontology_guided.contracts import VersionedRef

    analysis, run, reference = _reference_run(tmp_path)
    product = next(node for node in run.graph.nodes if not node.root)
    original_edge = run.graph.edges[0]
    other_product = product.model_copy(update={"entity_id": "different-same-name-product"})
    endpoint = product.model_copy(update={"entity_id": "endpoint", "label": "末端对象"})
    run.graph.nodes.extend((other_product, endpoint))
    second = original_edge.model_copy(update={
        "candidate_id": "second-hop", "subject_ref": VersionedRef(id=product.entity_id, revision=1),
        "object_ref": VersionedRef(id=endpoint.entity_id, revision=1),
    })
    alternative_first = original_edge.model_copy(update={
        "candidate_id": "other-first-hop",
        "object_ref": VersionedRef(id=other_product.entity_id, revision=1),
    })
    run.graph.edges.extend((second, alternative_first))
    # Only the first hop to the unrelated duplicate has valid evidence. The
    # product with a real second hop has no proved route from the root.
    original_edge.evidence_refs = []
    second_reference = reference.relationships[0].model_copy(update={
        "subject": reference.relationships[0].object,
        "object": EntityMatcher(class_iri=endpoint.class_iri, label=endpoint.label),
    })
    reference.paths = [ReferencePath(path_id="two-hops", edges=[
        reference.relationships[0], second_reference,
    ])]
    score = score_evaluation(run, reference, ir=analysis.ir)
    assert score["paths"]["tp"] == 0 and score["paths"]["fn"] == 1


def test_active_evaluator_preserves_shared_ranking_state_and_actual_pair_cost(tmp_path):
    from app.evaluation.quality_guided_variant import build_quality_guided_variant
    from app.services.extraction.ontology_guided.metadata import prepare_metadata
    from app.services.extraction.ontology_guided.semantic_reranker import (
        RankingPolicy,
        RankingService,
    )
    from tests.test_extraction.test_ontology_guided_core import FakeAdapter, ontology, sample
    from tests.test_extraction.test_semantic_ranking import RankingModel

    analysis = sample(tmp_path)
    model = RankingModel()
    service = RankingService(RankingPolicy(mode="semantic", batch_size=2), model)
    runner = build_quality_guided_variant(ontology=ontology(), adapter=FakeAdapter(),
                                          ranking_service=service)
    result = runner.run(
        recognition_run_id="ranking-evaluation", ir=analysis.ir,
        metadata=prepare_metadata(analysis.ir,
            section_tree=analysis.structure.section_tree.to_dict(), summary_version="test"),
        root_class_iri=REPORT, root_class_label="报告", filename="source.docx",
    )
    state = result.ranking["service"]
    assert state["model_identity"] == model.identity
    assert state["epochs"] and all(epoch["status"] == "committed" for epoch in state["epochs"])
    assert any(kind == "score" for kind, _ in model.calls)
    assert result.graph.progress.model_calls == len(result.adapter_calls)


def _retrieval():
    identity = {"query_id": "q", "document_hash": "d" * 64, "query_content_hash": "q" * 64}
    reference = {**identity, "annotation_complete": True, "records": [
        {"record_id": "support", "grade": 3, "role": "support"},
        {"record_id": "negative", "grade": 3, "role": "counterevidence"},
        {"record_id": "context", "grade": 1, "role": "context"},
    ], "assertions": [{"equivalent_evidence_sets": [
        {"target_record_ids": ["support", "negative"], "binding_source_ids": ["header"]},
    ]}]}
    observation = {**identity, "pool_record_ids": ["support", "negative", "context"],
                   "ranking_record_ids": ["support", "support", "negative", "context"],
                   "dispatch_record_ids": ["support", "negative"],
                   "assembled_record_ids": ["support", "negative"],
                   "assembled_source_ids": []}
    return reference, observation


def test_retrieval_deduplicates_windows_and_requires_binding_closure():
    reference, observation = _retrieval()
    score = score_retrieval_query(reference, observation, k=2)
    assert score["ndcg_at_k"] == 1
    assert score["mrr"] == 1 and score["recall_at_pool"] == 1
    assert score["roles"]["counterevidence"]["recall_at_k"] == 1
    assert score["duplicate_ranking_entries"] == 1
    assert score["evidence_closure"] == {"pool": 1, "dispatched": 1,
                                         "assembled": 0, "denominator": 1}
    observation["assembled_source_ids"] = ["header"]
    assert score_retrieval_query(reference, observation, k=2)["evidence_closure"]["assembled"] == 1


def test_zero_relevant_queries_and_unscored_records_do_not_inflate_metrics():
    reference, observation = _retrieval()
    for record in reference["records"]:
        record["grade"] = 0
    score = score_retrieval_query(reference, observation, k=2)
    assert score["ndcg_at_k"] is None and score["mrr"] is None
    reference["records"].pop()
    score = score_retrieval_query(reference, observation, k=2)
    assert score["unscored_records"] == ["context"]
    assert score["formal_quality_gate"] == "blocked_incomplete_annotation"


def test_omitted_pool_member_cannot_be_silently_scored_as_a_complete_ranking():
    reference, observation = _retrieval()
    observation["ranking_record_ids"] = ["support", "negative"]
    with pytest.raises(ValueError, match="complete frozen pool"):
        score_retrieval_query(reference, observation, k=2)


def test_fixed_pool_ablation_rejects_unregistered_changes_and_changed_pool():
    manifest = {
        "schema_version": "semantic-ranking-ablation-v1",
        "shared": {key: "frozen" for key in (
            "document_hash", "ontology_hash", "core_hash", "metadata_hash", "scope",
            "recognition_model_identity", "budget", "query_policy_hash", "view_policy_hash",
        )}, "factors": {"reranker": False},
        "fixed_pool": {"query_content_hash": "q", "pool_hash": "p",
                       "view_hashes": {"record": "v"}, "pool_record_ids": ["record"]},
    }
    right = deepcopy(manifest)
    right["factors"]["reranker"] = True
    assert validate_ablation_pair(manifest, right, allowed_factor_changes=["reranker"])["valid"]
    with pytest.raises(ValueError, match="unregistered"):
        validate_ablation_pair(manifest, right, allowed_factor_changes=[])
    right["fixed_pool"]["pool_hash"] = "changed"
    with pytest.raises(ValueError, match="pool_hash"):
        validate_ablation_pair(manifest, right, allowed_factor_changes=["reranker"])
    right["shared"]["budget"] = "increased"
    with pytest.raises(ValueError, match="budget"):
        validate_ablation_pair(manifest, right, allowed_factor_changes=["reranker"])

    right = deepcopy(manifest)
    manifest["factors"]["required_missing_capability"] = None
    with pytest.raises(ValueError, match="unregistered"):
        validate_ablation_pair(manifest, right, allowed_factor_changes=[])
    right = deepcopy(manifest)
    for value in (manifest, right):
        value["fixed_pool"]["pool_record_ids"].append("silently-unmapped-record")
    with pytest.raises(ValueError, match="view mapping"):
        validate_ablation_pair(manifest, right, allowed_factor_changes=[])
