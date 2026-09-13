"""Create, dispatch and read sparse runs through the real application boundary."""

from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace

import pytest
from docx import Document

from app.api import document_analysis
from app.config import settings
from app.models.document_analysis import DocumentAnalysisArtifact, DocumentAnalysisRun
from app.services.document_analysis import execution
from app.services.extraction.ontology_guided.executor import TaskOutcome

ROOT = "https://ontology.pharma-gmp.cn/slpra/drug-development/CMCReport"
POLICY = "sparse-candidates-v1"


class ControlledAdapter:
    model_identity = "sparse-api-controlled-adapter-v1"

    def __init__(self, outcome: str):
        self.outcome = outcome
        self.calls: list[str] = []

    def inspect(self, task, _context, _predicate, _menu):
        self.calls.append(task.task_id)
        return TaskOutcome(
            semantic_outcome="undetermined" if self.outcome == "undetermined" else "not_checked",
            complete=self.outcome != "technical_failure",
            reason_code=(
                "no_candidate_observed" if self.outcome == "empty" else "source_not_resolved"
            ),
            reason="隔离适配器没有肯定事实；语义未决与技术未完成独立记录。",
            model_calls=1,
        )


def _run_case(
    client, db, analyst_headers, fake_engine, tmp_path, monkeypatch,
    *, outcome="empty", record_count=4, slot_count=2, legacy=False,
):
    monkeypatch.setattr(settings, "document_analysis_storage_dir", tmp_path / "run-artifacts")
    monkeypatch.setattr(settings, "document_analysis_evidence_repair_enabled", not legacy)
    monkeypatch.setattr(settings, "document_analysis_adaptive_retrieval_mode", "disabled")
    monkeypatch.setattr(settings, "semantic_ranking_mode", "deterministic")
    monkeypatch.setattr(settings, "evidence_max_tasks", 256)
    # The API still persists the real queue entry; this test explicitly delivers
    # its durable dispatcher step after checking the accepted snapshot.
    monkeypatch.setattr(document_analysis, "dispatch_run", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(fake_engine, "get_class_detail", lambda iri: SimpleNamespace(
        name="CMC报告", parent_iris=[], comment="",
    ) if iri == ROOT else None)
    monkeypatch.setattr(fake_engine, "get_relation_schema", lambda *_args: [])
    monkeypatch.setattr(fake_engine, "get_data_properties_by_domain", lambda iri: [
        {"iri": f"urn:sparse:field:{number}", "label": f"独立检索属性{number}",
         "range": ["http://www.w3.org/2001/XMLSchema#string"]}
        for number in range(slot_count)
    ] if iri == ROOT else [])
    adapter = ControlledAdapter(outcome)
    monkeypatch.setattr(execution, "configured_model_adapter", lambda **_kwargs: adapter)

    document = Document()
    for number in range(record_count):
        document.add_paragraph(f"背景原文片段{number}。")
    source = BytesIO()
    document.save(source)
    created = client.post(
        "/api/document-analysis/runs",
        headers=analyst_headers,
        files={"file": ("候选执行.docx", source.getvalue(),
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        data={"root_class_iri": ROOT, "request_key": f"sparse-{outcome}-{slot_count}",
              "metadata_mode": "structure_only"},
    )
    assert created.status_code == 202, created.text
    run_id = created.json()["recognition_run_id"]
    path = f"/api/document-analysis/runs/{run_id}"
    queued = client.get(path, headers=analyst_headers)
    assert queued.status_code == 200, queued.text
    assert queued.json()["status"] == "queued"
    pending_graph = client.get(path + "/graph", headers=analyst_headers)
    assert pending_graph.status_code == 200, pending_graph.text
    assert pending_graph.json()["availability"] == "pending"
    if legacy:
        assert "candidate_policy" not in queued.json()["progress"]
        assert "completion" not in queued.json()["progress"]
        assert "candidate_policy" not in pending_graph.json()["coverage"]
    else:
        assert queued.json()["progress"]["candidate_policy"] == POLICY
        assert queued.json()["progress"]["completion"] == "incomplete"
        assert pending_graph.json()["coverage"]["candidate_policy"] == POLICY
    assert pending_graph.json()["coverage"]["records_planned"] == 0
    assert adapter.calls == []

    db.expire_all()
    run = db.get(DocumentAnalysisRun, run_id)
    original = db.get(DocumentAnalysisArtifact, run.source_artifact_ref)
    if legacy:
        assert "candidate_planning" not in original.payload["performance_policy"]
    else:
        assert original.payload["performance_policy"]["candidate_planning"] == POLICY
    frozen_source = dict(original.payload)
    db.rollback()
    assert execution.dispatch_next_run(bind=db.get_bind(), worker_id="sparse-api-worker") is True
    db.expire_all()

    status = client.get(path, headers=analyst_headers)
    graph = client.get(path + "/graph", headers=analyst_headers)
    assert status.status_code == 200, status.text
    assert graph.status_code == 200, graph.text
    current = db.get(DocumentAnalysisRun, run_id)
    assert current.run_fingerprint
    assert db.get(DocumentAnalysisArtifact, current.source_artifact_ref).payload == frozen_source
    calls_after_dispatch = list(adapter.calls)
    assert client.get(path, headers=analyst_headers).status_code == 200
    assert client.get(path + "/graph", headers=analyst_headers).status_code == 200
    assert execution.dispatch_next_run(bind=db.get_bind(), worker_id="sparse-api-worker") is False
    assert adapter.calls == calls_after_dispatch
    assert len(adapter.calls) == len(set(adapter.calls)), "first candidate task was executed twice"
    return status.json(), graph.json(), adapter


def test_new_run_freezes_sparse_policy_and_finishes_without_full_text_cross_product(
    client, db, analyst_headers, fake_engine, tmp_path, monkeypatch,
):
    status, graph, adapter = _run_case(
        client, db, analyst_headers, fake_engine, tmp_path, monkeypatch, record_count=40,
    )
    assert status["status"] == "finished", status
    progress = status["progress"]
    assert progress["completion"] == "policy_complete"
    assert progress["stop_reason"] == "candidate_search_exhausted"
    assert 0 < progress["records_planned"] < 40 * 2
    assert progress["records_planned"] == progress["records_examined"] == len(adapter.calls)
    assert progress["records_unattempted"] == progress["records_incomplete"] == 0
    coverage = graph["coverage"]
    assert coverage["candidate_policy"] == POLICY
    assert coverage["records_planned"] == progress["records_planned"]
    assert coverage["stop_reason"] == "candidate_search_exhausted"
    assert len(coverage["subjects"]) == 2
    assert all(subject["candidate_policy"] == POLICY for subject in coverage["subjects"])
    assert graph["relationships"] == graph["properties"] == []
    assert graph["unresolved"]["unsupported"] == 0, "unselected text is not a negative fact"


@pytest.mark.parametrize("outcome", ["undetermined", "technical_failure"])
def test_strategy_completion_and_semantic_results_are_independent(
    client, db, analyst_headers, fake_engine, tmp_path, monkeypatch, outcome,
):
    status, graph, adapter = _run_case(
        client, db, analyst_headers, fake_engine, tmp_path, monkeypatch, outcome=outcome,
    )
    assert adapter.calls
    progress = status["progress"]
    assert graph["coverage"]["candidate_policy"] == POLICY
    if outcome == "undetermined":
        assert status["status"] == "finished", status
        assert progress["completion"] == "policy_complete"
        assert progress["decisions"]["undetermined"] > 0
        assert graph["unresolved"]["undetermined"] > 0
        assert progress["records_incomplete"] == progress["records_unattempted"] == 0
    else:
        assert status["status"] == "retryable_failure", status
        assert progress["completion"] == "incomplete"
        assert progress["records_incomplete"] > 0
        assert graph["coverage"]["records_incomplete"] > 0
        assert status["error"]["retryable"] is True
    assert graph["relationships"] == graph["properties"] == []


def test_no_legal_slots_can_finish_without_fabricating_whole_document_negation(
    client, db, analyst_headers, fake_engine, tmp_path, monkeypatch,
):
    status, graph, adapter = _run_case(
        client, db, analyst_headers, fake_engine, tmp_path, monkeypatch, slot_count=0,
    )
    assert status["status"] == "finished", status
    assert status["progress"]["completion"] == "policy_complete"
    assert status["progress"]["records_planned"] == 0
    assert graph["coverage"]["candidate_policy"] == POLICY
    assert graph["coverage"]["records_examined"] == 0
    assert graph["relationships"] == graph["properties"] == []
    assert graph["unresolved"]["unsupported"] == 0
    assert all(entity["seed_origin"] == "user_selected" for entity in graph["entities"])
    assert adapter.calls == []


def test_legacy_pending_and_finished_payloads_do_not_receive_candidate_policy(
    client, db, analyst_headers, fake_engine, tmp_path, monkeypatch,
):
    status, graph, _adapter = _run_case(
        client, db, analyst_headers, fake_engine, tmp_path, monkeypatch,
        slot_count=0, legacy=True,
    )
    assert status["status"] == "finished", status
    assert "candidate_policy" not in status["progress"]
    assert "completion" not in status["progress"]
    assert "candidate_policy" not in graph["coverage"]
