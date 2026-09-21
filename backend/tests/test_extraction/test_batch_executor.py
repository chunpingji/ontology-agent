"""Batch transport through the actual worker, coordinator and current-state store."""

from __future__ import annotations

import json
from copy import deepcopy
from functools import partial
from threading import get_ident

import pytest

from app.models.document_analysis import DocumentRunRequest
from app.services.document_analysis import current_state
from app.services.document_analysis.run_store import HeadConflict
from app.services.extraction.evidence_identity import evidence_hash
from app.services.extraction.ontology_guided.batch_model_adapter import ReviewedWorkUnit
from app.services.extraction.ontology_guided.claim_protocol import ExtractionProfile
from app.services.extraction.ontology_guided.contracts import EdgeSpec, OntologySnapshot, SlotSpec
from app.services.extraction.ontology_guided.executor import OntologyGuidedExecutor
from app.services.extraction.ontology_guided.recognition_batch import RecognitionBatchPolicy
from app.services.extraction.ontology_guided.records import RecordIndex
from app.services.extraction.ontology_guided.tool_model_adapter import ToolModelRecognitionAdapter
from app.services.extraction.ontology_guided.tool_runtime import ToolLimits
from app.services.llm.local_client import ResponseTurn, StructuredModelError
from tests.test_extraction.test_layered_recognition import ROOT, arguments
from tests.test_extraction.test_ontology_guided_core import _definition

pytest_plugins = ["tests.test_extraction.test_tool_engine_resume"]

ATTRIBUTES = [("urn:batch:code", "编号", "A-001"),
              ("urn:batch:spec", "规格", "大号"),
              ("urn:batch:lot", "批号", "L-003")]
CHILD = "urn:batch:Device"
RELATIONS = [("urn:batch:uses", "使用设备"), ("urn:batch:maintains", "维护设备")]


