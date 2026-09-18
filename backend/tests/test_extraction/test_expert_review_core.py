"""Expert corrections are source-local, versioned and never evidence authority."""

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.services.extraction.ontology_guided import model_adapter
from app.services.extraction.ontology_guided.context import assemble_context
from app.services.extraction.ontology_guided.contracts import (
    GraphProperty,
    LocalMenu,
    SlotSpec,
    SubjectRef,
)
from app.services.extraction.ontology_guided.dependencies import DependencyIndex
from app.services.extraction.ontology_guided.expert_review import (
    ReviewReplay,
    apply_property_review,
    assertion_identity,
    local_repair_tasks,
)
from app.services.extraction.ontology_guided.ontology_plan import compile_local_menu
from app.services.extraction.ontology_guided.repair_adapter import EvidenceRepairAdapter
from app.services.extraction.ontology_guided.scheduler import RecognitionTask
from tests.test_extraction.test_evidence_repair import _ontology, repaired_response
from tests.test_extraction.test_joint_evidence_validation import _fixture


def _review_fixture():
    subject = SubjectRef(entity_id="subject", revision=1, class_iri="urn:Class")
    task = RecognitionTask.create(
        subject=subject, predicate_iri="urn:value", predicate_kind="property",
        record_id="record", phase=1, hop=1, dependency_hash="original",
    )
    candidate = GraphProperty(
        candidate_id="claim", revision=1, subject_ref={"id": "subject", "revision": 1},
        predicate_iri="urn:value", predicate_label="数值", raw_value="10",
        decision_status="supported", structural_valid=True, model_supported=True,
        policy_eligible=True, proof_ref={"id": "proof", "revision": 1},
    )
    review = {
        "review_id": "review", "review_revision": 1, "candidate_id": "claim",
        "candidate_revision": 1, "decision": "rejected", "reason": "这是另一属性的值",
        "reason_code": "incorrect_value", "original_task": task.model_dump(mode="json"),
        "predicate_iri": "urn:value", "record_ids": ["record"], "after_outcomes": 1,
    }
    menu = LocalMenu(
        menu_id="menu", ontology_snapshot_id="ontology", subject=subject,
        properties=[SlotSpec(iri=iri, label=iri) for iri in ("urn:value", "urn:other")],
    )
    index = SimpleNamespace(records=[
        SimpleNamespace(record_id=iri) for iri in ("record", "elsewhere")
    ])
    return candidate, review, menu, index


def test_review_changes_only_new_revision_and_does_not_fabricate_proof():
    candidate, review, _menu, _index = _review_fixture()
    dependencies = DependencyIndex()
    dependencies.add_proof("claim@1", ["proof@1"])
    properties = {"claim": candidate}
    rejected = apply_property_review(review, properties, dependencies)
    assert candidate.revision == 1 and candidate.independent_review == "unreviewed"
    assert rejected.revision == 2 and rejected.independent_review == "rejected"
    assert not dependencies.is_valid("claim@1") and not dependencies.is_valid("claim@2")
    assert rejected.proof_ref == candidate.proof_ref
    assert assertion_identity(rejected) == assertion_identity(candidate)
    failed = candidate.model_copy(update={"structural_valid": False, "model_supported": False})
    accepted = apply_property_review(
        {**review, "decision": "accepted"}, {"claim": failed}, DependencyIndex(),
    )
    assert not accepted.structural_valid and not accepted.model_supported


def test_review_requires_exact_candidate_and_nonempty_rejection_reason():
    candidate, review, _menu, _index = _review_fixture()
    for changed in ({"candidate_revision": 2}, {"reason": "   "}):
        with pytest.raises(ValueError):
            apply_property_review({**review, **changed}, {"claim": candidate}, DependencyIndex())


