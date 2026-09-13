"""Public candidate accounting cannot turn search scope into pending model work."""

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.schemas.document_analysis import RunProgress
from app.services.document_analysis.application import _progress


def projected_progress(source):
    run = SimpleNamespace(
        progress=source, execution_status="finished", stop_reason="candidate_search_exhausted",
        event_head=8, artifact_revision=4,
    )
    return RunProgress.model_validate(_progress(run)).model_dump(mode="json")


def test_completed_candidates_do_not_inherit_unselected_search_work():
    payload = projected_progress({
        "candidate_policy": "sparse-candidates-v1",
        "completion": "policy_complete",
        "records_planned": 2,
        "records_examined": 2,
        "tasks_attempted": 2,
        "model_calls": 3,
        "retrieval_diagnostics": {
            "records_soft_pruned": 1000,
            "records_pending_disposition": 2000,
            "search_status": "search_complete",
        },
    })
    assert payload["records_planned"] == payload["records_examined"] == 2
    assert payload["records_unattempted"] == payload["records_incomplete"] == 0
    assert payload["candidate_policy"] == "sparse-candidates-v1"
    assert payload["completion"] == "policy_complete"
    assert payload["retrieval_diagnostics"]["records_pending_disposition"] == 2000


@pytest.mark.parametrize("unfinished", [
    {"records_planned": 1, "records_unattempted": 1},
    {"records_planned": 1, "records_incomplete": 1},
    {"pending_frontiers": 1},
    {"model_calls_unresolved": 1},
])
def test_policy_completion_rejects_real_unfinished_work(unfinished):
    with pytest.raises(ValidationError, match="cannot hide unfinished recognition work"):
        projected_progress({
            "candidate_policy": "sparse-candidates-v1",
            "completion": "policy_complete", **unfinished,
        })


def test_no_candidates_can_finish_without_claiming_full_document_coverage():
    payload = projected_progress({
        "candidate_policy": "sparse-candidates-v1", "completion": "policy_complete",
    })
    assert payload["records_planned"] == payload["model_calls"] == 0
    assert payload["completion"] == "policy_complete"
    assert payload["stop_reason"] == "candidate_search_exhausted"


def test_legacy_progress_keeps_its_original_accounting_and_wire_shape():
    payload = projected_progress({
        "records_planned": 100, "records_examined": 2, "records_unattempted": 98,
    })
    assert payload["records_planned"] == 100
    assert payload["records_unattempted"] == 98
    assert "candidate_policy" not in payload
    assert "completion" not in payload

    with pytest.raises(ValidationError, match="subset of unattempted"):
        projected_progress({"retrieval_diagnostics": {"records_soft_pruned": 1}})
