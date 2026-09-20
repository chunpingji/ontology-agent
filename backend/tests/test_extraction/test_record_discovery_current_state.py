"""Record requests retain their single owner and publish candidate/queue changes together."""

import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Barrier
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import Session

from app.models.document_analysis import DocumentRunCandidate, DocumentRunRequest, DocumentRunResult
from app.services.document_analysis import current_state, execution
from app.services.document_analysis.run_store import (
    DocumentAnalysisRunStore,
    FenceViolation,
    HeadConflict,
    RunNotFound,
)
from app.services.extraction.ontology_guided.contracts import GraphNode, GraphSnapshot, VersionedRef
from app.services.extraction.ontology_guided.current_work import (
    call_request_key,
    protocol_result_ref,
)
from app.services.extraction.ontology_guided.executor import TaskOutcome
from app.services.extraction.ontology_guided.record_discovery import (
    RECORD_PROTOCOL,
    RecordDiscoveryTarget,
    RecordDiscoveryTask,
)
from tests.test_extraction.test_batch_current_state import batch_protocol
from tests.test_extraction.test_document_run_execution_postgresql import pg_engine as pg_engine
from tests.test_extraction.test_document_state_artifacts import seed
from tests.test_extraction.test_layered_recognition import arguments
from tests.test_extraction.test_tool_engine_graph_contracts import target_values
from tests.test_extraction.test_tool_engine_resume import protocol_state

pytest_plugins = ["tests.test_extraction.test_tool_engine_resume"]


def record_protocol():
    task = RecordDiscoveryTask.create(
        run_fingerprint="responses-current-fingerprint", record_id="record-1",
        schema_card_id="c" * 64, analysis_scope_ref="scope-1", dependency_hash="f" * 64,
    )
    document = target_values()["document_context"]
    document.document_hash = "d" * 64
    target = RecordDiscoveryTarget(
        target_id="record-target", task_id=task.task_id, document_context=document,
        schema_card_id=task.schema_card_id, analysis_scope_ref=task.analysis_scope_ref,
        ontology_hash="e" * 64, source_scope_hash="a" * 64, context_hash="b" * 64,
    )
    return {
        **protocol_state(), "version": RECORD_PROTOCOL, "lineage_id": task.claim_lineage_id,
        "task": task.model_dump(mode="json"), "base_target": target.model_dump(mode="json"),
        "scope_id": task.scope.scope_id,
    }


def persist(current_run, protocol, *, receipts=(), results=None, work_changes=None):
    store, run, token = current_run
    lineage = protocol["lineage_id"]
    return current_state.persist_calls(store, run, token, run.run_fingerprint, {
        "current_calls": 1, "version": 3, "recognition_run_id": str(run.recognition_run_id),
        "run_fingerprint": run.run_fingerprint, "lineage_calls": {},
        "protocols": {json.dumps(lineage): {"key": lineage, "value": deepcopy(protocol),
                                           "position": 0}},
        "reservations": list(receipts), "reservation_sequence": protocol["request_attempt"],
        "result_changes": results or {}, "work_changes": work_changes,
        "expected_work_version": run.work_version,
    })


def reservation(current_run, protocol):
    task = protocol["task"]
    protocol["request_attempt"] += 1
    attempt = protocol["request_attempt"]
    protocol["pending_request"] = {
        "attempt": attempt, "stage": protocol["stage"], "request_hash": "f" * 64,
        "reservation_key": call_request_key({"lineage_id": protocol["lineage_id"],
                                              "protocol_attempt": attempt}),
        "allowed_tool_names": [],
    }
    return {
        "sequence": attempt, "task_id": task["task_id"], "record_task_id": task["task_id"],
        "record_id": task["record_id"], "schema_card_id": task["schema_card_id"],
        "lineage_id": protocol["lineage_id"], "stage": "ontology_guided_" + protocol["stage"],
        "ordinal": attempt, "protocol_attempt": attempt, "input_hash": "f" * 64,
        "run_fingerprint": current_run[1].run_fingerprint,
    }


