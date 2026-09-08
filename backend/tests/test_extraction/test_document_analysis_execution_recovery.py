"""Deterministic hard-crash and startup recovery checks for document runs."""

from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from docx import Document
from sqlalchemy import select, update

from app.api import document_analysis
from app.config import settings
from app.models.document_analysis import (
    DocumentAnalysisArtifact,
    DocumentAnalysisExecution,
    DocumentAnalysisRun,
    DocumentRecognitionEventBatch,
    DocumentRunArtifact,
    DocumentRunCandidateHead,
    DocumentVerificationProofHead,
)
from app.services.document_analysis import execution as execution_service
from app.services.document_analysis.run_store import (
    DocumentAnalysisRunStore,
    FenceViolation,
)
from app.services.extraction.ontology_guided.contracts import GraphNode
from app.services.extraction.ontology_guided.executor import TaskOutcome

ROOT_IRI = "https://ontology.pharma-gmp.cn/slpra/drug-development/CMCReport"


class SimulatedProcessCrash(BaseException):
    """Escape normal worker error handling like an actual process termination."""


class CountingAdapter:
    model_identity = "durable-checkpoint-test-adapter-v1"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def inspect(self, task, _context, _predicate, _menu):
        self.calls.append(task.task_id)
        first_call_payload = {}
        if len(self.calls) == 1:
            target_id = f"target:{task.task_id}"
            first_call_payload = {
                "nodes": [
                    GraphNode(
                        entity_id="checkpoint-only-candidate",
                        revision=1,
                        class_iri=task.subject.class_iri,
                        class_label="检查点候选",
                        label="检查点候选",
                    )
                ],
                "proof_payloads": [
                    {
                        "proof_id": "checkpoint-proof",
                        "proof_revision": 1,
                        "target_id": target_id,
                    }
                ],
                "decision_payloads": [
                    {
                        "decision_id": "checkpoint-decision",
                        "target_id": target_id,
                        "verdict": "unsupported",
                    }
                ],
            }
        return TaskOutcome(
            semantic_outcome="unsupported",
            reason_code="record_not_supporting_target",
            reason="确定性恢复测试",
            model_calls=1,
            **first_call_payload,
        )


def _word_bytes(tmp_path: Path) -> bytes:
    document = Document()
    document.add_heading("第一章 产品概要", level=1)
    document.add_paragraph("本报告包含用于恢复测试的产品和设备描述。")
    document.add_heading("1.1 质量属性", level=2)
    document.add_paragraph("外观为白色片剂，生产使用冻干机。")
    path = tmp_path / "checkpoint-source.docx"
    document.save(path)
    return path.read_bytes()


