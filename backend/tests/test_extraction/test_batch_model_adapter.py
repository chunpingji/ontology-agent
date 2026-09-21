"""Batched transport exercises real member freeze, verification and final gates."""

import json
from copy import deepcopy
from dataclasses import replace

import pytest

from app.services.extraction.ontology_guided.claim_protocol import compile_schema_card
from app.services.extraction.ontology_guided.contracts import SlotSpec, TraversalScope
from app.services.extraction.ontology_guided.current_work import (
    call_request_key,
    protocol_result_ref,
    validate_protocol_result,
    validate_tool_protocol,
)
from app.services.extraction.ontology_guided.model_reference_projection import (
    project_reference_payload,
)
from app.services.extraction.ontology_guided.recognition_batch import (
    MemberContext,
    RecognitionBatchContext,
    RecognitionBatchPolicy,
    RecognitionWorkUnit,
)
from app.services.llm.local_client import ResponseTurn, StructuredModelError
from tests.test_extraction.test_tool_engine_adapter import setup_adapter

pytest_plugins = ["tests.test_extraction.test_tool_engine_freeze"]


@pytest.fixture
def source(tool_source):
    return tool_source


def batch_setup(source, monkeypatch, *, size=3, relation=False, stop_at=None, transform=None):
    members, tasks, proposals = [], [], {}
    for position in range(size):
        value = dict(source)
        if relation and position < int(relation):
            value["proposal"] = deepcopy(source["proposal"])
            if position:
                predicate = source["predicate"].model_copy(update={"iri": f"urn:links-{position}"})
                value["predicate"], value["options"] = predicate, source["task_context"](predicate)
                value["proposal"]["relations"][0]["predicate_iri"] = predicate.iri
        else:
            predicate = SlotSpec(
                iri=f"urn:quantity-{position}", label=f"数量{position}",
                declared_by=["urn:Source"],
                datatype_iris=["http://www.w3.org/2001/XMLSchema#decimal"], canonical_unit="g",
            )
            value["predicate"], value["options"] = predicate, source["task_context"](predicate)
            quote = source["quote"]
            value["proposal"] = {
                "entities": [], "relations": [], "external_links": [], "observations": [],
                "properties": [{
                    "local_id": "value", "subject_id": "subject-server-id",
                    "predicate_iri": predicate.iri, "value_quote": quote("5 mg"),
                    "field_support": [quote("数量")], "unit_support": [quote("mg")],
                    "qualifiers": {"polarity": "affirmed", "modality": "asserted",
                                   "condition_support": [], "scope_qualifiers": []},
                    "bridge_kind": "explicit_assertion", "bridge_ref_ids": [],
                }],
            }
        adapter, task, context, _, menu, _, _ = setup_adapter(value, monkeypatch)
        card = compile_schema_card(menu, predicate_iri=task.predicate_iri,
                                   profile=adapter.profile,
                                   scope=task.scope or TraversalScope.create())
        members.append(MemberContext(task_id=task.task_id, context=context, card=card))
        tasks.append(task)
        proposals[task.task_id] = value["proposal"]
    unit = RecognitionWorkUnit.create(
        run_fingerprint="test-run", policy=RecognitionBatchPolicy(max_members=size), members=tasks,
    )
    context = RecognitionBatchContext(
        work_unit_id=unit.work_unit_id, members=members,
        remaining_model_calls_by_member={task.task_id: 4 for task in tasks},
    )
    adapter.max_output_tokens = 20000
    stored = {"protocol": {}, "results": {}, "reservations": []}
    requests = []

    def checkpoint(value):
        value = deepcopy(value)
        changes = value.pop("result_changes", {})
        validate_tool_protocol(value)
        for wrapper in changes.values():
            validate_protocol_result(wrapper["field"], wrapper["value"])
        stored["protocol"] = value
        stored["results"].update(changes)
        if stop_at and any(row["field"] == stop_at for row in changes.values()):
            raise RuntimeError("pause after durable commit")

    def reserve(stage, ordinal):
        pending = stored["protocol"]["pending_request"]
        assert pending["attempt"] == ordinal
        stored["reservations"].append((stage, ordinal, pending["member_task_ids"]))

    context.bind_protocol_hook(checkpoint)
    context.bind_model_call_hook(reserve)

    def transport(client, **kwargs):
        requests.append(deepcopy(kwargs))
        view = json.loads(kwargs["input_items"][0]["content"][0]["text"])
        if kwargs.get("tool_choice") == "required":
            required = json.loads(kwargs["instructions"].splitlines()[-1])
            output = [{
                "type": "function_call", "call_id": f"call-{len(requests)}-{i}",
                "name": "validate_graph", "arguments": json.dumps({
                    **item, "shape_profile_id": required["shape_profile_id"],
                }),
            } for i, item in enumerate(required["required_relation_checks"])]
        else:
            answer = {"members": []}
            for member in view["members"]:
                wire_proposals = project_reference_payload(proposals)
                result = deepcopy(wire_proposals[member["task_id"]])
                if view["stage"] == "verification":
                    result = {"verifications": [{
                        "target_id": target["target_id"], "content_hash": target["content_hash"],
                        "facets": [{"name": facet, "verdict": "supported",
                                    "support": [project_reference_payload(
                                        source["quote"](source["source_unit"].text),
                                    )],
                                    "counterevidence_support": [], "reason": "原文支持"}
                                   for facet in target["required_facets"]],
                    } for target in member["verification_input"]["targets"]]}
                answer["members"].append({"task_id": member["task_id"], "result": result})
            if transform:
                transform(answer, view, len(requests))
            output = [{"type": "message", "role": "assistant", "content": [
                {"type": "output_text", "text": json.dumps(answer)},
            ]}]
        return ResponseTurn(f"response-{len(requests)}", output, "completed", None, None,
                            {"input_tokens": 10, "output_tokens": 5})

    monkeypatch.setattr("app.services.llm.local_client.responses_create", transport)
    return adapter, unit, context, menu, stored, requests


