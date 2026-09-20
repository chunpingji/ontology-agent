"""Expert repairs retain their original scope in the batched singleton path."""

from uuid import UUID, uuid4

from app.models.document_analysis_review import DocumentPropertyRepair, DocumentPropertyReview
from app.services.document_analysis import current_state
from app.services.extraction.evidence_identity import evidence_hash
from tests.test_extraction.test_batch_executor import setup_batch

pytest_plugins = ["tests.test_extraction.test_tool_engine_resume"]


def test_batch_expert_repair_preserves_scope_and_reports_paid_singleton_calls(
    tmp_path, monkeypatch, current_run,
):
    args, executor, requests, hooks = setup_batch(tmp_path, monkeypatch, current_run)
    original = executor().run(**args, **hooks)
    store, run, _token = current_run
    rows = current_state.restore_work(store, run, run.run_fingerprint).work_state
    control = rows["control"]["current"]
    calls = current_state.restore_calls(store, run, run.run_fingerprint)
    candidate = original.graph.properties[0]
    task = next(member for protocol in calls["protocols"].values()
                for member in protocol["work_unit"]["members"]
                if member["predicate_iri"] == candidate.predicate_iri)
    review = {
        "review_id": str(uuid4()), "review_revision": 1,
        "candidate_id": candidate.candidate_id, "candidate_revision": candidate.revision,
        "subject_ref": candidate.subject_ref.model_dump(mode="json"),
        "predicate_iri": candidate.predicate_iri, "original_task": task,
        "record_ids": [task["record_id"]], "after_outcomes": control["completed_tasks"],
        "decision": "rejected", "reason_code": "incorrect_value",
        "reason": "请根据同一原文重新核对属性值。",
    }
    operation = {
        "operation_id": str(uuid4()), "review_id": review["review_id"],
        "target": review, "status": "queued", "after_outcomes": control["completed_tasks"],
        "max_tasks": 1, "max_model_calls": 2,
    }
    store.db.add(DocumentPropertyReview(
        review_id=UUID(review["review_id"]), recognition_run_id=run.recognition_run_id,
        owner_id=run.owner_id, actor_role="expert", request_key="batch-review",
        request_hash=evidence_hash(review), candidate_id=candidate.candidate_id,
        candidate_revision=candidate.revision, revision=1, decision="rejected",
        graph_snapshot_id="batch-expert-test", payload=review, receipt={},
    ))
    store.db.flush()
    store.db.add(DocumentPropertyRepair(
        operation_id=UUID(operation["operation_id"]), recognition_run_id=run.recognition_run_id,
        review_id=UUID(review["review_id"]), request_key="batch-repair",
        request_hash=evidence_hash(operation), base_work_version=run.work_version,
        status="queued", payload=operation, result={}, receipt={},
    ))
    store.db.commit()
    repaired = executor().run(
        **args, **hooks, model_call_state=calls,
        resume_state={"work_state": rows, "frontier": control["frontier_policy"],
                      "diagnostics": control["diagnostics"]},
        property_reviews=[review], property_repairs=[operation], repair_only=True,
    )
    assert len(requests) == 4
    assert all(len(request["members"]) == 1 for request in requests[-2:])
    assert all(request["members"][0]["scope"] == task["scope"] for request in requests[-2:])
    summary = repaired.evidence_repair_summary["expert_review"]["operations"][
        operation["operation_id"]
    ]
    assert summary["result"]["tasks_attempted"] == 1
    assert summary["result"]["model_calls"] == 2
    assert summary["status"] == "unresolved"  # An identical source value remains rejected.
    assert not any("adapter_failure" in reason for reason in repaired.diagnostics)
    saved_operation = store.db.get(DocumentPropertyRepair, UUID(operation["operation_id"]))
    assert saved_operation.status == "unresolved"
    assert saved_operation.result["model_calls"] == 2
    assert current_state.restore_calls(store, run, run.run_fingerprint)["reservation_sequence"] == 4
