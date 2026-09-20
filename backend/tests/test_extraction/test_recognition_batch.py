"""Member identity, source authority and non-consuming batching regressions."""

from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.services.extraction.evidence_identity import evidence_hash
from app.services.extraction.ontology_guided.context import (
    build_batch_model_context,
    member_protocol_view,
)
from app.services.extraction.ontology_guided.contracts import TraversalScope
from app.services.extraction.ontology_guided.recognition_batch import (
    MemberContext,
    RecognitionBatchContext,
    RecognitionBatchPolicy,
    RecognitionWorkUnit,
    compatible_members,
    compile_batch_stage_schema,
    pack_work_unit,
    parse_batch_discovery,
    parse_batch_verification,
)
from app.services.extraction.ontology_guided.scheduler import FrontierScheduler, RecognitionTask
from tests.test_extraction.test_scheduler_performance import enqueue_plan, make_plan
from tests.test_extraction.test_semantic_scheduler import enqueue, task
from tests.test_extraction.test_tool_engine_context import authorized_context  # noqa: F401


@pytest.fixture
def batch_members(authorized_context):  # noqa: F811
    original = authorized_context["task"]
    tasks, members = [], []
    for index in range(3):
        member_task = RecognitionTask.create(
            subject=original.subject, predicate_kind=original.predicate_kind,
            record_id=original.record_id, phase=original.phase, hop=original.hop,
            predicate_iri=f"urn:property:{index}", dependency_hash=f"dependency:{index}",
        )
        context = authorized_context["base"].model_copy(deep=True)
        context.target = context.target.model_copy(update={
            "task_id": member_task.task_id, "predicate_iri": member_task.predicate_iri,
        })
        context.context_hash = evidence_hash([context.context_hash, index])
        card = authorized_context["trusted"]["card"].model_copy(deep=True)
        card.predicates = [card.predicates[0].model_copy(update={"iri": member_task.predicate_iri})]
        tasks.append(member_task)
        members.append(MemberContext(task_id=member_task.task_id, context=context, card=card))
    return tasks, members


def work_unit(tasks, max_members=4):
    return RecognitionWorkUnit.create(
        run_fingerprint="run", policy=RecognitionBatchPolicy(max_members=max_members),
        members=tasks,
    )


def empty_discovery():
    return dict(entities=[], properties=[], relations=[], external_links=[], observations=[])


def test_work_unit_identity_binds_policy_order_and_each_member_dependency():
    first, second = task(predicate="urn:a"), task(predicate="urn:b")
    second.dependency_hash = "different-predicate-dependency"
    assert compatible_members(first, second)
    original = work_unit([first, second])
    assert original == work_unit([first, second])
    assert original.work_unit_id != work_unit([second, first]).work_unit_id
    assert original.work_unit_id != work_unit([first, second], 2).work_unit_id
    changed = second.model_copy(update={"dependency_hash": "new-proof"})
    assert original.work_unit_id != work_unit([first, changed]).work_unit_id
    first.subject.revision = 2
    assert original.members[0].subject.revision == 1


@pytest.mark.parametrize("mutation", ["subject", "revision", "record", "scope", "retry", "same"])
def test_work_unit_rejects_incompatible_member(mutation):
    first, second = task(predicate="urn:a"), task(predicate="urn:b")
    if mutation == "subject":
        second.subject.entity_id = "other"
    elif mutation == "revision":
        second.subject.revision += 1
    elif mutation == "record":
        second.record_id = "other"
    elif mutation == "scope":
        second.scope = TraversalScope.create()
    elif mutation == "retry":
        second.retry_kind = "evidence"
    else:
        second.predicate_iri = first.predicate_iri
    with pytest.raises(ValidationError):
        work_unit([first, second])


@pytest.mark.parametrize("max_members", [0, 5, True, "4"])
def test_frozen_policy_rejects_invalid_capacity(max_members):
    with pytest.raises(ValidationError):
        RecognitionBatchPolicy(max_members=max_members)


