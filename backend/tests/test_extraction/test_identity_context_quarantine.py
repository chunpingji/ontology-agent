"""Unverified identity proposals stay in audit data, not trusted model identity.

The proposed_entities wrapper is an explicit exception: entity-type verification
must see the proposed key before it can accept or reject that key independently.
"""

import json
from copy import deepcopy

import pytest

from app.evaluation.quality_guided_variant import build_quality_guided_variant
from app.schemas.evidence import CandidateRef, ExtractionTask, TaskBudget, TypeVerification
from app.services.extraction.evidence_scope import build_scope
from app.services.extraction.extraction_tasks import GenericExtractionRunner
from app.services.extraction.hierarchical_context import (
    build_context,
    model_candidate,
    model_request,
)
from tests.test_extraction.test_hierarchical_context import TestTokenizer

PROPOSED_KEY = {"key_predicate": "urn:productIdentifier", "key_value": "TRIAL-SOURCE-01"}


def candidate_with_identity(subject_document, *, supported=False):
    ir, (source, _) = subject_document
    candidate = source.model_copy(deep=True)
    candidate.identity = deepcopy(PROPOSED_KEY)
    candidate.type_verification = TypeVerification(
        supported=True, identity_supported=supported,
        reason="Entity type is supported; the source-report key is not an instance identifier.",
    ) if supported is not None else None
    return ir, candidate


def payload_for(ir, candidate):
    task = ExtractionTask(
        task_id="identity-quarantine", task_kind="property", predicate_iri="urn:strength",
        subject=CandidateRef(candidate_id=candidate.candidate_id, revision=candidate.revision),
        scope=build_scope(ir, candidate),
        target_evidence_ids=[ir.evidence_units[1].evidence_id],
        budget=TaskBudget(max_input_tokens=100000),
    )
    envelope = build_context(task, ir, {candidate.candidate_id: candidate},
                             TestTokenizer(), model="identity-context-fixture")
    assert envelope.completion == "complete"
    return json.loads(envelope.serialized_input)


@pytest.mark.parametrize("stage", ["recall", "verify_binding", "verify_reference"])
def test_subject_context_removes_unverified_key_but_keeps_its_negative_verdict(
    subject_document, stage,
):
    ir, candidate = candidate_with_identity(subject_document)
    before = candidate.model_dump(mode="json")
    payload = payload_for(ir, candidate)
    request = json.loads(model_request(payload, stage))
    subject = json.loads(request["context"])["subjects"][candidate.candidate_id]
    assert "key_predicate" not in subject.get("identity", {})
    assert "key_value" not in subject.get("identity", {})
    assert subject["type_verification"]["identity_supported"] is False
    # The saved/provenance audit view must retain both proposal and actual verdict.
    assert candidate.model_dump(mode="json") == before
    assert payload["subjects"][candidate.candidate_id]["identity"] == PROPOSED_KEY
    assert payload["subjects"][candidate.candidate_id]["type_verification"][
        "identity_supported"] is False


@pytest.mark.parametrize("supported", [False, None])
@pytest.mark.parametrize("shared", [False, True])
def test_candidate_payload_cannot_promote_unverified_or_unchecked_keys(
    subject_document, supported, shared,
):
    ir, candidate = candidate_with_identity(subject_document, supported=supported)
    raw = candidate.model_dump(mode="json")
    before = deepcopy(raw)
    supplied = {"shared_candidates": [raw], "instruction": "Check independently"} if shared else raw
    request = json.loads(model_request(payload_for(ir, candidate), "verify_binding", supplied))
    projected = request["candidate"]["shared_candidates"][0] if shared else request["candidate"]
    assert "key_predicate" not in projected.get("identity", {})
    assert "key_value" not in projected.get("identity", {})
    if supported is False:
        assert projected["type_verification"]["identity_supported"] is False
    assert raw == before


def test_initial_type_verification_still_receives_proposed_identity_key(subject_document):
    ir, candidate = candidate_with_identity(subject_document, supported=None)
    raw = candidate.model_dump(mode="json")
    before = deepcopy(raw)
    request = json.loads(model_request(payload_for(ir, candidate), "verify_entity_types",
                                       {"proposed_entities": [raw]}))
    proposed = request["candidate"]["proposed_entities"][0]
    assert proposed["identity"] == PROPOSED_KEY
    assert raw == before


