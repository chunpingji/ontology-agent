"""Synthetic reference fixtures exercise final group and qualified-graph scoring."""

from __future__ import annotations

from copy import deepcopy

import pytest

from app.evaluation.ontology_guided_scorer import (
    EntityMatcher,
    OntologyGuidedReference,
    ReferenceEntity,
    ReferenceRelationshipGroup,
    ReferenceScopeMember,
    score_evaluation,
)
from app.services.extraction.ontology_guided.contracts import (
    GraphEdge,
    GraphRelationshipGroup,
    ScopeMember,
    TraversalScope,
    VersionedRef,
)
from tests.test_extraction.test_semantic_ranking_evaluation import _reference_run


@pytest.fixture
def case(tmp_path):
    analysis, run, reference = _reference_run(tmp_path)
    edge = run.graph.edges[0]
    first = next(node for node in run.graph.nodes if node.entity_id == edge.object_ref.id)
    second = first.model_copy(update={"entity_id": "second-product", "label": "产品乙"})
    second_ref = VersionedRef(id=second.entity_id, revision=second.revision)
    run.graph.nodes.append(second)
    group = GraphRelationshipGroup(
        **edge.model_dump(exclude={"object_ref"}), object_refs=[edge.object_ref, second_ref],
        selection="one_of", modality="possible", scope=TraversalScope.create(),
        selection_evidence_refs=edge.evidence_refs,
    )
    run.graph.edges = []
    run.graph.relationship_groups = [group]
    parent_ref = VersionedRef(id=group.candidate_id, revision=group.revision)
    run.graph.properties[0].scope = TraversalScope.create([
        ScopeMember(relation_ref=parent_ref, member_ref=edge.object_ref),
    ])
    run.graph.properties[0].modality = "asserted"
    first_matcher = reference.relationships[0].object
    second_matcher = EntityMatcher(class_iri=second.class_iri, label=second.label)
    gold_group = ReferenceRelationshipGroup(
        **reference.relationships[0].model_dump(exclude={"object"}), reference_id="gold-choice",
        objects=[first_matcher, second_matcher], selection="one_of", modality="possible", scope=[],
    )
    reference = reference.model_copy(update={
        "schema_version": "ontology-guided-reference-v2", "relationships": [],
        "relationship_groups": [gold_group],
        "properties": [reference.properties[0].model_copy(update={
            "modality": "asserted", "scope": [ReferenceScopeMember(
                relation_id="gold-choice", member=first_matcher,
            )],
        })],
        "entities": [*reference.entities, ReferenceEntity(
            entity=second_matcher,
            allowed_evidence_sets=reference.entities[0].allowed_evidence_sets,
        )],
    })
    return analysis, run, reference


def score(case):
    analysis, run, reference = case
    return score_evaluation(run, reference, ir=analysis.ir)


def test_group_and_scope_use_semantics_not_prediction_ids_or_member_order(case):
    _analysis, run, _reference = case
    run.graph.relationship_groups[0].object_refs.reverse()
    result = score(case)
    assert result["metrics"]["relationship_group"]["tp"] == 1
    assert result["metrics"]["property"]["tp"] == 1
    assert result["metrics"]["assertions"]["f1"] == 1
    assert result["formal_quality_gate"] == "pass"


def test_duplicate_group_is_counted_once_and_extra_delivery_is_false_positive(case):
    run = case[1]
    run.graph.relationship_groups.append(run.graph.relationship_groups[0].model_copy(update={
        "candidate_id": "duplicate-group",
    }))
    result = score(case)
    assert result["metrics"]["relationship_group"]["tp"] == 1
    assert result["metrics"]["relationship_group"]["fp"] == 1
    assert result["duplicate_eligible_predictions"]["relationship_groups"] == 1