@pytest.mark.parametrize("lazy", [False, True])
def test_preview_and_partial_consumption_preserve_unselected_queue(lazy):
    scheduler = FrontierScheduler(max_tasks=20)
    for predicate in ("urn:a", "urn:b", "urn:c"):
        enqueue_plan(scheduler, make_plan(predicate=predicate, count=12), lazy=lazy)
    before = deepcopy(scheduler.snapshot())
    candidates = scheduler.peek_work_unit_candidates(limit=3)
    assert len(candidates) == 3
    assert scheduler.snapshot() == before
    assert len({member.record_id for member in candidates}) == 1
    selected = [candidates[0].task_id, candidates[2].task_id]
    unit = scheduler.consume_work_unit(
        selected, run_fingerprint="run", policy=RecognitionBatchPolicy(),
    )
    assert [member.task_id for member in unit.members] == selected
    assert scheduler.dispatched == 2
    assert scheduler.pending == 34
    excluded = candidates[1]
    still_pending = scheduler.peek_fresh_slot(excluded.subject, excluded.predicate_iri)
    assert still_pending == excluded
    assert repr((excluded.subject.entity_id, excluded.predicate_iri)) not in scheduler._phase_turns
    restored = FrontierScheduler.from_snapshot(scheduler.snapshot(), max_tasks=20)
    assert restored.peek_work_unit_candidates() == scheduler.peek_work_unit_candidates()


def test_preview_does_not_skip_siblings_prior_record_or_excluded_slot():
    scheduler = FrontierScheduler(max_tasks=10)
    first = task(predicate="urn:a", record="shared")
    prior = task(predicate="urn:b", record="prior")
    later = task(predicate="urn:b", record="shared")
    excluded = task(predicate="urn:c", record="shared")
    for value in (first, prior, later, excluded):
        enqueue(scheduler, value)
    candidates = scheduler.peek_work_unit_candidates(
        excluded_slots={scheduler._slot_key(excluded)}, limit=4,
    )
    assert candidates == [first]
    before = deepcopy(scheduler.snapshot())
    with pytest.raises(ValueError, match="task_not_ready"):
        scheduler.consume_work_unit(
            [first.task_id, excluded.task_id], run_fingerprint="run",
            policy=RecognitionBatchPolicy(), excluded_slots={scheduler._slot_key(excluded)},
        )
    assert scheduler.snapshot() == before
    with pytest.raises(ValueError, match="skips_current_record"):
        scheduler.consume_work_unit(
            [first.task_id, later.task_id], run_fingerprint="run", policy=RecognitionBatchPolicy(),
        )
    assert scheduler.snapshot() == before


def test_batch_limit_counts_members_and_retry_never_acquires_siblings():
    scheduler = FrontierScheduler(max_tasks=2)
    for predicate in ("urn:a", "urn:b", "urn:c"):
        enqueue(scheduler, task(predicate=predicate))
    candidates = scheduler.peek_work_unit_candidates(limit=4)
    assert len(candidates) == 2
    scheduler.consume_work_unit(
        [item.task_id for item in candidates], run_fingerprint="run",
        policy=RecognitionBatchPolicy(),
    )
    assert scheduler.peek_work_unit_candidates() == []
    assert scheduler.pending == 1
    retry_scheduler = FrontierScheduler()
    retry = task(retry="evidence")
    enqueue(retry_scheduler, retry)
    assert retry_scheduler.peek_work_unit_candidates() == [retry]
    unit = retry_scheduler.consume_work_unit(
        [retry.task_id], run_fingerprint="run", policy=RecognitionBatchPolicy(),
        excluded_slots={retry_scheduler._slot_key(retry)},
    )
    assert unit.members == [retry]


def test_single_member_consumption_preserves_source_priority_fairness():
    scheduler = FrontierScheduler()
    for predicate in ("urn:a", "urn:b"):
        for index in range(4):
            value = task(subject="child", predicate=predicate, kind="property", record=str(index))
            enqueue(scheduler, value)
            if predicate == "urn:a":
                scheduler.prioritize_source(value, [{"source": "proved"}])
    baseline = FrontierScheduler.from_snapshot(scheduler.snapshot())
    while scheduler.pending:
        expected = baseline.next_task()
        candidates = scheduler.peek_work_unit_candidates(limit=1)
        unit = scheduler.consume_work_unit(
            [candidates[0].task_id], run_fingerprint="run", policy=RecognitionBatchPolicy(),
        )
        assert unit.members == [expected]
        assert scheduler.snapshot() == baseline.snapshot()


