"""Rerun is observable and paused valid candidates remain reviewable."""

import json

import pytest
from docx import Document
from fastapi import BackgroundTasks, HTTPException

from app.api import extraction
from app.dependencies import Identity
from app.models.entity_shadow import EntityShadow
from app.models.evidence import EvidenceJobState
from app.models.extraction import AnnotationExecution, ExtractionJob
from app.services.extraction.extraction_tasks import ExtractionRun
from app.services.extraction.progress import progress_bus
from app.services.extraction.word_analysis import analyze_word_core


@pytest.fixture
def rerun_job(db, tmp_path, monkeypatch):
    source = tmp_path / "source.docx"
    document = Document()
    document.add_paragraph("药品 A")
    document.save(source)
    job = ExtractionJob(
        source_type="word", source_filename=source.name, document_path=str(source),
        source_config={"doc_class_iri": "urn:Drug"}, status="reviewing",
    )
    db.add(job)
    db.commit()
    cache = tmp_path / "annotation.json"
    checkpoint = tmp_path / "checkpoint.json"
    cache.write_text("old")
    checkpoint.write_text("old")
    monkeypatch.setattr(extraction, "_annotation_cache_path", lambda _: cache)
    monkeypatch.setattr(extraction, "_annotation_checkpoint_path", lambda _: checkpoint)
    progress_bus.reset(str(job.id))
    return job, cache, checkpoint


@pytest.mark.asyncio
async def test_rerun_resets_progress_and_marks_job_before_background(
    db, rerun_job, fake_engine,
):
    job, cache, checkpoint = rerun_job
    progress_bus.publish(str(job.id), {"stage": "done"})
    background = BackgroundTasks()

    result = await extraction.rerun_annotation(
        job.id, background, db, fake_engine, Identity("analyst", "senior_analyst"),
    )

    assert result["status"] == "restarted" and result["run_id"]
    assert result["job_id"] == str(job.id)
    db.refresh(job)
    assert job.status == "annotating"
    assert not cache.exists() and not checkpoint.exists()
    assert progress_bus.history(str(job.id)) == [{
        "job_id": str(job.id), "stage": "annotating", "annotation_stage": "queued",
        "pct": 0, "status": "running", "degraded": False,
        "run_id": result["run_id"], "has_checkpoint": False, "can_resume": False,
    }]
    assert len(background.tasks) == 1
    with pytest.raises(HTTPException) as error:
        await extraction.rerun_annotation(
            job.id, BackgroundTasks(), db, fake_engine,
            Identity("analyst", "senior_analyst"),
        )
    assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_rerun_recovers_stale_annotating_state_after_process_restart(
    db, rerun_job, fake_engine,
):
    job, _cache, _checkpoint = rerun_job
    job.status = "annotating"
    db.commit()
    # An empty process-local history represents a worker/API process restart.
    progress_bus.reset(str(job.id))
    background = BackgroundTasks()

    result = await extraction.rerun_annotation(
        job.id, background, db, fake_engine, Identity("analyst", "senior_analyst"),
    )

    assert result["status"] == "restarted" and result["run_id"]
    assert len(background.tasks) == 1


@pytest.mark.asyncio
async def test_rerun_backfills_historical_job_type_from_uploaded_document(
    db, rerun_job, fake_engine,
):
    job, _cache, _checkpoint = rerun_job
    class_iri = "https://ontology.pharma-gmp.cn/slpra/drug-development/CMCReport"
    document_iri = "http://slpra.org/facts#historical-upload"
    job.source_config = None
    db.add(EntityShadow(
        iri=document_iri, class_iri=class_iri, label_zh="风险评估输入.docx",
        module="document", properties_json={"job_id": str(job.id)},
    ))
    db.commit()

    await extraction.rerun_annotation(
        job.id, BackgroundTasks(), db, fake_engine,
        Identity("analyst", "senior_analyst"),
    )

    db.refresh(job)
    assert job.source_config == {"doc_class_iri": class_iri, "doc_ref": document_iri}