def finalize(adapter, unit, context):
    nodes = {context.members[0].context.tool_inputs["subject_node"]["entity_id"]:
             context.members[0].context.tool_inputs["subject_node"]}
    outcomes = []
    for task in unit.members:
        if task.task_id not in context.protocol_state["verification_refs"]:
            continue
        outcome = adapter.finalize_reviewed_member(
            task, context, current_entities=nodes, current_resolutions=[],
        )
        outcomes.append(outcome)
        for node in outcome.nodes:
            nodes[node.entity_id] = node
    return outcomes


def test_three_properties_share_two_requests_and_independent_results(source, monkeypatch):
    baseline = 0
    for _ in range(3):
        single, unit, context, menu, _, requests = batch_setup(source, monkeypatch, size=1)
        single.inspect_work_unit(unit, context, menu)
        baseline += len(requests)
    assert baseline == 6
    adapter, unit, context, menu, stored, requests = batch_setup(source, monkeypatch)
    assert adapter.work_unit_fits(unit, context, menu)
    assert context.protocol_state == {}
    result = adapter.inspect_work_unit(unit, context, menu)
    assert not result.member_errors
    assert len(result.member_result_refs) == 3
    assert len(requests) == len(stored["reservations"]) == 2
    outcomes = finalize(adapter, unit, context)
    assert all(outcome.complete and len(outcome.properties) == 1 for outcome in outcomes)
    assert all(outcome.model_calls == 0 for outcome in outcomes)
    assert len(requests) == 2  # Finalization performs no model operation.
    view = json.loads(requests[0]["input_items"][0]["content"][0]["text"])
    assert len(view["evidence_units"]) < sum(len(m["evidence_refs"]) for m in view["members"])


@pytest.mark.parametrize("stage", ["discovery", "verification"])
@pytest.mark.parametrize("reason", ["model_request_failed", "model_parse_error", "model_timeout"])
def test_unconfirmed_transport_failure_preserves_pending_request_without_splitting(
    source, monkeypatch, stage, reason,
):
    def fail_transport(answer, view, number):
        if view["stage"] == stage:
            raise StructuredModelError(reason)

    adapter, unit, context, menu, stored, requests = batch_setup(
        source, monkeypatch, transform=fail_transport,
    )
    with pytest.raises(StructuredModelError, match=f"^{reason}$"):
        adapter.inspect_work_unit(unit, context, menu)
    paid = 1 if stage == "discovery" else 2
    assert len(requests) == len(stored["reservations"]) == paid
    protocol = stored["protocol"]
    assert protocol["pending_request"]["stage"] == stage
    assert protocol["pending_request"]["attempt"] == paid
    assert protocol["pending_request"]["member_task_ids"] == [t.task_id for t in unit.members]
    assert protocol["completed_attempts"] == ([] if paid == 1 else [1])
    assert not protocol["verification_refs"] and not protocol["outcome_refs"]
    before = deepcopy(stored)
    context.protocol_state = deepcopy(protocol)
    context.protocol_results = deepcopy(stored["results"])
    with pytest.raises(StructuredModelError, match="^model_request_outcome_unknown$"):
        adapter.inspect_work_unit(unit, context, menu)
    assert len(requests) == paid
    assert stored == before


@pytest.mark.parametrize("size", [1, 2, 4])
@pytest.mark.parametrize("observation", [None, "missing", "unknown", "ambiguous", "unbound"])
def test_empty_batch_uses_only_discovery_and_keeps_single_task_semantics(
    source, monkeypatch, size, observation,
):
    def empty_discovery(answer, view, number):
        if view["stage"] == "discovery":
            for member in answer["members"]:
                member["result"] = {key: [] for key in (
                    "entities", "properties", "relations", "external_links", "observations",
                )}
                if observation:
                    member["result"]["observations"] = [{
                        "subject_id": None, "predicate_iri": None,
                        "quote": source["quote"]("数量"), "kind": observation,
                        "reason": "当前记录的观察",
                    }]

    adapter, unit, context, menu, stored, requests = batch_setup(
        source, monkeypatch, size=size, transform=empty_discovery,
    )
    reviewed = adapter.inspect_work_unit(unit, context, menu)
    assert not reviewed.member_errors
    assert len(reviewed.member_result_refs) == size
    assert len(requests) == len(stored["reservations"]) == 1
    assert stored["reservations"][0][0] == "discovery"
    assert not any(row["field"] == "tool_result" for row in stored["results"].values())
    assert context.protocol_state["stage"] == "discovery"
    for task in unit.members:
        ref = context.protocol_state["verification_refs"][task.task_id]
        assert stored["results"][ref]["value"]["targets"] == []
        assert stored["results"][ref]["result_version"]["last_participating_request_attempt"] == 1
    outcomes = finalize(adapter, unit, context)
    complete = observation in (None, "missing")
    assert all(outcome.complete is complete for outcome in outcomes)
    assert all(outcome.semantic_outcome == ("not_checked" if complete else "undetermined")
               for outcome in outcomes)
    assert all(not outcome.properties and not outcome.relationship_groups for outcome in outcomes)


def test_invalid_discovery_is_not_confirmed_when_empty_verification_skips_model(
    source, monkeypatch,
):
    def invalid_quote_context(answer, view, number):
        if view["stage"] == "discovery":
            for member in answer["members"]:
                member["result"]["properties"][0]["value_quote"]["context_text"] = "数量"

    adapter, unit, context, menu, stored, requests = batch_setup(
        source, monkeypatch, size=2, transform=invalid_quote_context,
    )
    reviewed = adapter.inspect_work_unit(unit, context, menu)
    assert not reviewed.member_errors
    assert len(requests) == len(stored["reservations"]) == 1
    discoveries = [row["value"] for row in stored["results"].values()
                   if row["field"] == "discovery"]
    assert all("source_excerpt_mismatch" in value["claim_issues"]["value"]
               for value in discoveries)
    outcomes = finalize(adapter, unit, context)
    assert all(not outcome.complete and not outcome.properties for outcome in outcomes)


