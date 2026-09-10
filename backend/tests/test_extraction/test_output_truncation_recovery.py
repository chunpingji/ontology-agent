"""Truncation recovery preserves coverage, proof gates and durable task ownership."""

import json
from collections import Counter
from copy import deepcopy

import pytest
from docx import Document

from app.schemas.evidence import TaskBudget
from app.services.extraction.checkpoint_journal import CheckpointJournal, read_checkpoint
from app.services.extraction.extraction_tasks import GenericExtractionRunner
from app.services.extraction.word_analysis import analyze_word_core
from app.services.llm.local_client import StructuredModelError
from tests.test_extraction.test_extraction_tasks import accept_fixture_types
from tests.test_extraction.test_hierarchical_context import TestTokenizer


def source(tmp_path, texts=("实体甲", "实体乙")):
    doc = Document()
    for text in texts:
        doc.add_paragraph(text)
    path = tmp_path / "source.docx"
    doc.save(path)
    return analyze_word_core(path).ir


def runner(model, *, schema=None, max_tasks=64, max_output_tokens=2048):
    return GenericExtractionRunner(
        {"urn:TypeA": {}, "urn:TypeB": {}} if schema is None else schema,
        TestTokenizer(), model, model_identity="truncation-fixture",
        budget=TaskBudget(
            max_input_tokens=60000, max_output_tokens=max_output_tokens,
            max_regions_per_task=32, max_tasks=max_tasks,
        ),
    )


def request_parts(user):
    request = json.loads(user)
    context = json.loads(request["context"])
    targets = [f for f in context["fragments"] if f["purpose"] == "target"]
    return request, context["task"], targets


def entity_model(calls, *, bad_text=None):
    def model(system, user, schema, budget):
        request, task, targets = request_parts(user)
        if request["stage"] == "verify_entity_types":
            calls.append(("verify", tuple(c["candidate_id"] for c in
                                          request["candidate"]["proposed_entities"])))
            return accept_fixture_types(request)
        classes = tuple(task["predicate_definition"]["classes"])
        regions = tuple(f["anchor"]["evidence_id"] for f in targets)
        calls.append(("recall", regions, classes, budget.max_output_tokens))
        if len(targets) > 1 or len(classes) > 1:
            raise StructuredModelError("model_output_truncated")
        return {"entities": [{
            "class_iri": classes[0],
            "mention": {"evidence_id": regions[0], "text": bad_text or targets[0]["text"]},
        }]}

    return model


def test_repeated_truncation_partitions_every_source_and_type_without_raising_limit(tmp_path):
    ir, calls = source(tmp_path), []
    run = runner(entity_model(calls)).run(ir)
    assert run.completion == "complete", run.diagnostics
    assert not run.checkpoint["failures"]
    expected = {(u.text, iri) for u in ir.evidence_units for iri in ("urn:TypeA", "urn:TypeB")}
    assert {(c.text, c.class_iri) for c in run.candidates} == expected
    assert all(c.positive_eligible and c.review_status == "pending" for c in run.candidates)
    recalls = [c for c in calls if c[0] == "recall"]
    assert all(c[-1] == 2048 for c in recalls)
    assert run.checkpoint["attempt_count"] == len(recalls)
    assert run.checkpoint["model_calls"] == len(calls)
    assert any(t["status"] == "split" for t in run.tasks)
    assert "model_output_truncated" not in run.diagnostics


def test_split_and_completed_children_survive_journal_replay_and_pause(tmp_path):
    ir, calls = source(tmp_path), []
    worker = runner(entity_model(calls))
    path = tmp_path / "checkpoint.json"
    journal = CheckpointJournal(path, every=100, seconds=1000)
    first = worker.run(ir, pause_after=4, checkpoint_fn=journal.save)
    assert first.completion == "incomplete"
    saved = read_checkpoint(path)
    assert saved == first.checkpoint and saved["output_splits"]
    checked_prefix = deepcopy(first.candidates)
    prefix = list(calls)
    resumed = runner(entity_model(calls)).run(ir, checkpoint=saved)
    assert resumed.completion == "complete", resumed.diagnostics
    assert resumed.input_id == first.input_id
    assert all(c in resumed.candidates for c in checked_prefix)
    assert not set(prefix) & set(calls[len(prefix):])
    assert resumed.checkpoint["model_calls"] == len(calls)
    assert resumed.checkpoint["attempt_count"] == sum(c[0] == "recall" for c in calls)
    call_count = len(calls)
    replay = worker.run(ir, checkpoint=resumed.checkpoint)
    assert replay.candidates == resumed.candidates
    assert replay.completion == "complete" and len(calls) == call_count


