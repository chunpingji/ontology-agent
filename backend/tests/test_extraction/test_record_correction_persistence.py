"""Record answer correction persists through the real coordinator and current state."""

import json
from copy import deepcopy

import pytest

from app.models.document_analysis import DocumentRunResult
from app.services.document_analysis import current_state
from app.services.document_analysis.run_store import HeadConflict
from app.services.extraction.ontology_guided.record_discovery import RECORD_PROTOCOL
from tests.test_extraction.test_record_executor import record_setup

pytest_plugins = ["tests.test_extraction.test_tool_engine_resume"]


def correction_input(protocol):
    for item in protocol["stage_input_items"]:
        for part in item.get("content", []):
            if part.get("type") == "input_text":
                value = json.loads(part["text"])
                if "answer_correction" in value:
                    return value["answer_correction"]
    return None


def record_mistake(stage):
    def mistake(view, payload, *, record):
        if not record or view["stage"] != stage or "answer_correction" in view:
            return payload
        if stage == "discovery":
            prop = payload["properties"][0]
            prop["bridge_ref_ids"] = [prop["value_quote"]["evidence_id"]]
        else:
            target = next(target for target in view["verification_input"]["targets"]
                          if target["payload"].get("local_id") == "pA")
            answer = next(answer for answer in payload["verifications"]
                          if answer["target_id"] == target["target_id"])
            facet = next(facet for facet in answer["facets"] if facet["name"] == "qualifiers")
            facet["support"] = []
        return payload

    return mistake


@pytest.mark.parametrize("stage", ["discovery"])
@pytest.mark.parametrize("pause_after_answer", [False, True])
def test_record_correction_commits_and_cold_resume_preserves_paid_results(
    tmp_path, monkeypatch, current_run, stage, pause_after_answer,
):
    args, executor, requests, hooks = record_setup(
        tmp_path, monkeypatch, current_run, empty_relations=True, transform=record_mistake(stage),
    )
    stopped = False
    persist = hooks["model_call_hook"]

    def calls(state):
        nonlocal stopped
        persist(state)
        for row in state["protocols"].values():
            protocol = row["value"]
            if (protocol["version"] != RECORD_PROTOCOL or protocol["stage"] != stage
                    or not correction_input(protocol)):
                continue
            has_answer = any(result["field"] == "model_turn"
                             for result in state.get("result_changes", {}).values())
            if has_answer == pause_after_answer:
                stopped = True

    paused = executor(
        max_model_calls_per_record=4, progress_hook=lambda _: not stopped,
    ).run(**args, **{**hooks, "model_call_hook": calls})
    assert stopped and "execution_pause_requested" in paused.diagnostics

    store, run, _ = current_run
    store.db.expire_all()
    saved_calls = current_state.restore_calls(store, run, run.run_fingerprint)
    records = [protocol for protocol in saved_calls["protocols"].values()
               if protocol["version"] == RECORD_PROTOCOL]
    assert len(records) == 1
    assert records[0][stage + "_ref"] is None
    assert records[0]["pending_request"] is None
    assert correction_input(records[0])
    expected_paid = (1 if stage == "discovery" else 2) + int(pause_after_answer)
    assert saved_calls["reservation_sequence"] == len(requests) == expected_paid
    assert len(records[0]["completed_attempts"]) == expected_paid
    paid_results = deepcopy(current_state.read_rows(
        store, run, DocumentRunResult,
    )["calls:results"])
    paid_requests = deepcopy(requests)

    resumed = executor(max_model_calls_per_record=4).run(
        **args, **hooks,
        resume_state=vars(current_state.restore_work(store, run, run.run_fingerprint)),
        model_call_state=saved_calls,
    )
    assert {prop.raw_value for prop in resumed.graph.properties} == {"A", "B"}
    record_requests = [request for request in requests if "members" not in request]
    assert len(record_requests) == 3
    assert sum("answer_correction" in request for request in record_requests) == 1
    assert requests[:expected_paid] == paid_requests
    assert all(requests.count(request) == 1 for request in paid_requests)
    restored_calls = current_state.restore_calls(store, run, run.run_fingerprint)
    assert restored_calls["reservation_sequence"] == len(requests)
    assert resumed.graph.progress.model_calls == len(requests)
    assert resumed.graph.progress.model_calls_unresolved == 0
    results = current_state.read_rows(store, run, DocumentRunResult)["calls:results"]
    assert all(results[key] == value for key, value in paid_results.items())


