"""Pure budget and Responses consumption gates; no model calls or persisted state."""

from copy import deepcopy
from dataclasses import asdict, replace

import pytest
from pydantic import ConfigDict

from app.schemas.evidence import EvidenceModel
from app.services.extraction.evidence_identity import canonical_json
from app.services.extraction.ontology_guided import tool_model_adapter as adapter
from app.services.llm.local_client import ResponseTurn, StructuredModelError


def plan(stage, remaining, *, tools=None, progress=None, **protocol):
    return adapter.plan_model_turn(
        {"stage": stage, **protocol}, remaining_model_calls=remaining,
        available_tools=["inspect_evidence"] if tools is None else tools,
        batch_progress=progress,
    )


@pytest.mark.parametrize(("stage", "remaining", "mode", "reserved"), [
    ("discovery", 4, "tools", 2), ("discovery", 3, "tools", 2),
    ("discovery", 2, "answer", 1), ("discovery", 1, "stop", 0),
    ("discovery", 0, "stop", 0), ("verification", 3, "tools", 1),
    ("verification", 2, "tools", 1), ("verification", 1, "answer", 0),
    ("verification", 0, "stop", 0),
])
def test_budget_reserves_candidate_and_independent_verification(stage, remaining, mode, reserved):
    turn = plan(stage, remaining)
    assert turn.stage == stage and turn.mode == mode
    assert turn.reserved_model_calls == reserved
    assert turn.model_calls_remaining == remaining
    assert turn.allowed_tool_names == (["inspect_evidence"] if mode == "tools" else [])
    if mode != "stop":
        assert remaining >= 1 + reserved


@pytest.mark.parametrize("stage,reserved", [("discovery", 1), ("verification", 0)])
def test_no_tool_and_repeated_no_progress_enter_answer_without_extra_budget(stage, reserved):
    no_tools = plan(stage, 4, tools=[])
    no_progress = plan(stage, 4, progress=False)
    assert no_tools.mode == no_progress.mode == "answer"
    assert no_tools.reserved_model_calls == no_progress.reserved_model_calls == reserved
    assert no_tools.reason_code == "no_tools_available"
    assert no_progress.reason_code == "tool_batch_no_progress"


@pytest.mark.parametrize("progress", [None, True])
def test_first_error_or_first_no_match_does_not_close_tools(progress):
    assert plan("discovery", 3, progress=progress).mode == "tools"
    assert plan("verification", 2, progress=progress).mode == "tools"


def test_visibility_is_stage_and_recovery_scoped_and_never_includes_controller_tools():
    tools = ["inspect_evidence", "propose_mentions", "check_claim_binding", "propose_repair",
             "validate_metric", "validate_graph", "inspect_evidence"]
    assert plan("discovery", 4, tools=tools).allowed_tool_names == [
        "inspect_evidence", "propose_mentions",
    ]
    assert plan("verification", 4, tools=tools).allowed_tool_names == [
        "inspect_evidence", "check_claim_binding", "validate_graph",
    ]
    assert plan("verification", 4, tools=tools, recovery_kind="evidence").allowed_tool_names == [
        "inspect_evidence", "check_claim_binding", "propose_repair", "validate_graph",
    ]


def test_confirmed_stage_answer_cannot_start_another_model_turn():
    assert plan("discovery", 4, discovery_ref="confirmed-result").mode == "stop"
    assert plan("verification", 4, verification_ref="confirmed-result").mode == "stop"
    assert plan("verification", 1, discovery_ref="confirmed-result").mode == "answer"


def test_finalize_is_not_a_model_stage():
    with pytest.raises(ValueError, match="model_turn_stage_invalid"):
        plan("finalize", 4)


@pytest.mark.parametrize("remaining", [-1, True, 1.5])
def test_invalid_remaining_budget_is_not_coerced(remaining):
    with pytest.raises(ValueError, match="model_budget_invalid"):
        plan("discovery", remaining)


