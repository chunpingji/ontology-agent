"""The actual transport, accounting and durable identities use one projection."""

import json
from copy import deepcopy

import pytest

from app.services.extraction.evidence_identity import canonical_json
from app.services.extraction.ontology_guided.current_work import responses_request_hash
from app.services.extraction.ontology_guided.model_reference_projection import (
    project_reference_payload,
)
from app.services.llm.model_runtime import model_scope
from tests.test_extraction.test_batch_model_adapter import batch_setup
from tests.test_extraction.test_tool_engine_adapter import setup_adapter

pytest_plugins = ["tests.test_extraction.test_tool_engine_freeze"]


@pytest.fixture
def source(tool_source):
    return tool_source


@pytest.mark.parametrize("batch", [False, True])
def test_wire_request_matches_count_hash_and_canonical_paid_results(source, monkeypatch, batch):
    if batch:
        adapter, unit, context, menu, stored, requests = batch_setup(source, monkeypatch)
        def execute():
            return adapter.inspect_work_unit(unit, context, menu)
    else:
        adapter, task, context, predicate, menu, stored, requests = setup_adapter(
            source, monkeypatch,
        )

        def execute():
            return adapter.inspect(task, context, predicate, menu)
    adapter.chat_template_kwargs = {"enable_thinking": False}
    events = []
    with model_scope(on_harness_event=lambda kind, data: events.append((kind, data))):
        execute()
    starts = [data for kind, data in events if kind == "model_start"]
    paid = sorted((row["value"] for row in stored["results"].values()
                   if row["field"] == "model_turn"), key=lambda row: row["attempt"])
    assert len(starts) == len(paid) == len(requests)
    for event, request, result in zip(starts, requests, paid, strict=True):
        wire = deepcopy(event["request"])
        wire.pop("stream")
        assert wire["input"] == request["input_items"]
        assert wire["instructions"] == request["instructions"]
        assert wire["extra_body"] == request["extra_body"] == {
            "chat_template_kwargs": {"enable_thinking": False},
        }
        assert event["input_tokens"] == adapter.token_counter(canonical_json(wire))
        assert result["input_hash"] == responses_request_hash(wire)
        assert "@r:" in canonical_json(wire["input"])
        assert "@r:" not in canonical_json(result["output_items"])
        if result["stage"] == "verification" and request["tool_choice"] != "required":
            message = next(item for item in result["output_items"] if item["type"] == "message")
            answer = json.loads(message["content"][0]["text"])
            answers = [member["result"] for member in answer["members"]] if batch else [answer]
            assert all(len(item["target_id"]) == len(item["content_hash"]) == 64
                       for answer in answers for item in answer["verifications"])


def test_tool_arguments_restore_and_new_result_reference_survives_cold_resume(source, monkeypatch):
    from app.services.llm import local_client

    adapter, task, context, predicate, menu, stored, requests = setup_adapter(
        source, monkeypatch, stop_at="tool_result",
    )
    transport = local_client.responses_create

    def anchor_call(client, **kwargs):
        turn = transport(client, **kwargs)
        assert len(requests) == 1
        evidence_id = project_reference_payload({"evidence_id": source["source_unit"].evidence_id})[
            "evidence_id"
        ]
        turn.output_items[:] = [{
            "type": "function_call", "id": "provider-item", "call_id": "provider-call",
            "name": "resolve_source_anchor", "arguments": json.dumps({
                "evidence_id": evidence_id, "quote": "对象乙", "context_text": None,
            }),
        }]
        return turn

    monkeypatch.setattr(local_client, "responses_create", anchor_call)
    with pytest.raises(RuntimeError, match="pause after durable commit"):
        adapter.inspect(task, context, predicate, menu)
    saved = deepcopy(stored)
    paid_turn = next(row["value"] for row in saved["results"].values()
                     if row["field"] == "model_turn")
    assert json.loads(paid_turn["output_items"][0]["arguments"])["evidence_id"] == (
        source["source_unit"].evidence_id
    )
    tool_result = next(row["value"]["result"] for row in saved["results"].values()
                       if row["field"] == "tool_result")
    assert tool_result["status"] == "ok"
    mention_ref = tool_result["data"]["mention_ref"]
    assert len(mention_ref) == 64
    adapter, task, context, predicate, menu, stored, requests = setup_adapter(source, monkeypatch)
    stored.update(deepcopy(saved))
    context.protocol_state, context.protocol_results = saved["protocol"], saved["results"]
    context.remaining_model_calls = 3
    outcome = adapter.inspect(task, context, predicate, menu)
    assert outcome.complete and len(requests) == 2
    assert len(stored["reservations"]) == 3
    continued = next(item for item in requests[0]["input_items"]
                     if item.get("type") == "function_call_output")
    assert continued["call_id"] == "provider-call"
    assert json.loads(continued["output"])["data"]["mention_ref"] == project_reference_payload(
        {"mention_ref": mention_ref},
    )["mention_ref"]
    assert all(stored["results"][key] == value for key, value in saved["results"].items())
