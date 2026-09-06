from app.schemas.evidence import CandidateRef, ExtractionTask, TaskBudget
from app.services.extraction.evidence_scope import build_scope, scope_contains
from app.services.extraction.hierarchical_context import build_context, split_windows


class TestTokenizer:
    __test__ = False
    identity = "fixture-codepoint-v1"

    def count(self, text):
        return len(text)


def test_heading_subject_scope_does_not_leak_into_sibling(subject_document):
    ir, (a, b) = subject_document
    scope = build_scope(ir, a, [b])
    assert scope_contains(scope, ir.anchor(ir.evidence_units[1].evidence_id), ir)
    assert not scope_contains(scope, ir.anchor(ir.evidence_units[3].evidence_id), ir)


def test_cache_identity_includes_subject_revision_classification_and_budget(subject_document):
    ir, (a, b) = subject_document
    scope = build_scope(ir, a, [b])
    task = ExtractionTask(
        task_id="t",
        task_kind="property",
        predicate_iri="urn:strength",
        subject=CandidateRef(candidate_id=a.candidate_id, revision=1),
        scope=scope,
        target_evidence_ids=[ir.evidence_units[1].evidence_id],
        budget=TaskBudget(max_input_tokens=10000),
    )
    arguments = dict(ir=ir, candidates={a.candidate_id: a}, tokenizer=TestTokenizer(), model="m1")
    first = build_context(task, effective_class="urn:ReportA", **arguments)
    second = build_context(task, effective_class="urn:ReportB", **arguments)
    assert first.lookup_key != second.lookup_key
    third = build_context(
        task.model_copy(update={"budget": TaskBudget(max_input_tokens=9000)}),
        effective_class="urn:ReportA",
        **arguments,
    )
    assert first.lookup_key != third.lookup_key
    assert first.completion == "complete"
    assert first.allowed_fact_regions[0].evidence_id == ir.evidence_units[1].evidence_id


def test_mandatory_subject_and_competitors_cannot_be_dropped_for_budget(subject_document):
    ir, subjects = subject_document
    task = ExtractionTask(
        task_id="small",
        task_kind="property",
        predicate_iri="urn:strength",
        subject=CandidateRef(candidate_id=subjects[0].candidate_id, revision=1),
        competing_subjects=[CandidateRef(candidate_id=subjects[1].candidate_id, revision=1)],
        scope=build_scope(ir, subjects[0], subjects[1:]),
        target_evidence_ids=[ir.evidence_units[1].evidence_id],
        budget=TaskBudget(max_input_tokens=1),
    )
    result = build_context(
        task, ir, {s.candidate_id: s for s in subjects}, TestTokenizer(), model="m1"
    )
    assert result.completion == "incomplete"
    assert result.reason == "budget_exceeded"
    assert result.serialized_input is None


def test_overlapping_windows_cover_every_original_codepoint():
    text = "正文𠀀" * 40
    windows = split_windows(text, TestTokenizer(), max_tokens=13, overlap=3)
    covered = {index for start, end in windows for index in range(start, end)}
    assert covered == set(range(len(text)))
    assert all(len(text[start:end]) <= 13 for start, end in windows)


def test_model_projection_keeps_semantics_but_full_identity_stays_server_side(subject_document):
    import json

    from app.services.extraction.hierarchical_context import model_request

    ir, (a, b) = subject_document
    task = ExtractionTask(
        task_id="projection", task_kind="property", predicate_iri="urn:strength",
        subject=CandidateRef(candidate_id=a.candidate_id, revision=1),
        competing_subjects=[CandidateRef(candidate_id=b.candidate_id, revision=1)],
        scope=build_scope(ir, a, [b]),
        target_evidence_ids=[ir.evidence_units[1].evidence_id],
        budget=TaskBudget(max_input_tokens=20000),
    )
    envelope = build_context(task, ir, {a.candidate_id: a, b.candidate_id: b},
                             TestTokenizer(), model="m1")
    original = envelope.serialized_input
    assertion = {
        "subject": task.subject.model_dump(), "object": None,
        "predicate_iri": task.predicate_iri, "assertion_status": "conditional",
        "condition_anchors": [ir.anchor(ir.evidence_units[1].evidence_id).model_dump()],
        "literal": {"raw_value": "250 mg"}, "task_id": "internal-only",
    }
    wire = json.loads(model_request(json.loads(original), "verify_binding", assertion))
    projected = json.loads(wire["context"])
    assert "budget" not in projected["task"] and "task_id" not in projected["task"]
    assert projected["task"]["competing_subjects"] == [task.competing_subjects[0].model_dump()]
    assert set(projected["subjects"]) == {a.candidate_id, b.candidate_id}
    assert wire["candidate"]["assertion_status"] == "conditional"
    assert (
        wire["candidate"]["condition_anchors"][0]["evidence_id"] == ir.evidence_units[1].evidence_id
    )
    for before, after in zip(envelope.fragments, projected["fragments"], strict=True):
        for key in ("text", "purpose", "fact_eligible"):
            assert before[key] == after[key]
        for key, value in after["anchor"].items():
            assert before["anchor"][key] == value
        assert "document_hash" not in after["anchor"]
    assert envelope.serialized_input == original
    assert ir.document_hash in original and ir.document_hash not in wire["context"]
    assert len(wire["context"]) < len(original) * 0.75


def test_projection_token_accounting_and_static_prefix(subject_document):
    import json

    from app.services.extraction.evidence_identity import canonical_json
    from app.services.extraction.hierarchical_context import model_request

    ir, _ = subject_document
    task = ExtractionTask(
        task_id="count", task_kind="entity", target_class_iris=["urn:Drug"],
        target_evidence_ids=[ir.evidence_units[0].evidence_id],
        budget=TaskBudget(max_input_tokens=20000),
    )
    response_schema = {"type": "object"}
    envelope = build_context(task, ir, {}, TestTokenizer(), model="m", system_prompt="rules",
                             response_schema=response_schema)
    payload = json.loads(envelope.serialized_input)
    request = model_request(payload, "recall")
    assert envelope.input_tokens == len(request + "rules" + canonical_json(response_schema)) + 128
    before = json.loads(request)["context"]
    payload["fragments"][0]["text"] = "changed evidence"
    after = json.loads(model_request(payload, "recall"))["context"]
    assert before.split(',"fragments":')[0] == after.split(',"fragments":')[0]
    assert before.index('"task":') < before.index('"fragments":')
