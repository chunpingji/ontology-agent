"""Proof attempts retain immutable payloads through real online finalization."""

from __future__ import annotations

import json
from collections import Counter

from sqlalchemy import select

from app.models.document_analysis import DocumentAnalysisRun, DocumentVerificationProof
from app.services.document_analysis import execution as execution_service
from app.services.extraction.ontology_guided import model_adapter
from app.services.extraction.ontology_guided.executor import TaskOutcome
from app.services.extraction.ontology_guided.model_adapter import LocalModelRecognitionAdapter
from tests.test_extraction.test_document_analysis_execution_recovery import (
    _claim,
    _create_pending_run,
)
from tests.test_extraction.test_semantic_graph_closure import _analysis, _respond, _run


def test_same_target_attempts_keep_their_own_decisions_at_online_finalization(
    client, db, analyst_headers, tmp_path, monkeypatch,
):
    class RetryingAdapter:
        model_identity = "proof-attempt-persistence-v1"

        def __init__(self):
            self.calls = Counter()

        def inspect(self, task, *_args):
            target = task.claim_lineage_id
            self.calls[target] += 1
            attempt = self.calls[target]
            verdict = "not_checked" if attempt == 1 else "unsupported"
            return TaskOutcome(
                semantic_outcome=verdict,
                complete=attempt > 1,
                reason_code="controlled-retry",
                reason="第一次未完成，有限重试后保留独立判定",
                model_calls=1,
                proof_payloads=[{
                    "proof_id": f"{target}:proof:{attempt}",
                    "proof_revision": 1,
                    "target_id": target,
                }],
                decision_payloads=[{
                    "decision_id": f"{target}:decision:{attempt}",
                    "target_id": target,
                    "verdict": verdict,
                }],
            )

    run_id = _create_pending_run(
        client, analyst_headers, tmp_path, monkeypatch, key="proof-attempt-finalization"
    )
    adapter = RetryingAdapter()
    monkeypatch.setattr(execution_service, "configured_model_adapter", lambda: adapter)
    store, token = _claim(db, run_id)
    execution_service._execute_claimed(db, store, store.get_owned(run_id, "analyst"), token)
    db.expire_all()
    run = db.get(DocumentAnalysisRun, run_id)
    assert run.execution_status == "finished", (run.error, run.progress)
    assert adapter.calls and all(count == 2 for count in adapter.calls.values())
    rows = list(db.scalars(select(DocumentVerificationProof).where(
        DocumentVerificationProof.recognition_run_id == run_id
    )))
    assert len(rows) == sum(adapter.calls.values())
    for row in rows:
        decisions = row.payload["decisions"]
        assert len(decisions) == 1
        assert decisions[0]["decision_id"] == row.proof_id.replace(":proof:", ":decision:")


def test_graph_persistence_never_combines_different_attempt_bundles_for_one_target(
    client, db, analyst_headers, tmp_path, monkeypatch,
):
    run_id = _create_pending_run(
        client, analyst_headers, tmp_path, monkeypatch, key="graph-proof-attempt-bundles"
    )
    store, token = _claim(db, run_id)
    graph = _run(_analysis(tmp_path), None).graph
    events = [
        ("task_outcome", {"outcome": {
            "proof_payloads": [{
                "proof_id": f"same-target-proof-{attempt}",
                "proof_revision": 1,
                "target_id": "same-target",
            }],
            "decision_payloads": [{
                "decision_id": f"same-target-decision-{attempt}",
                "target_id": "same-target",
                "verdict": "unsupported",
            }],
        }}) for attempt in (1, 2)
    ]
    # First persist each attempt independently, as committed batches do.
    for event in events:
        execution_service._persist_graph_objects(
            db, store, store.get_owned(run_id, "analyst"), token, graph, [event]
        )
        db.commit()
    # Later whole-graph replay must preserve both exact immutable payloads.
    execution_service._persist_graph_objects(
        db, store, store.get_owned(run_id, "analyst"), token, graph, events
    )
    db.commit()
    rows = list(db.scalars(select(DocumentVerificationProof).where(
        DocumentVerificationProof.recognition_run_id == run_id
    )))
    assert len(rows) == 2
    assert all(len(row.payload["decisions"]) == 1 for row in rows)


def test_model_attempt_and_changed_response_never_reuse_an_immutable_proof_id(
    tmp_path, monkeypatch,
):
    captured = []
    response_reason = ["first explanation"]

    def respond(_client, *, user, **_kwargs):
        response = _respond(json.loads(user))
        for verification in response.get("verifications", []):
            verification["reason"] = response_reason[0]
        return response

    monkeypatch.setattr(model_adapter, "chat_with_schema", respond)
    adapter = LocalModelRecognitionAdapter(object(), model_identity="immutable-attempt-test")

    class Capture:
        def inspect(self, task, context, predicate, menu):
            outcome = adapter.inspect(task, context, predicate, menu)
            if outcome.proof_payloads and not captured:
                captured.append((task, context, predicate, menu, outcome))
            return outcome

    _run(_analysis(tmp_path), Capture())
    task, context, predicate, menu, first = captured[0]
    retry = task.model_copy(update={"task_id": task.task_id + ":retry"})
    # These direct adapter calls start new attempts outside the completed
    # executor. Its ephemeral budget hook must not survive a data replay.
    replay_context = type(context).model_validate(context.model_dump())
    retry_context = replay_context.model_copy(update={
        "target": context.target.model_copy(update={"task_id": retry.task_id})
    })
    second = adapter.inspect(retry, retry_context, predicate, menu)
    assert first.proof_payloads[0]["target_id"] == second.proof_payloads[0]["target_id"]
    assert first.proof_payloads[0]["proof_id"] != second.proof_payloads[0]["proof_id"]
    assert first.decision_payloads[0]["attempt_id"] != second.decision_payloads[0]["attempt_id"]
    response_reason[0] = "changed explanation for the same source"
    third = adapter.inspect(retry, retry_context, predicate, menu)
    assert second.proof_payloads[0]["proof_id"] != third.proof_payloads[0]["proof_id"]
    assert second.decision_payloads[0]["decision_id"] != third.decision_payloads[0]["decision_id"]
