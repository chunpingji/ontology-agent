"""Responses checkpoints use current references and atomically confirm exact results."""

from copy import deepcopy

import pytest

from app.models.document_analysis import DocumentRunRequest, DocumentRunResult
from app.services.document_analysis import current_state
from app.services.document_analysis.execution import CheckpointMismatch, _validate_model_call_state
from app.services.document_analysis.run_store import DocumentAnalysisRunStore, HeadConflict
from app.services.extraction.evidence_identity import evidence_hash
from app.services.extraction.ontology_guided.contracts import VerificationTarget
from app.services.extraction.ontology_guided.current_work import (
    TOOL_PROTOCOL_VERSION,
    call_request_key,
    protocol_result_ref,
    responses_request_hash,
    validate_protocol_result,
    validate_tool_protocol,
)
from app.services.extraction.ontology_guided.tool_model_adapter import assemble_stage_input
from tests.test_extraction.test_document_state_artifacts import seed
from tests.test_extraction.test_tool_engine_graph_contracts import target_values


def protocol_state():
    values = target_values()
    values["document_context"].document_hash = "d" * 64
    target = VerificationTarget.create(**values).model_dump(mode="json")
    return {
        "version": TOOL_PROTOCOL_VERSION, "api_protocol": "responses", "lineage_id": "e",
        "base_target": target, "scope_id": evidence_hash([]), "stage": "discovery",
        "assertion_generation": 0, "evidence_revision": 0, "evidence_hash": "a" * 64,
        "context_hash": "b" * 64, "request_attempt": 0, "completed_attempts": [],
        "active_instructions": "Read authorized evidence; preserve qualifiers.",
        "stage_input_items": [{"role": "user", "content": "Synthetic task"}],
        "pending_request": None, "turn_refs": [], "completed_tool_results": [],
        "tool_calls_used": 0, "materialized_refs": {}, "context_authorization": None,
        "discovery_ref": None, "verification_ref": None, "outcome_ref": None,
        "recovery_kind": "none", "recovery_used": False,
    }


@pytest.fixture()
def current_run(db):
    store = DocumentAnalysisRunStore(db)
    run, token = seed(store, "responses-current-results")
    run.run_fingerprint = "responses-current-fingerprint"
    db.commit()
    return store, run, token


def persist(current_run, protocol, *, receipts=(), results=None):
    store, run, token = current_run
    state = {
        "current_calls": 1, "version": 2,
        "recognition_run_id": str(run.recognition_run_id),
        "run_fingerprint": run.run_fingerprint,
        "lineage_calls": {"e": {"key": "e", "value": protocol["request_attempt"], "position": 0}},
        "protocols": {"e": {"key": "e", "value": deepcopy(protocol), "position": 0}},
        "reservations": list(receipts), "reservation_sequence": protocol["request_attempt"],
        "result_changes": results or {},
    }
    current_state.persist_calls(store, run, token, run.run_fingerprint, state)


def reserve(current_run, protocol, *, allowed_tool_names=None):
    _store, run, _token = current_run
    protocol["request_attempt"] += 1
    attempt = protocol["request_attempt"]
    allowed_tool_names = list(allowed_tool_names or [])
    request_hash = responses_request_hash({
        "model": "project-qwen", "store": False, "instructions": protocol["active_instructions"],
        "input": protocol["stage_input_items"],
        "tools": [{"type": "function", "name": name} for name in allowed_tool_names],
        "max_output_tokens": 128,
        "text": {"format": {"type": "json_object"}},
    })
    protocol["pending_request"] = {
        "attempt": attempt, "stage": protocol["stage"], "request_hash": request_hash,
        "reservation_key": call_request_key({"lineage_id": "e", "protocol_attempt": attempt}),
        "allowed_tool_names": allowed_tool_names,
    }
    receipt = {
        "sequence": attempt, "task_id": "task", "stage": "ontology_guided_" + protocol["stage"],
        "ordinal": attempt, "lineage_id": "e", "protocol_attempt": attempt,
        "input_hash": request_hash, "run_fingerprint": run.run_fingerprint,
        "subject_ref": protocol["base_target"]["subject_ref"],
    }
    persist(current_run, protocol, receipts=[receipt])
    return receipt


