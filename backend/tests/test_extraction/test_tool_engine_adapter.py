"""The real harness with controlled provider turns and real parsed Word sources."""

from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest

from app.services.extraction.evidence_identity import evidence_hash
from app.services.extraction.ontology_guided.claim_protocol import ExtractionProfile
from app.services.extraction.ontology_guided.contracts import (
    DocumentContext,
    GraphNode,
    LocalMenu,
    SlotSpec,
    VerificationTarget,
    VersionedRef,
)
from app.services.extraction.ontology_guided.current_work import validate_tool_protocol
from app.services.extraction.ontology_guided.tool_model_adapter import ToolModelRecognitionAdapter
from app.services.extraction.ontology_guided.tool_runtime import ToolLimits
from app.services.llm.local_client import ResponseTurn
from tests.test_extraction.test_tool_engine_freeze import _root_dependency

pytest_plugins = ["tests.test_extraction.test_tool_engine_freeze"]


def setup_adapter(
    source, monkeypatch, *, tool_calls=None, stop_at=None, response_status="completed",
    skip_relation_check=False, budget=4,
):
    options = source["options"]
    task, index = options["task"], options["index"]
    context = options["context"].model_copy(deep=True)
    predicate = source["predicate"]
    root = VersionedRef(id=task.subject.entity_id, revision=task.subject.revision)
    seed = VerificationTarget.create(
        run_fingerprint="test-run",
        claim_ref=VersionedRef(id=task.claim_lineage_id, revision=1),
        task_id=task.task_id,
        check_kind="predicate_entailment",
        document_context=DocumentContext(
            document_hash=index.ir.document_hash,
            document_class_iri=task.subject.class_iri,
            root_ref=root,
        ),
        subject_ref=task.subject,
        predicate_iri=predicate.iri,
        ontology_hash=evidence_hash("ontology"),
        source_scope_hash=evidence_hash(task.record_id),
        context_hash=evidence_hash("seed"),
    )
    node = GraphNode(
        entity_id=root.id,
        revision=root.revision,
        class_iri=task.subject.class_iri,
        class_label="主体",
        label="主体甲",
        root=True,
        root_origin="user_specified",
        grounding_kind="document_root",
        evidence_refs=context.subject_evidence_refs,
    )
    context.tool_inputs = {
        "target_seed": seed.model_dump(mode="json"),
        "run_fingerprint": "test-run",
        "subject_node": node.model_dump(mode="json"),
        "entity_dependencies": [_root_dependency(options).model_dump(mode="json")],
    }
    context.remaining_model_calls = budget
    menu = LocalMenu(
        menu_id="menu",
        subject=task.subject,
        ontology_snapshot_id="ontology",
        relationships=[predicate] if predicate.kind == "relationship" else [],
        properties=[predicate] if predicate.kind == "property" else [],
    )
    storage = {"protocol": {}, "results": {}, "reservations": []}

    def checkpoint(value):
        value = copy.deepcopy(value)
        changes = value.pop("result_changes", {})
        validate_tool_protocol(value)
        storage["protocol"] = value
        storage["results"].update(changes)
        if stop_at and any(row["field"] == stop_at for row in changes.values()):
            raise RuntimeError("pause after durable commit")

    def reserve(stage, ordinal):
        assert storage["protocol"]["pending_request"]["attempt"] == ordinal
        assert len(storage["reservations"]) < budget
        storage["reservations"].append((stage, ordinal))

    context.bind_protocol_hook(checkpoint)
    context.bind_model_call_hook(reserve)
    requests = []

    def transport(client, **kwargs):
        requests.append(copy.deepcopy(kwargs))
        view = json.loads(kwargs["input_items"][0]["content"][0]["text"])
        if kwargs.get("tool_choice") == "required" and (
            not skip_relation_check
        ):
            required = json.loads(kwargs["instructions"].splitlines()[-1])
            output = [{
                "type": "function_call", "id": f"fc-{len(requests)}-{i}",
                "call_id": f"check-{len(requests)}-{i}", "name": "validate_graph",
                "arguments": json.dumps({"claim_id": identity,
                                         "shape_profile_id": required["shape_profile_id"]}),
            } for i, identity in enumerate(required["required_relation_checks"])]
        elif tool_calls and len(requests) == 1:
            output = [
                {"type": "reasoning", "id": "opaque", "encrypted_content": "opaque-data"},
                *tool_calls,
            ]
        else:
            if view["stage"] == "discovery":
                answer = source["proposal"]
            else:
                answer = {"verifications": []}
                for target in view["verification_input"]["targets"]:
                    quote = source["quote"](source["source_unit"].text)
                    answer["verifications"].append(
                        {
                            "target_id": target["target_id"],
                            "content_hash": target["content_hash"],
                            "facets": [
                                {
                                    "name": facet,
                                    "verdict": "supported",
                                    "support": [quote],
                                    "counterevidence_support": [],
                                    "reason": "原文支持",
                                }
                                for facet in target["required_facets"]
                            ],
                        }
                    )
            output = [
                {
                    "type": "message",
                    "id": "msg",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": json.dumps(answer)}],
                }
            ]
        return ResponseTurn(
            response_id=f"response-{len(requests)}",
            output_items=output,
            response_status=response_status,
            incomplete_details=None,
            error=None,
            usage={"input_tokens": 10, "output_tokens": 5},
        )

    monkeypatch.setattr("app.services.llm.local_client.responses_create", transport)
    adapter = ToolModelRecognitionAdapter(
        object(),
        index=index,
        ontology=None,
        metadata=SimpleNamespace(node_summaries=[]),
        profile=ExtractionProfile(),
        token_counter=lambda text: len(text),
        model_identity="qwen-test",
        max_input_tokens=1000000,
        max_output_tokens=2000,
        tool_limits=ToolLimits(20000),
    )
    return adapter, task, context, predicate, menu, storage, requests


