"""Independent review checks for terminal identities and bounded repair recovery."""

from app.services.extraction.ontology_guided.projection import project_graph
from tests.test_extraction.test_expert_review_core import executor_review_fixture
from tests.test_extraction.test_layered_recognition import Adapter, run_executor
from tests.test_extraction.test_sparse_candidate_planning import resume_arguments


def test_repair_terminal_graph_hash_covers_the_actual_terminal_progress(tmp_path):
    args, snapshot, base, _original, review, operation, _result = executor_review_fixture(tmp_path)

    class Corrected(Adapter):
        def inspect(self, task, context, predicate, menu):
            outcome = super().inspect(task, context, predicate, menu)
            outcome.properties[0].raw_value = "2"
            outcome.properties[0].candidate_id = "corrected"
            return outcome

    result = run_executor(snapshot, Corrected()).run(
        **args, **resume_arguments(base), property_reviews=[review],
        property_repairs=[operation], repair_only=True,
    )
    graph = result.graph
    regenerated = project_graph(
        recognition_run_id=graph.recognition_run_id, run_revision=graph.run_revision,
        event_head=graph.event_head, metadata_snapshot_id=graph.metadata_snapshot_id,
        root_ref=graph.root_ref, nodes=graph.nodes, edges=graph.edges, properties=graph.properties,
        coverage=graph.coverage, progress=graph.progress,
        projection=graph.projection, artifact_status=graph.artifact_status,
    )
    assert graph.generated_from_hash == regenerated.generated_from_hash


def test_local_repair_does_not_wait_for_saved_paused_ordinary_ranking(tmp_path, monkeypatch):
    from app.services.extraction.ontology_guided.expert_review import ReviewReplay
    from app.services.extraction.ontology_guided.semantic_reranker import RankingEpoch

    args, snapshot, base, _original, review, operation, _result = executor_review_fixture(tmp_path)
    resumed = resume_arguments(base)
    plan = next(iter(base.recall_ledger.values()))
    paused = RankingEpoch(
        epoch_id="saved-paused-ranking", epoch_seq=1, plan_id=plan["plan_id"],
        subject_ref=plan["subject"], query_dependency_hash="saved-query",
        ranking_dependency_hash="saved-ranking", pool_hash="saved-pool", record_ids=[],
        ordered_record_ids=[], queries=[], observations=[], model_identity={},
        policy_hash="saved-policy", permission_scope_hash="saved-permission",
        actual_ranking_mode="semantic", status="paused", reason="ranking_budget_exhausted",
    ).model_dump(mode="json")
    resumed["ranking_state"]["service"]["pending_epochs"] = [paused]
    original_at, visits = ReviewReplay.at, 0

    def bounded_at(self, count):
        nonlocal visits
        visits += 1
        assert visits <= 20, "local repair busy-looped behind an ordinary ranking pause"
        return original_at(self, count)

    monkeypatch.setattr(ReviewReplay, "at", bounded_at)

    class Corrected(Adapter):
        def inspect(self, task, context, predicate, menu):
            outcome = super().inspect(task, context, predicate, menu)
            outcome.properties[0].raw_value = "2"
            outcome.properties[0].candidate_id = "corrected"
            return outcome

    adapter = Corrected()
    result = run_executor(snapshot, adapter).run(
        **args, **resumed, property_reviews=[review], property_repairs=[operation],
        repair_only=True,
    )
    assert len(adapter.tasks) == 1
    assert result.ranking_state["service"]["pending_epochs"] == [paused]
    assert result.graph.progress.stop_reason == "expert_repair_completed"


def test_rejecting_closed_slot_reopens_ordinary_search(tmp_path):
    args, snapshot, base, _original, review, _operation, _result = executor_review_fixture(tmp_path)
    adapter = Adapter()
    result = run_executor(snapshot, adapter).run(
        **args, **resume_arguments(base), property_reviews=[review],
    )
    # The fixture has one untouched background record outside the prior receipt.
    # With its sole supported value rejected, that source must remain searchable.
    assert len(adapter.tasks) == 1
    assert result.graph.progress.records_examined == 2


