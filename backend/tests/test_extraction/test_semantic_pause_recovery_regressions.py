"""Operator pauses must remain resumable across model stage boundaries."""

from types import SimpleNamespace

from docx import Document

from app.models.document_analysis import DocumentAnalysisRun
from app.services.document_analysis import execution as execution_service
from app.services.extraction.ontology_guided.executor import OntologyGuidedExecutor
from app.services.extraction.ontology_guided.metadata import prepare_metadata
from app.services.extraction.word_analysis import analyze_word_core
from app.services.llm.model_runtime import ModelCancelled
from tests.test_extraction.test_document_analysis_execution_recovery import (
    _claim,
    _create_pending_run,
)
from tests.test_extraction.test_semantic_model_call_budget import TwoStageAdapter
from tests.test_extraction.test_semantic_ranking_execution import ROOT, fixture


def test_repeated_operator_pause_does_not_exhaust_technical_retry_opportunity(tmp_path):
    ontology, arguments = fixture(tmp_path)
    ontology.classes[ROOT].declared_properties = (
        ontology.classes[ROOT].declared_properties[:1]
    )
    document = Document()
    document.add_paragraph("仅有一条待核验来源。")
    path = tmp_path / "one-record.docx"
    document.save(path)
    analysis = analyze_word_core(path)
    arguments.update(
        ir=analysis.ir,
        metadata=prepare_metadata(
            analysis.ir,
            section_tree=analysis.structure.section_tree.to_dict(),
            summary_version="repeated-pause",
        ),
    )
    adapter, state = TwoStageAdapter(), None
    for _ in range(2):
        stop_after_calls, batches = len(adapter.calls) + 1, []
        executor = OntologyGuidedExecutor(
            ontology=ontology,
            engine=object(),
            adapter=adapter,
            max_model_calls_per_record=6,
            progress_hook=lambda _stage: len(adapter.calls) < stop_after_calls,
        )
        executor.run(**arguments, resume_state=state, batch_hook=batches.append)
        state = batches[-1].model_dump(mode="json")
    result = OntologyGuidedExecutor(
        ontology=ontology,
        engine=object(),
        adapter=adapter,
        max_model_calls_per_record=6,
    ).run(**arguments, resume_state=state)
    assert result.graph.progress.completion == "in_scope_complete"
    assert result.graph.progress.records_incomplete == 0
    assert len(adapter.calls) == 4


def test_inflight_model_cancellation_acknowledges_durable_operator_pause(
    client, db, analyst_headers, tmp_path, monkeypatch,
):
    run_id = _create_pending_run(
        client, analyst_headers, tmp_path, monkeypatch, key="inflight-operator-pause"
    )
    store, token = _claim(db, run_id)
    monkeypatch.setattr(
        execution_service,
        "_LeaseKeeper",
        lambda **_kwargs: SimpleNamespace(
            start=lambda: None,
            stop=lambda: None,
            raise_if_lost=lambda: None,
            should_stop=lambda: True,
        ),
    )

    def pause_during_model(_db, _store, _run, _token, **_kwargs):
        current = store.get_owned(run_id, "analyst")
        store.request_control(
            run_id,
            "analyst",
            action="pause",
            expected_revision=current.revision,
            request_key="pause-during-model",
        )
        db.commit()
        # The lease keeper exposes the durable pause as should_stop=True;
        # the local model client responds with this exact cancellation type.
        raise ModelCancelled()

    monkeypatch.setattr(execution_service, "_execute_claimed", pause_during_model)
    execution_service._execute_dispatched_run(
        db, store, store.get_owned(run_id, "analyst"), token
    )
    db.expire_all()
    run = db.get(DocumentAnalysisRun, run_id)
    assert run.execution_status == "paused"
    assert run.stop_reason == "operator_pause"
    assert run.error is None
