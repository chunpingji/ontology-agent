"""Layer barriers, proof-bound slot completion and deterministic recovery."""

import pytest
from docx import Document

from app.services.extraction.evidence_identity import evidence_hash
from app.services.extraction.ontology_guided.adaptive_retrieval import AdaptivePolicy
from app.services.extraction.ontology_guided.contracts import (
    EdgeSpec,
    GraphEdge,
    GraphNode,
    GraphProperty,
    OntologySnapshot,
    SlotSpec,
    VersionedRef,
)
from app.services.extraction.ontology_guided.executor import TaskOutcome
from app.services.extraction.ontology_guided.heuristic_search import HeuristicSearchPolicy
from app.services.extraction.ontology_guided.metadata import prepare_metadata
from app.services.extraction.word_analysis import analyze_word_core
from tests.test_extraction.test_ontology_guided_core import _definition
from tests.test_extraction.test_sparse_candidate_planning import executor, resume_arguments

ROOT, CHILD, GRAND = "urn:layer:Root", "urn:layer:Child", "urn:layer:Grand"
ROOT_VALUE, CHILD_VALUE, GRAND_VALUE = "urn:rootValue", "urn:childValue", "urn:grandValue"
HAS_CHILD, HAS_GRAND = "urn:hasChild", "urn:hasGrand"


def arguments(tmp_path, lines):
    document = Document()
    for text in lines:
        document.add_paragraph(text)
    path = tmp_path / "layered.docx"
    document.save(path)
    analysis = analyze_word_core(path)
    return dict(
        recognition_run_id="layered-run", run_fingerprint="layered-fingerprint",
        ir=analysis.ir, metadata=prepare_metadata(
            analysis.ir, section_tree=analysis.structure.section_tree.to_dict(),
            summary_version="layered-test",
        ), root_class_iri=ROOT, root_class_label="报告", filename=path.name,
    )


def ontology(*, single=False, branches=True):
    classes = {
        ROOT: _definition(ROOT, "报告", properties=[SlotSpec(
            iri=ROOT_VALUE, label="第一属性", max_count=1 if single else None,
        )], relationships=[EdgeSpec(
            iri=HAS_CHILD, label="具有子项", range_class_iris=[CHILD],
        )] if branches else []),
        CHILD: _definition(CHILD, "子项", properties=[SlotSpec(
            iri=CHILD_VALUE, label="第二属性",
        )], relationships=[EdgeSpec(
            iri=HAS_GRAND, label="具有末级", range_class_iris=[GRAND],
        )]),
        GRAND: _definition(GRAND, "末级", properties=[SlotSpec(
            iri=GRAND_VALUE, label="第三属性",
        )]),
    }
    return OntologySnapshot(
        snapshot_id="layered-ontology", ontology_hash=evidence_hash(classes),
        classes=classes, created_from="frozen_fixture",
    )


class Adapter:
    model_identity = "layered-test"

    def __init__(self, *, conditional=False, conflict=False, fail_survey=False):
        self.tasks = []
        self.conditional = conditional
        self.conflict = conflict
        self.fail_survey = fail_survey

    def inspect(self, task, context, predicate, _menu):
        self.tasks.append(task)
        source = next(fragment for fragment in context.fragments if fragment.fact_eligible)
        subject = VersionedRef(id=task.subject.entity_id, revision=task.subject.revision)
        if task.retry_kind and self.conflict:
            return TaskOutcome(semantic_outcome="undetermined", reason_code="source_conflict",
                               reason="来源数值冲突，已完成对比")
        if self.fail_survey and "后文" in source.text:
            return TaskOutcome(semantic_outcome="not_checked", complete=False,
                               reason_code="model_timeout", reason="核验技术失败")
        if predicate.label not in source.text:
            return TaskOutcome(semantic_outcome="unsupported", reason_code="not_supported",
                               reason="此原文不支持当前谓词")
        common = dict(
            subject_ref=subject, predicate_iri=predicate.iri, predicate_label=predicate.label,
            decision_status="supported", structural_valid=True, model_supported=True,
            policy_eligible=True, evidence_refs=[source.anchor],
            predicate_evidence_refs=[source.anchor],
            proof_ref=VersionedRef(id=f"proof-{task.task_id}", revision=1),
            decision_refs=[VersionedRef(id=f"decision-{task.task_id}", revision=1)],
        )
        if predicate.kind == "relationship":
            identifiers = ["child-a", "child-b"] if predicate.iri == HAS_CHILD else ["grand"]
            class_iri = CHILD if predicate.iri == HAS_CHILD else GRAND
            nodes = [GraphNode(entity_id=identifier, revision=1, class_iri=class_iri,
                               class_label=class_iri, label=identifier,
                               evidence_refs=[source.anchor]) for identifier in identifiers]
            edges = [GraphEdge(
                candidate_id=f"edge-{subject.id}-{node.entity_id}", revision=1,
                object_ref=VersionedRef(id=node.entity_id, revision=1), **common,
            ) for node in nodes]
            return TaskOutcome(semantic_outcome="supported", reason_code="supported",
                               reason="明确关系", nodes=nodes, edges=edges)
        value = "2" if self.conflict and "后文" in source.text else "1"
        prop = GraphProperty(
            candidate_id=f"value-{subject.id}-{predicate.iri}-{task.record_id}", revision=1,
            raw_value=value, conditions=["特定批次"] if self.conditional else [], **common,
        )
        return TaskOutcome(semantic_outcome="supported", reason_code="supported", reason="明确数值",
                           properties=[prop])