def turn_change(protocol, *, output=None, status="completed"):
    attempt = protocol["request_attempt"]
    value = {
        "attempt": attempt, "stage": protocol["stage"],
        "input_hash": protocol["pending_request"]["request_hash"],
        "allowed_tool_names": list(protocol["pending_request"]["allowed_tool_names"]),
        "response_id": f"resp-{attempt}", "output_items": output or [],
        "response_status": status,
        "incomplete_details": {"reason": "max_output_tokens"} if status == "incomplete" else None,
        "error": None, "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
    }
    result_ref = protocol_result_ref("e", "model_turn", value)
    protocol["turn_refs"].append(result_ref)
    protocol["completed_attempts"].append(attempt)
    protocol["pending_request"] = None
    return result_ref, {result_ref: {"lineage_id": "e", "field": "model_turn", "value": value}}


def tool_change(protocol, *, attempt=1, call_id="call-1"):
    value = {
        "attempt": attempt, "call_id": call_id,
        "result": {
            "status": "blocked", "data": None, "evidence_refs": [],
            "issues": [{"code": "unknown_tool", "field_path": None, "message": "Unknown tool",
                        "evidence_ids": []}],
        },
    }
    result_ref = protocol_result_ref("e", "tool_result", value)
    protocol["completed_tool_results"].append(result_ref)
    protocol["tool_calls_used"] += 1
    return result_ref, {result_ref: {"lineage_id": "e", "field": "tool_result", "value": value}}


def request_row(current_run, attempt):
    store, run, _token = current_run
    return current_state.get_row(
        store, run, "calls:requests",
        call_request_key({"lineage_id": "e", "protocol_attempt": attempt}),
        model=DocumentRunRequest,
    )


def assembled_input(current_run, protocol):
    store, run, _token = current_run
    return assemble_stage_input(
        protocol,
        load_turn=lambda result_ref: current_state.load_protocol_result(
            store, run, "e", result_ref, "model_turn",
        ),
        load_tool_result=lambda result_ref: current_state.load_protocol_result(
            store, run, "e", result_ref, "tool_result",
        ),
    )


def test_multiple_responses_same_stage_have_exact_per_attempt_results(current_run, monkeypatch):
    protocol = protocol_state()
    reserve(current_run, protocol, allowed_tool_names=["inspect_evidence"])
    output = [
        {"id": "reason-1", "type": "reasoning", "encrypted_content": "opaque-content"},
        {"id": "fc-1", "type": "function_call", "call_id": "call-1",
         "name": "unknown_tool", "arguments": "{"},
    ]
    first_ref, changes = turn_change(protocol, output=output)
    persist(current_run, protocol, results=changes)
    items, pending = assembled_input(current_run, protocol)
    assert items == protocol["stage_input_items"] + output
    assert pending == [(1, "call-1")]
    tool_ref, changes = tool_change(protocol)
    persist(current_run, protocol, results=changes)
    reserve(current_run, protocol)
    second_ref, changes = turn_change(protocol, output=[{
        "type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "{}"}],
    }])
    persist(current_run, protocol, results=changes)
    assert first_ref != second_ref
    assert request_row(current_run, 1)["result_ref"] == first_ref
    assert request_row(current_run, 2)["result_ref"] == second_ref

    store, run, _token = current_run
    read = current_state.read_rows

    def no_result_history(*args, **kwargs):
        assert args[2] is not DocumentRunResult
        return read(*args, **kwargs)

    monkeypatch.setattr(current_state, "read_rows", no_result_history)
    restored = current_state.restore_calls(store, run, run.run_fingerprint)["protocols"]["e"]
    assert restored == protocol
    items, pending = assembled_input(current_run, restored)
    assert pending == []
    assert items[1:3] == output
    assert items[3]["type"] == "function_call_output"
    assert items[3]["call_id"] == "call-1"
    assert items[4]["type"] == "message"
    assert "result_changes" not in restored and "active_input_items" not in restored
    assert "pending_call_ids" not in restored
    assert current_state.load_protocol_result(store, run, "e", first_ref, "model_turn")[
        "output_items"
    ] == output
    assert current_state.load_protocol_result(store, run, "e", tool_ref, "tool_result")[
        "call_id"
    ] == "call-1"
    assert run.progress["model_calls_reserved"] == run.progress["model_calls"] == 2