def test_relation_requires_tool_result_before_independent_verifier_and_gate(source, monkeypatch):
    adapter, task, context, predicate, menu, storage, requests = setup_adapter(source, monkeypatch)
    outcome = adapter.inspect(task, context, predicate, menu)
    assert len(requests) == 3 and len(storage["reservations"]) == 3
    assert len(outcome.relationship_groups) == 1
    assert outcome.complete
    assert storage["protocol"]["stage"] == "finalize"
    assert storage["protocol"]["outcome_ref"]
    assert "reasoning" not in json.dumps(requests[1]["input_items"])
    assert requests[1]["tool_choice"] == "required"
    assert [t["name"] for t in requests[1]["tools"]] == ["validate_graph"]
    assert requests[1]["text_format"] is None
    initial = requests[1]["input_items"]
    assert requests[2]["input_items"][:len(initial)] == initial
    result = next(item for item in requests[2]["input_items"]
                  if item.get("type") == "function_call_output")
    assert json.loads(result["output"])["data"]["validation_status"] == "passed"
    for request in (requests[0], requests[2]):
        assert request["instructions"]
        assert "阶段回答JSON Schema" in request["instructions"]
        assert "validate_metric" not in {
            tool["name"] for tool in request.get("tools") or []
        }


def test_resume_verified_claims_with_whole_binding_source_needs_no_new_model_call(
    source, monkeypatch,
):
    from app.services.llm import local_client

    original = source["options"]["context"]
    auxiliary = next(fragment for fragment in original.fragments
                     if fragment.purpose == "required_context")
    auxiliary.anchor = auxiliary.anchor.model_copy(update={"span_start": None, "span_end": None})
    before = original.model_dump()
    adapter, task, context, predicate, menu, stored, requests = setup_adapter(
        source, monkeypatch, stop_at="verification",
    )
    transport = local_client.responses_create

    def verify_with_binding(client, **kwargs):
        turn = transport(client, **kwargs)
        if turn.output_items[0]["type"] == "function_call":
            return turn
        part = turn.output_items[0]["content"][0]
        answer = json.loads(part["text"])
        for target in answer.get("verifications", []):
            for facet in target["facets"]:
                facet["support"] = [source["quote"](auxiliary.text, supplemental=True)]
        part["text"] = json.dumps(answer)
        return turn

    monkeypatch.setattr(local_client, "responses_create", verify_with_binding)
    with pytest.raises(RuntimeError, match="pause after durable commit"):
        adapter.inspect(task, context, predicate, menu)
    assert len(requests) == 3
    saved = copy.deepcopy(stored)
    adapter, task, context, predicate, menu, stored, requests = setup_adapter(source, monkeypatch)
    stored.update(saved)
    context.protocol_state, context.protocol_results = saved["protocol"], saved["results"]
    context.remaining_model_calls = 1
    outcome = adapter.inspect(task, context, predicate, menu)
    assert outcome.complete and len(outcome.relationship_groups) == 1
    assert not requests
    assert stored["protocol"]["request_attempt"] == 3
    assert original.model_dump() == before


