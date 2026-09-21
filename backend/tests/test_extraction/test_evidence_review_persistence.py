"""A saved pending view can advance only after the same frozen source review."""

from copy import deepcopy

import pytest

from app.services.document_analysis import current_state
from app.services.extraction.ontology_guided.current_work import TOOL_PROTOCOL_VERSION


@pytest.mark.parametrize("violation", [
    None, "missing_policy", "pending_request", "terminal_before", "prior_proof",
    "changed_discovery", "clear_outcome", "foreign_owner", "pending_again", "wrong_context",
])
def test_pending_completion_exception_is_narrow(monkeypatch, violation):
    policy = {"record_discovery": {"graph_phase": "evidence_review"}}
    old = dict(
        version=TOOL_PROTOCOL_VERSION, lineage_id="lineage", stage="finalize",
        assertion_generation=1, evidence_revision=1, discovery_ref="discovery",
        verification_ref="verification", outcome_ref="pending", pending_request=None,
        context_hash="frozen-context",
    )
    protocol = {**old, "outcome_ref": "completed"}
    pending = dict(complete=False, semantic_outcome="not_checked", properties=[{
        "decision_status": "not_checked", "proof_ref": None, "decision_refs": [],
        "policy_eligible": False,
    }])
    verification = {"context_hash": "frozen-context"}
    changes = {"completed": {
        "lineage_id": "lineage", "field": "outcome", "value": {"complete": True},
    }}
    if violation == "missing_policy":
        policy = {}
    elif violation == "pending_request":
        old["pending_request"] = {"attempt": 2}
    elif violation == "terminal_before":
        pending["complete"] = True
    elif violation == "prior_proof":
        pending["properties"][0]["proof_ref"] = {"id": "proof", "revision": 1}
    elif violation == "changed_discovery":
        protocol["discovery_ref"] = "replacement-discovery"
    elif violation == "clear_outcome":
        protocol["outcome_ref"] = None
    elif violation == "foreign_owner":
        changes["completed"]["lineage_id"] = "foreign"
    elif violation == "pending_again":
        changes["completed"]["value"]["complete"] = False
    elif violation == "wrong_context":
        verification["context_hash"] = "foreign-context"
    monkeypatch.setattr(current_state, "performance_policy", lambda *_: policy)
    values = {"pending": pending, "verification": verification}
    loaded = []

    def load(_store, _run, lineage, ref, field):
        assert lineage == "lineage"
        loaded.append((ref, field))
        return deepcopy(values[ref])

    monkeypatch.setattr(current_state, "load_protocol_result", load)
    assert current_state._pending_review_completion(
        object(), object(), protocol, old, changes,
    ) is (violation is None)
    if violation is None:
        assert loaded == [("pending", "outcome"), ("verification", "verification")]
