import uuid
from copy import deepcopy

import pytest

from app.models.evidence import EvidenceJobState
from app.models.extraction import ExtractionJob
from app.services.extraction.candidate_store import CandidateConflict
from app.services.extraction.gap_workflow import reserve_round, update_run


def state(db):
    job = ExtractionJob(id=uuid.uuid4(), source_type="word")
    db.add(job)
    db.flush()
    row = EvidenceJobState(job_id=job.id, revision=0, extraction_run={"completion": "complete"})
    db.add(row)
    db.commit()
    return row


def test_reservation_is_durable_and_repeated_input_does_not_consume_budget(db):
    row = state(db)
    first, reason = reserve_round(db, row.job_id, 0, "input-1", "analyst", 2)
    assert reason is None and first["round"] == 1
    db.expire_all()
    assert (
        db.get(EvidenceJobState, row.job_id).extraction_run["gap_history"][0]["reason"] == "running"
    )
    _, reason = reserve_round(db, row.job_id, 1, "input-1", "analyst", 2)
    assert reason == "identical_input_already_processed"
    _, reason = reserve_round(db, row.job_id, 1, "input-2", "analyst", 2)
    assert reason == "gap_round_in_progress"


def test_cas_and_two_round_limit(db):
    row = state(db)
    run = deepcopy(row.extraction_run)
    update_run(db, row.job_id, 0, {**run, "diagnostics": []})
    db.commit()
    with pytest.raises(CandidateConflict):
        update_run(db, row.job_id, 0, run)
    db.rollback()
    for number in (1, 2):
        current = db.get(EvidenceJobState, row.job_id, populate_existing=True)
        entry, reason = reserve_round(
            db, row.job_id, current.revision, f"key-{number}", "analyst", 2
        )
        assert reason is None
        current = db.get(EvidenceJobState, row.job_id, populate_existing=True)
        run = deepcopy(current.extraction_run)
        run["gap_history"][-1]["reason"] = "no_new_evidence"
        update_run(db, row.job_id, current.revision, run)
        db.commit()
    current = db.get(EvidenceJobState, row.job_id, populate_existing=True)
    _, reason = reserve_round(db, row.job_id, current.revision, "third", "analyst", 9)
    assert reason == "gap_round_budget_exhausted"


def test_partial_entity_gap_keeps_valid_candidates_but_not_a_complete_task(tmp_path):
    import json

    from docx import Document

    from app.schemas.evidence import Candidate, DocumentProvenance, TaskBudget
    from app.services.extraction.extraction_tasks import GenericExtractionRunner
    from app.services.extraction.gap_extraction import run_gap_round
    from app.services.extraction.word_analysis import analyze_word_core
    from app.services.ontology_instance_writer import instance_iri
    from tests.test_extraction.test_hierarchical_context import TestTokenizer

    doc = Document()
    doc.add_paragraph("资料包含设备 E")
    path = tmp_path / "gap.docx"
    doc.save(path)
    ir = analyze_word_core(path).ir
    root = Candidate(
        candidate_id="root", kind="entity", class_iri="urn:Report", text="资料",
        validation_status="passed", identity={"document_root": ir.document_hash},
        provenance=[DocumentProvenance(anchors=[ir.anchor(ir.evidence_units[0].evidence_id)])],
    )

    class Selector:
        def select(self, *args):
            return {"objects": []}

        def class_matches(self, left, right):
            return left == right

    def model(system, user, schema, budget):
        context = json.loads(json.loads(user)["context"])
        evidence_id = context["fragments"][0]["anchor"]["evidence_id"]
        return {"entities": [
            {"class_iri": "urn:Device", "mention": {"evidence_id": evidence_id, "text": text}}
            for text in ("设备 E", "不存在的设备 F")
        ]}

    runner = GenericExtractionRunner(
        {"urn:Report": {"relationships": [{"iri": "urn:uses", "range": ["urn:Device"]}]},
         "urn:Device": {}}, TestTokenizer(), model, model_identity="gap-fixture",
        budget=TaskBudget(max_input_tokens=20000),
    )
    inputs = {"input_class_iri": "urn:Report", "tasks": [{
        "status": "missing", "reason": "path_or_object_properties_missing",
        "subject_instance_iri": instance_iri(root), "objects": [],
        "predicate_path": [{"predicate_iri": "urn:uses", "direction": "forward"}],
        "range_class_iri": "urn:Device",
    }]}
    values, reason, history = run_gap_round(ir, inputs, [root], Selector(), runner)
    assert reason == "pending_review" and [c.text for c in values] == ["设备 E"]
    assert values[0].review_status == "pending" and values[0].validation_status == "passed"
    assert history[0]["status"] == "incomplete"