def test_incorrect_property_admits_original_source_to_another_slot_and_cold_replays(tmp_path):
    from app.services.extraction.evidence_identity import evidence_hash
    from app.services.extraction.ontology_guided.contracts import SlotSpec
    from tests.test_extraction.test_layered_recognition import (
        CHILD_VALUE,
        ROOT,
        ROOT_VALUE,
        arguments,
        ontology,
    )

    args = arguments(tmp_path, ["第一属性：1", "第二属性：1", "独立背景"])
    snapshot = ontology(single=True, branches=False)
    snapshot.classes[ROOT].declared_properties.append(SlotSpec(
        iri=CHILD_VALUE, label="第二属性", max_count=1,
    ))
    snapshot.ontology_hash = evidence_hash(snapshot.classes)
    base_batches = []
    initial = run_executor(snapshot, Adapter()).run(**args, batch_hook=base_batches.append)
    base = base_batches[-1]
    frozen_base = base.model_dump(mode="json")
    original_batch = next(batch for batch in base_batches
                          if batch.task.predicate_iri == ROOT_VALUE)
    original = original_batch.outcome.properties[0]
    retained = next(prop for prop in initial.graph.properties if prop.predicate_iri == CHILD_VALUE)
    previous_target_plan = next(plan for plan in base.recall_ledger.values()
                                if plan["predicate_iri"] == CHILD_VALUE)
    assert original_batch.task.record_id not in previous_target_plan["ledger"]
    review = dict(
        review_id="wrong-property-review", candidate_id=original.candidate_id,
        candidate_revision=original.revision, review_revision=1,
        decision="rejected", reason="原文旧字段名称误导，应按语义核验为第二属性",
        reason_code="incorrect_property", after_outcomes=len(base.task_outcomes),
        subject_ref=original.subject_ref.model_dump(mode="json"),
        predicate_iri=original.predicate_iri,
        original_task=original_batch.task.model_dump(mode="json"),
        record_ids=[original_batch.task.record_id],
    )
    operation = dict(
        operation_id="wrong-property-repair", review_id=review["review_id"], target=review,
        status="queued", after_outcomes=review["after_outcomes"], max_tasks=16, max_model_calls=32,
    )

    class Reclassified(Adapter):
        def inspect(self, task, context, predicate, menu):
            assert context.expert_feedback["reason_code"] == "incorrect_property"
            assert task.predicate_iri == CHILD_VALUE
            assert task.record_id == original_batch.task.record_id
            # Simulate source verification resolving the legacy field label;
            # proof and anchors still come from the newly admitted original source.
            outcome = super().inspect(
                task, context, predicate.model_copy(update={"label": "第一属性"}), menu,
            )
            outcome.properties[0].predicate_label = predicate.label
            outcome.model_calls = 1
            return outcome

    adapter, repair_batches = Reclassified(), []
    result = run_executor(snapshot, adapter).run(
        **args, **resume_arguments(base), property_reviews=[review],
        property_repairs=[operation], repair_only=True, batch_hook=repair_batches.append,
    )
    assert len(adapter.tasks) == 1
    assert base.model_dump(mode="json") == frozen_base
    assert next(prop for prop in result.graph.properties
                if prop.candidate_id == retained.candidate_id) == retained
    assert next(prop for prop in result.graph.properties
                if prop.candidate_id == original.candidate_id).independent_review == "rejected"
    target_plan = next(plan for plan in result.retrieval_plans
                       if plan["predicate_iri"] == CHILD_VALUE)
    assert target_plan["ledger"][original_batch.task.record_id]["coverage_state"] == "examined"
    state = result.evidence_repair_summary["expert_review"]["operations"][operation["operation_id"]]
    assert state["status"] == "completed"
    assert state["result"]["model_calls"] == 1
    assert len(state["result"]["replacement_candidate_refs"]) == 1
    replay_adapter, replay_batches = Reclassified(), []
    restored = run_executor(snapshot, replay_adapter).run(
        **args, **resume_arguments(repair_batches[-1]), property_reviews=[review],
        property_repairs=[{**operation, "status": "completed"}], repair_only=True,
        batch_hook=replay_batches.append,
    )
    assert not replay_adapter.tasks and not replay_batches
    assert restored.graph == result.graph
    assert restored.evidence_repair_summary == result.evidence_repair_summary


def test_rejection_of_earlier_layer_can_reopen_its_slot_on_ordinary_resume(tmp_path):
    from tests.test_extraction.test_layered_recognition import (
        ROOT_VALUE,
        Adapter,
        arguments,
        ontology,
        run_executor,
    )
    from tests.test_extraction.test_sparse_candidate_planning import resume_arguments

    args = arguments(tmp_path, ["第一属性：1", "具有子项：子项", "第二属性：1",
                                "具有末级：末级", "第三属性：1", "独立背景"])
    snapshot, batches = ontology(single=True), []
    original = run_executor(snapshot, Adapter()).run(**args, batch_hook=batches.append)
    prop = next(p for p in original.graph.properties if p.predicate_iri == ROOT_VALUE)
    source = next(item["task"] for item in batches[-1].task_outcomes if any(
        value["candidate_id"] == prop.candidate_id for value in item["outcome"]["properties"]
    ))
    assert batches[-1].frontier["layered_recognition"]["active_hop"] > 0
    review = {"review_id": "earlier-layer-rejection", "candidate_id": prop.candidate_id,
              "candidate_revision": prop.revision, "review_revision": 1,
              "decision": "rejected", "reason": "需要复核第一属性", "reason_code": "other",
              "after_outcomes": len(batches[-1].task_outcomes),
              "subject_ref": prop.subject_ref.model_dump(mode="json"),
              "predicate_iri": ROOT_VALUE, "original_task": source,
              "record_ids": [source["record_id"]]}
    adapter, revised_batches = Adapter(), []
    repaired = run_executor(snapshot, adapter).run(
        **args, **resume_arguments(batches[-1]), property_reviews=[review],
        batch_hook=revised_batches.append,
    )
    assert adapter.tasks and all(task.predicate_iri == ROOT_VALUE for task in adapter.tasks)
    cold = Adapter()
    restored = run_executor(snapshot, cold).run(
        **args, **resume_arguments(revised_batches[-1]), property_reviews=[review],
    )
    assert not cold.tasks
    assert restored.graph == repaired.graph
