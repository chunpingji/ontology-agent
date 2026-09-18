"""Guard the probe's acceptance checks without contacting a model service."""

from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.evaluation.schema_card_probe import (
    CMC,
    Extraction,
    check_extraction,
    compact_predicate,
    expand_from_mentions,
    retrieve,
    schema_for,
)
from app.services.extraction.ontology_guided.contracts import EdgeSpec, RangeClass

PRODUCT = "https://example.test/DrugProduct"
API = "https://example.test/ActiveIngredient"
PREDICATE = "https://example.test/hasActiveIngredient"


@pytest.fixture
def sample():
    source = "产品甲含有活性成分乙。"
    card = {
        "schema_id": PRODUCT, "class_iri": PRODUCT, "label": "产品", "description": "",
        "parents": [], "properties": [],
        "allowed_relations": [{"iri": PREDICATE, "kind": "relationship",
                               "constraint_status": "resolved", "range_class_iris": [API]}],
    }
    case = {"scope_id": "sample", "document_title": "测试报告",
            "content": {"text": source, "section_path": "产品"},
            "source_units": [{"evidence_id": "u1", "text": source}]}
    result = {
        "scope_id": "sample",
        "mentions": [
            {"id": "p", "text": "产品甲", "class_iri": PRODUCT,
             "evidence_id": "u1", "evidence": source},
            {"id": "a", "text": "活性成分乙", "class_iri": API,
             "evidence_id": "u1", "evidence": source},
        ],
        "claims": [{"subject_id": "p", "predicate_iri": PREDICATE,
                    "object_id": "a", "literal_value": "", "polarity": "affirmed",
                    "condition": "", "evidence_id": "u1", "evidence": source}],
        "open_slots": [], "schema_gaps": [],
    }
    return case, result, [card]


def test_allowed_relation_and_source_quote(sample):
    assert check_extraction(*sample) == []


@pytest.mark.parametrize("mutation, error", [
    ({"subject_id": "a", "object_id": "p"}, "predicate_not_allowed_for_subject"),
    ({"object_id": "missing"}, "range_or_endpoint_mismatch"),
    ({"predicate_iri": "invented"}, "predicate_not_allowed_for_subject"),
    ({"evidence": "模型编造的证据"}, "claim_evidence_not_in_source"),
    ({"evidence_id": "wrong_cell"}, "claim_evidence_id_mismatch"),
    ({"condition": "原文没有的条件"}, "condition_not_in_source"),
])
def test_semantic_boundary_rejections(sample, mutation, error):
    case, result, cards = sample
    result["claims"][0].update(mutation)
    assert error in check_extraction(case, result, cards)


def test_schema_forbids_unknown_fields_and_wrong_types(sample):
    result = sample[1]
    result["claims"][0]["confidence"] = 0.99
    with pytest.raises(ValidationError):
        Extraction.model_validate(result)
    del result["claims"][0]["confidence"]
    result["claims"][0]["literal_value"] = 100
    with pytest.raises(ValidationError):
        Extraction.model_validate(result)


def test_unresolved_card_does_not_authorize_predicate(sample):
    case, result, cards = sample
    cards[0]["allowed_relations"][0]["constraint_status"] = "constraint_unresolved"
    assert "predicate_not_allowed_for_subject" in check_extraction(case, result, cards)
    assert schema_for(cards)["properties"]["claims"]["maxItems"] == 0


def test_retrieval_does_not_turn_model_hints_into_ontology(sample):
    case, _, cards = sample
    root = {**deepcopy(cards[0]), "schema_id": CMC, "class_iri": CMC, "label": "CMCReport"}
    request = {"semantic_query": {"intent": "invented", "anchors": []},
               "candidate_concepts": ["InventedClass"], "open_slots": [],
               "retrieval": {"top_k": 3, "max_depth": 1, "include_siblings": False}}
    selected = retrieve(case, request, {PRODUCT: cards[0], CMC: root})
    assert selected["cards"][0]["class_iri"] == CMC
    assert {c["class_iri"] for c in selected["cards"]} == {CMC, PRODUCT}


def test_compaction_preserves_formal_ranges_and_constraints():
    relation = EdgeSpec(
        iri=PREDICATE, label="含有活性成分", description="原文必须支持组成关系",
        declared_by=[PRODUCT], range_class_iris=[API, "https://example.test/APIChild"],
        range_classes=[RangeClass(iri=API, label="活性成分")],
        multiplicity="single", min_count=0, max_count=1,
    )
    compact = compact_predicate(relation)
    assert "range_classes" not in compact
    assert compact["range_class_iris"] == relation.range_class_iris
    assert compact["declared_by"] == [PRODUCT]
    assert compact["description"] == relation.description
    assert compact["constraint_status"] == "resolved"
    assert (compact["min_count"], compact["max_count"]) == (0, 1)


def test_endpoint_card_expansion_uses_only_known_ontology_types(sample):
    _, result, cards = sample
    api_card = {**deepcopy(cards[0]), "class_iri": API, "schema_id": API}
    result["mentions"].append({"class_iri": "invented"})
    expanded, added = expand_from_mentions(cards, result, {PRODUCT: cards[0], API: api_card})
    assert added == [API]
    assert {card["class_iri"] for card in expanded} == {PRODUCT, API}