def confirm(current_run, protocol):
    pending = protocol["pending_request"]
    value = {
        "attempt": pending["attempt"], "stage": pending["stage"],
        "input_hash": pending["request_hash"], "allowed_tool_names": [],
        "response_id": "record-response", "output_items": [], "response_status": "completed",
        "incomplete_details": None, "error": None, "usage": {"total_tokens": 12},
    }
    ref = protocol_result_ref(protocol["lineage_id"], "model_turn", value)
    protocol["turn_refs"].append(ref)
    protocol["completed_attempts"].append(value["attempt"])
    protocol["pending_request"] = None
    persist(current_run, protocol, results={ref: {
        "lineage_id": protocol["lineage_id"], "field": "model_turn", "value": value,
    }})
    return ref


def test_record_paid_response_and_scalar_cost_survive_cold_restore(current_run):
    protocol = record_protocol()
    persist(current_run, protocol)
    receipt = reservation(current_run, protocol)
    persist(current_run, protocol, receipts=[receipt])
    store, run, _ = current_run
    unknown = current_state.restore_calls(store, run, run.run_fingerprint)
    assert unknown["lineage_calls"] == {protocol["lineage_id"]: 1}
    assert unknown["reservations"] == [receipt]
    assert run.progress["model_calls_unresolved"] == 1
    assert not {"member_task_ids", "member_lineage_ids", "subject_ref"} & set(receipt)
    response_ref = confirm(current_run, protocol)
    store.db.expire_all()
    restored = current_state.restore_calls(store, run, run.run_fingerprint)
    assert restored["protocols"][protocol["lineage_id"]] == protocol
    assert restored["lineage_calls"] == {protocol["lineage_id"]: 1}
    assert run.progress["model_calls"] == run.progress["model_calls_reserved"] == 1
    assert run.progress["model_calls_unresolved"] == 0
    result = current_state.load_protocol_result(
        store, run, protocol["lineage_id"], response_ref, "model_turn",
    )
    assert result["response_id"] == "record-response"
    with pytest.raises(HeadConflict, match="reference mismatch"):
        current_state.load_protocol_result(store, run, "other-record", response_ref, "model_turn")


@pytest.mark.parametrize("field,value", [
    ("record_task_id", "other-task"), ("task_id", "other-task"),
    ("record_id", "other-record"), ("schema_card_id", "another-card"),
    ("subject_ref", {"id": "invented", "revision": 1}),
    ("member_task_ids", ["invented"]), ("input_hash", "e" * 64),
])
def test_record_request_cannot_borrow_another_task_or_batch_owner(current_run, field, value):
    protocol = record_protocol()
    persist(current_run, protocol)
    receipt = reservation(current_run, protocol)
    receipt[field] = value
    with pytest.raises((HeadConflict, execution.CheckpointMismatch)):
        persist(current_run, protocol, receipts=[receipt])
    store, run, _ = current_run
    assert current_state.read_rows(store, run, DocumentRunRequest, prefix="calls:requests") == {}
    restored = current_state.restore_calls(store, run, run.run_fingerprint)
    assert restored["reservation_sequence"] == 0


def test_record_and_real_batch_protocols_share_v3_ledger_without_fake_members(current_run):
    protocol = record_protocol()
    persist(current_run, protocol)
    receipt = reservation(current_run, protocol)
    persist(current_run, protocol, receipts=[receipt])
    store, run, _ = current_run
    restored = current_state.restore_calls(store, run, run.run_fingerprint)
    batch = batch_protocol()
    restored["protocols"][batch["lineage_id"]] = batch
    execution._validate_model_call_state({key: restored[key] for key in (
        "version", "recognition_run_id", "run_fingerprint", "lineage_calls", "reservations",
        "protocols",
    )})
    assert restored["lineage_calls"] == {protocol["lineage_id"]: 1}


def test_record_initial_task_and_queue_registration_are_atomic(current_run, monkeypatch):
    protocol = record_protocol()
    store, run, _ = current_run
    changes = {"control": {"current": {"active_unit_ref": protocol["lineage_id"]}},
               "record_discovery": {"record-1": {"status": "active"}}}
    original = current_state.put_rows

    def fail_protocol(*args, **kwargs):
        if args[3] == "calls:protocols":
            raise RuntimeError("record registration interrupted")
        return original(*args, **kwargs)

    monkeypatch.setattr(current_state, "put_rows", fail_protocol)
    with pytest.raises(RuntimeError, match="registration interrupted"):
        persist(current_run, protocol, work_changes=changes)
    assert run.work_version == 0
    assert current_state.get_row(store, run, "work:control") is None
    assert current_state.get_row(store, run, "work:record_discovery", "record-1") is None