def setup_batch(
    tmp_path, monkeypatch, current_run, *, max_members=4, failure=None, relations=False,
    transform=None, extra_lines=(),
):
    store, run, token = current_run
    owner_thread = get_ident()
    text = ("本报告使用并维护设备甲。" if relations
            else "本报告编号A-001，规格大号，批号L-003。")
    args = arguments(tmp_path, [text, *extra_lines])
    args.update(recognition_run_id=str(run.recognition_run_id), run_fingerprint=run.run_fingerprint)
    run.document_hash = args["ir"].document_hash
    store.db.commit()
    classes = {
        ROOT: _definition(
            ROOT, "报告", properties=[] if relations else [
                SlotSpec(iri=iri, label=label, declared_by=[ROOT],
                         datatype_iris=["http://www.w3.org/2001/XMLSchema#string"])
                for iri, label, _value in ATTRIBUTES
            ], relationships=[
                EdgeSpec(iri=iri, label=label, declared_by=[ROOT], range_class_iris=[CHILD])
                for iri, label in RELATIONS
            ] if relations else [],
        ),
        **({CHILD: _definition(CHILD, "设备")} if relations else {}),
    }
    ontology = OntologySnapshot(
        snapshot_id="batch-executor-fixture", ontology_hash=evidence_hash(classes),
        classes=classes, created_from="frozen_fixture",
    )
    index = RecordIndex(args["ir"])
    source = index.records[0].source_units[0]
    requests = []

    def quote(value):
        return {"evidence_id": source.evidence_id, "text": value, "context_text": None}

    def transport(_client, **kwargs):
        assert get_ident() != owner_thread
        view = json.loads(kwargs["input_items"][0]["content"][0]["text"])
        requests.append(deepcopy(view))
        if kwargs.get("tool_choice") == "required":
            required = json.loads(kwargs["instructions"].splitlines()[-1])
            output = [{
                "type": "function_call", "id": f"item-{position}",
                "call_id": f"check-{position}", "name": "validate_graph",
                "arguments": json.dumps({
                    **check, "shape_profile_id": required["shape_profile_id"],
                }),
            } for position, check in enumerate(required["required_relation_checks"])]
        else:
            answers = []
            for member in view["members"]:
                if view["stage"] == "discovery":
                    answer = dict(entities=[], properties=[], relations=[],
                                  external_links=[], observations=[], reference_bindings=[])
                    if failure == "missing" and member["predicate_iri"] == ATTRIBUTES[1][0]:
                        continue
                    if failure == "invalid" and member["predicate_iri"] == ATTRIBUTES[1][0]:
                        answers.append({"task_id": member["task_id"], "result": {"entities": []}})
                        continue
                    qualifiers = {"polarity": "affirmed", "modality": "asserted",
                                  "condition_support": [], "scope_qualifiers": []}
                    if relations:
                        answer["entities"] = [{
                            "local_id": "device", "class_iri": CHILD, "representation": "mention",
                            "mentions": [quote("设备甲")], "record_components": [],
                            "identifier_claims": [],
                        }]
                        answer["relations"] = [{
                            "local_id": "relation", "subject_id": member["subject_ref"]["id"],
                            "predicate_iri": member["predicate_iri"], "object_ids": ["device"],
                            "selection": "all", "bridge_support": [quote(text)],
                            "selection_support": [], "qualifiers": qualifiers,
                            "bridge_kind": "document_subject_description", "bridge_ref_ids": [],
                            "source_assertion": {
                                "subject_support": [],
                                "object_support": [{"object_id": "device",
                                                    "support": [quote("设备甲")]}],
                                "predicate_support": [quote(text)], "binding_ids": [],
                            },
                        }]
                    else:
                        iri, label, value = next(row for row in ATTRIBUTES
                                                if row[0] == member["predicate_iri"])
                        if failure == "predicate" and iri == ATTRIBUTES[1][0]:
                            iri = ATTRIBUTES[0][0]
                        answer["properties"] = [{
                            "local_id": "value", "subject_id": member["subject_ref"]["id"],
                            "predicate_iri": iri, "value_quote": quote(value),
                            "field_support": [quote(label)], "unit_support": [],
                            "qualifiers": qualifiers, "bridge_kind": "explicit_assertion",
                            "bridge_ref_ids": [],
                        }]
                else:
                    answer = {"verifications": [{
                        "target_id": target["target_id"], "content_hash": target["content_hash"],
                        "facets": [{
                            "name": facet, "verdict": "supported", "support": [quote(text)],
                            "counterevidence_support": [], "reason": "原文支持本成员声明",
                        } for facet in target["required_facets"]],
                    } for target in member["verification_input"]["targets"]]}
                answers.append({"task_id": member["task_id"], "result": answer})
            batch_answer = {"members": answers}
            if transform is not None:
                transform(batch_answer, view, len(requests))
            output = [{"type": "message", "role": "assistant", "status": "completed",
                       "content": [{"type": "output_text",
                                    "text": json.dumps(batch_answer)}]}]
        return ResponseTurn(
            response_id=f"batch-response-{len(requests)}", response_status="completed",
            output_items=output, incomplete_details=None, error=None,
            usage={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        )

    monkeypatch.setattr("app.services.llm.local_client.responses_create", transport)
    adapter = ToolModelRecognitionAdapter(
        object(), index=index, ontology=ontology, metadata=args["metadata"],
        profile=ExtractionProfile(), token_counter=len, model_identity="batch-controlled-model",
        max_input_tokens=1000000, max_output_tokens=20000, tool_limits=ToolLimits(20000),
        recognition_batching=RecognitionBatchPolicy(max_members=max_members),
        reference_resolution=True,
    )

    def executor(**kwargs):
        return OntologyGuidedExecutor(
            ontology=ontology, engine=None, adapter=adapter, current_state=True, **kwargs,
        )

    def calls(state):
        assert get_ident() == owner_thread
        if state.get("work_changes"):
            state = {**state, "expected_work_version": run.work_version}
        current_state.persist_calls(store, run, token, run.run_fingerprint, state)

    def work(changes):
        assert get_ident() == owner_thread
        current_state.persist_boundary(
            store, run, token, changes=changes, fingerprint=run.run_fingerprint,
            expected_version=run.work_version,
        )

    def batch(value):
        assert get_ident() == owner_thread
        current_state.persist_batch(
            store, run, token, batch=value, fingerprint=run.run_fingerprint, ontology=ontology,
            ir=args["ir"], metadata=args["metadata"], index=index,
            expected_version=run.work_version,
        )

    hooks = dict(
        model_call_hook=calls, work_hook=work, batch_hook=batch,
        protocol_result_loader=partial(current_state.load_protocol_result, store, run),
        protocol_record_loader=partial(current_state.load_protocol_record, store, run),
    )
    return args, executor, requests, hooks


@pytest.mark.parametrize(("max_members", "expected_calls"), [(1, 6), (2, 4), (4, 2)])
def test_three_attributes_share_discovery_and_verification_without_changing_facts(
    tmp_path, monkeypatch, current_run, max_members, expected_calls,
):
    args, executor, requests, hooks = setup_batch(
        tmp_path, monkeypatch, current_run, max_members=max_members,
    )
    result = executor().run(**args, **hooks)
    assert {(prop.predicate_iri, prop.raw_value) for prop in result.graph.properties} == {
        (iri, value) for iri, _label, value in ATTRIBUTES
    }, [(kind, value) for kind, value in result.events if kind == "task_outcome"]
    assert len(requests) == expected_calls, [
        (r["stage"], [m["predicate_iri"] for m in r["members"]]) for r in requests
    ]
    assert result.graph.progress.model_calls == expected_calls
    store, run, _token = current_run
    restored = current_state.restore_calls(store, run, run.run_fingerprint)
    assert restored["version"] == 3
    assert restored["reservation_sequence"] == expected_calls
    assert len(restored["reservations"]) == expected_calls
    assert sorted(restored["lineage_calls"].values()) == [2, 2, 2]
    assert sum(len(receipt["member_task_ids"]) for receipt in restored["reservations"]) == 6


@pytest.mark.parametrize("failure", ["missing", "invalid", "predicate"])
def test_one_member_failure_preserves_independent_properties_and_incomplete_coverage(
    tmp_path, monkeypatch, current_run, failure,
):
    args, executor, requests, hooks = setup_batch(
        tmp_path, monkeypatch, current_run, failure=failure,
    )
    result = executor().run(**args, **hooks)
    assert {prop.predicate_iri for prop in result.graph.properties} == {
        ATTRIBUTES[0][0], ATTRIBUTES[2][0],
    }, result.diagnostics
    bad = [row for row in result.graph.coverage if row.predicate_iri == ATTRIBUTES[1][0]]
    assert bad
    assert result.graph.progress.records_incomplete >= 1
    store, run, _token = current_run
    restored = current_state.restore_calls(store, run, run.run_fingerprint)
    assert max(restored["lineage_calls"].values()) <= 6
    participations = {}
    for request in requests:
        for member in request["members"]:
            iri = member["predicate_iri"]
            participations[iri] = participations.get(iri, 0) + 1
    assert participations[ATTRIBUTES[0][0]] == participations[ATTRIBUTES[2][0]] == 2


@pytest.mark.parametrize("mixed", [False, True])
@pytest.mark.parametrize("pause", [None, "discovery", "verification"])
def test_empty_results_preserve_durable_cost_versions_and_cold_publication(
    tmp_path, monkeypatch, current_run, mixed, pause,
):
    def empty_discovery(answer, view, number):
        if view["stage"] == "discovery":
            for member, result in zip(view["members"], answer["members"]):
                if not mixed or member["predicate_iri"] == ATTRIBUTES[0][0]:
                    result["result"]["properties"] = []

    args, executor, requests, hooks = setup_batch(
        tmp_path, monkeypatch, current_run, transform=empty_discovery,
    )
    store, run, _ = current_run
    persist = hooks["model_call_hook"]
    stop = False

    def pause_at_saved_result(state):
        nonlocal stop
        persist(state)
        if any(row["field"] == pause for row in state.get("result_changes", {}).values()):
            stop = True

    hooks["model_call_hook"] = pause_at_saved_result
    result = executor(progress_hook=lambda _stage: not stop).run(**args, **hooks)
    if pause:
        assert stop and len(requests) == 1
        assert result.graph.properties == []
        rows = current_state.restore_work(store, run, run.run_fingerprint).work_state
        control = rows["control"]["current"]
        calls = current_state.restore_calls(store, run, run.run_fingerprint)
        hooks["model_call_hook"] = persist
        result = executor().run(
            **args, **hooks, model_call_state=calls,
            resume_state={"work_state": rows, "frontier": control["frontier_policy"],
                          "diagnostics": control["diagnostics"]},
        )
    expected_calls = 2 if mixed else 1
    assert len(requests) == expected_calls
    assert result.graph.progress.model_calls == expected_calls
    assert not result.graph.progress.model_calls_unresolved
    assert result.graph.progress.records_incomplete == 0
    assert len(result.graph.properties) == (2 if mixed else 0)
    assert all(member["verification_input"]["targets"] for request in requests[1:]
               for member in request["members"])
    restored = current_state.restore_calls(store, run, run.run_fingerprint)
    assert len(restored["reservations"]) == expected_calls
    assert sorted(restored["lineage_calls"].values()) == ([1, 2, 2] if mixed else [1, 1, 1])
    for unit_id, protocol in restored["protocols"].items():
        for task in protocol["work_unit"]["members"]:
            task_id = task["task_id"]
            if mixed and task["predicate_iri"] != ATTRIBUTES[0][0]:
                continue
            reference = protocol["verification_refs"][task_id]
            value = current_state.load_protocol_record(
                store, run, unit_id, reference, "verification", member_task_id=task_id,
            )
            assert value["value"]["targets"] == []
            assert value["result_version"]["last_participating_request_attempt"] == 1
            assert protocol["member_states"][task_id]["last_participating_request_attempt"] == 1
    rows = current_state.restore_work(store, run, run.run_fingerprint).work_state
    assert len(rows["applied_model_results"]) == 3


@pytest.mark.parametrize(("pause", "paid_before_pause"), [
    ("initial_unit", 0), ("discovery", 1), ("verification", 2),
])
def test_saved_batch_response_resumes_cold_without_another_discovery_charge(
    tmp_path, monkeypatch, current_run, pause, paid_before_pause,
):
    args, executor, requests, hooks = setup_batch(tmp_path, monkeypatch, current_run)
    store, run, _token = current_run
    persist = hooks["model_call_hook"]
    pause_now = False

    def stop_after_response(state):
        nonlocal pause_now
        persist(state)
        results = state.get("result_changes", {}).values()
        if (pause == "initial_unit" and state.get("work_changes")
                or pause == "discovery" and any(row["field"] == "model_turn" for row in results)
                or pause == "verification" and any(row["field"] == "verification"
                                                   for row in results)):
            pause_now = True

    hooks["model_call_hook"] = stop_after_response
    paused = executor(progress_hook=lambda _stage: not pause_now).run(**args, **hooks)
    assert paused.graph.artifact_status == "partial"
    assert len(requests) == paid_before_pause
    assert paused.graph.properties == []
    rows = current_state.restore_work(store, run, run.run_fingerprint).work_state
    control = rows["control"]["current"]
    assert control["active_unit_ref"]
    calls = current_state.restore_calls(store, run, run.run_fingerprint)
    assert calls["reservation_sequence"] == paid_before_pause
    hooks["model_call_hook"] = persist
    resumed = executor().run(
        **args, **hooks, model_call_state=calls,
        resume_state={"work_state": rows, "frontier": control["frontier_policy"],
                      "diagnostics": control["diagnostics"]},
    )
    assert len(requests) == 2
    assert [request["stage"] for request in requests] == ["discovery", "verification"]
    assert len(resumed.graph.properties) == 3
    assert not resumed.graph.progress.model_calls_unresolved
    receipts = current_state.read_rows(store, run, DocumentRunRequest, prefix="calls:requests")
    assert len(receipts.get("calls:requests", {})) == 2


def test_logical_task_limit_leaves_unselected_member_unattempted(
    tmp_path, monkeypatch, current_run,
):
    args, executor, requests, hooks = setup_batch(tmp_path, monkeypatch, current_run)
    result = executor(max_tasks=2).run(**args, **hooks)
    assert len(result.graph.properties) == 2
    assert len(requests) == 2
    assert all(len(request["members"]) == 2 for request in requests)
    assert result.graph.progress.records_examined == 2
    assert result.graph.progress.records_unattempted == 1
    assert result.graph.progress.records_incomplete == 0
    assert result.graph.artifact_status == "partial"
    store, run, _token = current_run
    restored = current_state.restore_calls(store, run, run.run_fingerprint)
    assert sorted(restored["lineage_calls"].values()) == [2, 2]


@pytest.mark.parametrize("stage", ["discovery", "verification"])
@pytest.mark.parametrize("returned_error", [False, True])
def test_unknown_batch_request_preserves_completed_work_and_cold_resume_does_not_resend(
    tmp_path, monkeypatch, current_run, stage, returned_error,
):
    def fail_second_unit(answer, view, number):
        if len(view["members"]) == 1 and view["stage"] == stage:
            raise StructuredModelError("model_request_failed")

    args, executor, requests, hooks = setup_batch(
        tmp_path, monkeypatch, current_run, max_members=2, transform=fail_second_unit,
    )
    if returned_error:
        adapter = executor().adapter
        inspect = adapter.inspect_work_unit

        def return_member_errors(unit, context, menu):
            try:
                return inspect(unit, context, menu)
            except StructuredModelError as exc:
                return ReviewedWorkUnit(unit.work_unit_id, member_errors={
                    task.task_id: str(exc) for task in unit.members
                })

        monkeypatch.setattr(adapter, "inspect_work_unit", return_member_errors)
    result = executor().run(**args, **hooks)
    paid = 3 if stage == "discovery" else 4
    assert len(requests) == paid
    assert len(result.graph.properties) == 2
    assert result.graph.progress.model_calls == paid - 1
    assert result.graph.progress.model_calls_reserved == paid
    assert result.graph.progress.model_calls_unresolved == 1
    assert result.graph.progress.stop_reason == "model_calls_unresolved"
    assert result.graph.progress.completion == "incomplete"
    assert result.graph.progress.records_examined == 2
    assert result.graph.progress.records_unattempted == 1
    assert "model_request_outcome_unknown" in result.diagnostics
    store, run, _token = current_run
    rows = current_state.restore_work(store, run, run.run_fingerprint).work_state
    control = rows["control"]["current"]
    calls = current_state.restore_calls(store, run, run.run_fingerprint)
    protocol = calls["protocols"][control["active_unit_ref"]]
    assert protocol["pending_request"]["stage"] == stage
    assert not protocol["verification_refs"] and not protocol["outcome_refs"]
    resumed = executor().run(
        **args, **hooks, model_call_state=calls,
        resume_state={"work_state": rows, "frontier": control["frontier_policy"],
                      "diagnostics": control["diagnostics"]},
    )
    assert len(requests) == paid
    assert resumed.graph.properties == result.graph.properties
    assert resumed.graph.progress == result.graph.progress
    assert current_state.restore_calls(store, run, run.run_fingerprint) == calls


def test_later_member_proof_failure_rolls_back_whole_publication_and_reuses_paid_results(
    tmp_path, monkeypatch, current_run,
):
    args, executor, requests, hooks = setup_batch(tmp_path, monkeypatch, current_run)
    store, run, _token = current_run
    write_proofs = current_state.write_proofs
    proof_writes = 0

    def fail_after_second_member(*values, **options):
        nonlocal proof_writes
        write_proofs(*values, **options)
        proof_writes += 1
        if proof_writes == 2:
            raise RuntimeError("injected second member proof failure")

    monkeypatch.setattr(current_state, "write_proofs", fail_after_second_member)
    with pytest.raises(RuntimeError, match="second member proof failure"):
        executor().run(**args, **hooks)
    assert len(requests) == 2
    store.db.expire_all()
    rows = current_state.restore_work(store, run, run.run_fingerprint).work_state
    assert not rows.get("properties")
    assert not rows.get("proof_heads")
    assert not rows.get("applied_model_results")
    control = rows["control"]["current"]
    assert control["active_unit_ref"]
    calls = current_state.restore_calls(store, run, run.run_fingerprint)
    protocol = calls["protocols"][control["active_unit_ref"]]
    assert len(protocol["verification_refs"]) == 3
    assert protocol["outcome_refs"] == {}
    monkeypatch.setattr(current_state, "write_proofs", write_proofs)
    resumed = executor().run(
        **args, **hooks, model_call_state=calls,
        resume_state={"work_state": rows, "frontier": control["frontier_policy"],
                      "diagnostics": control["diagnostics"]},
    )
    assert len(requests) == 2
    assert len(resumed.graph.properties) == 3
    saved = current_state.restore_work(store, run, run.run_fingerprint).work_state
    assert len(saved["applied_model_results"]) == 3


@pytest.mark.parametrize("fixed", [True, False])
def test_finalizer_reproposal_resumes_same_member_and_publishes_a_new_owned_version(
    tmp_path, monkeypatch, current_run, fixed,
):
    def repair_predicate(answer, view, _number):
        if not fixed or view["stage"] != "discovery":
            return
        for member in view["members"]:
            if member.get("recovery_kind") == "reproposal":
                result = next(value["result"] for value in answer["members"]
                              if value["task_id"] == member["task_id"])
                result["properties"][0]["predicate_iri"] = member["predicate_iri"]

    args, executor, requests, hooks = setup_batch(
        tmp_path, monkeypatch, current_run, failure="predicate", transform=repair_predicate,
    )
    store, run, _token = current_run
    publish = hooks["batch_hook"]
    published = []

    def pause_after_publication(batch):
        publish(batch)
        published.append(list(batch.member_outcomes))

    hooks["batch_hook"] = pause_after_publication
    paused = executor(progress_hook=lambda _stage: not published).run(**args, **hooks)
    assert len(paused.graph.properties) == 2
    assert len(requests) == 2
    rows = current_state.restore_work(store, run, run.run_fingerprint).work_state
    control = rows["control"]["current"]
    calls = current_state.restore_calls(store, run, run.run_fingerprint)
    unit_id = control["active_unit_ref"]
    protocol = calls["protocols"][unit_id]
    task_id = next(task["task_id"] for task in protocol["work_unit"]["members"]
                   if task["predicate_iri"] == ATTRIBUTES[1][0])
    before = protocol["member_states"][task_id]
    assert before["recovery_kind"] == "reproposal" and before["recovery_used"]
    assert before["assertion_generation"] == 1
    previous_ref = protocol["outcome_refs"][task_id]
    resumed = executor().run(
        **args, **hooks, model_call_state=calls,
        resume_state={"work_state": rows, "frontier": control["frontier_policy"],
                      "diagnostics": control["diagnostics"]},
    )
    expected_calls = 4 if fixed else 3
    assert len(requests) == expected_calls
    assert len(resumed.graph.properties) == (3 if fixed else 2)
    latest = current_state.restore_calls(store, run, run.run_fingerprint)
    protocol = latest["protocols"][unit_id]
    after = protocol["member_states"][task_id]
    assert after["assertion_generation"] == (2 if fixed else 1)
    assert after["evidence_revision"] == before["evidence_revision"]
    assert after["recovery_used"] and after["recovery_kind"] == "none"
    assert protocol["outcome_refs"][task_id] != previous_ref
    outcome = current_state.load_protocol_record(
        store, run, unit_id, protocol["outcome_refs"][task_id], "outcome", member_task_id=task_id,
    )
    assert outcome["result_version"]["last_participating_request_attempt"] == expected_calls
    assert published[1:] == [[task_id]]
    # The failed member was excluded from the first semantic verification request.
    assert sorted(latest["lineage_calls"].values()) == sorted([2, 2, expected_calls - 1])


def test_exhausted_reproposal_keeps_last_paid_outcome_without_head_conflict(
    tmp_path, monkeypatch, current_run,
):
    args, executor, requests, hooks = setup_batch(
        tmp_path, monkeypatch, current_run, failure="predicate",
    )
    store, run, _token = current_run
    publish = hooks["batch_hook"]
    published = []

    def pause_after_publication(batch):
        publish(batch)
        published.append(True)

    hooks["batch_hook"] = pause_after_publication
    paused = executor(progress_hook=lambda _stage: not published).run(**args, **hooks)
    assert len(requests) == 2
    assert len(paused.graph.properties) == 2
    rows = current_state.restore_work(store, run, run.run_fingerprint).work_state
    control = rows["control"]["current"]
    calls = current_state.restore_calls(store, run, run.run_fingerprint)
    unit_id = control["active_unit_ref"]
    protocol = calls["protocols"][unit_id]
    task_id = next(task["task_id"] for task in protocol["work_unit"]["members"]
                   if task["predicate_iri"] == ATTRIBUTES[1][0])
    state = protocol["member_states"][task_id]
    assert state["last_participating_request_attempt"] == 1
    assert state["recovery_used"] and state["recovery_kind"] == "reproposal"
    previous_ref = protocol["outcome_refs"][task_id]

    result = executor(max_model_calls_per_record=1).run(
        **args, **hooks, model_call_state=calls,
        resume_state={"work_state": rows, "frontier": control["frontier_policy"],
                      "diagnostics": control["diagnostics"]},
    )
    assert len(requests) == 2
    assert len(result.graph.properties) == 2
    calls = current_state.restore_calls(store, run, run.run_fingerprint)
    protocol = calls["protocols"][unit_id]
    state = protocol["member_states"][task_id]
    assert state["recovery_used"] and state["recovery_kind"] == "none"
    assert protocol["outcome_refs"][task_id] == previous_ref
    outcome = current_state.load_protocol_record(
        store, run, unit_id, protocol["outcome_refs"][task_id], "outcome",
        member_task_id=task_id,
    )
    assert outcome["result_version"]["last_participating_request_attempt"] == 1
    assert outcome["value"]["reason_code"] != "model_call_budget_exhausted"


@pytest.mark.parametrize("reply", ["fixed", "empty", "unchanged"])
def test_empty_target_reproposal_consumes_its_saved_response_after_cold_resume(
    tmp_path, monkeypatch, current_run, reply,
):
    def change_proposal(answer, view, number):
        if view["stage"] != "discovery":
            return
        for member, result in zip(view["members"], answer["members"]):
            if member.get("recovery_kind") != "reproposal":
                continue
            if reply == "fixed":
                result["result"]["properties"][0]["predicate_iri"] = member["predicate_iri"]
            elif reply == "empty":
                result["result"]["properties"] = []

    args, executor, requests, hooks = setup_batch(
        tmp_path, monkeypatch, current_run, failure="predicate", transform=change_proposal,
    )
    store, run, _token = current_run
    persist = hooks["model_call_hook"]
    stop = False

    def pause_after_reproposal_response(state):
        nonlocal stop
        persist(state)
        if any(row["field"] == "model_turn" and row["value"]["attempt"] == 3
               for row in state.get("result_changes", {}).values()):
            stop = True

    hooks["model_call_hook"] = pause_after_reproposal_response
    paused = executor(progress_hook=lambda _stage: not stop).run(**args, **hooks)
    assert stop and len(requests) == 3
    assert len(paused.graph.properties) == 2
    rows = current_state.restore_work(store, run, run.run_fingerprint).work_state
    control = rows["control"]["current"]
    calls = current_state.restore_calls(store, run, run.run_fingerprint)
    unit_id = control["active_unit_ref"]
    protocol = calls["protocols"][unit_id]
    task_id = protocol["stage_member_ids"][0]
    assert protocol["stage"] == "discovery"
    assert protocol["member_states"][task_id]["assertion_generation"] == 1
    old_discovery = protocol["discovery_refs"][task_id]
    old_outcome = protocol["outcome_refs"][task_id]
    hooks["model_call_hook"] = persist
    resumed = executor().run(
        **args, **hooks, model_call_state=calls,
        resume_state={"work_state": rows, "frontier": control["frontier_policy"],
                      "diagnostics": control["diagnostics"]},
    )
    expected_calls = 4 if reply == "fixed" else 3
    assert len(requests) == expected_calls
    assert len(resumed.graph.properties) == (3 if reply == "fixed" else 2)
    latest = current_state.restore_calls(store, run, run.run_fingerprint)
    protocol = latest["protocols"][unit_id]
    state = protocol["member_states"][task_id]
    assert state["assertion_generation"] == (1 if reply == "unchanged" else 2)
    assert state["recovery_used"] and state["recovery_kind"] == "none"
    assert protocol["outcome_refs"][task_id] != old_outcome
    assert (protocol["discovery_refs"][task_id] == old_discovery) == (reply == "unchanged")
    outcome = current_state.load_protocol_record(
        store, run, unit_id, protocol["outcome_refs"][task_id], "outcome", member_task_id=task_id,
    )
    assert outcome["value"]["complete"] == (reply != "unchanged")
    if reply == "empty":
        assert outcome["value"]["semantic_outcome"] == "not_checked"
        assert outcome["value"]["reason_code"] == "record_no_claims"
    assert outcome["result_version"]["last_participating_request_attempt"] == expected_calls
    assert latest["reservation_sequence"] == expected_calls
    assert sorted(latest["lineage_calls"].values()) == sorted([2, 2, expected_calls - 1])
    assert all(member["verification_input"]["targets"]
               for request in requests if request["stage"] == "verification"
               for member in request["members"])


@pytest.mark.parametrize("tamper", ["unconfirmed_evidence", "unbound_generation"])
def test_persisted_member_versions_require_their_source_or_new_claim_transition(
    tmp_path, monkeypatch, current_run, tamper,
):
    args, executor, _requests, hooks = setup_batch(tmp_path, monkeypatch, current_run)
    executor().run(**args, **hooks)
    store, run, token = current_run
    before = current_state.restore_calls(store, run, run.run_fingerprint)
    unit_id, protocol = next(iter(before["protocols"].items()))
    changed = deepcopy(protocol)
    member = next(iter(changed["member_states"].values()))
    if tamper == "unconfirmed_evidence":
        member["evidence_revision"] += 1
        member["context_hash"] = "c" * 64
        member["evidence_hash"] = "e" * 64
    else:
        member["assertion_generation"] += 1
    with pytest.raises((HeadConflict, ValueError)):
        current_state.persist_calls(store, run, token, run.run_fingerprint, {
            "current_calls": 1, "version": 3,
            "recognition_run_id": str(run.recognition_run_id),
            "run_fingerprint": run.run_fingerprint,
            "lineage_calls": {}, "reservations": [],
            "reservation_sequence": before["reservation_sequence"],
            "protocols": {json.dumps(unit_id): {
                "key": unit_id, "value": changed, "position": 0,
            }},
        })
    assert current_state.restore_calls(store, run, run.run_fingerprint) == before


@pytest.mark.parametrize("pause_at", ["model_turn", "tool_result"])
@pytest.mark.parametrize("new_evidence", [False, True])
def test_confirmed_supplement_authorization_resumes_only_its_member_at_new_evidence_revision(
    tmp_path, monkeypatch, current_run, pause_at, new_evidence,
):
    from types import SimpleNamespace

    from app.services.extraction.ontology_guided import tool_runtime
    from app.services.llm import local_client

    def request_more_evidence(answer, view, number):
        if view["stage"] != "verification" or number != 2:
            return
        task_id = next(member["task_id"] for member in view["members"]
                       if member["predicate_iri"] == ATTRIBUTES[1][0])
        result = next(member["result"] for member in answer["members"]
                      if member["task_id"] == task_id)
        for target in result["verifications"]:
            for facet in target["facets"]:
                if facet["name"] == "value":
                    facet.update(verdict="undetermined", support=[], reason="需补充规格原文")

    args, executor, requests, hooks = setup_batch(
        tmp_path, monkeypatch, current_run, transform=request_more_evidence,
        extra_lines=("补充：规格大号适用于本报告。",),
    )
    index = RecordIndex(args["ir"])
    supplement = index.records[1]
    original_transport = local_client.responses_create

    def retrieval_transport(client, **kwargs):
        if (kwargs.get("tool_choice") != "required"
                or {tool["name"] for tool in kwargs.get("tools", [])} != {"retrieve_evidence"}):
            return original_transport(client, **kwargs)
        assert "当前成员需要补充" in kwargs["instructions"]
        view = json.loads(kwargs["input_items"][0]["content"][0]["text"])
        requests.append(deepcopy(view))
        assert len(view["members"]) == 1
        member = view["members"][0]
        assert member["predicate_iri"] == ATTRIBUTES[1][0]
        return ResponseTurn(
            response_id=f"batch-response-{len(requests)}", response_status="completed",
            output_items=[{
                "type": "function_call", "call_id": "supplement", "name": "retrieve_evidence",
                "arguments": json.dumps({
                    "member_task_id": member["task_id"], "subject_id": member["subject_ref"]["id"],
                    "predicate_iri": member["predicate_iri"], "missing_facets": ["value"],
                }),
            }], incomplete_details=None, error=None, usage={"input_tokens": 10, "output_tokens": 5},
        )

    monkeypatch.setattr(local_client, "responses_create", retrieval_transport)
    monkeypatch.setattr(tool_runtime, "plan_slot", lambda *args, **kwargs: SimpleNamespace(
        records=[SimpleNamespace(record_id=supplement.record_id)] if new_evidence else [],
    ))
    store, run, _token = current_run
    persist = hooks["model_call_hook"]
    pause_now = False

    def pause_after_retrieval(state):
        nonlocal pause_now
        persist(state)
        for row in state.get("result_changes", {}).values():
            if row["field"] == pause_at and row["value"]["attempt"] == 3:
                pause_now = True

    hooks["model_call_hook"] = pause_after_retrieval
    paused = executor(max_tasks=3, progress_hook=lambda _stage: not pause_now).run(**args, **hooks)
    assert len(requests) == 3
    assert len(paused.graph.properties) == 2
    rows = current_state.restore_work(store, run, run.run_fingerprint).work_state
    control = rows["control"]["current"]
    calls = current_state.restore_calls(store, run, run.run_fingerprint)
    unit_id = control["active_unit_ref"]
    protocol = calls["protocols"][unit_id]
    task_id = next(task["task_id"] for task in protocol["work_unit"]["members"]
                   if task["predicate_iri"] == ATTRIBUTES[1][0])
    source_state = protocol["member_states"][task_id]
    materialized = new_evidence and pause_at == "tool_result"
    assert source_state["evidence_revision"] == (2 if materialized else 1)
    assert source_state["assertion_generation"] == 1
    if materialized:
        assert supplement.record_id in source_state["context_authorization"]["record_ids"]
    else:
        assert source_state["context_authorization"] is None
    assert (task_id not in protocol["verification_refs"]) == materialized
    siblings = {key: value for key, value in protocol["member_states"].items() if key != task_id}
    assert all(member["evidence_revision"] == 1 for member in siblings.values())
    assert all(member["context_authorization"] is None for member in siblings.values())
    hooks["model_call_hook"] = persist
    resumed = executor(max_tasks=3).run(
        **args, **hooks, model_call_state=calls,
        resume_state={"work_state": rows, "frontier": control["frontier_policy"],
                      "diagnostics": control["diagnostics"]},
    )
    expected_calls = 4 if new_evidence else 3
    assert len(requests) == expected_calls
    assert len(resumed.graph.properties) == (3 if new_evidence else 2)
    latest = current_state.restore_calls(store, run, run.run_fingerprint)
    protocol = latest["protocols"][unit_id]
    assert {key: value for key, value in protocol["member_states"].items()
            if key != task_id} == siblings
    assert sorted(latest["lineage_calls"].values()) == [2, 2, expected_calls]
    saved = current_state.load_protocol_record(
        store, run, unit_id, protocol["outcome_refs"][task_id], "outcome", member_task_id=task_id,
    )
    assert saved["result_version"]["evidence_revision"] == (2 if new_evidence else 1)
    assert saved["result_version"]["assertion_generation"] == 1
    assert protocol["member_states"][task_id]["tool_calls_used"] == 1
    assert protocol["member_states"][task_id]["recovery_kind"] == "none"


def test_repeated_call_id_from_another_attempt_does_not_complete_pending_member_tool(current_run):
    from app.services.extraction.ontology_guided.current_work import protocol_result_ref
    from app.services.extraction.ontology_guided.tool_runtime import _error
    from tests.test_extraction.test_batch_current_state import (
        batch_protocol,
        confirm,
        persist,
        reserve,
    )

    protocol = batch_protocol()
    task_id = protocol["stage_member_ids"][0]
    persist(current_run, protocol)
    call = {
        "type": "function_call", "call_id": "reused-call-id", "name": "inspect_evidence",
        "arguments": json.dumps({"member_task_id": task_id, "evidence_ids": ["source"]}),
    }
    reserve(current_run, protocol, tools=["inspect_evidence"])
    confirm(current_run, protocol, output=[call])
    protocol["member_states"][task_id]["tool_calls_used"] = 1
    persist(current_run, protocol)
    result = {
        "attempt": 1, "call_id": call["call_id"], "stage_group_seq": 1,
        "member_task_id": task_id,
        "result": _error("reference_outside_scope").model_dump(mode="json"),
    }
    reference = protocol_result_ref(protocol["lineage_id"], "tool_result", result)
    protocol["completed_tool_results"].append(reference)
    persist(current_run, protocol, results={reference: {
        "lineage_id": protocol["lineage_id"], "field": "tool_result", "value": result,
    }})
    reserve(current_run, protocol, tools=["inspect_evidence"])
    confirm(current_run, protocol, output=[call])
    protocol.update(
        stage="verification", stage_group_seq=2, turn_refs=[], completed_tool_results=[],
        active_instructions="Independent verification", stage_input_items=[],
    )
    with pytest.raises(HeadConflict, match="unpaired tools"):
        persist(current_run, protocol)


def test_same_physical_entity_is_canonical_across_independently_proved_relations(
    tmp_path, monkeypatch, current_run,
):
    args, executor, requests, hooks = setup_batch(
        tmp_path, monkeypatch, current_run, relations=True,
    )
    result = executor().run(**args, **hooks)
    assert len(result.graph.edges) == 2, result.diagnostics
    assert len([node for node in result.graph.nodes if not node.root]) == 1
    assert len({edge.object_ref.id for edge in result.graph.edges}) == 1
    assert {edge.predicate_iri for edge in result.graph.edges} == {iri for iri, _label in RELATIONS}
    assert len({edge.proof_ref.id for edge in result.graph.edges}) == 2
    assert len(requests) == 2