@pytest.mark.parametrize("relation", [False, True])
def test_mixed_batch_verifies_only_nonempty_members(source, monkeypatch, relation):
    def empty_last_member(answer, view, number):
        if view["stage"] == "discovery":
            answer["members"][-1]["result"] = {key: [] for key in (
                "entities", "properties", "relations", "external_links", "observations",
            )}

    adapter, unit, context, menu, stored, requests = batch_setup(
        source, monkeypatch, size=2, relation=relation, transform=empty_last_member,
    )
    reviewed = adapter.inspect_work_unit(unit, context, menu)
    assert not reviewed.member_errors and len(reviewed.member_result_refs) == 2
    assert len(requests) == 2
    for request in requests[1:]:
        view = json.loads(request["input_items"][0]["content"][0]["text"])
        assert [m["task_id"] for m in view["members"]] == project_reference_payload(
            {"task_ids": [unit.members[0].task_id]},
        )["task_ids"]
        assert all(m["verification_input"]["targets"] for m in view["members"])
        for tool in request["tools"] or []:
            assert tool["parameters"]["properties"]["member_task_id"]["enum"] == [
                view["members"][0]["task_id"],
            ]
    assert sum(unit.members[1].task_id in ids for _, _, ids in stored["reservations"]) == 1
    outcomes = finalize(adapter, unit, context)
    assert outcomes[0].complete and (outcomes[0].relationship_groups if relation
                                    else outcomes[0].properties)
    assert outcomes[1].complete and outcomes[1].reason_code == "record_no_claims"


def test_invalid_entity_dependency_closes_without_empty_relation_verifier(source, monkeypatch):
    def invalid_mentions(answer, view, number):
        if view["stage"] == "discovery":
            for member in answer["members"]:
                for entity in member["result"]["entities"]:
                    entity["mentions"][0]["context_text"] = "数量"

    adapter, unit, context, menu, stored, requests = batch_setup(
        source, monkeypatch, size=1, relation=True, transform=invalid_mentions,
    )
    reviewed = adapter.inspect_work_unit(unit, context, menu)
    assert not reviewed.member_errors and len(requests) == len(stored["reservations"]) == 1
    frozen = next(row["value"] for row in stored["results"].values()
                  if row["field"] == "discovery")
    assert set(frozen["claim_issues"]) == {"b", "c", "r"}
    assert frozen["claim_issues"]["r"] == ["entity_dependency_invalid"]
    outcomes = finalize(adapter, unit, context)
    assert not outcomes[0].complete and outcomes[0].semantic_outcome == "undetermined"
    assert not outcomes[0].nodes and not outcomes[0].relationship_groups


@pytest.mark.parametrize("pause", ["discovery", "verification"])
def test_empty_members_finish_cold_with_zero_remaining_model_calls(source, monkeypatch, pause):
    def empty_discovery(answer, view, number):
        if view["stage"] == "discovery":
            for member in answer["members"]:
                member["result"] = {key: [] for key in (
                    "entities", "properties", "relations", "external_links", "observations",
                )}

    adapter, unit, context, menu, stored, requests = batch_setup(
        source, monkeypatch, size=2, transform=empty_discovery, stop_at=pause,
    )
    with pytest.raises(RuntimeError, match="pause after durable commit"):
        adapter.inspect_work_unit(unit, context, menu)
    assert len(requests) == 1
    original_results = deepcopy(stored["results"])
    resumed = context.model_copy(deep=True)
    resumed.protocol_state = deepcopy(stored["protocol"])
    resumed.protocol_results = deepcopy(stored["results"])
    resumed.remaining_model_calls_by_member = dict.fromkeys([t.task_id for t in unit.members], 0)

    def checkpoint(value):
        value = deepcopy(value)
        changes = value.pop("result_changes", {})
        validate_tool_protocol(value)
        stored["protocol"] = value
        stored["results"].update(changes)

    resumed.bind_protocol_hook(checkpoint)
    reviewed = adapter.inspect_work_unit(unit, resumed, menu)
    assert not reviewed.member_errors and len(reviewed.member_result_refs) == 2
    assert len(requests) == len(stored["reservations"]) == 1
    assert all(stored["results"][key] == value for key, value in original_results.items())
    assert all(outcome.complete for outcome in finalize(adapter, unit, resumed))
    saved = deepcopy(stored)
    adapter.inspect_work_unit(unit, resumed, menu)
    finalize(adapter, unit, resumed)
    assert stored == saved


def test_empty_member_cannot_reach_model_request_builder(source, monkeypatch):
    from app.services.extraction.ontology_guided.batch_model_adapter import BatchRecognitionRun

    def empty_discovery(answer, view, number):
        if view["stage"] == "discovery":
            answer["members"][0]["result"]["properties"] = []

    adapter, unit, context, menu, _, requests = batch_setup(
        source, monkeypatch, size=2, transform=empty_discovery,
    )
    adapter.inspect_work_unit(unit, context, menu)
    run = BatchRecognitionRun(adapter, unit, context, menu)
    run.prepare()
    for task in unit.members:
        run.verification(task.task_id)
    with pytest.raises(StructuredModelError, match="^verification_targets_empty$"):
        run.request([task.task_id for task in unit.members], "verification", [], [])
    assert len(requests) == 2