def test_initial_stage_input_can_be_filled_once_before_request(current_run):
    protocol = protocol_state()
    protocol["stage_input_items"] = []
    persist(current_run, protocol)
    protocol["stage_input_items"] = [{"role": "user", "content": "authorized initial view"}]
    persist(current_run, protocol)
    reserve(current_run, protocol)
    protocol["stage_input_items"] = [{"role": "user", "content": "changed pending input"}]
    with pytest.raises(HeadConflict, match="initial input changed"):
        persist(current_run, protocol)


def test_pending_request_requires_its_atomic_exact_reservation(current_run):
    protocol = protocol_state()
    persist(current_run, protocol)
    before = deepcopy(protocol)
    protocol["request_attempt"] = 1
    protocol["pending_request"] = {
        "attempt": 1, "stage": "discovery", "request_hash": "a" * 64,
        "reservation_key": call_request_key({"lineage_id": "e", "protocol_attempt": 1}),
        "allowed_tool_names": [],
    }
    store, run, token = current_run
    with pytest.raises(HeadConflict, match="has no reservation"):
        current_state.persist_calls(store, run, token, run.run_fingerprint, {
            "current_calls": 1, "version": 2,
            "recognition_run_id": str(run.recognition_run_id),
            "run_fingerprint": run.run_fingerprint, "lineage_calls": {},
            "protocols": {"e": {"key": "e", "value": protocol, "position": 0}},
            "reservations": [], "reservation_sequence": 0,
        })
    restored = current_state.restore_calls(store, run, run.run_fingerprint)
    assert restored["protocols"]["e"] == before
    assert restored["reservations"] == []


def test_unknown_response_stays_reserved_and_cannot_be_replaced_on_resume(current_run):
    protocol = protocol_state()
    reserve(current_run, protocol)
    store, run, _token = current_run
    restored = current_state.restore_calls(store, run, run.run_fingerprint)["protocols"]["e"]
    assert restored["pending_request"] == protocol["pending_request"]
    assert request_row(current_run, 1)["cost_status"] == "unknown"
    assert request_row(current_run, 1)["result_ref"] is None
    assert run.progress["model_calls_reserved"] == run.progress["model_calls_unresolved"] == 1
    modified = deepcopy(protocol)
    modified["pending_request"] = None
    with pytest.raises(HeadConflict, match="unknown model request"):
        persist(current_run, modified)
    assert request_row(current_run, 1)["cost_status"] == "unknown"


def test_incomplete_response_is_received_without_becoming_unknown_cost(current_run):
    protocol = protocol_state()
    reserve(current_run, protocol)
    result_ref, changes = turn_change(protocol, status="incomplete")
    persist(current_run, protocol, results=changes)
    request = request_row(current_run, 1)
    assert request["result_ref"] == result_ref
    assert request["dispatch_state"] == "completed"
    assert request["cost_status"] == "measured"
    assert request["actual_cost"] == 1
    assert protocol["discovery_ref"] is None