@pytest.mark.asyncio
async def test_resume_rejects_live_run_but_recovers_stale_annotating_state(
    db, rerun_job, fake_engine,
):
    job, _cache, checkpoint = rerun_job
    checkpoint.write_text(json.dumps({"completed": {}}))
    job.status = "annotating"
    db.commit()
    progress_bus.reset(str(job.id))
    background = BackgroundTasks()

    result = await extraction.resume_annotation(
        job.id, background, db, fake_engine, Identity("analyst", "senior_analyst"),
    )

    assert result["status"] == "resumed" and result["has_checkpoint"] is True
    assert result["run_id"]
    assert len(background.tasks) == 1
    with pytest.raises(HTTPException) as error:
        await extraction.resume_annotation(
            job.id, BackgroundTasks(), db, fake_engine,
            Identity("analyst", "senior_analyst"),
        )
    assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_paused_run_publishes_valid_candidates_and_cache(
    db, rerun_job, fake_engine, monkeypatch,
):
    job, cache, checkpoint_path = rerun_job
    ir = analyze_word_core(job.document_path).ir
    candidate = {
        "candidate_id": "drug-a", "kind": "entity", "class_iri": "urn:Drug",
        "text": "药品 A", "validation_status": "passed",
        "provenance": [{
            "kind": "document",
            "anchors": [
                ir.anchor(ir.evidence_units[0].evidence_id).model_dump(mode="json")
            ],
        }],
    }
    checkpoint = {"input_id": "run", "completed": {}, "failures": {},
                  "attempt_count": 1, "model_calls": 1, "candidate_ids": ["drug-a"],
                  "joint_completed": {}}
    payload = {
        "_version": extraction._ANNOTATOR_VERSION, "source_type": "word",
        "analysis": ir.model_dump(mode="json"), "evidence_run": {
            "input_id": "run", "completion": "incomplete", "degraded": False,
            "candidates": [candidate], "tasks": [], "diagnostics": ["task_budget_or_pause"],
            "checkpoint": checkpoint,
        }, "content": {"type": "doc", "content": []}, "warnings": [],
        "triples": [], "relationships": [], "_checkpoint": checkpoint,
    }
    monkeypatch.setattr(extraction, "_compute_annotation", lambda *_args, **_kwargs: payload)

    await extraction._precompute_annotation_bg(job.id, fake_engine, db)

    db.expire_all()
    state = db.get(EvidenceJobState, job.id)
    assert state.extraction_run["candidates"][0]["text"] == "药品 A"
    assert db.get(ExtractionJob, job.id).status == "paused"
    assert json.loads(cache.read_text())["evidence_run"]["completion"] == "incomplete"
    assert json.loads(checkpoint_path.read_text())["candidate_ids"] == ["drug-a"]
    assert progress_bus.history(str(job.id))[-1]["annotation_stage"] == "paused"


@pytest.mark.asyncio
async def test_incremental_transaction_is_visible_before_summary_and_uses_worker_session(
    db, rerun_job, fake_engine, monkeypatch,
):
    from sqlalchemy import event
    from sqlalchemy.orm import Session

    from app.schemas.evidence import Candidate, DocumentProvenance
    from app.services.extraction.candidate_store import CandidateStore

    job, cache, _ = rerun_job
    ir = analyze_word_core(job.document_path).ir
    candidate = Candidate(
        candidate_id="incremental", kind="entity", class_iri="urn:Drug", text="药品 A",
        validation_status="passed", provenance=[DocumentProvenance(
            anchors=[ir.anchor(ir.evidence_units[0].evidence_id)], excerpts=["药品 A"]
        )],
    )
    run = ExtractionRun(input_id="incremental-run", candidates=[candidate])
    partial = {"source_type": "word", "analysis": ir.model_dump(mode="json"),
               "evidence_run": run.model_dump(mode="json")}
    sessions = []
    original = extraction._persist_evidence_payload

    def persist(*args, **kwargs):
        worker_db = args[2]
        assert worker_db is not db
        sessions.append(worker_db)
        commits = []
        def callback(session):
            commits.append(1)
        event.listen(worker_db, "after_commit", callback)
        try:
            result = original(*args, **kwargs)
        finally:
            event.remove(worker_db, "after_commit", callback)
        assert commits == []  # the ownership fence commits candidates + progress together
        return result

    def compute(*args, snapshot_fn, defer_summary_fn, **kwargs):
        kwargs["checkpoint_fn"]({"input_id": "incremental-run", "attempt_count": 1})
        snapshot_fn(partial)
        with Session(db.bind) as reader:
            assert reader.get(ExtractionJob, job.id).status == "annotating"
            assert len(CandidateStore(reader).list(job.id)) == 1
            head = reader.get(AnnotationExecution, job.id)
            assert head.progress["candidate_count"] == 1
            assert head.progress["data_revision"] > 0
        latest = progress_bus.history(str(job.id))[-1]
        assert latest["status"] == "running" and latest["candidate_count"] == 1
        assert latest["data_revision"] > 0

        def summarize():
            assert progress_bus.history(str(job.id))[-1]["annotation_stage"] == "summarizing"
            assert json.loads(cache.read_text())["evidence_run"]["completion"] == "complete"
            assert extraction._load_annotation_checkpoint(job.id)["attempt_count"] == 1
            return {"summary": "deferred chapter"}

        defer_summary_fn(summarize)
        return {**partial, "evidence_run": {**partial["evidence_run"], "completion": "complete"}}

    monkeypatch.setattr(extraction, "_compute_annotation", compute)
    monkeypatch.setattr(extraction, "_persist_evidence_payload", persist)
    await extraction._precompute_annotation_bg(job.id, fake_engine, db)
    assert len(sessions) == 2 and sessions[0] is sessions[1]
    assert json.loads(cache.read_text())["section_tree"]["summary"] == "deferred chapter"
    assert extraction._load_annotation_checkpoint(job.id) is None


