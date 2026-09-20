"""Short model references preserve literal evidence and current-request boundaries."""

import json
import re
from copy import deepcopy

import pytest

from app.services.extraction.evidence_identity import canonical_json
from app.services.extraction.ontology_guided.model_reference_projection import (
    MODEL_REFERENCE_INSTRUCTIONS,
    ModelReferenceProjection,
    project_reference_payload,
)
from app.services.llm.local_client import ResponseTurn, StructuredModelError

SUBJECT = "1" * 64
EVIDENCE = "2" * 64
TASK = "3" * 64
CLAIM = "4" * 64
SCOPE = "5" * 64
OTHER = "6" * 64


def alias(identity):
    return "@r:" + identity[:12]


def request_for(payload, **kwargs):
    return {
        "model": "local-model",
        "instructions": "只依据原文和授权引用回答。",
        "input": [{
            "role": "user",
            "content": [{"type": "input_text", "text": canonical_json(payload)}],
        }],
        **kwargs,
    }


def input_payload(request):
    return json.loads(request["input"][0]["content"][0]["text"])


def response(*items):
    return ResponseTurn(
        response_id=SUBJECT, output_items=list(items), response_status="completed",
        incomplete_details=None, error=None, usage={"input_tokens": 123, "output_tokens": 45},
    )


def message(payload):
    return {
        "type": "message", "id": TASK, "role": "assistant",
        "content": [{"type": "output_text", "text": canonical_json(payload)}],
    }


def test_request_does_not_change_canonical_state_or_literal_hash_evidence():
    payload = {
        "subject_ref": {"id": SUBJECT, "revision": 2}, "scope_id": SCOPE,
        "evidence_units": [{"evidence_id": EVIDENCE, "text": EVIDENCE,
                            "context_text": SUBJECT, "description": TASK,
                            "label": SUBJECT, "labels": [EVIDENCE]}],
        "reason": {"id": TASK}, "unknown_value": OTHER,
        "local_ref_map": {SUBJECT: {"id": SUBJECT, "revision": 2}},
    }
    request = request_for(payload)
    original = deepcopy(request)
    projected = ModelReferenceProjection(request)
    result = input_payload(projected.request)
    assert request == original
    assert result["subject_ref"]["id"] == alias(SUBJECT)
    assert result["scope_id"] == alias(SCOPE)
    assert result["evidence_units"][0] == {
        **payload["evidence_units"][0], "evidence_id": alias(EVIDENCE),
    }
    assert result["reason"] == payload["reason"]
    assert result["unknown_value"] == OTHER
    assert result["local_ref_map"] == {alias(SUBJECT): {"id": alias(SUBJECT), "revision": 2}}
    assert projected.decode_payload(result) == payload


def test_tool_call_and_grouped_output_encode_without_touching_transport_or_reasoning():
    arguments = {"member_task_id": TASK, "evidence_ids": [EVIDENCE], "quote": SUBJECT}
    result = {"status": "ok", "evidence_refs": [EVIDENCE], "data": {
        "units": [{"evidence_id": EVIDENCE, "mentions": [{
            "mention_ref": SUBJECT, "text": SUBJECT,
            "start": 0, "end": 64, "roles": [{"role": "entity", "score": 0.9}],
        }]}],
        "coverage": {"requested_units": [EVIDENCE], "processed_units": [EVIDENCE],
                     "unprocessed_units": []},
    }}
    request = request_for({"task_id": TASK})
    reasoning = {"type": "reasoning", "id": EVIDENCE, "encrypted_content": SUBJECT,
                 "summary": [{"type": "summary_text", "text": canonical_json(arguments)}]}
    request["input"].extend([
        reasoning,
        {"type": "function_call", "id": EVIDENCE, "call_id": SUBJECT,
         "name": "propose_mentions", "arguments": canonical_json(arguments)},
        {"type": "function_call_output", "call_id": SUBJECT, "output": canonical_json(result)},
    ])
    projected = ModelReferenceProjection(request)
    encoded_call = projected.request["input"][2]
    assert encoded_call["call_id"] == SUBJECT and encoded_call["id"] == EVIDENCE
    assert json.loads(encoded_call["arguments"]) == {
        "member_task_id": alias(TASK), "evidence_ids": [alias(EVIDENCE)], "quote": SUBJECT,
    }
    assert projected.request["input"][1] == reasoning
    encoded_output = json.loads(projected.request["input"][3]["output"])
    assert encoded_output == project_reference_payload(result)
    assert encoded_output["data"]["units"][0]["mentions"][0]["text"] == SUBJECT
    decoded = projected.decode_response(response(encoded_call, reasoning))
    assert json.loads(decoded.output_items[0]["arguments"]) == arguments
    assert decoded.output_items[0]["call_id"] == SUBJECT
    assert decoded.output_items[1] == reasoning
    assert decoded.response_id == SUBJECT and decoded.usage["input_tokens"] == 123


