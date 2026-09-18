"""Tool contracts must preserve strict inputs, protocol errors and typed outputs."""

from __future__ import annotations

import copy
import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.services.extraction.ontology_guided.tool_contracts import (
    TOOL_DEFINITIONS,
    EvidenceData,
    EvidenceUnit,
    GetSchemaCardArgs,
    InspectEvidenceArgs,
    InstanceData,
    MetricData,
    ToolCall,
    ToolErrorResult,
    ToolIssue,
    ToolObservation,
    ToolResult,
    get_tool_definitions,
)

CONTRACTS = (
    Path(__file__).resolve().parents[3]
    / "specs/027-ontology-extraction-engine-v2/contracts"
)
SPEC_TOOLS = json.loads((CONTRACTS / "tools.json").read_text(encoding="utf-8"))
EXAMPLES = json.loads((CONTRACTS / "examples.json").read_text(encoding="utf-8"))


def _schema_without_annotations(value):
    if isinstance(value, dict):
        return {
            key: _schema_without_annotations(item)
            for key, item in value.items()
            if key not in {"title", "description"}
        }
    if isinstance(value, list):
        return [_schema_without_annotations(item) for item in value]
    return value


def _issue(code="invalid_tool_arguments"):
    return ToolIssue(code=code, field_path=None, message="参数不符合契约。", evidence_ids=[])


def _evidence_result():
    return ToolResult[EvidenceData](
        status="ok",
        data=EvidenceData(
            units=[
                EvidenceUnit(
                    evidence_id="ev-1",
                    record_id="record-1",
                    text="原文",
                    span_start=2,
                    span_end=4,
                    role="target",
                    fact_eligible=True,
                    table=None,
                )
            ],
            omitted_ids=[],
            coverage_complete=True,
        ),
        evidence_refs=["ev-1"],
        issues=[],
    )


def test_exported_functions_match_the_spec_without_runtime_spec_loading():
    actual = get_tool_definitions()
    assert [item["name"] for item in actual] == [item["name"] for item in SPEC_TOOLS]
    for generated, expected in zip(actual, SPEC_TOOLS, strict=True):
        assert set(generated) == {"type", "name", "description", "parameters", "strict"}
        assert generated["type"] == "function"
        assert generated["strict"] is False
        assert _schema_without_annotations(generated["parameters"]) == expected["parameters"]
    assert all(item["strict"] is True for item in get_tool_definitions(strict=True))


@pytest.mark.parametrize("name", list(TOOL_DEFINITIONS))
def test_argument_models_accept_spec_examples_and_reject_type_missing_and_extra(name):
    model = TOOL_DEFINITIONS[name].args_type
    value = EXAMPLES["tool_arguments"][name]
    parsed = model.model_validate_json(json.dumps(value), strict=True)
    assert parsed.model_dump(mode="json") == value
    for field in value:
        missing = {key: item for key, item in value.items() if key != field}
        with pytest.raises(ValidationError):
            model.model_validate_json(json.dumps(missing), strict=True)
        wrong = {**value, field: 42}
        with pytest.raises(ValidationError):
            model.model_validate_json(json.dumps(wrong), strict=True)
    with pytest.raises(ValidationError):
        model.model_validate_json(json.dumps({**value, "owner": "model"}), strict=True)


def test_nullable_arguments_remain_required_and_do_not_accept_coercion():
    assert GetSchemaCardArgs(subject_id="s", predicate_iri=None).predicate_iri is None
    with pytest.raises(ValidationError):
        GetSchemaCardArgs(subject_id="s")
    with pytest.raises(ValidationError):
        GetSchemaCardArgs(subject_id=123, predicate_iri=None)
    args_type = TOOL_DEFINITIONS["retrieve_evidence"].args_type
    with pytest.raises(ValidationError):
        args_type(subject_id="s", predicate_iri="urn:predicate", missing_facets=["invented"])


def test_catalog_keeps_calibration_controller_only_and_stage_metadata_off_the_wire():
    controller_only = {name for name, value in TOOL_DEFINITIONS.items() if not value.model_callable}
    assert controller_only == {"validate_metric"}
    assert len(TOOL_DEFINITIONS) - len(controller_only) == 10
    assert TOOL_DEFINITIONS["validate_graph"].allowed_stages == {"verification", "finalize"}
    for name in controller_only:
        assert TOOL_DEFINITIONS[name].allowed_stages == {"finalize"}
    assert TOOL_DEFINITIONS["propose_mentions"].allowed_stages == {"discovery"}
    assert TOOL_DEFINITIONS["find_referent_candidates"].allowed_stages == {
        "discovery", "verification",
    }
    assert TOOL_DEFINITIONS["check_claim_binding"].allowed_stages == {"verification", "finalize"}
    assert TOOL_DEFINITIONS["propose_repair"].allowed_stages == {"discovery", "verification"}
    for value in get_tool_definitions():
        assert "model_callable" not in value
        assert "allowed_stages" not in value
    with pytest.raises(TypeError):
        TOOL_DEFINITIONS["validate_graph"] = TOOL_DEFINITIONS["inspect_evidence"]


