"""Fixed answer keys make duplicate/omitted stage targets undecodable."""

from copy import deepcopy

import jsonschema
import pytest
from pydantic import ValidationError

from app.services.document_harness.model import gateway_schema
from app.services.document_harness.protocols import (
    INSTRUCTIONS,
    Discovery,
    EvidenceReview,
    PropertyAlignment,
    RelationAlignment,
    TypeAlignment,
    stage_schema,
    validate_paid_output,
)


def type_choice(**changes):
    return {
        "class_iri": "urn:legal:Type",
        "confidence": 0.9,
        "evidence": ["S1"],
        "reason": "original role",
        **changes,
    }


def judgment(**changes):
    return {
        "verdict": "accepted",
        "confidence": 0.95,
        "evidence": ["S1"],
        "reason": "source checked",
        **changes,
    }


def property_choice(**changes):
    predicate = changes.pop("predicate_iri", "urn:legal:property")
    return {
        "mappings": [
            {
                "predicate_iri": predicate,
                "confidence": 0.9,
                "value_component": "whole",
                "value_quote": None,
            }
        ]
        if predicate
        else [],
        "reason": "original field meaning",
        **changes,
    }


@pytest.mark.parametrize(
    "field",
    [
        "entities",
        "document_field_ids",
        "document_source_fields",
        "unowned_fields",
        "relation_hints",
    ],
)
def test_discovery_requires_an_explicit_result_for_each_task_even_when_empty(field):
    schema = stage_schema("discover", source_ids=["S1"])
    valid = {
        "entities": [],
        "document_field_ids": [],
        "document_source_fields": [],
        "unowned_fields": [],
        "relation_hints": [],
        "complete": True,
    }
    jsonschema.validate(valid, schema)
    Discovery.model_validate(valid)
    del valid[field]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(valid, schema)
    with pytest.raises(ValidationError):
        Discovery.model_validate(valid)


@pytest.mark.parametrize("field", ["field_ids", "source_fields"])
def test_discovery_mention_field_assignment_must_be_explicit_even_when_empty(field):
    schema = stage_schema("discover", source_ids=["S1"])
    valid = {
        "entities": [
            {
                "local_id": "e",
                "role": "object",
                "evidence": ["S1"],
                "anchor": {"source_id": "S1"},
                "field_ids": [],
                "source_fields": [],
            }
        ],
        "document_field_ids": [],
        "document_source_fields": [],
        "unowned_fields": [],
        "relation_hints": [],
        "complete": True,
    }
    jsonschema.validate(valid, schema)
    Discovery.model_validate(valid)
    del valid["entities"][0][field]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(valid, schema)
    with pytest.raises(ValidationError):
        Discovery.model_validate(valid)


def test_type_schema_requires_each_exact_key_once_without_id_inside_values():
    ids = ["E1", "E3", "E2"]
    schema = stage_schema(
        "type_alignment", source_ids=["S1"], entity_ids=ids, class_iris=["urn:legal:Type"]
    )
    mapping = schema["properties"]["entities"]
    assert mapping["type"] == "object"
    assert list(mapping["properties"]) == ids
    assert mapping["required"] == ids
    assert mapping["additionalProperties"] is False
    assert all(
        "entity_id" not in branch["properties"] for branch in schema["$defs"]["TypeChoice"]["anyOf"]
    )
    valid = {"entities": {key: type_choice() for key in reversed(ids)}}
    jsonschema.validate(valid, schema)
    assert set(TypeAlignment.model_validate(valid).entities) == set(ids)
    malformed = [
        {"entities": {"E1": type_choice(), "E3": type_choice()}},
        {"entities": {**valid["entities"], "E4": type_choice()}},
        {"entities": [{"entity_id": key, **type_choice()} for key in ("E1", "E3", "E3", "E3")]},
        {"entities": {**valid["entities"], "E1": type_choice(entity_id="E1")}},
    ]
    for value in malformed:
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(value, schema)


def test_type_shard_accepts_null_but_never_an_iri_from_another_shard_or_foreign_source():
    schema = stage_schema(
        "type_alignment", source_ids=["S1"], entity_ids=["E1"], class_iris=["urn:legal:Type"]
    )
    value = {
        "entities": {"E1": type_choice(class_iri=None, evidence=[], reason="not in this shard")}
    }
    jsonschema.validate(value, schema)
    for changed in (type_choice(class_iri="urn:other:Type"), type_choice(evidence=["S2"])):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({"entities": {"E1": changed}}, schema)
    assert "分片" in INSTRUCTIONS["type_alignment"]
    assert "不能改变原实体指称" in INSTRUCTIONS["type_alignment"]


