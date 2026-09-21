"""Provider structure binds frozen targets without replacing local proof validation."""

import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.extraction.evidence_identity import evidence_hash
from app.services.extraction.ontology_guided.claim_protocol import (
    ExtractionProfile,
    PropertyProposal,
    VerificationEnvelope,
    VerificationInput,
    VerificationTargetSpec,
    claim_content_hash,
    compile_schema_card,
    compile_stage_schema,
    validate_verification_targets,
)
from app.services.extraction.ontology_guided.contracts import TraversalScope, VersionedRef
from app.services.extraction.ontology_guided.model_reference_projection import (
    ModelReferenceProjection,
    project_reference_payload,
)
from app.services.extraction.ontology_guided.model_schema_projection import compact_answer_schema
from app.services.extraction.ontology_guided.recognition_batch import compile_batch_stage_schema
from tests.test_extraction.test_batch_model_adapter import batch_setup
from tests.test_extraction.test_tool_engine_contracts import make_menu

pytest_plugins = ["tests.test_extraction.test_tool_engine_freeze"]


@pytest.fixture
def validator():
    return pytest.importorskip("jsonschema").Draft202012Validator


@pytest.fixture
def claims():
    path = Path(__file__).resolve().parents[3] / (
        "specs/027-ontology-extraction-engine-v2/contracts/examples.json"
    )
    data = json.loads(path.read_text())["verification_exchange"]["verification_input"]
    targets = VerificationInput.model_validate(data, strict=True).targets
    quote = {"evidence_id": "ev-1", "text": "5", "context_text": None}
    payload = PropertyProposal(
        local_id="p1", subject_id="s", predicate_iri="urn:amount", value_quote=quote,
        field_support=[], unit_support=[], bridge_kind="owned_field_group", bridge_ref_ids=[],
        qualifiers={"polarity": "affirmed", "modality": "asserted",
                    "condition_support": [], "scope_qualifiers": []},
    )
    scope = TraversalScope.create()
    targets.append(VerificationTargetSpec(
        target_id=evidence_hash("property-target"), target_kind="property",
        claim_ref=VersionedRef(id="property-claim", revision=1),
        content_hash=claim_content_hash("property", payload, scope, []),
        required_facets=["subject_binding", "field_role", "predicate", "bridge", "value",
                         "qualifiers", "counterevidence"],
        payload=payload, scope=scope, dependency_refs=[],
    ))
    card = compile_schema_card(make_menu("urn:"), predicate_iri=None,
                               profile=ExtractionProfile(), scope=scope)
    return card, targets


def answer(targets, verdict="unsupported"):
    return {"verifications": [{
        "target_id": target.target_id, "content_hash": target.content_hash,
        "facets": [{"name": name, "verdict": verdict, "support": [],
                    "counterevidence_support": [], "reason": "独立核验"}
                   for name in target.required_facets],
    } for target in targets]}


def compile_single(card, targets):
    return compile_stage_schema("verification", card=card, evidence_ids=["ev-1"], targets=targets)


@pytest.mark.parametrize("verdict", ["supported", "unsupported", "undetermined"])
def test_all_verdicts_require_the_same_complete_mixed_target_structure(claims, validator, verdict):
    card, targets = claims
    value = answer(targets, verdict)
    original = compile_single(card, targets)
    for schema in (original, compact_answer_schema(original)):
        validator.check_schema(schema)
        validator(schema).validate(value)
    validate_verification_targets(VerificationEnvelope.model_validate(value), targets=targets)


@pytest.mark.parametrize("mutation", [
    "missing_target", "extra_target", "duplicate_target", "swapped_target_id", "swapped_hash",
    "missing_facet", "duplicate_facet", "wrong_facet", "extra_facet",
])
def test_schema_and_unchanged_local_gate_reject_incomplete_or_mismatched_answers(
    claims, validator, mutation,
):
    card, targets = claims
    value = answer(targets)
    rows = value["verifications"]
    facets = rows[0]["facets"]
    if mutation == "missing_target":
        rows.pop()
    elif mutation == "extra_target":
        rows.append(deepcopy(rows[0]))
    elif mutation == "duplicate_target":
        rows[1] = deepcopy(rows[0])
    elif mutation == "swapped_target_id":
        rows[0]["target_id"], rows[1]["target_id"] = rows[1]["target_id"], rows[0]["target_id"]
    elif mutation == "swapped_hash":
        rows[0]["content_hash"], rows[1]["content_hash"] = (
            rows[1]["content_hash"], rows[0]["content_hash"],
        )
    elif mutation == "missing_facet":
        del facets[1:]  # The original failure: unsupported type omitted the other entity checks.
    elif mutation == "duplicate_facet":
        facets[1] = deepcopy(facets[0])
    elif mutation == "wrong_facet":
        facets[0]["name"] = "value"
    else:
        facets.append({**facets[0], "name": "value"})
    original = compile_single(card, targets)
    for schema in (original, compact_answer_schema(original)):
        assert not validator(schema).is_valid(value)
    with pytest.raises(ValueError):
        validate_verification_targets(VerificationEnvelope.model_validate(value), targets=targets)