def test_auto_tool_turn_constrains_answers_to_the_current_stage(source, monkeypatch):
    adapter, task, context, predicate, menu, storage, requests = setup_adapter(source, monkeypatch)
    outcome = adapter.inspect(task, context, predicate, menu)
    assert outcome.complete
    assert len(requests) == 3
    for request in (requests[0], requests[2]):
        assert request["tools"] and request["tool_choice"] == "auto"
        view = json.loads(request["input_items"][0]["content"][0]["text"])
        answer_format = request["text_format"]
        assert answer_format["type"] == "json_schema"
        assert answer_format["name"] == view["stage"]
        schema = answer_format["schema"]
        assert schema["additionalProperties"] is False
        quotes = schema["$defs"]["Quote"]["properties"]["evidence_id"]["enum"]
        assert set(quotes) == {unit["evidence_id"] for unit in view["evidence_units"]}
        if view["stage"] == "discovery":
            relation = schema["$defs"]["RelationProposal"]
            assert "subject_id" in relation["required"]
            assert relation["properties"]["predicate_iri"]["enum"] == [predicate.iri]
        else:
            targets = schema["$defs"]["TargetVerification"]["properties"]["target_id"]["enum"]
            assert targets == [
                target["target_id"] for target in view["verification_input"]["targets"]
            ]


@pytest.mark.parametrize("context_limit,accepted", [(None, True), (102400, True), (35000, False)])
def test_input_allowance_is_not_total_context(source, monkeypatch, context_limit, accepted):
    adapter, task, context, predicate, menu, stored, requests = setup_adapter(source, monkeypatch)
    adapter.max_input_tokens = 32768
    adapter.max_output_tokens = 20480
    adapter.max_context_tokens = context_limit
    adapter.token_counter = lambda _: 20000
    if accepted:
        assert adapter.inspect(task, context, predicate, menu).complete
        assert len(requests) == 3
    else:
        with pytest.raises(RuntimeError, match="context_budget_exceeded"):
            adapter.inspect(task, context, predicate, menu)
        assert not requests and not stored["reservations"]


@pytest.mark.parametrize("kind", [None, "missing", "unknown", "ambiguous", "unbound"])
def test_no_claims_finishes_record_without_asserting_document_absence(source, monkeypatch, kind):
    source["proposal"] = {
        "entities": [], "properties": [], "relations": [], "external_links": [],
        "observations": [] if kind is None else [{
            "subject_id": None, "predicate_iri": None,
            "quote": source["quote"](source["source_unit"].text),
            "kind": kind, "reason": "当前记录的观察",
        }],
    }
    adapter, task, context, predicate, menu, _, requests = setup_adapter(source, monkeypatch)
    outcome = adapter.inspect(task, context, predicate, menu)
    assert len(requests) == 1
    assert not outcome.nodes and not outcome.properties and not outcome.relationship_groups
    assert outcome.complete is (kind in (None, "missing"))
    if outcome.complete:
        assert outcome.reason_code == "record_no_claims"
        assert outcome.semantic_outcome == "not_checked"
    else:
        assert outcome.semantic_outcome == "undetermined"


@pytest.mark.parametrize("failure", [None, "unit", "counterevidence", "shacl"])
def test_property_controller_checks_finish_and_preserve_quantity_or_gap(
    source, monkeypatch, failure,
):
    from app.services.llm import local_client

    slot = SlotSpec(
        iri="urn:quantity", label="数量", declared_by=["urn:Source"],
        datatype_iris=["http://www.w3.org/2001/XMLSchema#decimal"],
        canonical_unit="mL" if failure == "unit" else "g",
    )
    source["predicate"], source["options"] = slot, source["task_context"](slot)
    quote = source["quote"]
    source["proposal"] = {
        "entities": [], "properties": [{
            "local_id": "quantity", "subject_id": "subject-server-id",
            "predicate_iri": slot.iri, "value_quote": quote("5 mg"),
            "field_support": [quote("数量")], "unit_support": [quote("mg")],
            "qualifiers": {"polarity": "affirmed", "modality": "asserted",
                           "condition_support": [], "scope_qualifiers": []},
            "bridge_kind": "explicit_assertion", "bridge_ref_ids": [],
        }], "relations": [], "external_links": [], "observations": [],
    }
    adapter, task, context, predicate, menu, stored, requests = setup_adapter(source, monkeypatch)
    transport = local_client.responses_create
    if failure == "counterevidence":
        def missing_support(client, **kwargs):
            turn = transport(client, **kwargs)
            part = turn.output_items[0]["content"][0]
            payload = json.loads(part["text"])
            for result in payload.get("verifications", []):
                for facet in result["facets"]:
                    if facet["name"] == "counterevidence":
                        facet["support"] = []
            part["text"] = json.dumps(payload)
            return turn

        monkeypatch.setattr(local_client, "responses_create", missing_support)
    if failure == "shacl":
        def unavailable(*args, **kwargs):
            raise RuntimeError("isolated validator failure")

        monkeypatch.setattr("pyshacl.validate", unavailable)
    outcome = adapter.inspect(task, context, predicate, menu)
    assert stored["protocol"]["outcome_ref"]
    assert len(requests) <= 4
    assert all(outcome.controller_checks[kind] >= 1 for kind in ("binding", "metric", "shacl"))
    if failure:
        assert not outcome.properties and not outcome.complete
        if failure == "counterevidence":
            assert "counterevidence" in outcome.reason
    else:
        assert outcome.complete and len(outcome.properties) == 1
        prop = outcome.properties[0]
        assert prop.raw_value == "5 mg" and prop.normalized_value == "0.005"
        assert prop.normalization_record["source_unit"] == "mg"
        assert prop.normalization_record["target_unit"] == "g"
    assert all(not {"validate_metric", "validate_graph"} & {
        tool["name"] for tool in request.get("tools") or []
    } for request in requests)


