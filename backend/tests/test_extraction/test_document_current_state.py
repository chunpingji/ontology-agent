"""Current-state persistence and cold restart through the actual run executor."""

from collections import Counter

import pytest
from sqlalchemy import delete, func, select

from app.config import settings
from app.models.document_analysis import (
    DocumentAnalysisArtifact,
    DocumentAnalysisRun,
    DocumentRecognitionEventBatch,
    DocumentRunCurrentState,
)
from app.services.document_analysis import current_state, execution
from app.services.extraction.ontology_guided.semantic_reranker import RankingPolicy, RankingService
from tests.test_extraction.test_document_analysis_execution_recovery import (
    CountingAdapter,
    SimulatedProcessCrash,
    _claim,
    _create_pending_run,
    _expire_lease,
)


@pytest.mark.parametrize("adaptive_mode", ["disabled", "enhanced"])
def test_current_run_cold_resume_has_no_snapshot_history(
    client,
    db,
    analyst_headers,
    tmp_path,
    monkeypatch,
    adaptive_mode,
):
    monkeypatch.setattr(settings, "document_analysis_performance_enabled", True)
    run_id = _create_pending_run(
        client,
        analyst_headers,
        tmp_path,
        monkeypatch,
        key="current-state-cold",
        current_state=True,
        evidence_repair=adaptive_mode == "enhanced",
        adaptive_mode=adaptive_mode,
    )
    adapter = CountingAdapter()
    monkeypatch.setattr(execution, "configured_model_adapter", lambda **kw: adapter)
    store, token = _claim(db, run_id)
    original = current_state.persist_batch

    def persist_then_crash(*args, **kwargs):
        from app.services.document_analysis.run_store import HeadConflict

        version = original(*args, **kwargs)
        assert original(*args, **kwargs) == version
        changed = kwargs["batch"].model_copy(deep=True)
        changed.outcome.reason = "same batch identity with different content"
        with pytest.raises(HeadConflict):
            original(*args, **{**kwargs, "batch": changed})
        raise SimulatedProcessCrash()

    monkeypatch.setattr(current_state, "persist_batch", persist_then_crash)
    with pytest.raises(SimulatedProcessCrash):
        execution._execute_claimed(db, store, store.get_owned(run_id, "analyst"), token)
    first = adapter.calls[0]
    receipt = db.scalar(
        select(DocumentRecognitionEventBatch).where(
            DocumentRecognitionEventBatch.recognition_run_id == run_id
        )
    )
    assert receipt.checkpoint_artifact_id is None
    assert receipt.committed_work_version > 0
    assert (
        db.scalar(
            select(func.count())
            .select_from(DocumentAnalysisArtifact)
            .where(
                DocumentAnalysisArtifact.artifact_kind.in_(
                    [
                        "graph",
                        "recognition_checkpoint",
                        "state_block",
                        "ranking_state",
                        "public_graph",
                    ]
                )
            )
        )
        == 0
    )
    monkeypatch.setattr(current_state, "persist_batch", original)
    _expire_lease(db, run_id)
    store, token = _claim(db, run_id)
    execution._execute_claimed(db, store, store.get_owned(run_id, "analyst"), token)
    db.expire_all()
    assert db.get(DocumentAnalysisRun, run_id).execution_status == "finished"
    assert Counter(adapter.calls)[first] == 1
    assert (
        db.scalar(
            select(func.count())
            .select_from(DocumentRunCurrentState)
            .where(
                DocumentRunCurrentState.recognition_run_id == run_id,
                DocumentRunCurrentState.domain == "display:public_graph",
            )
        )
        == 1
    )
    response = client.get(f"/api/document-analysis/runs/{run_id}/graph", headers=analyst_headers)
    assert response.status_code == 200, response.text
    before = response.json()["graph_snapshot"]
    db.execute(
        delete(DocumentRunCurrentState).where(
            DocumentRunCurrentState.recognition_run_id == run_id,
            DocumentRunCurrentState.domain.in_(["display:public_graph", "display:nodes"]),
        )
    )
    db.commit()
    rebuilt = client.get(f"/api/document-analysis/runs/{run_id}/graph", headers=analyst_headers)
    assert rebuilt.status_code == 200, rebuilt.text
    assert rebuilt.json()["graph_snapshot"] == before

    # Missing authority must stop resume even though the display can be rebuilt.
    db.execute(
        delete(DocumentRunCurrentState).where(
            DocumentRunCurrentState.recognition_run_id == run_id,
            DocumentRunCurrentState.domain == "work:control",
        )
    )
    db.commit()
    run = store.get_owned(run_id, "analyst")
    with pytest.raises(Exception, match="current work control is missing"):
        current_state.restore_work(store, run, run.run_fingerprint)


