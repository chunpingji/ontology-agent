"""Batch receipts retain physical cost and member budgets through cold resume."""

import json
from copy import deepcopy

import pytest

from app.config import settings
from app.models.document_analysis import DocumentRunRequest
from app.services.document_analysis import current_state, execution
from app.services.document_analysis.run_store import HeadConflict
from app.services.extraction.ontology_guided.contracts import VerificationTarget, VersionedRef
from app.services.extraction.ontology_guided.current_work import (
    TOOL_BATCH_PROTOCOL_VERSION,
    call_request_key,
    member_result_version,
    protocol_result_ref,
)
from app.services.extraction.ontology_guided.recognition_batch import (
    RecognitionBatchPolicy,
    RecognitionWorkUnit,
)
from app.services.extraction.ontology_guided.scheduler import RecognitionTask
from tests.test_extraction.test_tool_engine_graph_contracts import target_values
from tests.test_extraction.test_tool_engine_resume import protocol_state

pytest_plugins = ["tests.test_extraction.test_tool_engine_resume"]


def batch_protocol():
    tasks, states = [], {}
    for i in range(2):
        values = target_values()
        values["document_context"].document_hash = "d" * 64
        task = RecognitionTask.create(
            subject=values["subject_ref"], predicate_iri=f"urn:property-{i}",
            predicate_kind="data", record_id="record", phase=1, hop=0,
            dependency_hash=f"predicate-dependency-{i}",
        )
        tasks.append(task)
        values.update(task_id=task.task_id, predicate_iri=task.predicate_iri,
                      claim_ref=VersionedRef(id=task.claim_lineage_id, revision=1))
        prior = protocol_state()
        states[task.task_id] = {key: prior[key] for key in (
            "scope_id", "assertion_generation", "evidence_revision", "evidence_hash",
            "context_hash", "context_authorization", "tool_calls_used", "materialized_refs",
            "recovery_kind", "recovery_used",
        )}
        states[task.task_id].update(
            base_target=VerificationTarget.create(**values).model_dump(mode="json"),
            last_participating_request_attempt=0, last_stage_group_seq=1,
        )
    unit = RecognitionWorkUnit.create(
        run_fingerprint="responses-current-fingerprint",
        policy=RecognitionBatchPolicy(), members=tasks,
    )
    return {
        "version": TOOL_BATCH_PROTOCOL_VERSION, "lineage_id": unit.work_unit_id,
        "work_unit": unit.model_dump(mode="json"), "member_states": states,
        "api_protocol": "responses", "stage": "discovery", "stage_member_ids": list(states),
        "stage_group_seq": 1, "request_attempt": 0, "completed_attempts": [],
        "active_instructions": "Use only each member's evidence.",
        "stage_input_items": [{"role": "user", "content": "member-specific evidence"}],
        "pending_request": None, "turn_refs": [], "completed_tool_results": [],
        "discovery_refs": {}, "verification_refs": {}, "outcome_refs": {},
    }


def persist(current_run, protocol, *, receipts=(), results=None, work_changes=None):
    store, run, token = current_run
    unit_id = protocol["lineage_id"]
    return current_state.persist_calls(store, run, token, run.run_fingerprint, {
        "current_calls": 1, "version": 3, "recognition_run_id": str(run.recognition_run_id),
        "run_fingerprint": run.run_fingerprint, "lineage_calls": {},
        "protocols": {json.dumps(unit_id): {"key": unit_id, "value": deepcopy(protocol),
                                           "position": 0}},
        "reservations": list(receipts), "reservation_sequence": protocol["request_attempt"],
        "result_changes": results or {}, "work_changes": work_changes,
        "expected_work_version": run.work_version,
    })