@pytest.mark.parametrize("batch", [False, True])
def test_single_and_batch_answers_roundtrip_with_new_local_candidates(batch):
    payload = {"task_id": TASK, "subject_ref": {"id": SUBJECT, "revision": 1},
               "evidence_ids": [EVIDENCE], "target_id": CLAIM, "content_hash": SCOPE}
    projected = ModelReferenceProjection(request_for(payload))
    result = {
        "entities": [{"local_id": "candidate-1", "mentions": [{
            "evidence_id": alias(EVIDENCE), "text": EVIDENCE, "context_text": None,
        }]}],
        "relations": [{"local_id": "relation-1", "subject_id": alias(SUBJECT),
                       "object_ids": ["candidate-1"]}],
        "targets": [{"target_id": alias(CLAIM), "content_hash": alias(SCOPE),
                     "reason": "保留文字引用 " + alias(EVIDENCE)}],
    }
    answer = {"members": [{"task_id": alias(TASK), "result": result}]} if batch else result
    turn = response(message(answer))
    original = deepcopy(turn)
    decoded = projected.decode_response(turn)
    assert turn == original
    body = json.loads(decoded.output_items[0]["content"][0]["text"])
    if batch:
        assert body["members"][0]["task_id"] == TASK
        body = body["members"][0]["result"]
    assert body["relations"][0]["subject_id"] == SUBJECT
    assert body["relations"][0]["object_ids"] == ["candidate-1"]
    assert body["entities"][0]["local_id"] == "candidate-1"
    assert body["entities"][0]["mentions"][0]["text"] == EVIDENCE
    assert body["targets"][0] == {
        "target_id": CLAIM, "content_hash": SCOPE, "reason": "保留文字引用 " + alias(EVIDENCE),
    }
    assert decoded.output_items[0]["id"] == TASK


def test_schema_enums_consts_and_instruction_json_share_the_payload_mapping():
    schema = {"type": "object", "properties": {
        "subject_id": {"type": "string", "enum": [SUBJECT]},
        "claim_ref": {"type": "object", "properties": {"id": {"const": CLAIM}}},
        "evidence_ids": {"type": "array", "items": {"enum": [EVIDENCE]}},
        "text": {"type": "string", "enum": [EVIDENCE]},
    }}
    instructions = "原文中的哈希 " + CLAIM + " 不改。\n" + canonical_json({
        "required_relation_checks": [CLAIM], "shape_profile_id": "ontology-relation-v1",
    })
    request = request_for({"subject_ref": {"id": SUBJECT, "revision": 1}},
                          instructions=instructions,
                          tools=[{"type": "function", "name": "validate_graph",
                                  "parameters": schema}],
                          text={"format": {"type": "json_schema", "schema": schema}})
    projected = ModelReferenceProjection(request)
    for actual in (projected.request["tools"][0]["parameters"],
                   projected.request["text"]["format"]["schema"]):
        properties = actual["properties"]
        assert properties["subject_id"]["enum"] == [alias(SUBJECT)]
        assert properties["evidence_ids"]["items"]["enum"] == [alias(EVIDENCE)]
        assert properties["claim_ref"]["properties"]["id"]["const"] == alias(CLAIM)
        assert properties["text"]["enum"] == [EVIDENCE]
    head, tail = projected.request["instructions"].removeprefix(
        MODEL_REFERENCE_INSTRUCTIONS,
    ).split("\n")
    assert head == instructions.split("\n")[0]
    assert json.loads(tail)["required_relation_checks"] == [alias(CLAIM)]
    assert json.loads(projected.request["instructions"].splitlines()[-1]) == json.loads(tail)


@pytest.mark.parametrize("value", ["@r:unknown", alias(OTHER), OTHER])
def test_unknown_references_cannot_cross_request_scope(value):
    first = ModelReferenceProjection(request_for({"evidence_ids": [EVIDENCE]}))
    second = ModelReferenceProjection(request_for({"evidence_ids": [OTHER]}))
    assert second.decode_payload({"evidence_id": alias(OTHER)}) == {"evidence_id": OTHER}
    with pytest.raises(StructuredModelError, match="^unknown_model_reference$"):
        first.decode_response(response(message({"evidence_id": value})))
    assert first.decode_payload({"evidence_id": EVIDENCE}) == {"evidence_id": EVIDENCE}
    assert first.decode_payload({"text": value}) == {"text": value}


