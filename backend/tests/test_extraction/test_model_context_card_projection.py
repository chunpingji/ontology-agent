"""Ontology read views retain each verifier's constraints and each member's authority."""

from copy import deepcopy

import pytest

from app.services.extraction.evidence_identity import canonical_json
from app.services.extraction.ontology_guided.model_context_projection import compact_model_context


def property_card(iri, *, max_count=1):
    return {
        "iri": iri, "kind": "property", "label": f"标签 {iri}",
        "description": "完整定义：条件成立时才适用；未提及不等于否定。" * 3,
        "declared_by": ["urn:Base"], "datatype_iris": ["urn:string"],
        "min_count": 0, "max_count": max_count, "constraint_status": "resolved",
    }


def class_card(iri):
    return {
        "class_iri": iri, "label": iri,
        "properties": [property_card(prop) for prop in (
            "urn:value", "urn:code", "urn:namespace", "urn:condition", "urn:bridge", "urn:unused",
        )],
        "quantity_policies": [
            {"predicate_iri": prop, "allowed_forms": ["scalar"], "unit_requirement": "physical"}
            for prop in ("urn:value", "urn:unused")
        ],
        "identity_keys": [{"class_iri": iri, "property_iris": ["urn:code", "urn:namespace"],
                           "namespace": "within-document", "scope": "document",
                           "declaration_ref": f"declaration-{iri}"}],
        "unsupported_constraints": [
            {"predicate_iri": prop, "construct": "intersection", "reason_code": "unresolved"}
            for prop in ("urn:value", "urn:unused", "urn:unexpanded")
        ],
    }


def record_member(task="task-a", *, stage="discovery", class_iri="urn:A"):
    entity = {"entity_ref": {"id": f"entity-{task}", "revision": 2}, "class_iri": class_iri,
              "source_refs": [{"evidence_id": f"evidence-{task}"}], "content_hash": "frozen"}
    return {
        "task_id": task, "stage": stage,
        "schema_card": {"kind": "record_discovery", "schema_card_id": f"card-{task}",
                        "class_cards": [class_card("urn:A"), class_card("urn:B")]},
        "registered_entities": [entity],
        "evidence_units": [{"evidence_id": f"evidence-{task}", "text": "条件成立时数值为 5。",
                            "fact_eligible": True, "role": "target"}],
        "verification_input": {
            "targets": [{"target_id": f"target-{task}", "content_hash": "frozen-target",
                         "target_kind": "property", "payload": {
                             "subject_id": entity["entity_ref"]["id"],
                             "predicate_iri": "urn:value", "qualifiers": {
                                 "polarity": "negated", "condition_support": ["逐字条件"],
                                 "scope_qualifiers": [{"predicate_iri": "urn:condition"}],
                             },
                         }}],
            "entity_dependencies": [deepcopy(entity)],
            "bridge_dependencies": [{"steps": [{"predicate_iri": "urn:bridge"}]}],
            "scope_resolutions": [{"steps": [{"polarity": "negated", "conditions": ["条件"]}]}],
        } if stage == "verification" else None,
    }


def expanded_properties(card, owner):
    definitions = card.get("property_definitions", {})
    return [deepcopy(definitions[value["property_ref"]]) if "property_ref" in value else value
            for value in owner["properties"]]


def test_record_discovery_shares_only_complete_identical_definitions_and_preserves_all_classes():
    value = record_member()
    before = deepcopy(value)
    result = compact_model_context(value)
    card = result["schema_card"]
    assert [row["class_iri"] for row in card["class_cards"]] == ["urn:A", "urn:B"]
    for old, new in zip(value["schema_card"]["class_cards"], card["class_cards"]):
        assert {**new, "properties": expanded_properties(card, new)} == old
    assert result["evidence_units"] == value["evidence_units"]
    assert value == before
    assert compact_model_context(result) == result
    assert len(canonical_json(result)) < len(canonical_json(value)) * .8


def test_identical_iris_with_different_cardinality_remain_distinct():
    value = record_member()
    value["schema_card"]["class_cards"][1]["properties"][0]["max_count"] = 2
    card = compact_model_context(value)["schema_card"]
    left, right = card["class_cards"]
    assert left["properties"][0] == value["schema_card"]["class_cards"][0]["properties"][0]
    assert right["properties"][0] == value["schema_card"]["class_cards"][1]["properties"][0]
    assert left["properties"][1]["property_ref"] == right["properties"][1]["property_ref"]


def test_verification_keeps_target_qualifier_bridge_identity_and_unexpanded_constraints():
    value = record_member(stage="verification")
    before = deepcopy(value)
    result = compact_model_context(value)
    card = result["schema_card"]
    assert [owner["class_iri"] for owner in card["class_cards"]] == ["urn:A"]
    owner = card["class_cards"][0]
    assert {prop["iri"] for prop in owner["properties"]} == {
        "urn:value", "urn:code", "urn:namespace", "urn:condition", "urn:bridge",
    }
    assert owner["identity_keys"] == value["schema_card"]["class_cards"][0]["identity_keys"]
    assert [item["predicate_iri"] for item in owner["quantity_policies"]] == ["urn:value"]
    assert [item["predicate_iri"] for item in owner["unsupported_constraints"]] == [
        "urn:value", "urn:unexpanded",
    ]
    verification = result["verification_input"]
    assert verification["targets"] == value["verification_input"]["targets"]
    assert verification["bridge_dependencies"] == value["verification_input"]["bridge_dependencies"]
    assert verification["scope_resolutions"] == value["verification_input"]["scope_resolutions"]
    assert verification["entity_dependency_refs"] == [value["registered_entities"][0]["entity_ref"]]
    assert compact_model_context(result) == result
    assert value == before


