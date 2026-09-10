"""Requests are charged before dispatch and survive interrupted verification."""

from copy import deepcopy

import pytest

from app.services.extraction.ontology_guided.executor import (
    ModelCallPersistenceFailure,
    OntologyGuidedExecutor,
    TaskOutcome,
)
from app.services.llm.model_runtime import ModelCancelled
from tests.test_extraction.test_semantic_ranking_execution import fixture


class ProcessCrash(BaseException):
    pass


class TwoStageAdapter:
    model_identity = "budget-test"

    def __init__(self, crash=False):
        self.calls = []
        self.crash = crash

    def inspect(self, task, context, predicate, menu):
        calls = 0
        for ordinal, stage in enumerate(("discovery", "verification"), 1):
            if calls >= context.remaining_model_calls:
                return TaskOutcome(
                    semantic_outcome="not_checked", complete=False,
                    reason_code="record_model_call_budget_exhausted",
                    reason="Independent verification has no remaining budget.", model_calls=calls,
                )
            context.before_model_call(stage, ordinal)
            self.calls.append((task.task_id, stage))
            calls += 1
            if self.crash:
                raise ProcessCrash()
        return TaskOutcome(
            semantic_outcome="not_checked", reason_code="no_candidate_observed",
            reason="No proposal in this record.", model_calls=calls,
        )


def executor(ontology, adapter, **kwargs):
    return OntologyGuidedExecutor(
        ontology=ontology, engine=object(), adapter=adapter,
        max_model_calls_per_record=2, **kwargs,
    )


def test_failed_reservation_never_dispatches_or_becomes_record_failure(tmp_path):
    ontology, arguments = fixture(tmp_path)
    adapter = TwoStageAdapter()

    def fail(_state):
        raise RuntimeError("owner fence lost")

    with pytest.raises(ModelCallPersistenceFailure):
        executor(ontology, adapter).run(**arguments, model_call_hook=fail)
    assert adapter.calls == []


def test_crash_reservation_survives_without_checkpoint_and_bounds_retry(tmp_path):
    ontology, arguments = fixture(tmp_path)
    adapter, states = TwoStageAdapter(crash=True), []
    with pytest.raises(ProcessCrash):
        executor(ontology, adapter).run(**arguments, model_call_hook=states.append)
    assert len(states) == len(adapter.calls) == 1
    interrupted_task = adapter.calls[0][0]
    adapter.crash = False
    result = executor(ontology, adapter).run(**arguments, model_call_state=states[-1])
    assert sum(task == interrupted_task for task, _ in adapter.calls) == 2
    assert max(result.model_call_state["lineage_calls"].values()) == 2
    assert any(
        payload["outcome"]["reason_code"] == "record_model_call_budget_exhausted"
        for name, payload in result.events if name == "task_outcome"
    )
    assert result.graph.progress.records_incomplete > 0
    assert result.graph.progress.model_calls_unresolved == 1
    assert result.graph.progress.model_calls_reserved == len(adapter.calls)
    assert result.graph.progress.model_calls + 1 == len(adapter.calls)


def test_pause_after_completed_verification_preserves_outcome_and_replay(tmp_path):
    ontology, arguments = fixture(tmp_path)
    adapter, batches = TwoStageAdapter(), []
    paused = executor(
        ontology, adapter, progress_hook=lambda stage: stage != "after_model",
    ).run(**arguments, batch_hook=batches.append)
    assert batches[0].outcome.complete
    assert batches[0].outcome.model_calls == 2
    assert paused.graph.progress.records_examined == 1
    completed_task = adapter.calls[0][0]
    state = batches[-1].model_dump(mode="json")
    result = executor(ontology, adapter).run(**arguments, resume_state=state)
    assert sum(task == completed_task for task, _ in adapter.calls) == 2
    assert result.graph.progress.records_incomplete == 0
    assert result.graph.progress.model_calls == len(adapter.calls)


def test_cancel_between_stages_propagates_without_spending_next_call(tmp_path):
    ontology, arguments = fixture(tmp_path)
    adapter, states = TwoStageAdapter(), []

    def progress(_stage):
        if adapter.calls:
            raise ModelCancelled()
        return True

    with pytest.raises(ModelCancelled):
        executor(ontology, adapter, progress_hook=progress).run(
            **arguments, model_call_hook=states.append,
        )
    assert len(adapter.calls) == len(states) == 1


def test_foreign_or_malformed_reservations_are_rejected(tmp_path):
    ontology, arguments = fixture(tmp_path)
    adapter, states = TwoStageAdapter(crash=True), []
    with pytest.raises(ProcessCrash):
        executor(ontology, adapter).run(**arguments, model_call_hook=states.append)
    for mutate in (
        lambda state: state.update(run_fingerprint="foreign"),
        lambda state: state["reservations"][0].update(sequence=2),
        lambda state: state.update(lineage_calls={}),
    ):
        state = deepcopy(states[-1])
        mutate(state)
        with pytest.raises(ValueError):
            executor(ontology, adapter).run(**arguments, model_call_state=state)
    assert len(adapter.calls) == 1


def test_repeated_between_stage_pause_does_not_spend_technical_retry(tmp_path):
    from docx import Document

    from app.services.extraction.ontology_guided.metadata import prepare_metadata
    from app.services.extraction.word_analysis import analyze_word_core
    from tests.test_extraction.test_semantic_ranking_execution import FIRST

    ontology, arguments = fixture(tmp_path)
    document = Document()
    document.add_paragraph("一条待独立验证的原文记录。")
    path = tmp_path / "pause.docx"
    document.save(path)
    analysis = analyze_word_core(path)
    arguments.update(ir=analysis.ir, metadata=prepare_metadata(
        analysis.ir, section_tree=analysis.structure.section_tree.to_dict(),
        summary_version="pause-test",
    ))
    adapter, state, batches = TwoStageAdapter(), None, []
    for _ in range(2):
        started = len(adapter.calls)
        runner = OntologyGuidedExecutor(
            ontology=ontology, engine=object(), adapter=adapter,
            max_model_calls_per_record=6,
            predicate_filter=lambda _subject, predicate, _hop: predicate.iri == FIRST,
            progress_hook=lambda stage: stage != "before_model" or len(adapter.calls) == started,
        )
        result = runner.run(**arguments, resume_state=state, batch_hook=batches.append)
        state = batches[-1].model_dump(mode="json")
        assert result.graph.progress.records_incomplete == 1
        assert batches[-1].outcome.reason_code == "execution_pause_requested"
    result = OntologyGuidedExecutor(
        ontology=ontology, engine=object(), adapter=adapter, max_model_calls_per_record=6,
        predicate_filter=lambda _subject, predicate, _hop: predicate.iri == FIRST,
    ).run(**arguments, resume_state=state)
    assert len(adapter.calls) == 4
    assert result.graph.progress.records_examined == 1
    assert result.graph.progress.records_incomplete == 0
    assert result.graph.progress.model_calls == 4
    assert result.graph.progress.model_calls_reserved == 4
    assert result.graph.progress.model_calls_unresolved == 0