@pytest.mark.parametrize("local_id", ["@r:unknown", alias(SUBJECT)])
def test_new_local_ids_cannot_claim_reserved_aliases(local_id):
    projection = ModelReferenceProjection(request_for({"subject_id": SUBJECT}))
    with pytest.raises(StructuredModelError, match="^unknown_model_reference$"):
        projection.decode_payload({"entities": [{"local_id": local_id}]})


def test_aliases_stay_stable_across_reordering_and_added_context():
    before = ModelReferenceProjection(request_for({"evidence_ids": [EVIDENCE, SUBJECT]}))
    after = ModelReferenceProjection(request_for({"evidence_ids": [OTHER, SUBJECT, EVIDENCE]}))
    assert before.encode_payload({"evidence_id": EVIDENCE}) == after.encode_payload({
        "evidence_id": EVIDENCE,
    }) == {"evidence_id": alias(EVIDENCE)}


def test_prefix_collision_fails_before_request_can_be_sent():
    first, second = "a" * 12 + "1" * 52, "a" * 12 + "2" * 52
    with pytest.raises(StructuredModelError, match="^model_reference_collision$"):
        ModelReferenceProjection(request_for({"evidence_ids": [first, second]}))


@pytest.mark.parametrize("invalid", [
    '{"evidence_id":', '{"evidence_id":"@r:unknown","evidence_id":"again"}',
    '{"score": NaN, "evidence_id":"@r:unknown"}',
])
def test_malformed_json_is_left_for_existing_stage_parser(invalid):
    projection = ModelReferenceProjection(request_for({"evidence_ids": [EVIDENCE]}))
    turn = response({"type": "message", "role": "assistant", "content": [
        {"type": "output_text", "text": invalid},
    ]})
    assert projection.decode_response(turn) == turn


def test_literal_hash_is_never_admitted_as_a_reference():
    projection = ModelReferenceProjection(request_for({"text": EVIDENCE}))
    with pytest.raises(StructuredModelError, match="^unknown_model_reference$"):
        projection.decode_payload({"evidence_id": EVIDENCE})


def test_hash_local_candidates_keep_identity_without_granting_evidence_permissions():
    projection = ModelReferenceProjection(request_for({"subject_id": SUBJECT}))
    answer = {
        "entities": [{"local_id": OTHER}],
        "relations": [{"local_id": "r", "subject_id": alias(SUBJECT), "object_ids": [OTHER]}],
        "reference_bindings": [{"local_id": "b", "source_id": OTHER,
                                "target_id": alias(SUBJECT), "binding_kind": "coreference"}],
    }
    result = projection.decode_payload(answer)
    assert result["entities"][0]["local_id"] == OTHER
    assert result["relations"][0]["object_ids"] == [OTHER]
    assert result["reference_bindings"][0]["source_id"] == OTHER
    assert result["reference_bindings"][0]["target_id"] == SUBJECT
    for field in ("evidence_id", "target_id", "schema_card_id"):
        with pytest.raises(StructuredModelError, match="^unknown_model_reference$"):
            projection.decode_payload({**answer, field: OTHER})
    request = ModelReferenceProjection(request_for(result))
    assert input_payload(request.request)["relations"][0]["object_ids"] == [OTHER]


def test_hash_local_candidates_do_not_cross_batch_member_boundaries():
    projection = ModelReferenceProjection(request_for({"subject_id": SUBJECT}))
    with pytest.raises(StructuredModelError, match="^unknown_model_reference$"):
        projection.decode_payload({"members": [
            {"task_id": "a", "result": {"entities": [{"local_id": OTHER}]}},
            {"task_id": "b", "result": {"relations": [{"object_ids": [OTHER]}]}},
        ]})


def test_reference_instruction_is_added_only_when_used_and_only_once():
    plain = ModelReferenceProjection(request_for({"text": EVIDENCE})).request
    assert MODEL_REFERENCE_INSTRUCTIONS not in plain["instructions"]
    request = request_for({"evidence_id": EVIDENCE})
    encoded = ModelReferenceProjection(request).request
    assert encoded["instructions"].count(MODEL_REFERENCE_INSTRUCTIONS) == 1
    request["instructions"] = encoded["instructions"]
    assert ModelReferenceProjection(request).request["instructions"] == encoded["instructions"]