@pytest.mark.parametrize("stage", ["discovery"])
@pytest.mark.parametrize("mutation", [
    "evidence_units", "previous_answer", "completed_attempts", "repeat_correction",
])
def test_record_correction_rejects_changed_authority_answer_cost_or_repeated_correction(
    tmp_path, monkeypatch, current_run, stage, mutation,
):
    args, executor, requests, hooks = record_setup(
        tmp_path, monkeypatch, current_run, empty_relations=True, transform=record_mistake(stage),
    )
    store, run, _ = current_run
    stopped = False
    persist = hooks["model_call_hook"]

    def calls(state):
        nonlocal stopped
        protocols = [row["value"] for row in state["protocols"].values()]
        corrective = any(protocol["version"] == RECORD_PROTOCOL
                         and protocol["stage"] == stage and correction_input(protocol)
                         for protocol in protocols)
        paid_turns = [result["value"] for result in state.get("result_changes", {}).values()
                      if result["field"] == "model_turn"]
        repeating = mutation == "repeat_correction"
        if not corrective or bool(paid_turns) != repeating:
            persist(state)
            return
        if repeating:
            persist(state)

        baseline = deepcopy(current_state.restore_calls(store, run, run.run_fingerprint))
        paid_results = deepcopy(current_state.read_rows(
            store, run, DocumentRunResult,
        )["calls:results"])
        modified = deepcopy(state)
        modified.pop("result_changes", None)
        protocol = next(row["value"] for row in modified["protocols"].values()
                        if row["value"]["version"] == RECORD_PROTOCOL)
        item = protocol["stage_input_items"][0]["content"][0]
        value = json.loads(item["text"])
        if mutation == "evidence_units":
            assert value["evidence_units"]
            value["evidence_units"] = []
        elif mutation == "previous_answer":
            field = "entities" if stage == "discovery" else "verifications"
            assert value["answer_correction"]["previous_answer"][field]
            value["answer_correction"]["previous_answer"][field] = []
        elif mutation == "completed_attempts":
            protocol["completed_attempts"] = []
        else:
            protocol.update(turn_refs=[], completed_tool_results=[])
            answer = next(part["text"] for output in paid_turns[0]["output_items"]
                          if output.get("type") == "message" for part in output["content"]
                          if part.get("type") == "output_text")
            value["answer_correction"]["previous_answer"] = json.loads(answer)
            value["answer_correction"]["issues"][0]["message"] = "再次纠正已经纠正过的回答"
        item["text"] = json.dumps(value, ensure_ascii=False)
        with pytest.raises(HeadConflict):
            persist(modified)
        store.db.expire_all()
        assert current_state.restore_calls(store, run, run.run_fingerprint) == baseline
        results = current_state.read_rows(store, run, DocumentRunResult)["calls:results"]
        assert results == paid_results
        if not repeating:
            persist(state)
        stopped = True

    paused = executor(
        max_model_calls_per_record=4, progress_hook=lambda _: not stopped,
    ).run(**args, **{**hooks, "model_call_hook": calls})
    assert stopped and "execution_pause_requested" in paused.diagnostics
    expected_paid = (1 if stage == "discovery" else 2) + int(mutation == "repeat_correction")
    assert len(requests) == expected_paid


def test_invalid_verification_is_downgraded_without_answer_correction(
    tmp_path, monkeypatch, current_run,
):
    args, executor, requests, hooks = record_setup(
        tmp_path,
        monkeypatch,
        current_run,
        empty_relations=True,
        transform=record_mistake("verification"),
    )
    result = executor(max_model_calls_per_record=4).run(**args, **hooks)
    record_requests = [request for request in requests if "members" not in request]
    assert record_requests
    assert all("answer_correction" not in request for request in record_requests)
    assert {prop.raw_value for prop in result.graph.properties} == {"B"}

    store, run, _ = current_run
    calls = current_state.restore_calls(store, run, run.run_fingerprint)
    protocols = [value for value in calls["protocols"].values()
                 if value["version"] == RECORD_PROTOCOL]
    assert protocols
    assert all(protocol["stage"] == "finalize" for protocol in protocols)
    assert all(not correction_input(protocol) for protocol in protocols)
    assert calls["reservation_sequence"] == len(requests)
