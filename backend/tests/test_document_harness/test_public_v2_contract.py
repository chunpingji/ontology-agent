"""Public progress separates reading coverage, work execution and fact proof."""

from typing import get_args

import pytest
from pydantic import ValidationError

from app.schemas.document_harness import (
    HarnessCandidateWork,
    HarnessGraph,
    HarnessProgress,
    HarnessVerification,
    Stage,
)


def progress():
    return {
        "completed_calls": 0,
        "candidate_count": 0,
        "fact_count": 0,
        "phase": "done",
        "reading_windows": {"total": 3, "saved": 3, "complete": 3, "incomplete": 0, "active": 0},
        "scope_complete": True,
        "reading": {"total_characters": 100, "processed_characters": 100, "complete_characters": 100, "complete": True},
        "work_counts": {"ready": 0, "waiting": 1, "pruned": 2, "done": 3, "failed": 0},
        "candidate_scope_limited": True,
        "rule_verified_count": 0,
        "llm_verified_count": 0,
        "stage_costs": [],
    }


def graph():
    return {
        "protocol": "document-harness-v2",
        "run_id": "00000000-0000-0000-0000-000000000001",
        "revision": 1,
        "status": "finished",
        "stage": "complete",
        "progress": progress(),
        **{key: [] for key in (
            "entities", "coreferences", "properties", "relations", "relation_groups",
            "interpretation_tasks", "observations", "targets", "candidate_work",
        )},
    }


def test_reading_complete_does_not_require_all_candidates_to_be_reviewed():
    result = HarnessGraph.model_validate(graph())
    assert result.progress.reading.complete
    assert result.progress.work_counts.waiting == 1
    assert result.progress.work_counts.pruned == 2
    assert result.progress.fact_count == 0


@pytest.mark.parametrize("field", [
    "reading", "work_counts", "candidate_scope_limited", "rule_verified_count",
    "llm_verified_count",
])
def test_v2_progress_fields_are_required(field):
    payload = progress()
    del payload[field]
    with pytest.raises(ValidationError):
        HarnessProgress.model_validate(payload)


def test_v2_requires_candidate_work_and_rejects_previous_protocol():
    payload = graph()
    del payload["candidate_work"]
    with pytest.raises(ValidationError):
        HarnessGraph.model_validate(payload)
    payload = graph()
    payload["protocol"] = "document-harness-v1"
    with pytest.raises(ValidationError):
        HarnessGraph.model_validate(payload)


@pytest.mark.parametrize("stage", get_args(Stage))
def test_each_public_stage_can_be_returned_while_paused(stage):
    payload = graph()
    payload.update(stage=stage, status="paused")
    assert HarnessGraph.model_validate(payload).stage == stage


@pytest.mark.parametrize("stage", ["assertion_alignment", "waiting_children", "drain_work"])
def test_internal_or_retired_stages_are_not_public(stage):
    payload = graph()
    payload["stage"] = stage
    with pytest.raises(ValidationError):
        HarnessGraph.model_validate(payload)


def test_reading_cannot_exceed_original_source_or_disagree_with_scope():
    payload = progress()
    payload["reading"]["processed_characters"] = 101
    with pytest.raises(ValidationError, match="reading_exceeds_source_length"):
        HarnessProgress.model_validate(payload)
    payload = progress()
    payload["scope_complete"] = False
    with pytest.raises(ValidationError, match="reading_scope_mismatch"):
        HarnessProgress.model_validate(payload)


def test_candidate_work_does_not_accept_a_fact_verdict_as_execution_status():
    payload = {
        "id": "work:1", "kind": "coreference_review", "subject_id": "mention:1",
        "object_ids": ["mention:2"], "predicate_iri": None,
        "status": "pruned", "reason_code": "weak_quota", "evidence": [],
    }
    assert HarnessCandidateWork.model_validate(payload).status == "pruned"
    payload["status"] = "rejected"
    with pytest.raises(ValidationError):
        HarnessCandidateWork.model_validate(payload)
    payload.update(status="waiting", kind="property_alignment")
    with pytest.raises(ValidationError):
        HarnessCandidateWork.model_validate(payload)


def test_public_verification_excludes_internal_fingerprint_and_permits_no_proof():
    payload = {
        "method": None, "rule_id": None, "rule_version": None, "semantic_verdict": None,
    }
    assert HarnessVerification.model_validate(payload).method is None
    payload["dependency_hash"] = "internal"
    with pytest.raises(ValidationError):
        HarnessVerification.model_validate(payload)


def test_costs_require_missing_measurement_count_and_preserve_unknown_tokens():
    payload = progress()
    payload["stage_costs"] = [{
        "stage": "relation_alignment", "calls": 2, "seconds": 1.5,
        "input_tokens": None, "output_tokens": 0,
    }]
    with pytest.raises(ValidationError):
        HarnessProgress.model_validate(payload)
    payload["stage_costs"][0]["unmeasured_attempts"] = 1
    result = HarnessProgress.model_validate(payload).stage_costs[0]
    assert result.input_tokens is None
    assert result.output_tokens == 0
    assert result.seconds == 1.5
    assert result.unmeasured_attempts == 1