def test_paid_ranking_before_work_commit_is_reused(
    client,
    db,
    analyst_headers,
    tmp_path,
    monkeypatch,
):
    from tests.test_extraction.test_semantic_ranking import RankingModel

    monkeypatch.setattr(settings, "document_analysis_performance_enabled", True)
    monkeypatch.setattr(settings, "evidence_max_tasks", 2)
    model, adapter = RankingModel(), CountingAdapter()
    policy = RankingPolicy(mode="semantic", pool_size=2)
    monkeypatch.setattr(execution, "configured_model_adapter", lambda **kw: adapter)
    monkeypatch.setattr(
        execution,
        "_configured_ranking",
        lambda: (
            RankingService(policy, model),
            {"policy": policy.model_dump(mode="json"), "model": model.identity},
        ),
    )
    run_id = _create_pending_run(
        client, analyst_headers, tmp_path, monkeypatch, key="current-paid-pool", current_state=True
    )
    store, token = _claim(db, run_id)
    original = current_state.persist_ranking

    def paid_then_crash(*args, **kwargs):
        original(*args, **kwargs)
        state = args[-1]
        if state["changes"].get("epochs"):
            raise SimulatedProcessCrash()

    monkeypatch.setattr(current_state, "persist_ranking", paid_then_crash)
    with pytest.raises(SimulatedProcessCrash):
        execution._execute_claimed(db, store, store.get_owned(run_id, "analyst"), token)
    assert adapter.calls == []
    before = len(model.calls)
    run = store.get_owned(run_id, "analyst")
    paid = current_state.restore_ranking(store, run, run.run_fingerprint)
    assert paid["service"]["costs"]["model_calls"] > 0
    summary = current_state.read_ranking_summary(store, run)
    db.execute(delete(DocumentRunCurrentState).where(
        DocumentRunCurrentState.recognition_run_id == run_id,
        DocumentRunCurrentState.domain.in_(["display:ranking_summary", "display:ranking_epochs"]),
    ))
    db.commit()
    rebuilt = current_state.read_ranking_summary(store, run)
    assert rebuilt == summary
    assert len(model.calls) == before
    monkeypatch.setattr(current_state, "persist_ranking", original)
    _expire_lease(db, run_id)
    store, token = _claim(db, run_id)
    # Stop after applying one paid pool's first task, before another slot is scored.
    save_batch = current_state.persist_batch

    def commit_then_stop(*args, **kwargs):
        save_batch(*args, **kwargs)
        raise SimulatedProcessCrash()

    monkeypatch.setattr(current_state, "persist_batch", commit_then_stop)
    with pytest.raises(SimulatedProcessCrash):
        execution._execute_claimed(db, store, store.get_owned(run_id, "analyst"), token)
    assert len(model.calls) == before
    assert len(adapter.calls) == 1


def test_current_protocol_result_and_request_survive_paid_response_crash(
    client,
    db,
    analyst_headers,
    tmp_path,
    monkeypatch,
):
    from app.models.document_analysis import DocumentRunRequest, DocumentRunResult
    from app.services.extraction.ontology_guided import model_adapter
    from app.services.extraction.ontology_guided.repair_adapter import EvidenceRepairAdapter

    calls = []

    def respond(_client, **kwargs):
        calls.append(1)
        return {"proposals": [], "refusal_reason": None}

    monkeypatch.setattr(settings, "evidence_max_tasks", 1)
    monkeypatch.setattr(model_adapter, "chat_with_schema", respond)
    monkeypatch.setattr(
        execution,
        "configured_model_adapter",
        lambda **kw: EvidenceRepairAdapter(object(), model_identity="current-protocol-fixture"),
    )
    run_id = _create_pending_run(
        client,
        analyst_headers,
        tmp_path,
        monkeypatch,
        key="current-protocol",
        current_state=True,
        evidence_repair=True,
    )
    store, token = _claim(db, run_id)
    persist = current_state.persist_calls

    def crash_after_paid_response(*args):
        persist(*args)
        if any(row["value"].get("outcome") for row in args[-1]["protocols"].values()):
            raise SimulatedProcessCrash()

    monkeypatch.setattr(current_state, "persist_calls", crash_after_paid_response)
    with pytest.raises(SimulatedProcessCrash):
        execution._execute_claimed(db, store, store.get_owned(run_id, "analyst"), token)
    assert len(calls) == 1
    run = store.get_owned(run_id, "analyst")
    requests = current_state.read_rows(store, run, DocumentRunRequest, prefix="calls:requests")
    request = next(iter(requests["calls:requests"].values()))
    assert request["dispatch_state"] == "completed"
    assert request["cost_status"] == "measured"
    assert request["actual_cost"] == request["reserved_cost"] == 1
    assert request["reservation"]["input_hash"]
    assert (
        current_state.get_row(
            store, run, "calls:results", request["result_ref"], model=DocumentRunResult
        )["field"]
        == "discovery"
    )
    protocol = next(
        iter(current_state.restore_calls(store, run, run.run_fingerprint)["protocols"].values())
    )
    assert protocol["outcome"] and protocol["discovery"]
    monkeypatch.setattr(current_state, "persist_calls", persist)
    _expire_lease(db, run_id)
    store, token = _claim(db, run_id)
    execution._execute_claimed(db, store, store.get_owned(run_id, "analyst"), token)
    assert len(calls) == 1
    assert store.get_owned(run_id, "analyst").progress["model_calls_reserved"] == 1