@pytest.mark.parametrize("missing_evidence", [True, False])
def test_nonnull_type_must_supply_nonempty_current_source_ids(missing_evidence):
    schema = stage_schema(
        "type_alignment", source_ids=["S1"], entity_ids=["E1"], class_iris=["urn:legal:Type"]
    )
    choice = type_choice(evidence=[])
    if missing_evidence:
        choice.pop("evidence")
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"entities": {"E1": choice}}, schema)
    choice["class_iri"] = None
    jsonschema.validate({"entities": {"E1": choice}}, schema)
    jsonschema.validate({"entities": {"E1": type_choice()}}, schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"entities": {"E1": type_choice(evidence=["foreign"])}}, schema)


def test_empty_type_shard_only_allows_null_and_never_a_placeholder_type():
    schema = stage_schema("type_alignment", source_ids=["S1"], entity_ids=["E1"], class_iris=[])
    jsonschema.validate({"entities": {"E1": type_choice(class_iri=None, evidence=[])}}, schema)
    for iri in ("urn:legal:Type", "__unavailable__"):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({"entities": {"E1": type_choice(class_iri=iri)}}, schema)


def test_review_schema_binds_exact_candidate_keys_and_preserves_nonempty_acceptance_support():
    schema = stage_schema("evidence_review", source_ids=["S1"], candidate_ids=["C1", "C2"])
    mapping = schema["properties"]["judgments"]
    assert mapping["required"] == ["C1", "C2"]
    assert set(mapping["properties"]) == {"C1", "C2"}
    assert mapping["additionalProperties"] is False
    valid = {
        "type_concerns": [],
        "judgments": {"C2": judgment(), "C1": judgment(verdict="unresolved", evidence=[])},
    }
    jsonschema.validate(valid, schema)
    assert set(EvidenceReview.model_validate(valid).judgments) == {"C1", "C2"}
    malformed = [
        {"judgments": {"C1": judgment()}},
        {"judgments": {**valid["judgments"], "foreign": judgment()}},
        {"judgments": [{"candidate_id": "C1", **judgment()}]},
        {"judgments": {**valid["judgments"], "C1": judgment(candidate_id="C1")}},
        {"judgments": {**valid["judgments"], "C1": judgment(evidence=[])}},
    ]
    for value in malformed:
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({"type_concerns": [], **value}, schema)


def test_property_schema_requires_all_field_keys_and_allows_explicit_unmatched_results():
    ids = ["F1", "F3", "F2"]
    schema = stage_schema(
        "property_alignment",
        source_ids=["S1"],
        field_ids=ids,
        property_iris=["urn:legal:property"],
    )
    assert "properties" in schema["required"]
    mapping = schema["properties"]["properties"]
    assert mapping["type"] == "object"
    assert list(mapping["properties"]) == ids
    assert mapping["required"] == ids
    assert mapping["additionalProperties"] is False
    assert "field_id" not in schema["$defs"]["PropertyChoice"]["properties"]
    valid = {
        "properties": {
            "F2": property_choice(predicate_iri=None, reason="no match in current card"),
            "F3": property_choice(),
            "F1": property_choice(),
        },
    }
    jsonschema.validate(valid, schema)
    assert set(PropertyAlignment.model_validate(valid).properties) == set(ids)
    malformed = [
        {},
        {**valid, "properties": {"F1": property_choice(), "F3": property_choice()}},
        {**valid, "properties": {**valid["properties"], "F4": property_choice()}},
        {**valid, "properties": [{"field_id": key, **property_choice()} for key in ids]},
        {**valid, "properties": {**valid["properties"], "F1": property_choice(field_id="F1")}},
        {
            **valid,
            "properties": {
                **valid["properties"],
                "F1": property_choice(predicate_iri="urn:foreign:property"),
            },
        },
        {**valid, "properties": {**valid["properties"], "F1": property_choice(reason="")}},
    ]
    for value in malformed:
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(value, schema)
    with pytest.raises(ValidationError):
        PropertyAlignment.model_validate({})