def test_explicit_reasoning_is_transmitted_and_belongs_to_request_hash(source, monkeypatch):
    from app.services.extraction.ontology_guided.current_work import responses_request_hash

    adapter, task, context, predicate, menu, storage, requests = setup_adapter(source, monkeypatch)
    adapter.reasoning = {"effort": "none"}
    outcome = adapter.inspect(task, context, predicate, menu)
    assert outcome.complete and len(requests) == 3
    turns = sorted([row["value"] for row in storage["results"].values()
                    if row["field"] == "model_turn"], key=lambda row: row["attempt"])
    for request, turn in zip(requests, turns, strict=True):
        assert request["reasoning"] == {"effort": "none"}
        wire = {"model": request["model"], "input": request["input_items"],
                "instructions": request["instructions"], "store": False,
                "max_output_tokens": request["max_output_tokens"],
                "reasoning": request["reasoning"]}
        if request["tools"] is not None:
            wire.update(tools=request["tools"], tool_choice=request["tool_choice"])
        if request["text_format"] is not None:
            wire["text"] = {"format": request["text_format"]}
        assert responses_request_hash(wire) == turn["input_hash"]
        wire.pop("reasoning")
        assert responses_request_hash(wire) != turn["input_hash"]


@pytest.mark.parametrize("cold_resume", [False, True])
@pytest.mark.parametrize("invalid_kind", ["schema", "registered_namespace"])
def test_invalid_stage_answer_receives_derived_feedback_without_relaxing_parser(
    source, monkeypatch, cold_resume, invalid_kind,
):
    from app.services.llm import local_client

    first = setup_adapter(source, monkeypatch, stop_at="model_turn" if cold_resume else None)
    adapter, task, context, predicate, menu, stored, requests = first
    valid_transport = local_client.responses_create
    invalid_payload = {"constraints": []}
    if invalid_kind == "registered_namespace":
        invalid_payload = copy.deepcopy(source["proposal"])
        duplicate = copy.deepcopy(invalid_payload["entities"][0])
        duplicate.update(local_id=task.subject.entity_id, class_iri=task.subject.class_iri)
        invalid_payload["entities"].insert(0, duplicate)
    invalid_text = json.dumps(invalid_payload)

    def invalid_first(client, **kwargs):
        turn = valid_transport(client, **kwargs)
        if len(requests) == 1:
            turn.output_items[0]["content"][0]["text"] = invalid_text
        return turn

    monkeypatch.setattr(local_client, "responses_create", invalid_first)
    if cold_resume:
        with pytest.raises(RuntimeError, match="pause after durable commit"):
            adapter.inspect(task, context, predicate, menu)
        saved = copy.deepcopy(stored)
        adapter, task, context, predicate, menu, stored, requests = setup_adapter(
            source, monkeypatch,
        )
        stored.update(saved)
        context.protocol_state, context.protocol_results = saved["protocol"], saved["results"]
        context.remaining_model_calls = 3
    outcome = adapter.inspect(task, context, predicate, menu)
    assert outcome.complete
    correction_request = requests[0 if cold_resume else 1]
    assert correction_request["input_items"][-2]["content"][0]["text"] == invalid_text
    feedback = json.loads(correction_request["input_items"][-1]["content"][0]["text"])
    assert feedback["kind"] == "stage_answer_invalid"
    if invalid_kind == "schema":
        assert {issue["field_path"] for issue in feedback["issues"]} >= {"constraints", "entities"}
    else:
        assert [issue["field_path"] for issue in feedback["issues"]] == ["entities.0.local_id"]
        assert "local_id_conflicts_with_registered_entity" in feedback["issues"][0]["message"]
    assert stored["protocol"]["request_attempt"] == 4
    assert len(stored["reservations"]) == 4
    assert "stage_answer_invalid" not in json.dumps(requests[-1]["input_items"])