@pytest.mark.parametrize("saved", ["prepared", "pending", "tool", "answer"])
def test_legacy_empty_verification_resumes_without_new_model_request(source, monkeypatch, saved):
    from app.services.extraction.ontology_guided.batch_model_adapter import BatchRecognitionRun

    def empty_discovery(answer, view, number):
        if view["stage"] == "discovery":
            for member in answer["members"]:
                member["result"] = {key: [] for key in (
                    "entities", "properties", "relations", "external_links", "observations",
                )}

    adapter, unit, context, menu, stored, requests = batch_setup(
        source, monkeypatch, size=1, transform=empty_discovery, stop_at="discovery",
    )
    task_id = unit.members[0].task_id
    checkpoint = context._protocol_hook
    with pytest.raises(RuntimeError, match="pause after durable commit"):
        adapter.inspect_work_unit(unit, context, menu)
    resumed = context.model_copy(deep=True)
    resumed.protocol_state = deepcopy(stored["protocol"])
    resumed.protocol_results = deepcopy(stored["results"])
    resumed.bind_protocol_hook(checkpoint)
    run = BatchRecognitionRun(adapter, unit, resumed, menu)
    run.prepare()
    run.verification(task_id)
    # Construct the exact old stage/receipt boundary; new code cannot send this request.
    run.switch("verification", [task_id])
    protocol = deepcopy(resumed.protocol_state)
    protocol["stage_input_items"] = run.initial_items([task_id], "verification")
    run.save(protocol)
    if saved != "prepared":
        protocol["request_attempt"] = 2
        protocol["member_states"][task_id].update(
            last_participating_request_attempt=2,
            last_stage_group_seq=protocol["stage_group_seq"],
        )
        protocol["pending_request"] = {
            "attempt": 2, "stage": "verification",
            "stage_group_seq": protocol["stage_group_seq"], "member_task_ids": [task_id],
            "request_hash": "a" * 64, "allowed_tool_names": ["resolve_source_anchor"],
            "reservation_key": call_request_key({"lineage_id": unit.work_unit_id,
                                                  "protocol_attempt": 2}),
        }
        run.save(protocol)
        resumed.before_model_call("verification", 2)
        if saved != "pending":
            output = [{"type": "function_call", "call_id": "saved-empty-target-tool",
                       "name": "resolve_source_anchor", "arguments": json.dumps({
                           "member_task_id": task_id,
                           "evidence_id": source["source_unit"].evidence_id,
                           "quote": "5 mg", "context_text": None,
                       })}] if saved == "tool" else [{
                "type": "message", "role": "assistant", "content": [{
                    "type": "output_text", "text": json.dumps({"members": [{
                        "task_id": task_id, "result": {"verifications": []},
                    }]}),
                }],
            }]
            value = {
                "attempt": 2, "stage": "verification", "input_hash": "a" * 64,
                "stage_group_seq": protocol["stage_group_seq"], "member_task_ids": [task_id],
                "allowed_tool_names": ["resolve_source_anchor"],
                **ResponseTurn("legacy-response", output, "completed", None, None, None).__dict__,
            }
            protocol["turn_refs"] = [protocol_result_ref(unit.work_unit_id, "model_turn", value)]
            protocol["pending_request"] = None
            protocol["completed_attempts"].append(2)
            run.save(protocol, field="model_turn", value=value)
    resumed.remaining_model_calls_by_member = {task_id: 0}
    original_results = deepcopy(stored["results"])
    if saved == "pending":
        before = deepcopy(stored)
        with pytest.raises(StructuredModelError, match="model_request_outcome_unknown"):
            adapter.inspect_work_unit(unit, resumed, menu)
        assert stored == before
        assert not resumed.protocol_state["verification_refs"]
        return
    reviewed = adapter.inspect_work_unit(unit, resumed, menu)
    assert not reviewed.member_errors
    assert len(requests) == 1  # No new request after loading the synthetic legacy receipt.
    assert len(stored["reservations"]) == (1 if saved == "prepared" else 2)
    assert all(stored["results"][key] == value for key, value in original_results.items())
    tools = [row["value"] for row in stored["results"].values() if row["field"] == "tool_result"]
    assert len(tools) == (1 if saved == "tool" else 0)
    if tools:
        assert tools[0]["result"]["status"] == "ok"
        assert tools[0]["call_id"] == "saved-empty-target-tool"
        assert resumed.protocol_state["member_states"][task_id]["tool_calls_used"] == 1
    assert all(outcome.complete for outcome in finalize(adapter, unit, resumed))