def test_verified_key_and_trusted_instance_are_preserved_in_both_entries(subject_document):
    ir, candidate = candidate_with_identity(subject_document, supported=True)
    candidate.identity = {**candidate.identity, "instance_iri": "urn:verified:instance"}
    request = json.loads(model_request(payload_for(ir, candidate), "verify_reference",
                                       candidate.model_dump(mode="json")))
    subject = json.loads(request["context"])["subjects"][candidate.candidate_id]
    assert subject["identity"] == candidate.identity
    assert request["candidate"]["identity"] == candidate.identity


def test_quarantine_preserves_root_instance_and_other_metadata_without_a_verdict(subject_document):
    ir, candidate = candidate_with_identity(subject_document, supported=None)
    metadata = {"document_root": ir.document_hash, "classification_source": "job_metadata",
                "instance_iri": "urn:existing:instance"}
    candidate.identity = deepcopy(metadata)
    request = json.loads(model_request(payload_for(ir, candidate), "recall",
                                       candidate.model_dump(mode="json")))
    subject = json.loads(request["context"])["subjects"][candidate.candidate_id]
    assert subject["identity"] == metadata
    assert request["candidate"]["identity"] == metadata


def test_negative_key_verdict_does_not_erase_independent_instance_or_root_metadata(
    subject_document,
):
    ir, candidate = candidate_with_identity(subject_document)
    metadata = {"instance_iri": "urn:existing:instance", "document_root": ir.document_hash,
                "classification_source": "job_metadata"}
    candidate.identity = {**candidate.identity, **metadata}
    projected = model_candidate(candidate.model_dump(mode="json"))
    assert projected["identity"] == metadata


def routing_fixture(ir, requests):
    predicate = {"iri": "urn:strength", "label": "Strength", "datatype": "decimal"}
    schema = {"urn:Drug": {"label": "Drug", "properties": [predicate], "relationships": []}}

    def model(system, user, response_schema, budget):
        payload = json.loads(user)
        requests.append(payload)
        return {"records": [{"record_id": key, "predicate_ids": []}
                            for key in payload["records"]]}

    base = GenericExtractionRunner(
        schema, TestTokenizer(), model, model_identity="identity-router-fixture",
        budget=TaskBudget(max_input_tokens=100000, max_output_tokens=4096),
    )
    runner = build_quality_guided_variant(base, ir)

    def make_task(kind, regions, **kwargs):
        return ExtractionTask(
            task_id="routing-task", task_kind=kind, target_ranges=regions,
            target_evidence_ids=[region.evidence_id for region in regions],
            budget=runner.budget, **kwargs,
        )

    def route(candidate):
        return runner._routes(candidate, [("property", predicate)], make_task)

    return route


@pytest.mark.parametrize("supported", [False, None, True])
def test_quality_router_quarantines_only_unverified_keys_and_keeps_metadata(
    subject_document, supported,
):
    ir, candidate = candidate_with_identity(subject_document, supported=supported)
    metadata = {"instance_iri": "urn:existing:instance", "document_root": ir.document_hash}
    candidate.identity = {**candidate.identity, **metadata}
    before = candidate.model_dump(mode="json")
    requests = []
    routing_fixture(ir, requests)(candidate)
    assert requests
    for request in requests:
        subject = request["subject"]
        assert subject["identity"] == ({**PROPOSED_KEY, **metadata} if supported else metadata)
        assert subject["type_verification"] == (
            candidate.type_verification.model_dump(mode="json") if supported is not None else None
        )
    assert candidate.model_dump(mode="json") == before


def test_quality_route_cache_includes_verdict_even_with_unchanged_id_revision_and_key(
    subject_document,
):
    ir, candidate = candidate_with_identity(subject_document, supported=None)
    original_identity = deepcopy(candidate.identity)
    original_revision = candidate.revision
    requests = []
    route = routing_fixture(ir, requests)
    route(candidate)
    first_count = len(requests)
    route(candidate)
    assert len(requests) == first_count
    candidate.type_verification = TypeVerification(
        supported=True, identity_supported=False, reason="Identifier is not supported",
    )
    route(candidate)
    second_count = len(requests)
    assert second_count == 2 * first_count
    assert requests[-1]["subject"]["identity"] == {}
    candidate.type_verification = TypeVerification(
        supported=True, identity_supported=True, reason="Identity independently supported",
    )
    route(candidate)
    assert len(requests) == 3 * first_count
    assert requests[-1]["subject"]["identity"] == PROPOSED_KEY
    route(candidate)
    assert len(requests) == 3 * first_count
    assert candidate.revision == original_revision
    assert candidate.identity == original_identity