def publication(current_run, protocol, monkeypatch):
    store, run, _ = current_run
    node = GraphNode(entity_id="record-entity", revision=1, class_iri="urn:Entity",
                     class_label="Entity", label="A")
    outcome = TaskOutcome(semantic_outcome="supported", reason_code="record_verified",
                          reason="record and entity verified", nodes=[node])
    finalized = deepcopy(protocol)
    finalized.update(stage="finalize", active_instructions="", stage_input_items=[],
                     turn_refs=[], completed_tool_results=[])
    value = outcome.model_dump(mode="json")
    ref = protocol_result_ref(protocol["lineage_id"], "outcome", value)
    finalized["outcome_ref"] = ref
    graph = GraphSnapshot(
        recognition_run_id=str(run.recognition_run_id), run_revision=run.revision,
        event_head=run.event_head, root_ref=VersionedRef(id="root", revision=1),
        artifact_status="partial", nodes=[node], generated_from_hash="a" * 64,
        projection="all", projection_policy="ontology-tool-graph-v1",
    )
    work_changes = {
        "nodes": {json.dumps(node.entity_id): {
            "key": node.entity_id, "position": 0, "value": node.model_dump(mode="json"),
        }},
        "record_discovery": {"record-1": {"status": "examined"}},
        "scheduler:record_relations": {"relation-task": {"entity_ref": "record-entity@1"}},
    }
    batch = SimpleNamespace(
        batch_id="record-batch", task=RecordDiscoveryTask.model_validate(protocol["task"]),
        work_unit=None, member_outcomes={}, outcome=outcome, graph=graph,
        protocol_state_changes={protocol["lineage_id"]: finalized},
        protocol_result_changes={ref: {"lineage_id": protocol["lineage_id"],
                                      "field": "outcome", "value": value}},
        work_changes=work_changes, dependency_index={}, evidence_repair_summary={},
    )

    def display(*args, graph, **kwargs):
        return "record-display", ({"public_graph": {"current": {
            "graph": graph.model_dump(mode="json"),
        }}}, {})

    monkeypatch.setattr(current_state, "display_payload", display)
    return batch, ref


def publish(current_run, batch):
    store, run, token = current_run
    return current_state.persist_batch(
        store, run, token, batch=batch, fingerprint=run.run_fingerprint,
        ontology=None, ir=None, metadata=None, index=None, expected_version=run.work_version,
    )


def test_record_candidate_outcome_and_following_queue_commit_or_rollback_together(
    current_run, monkeypatch,
):
    protocol = record_protocol()
    persist(current_run, protocol)
    receipt = reservation(current_run, protocol)
    persist(current_run, protocol, receipts=[receipt])
    response_ref = confirm(current_run, protocol)
    store, run, _ = current_run
    batch, outcome_ref = publication(current_run, protocol, monkeypatch)
    original = current_state.publish_display

    def fail_after_work(*args, **kwargs):
        raise RuntimeError("record publication interrupted")

    monkeypatch.setattr(current_state, "publish_display", fail_after_work)
    with pytest.raises(RuntimeError, match="publication interrupted"):
        publish(current_run, batch)
    assert run.work_version == 0
    assert store.db.get(DocumentRunCandidate, (run.recognition_run_id, "record-entity", 1)) is None
    assert current_state.get_row(store, run, "work:scheduler:record_relations", "relation-task") \
        is None
    assert current_state.get_row(store, run, "calls:results", outcome_ref,
                                 model=DocumentRunResult) is None
    assert current_state.load_protocol_result(
        store, run, protocol["lineage_id"], response_ref, "model_turn",
    )["response_id"] == "record-response"
    monkeypatch.setattr(current_state, "publish_display", original)
    assert publish(current_run, batch) == 1
    assert publish(current_run, batch) == 1
    assert current_state.get_row(store, run, "work:scheduler:record_relations", "relation-task") \
        == {"entity_ref": "record-entity@1"}
    row = current_state.get_row(store, run, "work:nodes", json.dumps("record-entity"))
    assert row["candidate_ref"] == {"id": "record-entity", "revision": 1, "kind": "entity"}
    assert "value" not in row
    assert current_state.restore_calls(store, run, run.run_fingerprint)["protocols"][
        protocol["lineage_id"]
    ]["outcome_ref"] == outcome_ref