def test_legacy_mixed_group_pairs_relation_check_before_excluding_empty_member(source, monkeypatch):
    from app.services.extraction.ontology_guided.batch_model_adapter import BatchRecognitionRun
    from app.services.extraction.ontology_guided.tool_contracts import RELATION_PROFILE

    def empty_last(answer, view, number):
        if view["stage"] == "discovery":
            answer["members"][-1]["result"]["properties"] = []

    adapter, unit, context, menu, stored, requests = batch_setup(
        source, monkeypatch, size=2, relation=True, transform=empty_last,
    )
    checkpoint = context._protocol_hook

    def pause_before_verification(value):
        checkpoint(value)
        if len(value["discovery_refs"]) == 2:
            raise RuntimeError("pause before verification")

    context.bind_protocol_hook(pause_before_verification)
    with pytest.raises(RuntimeError, match="pause before verification"):
        adapter.inspect_work_unit(unit, context, menu)
    context.protocol_state = deepcopy(stored["protocol"])
    context.protocol_results = deepcopy(stored["results"])
    context.bind_protocol_hook(checkpoint)
    run = BatchRecognitionRun(adapter, unit, context, menu)
    run.prepare()
    relation_id, empty_id = [task.task_id for task in unit.members]
    ids = [relation_id, empty_id]
    for task_id in ids:
        run.verification(task_id)
    run.switch("verification", ids)
    protocol = deepcopy(context.protocol_state)
    protocol["stage_input_items"] = run.initial_items(ids, "verification")
    protocol["request_attempt"] = 2
    for state in protocol["member_states"].values():
        state.update(last_participating_request_attempt=2,
                     last_stage_group_seq=protocol["stage_group_seq"])
    protocol["pending_request"] = {
        "attempt": 2, "stage": "verification", "stage_group_seq": protocol["stage_group_seq"],
        "member_task_ids": ids, "request_hash": "a" * 64, "allowed_tool_names": ["validate_graph"],
        "reservation_key": call_request_key({"lineage_id": unit.work_unit_id,
                                              "protocol_attempt": 2}),
    }
    run.save(protocol)
    context.before_model_call("verification", 2)
    claim = next(target for target in run.verifications[relation_id].targets
                 if target.target_kind == "relation")
    value = {
        "attempt": 2, "stage": "verification", "stage_group_seq": protocol["stage_group_seq"],
        "member_task_ids": ids, "input_hash": "a" * 64, "allowed_tool_names": ["validate_graph"],
        **ResponseTurn("legacy-check", [{
            "type": "function_call", "call_id": "legacy-check", "name": "validate_graph",
            "arguments": json.dumps({"member_task_id": relation_id,
                                     "claim_id": claim.claim_ref.id,
                                     "shape_profile_id": RELATION_PROFILE}),
        }], "completed", None, None, None).__dict__,
    }
    protocol["turn_refs"] = [protocol_result_ref(unit.work_unit_id, "model_turn", value)]
    protocol["pending_request"] = None
    protocol["completed_attempts"].append(2)
    run.save(protocol, field="model_turn", value=value)
    saved_results = deepcopy(stored["results"])
    context.remaining_model_calls_by_member = {relation_id: 1, empty_id: 0}
    reviewed = adapter.inspect_work_unit(unit, context, menu)
    assert not reviewed.member_errors
    assert len(requests) == 2 and len(stored["reservations"]) == 3
    assert stored["reservations"][-1] == ("verification", 3, [relation_id])
    assert all(stored["results"][key] == row for key, row in saved_results.items())
    tool_results = [row["value"] for row in stored["results"].values()
                    if row["field"] == "tool_result"]
    assert len(tool_results) == 1 and tool_results[0]["result"]["status"] == "ok"
    assert tool_results[0]["call_id"] == "legacy-check"
    assert all(outcome.complete for outcome in finalize(adapter, unit, context))
    empty = context.protocol_state["member_states"][empty_id]
    assert empty["last_participating_request_attempt"] == 2
    assert empty["tool_calls_used"] == 0


def test_mixed_members_share_relation_check_round_without_leaking_tool_owner(source, monkeypatch):
    adapter, unit, context, menu, stored, requests = batch_setup(
        source, monkeypatch, size=2, relation=True,
    )
    result = adapter.inspect_work_unit(unit, context, menu)
    assert not result.member_errors and len(result.member_result_refs) == 2
    assert len(requests) == 2
    tools = [row["value"] for row in stored["results"].values() if row["field"] == "tool_result"]
    assert len(tools) == 1 and tools[0]["member_task_id"] == unit.members[0].task_id
    assert context.protocol_state["member_states"][unit.members[1].task_id]["tool_calls_used"] == 0
    outcomes = finalize(adapter, unit, context)
    assert len(outcomes[0].relationship_groups) == 1
    assert len(outcomes[1].properties) == 1


def test_two_relations_reuse_identical_canonical_nodes_after_sequential_finalize(
    source, monkeypatch,
):
    def assertions(answer, view, number):
        if view["stage"] != "discovery":
            return
        for member in answer["members"]:
            member["result"]["reference_bindings"] = []
            for relation in member["result"]["relations"]:
                relation["source_assertion"] = {
                    "subject_support": [source["quote"]("主体甲")],
                    "object_support": [
                        {"object_id": "b", "support": [source["quote"]("对象乙")]},
                        {"object_id": "c", "support": [source["quote"]("对象丙")]},
                    ],
                    "predicate_support": [source["quote"](source["source_unit"].text)],
                    "binding_ids": [],
                }

    adapter, unit, context, menu, _, requests = batch_setup(
        source, monkeypatch, size=2, relation=2, transform=assertions,
    )
    adapter.reference_resolution = True
    reviewed = adapter.inspect_work_unit(unit, context, menu)
    assert not reviewed.member_errors
    outcomes = finalize(adapter, unit, context)
    assert len(requests) == 2
    assert all(len(outcome.relationship_groups) == 1 for outcome in outcomes)
    first = {node.entity_id: node for node in outcomes[0].nodes}
    assert first
    assert all(first[node.entity_id] == node for node in outcomes[1].nodes)


def test_missing_member_does_not_erase_valid_siblings(source, monkeypatch):
    def omit(answer, view, number):
        if view["stage"] == "discovery":
            answer["members"] = answer["members"][:1] if number == 1 else []

    adapter, unit, context, menu, _, _ = batch_setup(source, monkeypatch, size=2, transform=omit)
    reviewed = adapter.inspect_work_unit(unit, context, menu)
    assert set(reviewed.member_result_refs) == {unit.members[0].task_id}
    assert reviewed.member_errors == {unit.members[1].task_id: "member_answer_missing"}
    assert unit.members[1].task_id not in context.protocol_state["discovery_refs"]