def test_a_range_field_can_map_to_two_legal_predicates_without_model_generated_values():
    schema = stage_schema(
        "property_alignment", field_ids=["F1"], property_iris=["urn:test:p", "urn:test:q"]
    )
    choice = {
        "mappings": [
            {
                "predicate_iri": "urn:test:p",
                "value_component": "lower",
                "value_quote": None,
                "confidence": 0.95,
            },
            {
                "predicate_iri": "urn:test:q",
                "value_component": "upper",
                "value_quote": None,
                "confidence": 0.95,
            },
        ],
        "reason": "两个端点，保留原范围",
    }
    output = {"properties": {"F1": choice}}
    jsonschema.validate(output, schema)
    PropertyAlignment.model_validate(output)
    for extra in (
        {"value": 4.0},
        {"predicate_iri": "urn:foreign:p"},
        {"value_component": "invented"},
    ):
        invalid = deepcopy(output)
        invalid["properties"]["F1"]["mappings"][0].update(extra)
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(invalid, schema)
    with pytest.raises(ValidationError):
        PropertyAlignment.model_validate(
            {
                "properties": {
                    "F1": {
                        "predicate_iri": "urn:test:p",
                        "confidence": 0.9,
                        "reason": "retired single-property format",
                    }
                },
            }
        )


def relation_proposal(**changes):
    return {
        "verdict": "proposed",
        "evidence": ["S1"],
        "polarity": "positive",
        "conditions": [],
        "participation": None,
        "selection": None,
        "timing": None,
        "missing_context": "none",
        "reason": "explicit original relation",
        "confidence": 0.95,
        **changes,
    }


def test_empty_field_input_requires_empty_map_and_relations_have_separate_fixed_candidates():
    schema = stage_schema("property_alignment")
    jsonschema.validate({"properties": {}}, schema)
    for output in (
        {},
        {"properties": []},
        {"properties": {"F1": property_choice()}},
        {"properties": {}, "relations": []},
    ):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(output, schema)
    items = [{"candidate_id": key, "object_ids": ["E1"]} for key in ("C1", "C2")]
    schema = stage_schema("relation_alignment", source_ids=["S1"], relation_items=items)
    valid = {"proposals": {key: relation_proposal() for key in ("C1", "C2")}}
    jsonschema.validate(valid, schema)
    assert len(RelationAlignment.model_validate(valid).proposals) == 2
    validate_paid_output(
        "relation_alignment",
        {"items": items},
        valid,
        schema=schema,
    )


def test_relation_alignment_requires_an_explicit_candidate_map_even_when_empty():
    schema = stage_schema("relation_alignment")
    jsonschema.validate({"proposals": {}}, schema)
    RelationAlignment.model_validate({"proposals": {}})
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({}, schema)
    with pytest.raises(ValidationError):
        RelationAlignment.model_validate({})
    with pytest.raises(KeyError):
        stage_schema("assertion_alignment")


@pytest.mark.parametrize("stage", ["type_alignment", "evidence_review", "property_alignment"])
def test_stage_schema_rejects_duplicate_input_ids_without_silently_deduplicating(stage):
    ids = {
        "type_alignment": {"entity_ids": ["E1", "E1"]},
        "evidence_review": {"candidate_ids": ["C1", "C1"]},
        "property_alignment": {"field_ids": ["F1", "F1"]},
    }[stage]
    with pytest.raises(ValueError, match="duplicate_stage_input_id"):
        stage_schema(stage, **ids)


@pytest.mark.parametrize(
    "model,field,value,old_key,extra",
    [
        (TypeAlignment, "entities", type_choice(), "entity_id", {}),
        (EvidenceReview, "judgments", judgment(), "candidate_id", {"type_concerns": []}),
        (PropertyAlignment, "properties", property_choice(), "field_id", {}),
    ],
)
def test_old_array_protocol_and_repeated_ids_inside_values_are_not_converted(
    model, field, value, old_key, extra
):
    with pytest.raises(ValidationError):
        model.model_validate({**extra, field: [{old_key: "ID1", **value}]})
    with pytest.raises(ValidationError):
        model.model_validate({**extra, field: {"ID1": {old_key: "ID1", **value}}})
    saved = deepcopy(value)
    model.model_validate({**extra, field: {"ID1": value}})
    assert value == saved