def reserve(current_run, protocol, *, tools=()):
    protocol["request_attempt"] += 1
    attempt = protocol["request_attempt"]
    unit_id = protocol["lineage_id"]
    protocol["pending_request"] = {
        "attempt": attempt, "stage": protocol["stage"], "request_hash": "f" * 64,
        "reservation_key": call_request_key({"lineage_id": unit_id, "protocol_attempt": attempt}),
        "allowed_tool_names": list(tools), "stage_group_seq": protocol["stage_group_seq"],
        "member_task_ids": list(protocol["stage_member_ids"]),
    }
    for task_id in protocol["stage_member_ids"]:
        protocol["member_states"][task_id].update(
            last_participating_request_attempt=attempt,
            last_stage_group_seq=protocol["stage_group_seq"],
        )
    members = {task["task_id"]: task for task in protocol["work_unit"]["members"]}
    receipt = {
        "sequence": attempt, "task_id": unit_id, "lineage_id": unit_id,
        "stage": "ontology_guided_" + protocol["stage"], "ordinal": attempt,
        "protocol_attempt": attempt, "input_hash": "f" * 64,
        "run_fingerprint": current_run[1].run_fingerprint,
        "stage_group_seq": protocol["stage_group_seq"],
        "member_task_ids": list(protocol["stage_member_ids"]),
        "member_lineage_ids": [members[key]["claim_lineage_id"]
                               for key in protocol["stage_member_ids"]],
    }
    persist(current_run, protocol, receipts=[receipt])
    return receipt


def confirm(current_run, protocol, *, output=(), reference_error=None):
    value = {
        "attempt": protocol["request_attempt"], "stage": protocol["stage"], "input_hash": "f" * 64,
        "allowed_tool_names": protocol["pending_request"]["allowed_tool_names"],
        "response_id": f'resp-{protocol["request_attempt"]}', "output_items": list(output),
        "response_status": "completed", "incomplete_details": None, "error": None, "usage": None,
        "stage_group_seq": protocol["stage_group_seq"],
        "member_task_ids": list(protocol["stage_member_ids"]),
    }
    if reference_error is not None:
        value["reference_error"] = reference_error
    ref = protocol_result_ref(protocol["lineage_id"], "model_turn", value)
    protocol["turn_refs"].append(ref)
    protocol["completed_attempts"].append(protocol["request_attempt"])
    protocol["pending_request"] = None
    persist(current_run, protocol, results={ref: {
        "lineage_id": protocol["lineage_id"], "field": "model_turn", "value": value,
    }})
    return ref


def member_result(protocol, task_id, field="discovery", value=None):
    version = member_result_version(protocol, task_id)
    value = value if value is not None else {"synthetic": "same value for both members"}
    ref = protocol_result_ref(protocol["lineage_id"], field, value,
                              member_task_id=task_id, result_version=version)
    protocol[field + "_refs"][task_id] = ref
    return ref, {ref: {"lineage_id": protocol["lineage_id"], "member_task_id": task_id,
                      "result_version": version, "field": field, "value": value}}


def test_physical_cost_and_member_budgets_restore(current_run):
    protocol = batch_protocol()
    persist(current_run, protocol)
    receipt = reserve(current_run, protocol)
    confirm(current_run, protocol)
    store, run, _ = current_run
    restored = current_state.restore_calls(store, run, run.run_fingerprint)
    assert restored["reservations"] == [receipt]
    assert restored["reservation_sequence"] == 1
    assert restored["lineage_calls"] == {lineage: 1 for lineage in receipt["member_lineage_ids"]}
    assert run.progress["model_calls_reserved"] == run.progress["model_calls"] == 1
    assert current_state.read_rows(store, run, DocumentRunRequest,
                                   prefix="calls:lineage_calls") == {}
    assert restored["protocols"][protocol["lineage_id"]] == protocol