def test_result_and_authorization_reference_commit_or_rollback_together(current_run, monkeypatch):
    protocol = protocol_state()
    reserve(current_run, protocol, allowed_tool_names=["retrieve_evidence"])
    _turn_ref, changes = turn_change(protocol, output=[{
        "type": "function_call", "id": "fc-1", "call_id": "call-1",
        "name": "retrieve_evidence", "arguments": "{}",
    }])
    persist(current_run, protocol, results=changes)
    before = deepcopy(protocol)
    tool_ref, changes = tool_change(protocol)
    failed_changes = deepcopy(changes)
    protocol["context_authorization"] = {
        "task_id": "task", "record_ids": ["record-new"],
        "ir_identity": {"document_hash": "d" * 64, "parser_version": "parser-v1",
                        "structure_hash": "e" * 64},
        "context_policy_hash": "f" * 64, "fragments": [],
        "bindings": {name: [] for name in (
            "endpoint_evidence", "proof_dependencies", "competing_subject_refs",
            "subject_evidence_refs", "owner_field_refs", "required_context_refs",
            "counterevidence_refs", "omitted_refs",
        )},
    }
    protocol["evidence_revision"] += 1
    protocol["evidence_hash"] = "c" * 64
    protocol["context_hash"] = "d" * 64
    changes[tool_ref]["value"]["result"] = {
        "status": "ok", "issues": [], "evidence_refs": [],
        "data": {
            "record_ids": ["record-new"], "evidence_ids": [], "context_hash": "d" * 64,
            "new_evidence": True,
            "coverage": {"examined_records": ["record-new"], "unattempted_records": [],
                         "stop_reason": None},
        },
    }
    store, run, _token = current_run
    with pytest.raises(HeadConflict, match="confirmed new retrieval evidence"):
        persist(current_run, protocol, results=failed_changes)
    assert current_state.restore_calls(store, run, run.run_fingerprint)["protocols"]["e"] == before
    put = current_state.put_rows

    def fail_after_result_write(*args, **kwargs):
        if args[3] == "calls:protocols":
            assert current_state.get_row(store, run, "calls:results", tool_ref,
                                         model=DocumentRunResult) is not None
            raise RuntimeError("injected publication failure")
        return put(*args, **kwargs)

    monkeypatch.setattr(current_state, "put_rows", fail_after_result_write)
    with pytest.raises(RuntimeError, match="publication failure"):
        persist(current_run, protocol, results=changes)
    monkeypatch.setattr(current_state, "put_rows", put)
    assert current_state.get_row(
        store, run, "calls:results", tool_ref, model=DocumentRunResult,
    ) is None
    assert current_state.restore_calls(store, run, run.run_fingerprint)["protocols"]["e"] == before
    persist(current_run, protocol, results=changes)
    restored = current_state.restore_calls(store, run, run.run_fingerprint)["protocols"]["e"]
    assert restored["context_authorization"] == protocol["context_authorization"]
    result = current_state.load_protocol_result(store, run, "e", tool_ref, "tool_result")
    assert "context_authorization" not in result


def test_exact_result_rewrite_wrong_request_hash_and_foreign_call_are_rejected(current_run):
    protocol = protocol_state()
    reserve(current_run, protocol, allowed_tool_names=["inspect_evidence"])
    result_ref, changes = turn_change(protocol, output=[{
        "type": "function_call", "id": "fc-1", "call_id": "call-1",
        "name": "inspect_evidence", "arguments": "{}",
    }])
    wrong = deepcopy(changes)
    wrong[result_ref]["value"]["input_hash"] = "f" * 64
    with pytest.raises(HeadConflict, match="reserved request"):
        persist(current_run, protocol, results=wrong)
    assert request_row(current_run, 1)["cost_status"] == "unknown"
    persist(current_run, protocol, results=changes)
    changed = deepcopy(changes)
    changed[result_ref]["value"]["output_items"] = []
    with pytest.raises(HeadConflict, match="cannot be rewritten"):
        persist(current_run, protocol, results=changed)
    _tool_ref, wrong_call = tool_change(protocol, call_id="not-in-response")
    with pytest.raises(HeadConflict, match="unconsumable response"):
        persist(current_run, protocol, results=wrong_call)


def test_received_turn_cannot_widen_original_request_tool_permissions(current_run):
    protocol = protocol_state()
    reserve(current_run, protocol, allowed_tool_names=["inspect_evidence"])
    result_ref, changes = turn_change(protocol)
    wrong = deepcopy(changes)
    wrong[result_ref]["value"]["allowed_tool_names"] = ["inspect_evidence", "retrieve_evidence"]
    with pytest.raises(HeadConflict, match="tool permissions differ"):
        persist(current_run, protocol, results=wrong)
    assert request_row(current_run, 1)["result_ref"] is None
    persist(current_run, protocol, results=changes)
    store, run, _token = current_run
    result = current_state.load_protocol_result(store, run, "e", result_ref, "model_turn")
    assert result["allowed_tool_names"] == ["inspect_evidence"]