def test_split_parent_is_saved_before_first_child_request(tmp_path):
    ir, calls = source(tmp_path), []
    path = tmp_path / "crash-checkpoint.json"
    journal = CheckpointJournal(path, every=100, seconds=1000)

    def crash_after_partition(value):
        journal.save(value)
        if value["output_splits"]:
            raise OSError("simulated worker crash after durable partition")

    with pytest.raises(OSError, match="simulated worker crash"):
        runner(entity_model(calls)).run(ir, checkpoint_fn=crash_after_partition)
    assert len(calls) == 1
    saved = read_checkpoint(path)
    assert saved["attempt_count"] == saved["model_calls"] == 1
    resumed = runner(entity_model(calls)).run(ir, checkpoint=saved)
    assert resumed.completion == "complete"
    assert Counter(calls)[calls[0]] == 1


def test_total_budget_and_pause_apply_to_children_without_reset(tmp_path):
    ir, calls = source(tmp_path), []
    worker = runner(entity_model(calls), max_tasks=2)
    first = worker.run(ir)
    assert first.completion == "incomplete" and len(calls) == 2
    assert first.checkpoint["output_splits"] and not first.checkpoint["completed"]
    resumed = worker.run(ir, checkpoint=first.checkpoint)
    assert resumed.completion == "incomplete" and len(calls) == 2
    assert resumed.checkpoint["attempt_count"] == 2
    assert "task_budget_or_pause" in resumed.diagnostics


def test_unsplittable_truncation_stays_failed(tmp_path):
    ir = source(tmp_path, ("实体甲",))
    calls = []

    def fail(*args):
        calls.append(1)
        raise StructuredModelError("model_output_truncated")

    run = runner(fail, schema={"urn:TypeA": {}}).run(ir)
    assert run.completion == "incomplete" and not run.candidates
    assert run.diagnostics == ["model_output_truncated"] and len(calls) == 1
    assert not run.checkpoint["output_splits"]
    assert len(run.checkpoint["failures"]) == 1


@pytest.mark.parametrize("code", ["model_timeout", "model_parse_error", "model_request_failed"])
def test_non_truncation_model_failures_keep_existing_stop_policy(tmp_path, code):
    calls = []

    def fail(*args):
        calls.append(1)
        raise StructuredModelError(code)

    run = runner(fail).run(source(tmp_path))
    assert run.completion == "incomplete" and run.diagnostics == [code]
    assert not run.checkpoint["output_splits"] and len(calls) == 1


def test_child_quote_failure_is_not_hidden_by_successful_siblings(tmp_path):
    ir, calls = source(tmp_path), []
    valid = entity_model(calls)
    invalid = entity_model(calls, bad_text="原文中不存在的实体")

    def model(system, user, schema, budget):
        request, task, targets = request_parts(user)
        fail_quote = (
            request["stage"] == "recall" and len(targets) == 1
            and targets[0]["text"] == "实体甲"
        )
        return (invalid if fail_quote else valid)(system, user, schema, budget)

    run = runner(model, schema={"urn:TypeA": {}}).run(ir)
    assert run.completion == "incomplete"
    assert run.diagnostics == ["source_excerpt_mismatch"]
    assert [c.text for c in run.candidates] == ["实体乙"]
    assert len(run.checkpoint["failures"]) == 1


def test_compatible_legacy_truncated_checkpoint_is_split_without_recalling_parent(
    tmp_path, monkeypatch,
):
    ir, calls = source(tmp_path), []
    worker = runner(entity_model(calls), schema={"urn:TypeA": {}})
    identify = worker.input_id
    with monkeypatch.context() as patch:
        patch.setattr(worker, "split_output_task", lambda task: [])
        patch.setattr(worker, "input_id", lambda ir, effective_class="", **kwargs: identify(
            ir, effective_class, scheduler_version=kwargs.get("scheduler_version"),
            output_split_version=None,
        ))
        old = worker.run(ir)
    legacy = deepcopy(old.checkpoint)
    legacy.pop("output_split_version")
    legacy.pop("output_splits")
    assert len(calls) == 1 and legacy["failures"]
    resumed = worker.run(ir, checkpoint=legacy)
    assert resumed.input_id == old.input_id
    assert resumed.completion == "complete", resumed.diagnostics
    assert Counter(calls)[calls[0]] == 1
    assert resumed.checkpoint["attempt_count"] == 3
    count = len(calls)
    assert worker.run(ir, checkpoint=resumed.checkpoint).completion == "complete"
    assert len(calls) == count


def test_checkpoint_cannot_replace_a_saved_child_with_another_partition(tmp_path):
    ir, calls = source(tmp_path), []
    worker = runner(entity_model(calls))
    first = worker.run(ir, pause_after=1)
    saved = deepcopy(first.checkpoint)
    next(iter(saved["output_splits"].values()))["children"][0]["target_class_iris"] = ["urn:Other"]
    count = len(calls)
    with pytest.raises(ValueError, match="does not match task partition"):
        worker.run(ir, checkpoint=saved)
    assert len(calls) == count


