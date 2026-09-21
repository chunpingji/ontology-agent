"""Rejected short references remain rejected after the paid raw response is restored."""

import json
from copy import deepcopy

import pytest

from app.services.extraction.ontology_guided.current_work import validate_protocol_result
from app.services.extraction.ontology_guided.model_adapter import RecognitionModelFailure
from app.services.extraction.ontology_guided.tool_model_adapter import (
    ToolModelRecognitionAdapter,
    assemble_stage_input,
)
from app.services.llm.local_client import ResponseTurn, StructuredModelError
from tests.test_extraction.test_batch_model_adapter import batch_setup
from tests.test_extraction.test_tool_engine_adapter import setup_adapter

pytest_plugins = ["tests.test_extraction.test_tool_engine_freeze"]


@pytest.fixture
def source(tool_source):
    return tool_source


def rejected_turn(source, *, kind, members=()):
    if kind == "tool":
        arguments = {"evidence_id": "f" * 64, "quote": "原文", "context_text": None}
        if members:
            arguments["member_task_id"] = members[0].task_id
        output = [{"type": "function_call", "call_id": "paid-call",
                   "name": "resolve_source_anchor", "arguments": json.dumps(arguments)}]
    else:
        answer = deepcopy(source["proposal"])
        previous = answer["entities"][0]["local_id"]
        answer["entities"][0]["local_id"] = "@r:unknown"
        for relation in answer["relations"]:
            relation["object_ids"] = ["@r:unknown" if value == previous else value
                                      for value in relation["object_ids"]]
        if members:
            answer = {"members": [{"task_id": task.task_id, "result": deepcopy(answer)}
                                  for task in members]}
        output = [{"type": "message", "role": "assistant", "content": [
            {"type": "output_text", "text": json.dumps(answer)},
        ]}]
    return ResponseTurn("paid-response", output, "completed", None, None,
                        {"input_tokens": 101, "output_tokens": 23})


def install_transport(monkeypatch, turn):
    requests = []

    def transport(_client, **kwargs):
        requests.append(deepcopy(kwargs))
        return deepcopy(turn)

    def unexpected_tool(*_args, **_kwargs):
        pytest.fail("Rejected raw references must never reach tool dispatch")

    monkeypatch.setattr("app.services.llm.local_client.responses_create", transport)
    monkeypatch.setattr("app.services.extraction.ontology_guided.tool_runtime.dispatch_tool",
                        unexpected_tool)
    monkeypatch.setattr("app.services.extraction.ontology_guided.batch_model_adapter.dispatch_member_tool",
                        unexpected_tool)
    return requests


def assert_paid_rejection(stored, turn):
    protocol = stored["protocol"]
    assert protocol["pending_request"] is None
    assert protocol["request_attempt"] == 1 and protocol["completed_attempts"] == [1]
    assert len(stored["reservations"]) == 1
    assert not protocol["completed_tool_results"]
    records = [wrapper["value"] for wrapper in stored["results"].values()
               if wrapper["field"] == "model_turn"]
    assert len(records) == 1
    record = records[0]
    validate_protocol_result("model_turn", record)
    assert record["reference_error"] == "unknown_model_reference"
    assert record["output_items"] == turn.output_items
    assert record["usage"] == turn.usage and record["error"] is None
    assert record["response_status"] == "completed"
    return record


@pytest.mark.parametrize("kind", ["answer", "tool"])
def test_scalar_rejected_reference_is_saved_once_and_stays_rejected_on_cold_resume(
    source, monkeypatch, kind,
):
    adapter, task, context, predicate, menu, stored, _ = setup_adapter(source, monkeypatch)
    turn = rejected_turn(source, kind=kind)
    requests = install_transport(monkeypatch, turn)
    with pytest.raises(RecognitionModelFailure, match="^unknown_model_reference$") as failure:
        adapter.inspect(task, context, predicate, menu)
    assert failure.value.model_calls == 1
    assert len(requests) == 1
    record = assert_paid_rejection(stored, turn)
    with pytest.raises(StructuredModelError, match="^unknown_model_reference$"):
        ToolModelRecognitionAdapter._turn(record)
    with pytest.raises(StructuredModelError, match="^unknown_model_reference$"):
        assemble_stage_input(stored["protocol"], load_turn=lambda _ref: record,
                             load_tool_result=lambda _ref: pytest.fail("No tools were confirmed"))

    saved = deepcopy(stored)
    resumed, task, context, predicate, menu, storage, _ = setup_adapter(source, monkeypatch)
    storage.update(deepcopy(saved))
    context.protocol_state = deepcopy(saved["protocol"])
    context.protocol_results = deepcopy(saved["results"])
    context.remaining_model_calls = 3
    resumed_requests = install_transport(monkeypatch, turn)
    with pytest.raises(RecognitionModelFailure, match="^unknown_model_reference$") as failure:
        resumed.inspect(task, context, predicate, menu)
    assert failure.value.model_calls == 0
    assert not resumed_requests
    assert storage == saved


@pytest.mark.parametrize("kind", ["answer", "tool"])
def test_batch_rejected_reference_keeps_member_errors_and_paid_turn_on_cold_resume(
    source, monkeypatch, kind,
):
    adapter, unit, context, menu, stored, _ = batch_setup(
        source, monkeypatch, size=2, relation=2,
    )
    turn = rejected_turn(source, kind=kind, members=unit.members)
    requests = install_transport(monkeypatch, turn)
    expected = {task.task_id: "unknown_model_reference" for task in unit.members}
    result = adapter.inspect_work_unit(unit, context, menu)
    assert result.member_errors == expected and not result.member_result_refs
    assert len(requests) == 1
    assert_paid_rejection(stored, turn)

    saved = deepcopy(stored)
    resumed, unit, context, menu, storage, _ = batch_setup(
        source, monkeypatch, size=2, relation=2,
    )
    storage.update(deepcopy(saved))
    context.protocol_state = deepcopy(saved["protocol"])
    context.protocol_results = deepcopy(saved["results"])
    context.remaining_model_calls_by_member = {task.task_id: 3 for task in unit.members}
    resumed_requests = install_transport(monkeypatch, turn)
    result = resumed.inspect_work_unit(unit, context, menu)
    assert result.member_errors == expected and not result.member_result_refs
    assert not resumed_requests
    assert storage == saved


@pytest.mark.parametrize("reason", [None, "", "provider_error", [], 1])
def test_paid_reference_error_field_rejects_unknown_or_nonstring_codes(reason):
    value = {
        "attempt": 1, "stage": "discovery", "input_hash": "a" * 64,
        "allowed_tool_names": [], "response_id": "response", "output_items": [],
        "response_status": "completed", "incomplete_details": None, "error": None,
        "usage": {}, "reference_error": reason,
    }
    with pytest.raises(ValueError, match="model turn result is invalid"):
        validate_protocol_result("model_turn", value)


@pytest.mark.parametrize("reason", ["unknown_model_reference", "model_reference_collision"])
def test_paid_reference_error_codes_are_valid_and_replayed_exactly(reason):
    value = {
        "attempt": 1, "stage": "discovery", "input_hash": "a" * 64,
        "allowed_tool_names": [], "response_id": "response", "output_items": [],
        "response_status": "completed", "incomplete_details": None, "error": None,
        "usage": {}, "reference_error": reason,
    }
    validate_protocol_result("model_turn", value)
    with pytest.raises(StructuredModelError, match=f"^{reason}$"):
        ToolModelRecognitionAdapter._turn(value)