@pytest.mark.parametrize(
    "stage",
    [
        "type_alignment",
        "property_alignment",
        "relation_alignment",
        "entity_review",
        "evidence_review",
    ],
)
@pytest.mark.parametrize("mutation", ["extra_target", "missing_target", "foreign_source"])
def test_paid_validation_rejects_target_and_menu_changes_before_business_application(
    stage, mutation
):
    ids = ["C1", "C2"]
    parameters = {
        "source_ids": ["S1"],
        "candidate_ids": ids,
        "entity_ids": ids,
        "field_ids": ids,
        "class_iris": ["urn:legal:Type"],
        "property_iris": ["urn:legal:property"],
    }
    if stage == "type_alignment":
        field, item = "entities", type_choice()
        payload = {"entities": [{"entity_id": key} for key in ids]}
    elif stage == "property_alignment":
        field, item = "properties", property_choice()
        item["mappings"][0].update(
            value_component="span",
            value_quote={
                "source_id": "S1",
                "text": "original value",
                "occurrence": None,
            },
        )
        payload = {"property_field_ids": ids}
    elif stage == "relation_alignment":
        field, item = "proposals", relation_proposal()
        payload = {"items": [{"candidate_id": key, "object_ids": ["E1"]} for key in ids]}
        parameters["relation_items"] = payload["items"]
    else:
        field, item = "judgments", judgment()
        payload = {"candidates": [{"id": key} for key in ids]}
    output = {field: {key: deepcopy(item) for key in ids}}
    if stage == "evidence_review":
        output["type_concerns"] = []
    schema = stage_schema(stage, **parameters)
    validate_paid_output(stage, payload, output, schema=schema)
    if mutation == "extra_target":
        output[field]["C3"] = deepcopy(item)
    elif mutation == "missing_target":
        del output[field]["C2"]
    elif stage == "property_alignment":
        output[field]["C1"]["mappings"][0]["value_quote"]["source_id"] = "S9"
    else:
        output[field]["C1"]["evidence"] = ["S9"]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(output, schema)
    with pytest.raises(ValueError):
        validate_paid_output(stage, payload, output, schema=schema)


@pytest.mark.parametrize("value", ["0.95", True])
def test_paid_message_rejects_type_coercion_before_schema_choices(value):
    schema = stage_schema("entity_review", source_ids=["S1"], candidate_ids=["C1"])
    with pytest.raises(ValueError):
        validate_paid_output(
            "entity_review",
            {"candidates": [{"id": "C1"}]},
            {
                "judgments": {"C1": judgment(confidence=value)},
            },
            schema=schema,
        )


def test_paid_review_requires_both_group_and_timing_judgments():
    payload = {"candidates": [{"id": "R1"}, {"id": "T1"}]}
    schema = stage_schema("evidence_review", source_ids=["S1"], candidate_ids=["R1", "T1"])
    valid = {
        "type_concerns": [],
        "judgments": {
            "R1": judgment(),
            "T1": judgment(verdict="unresolved", evidence=[]),
        },
    }
    validate_paid_output("evidence_review", payload, valid, schema=schema)
    del valid["judgments"]["T1"]
    with pytest.raises(ValueError, match="evidence_review_answer_set_mismatch"):
        validate_paid_output("evidence_review", payload, valid)
    with pytest.raises(ValueError):
        validate_paid_output("evidence_review", payload, valid, schema=schema)


def test_paid_coreference_alias_binding_is_per_pair_not_any_available_source():
    sources = ["S1", "S2", "S3"]
    pairs = {"P1": ("S1", "S2"), "P2": ("S2", "S3")}
    schema = stage_schema(
        "coreference_review", source_ids=sources, candidate_ids=list(pairs), pair_sources=pairs
    )
    payload = {"pairs": [{"pair_id": key} for key in pairs]}
    output = {
        "judgments": {
            key: {
                "verdict": "same",
                "basis": "explicit_alias",
                "confidence": 0.99,
                "evidence": list(pair),
                "proof": [],
                "reason": "explicit alias",
                "alias_binding": {
                    "left": {"source_id": pair[0], "text": "original", "occurrence": None},
                    "right": {"source_id": pair[1], "text": "alias", "occurrence": None},
                    "declaration": {
                        "source_id": pair[0],
                        "text": "original called alias",
                        "occurrence": None,
                    },
                },
            }
            for key, pair in pairs.items()
        }
    }
    validate_paid_output("coreference_review", payload, output, schema=schema)
    output["judgments"]["P1"]["alias_binding"]["right"]["source_id"] = "S3"
    with pytest.raises(ValueError, match="harness_output_schema_mismatch"):
        validate_paid_output("coreference_review", payload, output, schema=schema)