def test_planning_neither_mutates_protocol_nor_spends_budget():
    protocol = {"stage": "verification", "request_attempt": 3}
    tools = ["inspect_evidence"]
    before = dict(protocol)
    first = adapter.plan_model_turn(protocol, remaining_model_calls=1,
                                    available_tools=tools, batch_progress=None)
    second = adapter.plan_model_turn(protocol, remaining_model_calls=1,
                                     available_tools=tools, batch_progress=None)
    assert first == second
    assert protocol == before and tools == ["inspect_evidence"]


def response(items):
    return ResponseTurn("response-1", items, "completed", None, None, None)


def function_call(call_id="call-2", **changes):
    return {"type": "function_call", "id": "item-1", "call_id": call_id,
            "name": "unknown_tool", "arguments": "not valid JSON", **changes}


def message(text):
    return {"type": "message", "role": "assistant", "content": [
        {"type": "output_text", "text": text, "annotations": []},
    ]}


def test_call_extraction_preserves_unknown_name_and_bad_arguments_for_error_feedback():
    turn = response([{"type": "reasoning", "encrypted_content": "opaque"}, function_call()])
    calls = adapter.extract_tool_calls(turn)
    assert [(c.call_id, c.name, c.arguments_json) for c in calls] == [
        ("call-2", "unknown_tool", "not valid JSON"),
    ]
    assert turn.output_items[1]["id"] == "item-1"


@pytest.mark.parametrize("mutation", ["duplicate", "missing_id", "missing_arguments", "wrong_type"])
def test_invalid_batch_identity_rejects_the_whole_batch(mutation):
    calls = [function_call("call-first"), function_call("call-second")]
    if mutation == "duplicate":
        calls[1]["call_id"] = "call-first"
    elif mutation == "missing_id":
        calls[1].pop("call_id")
    elif mutation == "missing_arguments":
        calls[1].pop("arguments")
    else:
        calls[1]["arguments"] = {"already_parsed": True}
    with pytest.raises(StructuredModelError, match="model_tool_protocol_invalid"):
        adapter.extract_tool_calls(response(calls))


@pytest.mark.parametrize("status,code", [
    ("incomplete", "model_response_incomplete"), ("failed", "model_response_failed"),
    ("queued", "model_tool_protocol_invalid"),
])
def test_unconsumable_response_cannot_produce_executable_calls(status, code):
    with pytest.raises(StructuredModelError, match=code):
        adapter.extract_tool_calls(replace(response([function_call()]), response_status=status))


def test_refusal_after_valid_call_still_rejects_entire_batch():
    turn = response([function_call(), {"type": "message", "content": [
        {"type": "refusal", "refusal": "synthetic"},
    ]}])
    with pytest.raises(StructuredModelError, match="model_refusal"):
        adapter.extract_tool_calls(turn)


def test_batch_limits_and_answer_only_round_do_not_allow_partial_execution():
    with pytest.raises(StructuredModelError, match="tool_budget_exhausted"):
        adapter.extract_tool_calls(response([function_call(str(i)) for i in range(9)]))
    with pytest.raises(StructuredModelError, match="model_tool_protocol_invalid"):
        adapter.extract_tool_calls(response([function_call()]), allow_tools=False)


class StageAnswer(EvidenceModel):
    model_config = ConfigDict(strict=True)
    count: int


def test_answer_is_validated_without_reading_reasoning_or_tool_arguments():
    turn = response([{"type": "reasoning", "summary": [{"text": '{"count":999}'}]},
                     message('{"count":2}')])
    assert adapter.parse_stage_answer(turn, StageAnswer).count == 2


@pytest.mark.parametrize("text", [
    '{"count":"2"}', '{"count":2,"accepted":true}', '```json\n{"count":2}\n```',
    '{"count":NaN}', '{"count":Infinity}', '{"count":1,"count":2}', '[]', '',
])
def test_invalid_stage_json_is_a_protocol_error_not_a_semantic_verdict(text):
    with pytest.raises(StructuredModelError, match="model_parse_error"):
        adapter.parse_stage_answer(response([message(text)]), StageAnswer)