def _create_pending_run(client, analyst_headers, tmp_path, monkeypatch, *, key: str) -> str:
    monkeypatch.setattr(settings, "document_analysis_storage_dir", tmp_path / "run-artifacts")
    monkeypatch.setattr(document_analysis, "dispatch_run", lambda *_args, **_kwargs: None)
    response = client.post(
        "/api/document-analysis/runs",
        headers=analyst_headers,
        files={
            "file": (
                "恢复测试.docx",
                _word_bytes(tmp_path),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
        data={
            "root_class_iri": ROOT_IRI,
            "request_key": key,
            "metadata_mode": "structure_only",
        },
    )
    assert response.status_code == 202, response.text
    return response.json()["recognition_run_id"]


def _claim(db, run_id: str) -> tuple[DocumentAnalysisRunStore, str]:
    store = DocumentAnalysisRunStore(db)
    token = store.claim(
        run_id,
        "analyst",
        actor="checkpoint-test",
        worker_id="checkpoint-worker",
        lease_seconds=30,
    )
    db.commit()
    return store, token


def _expire_lease(db, run_id: str) -> None:
    db.execute(
        update(DocumentAnalysisExecution)
        .where(DocumentAnalysisExecution.recognition_run_id == run_id)
        .values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    db.commit()
    db.expire_all()


def _crash_after_first_batch(db, run_id: str, monkeypatch):
    store, old_token = _claim(db, run_id)
    original = execution_service._persist_recognition_batch

    def persist_then_crash(*args, **kwargs):
        original(*args, **kwargs)
        raise SimulatedProcessCrash("after committed recognition batch")

    monkeypatch.setattr(execution_service, "_persist_recognition_batch", persist_then_crash)
    run = store.get_owned(run_id, "analyst")
    with pytest.raises(SimulatedProcessCrash):
        execution_service._execute_claimed(db, store, run, old_token)
    monkeypatch.setattr(execution_service, "_persist_recognition_batch", original)
    db.expire_all()
    return store, old_token


def _checkpoint_payload(db, run_id: str) -> tuple[dict, DocumentRecognitionEventBatch]:
    receipt = db.scalar(
        select(DocumentRecognitionEventBatch).where(
            DocumentRecognitionEventBatch.recognition_run_id == run_id
        )
    )
    assert receipt is not None
    artifact = db.get(DocumentAnalysisArtifact, receipt.checkpoint_artifact_id)
    assert artifact is not None and artifact.payload is not None
    return artifact.payload, receipt


def test_committed_batch_is_secret_free_closed_waterline_and_resumes_without_recall(
    client, db, analyst_headers, tmp_path, monkeypatch
):
    run_id = _create_pending_run(
        client, analyst_headers, tmp_path, monkeypatch, key="crash-after-first-batch"
    )
    adapter = CountingAdapter()
    monkeypatch.setattr(execution_service, "configured_model_adapter", lambda: adapter)

    store, old_token = _crash_after_first_batch(db, run_id, monkeypatch)
    first_task_id = adapter.calls[0]
    checkpoint, receipt = _checkpoint_payload(db, run_id)
    serialized = json.dumps(checkpoint, ensure_ascii=False, sort_keys=True)

    assert old_token not in serialized
    assert "execution_token" not in serialized
    assert checkpoint["execution_generation"] == 1
    assert checkpoint["event_seq"] == receipt.last_sequence == receipt.first_sequence
    assert checkpoint["dependency_index"]["requirements"] == {
        "checkpoint-proof@1": [
            ["checkpoint-decision@1"],
        ]
    }
    run = store.get_owned(run_id, "analyst")
    assert run.execution_status == "running"
    assert run.event_head == checkpoint["event_seq"]

    candidate_heads = {
        (row.candidate_id, row.revision)
        for row in db.scalars(
            select(DocumentRunCandidateHead).where(
                DocumentRunCandidateHead.recognition_run_id == run_id
            )
        )
    }
    proof_heads = {
        (row.proof_id, row.proof_revision)
        for row in db.scalars(
            select(DocumentVerificationProofHead).where(
                DocumentVerificationProofHead.recognition_run_id == run_id
            )
        )
    }
    assert candidate_heads == {
        (item["id"], item["revision"]) for item in checkpoint["candidate_head_refs"]
    }
    assert proof_heads == {(item["id"], item["revision"]) for item in checkpoint["proof_head_refs"]}
    assert "checkpoint-only-candidate" in {item[0] for item in candidate_heads}
    assert "checkpoint-proof" in {item[0] for item in proof_heads}

    graph_ref = db.scalar(
        select(DocumentRunArtifact).where(
            DocumentRunArtifact.recognition_run_id == run_id,
            DocumentRunArtifact.artifact_kind == "graph",
            DocumentRunArtifact.event_head == checkpoint["event_seq"],
        )
    )
    assert graph_ref is not None
    graph_artifact = db.get(DocumentAnalysisArtifact, graph_ref.artifact_id)
    assert graph_artifact is not None
    assert graph_artifact.payload["graph"] == checkpoint["graph_state"]

    _expire_lease(db, run_id)
    new_token = store.claim(
        run_id,
        "analyst",
        actor="checkpoint-recovery",
        worker_id="replacement-worker",
        lease_seconds=30,
    )
    db.commit()
    assert new_token != old_token
    with pytest.raises(FenceViolation):
        store.event_batch_receipt(
            run_id,
            "analyst",
            old_token,
            batch_id=receipt.batch_id,
            batch_hash=receipt.content_hash,
        )

    execution_service._execute_claimed(
        db,
        store,
        store.get_owned(run_id, "analyst"),
        new_token,
    )
    db.expire_all()
    assert db.get(DocumentAnalysisRun, run_id).execution_status == "finished"
    assert Counter(adapter.calls)[first_task_id] == 1


def test_hard_crash_before_terminal_event_reuses_checkpoint_without_new_graph_revision(
    client, db, analyst_headers, tmp_path, monkeypatch
):
    run_id = _create_pending_run(
        client, analyst_headers, tmp_path, monkeypatch, key="crash-before-terminal-event"
    )
    adapter = CountingAdapter()
    monkeypatch.setattr(execution_service, "configured_model_adapter", lambda: adapter)
    store, old_token = _claim(db, run_id)
    original_finish = execution_service._finish

    def crash_before_terminal(*_args, **_kwargs):
        raise SimulatedProcessCrash("before terminal event")

    monkeypatch.setattr(execution_service, "_finish", crash_before_terminal)
    with pytest.raises(SimulatedProcessCrash):
        execution_service._execute_claimed(db, store, store.get_owned(run_id, "analyst"), old_token)
    monkeypatch.setattr(execution_service, "_finish", original_finish)
    db.expire_all()

    run = store.get_owned(run_id, "analyst")
    checkpoint = execution_service._restore_recognition_checkpoint(
        db,
        store,
        run,
        final_fingerprint=run.run_fingerprint,
    )
    assert checkpoint is not None
    graph_head_before = store.get_artifact_head(run_id, "analyst", "graph")
    calls_before = list(adapter.calls)

    _expire_lease(db, run_id)
    replacement = store.claim(
        run_id,
        "analyst",
        actor="terminal-recovery",
        worker_id="replacement-worker",
        lease_seconds=30,
    )
    db.commit()
    execution_service._execute_claimed(
        db,
        store,
        store.get_owned(run_id, "analyst"),
        replacement,
    )
    db.expire_all()

    assert db.get(DocumentAnalysisRun, run_id).execution_status == "finished"
    assert adapter.calls == calls_before
    graph_head_after = store.get_artifact_head(run_id, "analyst", "graph")
    assert graph_head_after.revision == graph_head_before.revision
    assert graph_head_after.artifact_id == graph_head_before.artifact_id


def test_changed_fingerprint_blocks_recovery_without_another_model_call(
    client, db, analyst_headers, tmp_path, monkeypatch
):
    run_id = _create_pending_run(
        client, analyst_headers, tmp_path, monkeypatch, key="fingerprint-mismatch"
    )
    adapter = CountingAdapter()
    monkeypatch.setattr(execution_service, "configured_model_adapter", lambda: adapter)
    _store, _old_token = _crash_after_first_batch(db, run_id, monkeypatch)
    calls_before = list(adapter.calls)

    _expire_lease(db, run_id)
    monkeypatch.setattr(settings, "evidence_max_tasks", settings.evidence_max_tasks + 1)
    execution_service.dispatch_run(run_id, bind=db.get_bind())
    db.expire_all()

    run = db.get(DocumentAnalysisRun, run_id)
    assert run.execution_status == "blocked_dependency"
    assert run.error["code"] == "FINGERPRINT_MISMATCH"
    assert run.error["retryable"] is False
    assert adapter.calls == calls_before


def test_startup_recovery_claims_expired_pausing_run_and_finishes_pause(
    client, db, analyst_headers, tmp_path, monkeypatch
):
    run_id = _create_pending_run(
        client, analyst_headers, tmp_path, monkeypatch, key="startup-pausing-recovery"
    )
    adapter = CountingAdapter()
    monkeypatch.setattr(execution_service, "configured_model_adapter", lambda: adapter)
    store, old_token = _claim(db, run_id)
    running = store.get_owned(run_id, "analyst")
    pausing = store.request_control(
        run_id,
        "analyst",
        action="pause",
        expected_revision=running.revision,
        request_key="pause-before-worker-crash",
    )
    db.commit()
    assert pausing.execution_status == "pausing"
    _expire_lease(db, run_id)

    def parse_after_pause(*_args, **_kwargs):
        pytest.fail("replacement worker started parsing after a durable pause request")

    monkeypatch.setattr(execution_service, "ensure_docx", parse_after_pause)

    assert execution_service.recover_document_analysis_runs(bind=db.get_bind()) == 1
    db.expire_all()
    recovered = db.get(DocumentAnalysisRun, run_id)

    assert recovered.execution_status == "paused"
    assert recovered.stop_reason == "operator_pause"
    assert adapter.calls == []
    with pytest.raises(FenceViolation):
        store.assert_fence(run_id, "analyst", old_token)
    assert execution_service.recover_document_analysis_runs(bind=db.get_bind()) == 0