def test_strict_hash_length_schema_accepts_projected_reference_length():
    schema = {"type": "object", "properties": {
        "evidence_id": {"type": "string", "minLength": 64, "maxLength": 64},
        "text": {"type": "string", "minLength": 64, "maxLength": 64},
    }}
    projected = ModelReferenceProjection(request_for(
        {"evidence_id": EVIDENCE}, text={"format": {"schema": schema}},
    ))
    properties = projected.request["text"]["format"]["schema"]["properties"]
    assert properties["evidence_id"]["minLength"] == len(alias(EVIDENCE))
    assert properties["evidence_id"]["maxLength"] == len(alias(EVIDENCE))
    assert properties["text"]["minLength"] == properties["text"]["maxLength"] == 64


def test_fragmented_answer_decodes_and_reencodes_with_existing_stage_parse_contract():
    from app.services.extraction.ontology_guided.claim_protocol import DiscoveryEnvelope
    from app.services.extraction.ontology_guided.tool_model_adapter import parse_stage_answer

    request = request_for({"evidence_id": EVIDENCE})
    projection = ModelReferenceProjection(request)
    answer = {"entities": [{
        "local_id": "e1", "class_iri": "urn:Entity", "representation": "mention",
        "mentions": [{"evidence_id": alias(EVIDENCE), "text": EVIDENCE,
                      "context_text": None}],
        "record_components": [], "identifier_claims": [],
    }], "relations": [], "properties": [], "external_links": [], "observations": []}
    text = canonical_json(answer)
    split = text.index(alias(EVIDENCE)) + 6
    pieces = [text[:split], text[split:split + 3], text[split + 3:]]
    reasoning = {"type": "reasoning", "id": CLAIM, "encrypted_content": EVIDENCE}
    turn = response(
        {"type": "message", "role": "assistant", "id": SUBJECT, "content": [
            {"type": "output_text", "text": piece} for piece in pieces[:2]
        ]}, reasoning,
        {"type": "message", "role": "assistant", "id": TASK, "content": [
            {"type": "output_text", "text": pieces[2]},
        ]},
    )
    decoded = projection.decode_response(turn)
    parsed = parse_stage_answer(decoded, DiscoveryEnvelope)
    assert parsed.entities[0].mentions[0].evidence_id == EVIDENCE
    assert parsed.entities[0].mentions[0].text == EVIDENCE
    assert decoded.response_id == turn.response_id
    assert [item["id"] for item in decoded.output_items] == [SUBJECT, CLAIM, TASK]
    assert decoded.output_items[1] == reasoning
    assert len(decoded.output_items[0]["content"]) == 2
    request["input"].extend(decoded.output_items)
    reencoded = ModelReferenceProjection(request).request["input"][1:]
    assert reencoded[1] == reasoning
    joined = "".join(part["text"] for item in reencoded if item["type"] == "message"
                     for part in item["content"])
    assert json.loads(joined) == answer


def test_new_candidate_output_schema_prevents_reserved_aliases_without_changing_evidence():
    schema = {"type": "object", "properties": {
        "local_id": {"type": "string"}, "text": {"type": "string"},
        "context_text": {"type": "string"}, "description": {"type": "string"},
    }}
    original = deepcopy(schema)
    projected = ModelReferenceProjection(request_for(
        {"text": "@r:p_mass_1", "evidence_id": EVIDENCE},
        text={"format": {"schema": schema}},
        tools=[{"name": "unrelated", "parameters": schema}],
    ))
    output_schema = projected.request["text"]["format"]["schema"]
    pattern = re.compile(output_schema["properties"]["local_id"]["pattern"])
    assert not pattern.fullmatch("@r:p_mass_1")
    assert not pattern.fullmatch("@r:mass_5mg")
    for local_id in ("e1", "p1", "123", OTHER):
        assert pattern.fullmatch(local_id)
        assert projected.decode_payload({"local_id": local_id}) == {"local_id": local_id}
    assert input_payload(projected.request)["text"] == "@r:p_mass_1"
    for name in ("text", "context_text", "description"):
        assert output_schema["properties"][name] == schema["properties"][name]
    assert projected.request["tools"][0]["parameters"] == original
    assert schema == original


def test_candidate_output_pattern_preserves_existing_enum_const_and_length_constraints():
    local_schema = {"type": "string", "enum": [OTHER], "const": OTHER,
                    "minLength": 64, "maxLength": 64, "description": "原有说明"}
    projection = ModelReferenceProjection(request_for(
        {"subject_id": SUBJECT}, text={"format": {"schema": {
            "type": "object", "properties": {"local_id": local_schema},
        }}},
    ))
    actual = projection.request["text"]["format"]["schema"]["properties"]["local_id"]
    for name in ("enum", "const", "minLength", "maxLength"):
        assert actual[name] == local_schema[name]
    assert actual["description"].startswith("原有说明\n")