def test_record_publication_rejects_outcome_different_from_confirmed_owner(
    current_run, monkeypatch,
):
    protocol = record_protocol()
    persist(current_run, protocol)
    receipt = reservation(current_run, protocol)
    persist(current_run, protocol, receipts=[receipt])
    confirm(current_run, protocol)
    batch, _ = publication(current_run, protocol, monkeypatch)
    batch.outcome = batch.outcome.model_copy(update={"reason": "changed after finalization"})
    with pytest.raises(HeadConflict, match="differs from its finalized result"):
        publish(current_run, batch)
    assert current_run[1].work_version == 0


def review_marker(current_run, monkeypatch):
    protocol = record_protocol()
    persist(current_run, protocol)
    receipt = reservation(current_run, protocol)
    persist(current_run, protocol, receipts=[receipt])
    confirm(current_run, protocol)
    original, _ = publication(current_run, protocol, monkeypatch)
    publish(current_run, original)
    review = deepcopy(original)
    review.batch_id = "record-review-boundary"
    review.outcome = TaskOutcome(
        semantic_outcome="not_checked", complete=False, reason_code="expert_review_boundary",
        reason="人工审核操作边界；不增加原文检查或模型调用",
    )
    review.protocol_state_changes = {}
    review.protocol_result_changes = {}
    review.work_changes = {"control": {"current": {"review_applied": True}}}
    return review, original


def test_empty_record_review_boundary_preserves_original_discovery_result(current_run, monkeypatch):
    review, original = review_marker(current_run, monkeypatch)
    store, run, _ = current_run
    before = current_state.restore_calls(store, run, run.run_fingerprint)
    assert publish(current_run, review) == 2
    assert publish(current_run, review) == 2
    assert current_state.restore_calls(store, run, run.run_fingerprint) == before
    assert current_state.get_row(store, run, "work:control") == {"review_applied": True}
    assert before["protocols"][review.task.claim_lineage_id]["outcome_ref"] == (
        original.protocol_state_changes[review.task.claim_lineage_id]["outcome_ref"]
    )


@pytest.mark.parametrize("field,value", [
    ("nodes", ["new-entity"]), ("properties", ["new-property"]),
    ("edges", ["new-edge"]), ("relationship_groups", ["new-group"]),
    ("proof_payloads", [{"proof_id": "new-proof"}]),
    ("decision_payloads", [{"target_id": "new-decision"}]),
    ("reference_resolutions", [{"binding_ref": "new-binding"}]),
    ("controller_checks", {"verified": 1}), ("model_calls", 1), ("complete", True),
    ("semantic_outcome", "supported"),
])
def test_record_review_marker_cannot_bypass_discovery_result_checks(
    current_run, monkeypatch, field, value,
):
    review, _ = review_marker(current_run, monkeypatch)
    review.outcome = review.outcome.model_copy(update={field: value})
    before = current_run[1].work_version
    with pytest.raises(HeadConflict, match="review boundary cannot carry recognition changes"):
        publish(current_run, review)
    assert current_run[1].work_version == before


@pytest.mark.parametrize("field", [
    "protocol_state_changes", "protocol_result_changes", "member_outcomes",
    "model_call_state", "task_outcomes",
])
def test_record_review_boundary_cannot_mutate_protocol_or_paid_results(
    current_run, monkeypatch, field,
):
    review, _ = review_marker(current_run, monkeypatch)
    setattr(review, field, {"changed": True})
    store, run, _ = current_run
    before = current_state.restore_calls(store, run, run.run_fingerprint)
    with pytest.raises(HeadConflict, match="review boundary cannot carry recognition changes"):
        publish(current_run, review)
    assert current_state.restore_calls(store, run, run.run_fingerprint) == before