def test_answer_only_response_retains_raw_call_but_cannot_confirm_tool_execution(current_run):
    protocol = protocol_state()
    reserve(current_run, protocol)
    result_ref, changes = turn_change(protocol, output=[{
        "type": "function_call", "id": "fc-1", "call_id": "call-1",
        "name": "inspect_evidence", "arguments": "{}",
    }])
    persist(current_run, protocol, results=changes)
    store, run, _token = current_run
    assert current_state.load_protocol_result(store, run, "e", result_ref, "model_turn")[
        "allowed_tool_names"
    ] == []
    _tool_ref, changes = tool_change(protocol)
    with pytest.raises(HeadConflict, match="answer-only response"):
        persist(current_run, protocol, results=changes)


def test_unoffered_tool_preserves_blocked_observation_but_cannot_confirm_success(current_run):
    protocol = protocol_state()
    reserve(current_run, protocol, allowed_tool_names=["inspect_evidence"])
    _result_ref, changes = turn_change(protocol, output=[{
        "type": "function_call", "id": "fc-1", "call_id": "call-1",
        "name": "retrieve_evidence", "arguments": "{}",
    }])
    persist(current_run, protocol, results=changes)
    tool_ref, changes = tool_change(protocol)
    wrong = deepcopy(changes)
    wrong[tool_ref]["value"]["result"] = {
        "status": "ok", "issues": [], "evidence_refs": [], "data": {
            "record_ids": [], "evidence_ids": [], "context_hash": protocol["context_hash"],
            "new_evidence": False,
            "coverage": {"examined_records": [], "unattempted_records": [], "stop_reason": None},
        },
    }
    with pytest.raises(HeadConflict, match="stored tool result"):
        persist(current_run, protocol, results=wrong)
    persist(current_run, protocol, results=changes)
    store, run, _token = current_run
    assert current_state.load_protocol_result(store, run, "e", tool_ref, "tool_result")[
        "result"
    ]["status"] == "blocked"


@pytest.mark.parametrize("names", [
    None, "inspect_evidence", [""], ["validate_graph"],
    ["inspect_evidence", "inspect_evidence"], ["check_claim_binding"],
])
def test_pending_and_response_permissions_reject_invalid_or_unoffered_tools(current_run, names):
    protocol = protocol_state()
    reserve(current_run, protocol)
    invalid = deepcopy(protocol)
    invalid["pending_request"]["allowed_tool_names"] = names
    with pytest.raises(ValueError, match="pending reservation"):
        validate_tool_protocol(invalid)
    state = {"version": 2, "recognition_run_id": "run", "run_fingerprint": "fp",
             "lineage_calls": {"e": 1}, "reservations": [], "protocols": {"e": invalid}}
    with pytest.raises(CheckpointMismatch, match="pending reservation"):
        _validate_model_call_state(state, check_paid_prefix=False)
    result_ref, changes = turn_change(protocol)
    invalid_turn = {**changes[result_ref]["value"], "allowed_tool_names": names}
    with pytest.raises(ValueError, match="model turn result"):
        validate_protocol_result("model_turn", invalid_turn)


@pytest.mark.parametrize("field", ["active_input_items", "pending_call_ids", "result_changes"])
def test_current_protocol_rejects_duplicate_or_transient_persisted_fields(field):
    protocol = {**protocol_state(), field: []}
    with pytest.raises(ValueError, match="current state fields"):
        validate_tool_protocol(protocol)


def test_protocol_validator_checks_wire_protocol_and_paid_receipts():
    protocol = protocol_state()
    state = {
        "version": 2, "recognition_run_id": "run", "run_fingerprint": "fp",
        "lineage_calls": {"e": 0}, "reservations": [], "protocols": {"e": protocol},
    }
    _validate_model_call_state(state)
    protocol["api_protocol"] = "chat_completions"
    with pytest.raises(CheckpointMismatch, match="identity or stage"):
        _validate_model_call_state(state)


@pytest.mark.parametrize("field", ["instructions", "input", "tools", "text", "include", "store"])
def test_actual_request_hash_covers_every_wire_field(field):
    request = {
        "instructions": "rules", "input": [{"role": "user", "content": "evidence"}],
        "tools": [], "text": {"format": {"type": "json_object"}}, "include": [], "store": False,
    }
    changed = {**request, field: "changed"}
    assert responses_request_hash(changed) != responses_request_hash(request)