@pytest.mark.parametrize("reason", ["unknown_model_reference", "model_reference_collision"])
def test_rejected_batch_reference_turn_is_paid_but_cannot_confirm_tool_result(current_run, reason):
    protocol = batch_protocol()
    persist(current_run, protocol)
    reserve(current_run, protocol, tools=["inspect_evidence"])
    owner = protocol["stage_member_ids"][0]
    output = [{"type": "function_call", "call_id": "call-1", "name": "inspect_evidence",
               "arguments": json.dumps({"member_task_id": owner,
                                        "evidence_ids": ["@r:unknown"]})}]
    turn_ref = confirm(current_run, protocol, output=output, reference_error=reason)
    stored_protocol = deepcopy(protocol)
    store, run, _ = current_run
    paid = current_state.load_protocol_result(
        store, run, protocol["lineage_id"], turn_ref, "model_turn",
    )
    assert paid["output_items"] == output and paid["reference_error"] == reason
    assert paid["response_status"] == "completed" and paid["error"] is None
    assert run.progress["model_calls"] == run.progress["model_calls_reserved"] == 1
    value = {
        "attempt": 1, "call_id": "call-1", "stage_group_seq": protocol["stage_group_seq"],
        "member_task_id": owner, "result": {
            "status": "blocked", "data": None, "evidence_refs": [], "issues": [{
                "code": "reference_outside_context", "field_path": None,
                "message": "Unknown reference", "evidence_ids": [],
            }],
        },
    }
    ref = protocol_result_ref(protocol["lineage_id"], "tool_result", value)
    protocol["completed_tool_results"].append(ref)
    protocol["member_states"][owner]["tool_calls_used"] += 1
    with pytest.raises(HeadConflict, match="unconsumable response"):
        persist(current_run, protocol, results={ref: {
            "lineage_id": protocol["lineage_id"], "field": "tool_result", "value": value,
        }})
    restored = current_state.restore_calls(store, run, run.run_fingerprint)
    assert restored["protocols"][protocol["lineage_id"]] == stored_protocol
    assert run.progress["model_calls"] == run.progress["model_calls_reserved"] == 1


def test_member_results_cannot_be_read_using_another_owner(current_run):
    protocol = batch_protocol()
    persist(current_run, protocol)
    reserve(current_run, protocol)
    confirm(current_run, protocol)
    owners = list(protocol["member_states"])
    ref_a, changes_a = member_result(protocol, owners[0])
    ref_b, changes_b = member_result(protocol, owners[1])
    assert ref_a != ref_b
    persist(current_run, protocol, results=changes_a | changes_b)
    store, run, _ = current_run
    assert current_state.load_protocol_record(
        store, run, protocol["lineage_id"], ref_a, "discovery", member_task_id=owners[0],
    ) == changes_a[ref_a]
    for owner in (owners[1], None):
        with pytest.raises(HeadConflict, match="reference mismatch"):
            current_state.load_protocol_result(store, run, protocol["lineage_id"], ref_a,
                                               "discovery", member_task_id=owner)


def test_subset_verification_cost_and_previous_result_version(current_run):
    protocol = batch_protocol()
    persist(current_run, protocol)
    first = reserve(current_run, protocol)
    confirm(current_run, protocol)
    owner = protocol["stage_member_ids"][0]
    discovery_ref, changes = member_result(protocol, owner)
    persist(current_run, protocol, results=changes)
    protocol.update(stage="verification", stage_group_seq=2, stage_member_ids=[owner],
                    turn_refs=[], completed_tool_results=[], active_instructions="Verify one",
                    stage_input_items=[{"role": "user", "content": "independent verification"}])
    persist(current_run, protocol)
    second = reserve(current_run, protocol)
    confirm(current_run, protocol)
    store, run, _ = current_run
    restored = current_state.restore_calls(store, run, run.run_fingerprint)
    assert restored["reservation_sequence"] == run.progress["model_calls"] == 2
    assert restored["lineage_calls"][second["member_lineage_ids"][0]] == 2
    assert restored["lineage_calls"][first["member_lineage_ids"][1]] == 1
    record = current_state.load_protocol_record(store, run, protocol["lineage_id"], discovery_ref,
                                               "discovery", member_task_id=owner)
    assert record["result_version"]["last_participating_request_attempt"] == 1


@pytest.mark.parametrize("change", ["refund", "group", "tool_counter", "authorization", "members"])
def test_durable_state_rejects_regression(current_run, change):
    protocol = batch_protocol()
    owner = protocol["stage_member_ids"][0]
    protocol["member_states"][owner]["tool_calls_used"] = 1
    persist(current_run, protocol)
    reserve(current_run, protocol)
    if change == "refund":
        protocol["pending_request"] = None
    elif change == "group":
        protocol["stage_member_ids"] = [owner]
        protocol["pending_request"]["member_task_ids"] = [owner]
    elif change == "tool_counter":
        protocol["member_states"][owner]["tool_calls_used"] = 0
    elif change == "authorization":
        protocol["member_states"][owner]["context_hash"] = "c" * 64
    else:
        protocol["work_unit"]["members"].reverse()
    with pytest.raises((HeadConflict, execution.CheckpointMismatch)):
        persist(current_run, protocol)


