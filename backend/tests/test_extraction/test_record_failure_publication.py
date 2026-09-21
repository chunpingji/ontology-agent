"""A failed paid record response remains incomplete without aborting publication."""

import json
from dataclasses import replace

import pytest

from app.models.document_analysis import DocumentRunRequest, DocumentRunResult
from app.services.document_analysis import current_state
from app.services.extraction.ontology_guided.record_discovery import RECORD_PROTOCOL
from app.services.llm import local_client
from tests.test_extraction.test_record_executor import record_setup

pytest_plugins = ["tests.test_extraction.test_tool_engine_resume"]


@pytest.mark.parametrize("stage", ["discovery", "verification"])
@pytest.mark.parametrize("tool_first", [False, True])
def test_failed_record_response_publishes_incomplete_outcome_and_keeps_paid_results(
    tmp_path, monkeypatch, current_run, stage, tool_first,
):
    args, executor, requests, hooks = record_setup(
        tmp_path, monkeypatch, current_run, empty_relations=True,
    )
    transport = local_client.responses_create
    failed_stage_calls = 0

    def response(*args, **kwargs):
        nonlocal failed_stage_calls
        turn = transport(*args, **kwargs)
        view = json.loads(kwargs["input_items"][0]["content"][0]["text"])
        if view["stage"] != stage:
            return turn
        failed_stage_calls += 1
        if tool_first and failed_stage_calls == 1:
            return replace(turn, output_items=[{
                "type": "function_call", "call_id": "unknown-call", "name": "unknown_tool",
                "arguments": "{}",
            }])
        return replace(turn, output_items=[], response_status="incomplete",
                       incomplete_details={"reason": "max_output_tokens"})

    monkeypatch.setattr(local_client, "responses_create", response)
    result = executor(max_model_calls_per_record=8).run(**args, **hooks)
    assert result.graph.progress.completion == "incomplete"
    assert not result.graph.properties
    assert not result.graph.edges
    assert result.graph.progress.model_calls == len(requests)
    assert result.graph.progress.model_calls_unresolved == 0

    store, run, _ = current_run
    store.db.expire_all()
    calls = current_state.restore_calls(store, run, run.run_fingerprint)
    protocols = [p for p in calls["protocols"].values() if p["version"] == RECORD_PROTOCOL]
    assert len(protocols) == 1
    protocol = protocols[0]
    outcome = current_state.load_protocol_result(
        store, run, protocol["lineage_id"], protocol["outcome_ref"], "outcome",
    )
    assert outcome["complete"] is False
    assert outcome["semantic_outcome"] == "not_checked"
    discovery_rejected_tool = stage == "discovery" and tool_first
    assert outcome["reason_code"] == (
        "model_tool_protocol_invalid" if discovery_rejected_tool
        else "model_output_truncated"
    )

    paid = current_state.read_rows(store, run, DocumentRunResult)["calls:results"]
    turns = [r["value"] for r in paid.values() if r["field"] == "model_turn"]
    tools = [r["value"] for r in paid.values() if r["field"] == "tool_result"]
    assert len(turns) == len(requests)
    assert len(tools) == int(tool_first and not discovery_rejected_tool)
    reservations = current_state.read_rows(store, run, DocumentRunRequest)["calls:requests"]
    assert len(reservations) == len(requests)
    assert all(r["dispatch_state"] == "completed" and r["result_ref"] in paid
               for r in reservations.values())

    restored = vars(current_state.restore_work(store, run, run.run_fingerprint))
    record = next(row["value"] for row in restored["work_state"]["record_discovery"].values()
                  if row["key"] == protocol["lineage_id"])
    assert record["status"] == "incomplete"
    resumed = executor(max_model_calls_per_record=8).run(
        **args, **hooks, resume_state=restored, model_call_state=calls,
    )
    assert resumed.graph.progress.completion == "incomplete"
    assert len(requests) == len(turns)