def test_truncated_verification_is_saved_then_split_without_rediscovery(source, monkeypatch):
    from app.services.llm import local_client

    adapter, unit, context, menu, stored, requests = batch_setup(source, monkeypatch, size=2)
    original = local_client.responses_create

    def truncate(client, **kwargs):
        response = original(client, **kwargs)
        if len(requests) == 2:
            return replace(response, response_status="incomplete",
                           incomplete_details={"reason": "max_output_tokens"})
        return response

    monkeypatch.setattr(local_client, "responses_create", truncate)
    reviewed = adapter.inspect_work_unit(unit, context, menu)
    assert not reviewed.member_errors and len(reviewed.member_result_refs) == 2
    assert len(requests) == 4
    stages = [json.loads(request["input_items"][0]["content"][0]["text"])["stage"]
              for request in requests]
    assert stages == ["discovery", "verification", "verification", "verification"]
    incomplete = [row["value"] for row in stored["results"].values()
                  if row["field"] == "model_turn" and row["value"]["attempt"] == 2]
    assert incomplete[0]["response_status"] == "incomplete"
    assert all(row["result_version"]["last_participating_request_attempt"] != 2
               for row in stored["results"].values() if row["field"] == "verification")


def test_stage_output_limits_reach_discovery_and_verification_transport(source, monkeypatch):
    adapter, unit, context, menu, _, requests = batch_setup(source, monkeypatch, size=2)
    adapter.stage_output_tokens = {"discovery": 8192, "verification": 16384}

    reviewed = adapter.inspect_work_unit(unit, context, menu)

    assert not reviewed.member_errors and len(reviewed.member_result_refs) == 2
    assert [request["max_output_tokens"] for request in requests] == [8192, 16384]


def test_estimated_output_splits_complete_members_before_reserving_verification(
    source, monkeypatch,
):
    from app.services.extraction.ontology_guided.batch_model_adapter import BatchRecognitionRun

    adapter, unit, context, menu, _, _ = batch_setup(source, monkeypatch, size=2)
    adapter.inspect_work_unit(unit, context, menu)
    run = BatchRecognitionRun(adapter, unit, context, menu)
    run.prepare()
    for task in unit.members:
        run.verification(task.task_id)
    maximum = max(run.estimate_verification_output([task.task_id]) for task in unit.members)
    assert run.estimate_verification_output([task.task_id for task in unit.members]) > maximum
    adapter, unit, context, menu, _, requests = batch_setup(source, monkeypatch, size=2)
    adapter.max_output_tokens = maximum
    reviewed = adapter.inspect_work_unit(unit, context, menu)
    assert not reviewed.member_errors and len(reviewed.member_result_refs) == 2
    assert len(requests) == 3
    assert all(len(json.loads(request["input_items"][0]["content"][0]["text"])["members"]) == 1
               for request in requests[1:])


def test_invalid_tool_route_and_sibling_claim_stay_isolated(source, monkeypatch):
    from app.services.extraction.ontology_guided.batch_model_adapter import BatchRecognitionRun
    from app.services.extraction.ontology_guided.tool_contracts import RELATION_PROFILE, ToolCall
    from app.services.extraction.ontology_guided.tool_runtime import dispatch_member_tool

    adapter, unit, context, menu, _, _ = batch_setup(source, monkeypatch, size=2, relation=True)
    adapter.inspect_work_unit(unit, context, menu)
    run = BatchRecognitionRun(adapter, unit, context, menu)
    run.prepare()
    for task in unit.members:
        run.verification(task.task_id)
    relationship, property_task = unit.members
    claim = next(target.claim_ref.id for target in run.verifications[relationship.task_id].targets
                 if target.target_kind == "relation")
    for owner in ("unknown-member", property_task.task_id):
        result = dispatch_member_tool(ToolCall(
            call_id="isolated", name="validate_graph", arguments_json=json.dumps({
                "member_task_id": owner, "claim_id": claim, "shape_profile_id": RELATION_PROFILE,
            }),
        ), run.contexts)
        assert result.status == "blocked" and result.data is None


@pytest.mark.parametrize("legacy", [False, True])
def test_empty_member_recovery_uses_reproposal_even_with_a_supplement_plan(
    source, monkeypatch, legacy,
):
    from types import SimpleNamespace

    from app.services.extraction.ontology_guided import evidence_work
    from app.services.extraction.ontology_guided.batch_model_adapter import BatchRecognitionRun

    def invalid_first(answer, view, number):
        if number == 1:
            answer["members"][0]["result"]["properties"][0]["value_quote"] = source["quote"]("6 mg")

    adapter, unit, context, menu, _, requests = batch_setup(
        source, monkeypatch, size=1, transform=invalid_first,
    )
    task_id = unit.members[0].task_id
    monkeypatch.setattr(evidence_work, "plan_evidence_recovery",
                        lambda *args, **kwargs: SimpleNamespace(action="supplement"))
    adapter.inspect_work_unit(unit, context, menu)
    assert len(requests) == 1
    context.remaining_model_calls_by_member = {task_id: 3}
    assert not finalize(adapter, unit, context)[0].complete
    assert context.protocol_state["member_states"][task_id]["recovery_kind"] == "reproposal"
    if legacy:
        saved = deepcopy(context.protocol_state)
        saved["member_states"][task_id]["recovery_kind"] = "evidence"
        BatchRecognitionRun(adapter, unit, context, menu).save(saved)
    reviewed = adapter.inspect_work_unit(unit, context, menu)
    assert not reviewed.member_errors
    assert len(requests) == 3
    assert json.loads(requests[1]["input_items"][0]["content"][0]["text"])["stage"] == "discovery"
    outcome = finalize(adapter, unit, context)[0]
    assert outcome.complete and len(outcome.properties) == 1
    state = context.protocol_state["member_states"][task_id]
    assert state["assertion_generation"] == 2
    assert state["evidence_revision"] == 1
    assert state["recovery_used"] and state["recovery_kind"] == "none"