def test_unknown_request_still_consumes_budget_after_restore(current_run):
    protocol = batch_protocol()
    persist(current_run, protocol)
    receipt = reserve(current_run, protocol)
    store, run, _ = current_run
    restored = current_state.restore_calls(store, run, run.run_fingerprint)
    assert restored["protocols"][protocol["lineage_id"]]["pending_request"]
    assert restored["lineage_calls"] == {lineage: 1 for lineage in receipt["member_lineage_ids"]}
    assert run.progress["model_calls_reserved"] == run.progress["model_calls_unresolved"] == 1
    assert run.progress["model_calls"] == 0


def test_technical_retry_can_publish_a_new_owned_outcome_after_a_new_request(current_run):
    protocol = batch_protocol()
    owner = protocol["stage_member_ids"][0]
    persist(current_run, protocol)
    reserve(current_run, protocol)
    confirm(current_run, protocol)
    old_ref, changes = member_result(protocol, owner, "outcome", {"complete": False})
    persist(current_run, protocol, results=changes)
    reserve(current_run, protocol)
    confirm(current_run, protocol)
    new_ref, changes = member_result(protocol, owner, "outcome", {"complete": True})
    assert new_ref != old_ref
    persist(current_run, protocol, results=changes)
    store, run, _ = current_run
    assert current_state.load_protocol_result(
        store, run, protocol["lineage_id"], old_ref, "outcome", member_task_id=owner,
    ) == {"complete": False}
    assert current_state.load_protocol_result(
        store, run, protocol["lineage_id"], new_ref, "outcome", member_task_id=owner,
    ) == {"complete": True}


def test_initial_unit_and_active_queue_boundary_are_atomic(current_run, monkeypatch):
    protocol = batch_protocol()
    store, run, _ = current_run
    changes = {"control": {"current": {"active_unit_ref": protocol["lineage_id"]}}}
    original = current_state.put_rows

    def fail_calls(*args, **kwargs):
        if args[3] == "calls:protocols":
            raise RuntimeError("interrupted current unit commit")
        return original(*args, **kwargs)

    monkeypatch.setattr(current_state, "put_rows", fail_calls)
    with pytest.raises(RuntimeError, match="interrupted"):
        persist(current_run, protocol, work_changes=changes)
    assert current_state.get_row(store, run, "work:control") is None
    assert run.work_version == 0
    monkeypatch.setattr(current_state, "put_rows", original)
    assert persist(current_run, protocol, work_changes=changes) == 1
    assert current_state.get_row(store, run, "work:control") == changes["control"]["current"]


@pytest.mark.parametrize("batching", [{"max_members": True}, {"max_members": 0},
                                       {"max_members": 5}, {"bogus": 1},
                                       {"version": "unknown"}, None, False])
def test_configuration_rejects_invalid_policy(monkeypatch, batching):
    monkeypatch.setattr(settings, "ontology_extraction_options", {"batching": batching})
    with pytest.raises(ValueError):
        execution.freeze_tool_engine_policy()


@pytest.mark.parametrize("options", [{}, {"responses": {"strict_tools": False}}])
def test_new_runs_enable_batching_by_default_without_mutating_settings(monkeypatch, options):
    original = deepcopy(options)
    monkeypatch.setattr(settings, "ontology_extraction_options", options)
    current = execution.freeze_tool_engine_policy()
    assert current["recognition_batching"] == {"version": "predicate-batch-v1", "max_members": 4}
    assert current["model_call_state_version"] == 3
    assert "batching" not in current["extraction_options"]
    assert settings.ontology_extraction_options == original


@pytest.mark.parametrize("max_members", [1, 2, 4])
def test_explicit_batching_size_overrides_default(monkeypatch, max_members):
    monkeypatch.setattr(settings, "ontology_extraction_options", {
        "batching": {"max_members": max_members},
    })
    current = execution.freeze_tool_engine_policy()
    assert current["recognition_batching"] == {
        "version": "predicate-batch-v1", "max_members": max_members,
    }
    assert current["model_call_state_version"] == 3
    assert current["extraction_protocol"] == "ontology-tool-extraction-v1"
    assert "batching" not in current["extraction_options"]