@pytest.mark.parametrize("reason,expected", [
    ("incorrect_value", ["urn:value"]), ("incorrect_property", ["urn:other"]),
    ("incorrect_subject", []),
])
def test_repair_returns_original_source_only_and_reason_controls_legal_local_predicates(
    reason, expected,
):
    _candidate, review, menu, index = _review_fixture()
    review["reason_code"] = reason
    operation = {"operation_id": "repair"}
    tasks, status = local_repair_tasks(review, operation, menu, index)
    assert [task.predicate_iri for task in tasks] == expected
    assert all(task.record_id == "record" and task.subject == menu.subject for task in tasks)
    assert local_repair_tasks(review, operation, menu, index) == (tasks, status)
    if tasks:
        original = RecognitionTask.model_validate(review["original_task"])
        assert tasks[0].task_id != original.task_id
        assert tasks[0].claim_lineage_id != original.claim_lineage_id
        changed, _ = local_repair_tasks(review, {"operation_id": "another"}, menu, index)
        assert changed[0].task_id != tasks[0].task_id
    else:
        assert status == "expert_subject_localization_required"


def test_review_events_freeze_after_validated_prefix_and_replay_idempotently():
    _candidate, review, _menu, _index = _review_fixture()
    journal = ReviewReplay()
    journal.admit([review], [], after_outcomes=3)
    frozen = journal.snapshot()
    assert journal.at(1) == [] and len(journal.at(3)) == 1
    restored = ReviewReplay(frozen)
    restored.admit([review], [], after_outcomes=3)
    assert restored.snapshot() == frozen
    restored.applied.add("review:review")
    assert restored.at(3) == []
    with pytest.raises(ValueError):
        ReviewReplay().admit([review], [], after_outcomes=0)


def test_expert_feedback_cannot_reuse_prior_protocol_or_change_evidence_permissions(
    tmp_path, monkeypatch,
):
    _analysis, index, task, target, binding = _fixture(tmp_path)
    menu = compile_local_menu(_ontology(), task.subject)
    predicate = next(p for p in menu.relationships if p.iri == task.predicate_iri)
    requests, stages = [], []

    def respond(_client, *, user, **_kwargs):
        request = json.loads(user)
        requests.append(request)
        return repaired_response(request)

    monkeypatch.setattr(model_adapter, "chat_with_schema", respond)
    adapter = EvidenceRepairAdapter(object(), model_identity="review-test")
    context = assemble_context(target, task.record_id, index, required_context_refs=[binding],
                               repair_enabled=True)
    context.bind_protocol_hook(lambda state: stages.append(deepcopy(state)))
    adapter.inspect(task, context, predicate, menu)
    original = deepcopy(context.protocol_state)
    prior_fragments = requests[0]["fragments"]
    context.expert_feedback = {"operation_id": "repair", "reason": "先前归属判断错误"}
    with pytest.raises(ValueError, match="expert_review_mismatch"):
        adapter.verify_existing(task, context, predicate, menu, original)
    context.protocol_state = {}
    repaired = adapter.inspect(task, context, predicate, menu)
    assert repaired.model_calls > 0
    assert requests[-1]["expert_feedback"] == context.expert_feedback
    assert requests[-1]["fragments"] == prior_fragments
    assert original["verification_request_hash"] != context.protocol_state[
        "verification_request_hash"
    ]
    count = len(requests)
    adapter.inspect(task, context, predicate, menu)
    assert len(requests) == count


def executor_review_fixture(tmp_path):
    from tests.test_extraction.test_layered_recognition import (
        Adapter,
        arguments,
        ontology,
        run_executor,
    )

    args = arguments(tmp_path, ["第一属性：1", "独立背景"])
    snapshot, batches = ontology(single=True, branches=False), []
    result = run_executor(snapshot, Adapter()).run(**args, batch_hook=batches.append)
    original = batches[-1].outcome.properties[0]
    review = dict(
        review_id="expert-review-1", candidate_id=original.candidate_id,
        candidate_revision=original.revision, review_revision=1,
        decision="rejected", reason="数值转录错误，应根据原文核验", reason_code="incorrect_value",
        after_outcomes=len(batches[-1].task_outcomes),
        subject_ref=original.subject_ref.model_dump(mode="json"),
        predicate_iri=original.predicate_iri,
        original_task=batches[-1].task.model_dump(mode="json"),
        record_ids=[batches[-1].task.record_id],
    )
    operation = dict(operation_id="expert-operation-1", review_id=review["review_id"],
                     target=review, status="queued", after_outcomes=review["after_outcomes"],
                     max_tasks=16, max_model_calls=32)
    return args, snapshot, batches[-1], original, review, operation, result