def test_flattened_one_of_counts_two_false_edges_and_one_missing_group(case):
    _analysis, run, reference = case
    group = run.graph.relationship_groups[0]
    run.graph.edges = [GraphEdge(
        **{**group.model_dump(exclude={"object_refs", "selection", "selection_evidence_refs"}),
           "candidate_id": f"flattened-{index}"}, object_ref=member,
    ) for index, member in enumerate(group.object_refs)]
    run.graph.relationship_groups = []
    run.graph.properties = []
    reference.properties = []
    result = score(case)
    assert result["metrics"]["relationship"]["fp"] == 2
    assert result["metrics"]["relationship_group"]["fn"] == 1
    assert result["metrics"]["assertions"] == {
        "tp": 0, "fp": 2, "fn": 1, "precision": 0, "recall": 0, "f1": 0,
    }


@pytest.mark.parametrize("mutation", [
    "no_scope", "other_member", "stale_parent", "selection", "parent_modality",
    "parent_condition", "modality", "condition", "missing_parent_proof", "missing_parent",
])
def test_scope_and_qualifier_loss_never_match_an_expected_property(case, mutation):
    _analysis, run, _reference = case
    group, prop = run.graph.relationship_groups[0], run.graph.properties[0]
    if mutation == "no_scope":
        prop.scope = TraversalScope.create()
    elif mutation in {"other_member", "stale_parent"}:
        step = prop.scope.members[0]
        prop.scope = TraversalScope.create([ScopeMember(
            relation_ref=(step.relation_ref.model_copy(update={"revision": 99})
                          if mutation == "stale_parent" else step.relation_ref),
            member_ref=(group.object_refs[1] if mutation == "other_member" else step.member_ref),
        )])
    elif mutation == "selection":
        group.selection = "all"
    elif mutation == "parent_modality":
        group.modality = "asserted"
    elif mutation == "parent_condition":
        group.conditions = ["仅在限定场景"]
    elif mutation == "modality":
        prop.modality = "required"
    elif mutation == "condition":
        prop.conditions = ["额外条件"]
    elif mutation == "missing_parent_proof":
        unit = next(unit for unit in _analysis.ir.evidence_units if "产品乙" in unit.text)
        unrelated = _analysis.ir.anchor(unit.evidence_id, 0, len(unit.text))
        for field in type(group).model_fields:
            if field.endswith("evidence_refs"):
                setattr(group, field, [unrelated])
    else:
        run.graph.relationship_groups = []
    result = score(case)
    assert result["metrics"]["property"]["tp"] == 0
    assert result["metrics"]["property"]["fp"] == 1
    assert result["metrics"]["property"]["fn"] == 1


def test_nested_scope_retains_each_parent_selection_and_qualifier(case):
    _analysis, run, reference = case
    parent = run.graph.relationship_groups[0]
    first, second = parent.object_refs
    nested = parent.model_copy(update={
        "candidate_id": "nested-prediction", "subject_ref": first,
        "selection": "alternatives", "modality": "required",
        "scope": run.graph.properties[0].scope,
    })
    run.graph.relationship_groups.append(nested)
    run.graph.properties[0].subject_ref = second
    run.graph.properties[0].scope = TraversalScope.create([
        *nested.scope.members, ScopeMember(
            relation_ref=VersionedRef(id=nested.candidate_id, revision=nested.revision),
            member_ref=second,
        ),
    ])
    parent_reference = reference.relationship_groups[0]
    first_matcher, second_matcher = parent_reference.objects
    nested_reference = parent_reference.model_copy(update={
        "reference_id": "nested-gold", "subject": first_matcher,
        "selection": "alternatives", "modality": "required",
        "scope": [ReferenceScopeMember(relation_id="gold-choice", member=first_matcher)],
    })
    reference.relationship_groups.append(nested_reference)
    reference.properties[0].subject = second_matcher
    reference.properties[0].scope = [*nested_reference.scope, ReferenceScopeMember(
        relation_id="nested-gold", member=second_matcher,
    )]
    result = score(case)
    assert result["metrics"]["relationship_group"]["tp"] == 2
    assert result["metrics"]["property"]["tp"] == 1
    nested.modality = "asserted"
    assert score(case)["metrics"]["property"]["tp"] == 0