def test_current_calls_preserve_reserved_cost_across_restart(
    client,
    db,
    analyst_headers,
    tmp_path,
    monkeypatch,
):
    from tests.test_extraction.test_document_analysis_model_call_recovery import TwoStageAdapter

    monkeypatch.setattr(settings, "document_analysis_performance_enabled", True)
    monkeypatch.setattr(settings, "evidence_max_tasks", 2)
    adapter = TwoStageAdapter()
    monkeypatch.setattr(execution, "configured_model_adapter", lambda **kw: adapter)
    run_id = _create_pending_run(
        client, analyst_headers, tmp_path, monkeypatch, key="current-paid-calls", current_state=True
    )
    store, token = _claim(db, run_id)
    original = current_state.persist_batch

    def committed_then_crash(*args, **kwargs):
        original(*args, **kwargs)
        raise SimulatedProcessCrash()

    monkeypatch.setattr(current_state, "persist_batch", committed_then_crash)
    with pytest.raises(SimulatedProcessCrash):
        execution._execute_claimed(db, store, store.get_owned(run_id, "analyst"), token)
    run = store.get_owned(run_id, "analyst")
    state = current_state.restore_calls(store, run, run.run_fingerprint)
    assert sum(state["lineage_calls"].values()) == 2
    assert state["reservation_sequence"] == 2
    first_tasks = {t for t, stage in adapter.calls}
    monkeypatch.setattr(current_state, "persist_batch", original)
    _expire_lease(db, run_id)
    store, token = _claim(db, run_id)
    execution._execute_claimed(db, store, store.get_owned(run_id, "analyst"), token)
    run = store.get_owned(run_id, "analyst")
    after = current_state.restore_calls(store, run, run.run_fingerprint)
    assert sum(after["lineage_calls"].values()) == 4
    assert all(Counter(t for t, stage in adapter.calls)[t] == 2 for t in first_tasks)


def test_control_change_during_batch_preparation_does_not_conflict(
    client,
    db,
    analyst_headers,
    tmp_path,
    monkeypatch,
):
    from app.api import document_analysis

    monkeypatch.setattr(settings, "document_analysis_performance_enabled", True)
    monkeypatch.setattr(document_analysis, "notify_document_analysis_dispatcher", lambda: True)
    run_id = _create_pending_run(
        client, analyst_headers, tmp_path, monkeypatch, key="current-pause-race", current_state=True
    )
    adapter = CountingAdapter()
    monkeypatch.setattr(execution, "configured_model_adapter", lambda **kw: adapter)
    store, token = _claim(db, run_id)
    prepare = current_state.display_payload
    paused = False

    def prepare_and_pause(*args, **kwargs):
        nonlocal paused
        result = prepare(*args, **kwargs)
        if not paused and kwargs.get("changes") is not None:
            paused = True
            run = store.get_owned(run_id, "analyst")
            store.request_control(
                run_id,
                "analyst",
                action="pause",
                expected_revision=run.revision,
                request_key="pause-during-work",
            )
            db.commit()
        return result

    monkeypatch.setattr(current_state, "display_payload", prepare_and_pause)
    execution._execute_claimed(db, store, store.get_owned(run_id, "analyst"), token)
    run = store.get_owned(run_id, "analyst")
    assert run.execution_status == "paused"
    assert len(adapter.calls) == 1
    assert (
        db.scalar(
            select(func.count())
            .select_from(DocumentRecognitionEventBatch)
            .where(DocumentRecognitionEventBatch.recognition_run_id == run_id)
        )
        == 1
    )