def reopening(current_run, tmp_path, monkeypatch):
    from app.services.extraction.ontology_guided.records import RecordIndex

    store, run, token = current_run
    ir = arguments(tmp_path, ["装置甲待补充。", "另一记录的装置乙。"])["ir"]
    record = RecordIndex(ir).records[0]
    protocol = record_protocol()
    task = RecordDiscoveryTask.create(
        run_fingerprint=run.run_fingerprint, record_id=record.record_id,
        schema_card_id=protocol["task"]["schema_card_id"],
        analysis_scope_ref=protocol["task"]["analysis_scope_ref"], dependency_hash="f" * 64,
    )
    protocol.update(task=task.model_dump(mode="json"), lineage_id=task.claim_lineage_id)
    protocol["base_target"]["task_id"] = task.task_id
    protocol["base_target"]["document_context"]["document_hash"] = ir.document_hash
    run.document_hash = ir.document_hash
    payload = {"analysis": ir.model_dump(mode="json")}
    store.update_artifact(
        run.recognition_run_id, run.owner_id, token, artifact_kind="metadata",
        expected_revision=0, artifact_hash=current_state.content_hash(payload),
        status="ready", payload=payload,
    )
    store.db.commit()
    persist(current_run, protocol)
    for _ in range(2):
        receipt = reservation(current_run, protocol)
        persist(current_run, protocol, receipts=[receipt])
        confirm(current_run, protocol)
    batch, outcome_ref = publication(current_run, protocol, monkeypatch)
    key = json.dumps(task.claim_lineage_id)
    saved = {"key": task.claim_lineage_id, "position": 0, "value": {
        "task": task.model_dump(mode="json"), "status": "examined", "outcome_ref": outcome_ref,
    }}
    batch.work_changes["record_discovery"] = {key: saved}
    publish(current_run, batch)
    protocol = deepcopy(batch.protocol_state_changes[task.claim_lineage_id])
    anchor = ir.anchor(record.source_units[0].evidence_id, 0, len(record.source_units[0].text))
    feedback = "9" * 64
    pending = deepcopy(saved)
    pending["value"].update(status="pending", feedback_hash=feedback, feedback_refs=[],
                            feedback_source_refs=[anchor.model_dump(mode="json")])
    current_state.persist_boundary(
        store, run, token, changes={"record_discovery": {key: pending}},
        fingerprint=run.run_fingerprint, expected_version=run.work_version,
    )
    protocol.update(
        assertion_generation=protocol["assertion_generation"] + 1,
        evidence_revision=protocol["evidence_revision"] + 1,
        context_hash="c" * 64, evidence_hash="c" * 64, stage="discovery",
        active_instructions="Discover only the newly located record feedback.",
        turn_refs=[], completed_tool_results=[], materialized_refs={}, stage_input_items=[],
        discovery_ref=None, verification_ref=None, outcome_ref=None, record_feedback_hash=feedback,
    )
    protocol["base_target"]["context_hash"] = protocol["context_hash"]
    active = deepcopy(pending)
    active["value"].update(status="active", consumed_feedback_hash=feedback,
                           generation=protocol["assertion_generation"])
    return protocol, {"record_discovery": {key: active}}, pending, ir


def test_record_reopening_consumes_only_same_source_feedback_and_retains_paid_calls(
    current_run, tmp_path, monkeypatch,
):
    protocol, changes, _pending, _ir = reopening(current_run, tmp_path, monkeypatch)
    persist(current_run, protocol, work_changes=changes)
    store, run, _ = current_run
    restored = current_state.restore_calls(store, run, run.run_fingerprint)
    assert restored["lineage_calls"] == {protocol["lineage_id"]: 2}
    assert restored["protocols"][protocol["lineage_id"]] == protocol
    assert restored["reservation_sequence"] == 2