def test_order_is_only_a_provider_serialization_constraint(claims, validator):
    card, targets = claims
    value = answer(targets)
    value["verifications"].reverse()
    value["verifications"][0]["facets"].reverse()
    assert not validator(compile_single(card, targets)).is_valid(value)
    validate_verification_targets(VerificationEnvelope.model_validate(value), targets=targets)


def test_split_schemas_only_require_the_current_target_subset_and_empty_stays_empty(
    claims, validator,
):
    card, targets = claims
    for part in (targets[:1], targets[1:], []):
        schema = compact_answer_schema(compile_single(card, part))
        validator.check_schema(schema)
        validator(schema).validate(answer(part))
        assert not validator(schema).is_valid(answer(targets))


def test_members_own_distinct_targets_and_const_ids_survive_compact_projection(claims, validator):
    card, targets = claims
    members = [SimpleNamespace(task_id=evidence_hash(["member", i]), card=card,
                               context=SimpleNamespace(fragments=[])) for i in range(2)]
    owned = {members[0].task_id: targets[:2], members[1].task_id: targets[2:]}
    schema = compact_answer_schema(compile_batch_stage_schema(
        "verification", members, targets_by_member=owned,
    ))
    validator.check_schema(schema)
    value = {"members": [{"task_id": m.task_id, "result": answer(owned[m.task_id])}
                         for m in members]}
    validator(schema).validate(value)
    for mutation in ("missing_member", "duplicate_member", "swapped_results", "all_targets"):
        bad = deepcopy(value)
        if mutation == "missing_member":
            bad["members"].pop()
        elif mutation == "duplicate_member":
            bad["members"][1] = deepcopy(bad["members"][0])
        elif mutation == "swapped_results":
            bad["members"][0]["result"], bad["members"][1]["result"] = (
                bad["members"][1]["result"], bad["members"][0]["result"],
            )
        else:
            bad["members"][0]["result"] = answer(targets)
        assert not validator(schema).is_valid(bad)
    projection = ModelReferenceProjection({
        "input": json.dumps(value),
        "text": {"format": {"type": "json_schema", "name": "verification", "schema": schema}},
    })
    encoded = projection.encode_payload(value)
    wire_schema = projection.request["text"]["format"]["schema"]
    validator(wire_schema).validate(encoded)
    assert not validator(wire_schema).is_valid(value)
    assert projection.decode_payload(encoded) == value
    assert compact_answer_schema(wire_schema) == wire_schema
    for m in members:
        subset = compact_answer_schema(compile_batch_stage_schema(
            "verification", [m], targets_by_member=owned,
        ))
        validator(subset).validate({"members": [next(
            item for item in value["members"] if item["task_id"] == m.task_id
        )]})
        assert not validator(subset).is_valid(value)


def test_batch_split_and_cold_resume_send_matching_exact_member_schemas(
    tool_source, monkeypatch, validator,
):
    from app.services.llm import local_client

    adapter, unit, context, menu, stored, requests = batch_setup(
        tool_source, monkeypatch, size=2, stop_at="verification",
    )
    transport = local_client.responses_create

    def truncate(client, **kwargs):
        response = transport(client, **kwargs)
        if len(requests) == 2:
            return replace(response, response_status="incomplete",
                           incomplete_details={"reason": "max_output_tokens"})
        return response

    monkeypatch.setattr(local_client, "responses_create", truncate)
    with pytest.raises(RuntimeError, match="pause after durable commit"):
        adapter.inspect_work_unit(unit, context, menu)
    assert len(stored["protocol"]["verification_refs"]) == 1
    saved = deepcopy(stored)
    previous_requests = deepcopy(requests)
    adapter, unit, context, menu, stored, requests = batch_setup(tool_source, monkeypatch, size=2)
    context.protocol_state, context.protocol_results = saved["protocol"], saved["results"]
    stored.update(saved)
    context.remaining_model_calls_by_member = dict.fromkeys(
        context.remaining_model_calls_by_member, 2,
    )
    reviewed = adapter.inspect_work_unit(unit, context, menu)
    assert not reviewed.member_errors and len(reviewed.member_result_refs) == 2
    assert len(requests) == 1
    completed_member = next(iter(saved["protocol"]["verification_refs"]))
    resumed_view = json.loads(requests[0]["input_items"][0]["content"][0]["text"])
    completed_wire_id = project_reference_payload({"task_id": completed_member})["task_id"]
    assert completed_wire_id not in {m["task_id"] for m in resumed_view["members"]}
    verification_requests = [*previous_requests[1:], *requests]
    for request in verification_requests:
        view = json.loads(request["input_items"][0]["content"][0]["text"])
        expected = {"members": [{"task_id": m["task_id"], "result": {
            "verifications": [{"target_id": t["target_id"], "content_hash": t["content_hash"],
                "facets": [{"name": f, "verdict": "unsupported", "support": [],
                            "counterevidence_support": [], "reason": "未支持"}
                           for f in t["required_facets"]]}
                for t in m["verification_input"]["targets"]]}}
            for m in view["members"]]}
        validator(request["text_format"]["schema"]).validate(expected)
    assert [len(json.loads(r["input_items"][0]["content"][0]["text"])["members"])
            for r in verification_requests] == [2, 1, 1]