def test_current_property_review_and_repair_use_exact_original_task(
    client,
    db,
    analyst_headers,
    tmp_path,
    monkeypatch,
):
    from app.api import document_analysis
    from app.models.document_analysis_review import DocumentPropertyRepair
    from app.services.document_analysis import application
    from app.services.extraction.ontology_guided.contracts import (
        GraphProperty,
        OntologySnapshot,
        SlotSpec,
        VersionedRef,
    )
    from app.services.extraction.ontology_guided.executor import TaskOutcome
    from tests.test_extraction.test_document_analysis_execution_recovery import ROOT_IRI
    from tests.test_extraction.test_ontology_guided_core import _definition

    monkeypatch.setattr(settings, "evidence_max_tasks", 1)
    monkeypatch.setattr(document_analysis, "notify_document_analysis_dispatcher", lambda: True)
    snapshot = OntologySnapshot(
        snapshot_id="current-review-ontology",
        ontology_hash="a" * 64,
        classes={
            ROOT_IRI: _definition(
                ROOT_IRI, "报告", properties=[SlotSpec(iri="urn:current:property", label="产品")]
            )
        },
        created_from="frozen_fixture",
    )
    monkeypatch.setattr(application, "ontology_snapshot_from_engine", lambda *_: snapshot)

    class PropertyAdapter:
        model_identity = "current-property-review-fixture"

        def inspect(self, task, context, predicate, menu):
            assert predicate.kind == "property"
            source = next(f for f in context.fragments if f.fact_eligible)
            return TaskOutcome(
                semantic_outcome="supported",
                reason_code="supported",
                reason="合成审核用例",
                properties=[
                    GraphProperty(
                        candidate_id="current-property",
                        revision=1,
                        subject_ref=VersionedRef(
                            id=task.subject.entity_id, revision=task.subject.revision
                        ),
                        predicate_iri=predicate.iri,
                        predicate_label=predicate.label,
                        raw_value="测试值",
                        decision_status="supported",
                        structural_valid=True,
                        model_supported=True,
                        policy_eligible=True,
                        proof_ref=VersionedRef(id="test-proof", revision=1),
                        decision_refs=[VersionedRef(id="test-decision", revision=1)],
                        evidence_refs=[source.anchor],
                        value_evidence_refs=[source.anchor],
                    )
                ],
            )

    monkeypatch.setattr(execution, "configured_model_adapter", lambda **kw: PropertyAdapter())
    run_id = _create_pending_run(
        client,
        analyst_headers,
        tmp_path,
        monkeypatch,
        key="current-review",
        current_state=True,
        evidence_repair=True,
    )
    store, token = _claim(db, run_id)
    execution._execute_claimed(db, store, store.get_owned(run_id, "analyst"), token)
    run = store.get_owned(run_id, "analyst")
    version = run.work_version
    from app.models.document_analysis import DocumentRunCandidate

    assert db.get(DocumentRunCandidate, (run.recognition_run_id, "current-property", 1)) is not None
    response = client.post(
        f"/api/document-analysis/runs/{run_id}/reviews",
        headers=analyst_headers,
        json={
            "request_key": "review-current",
            "expected_run_revision": run.revision,
            "graph_snapshot_id": run.graph_snapshot_id,
            "candidate_id": "current-property",
            "candidate_revision": 1,
            "expected_review_revision": 0,
            "decision": "rejected",
            "reason_code": "incorrect_value",
            "reason": "核对原文",
        },
    )
    assert response.status_code == 201, response.text
    receipt = response.json()
    repaired = client.post(
        f"/api/document-analysis/runs/{run_id}/repairs",
        headers=analyst_headers,
        json={
            "request_key": "repair-current",
            "expected_run_revision": receipt["run"]["run_revision"],
            "review_id": receipt["review"]["review_id"],
        },
    )
    assert repaired.status_code == 202, repaired.text
    row = db.scalar(
        select(DocumentPropertyRepair).where(DocumentPropertyRepair.recognition_run_id == run_id)
    )
    assert row.base_checkpoint_artifact_id is None
    assert row.base_work_version == version
    assert row.payload["after_outcomes"] == 1
    assert row.payload["target"]["original_task"]["predicate_kind"] == "property"