def test_recovery_success_closes_pending_member_without_resetting_budget(source, monkeypatch):
    from types import SimpleNamespace

    from app.services.extraction.ontology_guided import evidence_work

    def change_reproposal(answer, view, number):
        if view["stage"] == "discovery" and number > 2:
            # A real changed source-grounded claim produces a new generation.
            answer["members"][0]["result"]["properties"][0]["value_quote"] = source["quote"]("5")

    adapter, unit, context, menu, _, requests = batch_setup(
        source, monkeypatch, size=1, transform=change_reproposal,
    )
    monkeypatch.setattr(evidence_work, "plan_evidence_recovery",
                        lambda *args, **kwargs: SimpleNamespace(action="reproposal"))
    adapter.inspect_work_unit(unit, context, menu)
    finalize(adapter, unit, context)
    assert adapter.work_unit_pending_recovery(context) == [unit.members[0].task_id]
    context.remaining_model_calls_by_member = {unit.members[0].task_id: 2}
    adapter.inspect_work_unit(unit, context, menu)
    context.remaining_model_calls_by_member = {unit.members[0].task_id: 0}
    finalize(adapter, unit, context)
    state = context.protocol_state["member_states"][unit.members[0].task_id]
    assert state["recovery_used"] and state["recovery_kind"] == "none"
    assert state["assertion_generation"] == 2
    assert not adapter.work_unit_pending_recovery(context)
    assert len(requests) == 4


@pytest.mark.parametrize("failure", ["json", "duplicate"])
def test_completed_invalid_outer_answer_can_start_smaller_independent_group(
    source, monkeypatch, failure,
):
    from app.services.llm import local_client

    adapter, unit, context, menu, _, requests = batch_setup(source, monkeypatch, size=2)
    original = local_client.responses_create

    def malformed(client, **kwargs):
        response = original(client, **kwargs)
        if len(requests) == 1:
            text = response.output_items[0]["content"][0]
            if failure == "json":
                text["text"] = "{invalid-json"
            else:
                answer = json.loads(text["text"])
                answer["members"].append(deepcopy(answer["members"][0]))
                text["text"] = json.dumps(answer)
        return response

    monkeypatch.setattr(local_client, "responses_create", malformed)
    reviewed = adapter.inspect_work_unit(unit, context, menu)
    assert not reviewed.member_errors and len(reviewed.member_result_refs) == 2
    assert len(requests) == 4
    assert len([row for row in context.protocol_results.values()
                if row["field"] == "discovery"]) == 2


@pytest.mark.parametrize("tool_limit", [1, 16])
def test_cold_resume_keeps_unconfirmed_tool_reservation_cost(source, monkeypatch, tool_limit):
    from app.services.llm import local_client

    adapter, unit, context, menu, stored, requests = batch_setup(source, monkeypatch, size=1)
    adapter.tool_limits = replace(adapter.tool_limits, max_calls_per_lineage=tool_limit)
    original = local_client.responses_create
    task_id = unit.members[0].task_id

    def with_tool(client, **kwargs):
        response = original(client, **kwargs)
        if len(requests) == 1:
            return replace(response, output_items=[{
                "type": "function_call", "call_id": "unconfirmed-tool",
                "name": "resolve_source_anchor",
                "arguments": json.dumps({"member_task_id": task_id,
                    "evidence_id": source["source_unit"].evidence_id,
                    "quote": "5 mg", "context_text": None}),
            }])
        return response

    checkpoint = context._protocol_hook

    def pause(value):
        checkpoint(value)
        if (value["member_states"][task_id]["tool_calls_used"] == 1
                and not value["completed_tool_results"]):
            raise RuntimeError("pause after tool reservation")

    context.bind_protocol_hook(pause)
    monkeypatch.setattr(local_client, "responses_create", with_tool)
    with pytest.raises(RuntimeError, match="pause after tool reservation"):
        adapter.inspect_work_unit(unit, context, menu)
    resumed = context.model_copy(deep=True)
    resumed.protocol_state, resumed.protocol_results = stored["protocol"], stored["results"]
    resumed.remaining_model_calls_by_member = {task_id: 3}
    resumed.bind_protocol_hook(checkpoint)
    reviewed = adapter.inspect_work_unit(unit, resumed, menu)
    assert not reviewed.member_errors
    assert resumed.protocol_state["member_states"][task_id]["tool_calls_used"] == min(2, tool_limit)
    results = [row["value"]["result"] for row in resumed.protocol_results.values()
               if row["field"] == "tool_result"]
    assert len(results) == 1
    assert results[0]["status"] == ("blocked" if tool_limit == 1 else "ok")


def test_unowned_tool_attempt_is_charged_to_all_participating_members(source, monkeypatch):
    from app.services.llm import local_client

    adapter, unit, context, menu, _, requests = batch_setup(source, monkeypatch, size=2)
    original = local_client.responses_create

    def with_unknown_owner(client, **kwargs):
        response = original(client, **kwargs)
        if len(requests) == 1:
            return replace(response, output_items=[{
                "type": "function_call", "call_id": "unknown-owner", "name": "inspect_evidence",
                "arguments": json.dumps({"member_task_id": "outside-unit",
                    "evidence_ids": [source["source_unit"].evidence_id]}),
            }])
        return response

    monkeypatch.setattr(local_client, "responses_create", with_unknown_owner)
    reviewed = adapter.inspect_work_unit(unit, context, menu)
    assert not reviewed.member_errors
    assert all(state["tool_calls_used"] == 1
               for state in context.protocol_state["member_states"].values())
    results = [row["value"] for row in context.protocol_results.values()
               if row["field"] == "tool_result"]
    assert len(results) == 1 and results[0]["member_task_id"] is None
    assert results[0]["result"]["status"] == "blocked"


