"""Transport differences cannot hide incomplete responses or invalid original outputs."""

import json
from copy import deepcopy
from types import SimpleNamespace

import httpx
import jsonschema
import pytest

from app.evaluation.harness_model_comparison import gateway_call, score_state, wire_schema


def test_gateway_schema_preserves_original_constraints_without_mutating_input():
    original = {
        "type": "object", "additionalProperties": False,
        "properties": {"quote": {"$ref": "#/$defs/Q", "description": "verbatim source"}},
        "required": ["quote"], "$defs": {"Q": {
            "type": "object", "additionalProperties": False,
            "properties": {"text": {"type": "string", "minLength": 1},
                           "occurrence": {"enum": ["first", None], "default": None}},
            "required": ["text"],
        }},
    }
    before = deepcopy(original)
    schema = wire_schema(original)
    value = {"quote": {"text": "source", "occurrence": None}}
    jsonschema.validate(value, original)
    jsonschema.validate(value, schema)
    assert original == before
    assert "$ref" not in schema["properties"]["quote"]
    assert schema["properties"]["quote"]["description"] == "verbatim source"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"quote": {"text": "", "occurrence": None}}, schema)


def test_nullable_type_choice_keeps_null_while_declaring_gateway_required_type():
    schema = {"anyOf": [{"enum": ["urn:Object", None]}, {"const": None}]}
    adapted = wire_schema(schema)
    assert adapted["anyOf"][1] == {"const": None, "type": "null"}
    for value in (None, "urn:Object"):
        jsonschema.validate(value, schema)
        jsonschema.validate(value, adapted)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate("urn:Wrong", adapted)


@pytest.mark.parametrize("terminal,expected", [("completed", None),
                                               ("incomplete", "gateway_response_not_completed")])
def test_streamed_items_are_used_when_gateway_terminal_output_is_empty(
    monkeypatch, terminal, expected,
):
    from app.evaluation import harness_model_comparison as comparison

    events = [
        {"type": "response.output_item.done", "output_index": 0,
         "item": {"type": "message", "content": [
             {"type": "output_text", "text": '{"answer": 2}'},
         ]}},
        {"type": "response." + terminal,
         "response": {"status": terminal, "output": [], "usage": {"output_tokens": 6}}},
    ]
    original = httpx.Client

    def handler(request):
        assert request.headers["Authorization"] == "Bearer test-credential"
        return httpx.Response(200, text="\n\n".join(
            "data: " + json.dumps(event) for event in events
        ), headers={"content-type": "text/event-stream"})

    monkeypatch.setattr(comparison.httpx, "Client", lambda **kwargs: original(
        **kwargs, transport=httpx.MockTransport(handler),
    ))
    result = gateway_call({}, base_url="http://gateway.test/v1", secret="test-credential")
    assert result["error"] == expected
    assert result["raw_response"]["output"] == []
    assert result["output"] == ({"answer": 2} if terminal == "completed" else None)
    assert "test-credential" not in json.dumps(result)


def test_gateway_error_is_saved_without_credentials(monkeypatch):
    from app.evaluation import harness_model_comparison as comparison

    original = httpx.Client
    monkeypatch.setattr(comparison.httpx, "Client", lambda **kwargs: original(
        **kwargs, transport=httpx.MockTransport(lambda _: httpx.Response(
            400, json={"error": "request failed with test-credential"},
        )),
    ))
    result = gateway_call({}, base_url="http://gateway.test/v1", secret="test-credential")
    assert result["error"] == "http_400"
    assert "test-credential" not in json.dumps(result)


def state_fixture():
    facility = "https://ontology.pharma-gmp.cn/slpra/facility/"
    development = "https://ontology.pharma-gmp.cn/slpra/drug-development/"
    state = {"entities": {}, "properties": {}, "relation_groups": {},
             "cursor": {"main": {"scope_complete": True}}}
    for index, value in enumerate(("644", "642")):
        ref = {"source_id": "s", "start": index * 4, "end": index * 4 + 3, "text": value}
        state["entities"][value] = {
            "id": value, "label": value, "class_iri": facility + "ProductionArea",
            "state": "accepted", "role": "area", "referent": ref, "evidence": [ref],
        }
        state["properties"][value] = {
            "subject_id": value, "predicate_iri": facility + "areaIdentifier",
            "state": "accepted", "value": value, "value_evidence": [ref],
        }
    state["entities"]["root"] = {
        "id": "root", "label": "Plan", "class_iri": development + "ClinicalSampleProductionPlan",
        "state": "accepted", "role": "document_root", "evidence": [],
    }
    state["relation_groups"]["g"] = {
        "subject_id": "root", "object_ids": ["644", "642"],
        "predicate_iri": development + "producedInArea", "participation": "all",
        "selection": "unspecified", "timing": "parallel", "ordered_object_ids": None, "order_evidence": [], "state": "accepted",
        "timing_state": "accepted",
    }
    return state, SimpleNamespace(model_dump=lambda **_: {"classes": {}})


def test_scoring_rejects_swapped_number_ownership_and_incomplete_runs():
    state, catalog = state_fixture()
    before = deepcopy(state)
    assert score_state(state, catalog, "parallel_plan")["full_target_ok"]
    assert state == before
    state["properties"]["644"]["subject_id"] = "642"
    state["properties"]["642"]["subject_id"] = "644"
    assert not score_state(state, catalog, "parallel_plan")["identifiers_ok"]
    state = deepcopy(before)
    state["cursor"]["main"]["scope_complete"] = False
    assert not score_state(state, catalog, "parallel_plan")["full_target_ok"]


def test_no_accepted_identifiers_cannot_pass_ownership_metric():
    state, catalog = state_fixture()
    state["properties"].clear()
    result = score_state(state, catalog, "parallel_plan")
    assert not result["identifier_ownership"]
    assert not result["identifiers_ok"]
    assert not result["full_target_ok"]