def stage_records():
    turn = asdict(response([{"type": "reasoning", "encrypted_content": "opaque"},
                            function_call("call-a"), function_call("call-b")]))
    turn.update(attempt=2, stage="discovery", input_hash="request-hash",
                allowed_tool_names=["inspect_evidence"])
    result = {"status": "error", "data": None, "evidence_refs": [], "issues": [
        {"code": "unknown_tool", "field_path": None, "message": "Unknown tool",
         "evidence_ids": []},
    ]}
    records = {"turn-2": turn, "result-a": {"attempt": 2, "call_id": "call-a", "result": result},
               "result-b": {"attempt": 2, "call_id": "call-b", "result": deepcopy(result)}}
    protocol = {"stage": "discovery", "stage_input_items": [
        {"role": "user", "content": "authorized initial evidence"},
    ], "turn_refs": ["turn-2"], "completed_tool_results": ["result-b", "result-a"]}
    return protocol, records


def assemble(protocol, records):
    return adapter.assemble_stage_input(protocol, load_turn=records.__getitem__,
                                        load_tool_result=records.__getitem__)


def test_input_assembly_uses_response_call_order_and_canonical_results_only():
    protocol, records = stage_records()
    before = deepcopy((protocol, records))
    items, pending = assemble(protocol, records)
    assert not pending
    assert items == [*protocol["stage_input_items"], *records["turn-2"]["output_items"],
                     {"type": "function_call_output", "call_id": "call-a",
                      "output": canonical_json(records["result-a"]["result"])},
                     {"type": "function_call_output", "call_id": "call-b",
                      "output": canonical_json(records["result-b"]["result"])}]
    records["result-a"]["result"] = dict(reversed(list(records["result-a"]["result"].items())))
    assert assemble(protocol, records) == (items, pending)
    items[0]["content"] = "caller mutation"
    items[1]["encrypted_content"] = "caller mutation"
    assert (protocol, records) == before


def test_input_assembly_derives_pending_calls_without_reexecuting_confirmed_results():
    protocol, records = stage_records()
    protocol["completed_tool_results"] = ["result-b"]
    loaded = []

    def load_result(ref):
        loaded.append(ref)
        return records[ref]

    items, pending = adapter.assemble_stage_input(
        protocol, load_turn=records.__getitem__, load_tool_result=load_result,
    )
    assert pending == [(2, "call-a")]
    assert loaded == ["result-b"]
    assert items[-1]["call_id"] == "call-b"
    assert "pending_call_ids" not in protocol and "active_input_items" not in protocol


@pytest.mark.parametrize("mutation", [
    "duplicate_result", "foreign_result", "foreign_stage", "unordered_attempts", "early_pending",
])
def test_input_assembly_rejects_mismatched_or_out_of_order_references(mutation):
    protocol, records = stage_records()
    if mutation == "duplicate_result":
        records["result-duplicate"] = deepcopy(records["result-a"])
        protocol["completed_tool_results"].append("result-duplicate")
    elif mutation == "foreign_result":
        records["result-a"]["call_id"] = "call-unrelated"
    elif mutation == "foreign_stage":
        records["turn-2"]["stage"] = "verification"
    else:
        records["turn-3"] = {**records["turn-2"], "attempt": 3, "output_items": [message('{}')]}
        if mutation == "unordered_attempts":
            protocol["turn_refs"] = ["turn-3", "turn-2"]
        else:
            protocol["turn_refs"].append("turn-3")
            protocol["completed_tool_results"].remove("result-a")
    with pytest.raises(StructuredModelError, match="model_tool_protocol_invalid"):
        assemble(protocol, records)


def test_same_call_id_in_different_attempts_keeps_independent_results():
    protocol, records = stage_records()
    records["turn-3"] = {
        **records["turn-2"], "attempt": 3, "output_items": [function_call("call-a")],
    }
    records["result-c"] = {**records["result-a"], "attempt": 3}
    protocol["turn_refs"].append("turn-3")
    protocol["completed_tool_results"].append("result-c")
    items, pending = assemble(protocol, records)
    assert not pending
    assert [item["call_id"] for item in items if item.get("type") == "function_call_output"] == [
        "call-a", "call-b", "call-a",
    ]