def test_late_summary_cannot_overwrite_a_new_run(rerun_job):
    job, cache, _ = rerun_job
    extraction._write_annotation_cache(job.id, {"annotation_run_id": "new", "section_tree": {}})
    extraction._write_annotation_cache(
        job.id, {"annotation_run_id": "old", "section_tree": {"stale": True}}, expected_run="old"
    )
    assert json.loads(cache.read_text()) == {"annotation_run_id": "new", "section_tree": {}}


def test_paused_compute_uses_fallback_summary_without_another_model_call(
    db, rerun_job, fake_engine, monkeypatch,
):
    from app.services.extraction import local_semantic_model, word_tree_summarizer

    job, _cache, _checkpoint_path = rerun_job
    checkpoint = {
        "input_id": "paused-run", "completed": {}, "failures": {},
        "attempt_count": 1, "model_calls": 1, "candidate_ids": [],
        "joint_completed": {},
    }

    class PausedRunner:
        schema = {}

        def run(self, *_args, **_kwargs):
            return ExtractionRun(
                input_id="paused-run", completion="incomplete",
                diagnostics=["task_budget_or_pause"], checkpoint=checkpoint,
            )

    monkeypatch.setattr(
        local_semantic_model, "configured_generic_runner", lambda _engine: PausedRunner(),
    )

    def forbidden_summary(*_args, **_kwargs):
        raise AssertionError("paused annotation must not start chapter-summary model calls")

    monkeypatch.setattr(word_tree_summarizer, "summarize_word_tree", forbidden_summary)

    result = extraction._compute_annotation(
        job, fake_engine, should_pause_fn=lambda: True,
    )

    assert result["_checkpoint"] == checkpoint
    assert result["evidence_run"]["diagnostics"] == ["task_budget_or_pause"]
    assert result["section_tree"]["layer_metadata"]["summary_source"] == "extractive_fallback"


@pytest.mark.asyncio
async def test_checkpoint_and_counts_survive_worker_failure(
    db, rerun_job, fake_engine, monkeypatch,
):
    job, _cache, checkpoint_path = rerun_job
    checkpoint = {"input_id": "recoverable-run", "attempt_count": 3,
                  "model_calls": 5, "completed": {"one": [], "two": []},
                  "failures": {"three": {"reason": "source_quote_outside_scope"}}}

    def interrupted_compute(*_args, checkpoint_fn, **_kwargs):
        checkpoint_fn(checkpoint)
        assert json.loads(checkpoint_path.read_text()) == checkpoint
        event = progress_bus.history(str(job.id))[-1]
        assert event["annotation_stage"] == "extracting"
        assert event["tasks_processed"] == 3
        assert event["tasks_completed"] == 2
        assert event["tasks_failed"] == 1
        assert event["model_calls"] == 5
        raise RuntimeError("worker interrupted after a saved task")

    monkeypatch.setattr(extraction, "_compute_annotation", interrupted_compute)
    await extraction._precompute_annotation_bg(job.id, fake_engine, db)
    assert json.loads(checkpoint_path.read_text()) == checkpoint
    assert progress_bus.history(str(job.id))[-1]["has_checkpoint"] is True
    db.expire_all()  # background work owns a separate Session
    assert db.get(ExtractionJob, job.id).status == "failed"


