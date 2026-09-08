"""A reviewed-out parent edge blocks its branch without cancelling other chapters.

Only model responses are fixtures; execution and atomic citation replay are real.
"""

import json
from copy import deepcopy

from app.evaluation.quality_guided_variant import build_quality_guided_variant
from app.schemas.evidence import TaskBudget
from app.services.extraction.extraction_tasks import GenericExtractionRunner
from tests.test_extraction import test_evaluation_cmc_product_api_path as path_fixture


def _request_subject(trace):
    request = json.loads(trace["user"])
    if trace["stage"] == "route_records":
        return request["subject"]["candidate_id"]
    subject = json.loads(request["context"])["task"].get("subject")
    if subject is None:
        return None
    identity = subject["candidate_id"]
    return trace["references"]["candidate"].get(identity, identity)


def test_review_rejected_parent_on_resume_blocks_child_calls_but_keeps_other_chapters(tmp_path):
    source = path_fixture.source.__wrapped__(tmp_path)
    calls, trace = [], []
    base = GenericExtractionRunner(
        deepcopy(path_fixture.SCHEMA),
        path_fixture.TestTokenizer(),
        path_fixture.model_response(calls),
        model_identity="quality-review-gate-fixture",
        compact_identifiers=True,
        budget=TaskBudget(
            max_input_tokens=200000,
            max_output_tokens=4096,
            max_tasks=200,
            max_hops=3,
            max_regions_per_task=32,
        ),
    )
    base.trace_fn = trace.append
    runner = build_quality_guided_variant(base, source, focus_path=path_fixture.FOCUS_PATH)
    assert runner.execute_task.__func__ is GenericExtractionRunner.execute_task

    checkpoint = None
    upstream = None
    # Pause at each completed record so the accepted edge's child is still queued.
    for _ in range(len(runner.record_index.records)):
        result = runner.run(
            source,
            effective_class=path_fixture.CMC_REPORT,
            checkpoint=checkpoint,
            pause_after=1,
        )
        checkpoint = deepcopy(result.checkpoint)
        upstream = next(
            (
                candidate
                for candidate in checkpoint["candidates"]
                if candidate["kind"] == "relationship"
                and candidate["predicate_iri"] == path_fixture.DESCRIBES
                and candidate["validation_status"] == "passed"
            ),
            None,
        )
        if upstream is not None:
            break
    assert upstream is not None, result.diagnostics
    product_id = upstream["object"]["candidate_id"]
    root_id = upstream["subject"]["candidate_id"]
    assert any(
        work["kind"] == "expand" and work["subject_id"] == product_id
        for work in checkpoint["queue"]
    )
    remaining_root_records = {
        work["record_id"]
        for work in checkpoint["queue"]
        if work["kind"] == "relationship"
        and work["subject_id"] == root_id
        and work["predicate"]["iri"] == path_fixture.DESCRIBES
    }
    assert remaining_root_records
    assert not any(_request_subject(event) == product_id for event in trace)

    # A saved review rejection does not rewrite validation or any entity/scope.
    upstream["review_status"] = "rejected"
    assert upstream["validation_status"] == "passed"
    start = len(trace)
    resumed = runner.run(
        source, effective_class=path_fixture.CMC_REPORT, checkpoint=checkpoint
    )
    resumed_trace = trace[start:]

    assert not any(_request_subject(event) == product_id for event in resumed_trace)
    assert not any(
        candidate.positive_eligible
        and (
            candidate.subject is not None and candidate.subject.candidate_id == product_id
            or any(
                ref.candidate_id == upstream["candidate_id"]
                for ref in candidate.dependency_refs
            )
        )
        for candidate in resumed.candidates
    )
    assert not any(
        candidate.kind == "relationship" and candidate.predicate_iri == path_fixture.HAS_API
        for candidate in resumed.candidates
    )
    reviewed = next(
        candidate
        for candidate in resumed.candidates
        if candidate.candidate_id == upstream["candidate_id"]
    )
    assert reviewed.review_status == "rejected"

    visited_root_records = {
        runner._record_for_task[event["task_id"]].record_id
        for event in resumed_trace
        if event["stage"] != "route_records" and _request_subject(event) == root_id
    }
    assert remaining_root_records <= visited_root_records
    assert not resumed.checkpoint["queue"]
