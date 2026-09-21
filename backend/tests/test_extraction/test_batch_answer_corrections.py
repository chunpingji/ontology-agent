"""Malformed discovery and incomplete record proofs receive bounded member feedback."""

import json
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal

import pytest

from tests.test_extraction.test_batch_model_adapter import batch_setup, finalize
from tests.test_extraction.test_tool_engine_quantity import metric_case, normalize_metric, shacl

pytest_plugins = ["tests.test_extraction.test_tool_engine_freeze"]


@pytest.fixture
def source(tool_source):
    return tool_source


@pytest.mark.parametrize("failure,reason", [
    ("self_reference", "endpoint_is_not_an_entity"),
    ("missing_entity", "entity_reference_missing"),
    ("evidence_as_bridge", "bridge_reference_missing"),
])
def test_single_relationship_corrects_exact_error_and_keeps_entity_evidence(
    source, monkeypatch, failure, reason,
):
    def malformed_first(answer, view, number):
        if number != 1:
            return
        result = answer["members"][0]["result"]
        relation = result["relations"][0]
        if failure == "self_reference":
            result["entities"] = []
            relation.update(object_ids=[relation["local_id"]], selection="all")
        elif failure == "missing_entity":
            result["entities"] = []
        else:
            relation["bridge_ref_ids"] = [source["source_unit"].evidence_id]

    adapter, unit, context, menu, stored, requests = batch_setup(
        source, monkeypatch, size=1, relation=True, transform=malformed_first,
    )
    reviewed = adapter.inspect_work_unit(unit, context, menu)
    assert not reviewed.member_errors
    assert len(requests) == len(stored["reservations"]) == 3
    correction = json.loads(requests[1]["input_items"][0]["content"][0]["text"])
    feedback = correction["members"][0]["answer_correction"]
    assert reason in json.dumps(feedback["issues"])
    assert feedback["previous_answer"]["relations"]
    assert not any(item["type"] == "reasoning" for item in requests[1]["input_items"]
                   if "type" in item)
    outcome = finalize(adapter, unit, context)[0]
    assert outcome.complete and len(outcome.nodes) == 2
    assert len(outcome.relationship_groups) == 1
    assert all(node.evidence_refs for node in outcome.nodes)


def test_requests_include_actual_narrowed_batch_schema_even_when_not_strict(source, monkeypatch):
    adapter, unit, context, menu, _, requests = batch_setup(
        source, monkeypatch, size=1, relation=True,
    )
    adapter.strict_answers = False
    adapter.inspect_work_unit(unit, context, menu)
    for request in requests:
        if request.get("text_format") is None:
            continue  # Forced deterministic relation checks have their own tool contract.
        assert "阶段回答JSON Schema" not in request["instructions"]
        assert request["text_format"]["schema"]["properties"]
        assert request["text_format"]["strict"] is False
    discovery = requests[0]["text_format"]["schema"]["$defs"]
    assert discovery["BatchMemberResult"]["properties"]["properties"]["maxItems"] == 0
    assert "maxItems" not in discovery["BatchMemberResult"]["properties"]["entities"]
    assert "record_components" in requests[0]["instructions"]
    assert "范围保留上下限及单位原文" in requests[0]["instructions"]


def test_single_invalid_correction_stops_before_verification_budget_is_spent(
    source, monkeypatch,
):
    def malformed(answer, view, number):
        relation = answer["members"][0]["result"]["relations"][0]
        relation.update(object_ids=[relation["local_id"]], selection="all")

    adapter, unit, context, menu, _, requests = batch_setup(
        source, monkeypatch, size=1, relation=True, transform=malformed,
    )
    context.remaining_model_calls_by_member = {unit.members[0].task_id: 6}
    reviewed = adapter.inspect_work_unit(unit, context, menu)
    assert reviewed.member_errors == {unit.members[0].task_id: "member_answer_invalid"}
    assert not reviewed.member_result_refs
    assert len(requests) == 2


def test_single_completed_malformed_json_is_corrected_without_reusing_thinking(source, monkeypatch):
    from app.services.llm import local_client

    adapter, unit, context, menu, _, requests = batch_setup(source, monkeypatch, size=1)
    original = local_client.responses_create

    def malformed(client, **kwargs):
        turn = original(client, **kwargs)
        if len(requests) == 1:
            return replace(turn, output_items=[
                {"type": "reasoning", "content": [{"type": "reasoning_text",
                                                     "text": "private analysis marker"}]},
                {"type": "message", "role": "assistant", "content": [
                    {"type": "output_text", "text": '{"members":'},
                ]},
            ])
        return turn

    monkeypatch.setattr(local_client, "responses_create", malformed)
    reviewed = adapter.inspect_work_unit(unit, context, menu)
    assert not reviewed.member_errors and len(requests) == 3
    repair = json.loads(requests[1]["input_items"][0]["content"][0]["text"])
    assert repair["members"][0]["answer_correction"]["previous_answer"] == '{"members":'
    assert "private analysis marker" not in json.dumps(requests[1])
    assert finalize(adapter, unit, context)[0].complete


