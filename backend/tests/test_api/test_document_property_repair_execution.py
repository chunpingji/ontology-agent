"""Real run/checkpoint integration; only the recognition adapter is deterministic."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from docx import Document
from sqlalchemy.orm import Session

from app.api import document_analysis
from app.config import settings
from app.models.document_analysis import (
    DocumentAnalysisExecution,
    DocumentAnalysisRun,
    DocumentRunCandidate,
)
from app.services.document_analysis import application, execution
from app.services.document_analysis.reviews import repair_operations
from app.services.document_analysis.run_store import DocumentAnalysisRunStore
from app.services.extraction.evidence_identity import evidence_hash
from app.services.extraction.ontology_guided.contracts import (
    GraphProperty,
    OntologyClassDefinition,
    OntologySnapshot,
    SlotSpec,
    VersionedRef,
)
from app.services.extraction.ontology_guided.executor import TaskOutcome

ROOT, PREDICATE = "urn:review:Report", "urn:review:appearance"


class SimulatedCrash(BaseException):
    pass


class CorrectingAdapter:
    model_identity = "property-review-integration-v1"

    def __init__(self):
        self.calls = []

    def inspect(self, task, context, _predicate, _menu):
        self.calls.append(task.task_id)
        is_repair = bool(task.retry_kind and task.retry_kind.startswith("expert_review:"))
        reference = next(fragment.anchor for fragment in context.fragments
                         if "白色片剂" in fragment.text)
        identity = "corrected" if is_repair else "incorrect"
        candidate = GraphProperty(
            candidate_id=identity, revision=1,
            subject_ref=VersionedRef(id=task.subject.entity_id, revision=task.subject.revision),
            predicate_iri=PREDICATE, predicate_label="外观",
            raw_value="白色片剂" if is_repair else "红色片剂",
            decision_status="supported", structural_valid=True, model_supported=True,
            policy_eligible=True, proof_ref=VersionedRef(id=f"proof:{identity}", revision=1),
            decision_refs=[VersionedRef(id=f"decision:{identity}", revision=1)],
            evidence_refs=[reference], value_evidence_refs=[reference],
        )
        return TaskOutcome(
            properties=[candidate], semantic_outcome="supported", model_calls=1,
            reason_code="fixture_supported", reason="受控适配器返回，用于验证运行与恢复链路",
            proof_payloads=[{"proof_id": f"proof:{identity}", "proof_revision": 1,
                             "target_id": identity}],
            decision_payloads=[{"decision_id": f"decision:{identity}", "revision": 1,
                                "target_id": identity, "verdict": "supported"}],
        )


def ontology():
    values = {"iri": ROOT, "label": "报告", "declared_properties": [
        SlotSpec(iri=PREDICATE, label="外观", declared_by=[ROOT]),
    ]}
    classes = {ROOT: OntologyClassDefinition(**values, source_hash=evidence_hash(values))}
    return OntologySnapshot(snapshot_id="review-ontology", ontology_hash=evidence_hash(classes),
                            classes=classes, created_from="frozen_fixture")


def execute_claim(db, run_id):
    store = DocumentAnalysisRunStore(db)
    token = store.claim(run_id, "analyst", actor="test", worker_id="review-integration")
    db.commit()
    execution._execute_claimed(db, store, store.get_owned(run_id, "analyst"), token)


def queue_repair(
    client, db, analyst_headers, tmp_path: Path, monkeypatch, evidence_repair,
):
    monkeypatch.setattr(settings, "document_analysis_storage_dir", tmp_path / "artifacts")
    monkeypatch.setattr(settings, "document_analysis_evidence_repair_enabled", evidence_repair)
    monkeypatch.setattr(settings, "document_analysis_performance_enabled", False)
    monkeypatch.setattr(settings, "document_analysis_adaptive_retrieval_mode", "disabled")
    monkeypatch.setattr(settings, "semantic_ranking_enabled", False)
    monkeypatch.setattr(document_analysis, "notify_document_analysis_dispatcher", lambda: True)
    monkeypatch.setattr(application, "ontology_snapshot_from_engine", lambda *_args: ontology())
    adapter = CorrectingAdapter()
    monkeypatch.setattr(execution, "_configured_recognition_adapter", lambda _performance: adapter)
    word = Document()
    word.add_paragraph("外观为白色片剂。")
    path = tmp_path / "source.docx"
    word.save(path)
    created = client.post("/api/document-analysis/runs", headers=analyst_headers, files={
        "file": ("source.docx", path.read_bytes(),
                 "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    }, data={"root_class_iri": ROOT, "request_key": "review-integration",
             "metadata_mode": "structure_only"})
    assert created.status_code == 202, created.text
    run_id = created.json()["recognition_run_id"]
    execute_claim(db, run_id)
    base = f"/api/document-analysis/runs/{run_id}"
    before = client.get(base + "/graph?projection=all_candidates", headers=analyst_headers).json()
    run = db.get(DocumentAnalysisRun, run_id)
    assert run.execution_status == "finished", (run.error, run.stop_reason, before)
    target = next(item for item in before["properties"] if item["candidate_id"] == "incorrect")
    original = db.get(DocumentRunCandidate, (run_id, "incorrect", target["revision"]))
    original_hash = original.payload_hash
    reviewed = client.post(base + "/reviews", headers=analyst_headers, json={
        "request_key": "review", "expected_run_revision": run.revision,
        "graph_snapshot_id": before["graph_snapshot"]["snapshot_id"],
        "candidate_id": target["candidate_id"], "candidate_revision": target["revision"],
        "expected_review_revision": 0, "decision": "rejected", "reason_code": "incorrect_value",
        "reason": "原文是白色，当前值错误，请局部重识别",
    })
    assert reviewed.status_code == 201, reviewed.text
    review = reviewed.json()
    queued = client.post(base + "/repairs", headers=analyst_headers, json={
        "request_key": "repair", "expected_run_revision": review["run"]["run_revision"],
        "review_id": review["review"]["review_id"],
    })
    assert queued.status_code == 202, queued.text
    return run_id, adapter, base, target, original_hash


@pytest.mark.parametrize("evidence_repair", [False, True])
def test_reject_repair_checkpoint_commit_and_cold_replay_do_not_repeat_model_calls(
    client, db, analyst_headers, tmp_path: Path, monkeypatch, evidence_repair,
):
    run_id, adapter, base, target, original_hash = queue_repair(
        client, db, analyst_headers, tmp_path, monkeypatch, evidence_repair,
    )
    original_publish = execution._persist_recognition_batch

    def crash_after_repair(*args, **kwargs):
        original_publish(*args, **kwargs)
        if (kwargs["batch"].task.retry_kind or "").startswith("expert_review:"):
            raise SimulatedCrash()

    monkeypatch.setattr(execution, "_persist_recognition_batch", crash_after_repair)
    with pytest.raises(SimulatedCrash):
        execute_claim(db, run_id)
    db.rollback()
    call_count = len(adapter.calls)
    assert call_count == 2
    operation = repair_operations(db, db.get(DocumentAnalysisRun, run_id))[0]
    assert operation["status"] == "completed"
    assert operation["result"]["replacement_candidate_refs"][0]["id"] == "corrected"
    monkeypatch.setattr(execution, "_persist_recognition_batch", original_publish)
    lease = db.get(DocumentAnalysisExecution, run_id)
    lease.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    with Session(db.get_bind(), expire_on_commit=False) as cold:
        execute_claim(cold, run_id)
    assert len(adapter.calls) == call_count
    db.expire_all()
    after = client.get(base + "/graph?projection=all_candidates", headers=analyst_headers).json()
    repaired = next(item for item in after["properties"] if item["candidate_id"] == "corrected")
    rejected = next(item for item in after["properties"] if item["candidate_id"] == "incorrect")
    assert repaired["raw_value"] == "白色片剂" and repaired["independent_review"] == "unreviewed"
    assert rejected["independent_review"] == "rejected"
    assert rejected["revision"] > target["revision"]
    assert db.get(DocumentRunCandidate, (run_id, "incorrect", target["revision"])).payload_hash == (
        original_hash
    )
    assert client.get(base + "/repairs", headers=analyst_headers).json()["items"][0]["status"] == (
        "completed"
    )


def test_worker_failure_stops_repair_and_ordinary_resume_cannot_reopen_its_budget(
    client, db, analyst_headers, tmp_path, monkeypatch,
):
    run_id, adapter, base, _target, _original_hash = queue_repair(
        client, db, analyst_headers, tmp_path, monkeypatch, True,
    )
    original_publish = execution._persist_recognition_batch

    def fail_after_review_boundary(*args, **kwargs):
        original_publish(*args, **kwargs)
        if kwargs["batch"].outcome.reason_code == "expert_review_boundary":
            raise RuntimeError("synthetic publication interruption")

    monkeypatch.setattr(execution, "_persist_recognition_batch", fail_after_review_boundary)
    store = DocumentAnalysisRunStore(db)
    token = store.claim(run_id, "analyst", actor="test", worker_id="review-failure")
    db.commit()
    execution._execute_dispatched_run(db, store, store.get_owned(run_id, "analyst"), token)
    db.expire_all()
    run = db.get(DocumentAnalysisRun, run_id)
    assert run.execution_status == "failed"
    operation = repair_operations(db, run)[0]
    assert operation["status"] == "failed"
    assert len(adapter.calls) == 1
    monkeypatch.setattr(execution, "_persist_recognition_batch", original_publish)
    resumed = client.post(base + "/resume", headers=analyst_headers, json={
        "request_key": "resume-after-failure", "expected_revision": run.revision,
        "reason": "继续原运行，不能重复已失败的局部修复",
    })
    assert resumed.status_code == 202, resumed.text
    with Session(db.get_bind(), expire_on_commit=False) as cold:
        execute_claim(cold, run_id)
    assert len(adapter.calls) == 1
    db.expire_all()
    final_operation = repair_operations(db, db.get(DocumentAnalysisRun, run_id))[0]
    assert final_operation["status"] == "failed"
    assert final_operation["result"] == operation["result"]


def test_missing_recognition_model_finishes_repair_with_explicit_failure(
    client, db, analyst_headers, tmp_path, monkeypatch,
):
    run_id, adapter, _base, _target, _original_hash = queue_repair(
        client, db, analyst_headers, tmp_path, monkeypatch, True,
    )
    monkeypatch.setattr(execution, "_configured_recognition_adapter", lambda _performance: None)
    store = DocumentAnalysisRunStore(db)
    token = store.claim(run_id, "analyst", actor="test", worker_id="missing-model")
    db.commit()
    execution._execute_dispatched_run(db, store, store.get_owned(run_id, "analyst"), token)
    db.expire_all()
    run = db.get(DocumentAnalysisRun, run_id)
    assert run.execution_status in {"failed", "blocked_dependency"}
    assert run.error and run.error["code"]
    operation = repair_operations(db, run)[0]
    assert operation["status"] == "failed", operation
    assert operation["result"]["reason_code"]
    assert len(adapter.calls) == 1
