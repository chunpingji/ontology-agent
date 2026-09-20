"""Model schema compaction keeps authorization and strict answer boundaries."""

from copy import deepcopy
from itertools import product

import pytest

from app.services.extraction.ontology_guided.contracts import EdgeSpec
from app.services.extraction.ontology_guided.model_schema_projection import compact_answer_schema
from app.services.extraction.ontology_guided.recognition_batch import compile_batch_stage_schema
from tests.test_extraction.test_recognition_batch import batch_members  # noqa: F401
from tests.test_extraction.test_tool_engine_context import authorized_context  # noqa: F401


def _refs(value):
    if isinstance(value, dict):
        if "$ref" in value:
            yield value["$ref"]
        for child in value.values():
            yield from _refs(child)
    elif isinstance(value, list):
        for child in value:
            yield from _refs(child)


def _resolve(schema, reference):
    value = schema
    for segment in reference[2:].split("/"):
        value = value[segment.replace("~1", "/").replace("~0", "~")]
    return value


def test_compaction_preserves_literal_values_and_properties_named_title():
    literal = {"title": "meaningful data", "$ref": "#/$defs/NotASchemaReference"}
    schema = {
        "type": "object", "title": "Verbose root", "additionalProperties": False,
        "description": "Business meaning must survive compaction.",
        "properties": {"title": {
            "title": "Field title", "description": "The actual document title.",
            "enum": [literal], "default": literal,
        }},
        "required": ["title"], "$defs": {"Unused": {"type": "string"}},
    }
    original = deepcopy(schema)
    compact = compact_answer_schema(schema)
    assert schema == original
    assert compact["properties"]["title"] == {
        "description": "The actual document title.", "enum": [literal], "default": literal,
    }
    assert compact["description"] == schema["description"]
    assert compact["required"] == ["title"]
    assert compact["additionalProperties"] is False
    assert "$defs" not in compact


def test_compaction_retains_recursive_and_escaped_definition_references():
    schema = {
        "$ref": "#/$defs/a~1b", "$defs": {
            "a/b": {"anyOf": [{"type": "null"}, {"$ref": "#/$defs/Child"}]},
            "Child": {"type": "array", "items": {"$ref": "#/$defs/a~1b"}},
            "Unused": {"$ref": "#/$defs/AlsoUnused"},
            "AlsoUnused": {"type": "string"},
        },
    }
    compact = compact_answer_schema(schema)
    assert set(compact["$defs"]) == {"a/b", "Child"}
    for reference in _refs(compact):
        assert _resolve(compact, reference)
    assert compact_answer_schema(compact) == compact


@pytest.mark.parametrize("kind", ["property", "relationship"])
def test_batch_compaction_keeps_allowed_claim_authority_and_resolves_every_reference(
    batch_members, kind,  # noqa: F811
):
    tasks, members = batch_members
    if kind == "relationship":
        for task, member in zip(tasks, members):
            member.card.predicates = [EdgeSpec(
                iri=task.predicate_iri, label="related", range_class_iris=["urn:Target"],
            )]
    original = compile_batch_stage_schema("discovery", members, reference_resolution=True)
    compact = compact_answer_schema(original)
    forbidden = "properties" if kind == "relationship" else "relations"
    forbidden_definition = "PropertyProposal" if kind == "relationship" else "RelationProposal"
    allowed_definition = "RelationProposal" if kind == "relationship" else "PropertyProposal"
    definitions = compact["$defs"]
    assert forbidden_definition not in definitions
    assert "IdentifierProposal" not in definitions
    assert definitions["BatchMemberResult"]["properties"][forbidden] == {
        "type": "array", "maxItems": 0, "items": {"type": "string"},
    }
    assert definitions[allowed_definition]["properties"]["predicate_iri"]["enum"] == [
        task.predicate_iri for task in tasks
    ]
    assert definitions["Quote"]["properties"]["evidence_id"]["enum"] == (
        original["$defs"]["Quote"]["properties"]["evidence_id"]["enum"]
    )
    for reference in _refs(compact):
        assert _resolve(compact, reference)
    assert compact_answer_schema(compact) == compact


def test_schema_values_remain_equivalent_after_unreachable_items_are_removed():
    jsonschema = pytest.importorskip("jsonschema")
    schema = {
        "type": "object", "additionalProperties": False, "required": ["disabled", "enabled"],
        "properties": {
            "disabled": {"type": "array", "maxItems": 0, "items": {"$ref": "#/$defs/Unused"}},
            "enabled": {"$ref": "#/$defs/Allowed"},
        },
        "$defs": {
            "Unused": {"type": "object", "properties": {"secret": {"type": "string"}}},
            "Allowed": {"type": "string", "enum": ["allowed"]},
        },
    }
    compact = compact_answer_schema(schema)
    jsonschema.Draft202012Validator.check_schema(compact)
    before = jsonschema.Draft202012Validator(schema)
    after = jsonschema.Draft202012Validator(compact)
    values = [None, False, 0, "allowed", "denied", [], [None], ["x"], {}, {"secret": "x"}]
    for disabled, enabled in product(values, repeat=2):
        candidate = {"disabled": disabled, "enabled": enabled}
        assert before.is_valid(candidate) == after.is_valid(candidate)
    assert after.is_valid({"disabled": [], "enabled": "allowed"})
    for candidate in ({}, {"disabled": [], "enabled": "allowed", "extra": True}):
        assert not before.is_valid(candidate) and not after.is_valid(candidate)


def test_empty_arrays_keep_constraints_that_can_make_them_unsatisfiable():
    schema = {
        "type": "array", "maxItems": 0, "minItems": 1,
        "contains": {"type": "integer"}, "items": {"type": "object"},
    }
    compact = compact_answer_schema(schema)
    assert compact["minItems"] == 1
    assert compact["contains"] == {"type": "integer"}
    assert compact["maxItems"] == 0
