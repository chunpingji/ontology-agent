"""Invalid mappings stay excluded while source observations survive failed calibration."""

import copy

import pytest

from app.services.extraction.ontology_guided.attribute_calibration import (
    collect_candidates,
    field_candidate,
    merge_candidates,
)
from app.services.extraction.ontology_guided.attribute_disambiguation import field_payload
from app.services.extraction.ontology_guided.claim_freeze import freeze_record_proposal
from app.services.extraction.ontology_guided.claim_protocol import (
    DiscoveryEnvelope,
    EntityDependencyView,
)
from app.services.extraction.ontology_guided.contracts import GraphProperty, VersionedRef
from app.services.extraction.ontology_guided.executor import TaskOutcome
from tests.test_extraction.test_contextual_record_adapter import field_for, setup_attribute
from tests.test_extraction.test_record_model_adapter import setup_record
from tests.test_extraction.test_record_model_adapter import source as source  # noqa: F401


def incomplete(reason="model_timeout"):
    return TaskOutcome(
        semantic_outcome="not_checked", complete=False, reason_code=reason,
        reason="Current attempt did not establish a property.",
    )


@pytest.mark.parametrize("violation, expected_issue", [
    ("predicate", "predicate_outside_menu"),
    ("value_source", "fact_source_outside_scope"),
    ("field_source", "source_quote_outside_scope"),
])
def test_frozen_invalid_mapping_does_not_become_calibration_candidate(
    source, monkeypatch, violation, expected_issue,
):
    _adapter, task, context, card, _storage, requests, proposal = setup_record(source, monkeypatch)
    proposal = copy.deepcopy(proposal)
    proposal["properties"] = proposal["properties"][:1]
    prop = proposal["properties"][0]
    if violation == "predicate":
        prop["predicate_iri"] = "urn:outside-ontology"
    elif violation == "value_source":
        prop["value_quote"]["evidence_id"] = "unauthorized-source"
    else:
        prop["field_support"][0]["evidence_id"] = "unauthorized-source"
    frozen = freeze_record_proposal(
        DiscoveryEnvelope.model_validate(proposal), task=task, context=context,
        card=card, index=source["index"], generation=1, reference_resolution=True,
    )
    assert expected_issue in frozen.claim_issues[prop["local_id"]]
    assert collect_candidates(
        task, context, card, frozen, {}, incomplete(), feedback={},
    ) == []
    assert requests == []


def test_invalid_model_mapping_keeps_only_the_authorized_physical_field(source, monkeypatch):
    _adapter, task, context, card, _storage, requests, proposal = setup_attribute(
        source, monkeypatch,
    )
    proposal = copy.deepcopy(proposal)
    proposal["properties"][0]["predicate_iri"] = "urn:outside-ontology"
    frozen = freeze_record_proposal(
        DiscoveryEnvelope.model_validate(proposal), task=task, context=context,
        card=card, index=source["index"], generation=1,
        entity_dependencies=[
            EntityDependencyView.model_validate(value)
            for value in context.tool_inputs["entity_dependencies"]
        ],
        reference_resolution=True,
    )
    candidates = collect_candidates(
        task, context, card, frozen, {}, incomplete("attribute_not_supported"), feedback={},
    )
    assert len(candidates) == 1
    candidate = candidates[0]
    field = field_for(source)
    assert candidate.field_id == field.field_id
    assert candidate.raw_value == "5" and candidate.value_refs == list(field.value_refs)
    assert candidate.source_claim_id is None
    assert candidate.options == context.tool_inputs["attribute_disambiguation"]["options"]
    assert all(option["predicate_iri"] != "urn:outside-ontology" for option in candidate.options)
    assert requests == []