@pytest.mark.parametrize("new_evidence", [False, True])
def test_supplement_changes_only_routed_member_authorization(source, monkeypatch, new_evidence):
    from types import SimpleNamespace

    from app.services.extraction.ontology_guided import evidence_work, tool_runtime
    from app.services.extraction.ontology_guided.contracts import MetadataSnapshot
    from app.services.llm import local_client

    adapter, unit, context, menu, _, requests = batch_setup(source, monkeypatch, size=2)
    first, sibling = unit.members
    index = adapter.index
    adapter.metadata = MetadataSnapshot(
        snapshot_id="metadata", analysis_id=index.ir.analysis_id,
        document_hash=index.ir.document_hash, structure_hash=index.ir.structure_hash,
        summary_version="v1", generation_source="structure_only", dependency_hash="a" * 64,
    )
    seen = {fragment.anchor.evidence_id for fragment in context.members[0].context.fragments}
    record = next(r for r in index.records
                  if any(source.evidence_id not in seen for source in r.source_units))
    monkeypatch.setattr(evidence_work, "plan_evidence_recovery", lambda task, *args, **kwargs:
                        SimpleNamespace(action="supplement" if task.task_id == first.task_id
                                        else "none"))
    monkeypatch.setattr(tool_runtime, "plan_slot", lambda *args, **kwargs: SimpleNamespace(
        records=[SimpleNamespace(record_id=record.record_id)] if new_evidence else [],
    ))
    original = local_client.responses_create

    def supplement(client, **kwargs):
        if "当前成员需要补充原文证据。" in kwargs["instructions"]:
            requests.append(deepcopy(kwargs))
            return ResponseTurn(f"response-{len(requests)}", [{
                "type": "function_call", "call_id": "supplement", "name": "retrieve_evidence",
                "arguments": json.dumps({"member_task_id": first.task_id,
                    "subject_id": first.subject.entity_id, "predicate_iri": first.predicate_iri,
                    "missing_facets": ["value"]}),
            }], "completed", None, None, {"input_tokens": 10, "output_tokens": 5})
        return original(client, **kwargs)

    monkeypatch.setattr(local_client, "responses_create", supplement)
    adapter.inspect_work_unit(unit, context, menu)
    context.remaining_model_calls_by_member = {task.task_id: 2 for task in unit.members}
    finalize(adapter, unit, context)
    sibling_before = deepcopy(context.protocol_state["member_states"][sibling.task_id])
    reviewed = adapter.inspect_work_unit(unit, context, menu)
    assert not reviewed.member_errors
    assert context.protocol_state["member_states"][sibling.task_id] == sibling_before
    assert context.protocol_state["member_states"][first.task_id]["evidence_revision"] == (
        2 if new_evidence else 1
    )
    assert len(requests) == (4 if new_evidence else 3)
    context.remaining_model_calls_by_member = {task.task_id: 0 for task in unit.members}
    finalize(adapter, unit, context)
    assert not adapter.work_unit_pending_recovery(context)


@pytest.mark.parametrize("relations", [8, 9])
def test_relation_checks_are_precomputed_without_model_tool_rounds(
    source, monkeypatch, relations,
):
    def repeat_relations(answer, view, number):
        if view["stage"] == "discovery":
            for member in answer["members"]:
                originals = member["result"]["relations"]
                if originals:
                    member["result"]["relations"] = [
                        {**deepcopy(originals[0]), "local_id": f"relation-{index}"}
                        for index in range(relations)
                    ]

    adapter, unit, context, menu, stored, requests = batch_setup(
        source, monkeypatch, size=2, relation=True, transform=repeat_relations,
    )
    adapter.max_output_tokens = 200000
    reviewed = adapter.inspect_work_unit(unit, context, menu)
    assert not reviewed.member_errors and len(reviewed.member_result_refs) == 2
    assert len(requests) == 2
    turns = [row["value"] for row in stored["results"].values() if row["field"] == "model_turn"]
    assert all(not any(item["type"] == "function_call" for item in turn["output_items"])
               for turn in turns)
    tools = [row["value"] for row in stored["results"].values()
             if row["field"] == "tool_result"]
    assert len(tools) == relations
    assert all(item["call_id"].startswith("controller-relation-") for item in tools)
    assert context.protocol_state["member_states"][unit.members[0].task_id]["tool_calls_used"] == (
        relations
    )


def test_member_cannot_borrow_sibling_calls_but_sibling_still_completes(source, monkeypatch):
    adapter, unit, context, menu, stored, requests = batch_setup(source, monkeypatch, size=2)
    first, second = unit.members
    context.remaining_model_calls_by_member[first.task_id] = 1
    reviewed = adapter.inspect_work_unit(unit, context, menu)
    assert reviewed.member_errors == {first.task_id: "model_budget_exhausted"}
    assert set(reviewed.member_result_refs) == {second.task_id}
    assert len(requests) == 2
    assert all(ids == [second.task_id] for _, _, ids in stored["reservations"])
    assert first.task_id not in context.protocol_state["discovery_refs"]


@pytest.mark.parametrize("stop_at", ["model_turn", "discovery", "verification", "tool_result"])
def test_cold_resume_reuses_saved_member_and_paired_tool_results(source, monkeypatch, stop_at):
    adapter, unit, context, menu, stored, requests = batch_setup(
        source, monkeypatch, size=2, relation=True, stop_at=stop_at,
    )
    with pytest.raises(RuntimeError, match="pause after durable commit"):
        adapter.inspect_work_unit(unit, context, menu)
    resumed = context.model_copy(deep=True)
    resumed.protocol_state, resumed.protocol_results = stored["protocol"], stored["results"]
    def checkpoint(value):
        value = deepcopy(value)
        changes = value.pop("result_changes", {})
        validate_tool_protocol(value)
        stored["protocol"] = value
        stored["results"].update(changes)

    resumed.bind_protocol_hook(checkpoint)
    counts = {task.task_id: sum(task.task_id in ids for _, _, ids in stored["reservations"])
              for task in unit.members}
    resumed.remaining_model_calls_by_member = {key: 4 - value for key, value in counts.items()}
    reviewed = adapter.inspect_work_unit(unit, resumed, menu)
    assert not reviewed.member_errors and len(reviewed.member_result_refs) == 2
    assert len(requests) == 2