def test_batch_cards_and_entity_reference_selection_remain_member_local():
    left = record_member("left", stage="verification", class_iri="urn:A")
    right = record_member("right", stage="verification", class_iri="urn:B")
    right["schema_card"]["class_cards"][1]["properties"][0]["max_count"] = 2
    right["evidence_units"][0].update(fact_eligible=False, role="binding")
    value = {"stage": "verification", "members": [left, right]}
    result = compact_model_context(value)
    for original, projected, expected in zip(
        value["members"], result["members"], [("urn:A", 1), ("urn:B", 2)],
    ):
        cards = projected["schema_card"]["class_cards"]
        assert len(cards) == 1 and cards[0]["class_iri"] == expected[0]
        assert cards[0]["properties"][0]["max_count"] == expected[1]
        assert projected["evidence_units"] == original["evidence_units"]
        assert projected["verification_input"]["entity_dependency_refs"] == [
            original["registered_entities"][0]["entity_ref"],
        ]
    assert compact_model_context(result) == result


def test_scalar_entity_verification_retains_relation_full_range_and_constraints():
    relation = {"iri": "urn:contains", "kind": "relationship", "max_count": 2,
                "range_class_iris": ["urn:A", "urn:B"],
                "range_classes": [{"iri": "urn:A", "label": "甲"},
                                  {"iri": "urn:B", "label": "乙"}]}
    value = {"stage": "verification", "schema_card": {
        "class_iris": ["urn:Subject", "urn:A", "urn:B"],
        "predicates": [relation, property_card("urn:unused")],
        "identity_keys": [], "quantity_policies": [], "unsupported_constraints": [],
    }, "verification_input": {"targets": [{"target_kind": "entity", "payload": {
        "class_iri": "urn:A", "identifier_claims": [],
    }}]}}
    result = compact_model_context(value)
    predicates = result["schema_card"]["predicates"]
    assert len(predicates) == 2
    assert predicates[0] == {key: item for key, item in relation.items()
                             if key != "range_class_iris"}
    assert predicates[1] == value["schema_card"]["predicates"][1]
    assert result["schema_card"]["class_iris"] == value["schema_card"]["class_iris"]
    assert result["verification_input"] == value["verification_input"]
    assert compact_model_context(result) == result


def test_entity_identifier_claim_keeps_complete_type_card_without_an_identity_key():
    value = record_member(stage="verification")
    for card in value["schema_card"]["class_cards"]:
        card["identity_keys"] = []
    verification = value["verification_input"]
    verification["targets"] = [{"target_kind": "entity", "payload": {
        "class_iri": "urn:A", "identifier_claims": [{"predicate_iri": "urn:code"}],
    }}]
    verification["bridge_dependencies"] = []
    result = compact_model_context(value)
    assert result["schema_card"]["class_cards"] == [value["schema_card"]["class_cards"][0]]


def test_unknown_record_owner_does_not_guess_from_other_batch_members():
    value = record_member(stage="verification")
    value["verification_input"]["entity_dependencies"] = []
    other = record_member("other", stage="verification")
    result = compact_model_context({"members": [value, other]})
    card = result["members"][0]["schema_card"]
    assert [owner["class_iri"] for owner in card["class_cards"]] == ["urn:A", "urn:B"]
    for old, new in zip(value["schema_card"]["class_cards"], card["class_cards"]):
        assert expanded_properties(card, new) == old["properties"]


@pytest.mark.parametrize("representation", ["record", "mention"])
def test_entity_type_verification_preserves_unmentioned_fields_and_negative_constraints(
    representation,
):
    value = record_member(stage="verification")
    owner = value["schema_card"]["class_cards"][0]
    owner["identity_keys"] = []
    owner["properties"][0].update(
        min_count=1, max_count=1, constraint_status="constraint_unresolved",
    )
    value["verification_input"]["targets"] = [{
        "target_id": "entity-target", "target_kind": "entity",
        "required_facets": ["type", "referent", "subject_role"], "payload": {
            "local_id": "entity", "class_iri": "urn:A", "representation": representation,
            "mentions": [{"evidence_id": "e", "text": "候选记录"}],
            "record_components": [
                {"role": "field", "quote": {"evidence_id": "e", "text": "含量"}},
                {"role": "value", "quote": {"evidence_id": "e", "text": "不适用"}},
            ], "identifier_claims": [],
        },
    }]
    # Neither the target nor any dependency names the field whose constraints
    # can distinguish this record type or provide counterevidence.
    value["verification_input"]["bridge_dependencies"] = []
    before = deepcopy(value)
    result = compact_model_context(value)
    assert result["schema_card"]["class_cards"] == [owner]
    assert result["verification_input"]["targets"] == before["verification_input"]["targets"]
    assert result["evidence_units"] == before["evidence_units"]
    assert compact_model_context(result) == result
    assert value == before


def test_one_resolved_owner_does_not_allow_narrowing_an_unresolved_property_target():
    value = record_member(stage="verification")
    unknown = deepcopy(value["verification_input"]["targets"][0])
    unknown["target_id"] = "unknown-owner-target"
    unknown["payload"]["subject_id"] = "unresolved-owner"
    value["verification_input"]["targets"].append(unknown)
    card = compact_model_context(value)["schema_card"]
    assert len(card["class_cards"]) == len(value["schema_card"]["class_cards"])
    for old, new in zip(value["schema_card"]["class_cards"], card["class_cards"]):
        assert {**new, "properties": expanded_properties(card, new)} == old