def test_type_verification_splits_only_unfinished_batch_and_keeps_competing_types(tmp_path):
    ir = source(tmp_path, ("实体甲；实体乙；实体丙；实体丁",))
    calls, competitors = [], []

    def model(system, user, schema, budget):
        request, task, targets = request_parts(user)
        if request["stage"] == "recall":
            calls.append("recall")
            return {"entities": [{
                "class_iri": f"urn:Type{i % 2}",
                "mention": {"evidence_id": targets[0]["anchor"]["evidence_id"], "text": name},
            } for i, name in enumerate(("实体甲", "实体乙", "实体丙", "实体丁"))]}
        batch = request["candidate"]["proposed_entities"]
        calls.append(tuple(c["candidate_id"] for c in batch))
        competitors.append(request["candidate"]["competing_class_iris"])
        if len(batch) > 1:
            raise StructuredModelError("model_output_truncated")
        return accept_fixture_types(request)

    run = runner(model, schema={"urn:Type0": {}, "urn:Type1": {}}).run(ir)
    assert run.completion == "complete" and len(run.candidates) == 4
    assert calls.count("recall") == 1
    assert sum(isinstance(c, tuple) and len(c) == 1 for c in calls) == 4
    assert all(c == ["urn:Type0", "urn:Type1"] for c in competitors)
    assert run.checkpoint["model_calls"] == len(calls)
    assert all(c.type_verification.supported and c.review_status == "pending" for c in run.candidates)


def test_property_recall_splits_source_regions_with_same_subject_and_predicate(tmp_path):
    ir, calls = source(tmp_path, ("报告标题", "参数甲", "参数乙")), []

    def model(system, user, schema, budget):
        request, task, targets = request_parts(user)
        assert task["task_kind"] == "property" and task["predicate_iri"] == "urn:value"
        calls.append((task["subject"], tuple(t["anchor"]["evidence_id"] for t in targets)))
        if len(targets) > 1:
            raise StructuredModelError("model_output_truncated")
        return {"assertions": []}

    run = runner(model, schema={"urn:Report": {"properties": [{"iri": "urn:value"}]}}).run(
        ir, effective_class="urn:Report",
    )
    assert run.completion == "complete", run.diagnostics
    assert {ids[0] for _, ids in calls if len(ids) == 1} == {u.evidence_id for u in ir.evidence_units}
    assert all(subject == calls[0][0] for subject, _ in calls)


def test_split_relationships_resume_and_retain_downstream_paths(tmp_path):
    ir = source(tmp_path, ("报告描述设备甲和设备乙",))
    schema = {
        "urn:Report": {"relationships": [{"iri": "urn:uses", "range": ["urn:Device"]}]},
        "urn:Device": {"properties": [{"iri": "urn:code"}]},
    }
    calls, downstream = [], set()

    def model(system, user, response_schema, budget):
        request, task, targets = request_parts(user)
        quote = {"evidence_id": targets[0]["anchor"]["evidence_id"], "text": targets[0]["text"]}
        if request["stage"] == "verify_entity_types":
            return accept_fixture_types(request)
        if request["stage"] == "verify_binding":
            candidate = request["candidate"]
            return {
                "supported": True, "subject_candidate_id": candidate["subject"]["candidate_id"],
                "object_candidate_id": candidate["object"]["candidate_id"],
                "assertion_status": "affirmed", "method": "explicit_assertion",
                "assertion_spans": [quote],
            }
        if task["task_kind"] == "entity":
            return {"entities": [{"class_iri": "urn:Device", "mention": {**quote, "text": name}}
                                 for name in ("设备甲", "设备乙")]}
        if task["task_kind"] == "property":
            if task["relationship_path"] == ["urn:uses"]:
                downstream.add(task["subject"]["candidate_id"])
            return {"assertions": []}
        objects = tuple(ref["candidate_id"] for ref in task["object_candidates"])
        calls.append(objects)
        if len(objects) > 1:
            raise StructuredModelError("model_output_truncated")
        return {"assertions": [{
            "object_candidate_id": objects[0], "assertion_status": "affirmed",
            "assertion_spans": [quote],
        }]}

    worker = runner(model, schema=schema)
    first = worker.run(ir, effective_class="urn:Report", pause_after=3)
    assert first.completion == "incomplete"
    assert sum(c.kind == "relationship" for c in first.candidates) == 1
    snapshots = []
    resumed = worker.run(
        ir, effective_class="urn:Report", checkpoint=deepcopy(first.checkpoint),
        snapshot_fn=lambda partial: snapshots.append(deepcopy(partial.candidates)),
    )
    assert resumed.completion == "complete", resumed.diagnostics
    relations = [c for c in resumed.candidates if c.kind == "relationship"]
    assert len(relations) == 2 and all(c.positive_eligible and c.bindings for c in relations)
    assert downstream == {c.object.candidate_id for c in relations}
    assert all(count == 1 for count in Counter(calls).values())
    assert {c.candidate_id for snapshot in snapshots for c in snapshot if c.kind == "relationship"}