def test_typed_qualifier_matches_meaning_without_copying_source_coordinates_into_gold(case):
    _analysis, run, reference = case
    qualifier = {"predicate_iri": "urn:context", "text": "条件甲"}
    reference.properties[0].applicability = {"qualifiers": [qualifier]}
    run.graph.properties[0].applicability = {"qualifiers": [{
        **qualifier, "evidence_refs": [run.graph.properties[0].evidence_refs[0].model_dump()],
    }]}
    assert score(case)["metrics"]["property"]["tp"] == 1
    run.graph.properties[0].applicability["qualifiers"][0]["text"] = "条件乙"
    assert score(case)["metrics"]["property"]["tp"] == 0


@pytest.mark.parametrize("field", ["modality", "scope", "relationship_groups"])
def test_v2_reference_requires_explicit_new_contract_fields(case, field):
    payload = case[2].model_dump(mode="json")
    if field == "relationship_groups":
        del payload[field]
    else:
        del payload["properties"][0][field]
    with pytest.raises(ValueError, match="v2 reference"):
        OntologyGuidedReference.model_validate(payload)


@pytest.mark.parametrize("field", ["modality", "scope"])
def test_new_prediction_omission_does_not_inherit_legacy_asserted_unscoped_defaults(case, field):
    run, reference = case[1], case[2]
    run.graph.projection_policy = "ontology-tool-graph-v1"
    prop = run.graph.properties[0]
    prop.scope = TraversalScope.create()
    reference.properties[0].scope = []
    payload = prop.model_dump(mode="json")
    del payload[field]
    run.graph.properties[0] = type(prop).model_validate(payload)
    assert score(case)["metrics"]["property"]["tp"] == 0


def test_scope_reference_rejects_dangling_members_and_cycles(case):
    payload = case[2].model_dump(mode="json")
    bad = deepcopy(payload)
    bad["properties"][0]["scope"][0]["relation_id"] = "absent"
    with pytest.raises(ValueError, match="expected parent"):
        OntologyGuidedReference.model_validate(bad)
    bad = deepcopy(payload)
    bad["properties"][0]["scope"][0]["member"]["label"] = "不属于该组"
    with pytest.raises(ValueError, match="not a parent object"):
        OntologyGuidedReference.model_validate(bad)
    bad = deepcopy(payload)
    bad["relationship_groups"][0]["scope"] = bad["properties"][0]["scope"]
    with pytest.raises(ValueError, match="cyclic"):
        OntologyGuidedReference.model_validate(bad)


def test_group_contract_rejects_singleton_duplicate_and_missing_selection(case):
    original = case[2].relationship_groups[0].model_dump(mode="json")
    for objects in ([original["objects"][0]], [original["objects"][0]] * 2):
        with pytest.raises(ValueError):
            ReferenceRelationshipGroup.model_validate({**original, "objects": objects})
    del original["selection"]
    with pytest.raises(ValueError):
        ReferenceRelationshipGroup.model_validate(original)


def test_missing_delivery_counts_recall_and_unadjudicated_groups_are_reported(case):
    _analysis, run, reference = case
    saved = run.graph.relationship_groups
    run.graph.relationship_groups = []
    assert score(case)["metrics"]["relationship_group"]["fn"] == 1
    run.graph.relationship_groups = saved
    reference.properties = []
    run.graph.properties = []
    reference.relationship_groups[0].expectation = "undetermined"
    result = score(case)
    assert result["unscored_predictions"]["relationship_groups"] == 1
    assert result["undetermined_reference_assertions"]["relationship_group"] == 1
    assert result["unscored_predictions"]["details"][0]["reason"] == "expert_undetermined"
    assert result["formal_quality_gate"] == "fail"
    run.graph.relationship_groups = []
    assert score(case)["undetermined_reference_assertions"]["relationship_group"] == 1


def test_v1_reference_keeps_omitted_extensions_and_original_scoring(tmp_path):
    analysis, run, reference = _reference_run(tmp_path)
    payload = reference.model_dump(mode="json")
    assert "relationship_groups" not in payload
    assert all("scope" not in item and "modality" not in item
               for item in [*payload["properties"], *payload["relationships"]])
    restored = OntologyGuidedReference.model_validate(payload)
    assert restored.model_dump(mode="json") == payload
    assert score_evaluation(run, restored, ir=analysis.ir)["metrics"]["overall"]["f1"] == 1