def run_executor(snapshot, adapter, **options):
    return executor(
        snapshot, adapter=adapter, layered_recognition=True,
        heuristic_policy=HeuristicSearchPolicy.durable(
            incremental=True, initial_page_size=1, expanded_page_size=1,
            exploration_page_size=1,
        ), **options,
    )


def test_each_layer_finishes_properties_then_relations_before_next_subject_plans(tmp_path):
    args = arguments(tmp_path, ["第一属性：1", "具有子项：子项", "第二属性：1",
                                "具有末级：末级", "第三属性：1", "独立背景"])
    adapter, batches = Adapter(), []
    result = run_executor(ontology(), adapter).run(**args, batch_hook=batches.append)
    order = [(task.hop, task.predicate_kind == "relationship") for task in adapter.tasks
             if not task.retry_kind]
    assert order == sorted(order)
    assert {task.hop for task in adapter.tasks} == {0, 1, 2}
    first_relation = next(batch for batch in batches if batch.task.predicate_iri == HAS_CHILD
                          and batch.outcome.edges)
    assert all(plan["subject"]["entity_id"] != "child-a"
               for plan in first_relation.recall_ledger.values())
    assert len([plan for plan in result.retrieval_plans
                if plan["subject"]["entity_id"] == "grand"]) == 1
    child_subjects = [task.subject.entity_id for task in adapter.tasks
                      if task.hop == 1 and task.predicate_kind == "property"]
    assert child_subjects[:2] == ["child-a", "child-b"]
    assert result.graph.progress.completion == "policy_complete"


def test_single_value_closes_only_after_lexical_survey_without_claiming_full_coverage(tmp_path):
    args = arguments(tmp_path, ["第一属性：1", *[f"独立背景{i}" for i in range(10)]])
    adapter, batches = Adapter(), []
    result = run_executor(ontology(single=True, branches=False), adapter).run(
        **args, batch_hook=batches.append,
    )
    assert len(adapter.tasks) == 1
    assert result.graph.progress.records_examined == 1
    assert result.graph.coverage[0].stop_reason == "single_value_satisfied"
    receipt = next(iter(batches[-1].frontier["layered_recognition"]["slot_completion"][
        "receipts"
    ].values()))
    assert receipt["unexamined_source_count"] == 10
    assert receipt["coverage_claim"] == "policy_scope_only"
    assert result.graph.progress.completion == "policy_complete"


def test_closing_one_slot_does_not_remove_the_same_source_from_another_attribute(tmp_path):
    args = arguments(tmp_path, ["第一属性：1；第二属性：1", "独立背景"])
    snapshot = ontology(single=True, branches=False)
    snapshot.classes[ROOT].declared_properties.append(SlotSpec(
        iri=CHILD_VALUE, label="第二属性", max_count=1,
    ))
    result = run_executor(snapshot, Adapter()).run(**args)
    assert {prop.predicate_iri for prop in result.graph.properties} == {ROOT_VALUE, CHILD_VALUE}
    assert all(coverage.stop_reason == "single_value_satisfied"
               for coverage in result.graph.coverage)