@pytest.mark.parametrize("fault", [
    "feedback_hash", "no_published_outcome", "not_pending", "generation_jump",
    "changed_ontology", "other_record", "unauthorized_entity",
])
def test_record_reopening_rejects_unowned_feedback_without_changing_current_protocol(
    current_run, tmp_path, monkeypatch, fault,
):
    protocol, changes, pending, ir = reopening(current_run, tmp_path, monkeypatch)
    store, run, token = current_run
    key = json.dumps(protocol["lineage_id"])
    if fault == "feedback_hash":
        protocol["record_feedback_hash"] = "8" * 64
    elif fault == "no_published_outcome":
        changes["record_discovery"][key]["value"]["outcome_ref"] = "unpublished"
    elif fault == "not_pending":
        pending["value"]["status"] = "examined"
    elif fault == "generation_jump":
        protocol["assertion_generation"] += 1
    elif fault == "changed_ontology":
        protocol["base_target"]["ontology_hash"] = "8" * 64
    elif fault == "other_record":
        unit = ir.evidence_units[-1]
        anchors = [ir.anchor(unit.evidence_id, 0, len(unit.text)).model_dump(mode="json")]
        pending["value"]["feedback_source_refs"] = anchors
        changes["record_discovery"][key]["value"]["feedback_source_refs"] = anchors
    else:
        protocol["reference_context"] = {
            "version": 1, "entity_refs": [{"id": "foreign-entity", "revision": 1}],
        }
    current_state.persist_boundary(
        store, run, token, changes={"record_discovery": {key: pending}},
        fingerprint=run.run_fingerprint, expected_version=run.work_version,
    )
    before = current_state.restore_calls(store, run, run.run_fingerprint)
    version = run.work_version
    with pytest.raises((HeadConflict, execution.CheckpointMismatch)):
        persist(current_run, protocol, work_changes=changes)
    assert run.work_version == version
    assert current_state.restore_calls(store, run, run.run_fingerprint) == before


@pytest.fixture
def record_pg_run(pg_engine):
    with Session(pg_engine, expire_on_commit=False) as db:
        store = DocumentAnalysisRunStore(db)
        run, token = seed(store, "record-postgresql")
        run.run_fingerprint = "responses-current-fingerprint"
        db.commit()
        yield store, run, token


def test_postgresql_record_candidate_and_queue_rollback(record_pg_run, monkeypatch):
    test_record_candidate_outcome_and_following_queue_commit_or_rollback_together(
        record_pg_run, monkeypatch,
    )


@pytest.mark.parametrize("pause_stage", ["discovery", "verification"])
def test_postgresql_record_cold_continue_reuses_paid_response(
    tmp_path, monkeypatch, record_pg_run, pause_stage,
):
    from tests.test_extraction.test_record_executor import (
        test_cold_continue_reuses_confirmed_record_response as check_cold_continue,
    )

    check_cold_continue(tmp_path, monkeypatch, record_pg_run, pause_stage)


def test_postgresql_record_publications_have_one_version_winner_and_reject_foreign_owner(
    record_pg_run, monkeypatch,
):
    protocol = record_protocol()
    persist(record_pg_run, protocol)
    receipt = reservation(record_pg_run, protocol)
    persist(record_pg_run, protocol, receipts=[receipt])
    confirm(record_pg_run, protocol)
    batch, _ = publication(record_pg_run, protocol, monkeypatch)
    store, run, token = record_pg_run
    engine = store.db.get_bind()
    run_id, owner, fingerprint = run.recognition_run_id, run.owner_id, run.run_fingerprint
    rendezvous = Barrier(2)

    def compete(number):
        with Session(engine, expire_on_commit=False) as db:
            contender = DocumentAnalysisRunStore(db)
            current = contender.get_owned(run_id, owner)
            own_batch = deepcopy(batch)
            own_batch.batch_id += f"-{number}"
            rendezvous.wait(timeout=10)
            try:
                current_state.persist_batch(
                    contender, current, token, batch=own_batch, fingerprint=fingerprint,
                    ontology=None, ir=None, metadata=None, index=None, expected_version=0,
                )
                return "committed"
            except HeadConflict:
                db.rollback()
                return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(compete, number) for number in range(2)]
        assert sorted(future.result(timeout=20) for future in futures) == ["committed", "conflict"]
    store.db.expire_all()
    assert run.work_version == 1
    assert current_state.get_row(store, run, "work:scheduler:record_relations", "relation-task") \
        == {"entity_ref": "record-entity@1"}
    foreign = SimpleNamespace(
        recognition_run_id=run_id, owner_id="foreign", run_fingerprint=fingerprint,
        work_version=1, revision=run.revision, event_head=run.event_head,
    )
    with pytest.raises((RunNotFound, FenceViolation)):
        publish((store, foreign, token), batch)