@pytest.mark.parametrize("endpoint,expected", [("lower", "0.30"), ("upper", "5.00")])
def test_production_batch_range_keeps_both_bounds_for_later_property_tasks(endpoint, expected):
    case = metric_case("0.30~5.00kg", forms=["interval"], endpoint=endpoint)
    result = normalize_metric(**case)
    assert result.validation_status == "passed"
    assert Decimal(result.normalized_literal) == Decimal(expected)
    assert Decimal(result.quantity.lower) == Decimal("0.30")
    assert Decimal(result.quantity.upper) == Decimal("5.00")
    assert result.quantity.raw == "0.30~5.00kg"
    assert result.quantity.endpoint_role == endpoint
    assert shacl(result, case).validation_status == "passed"


def test_correction_feedback_survives_pause_before_request(source, monkeypatch):
    def malformed(answer, view, number):
        if number == 1:
            relation = answer["members"][0]["result"]["relations"][0]
            relation.update(object_ids=[relation["local_id"]], selection="all")

    adapter, unit, context, menu, stored, requests = batch_setup(
        source, monkeypatch, size=1, relation=True, transform=malformed,
    )
    original_hook = context._protocol_hook

    def pause(value):
        original_hook(value)
        if value["stage_group_seq"] == 2 and value["stage_input_items"]:
            raise RuntimeError("pause with correction")

    context.bind_protocol_hook(pause)
    with pytest.raises(RuntimeError, match="pause with correction"):
        adapter.inspect_work_unit(unit, context, menu)
    assert len(requests) == 1
    saved_results = deepcopy(stored["results"])
    fresh, _, resumed, _, fresh_stored, new_requests = batch_setup(
        source, monkeypatch, size=1, relation=True,
    )
    resumed.protocol_state = deepcopy(stored["protocol"])
    resumed.protocol_results = deepcopy(saved_results)
    resumed.remaining_model_calls_by_member = {unit.members[0].task_id: 3}
    fresh_stored["results"].update(deepcopy(saved_results))
    reviewed = fresh.inspect_work_unit(unit, resumed, menu)
    assert not reviewed.member_errors and len(new_requests) == 2
    member = json.loads(new_requests[0]["input_items"][0]["content"][0]["text"])["members"][0]
    assert "endpoint_is_not_an_entity" in json.dumps(member["answer_correction"])
    assert all(fresh_stored["results"][key] == value for key, value in saved_results.items())
    assert finalize(fresh, unit, resumed)[0].complete


def test_record_referent_gap_is_downgraded_without_verification_correction(
    source, monkeypatch,
):
    source = {**source, "proposal": deepcopy(source["proposal"])}
    entity = source["proposal"]["entities"][0]
    entity.update(representation="record", mentions=[], record_components=[
        {"role": "subject", "quote": source["quote"]("对象乙")},
        {"role": "value", "quote": source["quote"]("数量为5 mg")},
    ])

    def partial_referent(answer, view, number):
        if view["stage"] != "verification":
            return
        for member in answer["members"]:
            targets = {target["target_id"]: target for target in
                       view["members"][0]["verification_input"]["targets"]}
            for verification in member["result"]["verifications"]:
                if targets[verification["target_id"]]["payload"]["local_id"] != "b":
                    continue
                for facet in verification["facets"]:
                    if facet["name"] == "referent":
                        facet["support"] = [source["quote"]("对象乙")]

    adapter, unit, context, menu, stored, requests = batch_setup(
        source, monkeypatch, size=1, relation=True, transform=partial_referent,
    )
    reviewed = adapter.inspect_work_unit(unit, context, menu)
    assert not reviewed.member_errors
    discovery = next(row["value"] for row in stored["results"].values()
                     if row["field"] == "discovery")
    assert "b" not in discovery["claim_issues"]
    assert len(requests) == 2
    verification = json.loads(requests[-1]["input_items"][0]["content"][0]["text"])
    assert verification["stage"] == "verification"
    assert "answer_correction" not in verification["members"][0]
    outcome = finalize(adapter, unit, context)[0]
    assert not outcome.complete and not outcome.relationship_groups
    assert "record_composition_source_coverage_missing" in outcome.reason


def test_bad_member_feedback_never_includes_successful_sibling(source, monkeypatch):
    def malformed_first(answer, view, number):
        if number == 1:
            answer["members"][0]["result"]["properties"][0]["value_quote"].pop("text")

    adapter, unit, context, menu, _, requests = batch_setup(
        source, monkeypatch, size=2, transform=malformed_first,
    )
    reviewed = adapter.inspect_work_unit(unit, context, menu)
    assert not reviewed.member_errors
    repair = json.loads(requests[1]["input_items"][0]["content"][0]["text"])
    from app.services.extraction.ontology_guided.model_reference_projection import (
        project_reference_payload,
    )

    task_ids = project_reference_payload({
        "task_ids": [t.task_id for t in unit.members],
    })["task_ids"]
    assert [member["task_id"] for member in repair["members"]] == task_ids[:1]
    assert "properties.0.value_quote.text" in json.dumps(repair["members"][0]["answer_correction"])
    assert task_ids[1] not in json.dumps(repair)
    assert all(outcome.complete for outcome in finalize(adapter, unit, context))