def test_executor_local_repair_retains_original_checkpoint_and_cold_replays(tmp_path):
    from tests.test_extraction.test_layered_recognition import Adapter, run_executor
    from tests.test_extraction.test_sparse_candidate_planning import resume_arguments

    args, snapshot, base, original, review, operation, _ = executor_review_fixture(tmp_path)
    frozen = base.model_dump(mode="json")

    class Corrected(Adapter):
        def inspect(self, task, context, predicate, menu):
            assert context.expert_feedback["reason"] == review["reason"]
            assert task.record_id in review["record_ids"]
            result = super().inspect(task, context, predicate, menu)
            result.properties[0].raw_value = "2"
            result.properties[0].candidate_id = "corrected-value"
            result.model_calls = 1
            return result

    adapter, batches = Corrected(), []
    result = run_executor(snapshot, adapter, max_tasks=1).run(
        **args, **resume_arguments(base), property_reviews=[review],
        property_repairs=[operation], repair_only=True, batch_hook=batches.append,
    )
    assert len(adapter.tasks) == 1
    assert base.model_dump(mode="json") == frozen
    assert len(batches) == 2  # review boundary plus a real source task
    assert len(batches[0].task_outcomes) == len(base.task_outcomes)
    assert batches[0].graph.progress.records_examined == base.graph.progress.records_examined
    rejected = next(p for p in result.graph.properties if p.candidate_id == original.candidate_id)
    assert rejected.independent_review == "rejected"
    assert rejected.revision == original.revision + 1
    assert result.evidence_repair_summary["expert_review"]["operations"][operation["operation_id"]][
        "status"
    ] == "completed"
    replay_adapter, restored_batches = Corrected(), []
    restored = run_executor(snapshot, replay_adapter, max_tasks=1).run(
        **args, **resume_arguments(batches[-1]), property_reviews=[review],
        property_repairs=[{**operation, "status": "completed"}], repair_only=True,
        batch_hook=restored_batches.append,
    )
    assert not replay_adapter.tasks and not restored_batches
    assert restored.graph == result.graph
    assert restored.evidence_repair_summary == result.evidence_repair_summary


def test_same_rejected_assertion_with_new_candidate_id_stays_unresolved(tmp_path):
    from tests.test_extraction.test_layered_recognition import Adapter, run_executor
    from tests.test_extraction.test_sparse_candidate_planning import resume_arguments

    args, snapshot, base, _, review, operation, _ = executor_review_fixture(tmp_path)

    class Repeated(Adapter):
        def inspect(self, task, context, predicate, menu):
            outcome = super().inspect(task, context, predicate, menu)
            outcome.properties[0].candidate_id = "new-id-same-rejected-value"
            return outcome

    result = run_executor(snapshot, Repeated()).run(
        **args, **resume_arguments(base), property_reviews=[review],
        property_repairs=[operation], repair_only=True,
    )
    assert all(p.independent_review == "rejected" for p in result.graph.properties)
    state = result.evidence_repair_summary["expert_review"]["operations"][operation["operation_id"]]
    assert state["status"] == "unresolved"
    assert state["result"]["reason_code"] == "expert_repair_no_replacement"
    assert result.graph.progress.completion == "incomplete"


def test_subject_rejection_publishes_no_model_boundary_and_survives_cold_resume(tmp_path):
    from tests.test_extraction.test_layered_recognition import Adapter, run_executor
    from tests.test_extraction.test_sparse_candidate_planning import resume_arguments

    args, snapshot, base, _, review, operation, _ = executor_review_fixture(tmp_path)
    review["reason_code"] = "incorrect_subject"
    adapter, batches = Adapter(), []
    result = run_executor(snapshot, adapter).run(
        **args, **resume_arguments(base), property_reviews=[review],
        property_repairs=[operation], repair_only=True, batch_hook=batches.append,
    )
    assert not adapter.tasks
    assert len(batches) == 1
    assert len(batches[0].task_outcomes) == len(base.task_outcomes)
    restored = run_executor(snapshot, Adapter()).run(
        **args, **resume_arguments(batches[0]), property_reviews=[review],
        property_repairs=[operation], repair_only=True,
    )
    assert restored.graph == result.graph
    assert restored.evidence_repair_summary == result.evidence_repair_summary