def test_empty_property_menu_cannot_accept_an_unavailable_placeholder_predicate():
    schema = stage_schema("property_alignment", field_ids=["F1"], property_iris=[])
    payload = {"property_field_ids": ["F1"]}
    validate_paid_output(
        "property_alignment",
        payload,
        {
            "properties": {"F1": property_choice(predicate_iri=None)},
        },
        schema=schema,
    )
    with pytest.raises(ValueError, match="harness_output_schema_mismatch"):
        validate_paid_output(
            "property_alignment",
            payload,
            {
                "properties": {"F1": property_choice(predicate_iri="__unavailable__")},
            },
            schema=schema,
        )


def test_group_interpretation_requires_evidence_and_consistent_participation():
    schema = stage_schema("group_interpretation", source_ids=["S1"])
    valid = {
        "verdict": "supported",
        "participation": "all",
        "selection": "unspecified",
        "timing": "parallel",
        "evidence": ["S1"],
        "reason": "explicit joint statement",
    }
    validate_paid_output("group_interpretation", {}, valid, schema=schema)
    for change in (
        {"evidence": ["S9"]},
        {"evidence": []},
        {"participation": "unknown"},
        {"selection": "exactly_one"},
        {"object_ids": ["changed"]},
    ):
        with pytest.raises(ValueError):
            validate_paid_output("group_interpretation", {}, {**valid, **change}, schema=schema)


def test_relation_proposal_checks_single_and_group_semantics_against_frozen_input():
    single = {"items": [{"candidate_id": "C1", "object_ids": ["E1"]}]}
    group = {"items": [{"candidate_id": "C1", "object_ids": ["E1", "E2"]}]}
    proposal = {"proposals": {"C1": relation_proposal()}}
    validate_paid_output("relation_alignment", single, proposal)
    with pytest.raises(ValueError, match="group_relation_requires_complete_semantics"):
        validate_paid_output("relation_alignment", group, proposal)
    proposal["proposals"]["C1"].update(
        participation="options", selection="unspecified", timing="unspecified"
    )
    validate_paid_output("relation_alignment", group, proposal)
    with pytest.raises(ValueError, match="single_relation_has_group_semantics"):
        validate_paid_output("relation_alignment", single, proposal)


@pytest.mark.parametrize("verdict", ["proposed", "no_relation", "unresolved"])
@pytest.mark.parametrize("target,participation,selection,timing,valid", [
    ("single", None, None, None, True),
    # The observed failure: single endpoints carrying a partial options judgment.
    ("single", "options", "exactly_one", None, False),
    ("single", "options", "exactly_one", "unspecified", False),
    ("single", None, "unspecified", None, False),
    ("single", None, None, "unspecified", False),
    ("group", "options", "exactly_one", "unspecified", True),
    ("group", "options", "unspecified", "unspecified", True),
    ("group", "all", "unspecified", "parallel", True),
    ("group", "all", "unspecified", "sequential", True),
    ("group", "unknown", "unspecified", "unspecified", True),
    ("group", None, None, None, False),
    ("group", "options", "exactly_one", None, False),
    ("group", "all", "exactly_one", "unspecified", False),
    ("group", "options", "unspecified", "parallel", False),
    ("group", "unknown", "exactly_one", "unspecified", False),
])
def test_mixed_relation_schema_constrains_each_frozen_candidate(
    verdict, target, participation, selection, timing, valid,
):
    items = [
        {"candidate_id": "single", "object_ids": ["E1"]},
        {"candidate_id": "group", "object_ids": ["E1", "E2"]},
    ]
    schema = stage_schema("relation_alignment", source_ids=["S1"], relation_items=items)
    output = {"proposals": {
        "single": relation_proposal(),
        "group": relation_proposal(participation="all", selection="unspecified", timing="parallel"),
    }}
    output["proposals"][target].update(
        verdict=verdict, participation=participation, selection=selection, timing=timing,
    )
    for contract in (schema, gateway_schema(schema)):
        if valid:
            jsonschema.validate(output, contract)
            validate_paid_output("relation_alignment", {"items": items}, output, schema=contract)
        else:
            with pytest.raises(jsonschema.ValidationError):
                jsonschema.validate(output, contract)
            with pytest.raises(ValueError):
                validate_paid_output(
                    "relation_alignment", {"items": items}, output, schema=contract,
                )