@pytest.mark.asyncio
@pytest.mark.parametrize("status,stage", [("annotating", "interrupted"), ("paused", "paused")])
async def test_progress_recovers_durable_state_after_restart(db, rerun_job, status, stage):
    job, _cache, checkpoint_path = rerun_job
    job.status = status
    db.commit()
    checkpoint_path.write_text(json.dumps({"attempt_count": 17, "model_calls": 22}))
    response = await extraction.job_progress(job.id, Identity("analyst", "senior_analyst"), db)
    messages = [message async for message in response.body_iterator]
    assert len(messages) == 1
    event = json.loads(messages[0].removeprefix("data: "))
    assert event["annotation_stage"] == stage
    assert event["has_checkpoint"] is True
    assert event["tasks_processed"] == 17
    assert event["model_calls"] == 22
    assert event["status"] != "running"


@pytest.mark.asyncio
async def test_progress_reconnection_starts_at_latest_task():
    from app.services.extraction.progress import ProgressBus

    bus = ProgressBus()
    bus.publish("job", {"annotation_stage": "queued", "status": "running"})
    bus.publish("job", {
        "annotation_stage": "extracting", "status": "running", "tasks_processed": 12,
    })
    stream = bus.stream("job", latest_only=True)
    assert (await anext(stream))["tasks_processed"] == 12
    bus.publish("job", {
        "annotation_stage": "extracting", "status": "running", "tasks_processed": 13,
    })
    assert (await anext(stream))["tasks_processed"] == 13
    await stream.aclose()


@pytest.mark.asyncio
async def test_resume_does_not_queue_an_exhausted_run(db, rerun_job, fake_engine, monkeypatch):
    from app.config import settings

    job, _cache, checkpoint_path = rerun_job
    monkeypatch.setattr(settings, "evidence_max_tasks", 8)
    checkpoint_path.write_text(json.dumps({"attempt_count": 8, "completed": {}}))
    background = BackgroundTasks()
    with pytest.raises(HTTPException) as error:
        await extraction.resume_annotation(job.id, background, db, fake_engine,
                                           Identity("analyst", "senior_analyst"))
    assert error.value.status_code == 422
    assert not background.tasks
    assert json.loads(checkpoint_path.read_text())["attempt_count"] == 8


@pytest.mark.asyncio
async def test_selected_template_priorities_are_frozen_until_rerun(
    db, rerun_job, fake_engine,
):
    from app.models.extraction import AstTemplate

    job, _cache, checkpoint_path = rerun_job
    template = AstTemplate(name="priority-fixture", version="1", iri_pattern="urn:Drug",
                           schema_json={"sections": [{"coverage": [{
                               "kind": "ontology_relation", "doc_class_iri": "urn:Drug",
                               "predicate_iri": "urn:hasPlan", "required_properties": ["urn:upper"],
                           }]}]})
    db.add(template)
    db.commit()
    await extraction.rerun_annotation(job.id, BackgroundTasks(), db, fake_engine,
                                     Identity("analyst", "senior_analyst"), template.id)
    db.refresh(job)
    frozen = dict(job.source_config)
    assert frozen["recognition_template_id"] == str(template.id)
    assert frozen["extraction_priority_paths"] == [["urn:hasPlan", "urn:upper"], ["urn:hasPlan"]]
    template.schema_json = {"sections": []}
    job.status = "paused"
    db.get(AnnotationExecution, job.id).status = "paused"
    db.commit()
    progress_bus.reset(str(job.id))
    checkpoint_path.write_text(json.dumps({"attempt_count": 1, "completed": {}}))
    await extraction.resume_annotation(job.id, BackgroundTasks(), db, fake_engine,
                                      Identity("analyst", "senior_analyst"))
    db.refresh(job)
    assert job.source_config == frozen


@pytest.mark.asyncio
async def test_missing_explicit_template_keeps_previous_cache_and_checkpoint(
    db, rerun_job, fake_engine,
):
    import uuid

    job, cache, checkpoint = rerun_job
    with pytest.raises(HTTPException) as error:
        await extraction.rerun_annotation(job.id, BackgroundTasks(), db, fake_engine,
                                         Identity("analyst", "senior_analyst"), uuid.uuid4())
    assert error.value.status_code == 404
    assert cache.read_text() == checkpoint.read_text() == "old"