def test_multiple_reviews_of_same_base_revision_replay_in_order(tmp_path):
    from tests.test_extraction.test_layered_recognition import Adapter, run_executor
    from tests.test_extraction.test_sparse_candidate_planning import resume_arguments

    args, snapshot, base, original, rejected, _, _ = executor_review_fixture(tmp_path)
    accepted = {**rejected, "review_id": "review-confirmed", "review_revision": 2,
                "decision": "accepted", "reason": "复核确认原值"}
    batches = []
    result = run_executor(snapshot, Adapter()).run(
        **args, **resume_arguments(base), property_reviews=[rejected, accepted],
        repair_only=True, batch_hook=batches.append,
    )
    assert result.graph.properties[0].independent_review == "accepted"
    assert result.graph.properties[0].revision == original.revision + 2
    restored = run_executor(snapshot, Adapter()).run(
        **args, **resume_arguments(batches[-1]), property_reviews=[rejected, accepted],
        repair_only=True,
    )
    assert restored.graph == result.graph


def test_local_repair_pause_continuation_retains_feedback_and_budget(tmp_path):
    from app.services.extraction.ontology_guided.executor import TaskOutcome
    from tests.test_extraction.test_layered_recognition import Adapter, run_executor
    from tests.test_extraction.test_sparse_candidate_planning import resume_arguments

    args, snapshot, base, _, review, operation, _ = executor_review_fixture(tmp_path)

    class Paused(Adapter):
        def inspect(self, task, context, predicate, menu):
            self.tasks.append(task)
            return TaskOutcome(semantic_outcome="not_checked", complete=False,
                               reason_code="execution_pause_requested", reason="请求暂停",
                               model_calls=1)

    batches = []
    paused = run_executor(snapshot, Paused()).run(
        **args, **resume_arguments(base), property_reviews=[review],
        property_repairs=[operation], repair_only=True, batch_hook=batches.append,
        # Stop at the first completed task publication, using the real callback boundary.
    )
    # A model-requested pause must yield with a resumable local task, not spin
    # through all sixteen task attempts in a single invocation.
    assert paused.evidence_repair_summary["expert_review"]["operations"][
        operation["operation_id"]
    ]["result"]["tasks_attempted"] == 1

    class Corrected(Adapter):
        def inspect(self, task, context, predicate, menu):
            assert context.expert_feedback["review_id"] == review["review_id"]
            result = super().inspect(task, context, predicate, menu)
            result.properties[0].raw_value = "2"
            result.model_calls = 1
            return result

    adapter = Corrected()
    result = run_executor(snapshot, adapter).run(
        **args, **resume_arguments(batches[-1]), property_reviews=[review],
        property_repairs=[{**operation, "status": "running"}], repair_only=True,
    )
    state = result.evidence_repair_summary["expert_review"]["operations"][operation["operation_id"]]
    assert state["status"] == "completed"
    assert state["result"]["tasks_attempted"] == 2
    assert state["result"]["model_calls"] == 2
    assert len(adapter.tasks) == 1


def test_local_repair_reserves_no_calls_beyond_operation_cap(tmp_path):
    from tests.test_extraction.test_layered_recognition import Adapter, run_executor
    from tests.test_extraction.test_sparse_candidate_planning import resume_arguments

    args, snapshot, base, _, review, operation, _ = executor_review_fixture(tmp_path)
    operation["max_model_calls"] = 2
    paid = []

    class Expensive(Adapter):
        def inspect(self, task, context, predicate, menu):
            self.tasks.append(task)
            for ordinal in range(1, 4):
                context.before_model_call("verification", ordinal)
                paid.append(ordinal)
            raise AssertionError("third paid call must never happen")

    batches = []
    result = run_executor(snapshot, Expensive()).run(
        **args, **resume_arguments(base), property_reviews=[review],
        property_repairs=[operation], repair_only=True, batch_hook=batches.append,
    )
    assert paid == [1, 2]
    result = run_executor(snapshot, Expensive()).run(
        **args, **resume_arguments(batches[-1]), property_reviews=[review],
        property_repairs=[{**operation, "status": "running"}], repair_only=True,
    )
    assert paid == [1, 2]
    state = result.evidence_repair_summary["expert_review"]["operations"][operation["operation_id"]]
    assert state["status"] == "unresolved"
    assert state["result"]["model_calls"] == 2