def test_valid_source_mapping_remains_pending_without_successful_checks(source, monkeypatch):
    _adapter, task, context, card, _storage, requests, proposal = setup_record(source, monkeypatch)
    proposal = copy.deepcopy(proposal)
    proposal["properties"] = proposal["properties"][:1]
    frozen = freeze_record_proposal(
        DiscoveryEnvelope.model_validate(proposal), task=task, context=context,
        card=card, index=source["index"], generation=1, reference_resolution=True,
    )
    assert "pb" not in frozen.claim_issues
    candidates = collect_candidates(
        task, context, card, frozen, {}, incomplete("metric_not_evaluated"), feedback={},
    )
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.status == "pending"
    assert candidate.options == [{
        "subject_ref": frozen.local_ref_map["b"].model_dump(mode="json"),
        "class_iri": "urn:T", "predicate_iri": "urn:value",
    }]
    assert candidate.parsed_value.value == "5"
    assert candidate.parsed_value.datatype_iri == "http://www.w3.org/2001/XMLSchema#decimal"
    assert [source["index"].ir.resolve(ref) for ref in candidate.value_refs] == ["5"]
    assert candidate.checks.get("metric") != "passed"
    assert candidate.checks["shacl"] == "not_checked"
    assert requests == []


def pending_field(source, task, context):
    return field_candidate(
        task, field_payload(field_for(source)),
        context.tool_inputs["attribute_disambiguation"]["options"],
        reason="attribute_subject_unresolved",
    )


@pytest.mark.parametrize("attempt", ["technical_failure", "model_omission"])
def test_existing_source_candidate_survives_failed_or_empty_calibration(
    source, monkeypatch, attempt,
):
    _adapter, task, context, _card, _storage, requests, _proposal = setup_attribute(
        source, monkeypatch,
    )
    previous = pending_field(source, task, context)
    outcome = incomplete() if attempt == "technical_failure" else TaskOutcome(
        semantic_outcome="not_checked", complete=True, reason_code="record_no_claims",
        reason="No property was proposed on this attempt.",
    )
    merged = merge_candidates([previous.model_dump(mode="json")], outcome)
    assert len(merged) == 1
    retained = merged[0]
    assert retained.model_dump(exclude={"reason_codes"}) == previous.model_dump(
        exclude={"reason_codes"},
    )
    assert "attribute_subject_unresolved" in retained.reason_codes
    if attempt == "technical_failure":
        assert "model_timeout" in retained.reason_codes
    assert outcome.properties == [] and requests == []


def verified_property(candidate, **changes):
    option = candidate.options[0]
    values = dict(
        candidate_id="verified-property", revision=2,
        subject_ref=VersionedRef.model_validate(option["subject_ref"]),
        predicate_iri=option["predicate_iri"], predicate_label="数量",
        raw_value="5", normalized_value=5, decision_status="supported",
        structural_valid=True, model_supported=True, policy_eligible=True,
        proof_ref=VersionedRef(id="property-proof", revision=1),
        decision_refs=[VersionedRef(id="property-decision", revision=1)],
        value_evidence_refs=candidate.value_refs,
    )
    return GraphProperty(**{**values, **changes})


@pytest.mark.parametrize("corrected_owner", [False, True])
def test_calibration_clears_the_field_but_keeps_unrelated_same_values(
    source, monkeypatch, corrected_owner,
):
    _adapter, task, context, _card, _storage, requests, _proposal = setup_attribute(
        source, monkeypatch,
    )
    candidate = pending_field(source, task, context)
    other = candidate.model_copy(update={
        "candidate_id": "other-observation", "field_id": None,
        "value_refs": [candidate.value_refs[0].model_copy(update={"evidence_id": "other-record"})],
    })
    another_property = candidate.model_copy(update={
        "candidate_id": "other-property", "field_id": None,
        "options": [{**candidate.options[0], "predicate_iri": "urn:other-property"}],
    })
    changes = {"subject_ref": VersionedRef(id="newly-established-owner", revision=1)} if (
        corrected_owner
    ) else {}
    outcome = TaskOutcome(
        semantic_outcome="supported", complete=True, reason_code="claim_verified",
        reason="The original field has now passed all checks.",
        properties=[verified_property(candidate, **changes)],
    )
    merged = merge_candidates([
        item.model_dump(mode="json") for item in (candidate, other, another_property)
    ], outcome)
    assert merged == [other, another_property]
    assert requests == []