@pytest.mark.parametrize("cold_resume", [False, True])
@pytest.mark.parametrize("mutation,field_path,reason", [
    ("target", "verifications", "verification_target_set_mismatch"),
    ("hash", "verifications.0.content_hash", "verification_content_hash_mismatch"),
    ("facet", "verifications.0.facets", "verification_facet_set_mismatch"),
])
def test_verifier_protocol_errors_receive_feedback_without_changing_claims(
    source, monkeypatch, cold_resume, mutation, field_path, reason,
):
    from app.services.llm import local_client

    adapter, task, context, predicate, menu, stored, requests = setup_adapter(source, monkeypatch)
    valid_transport = local_client.responses_create
    invalid_text = None

    def invalid_verifier(client, **kwargs):
        nonlocal invalid_text
        turn = valid_transport(client, **kwargs)
        if len(requests) == 3:
            payload = json.loads(turn.output_items[0]["content"][0]["text"])
            if mutation == "target":
                payload["verifications"].pop()
            elif mutation == "hash":
                payload["verifications"][0]["content_hash"] = "incorrect-hash"
            else:
                payload["verifications"][0]["facets"].pop()
            invalid_text = json.dumps(payload)
            turn.output_items[0]["content"][0]["text"] = invalid_text
        return turn

    monkeypatch.setattr(local_client, "responses_create", invalid_verifier)
    if cold_resume:
        save = context._protocol_hook

        def pause_after_verification(value):
            save(value)
            if any(row["field"] == "model_turn" and row["value"]["stage"] == "verification"
                   and row["value"]["attempt"] == 3
                   for row in value.get("result_changes", {}).values()):
                raise RuntimeError("pause after verifier commit")

        context.bind_protocol_hook(pause_after_verification)
        with pytest.raises(RuntimeError, match="pause after verifier commit"):
            adapter.inspect(task, context, predicate, menu)
        saved = copy.deepcopy(stored)
        adapter, task, context, predicate, menu, stored, requests = setup_adapter(
            source, monkeypatch,
        )
        stored.update(saved)
        context.protocol_state, context.protocol_results = saved["protocol"], saved["results"]
        context.remaining_model_calls = 1
    outcome = adapter.inspect(task, context, predicate, menu)
    assert outcome.complete and len(outcome.relationship_groups) == 1
    correction = requests[0 if cold_resume else 3]
    assert correction["input_items"][-2]["content"][0]["text"] == invalid_text
    feedback = json.loads(correction["input_items"][-1]["content"][0]["text"])
    assert feedback["stage"] == "verification"
    assert [issue["field_path"] for issue in feedback["issues"]] == [field_path]
    assert reason in feedback["issues"][0]["message"]
    assert stored["protocol"]["request_attempt"] == 4
    assert len(stored["reservations"]) == 4
    assert len([row for row in stored["results"].values() if row["field"] == "discovery"]) == 1


@pytest.mark.parametrize("name,args", [("unknown_function", "{}"), ("get_schema_card", "{bad")])
def test_error_tool_pairs_preserve_original_call_and_opaque_reasoning(
    source, monkeypatch, name, args
):
    call = {
        "type": "function_call",
        "id": "output-item",
        "call_id": "call-1",
        "name": name,
        "arguments": args,
    }
    adapter, task, context, predicate, menu, storage, requests = setup_adapter(
        source,
        monkeypatch,
        tool_calls=[call],
    )
    outcome = adapter.inspect(task, context, predicate, menu)
    assert len(requests) == 4 and outcome.complete
    continued = requests[1]["input_items"]
    assert continued[1]["encrypted_content"] == "opaque-data"
    assert continued[2] == call
    assert continued[3]["call_id"] == "call-1"
    assert json.loads(continued[3]["output"])["status"] == "blocked"
    assert storage["protocol"]["tool_calls_used"] == 2


