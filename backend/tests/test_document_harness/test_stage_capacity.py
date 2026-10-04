"""A complete answer must fit the exact batch the server actually requested."""

from copy import deepcopy

import jsonschema
import pytest

from app.services.document_harness import model
from app.services.document_harness.protocols import stage_schema, validate_paid_output
from app.services.document_harness.runtime import HarnessCallFailed, Repository, failure_reason
from tests.test_document_harness.test_fixed_stage_protocol import (
    judgment,
    property_choice,
    relation_proposal,
    type_choice,
)
from tests.test_extraction.test_document_harness_runtime import _requests, _run


def requested_batch(stage, count):
    ids = [f"C{i}" for i in range(1, count + 1)]
    options = {"source_ids": ["S1"]}
    extra = {}
    if stage == "type_alignment":
        field, choice = "entities", type_choice()
        payload = {"entities": [{"entity_id": key} for key in ids]}
        options.update(entity_ids=ids, class_iris=["urn:legal:Type"])
    elif stage == "property_alignment":
        field, choice = "properties", property_choice()
        payload = {"property_field_ids": ids}
        options.update(field_ids=ids, property_iris=["urn:legal:property"])
    elif stage == "relation_alignment":
        field, choice = "proposals", relation_proposal()
        items = [{"candidate_id": key, "object_ids": ["E2"]} for key in ids]
        payload = {"items": items}
        options.update(relation_items=items)
    elif stage == "coreference_review":
        field = "judgments"
        choice = {"verdict": "unresolved", "basis": "insufficient", "confidence": 0.5,
                  "evidence": [], "proof": [], "alias_binding": None,
                  "reason": "source does not establish identity"}
        payload = {"pairs": [{"pair_id": key} for key in ids]}
        options.update(candidate_ids=ids)
    else:
        field, choice = "judgments", judgment()
        payload = {"candidates": [{"id": key} for key in ids]}
        options.update(candidate_ids=ids)
        if stage == "evidence_review":
            extra = {"type_concerns": []}
    output = {field: {key: deepcopy(choice) for key in ids}, **extra}
    return payload, stage_schema(stage, **options), output, field


@pytest.mark.parametrize("stage,count", [
    ("type_alignment", 13), ("type_alignment", 34), ("type_alignment", 48),
    ("property_alignment", 33), ("relation_alignment", 7),
    ("entity_review", 13), ("evidence_review", 13), ("coreference_review", 7),
])
def test_full_requested_batch_has_no_smaller_hidden_answer_limit(stage, count):
    payload, schema, output, field = requested_batch(stage, count)
    jsonschema.validate(output, schema)
    validate_paid_output(stage, payload, output, schema=schema)

    missing = deepcopy(output)
    del missing[field]["C1"]
    extra = deepcopy(output)
    extra[field]["not_requested"] = deepcopy(output[field]["C1"])
    for invalid in (missing, extra):
        with pytest.raises(ValueError):
            validate_paid_output(stage, payload, invalid, schema=schema)
        with pytest.raises(ValueError, match="_set_mismatch"):
            validate_paid_output(stage, payload, invalid)


@pytest.mark.parametrize("choice", [
    type_choice(class_iri="urn:foreign:Type"), type_choice(evidence=["S99"]),
    type_choice(confidence=1.1), type_choice(reason=""),
])
def test_larger_type_batch_still_enforces_type_source_and_value_boundaries(choice):
    payload, schema, output, _ = requested_batch("type_alignment", 34)
    output["entities"]["C34"] = choice
    with pytest.raises(ValueError):
        validate_paid_output("type_alignment", payload, output, schema=schema)


@pytest.mark.parametrize("prepared", [False, True])
def test_34_entity_paid_answer_survives_pause_before_application(db, monkeypatch, prepared):
    run, token = _run(db)
    repo = Repository(db, run, token)
    payload, schema, output, _ = requested_batch("type_alignment", 34)
    calls = []
    monkeypatch.setattr(model, "call_model", lambda *args: calls.append(args) or {
        "output": output, "error": None, "raw_response": {"id": "paid-34"},
        "usage": {"input_tokens": 100, "output_tokens": 200},
    })
    try:
        if prepared:
            repo.save({"cursor": {"main": {"phase": "skeleton", "active_batches": {},
                                           "stage": "type_alignment"}}})
            batch = repo.prepare_batch("type_alignment", payload, schema, {
                "window_id": "w", "step": "type_alignment", "targets": [],
            })
            repo.submit_prepared(batch)
            outcomes = repo.collect_prepared([batch])
            assert outcomes == [(batch, output, None)]
            assert repo.call_result(batch["call_key"]) == output
        else:
            assert repo.invoke("type_alignment", payload, schema) == output
    finally:
        repo.close()
    resumed = Repository(db, run, token)
    try:
        assert resumed.invoke("type_alignment", payload, schema) == output
        record = next(iter(_requests(db, run).values()))
        assert record["status"] == "completed"
        assert record["attempts"] == 1
        assert len(calls) == 1
    finally:
        resumed.close()


@pytest.mark.parametrize("cause,expected", [
    ("ValidationError", "ValidationError"),
    ("type_alignment_answer_set_mismatch", "type_alignment_answer_set_mismatch"),
    ("raw user text and credentials", "HarnessCallFailed"),
])
def test_saved_call_failure_retains_only_its_safe_cause(cause, expected):
    assert failure_reason(HarnessCallFailed(cause)) == expected