@pytest.mark.parametrize("priority", ["branch", "source", "template"])
def test_grouped_priority_predicates_cannot_starve_the_other_fairness_branch(priority):
    scheduler = FrontierScheduler(template_interleaving=priority == "template")
    for predicate in ("urn:a", "urn:b", "urn:c", "urn:ordinary"):
        for record in range(6):
            preferred = predicate != "urn:ordinary"
            value = task(
                subject="root" if priority == "branch" and not preferred else "child",
                predicate=predicate, kind="property", record=str(record), position=record,
            )
            scheduler.enqueue(
                value, root_branch=value.subject.is_document_root,
                template_priority=priority == "template" and preferred,
            )
            if priority == "source" and preferred:
                scheduler.prioritize_source(value, [{"source": "proved"}])
    before = scheduler.snapshot()
    first = scheduler.peek_work_unit_candidates(limit=4)
    assert len(first) == 2
    assert scheduler.snapshot() == before
    excessive = [task(subject="child", predicate=f"urn:{value}", kind="property", record="0")
                 for value in ("a", "b", "c")]
    with pytest.raises(ValueError, match="exceeds_fair_turn"):
        scheduler.consume_work_unit(
            [value.task_id for value in excessive], run_fingerprint="run",
            policy=RecognitionBatchPolicy(),
        )
    assert scheduler.snapshot() == before
    scheduler.consume_work_unit(
        [value.task_id for value in first], run_fingerprint="run", policy=RecognitionBatchPolicy(),
    )
    second = scheduler.peek_work_unit_candidates(limit=4)
    assert len(second) == 1
    assert second[0].predicate_iri == "urn:ordinary"


def test_packing_measures_complete_requests_without_replacing_oversized_seed(batch_members):
    tasks, members = batch_members
    inputs = {member.task_id: member for member in members}
    measured = []

    def measure(values):
        measured.append([member.task_id for member in values])
        return tasks[1].task_id not in measured[-1]

    selected = pack_work_unit(
        tasks, inputs, policy=RecognitionBatchPolicy(), measure_request=measure,
    )
    assert selected == [
        tasks[0].task_id, tasks[2].task_id,
    ]
    assert measured == [[tasks[0].task_id], [tasks[0].task_id, tasks[1].task_id],
                        [tasks[0].task_id, tasks[2].task_id]]
    assert pack_work_unit(tasks, inputs, policy=RecognitionBatchPolicy(),
                          measure_request=lambda values: False) == []
    assert pack_work_unit(tasks, inputs, policy=RecognitionBatchPolicy(),
                          measure_request=lambda values: True,
                          readiness={tasks[1].task_id: True}) == []


def test_member_missing_and_invalid_answers_do_not_erase_valid_explicit_empty():
    parsed = parse_batch_discovery({"members": [
        {"task_id": "a", "result": empty_discovery()},
        {"task_id": "b", "result": {"entities": []}},
    ]}, member_task_ids=["a", "b", "c"])
    assert list(parsed.answers) == ["a"]
    assert parsed.answers["a"].entities == []
    assert parsed.errors == {"b": "member_answer_invalid", "c": "member_answer_missing"}


@pytest.mark.parametrize(("raw", "code"), [
    ({"members": [{"task_id": "other", "result": {}}]}, "batch_member_unknown"),
    ({"members": [{"task_id": "a"}, {"task_id": "a"}]}, "batch_member_duplicate"),
    ({"members": [], "accepted": True}, "batch_answer_invalid"),
    ('{"members":[', "batch_answer_invalid"),
    ('{"members":[],"members":[]}', "batch_answer_invalid"),
])
def test_outer_protocol_errors_reject_entire_answer(raw, code):
    with pytest.raises(ValueError, match=code):
        parse_batch_discovery(raw, member_task_ids=["a", "b"])


def test_local_ids_are_member_scoped_and_verification_errors_are_isolated():
    entity = {
        "local_id": "e1", "class_iri": "urn:Entity", "representation": "mention",
        "mentions": [{"evidence_id": "evidence", "text": "entity", "context_text": None}],
        "record_components": [], "identifier_claims": [],
    }
    answer = {**empty_discovery(), "entities": [entity]}
    parsed = parse_batch_discovery({"members": [
        {"task_id": member, "result": answer} for member in ("a", "b")
    ]}, member_task_ids=["a", "b"])
    assert not parsed.errors
    assert parsed.answers["a"].entities[0].local_id == parsed.answers["b"].entities[0].local_id
    verification = parse_batch_verification({"members": [
        {"task_id": "a", "result": {"verifications": []}},
        {"task_id": "b", "result": {"verifications": [], "accepted": True}},
    ]}, member_task_ids=["a", "b"])
    assert list(verification.answers) == ["a"]
    assert verification.errors == {"b": "member_answer_invalid"}


def test_batch_schema_reuses_atomic_definitions_and_requires_each_member(batch_members):
    tasks, members = batch_members
    schema = compile_batch_stage_schema("discovery", members, reference_resolution=True)
    assert schema["properties"]["members"]["minItems"] == 3
    assert schema["properties"]["members"]["items"]["properties"]["task_id"]["enum"] == [
        task.task_id for task in tasks
    ]
    definitions = schema["$defs"]
    assert definitions["PropertyProposal"]["properties"]["predicate_iri"]["enum"] == [
        task.predicate_iri for task in tasks
    ]
    assert len([name for name in definitions if name == "Quote"]) == 1
    assert "reference_bindings" in definitions["BatchMemberResult"]["required"]