@pytest.mark.parametrize("failure", [False, True])
def test_late_lexical_conflict_or_failed_survey_cannot_be_hidden_by_binding(tmp_path, failure):
    args = arguments(tmp_path, ["第一属性：1", "独立背景", "后文第一属性：2"])
    adapter = Adapter(conflict=not failure, fail_survey=failure)
    result = run_executor(ontology(single=True, branches=False), adapter).run(**args)
    assert len({task.record_id for task in adapter.tasks}) >= 2
    assert result.graph.coverage[0].stop_reason != "single_value_satisfied"
    assert result.graph.progress.completion == ("incomplete" if failure else "policy_complete")
    if not failure:
        assert any(task.retry_kind for task in adapter.tasks)


@pytest.mark.parametrize("mode", ["unspecified", "multiple", "conditional", "unresolved"])
def test_unsupported_completion_modes_keep_ordinary_search(tmp_path, mode):
    args = arguments(tmp_path, ["第一属性：1", "独立背景"])
    snapshot = ontology(single=mode in {"conditional", "unresolved"}, branches=False)
    predicate = snapshot.classes[ROOT].declared_properties[0]
    if mode == "multiple":
        predicate.multiplicity = "multiple"
    if mode == "unresolved":
        predicate.constraint_status = "constraint_unresolved"
    adapter = Adapter(conditional=mode == "conditional")
    result = run_executor(snapshot, adapter).run(**args)
    assert len(adapter.tasks) > 1
    assert all(coverage.stop_reason != "single_value_satisfied"
               for coverage in result.graph.coverage)


def test_budget_before_relationship_activation_retains_unexpanded_obligation(tmp_path):
    args = arguments(tmp_path, ["第一属性：1", "具有子项：子项"])
    result = run_executor(ontology(single=True), Adapter(), max_tasks=1).run(**args)
    assert result.graph.progress.completion == "incomplete"
    assert result.graph.progress.pending_frontiers > 0
    assert result.graph.progress.stop_reason == "task_budget_exhausted"


def test_cold_recovery_rebuilds_layer_barriers_and_slot_receipts(tmp_path):
    args = arguments(tmp_path, ["第一属性：1", "具有子项：子项", "第二属性：1",
                                "具有末级：末级", "第三属性：1", "独立背景"])
    snapshot, batches = ontology(single=True), []
    original = run_executor(snapshot, Adapter()).run(**args, batch_hook=batches.append)
    # Recover immediately after a proved relation registered the next layer,
    # before its properties were planned or any model request was issued for it.
    boundary = next(batch for batch in batches if batch.task.predicate_iri == HAS_CHILD
                    and batch.outcome.edges)
    replay = Adapter()
    restored = run_executor(snapshot, replay).run(**args, **resume_arguments(boundary))
    assert restored.graph.model_dump(mode="json") == original.graph.model_dump(mode="json")
    prefix_ids = {item["task"]["task_id"] for item in boundary.task_outcomes}
    assert not prefix_ids.intersection(task.task_id for task in replay.tasks)


def test_adaptive_search_uses_the_same_closure_and_frozen_recovery_policy(tmp_path):
    args = arguments(tmp_path, ["第一属性：1", "后文第一属性：1", "独立背景"])
    snapshot, batches = ontology(single=True, branches=False), []

    def build(adapter, *, enabled):
        return executor(
            snapshot, adapter=adapter, layered_recognition=enabled,
            heuristic_policy=HeuristicSearchPolicy.durable(
                adaptive=True, initial_page_size=1, expanded_page_size=1,
                exploration_page_size=1,
            ), adaptive_policy=AdaptivePolicy(mode="enhanced"),
        )

    original = build(Adapter(), enabled=True).run(**args, batch_hook=batches.append)
    assert original.graph.coverage[0].stop_reason == "single_value_satisfied"
    restored = build(Adapter(), enabled=False).run(**args, **resume_arguments(batches[0]))
    assert restored.graph.model_dump(mode="json") == original.graph.model_dump(mode="json")