@pytest.mark.parametrize("case", EXAMPLES["tool_protocol_error_examples"])
def test_invalid_calls_survive_typed_error_observation_without_parsing_raw_arguments(case):
    saved = case["observation_before_pause"]
    call = ToolCall.model_validate(saved["call"], strict=True)
    error = ToolErrorResult.model_validate(saved["result"], strict=True)
    observation = ToolObservation(
        request_attempt=saved["request_attempt"],
        call=call,
        parsed_arguments=None,
        result_ref=saved["result_ref"],
        result=error,
    )
    assert observation.call.model_dump() == saved["call"]
    assert json.loads(observation.result.model_dump_json()) == saved["result"]
    assert observation.parsed_arguments is None
    with pytest.raises(FrozenInstanceError):
        observation.request_attempt = 2


def test_result_serialization_retains_specific_fields_and_wrong_tool_types_are_rejected():
    result = _evidence_result()
    call = ToolCall(call_id="call-1", name="inspect_evidence", arguments_json='{"evidence_ids":[]}')
    observation = ToolObservation(
        request_attempt=1,
        call=call,
        parsed_arguments=InspectEvidenceArgs(evidence_ids=[]),
        result_ref="result-1",
        result=result,
    )
    assert json.loads(observation.result.model_dump_json())["data"]["units"][0]["text"] == "原文"
    with pytest.raises(ValueError, match="registered tool"):
        ToolObservation(
            request_attempt=1,
            call=call,
            parsed_arguments=GetSchemaCardArgs(subject_id="s", predicate_iri=None),
            result_ref="result-1",
            result=result,
        )
    with pytest.raises(ValueError, match="concrete result type"):
        ToolObservation(
            request_attempt=1,
            call=call,
            parsed_arguments=InspectEvidenceArgs(evidence_ids=[]),
            result_ref="result-1",
            result=ToolErrorResult(
                status="blocked", data=None, evidence_refs=[], issues=[_issue()]
            ),
        )


def test_execution_error_keeps_valid_arguments_and_specific_result_type():
    args = InspectEvidenceArgs(evidence_ids=["ev-1"])
    result = ToolResult[EvidenceData](
        status="blocked", data=None, evidence_refs=[], issues=[_issue("reference_outside_scope")]
    )
    observation = ToolObservation(
        request_attempt=2,
        call=ToolCall(
            call_id="call-1", name="inspect_evidence", arguments_json=args.model_dump_json()
        ),
        parsed_arguments=args,
        result_ref="result-2",
        result=result,
    )
    assert observation.parsed_arguments is args
    assert observation.result.issues[0].code == "reference_outside_scope"


@pytest.mark.parametrize("status", ["blocked", "error"])
def test_protocol_error_requires_issues_and_cannot_claim_success(status):
    with pytest.raises(ValidationError):
        ToolErrorResult(status=status, data=None, evidence_refs=[], issues=[])
    with pytest.raises(ValidationError):
        ToolErrorResult(status="ok", data=None, evidence_refs=[], issues=[_issue()])
    with pytest.raises(ValidationError):
        ToolResult[EvidenceData](status="ok", data=None, evidence_refs=[], issues=[])


@pytest.mark.parametrize("literal", ["5.000", True, False, None])
def test_metric_literal_preserves_decimal_strings_and_boolean_identity(literal):
    value = MetricData(
        claim_ref={"id": "p1", "revision": 1},
        validation_status="incomplete",
        quantity=None,
        normalized_literal=literal,
        issues=[],
    )
    assert value.normalized_literal == literal
    assert type(value.normalized_literal) is type(literal)


@pytest.mark.parametrize("literal", [1, 1.5, ["5"]])
def test_metric_literal_rejects_numeric_or_container_coercion(literal):
    with pytest.raises(ValidationError):
        MetricData(
            claim_ref={"id": "p1", "revision": 1},
            validation_status="passed",
            quantity=None,
            normalized_literal=literal,
            issues=[],
        )


def test_incomplete_instance_search_cannot_be_reported_as_no_match():
    payload = {
        "candidates": [], "searched_sources": ["source-1"], "incomplete_sources": [],
        "excluded_count": 0, "identity_status": "not_checked",
    }
    result_type = TOOL_DEFINITIONS["query_instances"].result_type
    result = result_type(status="no_match", data=payload, evidence_refs=[], issues=[])
    assert isinstance(result.data, InstanceData)
    for update in ({"excluded_count": None}, {"incomplete_sources": ["source-1"]}):
        with pytest.raises(ValidationError):
            result_type(status="no_match", data={**payload, **update}, evidence_refs=[], issues=[])
    with pytest.raises(ValidationError):
        InstanceData(**{**payload, "excluded_count": -1})


def test_result_models_reject_untyped_extra_data_and_invalid_original_spans():
    result = _evidence_result().model_dump(mode="json")
    corrupted = copy.deepcopy(result)
    corrupted["data"]["invented_metadata"] = True
    with pytest.raises(ValidationError):
        ToolResult[EvidenceData].model_validate_json(json.dumps(corrupted), strict=True)
    corrupted = copy.deepcopy(result)
    corrupted["data"]["units"][0]["span_end"] = 3
    with pytest.raises(ValidationError, match="half-open"):
        ToolResult[EvidenceData].model_validate_json(json.dumps(corrupted), strict=True)


def test_each_tool_exports_a_concrete_result_schema():
    for definition in TOOL_DEFINITIONS.values():
        schema = definition.result_type.model_json_schema()
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == {"status", "data", "evidence_refs", "issues"}
        assert "$ref" in schema["properties"]["data"]["anyOf"][0]