def test_shared_text_does_not_or_fact_permission_and_precise_spans_stay_separate(batch_members):
    tasks, members = batch_members
    original = members[0].context.fragments[0]
    for member in members:
        member.context.fragments = [original.model_copy(deep=True)]
    members[0].context.fragments[0].fact_eligible = True
    members[1].context.fragments[0].fact_eligible = False
    third = members[2].context.fragments[0]
    assert len(third.text) > 1
    third.anchor = third.anchor.model_copy(update={"span_start": 0, "span_end": 1})
    third.text = third.text[:1]
    view = build_batch_model_context(work_unit(tasks), members, stage="discovery")
    assert len(view["evidence_units"]) == 2
    refs = [member["evidence_refs"][0] for member in view["members"]]
    assert refs[0]["unit_id"] == refs[1]["unit_id"]
    assert refs[0]["fact_eligible"] is True
    assert refs[1]["fact_eligible"] is False
    assert refs[2]["unit_id"] != refs[0]["unit_id"]
    assert all("fact_eligible" not in unit for unit in view["evidence_units"])
    assert view["members"][0]["context_hash"] != view["members"][1]["context_hash"]


def test_rendered_batch_preserves_member_metadata_and_rejects_permission_changes(batch_members):
    tasks, members = batch_members
    rendered = {}
    for member in members:
        rendered[member.task_id] = {
            "task_id": member.task_id, "stage": "verification",
            "context_hash": member.context.context_hash,
            "registered_entities": [{"entity_id": f"private:{member.task_id}"}],
            "source_catalog": [{"record_id": member.context.record_id}],
            "verification_input": {"targets": [{"target_id": f"target:{member.task_id}"}]},
            "evidence_units": [{
                "evidence_id": fragment.anchor.evidence_id, "text": fragment.text,
                "span_start": fragment.anchor.span_start or 0,
                "span_end": (fragment.anchor.span_start or 0) + len(fragment.text),
                "role": fragment.purpose, "fact_eligible": fragment.fact_eligible,
                "table": {"row": index},
            } for index, fragment in enumerate(member.context.fragments)],
        }
    before = deepcopy(rendered)
    view = build_batch_model_context(
        work_unit(tasks), members, stage="verification", results=rendered,
    )
    assert rendered == before
    for member in view["members"]:
        assert member["registered_entities"] == rendered[member["task_id"]]["registered_entities"]
        assert member["verification_input"] == rendered[member["task_id"]]["verification_input"]
        assert member["evidence_refs"][0]["table"] == {"row": 0}
    rendered[members[0].task_id]["evidence_units"][0]["fact_eligible"] = not (
        rendered[members[0].task_id]["evidence_units"][0]["fact_eligible"]
    )
    with pytest.raises(ValueError, match="permission_mismatch"):
        build_batch_model_context(work_unit(tasks), members, stage="verification", results=rendered)


def test_member_projection_preserves_current_revision_and_has_no_write_authority(batch_members):
    tasks, members = batch_members
    member = members[0]
    state = {
        "version": "ontology-tool-batch-v1", "work_unit": work_unit(tasks).model_dump(mode="json"),
        "stage": "verification", "request_attempt": 4,
        "member_states": {member.task_id: {
            "base_target": member.context.target.model_dump(mode="json"),
            "evidence_revision": 3, "assertion_generation": 2,
            "context_hash": member.context.context_hash, "evidence_hash": "e" * 64,
            "scope_id": "scope", "materialized_refs": {"e1": {"id": "member-only"}},
        }},
        "discovery_refs": {member.task_id: "discovery-3"}, "verification_refs": {},
    }
    view = member_protocol_view(state, member.task_id)
    assert view["evidence_revision"] == 3
    assert view["assertion_generation"] == 2
    assert view["discovery_ref"] == "discovery-3"
    assert view["version"] == "ontology-tool-extraction-v1"
    with pytest.raises(TypeError, match="read_only"):
        view["evidence_revision"] = 1
    view["materialized_refs"]["e1"]["id"] = "detached"
    assert state["member_states"][member.task_id]["materialized_refs"]["e1"]["id"] == "member-only"
    batch = RecognitionBatchContext(work_unit_id="unit", members=members)
    calls = []
    batch.bind_model_call_hook(lambda stage, attempt: calls.append((stage, attempt)))
    batch.before_model_call("verification", 4)
    assert calls == [("verification", 4)]
    assert all(member.context.before_model_call is None for member in batch.members)
