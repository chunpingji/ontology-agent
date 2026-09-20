"""Human repair resumes record-discovered attributes on their actual isolated owner."""

from copy import deepcopy
from uuid import UUID, uuid4

from app.models.document_analysis import DocumentRunCandidate
from app.models.document_analysis_review import DocumentPropertyRepair, DocumentPropertyReview
from app.services.document_analysis import current_state
from app.services.extraction.evidence_identity import evidence_hash
from app.services.extraction.ontology_guided.contracts import SubjectRef
from app.services.extraction.ontology_guided.record_discovery import RECORD_PROTOCOL
from tests.test_extraction.test_record_executor import VALUE, A, record_setup

pytest_plugins = ["tests.test_extraction.test_tool_engine_resume"]


def test_record_property_review_repairs_isolated_entity_using_actual_property_task(
    tmp_path, monkeypatch, current_run,
):
    def controlled_correction(view, payload, *, record):
        if view["stage"] != "discovery":
            return payload
        if record:
            # A model's wrong same-record value motivates the human review.
            # Both strings are real source text; the repair must establish ownership again.
            next(prop for prop in payload["properties"] if prop["local_id"] == "pA")[
                "value_quote"
            ]["text"] = "B"
        elif view["predicate_iri"] == VALUE:
            evidence_id = next(ref["evidence_id"] for ref in view["evidence_refs"]
                               if ref["fact_eligible"])

            def quote(text):
                return dict(evidence_id=evidence_id, text=text, context_text=None)

            payload["properties"] = [dict(
                local_id="corrected-model", subject_id=view["subject_ref"]["id"],
                predicate_iri=VALUE, value_quote=quote("A"), field_support=[quote("型号")],
                unit_support=[], bridge_kind="explicit_assertion", bridge_ref_ids=[],
                qualifiers=dict(polarity="affirmed", modality="asserted",
                                condition_support=[], scope_qualifiers=[]),
            )]
        return payload

    args, executor, requests, hooks = record_setup(
        tmp_path, monkeypatch, current_run, empty_relations=True, transform=controlled_correction,
    )
    initial = executor().run(**args, **hooks)
    owner = next(node for node in initial.graph.nodes if node.class_iri == A)
    candidate = next(prop for prop in initial.graph.properties
                     if prop.subject_ref.id == owner.entity_id)
    assert candidate.raw_value == "B" and not initial.graph.edges
    store, run, _token = current_run
    rows = current_state.restore_work(store, run, run.run_fingerprint).work_state
    control = rows["control"]["current"]
    calls = current_state.restore_calls(store, run, run.run_fingerprint)
    original_protocol = next(protocol for protocol in calls["protocols"].values()
                             if protocol["version"] == RECORD_PROTOCOL)
    frozen_original = deepcopy(original_protocol)
    task = original_protocol["task"]
    assert "subject" not in task and "predicate_iri" not in task
    paid_before = len(requests)
    subject = SubjectRef(entity_id=owner.entity_id, revision=owner.revision,
                         class_iri=owner.class_iri)
    review = dict(
        review_id=str(uuid4()), review_revision=1,
        candidate_id=candidate.candidate_id, candidate_revision=candidate.revision,
        subject_ref=candidate.subject_ref.model_dump(mode="json"),
        subject=subject.model_dump(mode="json"), predicate_iri=candidate.predicate_iri,
        original_task=task, record_ids=[task["record_id"]],
        after_outcomes=control["completed_tasks"],
        decision="rejected", reason_code="incorrect_value", reason="型号应取装置甲的A，B属于部件。",
    )
    operation = dict(
        operation_id=str(uuid4()), review_id=review["review_id"], target=review,
        status="queued", after_outcomes=control["completed_tasks"], max_tasks=1, max_model_calls=2,
    )
    store.db.add(DocumentPropertyReview(
        review_id=UUID(review["review_id"]), recognition_run_id=run.recognition_run_id,
        owner_id=run.owner_id, actor_role="expert", request_key="record-review",
        request_hash=evidence_hash(review), candidate_id=candidate.candidate_id,
        candidate_revision=candidate.revision, revision=1, decision="rejected",
        graph_snapshot_id="record-expert-test", payload=review, receipt={},
    ))
    store.db.flush()
    store.db.add(DocumentPropertyRepair(
        operation_id=UUID(operation["operation_id"]), recognition_run_id=run.recognition_run_id,
        review_id=UUID(review["review_id"]), request_key="record-repair",
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
    assert len(requests) == paid_before + 2, repaired.diagnostics
    assert all(len(view["members"]) == 1 for view in requests[paid_before:])
    assert all(view["members"][0]["subject_ref"] == candidate.subject_ref.model_dump(mode="json")
               and view["members"][0]["predicate_iri"] == VALUE
               for view in requests[paid_before:])
    after_calls = current_state.restore_calls(store, run, run.run_fingerprint)
    assert after_calls["protocols"][task["claim_lineage_id"]] == frozen_original
    repair_tasks = [member for protocol in after_calls["protocols"].values()
                    for member in protocol.get("work_unit", {}).get("members", [])
                    if member["retry_kind"] == f"expert_review:{operation['operation_id']}"]
    assert len(repair_tasks) == 1
    repair_task = repair_tasks[0]
    assert repair_task["subject"] == subject.model_dump(mode="json")
    assert repair_task["predicate_kind"] == "property" and repair_task["predicate_iri"] == VALUE
    assert repair_task["record_id"] == task["record_id"] and repair_task["scope"] == task["scope"]
    assert repair_task["claim_lineage_id"] != task["claim_lineage_id"]
    assert not repaired.graph.edges
    rejected = store.db.get(DocumentRunCandidate, (
        run.recognition_run_id, candidate.candidate_id, candidate.revision + 1,
    ))
    assert rejected is not None and rejected.payload["independent_review"] == "rejected"
    assert rejected.payload["raw_value"] == "B"
    assert all(prop.candidate_id != candidate.candidate_id for prop in repaired.graph.properties)
    assert any(prop.subject_ref == candidate.subject_ref and prop.raw_value == "A"
               and prop.policy_eligible and prop.independent_review != "rejected"
               for prop in repaired.graph.properties), repaired.events
    summary = repaired.evidence_repair_summary["expert_review"]["operations"][
        operation["operation_id"]
    ]
    assert summary["status"] == "completed", summary
    assert summary["result"]["tasks_attempted"] == 1 and summary["result"]["model_calls"] == 2
    saved = store.db.get(DocumentPropertyRepair, UUID(operation["operation_id"]))
    assert saved.status == "completed" and saved.result["model_calls"] == 2