def test_incomplete_response_is_saved_before_failure_and_never_runs_tools(source, monkeypatch):
    call = {
        "type": "function_call",
        "id": "output-item",
        "call_id": "call-1",
        "name": "get_schema_card",
        "arguments": "{}",
    }
    adapter, task, context, predicate, menu, storage, requests = setup_adapter(
        source,
        monkeypatch,
        tool_calls=[call],
        response_status="incomplete",
    )
    with pytest.raises(Exception, match="model_response_incomplete"):
        adapter.inspect(task, context, predicate, menu)
    assert len(requests) == 1
    assert storage["protocol"]["turn_refs"]
    assert not storage["protocol"]["completed_tool_results"]
    assert storage["protocol"]["tool_calls_used"] == 0


@pytest.mark.parametrize("stop_at", ["model_turn", "tool_result"])
def test_cold_resume_keeps_paired_results_and_never_repeats_confirmed_tool(
    source, monkeypatch, stop_at,
):
    call = {
        "type": "function_call", "id": "output-item", "call_id": "call-1",
        "name": "get_schema_card", "arguments": json.dumps({
            "subject_id": source["options"]["task"].subject.entity_id, "predicate_iri": None,
        }),
    }
    first = setup_adapter(source, monkeypatch, tool_calls=[call], stop_at=stop_at)
    adapter, task, context, predicate, menu, stored, _requests = first
    with pytest.raises(RuntimeError, match="pause after durable commit"):
        adapter.inspect(task, context, predicate, menu)
    saved = copy.deepcopy(stored)
    resumed = setup_adapter(source, monkeypatch)
    adapter, task, context, predicate, menu, storage, requests = resumed
    storage.update(saved)
    context.protocol_state = copy.deepcopy(saved["protocol"])
    context.protocol_results = copy.deepcopy(saved["results"])
    context.remaining_model_calls = 4 - len(saved["reservations"])
    outcome = adapter.inspect(task, context, predicate, menu)
    assert outcome.complete and len(requests) == 3
    assert storage["protocol"]["request_attempt"] == 4
    assert storage["protocol"]["tool_calls_used"] == 2
    results = [value for value in storage["results"].values() if value["field"] == "tool_result"]
    assert len(results) == 2
    assert sum(row["value"]["call_id"] == "call-1" for row in results) == 1


def test_paused_answer_response_does_not_gain_tool_permission_on_resume(source, monkeypatch):
    call = {"type": "function_call", "id": "output-item", "call_id": "call-1",
            "name": "get_schema_card", "arguments": "{}"}
    first = setup_adapter(source, monkeypatch, tool_calls=[call], stop_at="model_turn")
    adapter, task, context, predicate, menu, stored, requests = first
    context.remaining_model_calls = 2
    with pytest.raises(RuntimeError, match="pause after durable commit"):
        adapter.inspect(task, context, predicate, menu)
    assert requests[0]["tools"] is None
    assert requests[0]["text_format"]["type"] == "json_schema"
    saved = copy.deepcopy(stored)
    resumed = setup_adapter(source, monkeypatch)
    adapter, task, context, predicate, menu, storage, requests = resumed
    storage.update(saved)
    context.protocol_state, context.protocol_results = saved["protocol"], saved["results"]
    context.remaining_model_calls = 1
    with pytest.raises(Exception, match="model_tool_protocol_invalid"):
        adapter.inspect(task, context, predicate, menu)
    assert not requests
    assert not storage["protocol"]["completed_tool_results"]
    assert storage["protocol"]["tool_calls_used"] == 0


def test_observation_uses_actual_request_and_narrowed_card(source, monkeypatch):
    from app.services.llm.model_runtime import model_scope

    adapter, task, context, predicate, menu, _, requests = setup_adapter(source, monkeypatch)
    events = []
    with model_scope(on_harness_event=lambda kind, data: events.append((kind, data))):
        adapter.inspect(task, context, predicate, menu)
    starts = [data for kind, data in events if kind == "model_start"]
    assert len(starts) == len(requests) == 3
    assert starts[0]["call_id"] != starts[1]["call_id"]
    for start, request in zip(starts, requests):
        assert start["request"]["input"] == request["input_items"]
        assert start["request"]["instructions"] == request["instructions"]
        input_view = json.loads(request["input_items"][0]["content"][0]["text"])
        assert start["schema_card"] == input_view["schema_card"]
        assert len(start["schema_card"]["predicates"]) == 1
    assert any(kind == "operation_start" and data["kind"] == "validation"
               for kind, data in events)
