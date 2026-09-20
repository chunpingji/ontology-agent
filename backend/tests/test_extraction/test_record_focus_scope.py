"""Repeated focus types retain every allowed relation in record-driven evaluation."""

from app.evaluation.quality_guided_variant import build_quality_guided_variant
from app.services.extraction.evidence_identity import evidence_hash
from app.services.extraction.ontology_guided.contracts import (
    EdgeSpec,
    GraphEdge,
    GraphNode,
    OntologySnapshot,
    SlotSpec,
    VersionedRef,
)
from app.services.extraction.ontology_guided.executor import TaskOutcome
from tests.test_extraction.test_layered_recognition import arguments
from tests.test_extraction.test_ontology_guided_core import _definition
from tests.test_extraction.test_record_executor import (
    EDGE,
    ROOT,
    ROOT_EDGE,
    TEXT,
    VALUE,
    A,
    B,
    record_setup,
)

pytest_plugins = ["tests.test_extraction.test_tool_engine_resume"]

SELF_EDGE, UNRELATED = "urn:nextDevice", "urn:unrelated"
FOCUS = (ROOT_EDGE, SELF_EDGE, EDGE)


def focus_ontology():
    classes = {
        ROOT: _definition(ROOT, "报告", relationships=[EdgeSpec(
            iri=ROOT_EDGE, label="描述", declared_by=[ROOT], range_class_iris=[A],
        )]),
        A: _definition(A, "装置", properties=[SlotSpec(
            iri=VALUE, label="型号", declared_by=[A],
            datatype_iris=["http://www.w3.org/2001/XMLSchema#string"],
        )], relationships=[EdgeSpec(
            iri=iri, label=label, declared_by=[A], range_class_iris=[target],
        ) for iri, label, target in ((SELF_EDGE, "后续装置", A), (EDGE, "连接", B),
                                      (UNRELATED, "无关关系", B))]),
        B: _definition(B, "部件", properties=[SlotSpec(
            iri=VALUE, label="规格", declared_by=[B],
            datatype_iris=["http://www.w3.org/2001/XMLSchema#string"],
        )]),
    }
    return OntologySnapshot(
        snapshot_id="repeated-focus-types", ontology_hash=evidence_hash(classes),
        classes=classes, created_from="frozen_fixture",
    )


def test_record_evaluation_schedules_deeper_focus_predicate_for_independent_entity(
    tmp_path, monkeypatch, current_run,
):
    args, executor, requests, _hooks = record_setup(
        tmp_path, monkeypatch, current_run, empty_relations=True,
    )
    adapter = executor().adapter
    adapter.ontology = focus_ontology()
    runner = build_quality_guided_variant(
        ontology=adapter.ontology, adapter=adapter, focus_path=FOCUS, max_hops=3,
    )
    result = runner.run(**{key: value for key, value in args.items() if key != "run_fingerprint"})

    # No incoming edge exists: the independently proved A is registered at depth 1,
    # while the frozen type route also permits its outgoing EDGE at depth 2.
    assert {node.class_iri for node in result.graph.nodes} == {ROOT, A, B}
    assert not result.graph.edges
    predicates = {member["predicate_iri"] for view in requests if view["stage"] == "discovery"
                  for member in view.get("members", [])}
    assert predicates == set(FOCUS), result.diagnostics
    assert UNRELATED not in {item.predicate_iri for item in result.graph.coverage}
    assert result.graph.progress.completion == "policy_complete", result.diagnostics


class LegacyFocusAdapter:
    model_identity = "legacy-focus-fixture"

    def __init__(self):
        self.tasks = []

    def inspect(self, task, context, predicate, _menu):
        self.tasks.append(task)
        if predicate.kind != "relationship":
            return TaskOutcome(semantic_outcome="not_checked", reason_code="fixture_empty",
                               reason="当前记录没有属性候选")
        source = next(fragment for fragment in context.fragments if fragment.fact_eligible)
        identity, class_iri = {
            ROOT_EDGE: ("first-device", A), SELF_EDGE: ("second-device", A), EDGE: ("part", B),
        }[predicate.iri]
        node = GraphNode(entity_id=identity, revision=1, class_iri=class_iri,
                         class_label=class_iri, label=identity, evidence_refs=[source.anchor])
        edge = GraphEdge(
            candidate_id="edge-" + identity, revision=1,
            subject_ref=VersionedRef(id=task.subject.entity_id, revision=task.subject.revision),
            object_ref=VersionedRef(id=identity, revision=1), predicate_iri=predicate.iri,
            predicate_label=predicate.label, evidence_refs=[source.anchor],
            decision_status="supported", structural_valid=True, model_supported=True,
            policy_eligible=True, proof_ref=VersionedRef(id="proof-" + identity, revision=1),
            decision_refs=[VersionedRef(id="decision-" + identity, revision=1)],
        )
        return TaskOutcome(semantic_outcome="supported", reason_code="fixture_relation",
                           reason="受控原文关系", nodes=[node], edges=[edge])


def test_legacy_evaluation_retains_focus_position_filter_for_repeated_types(tmp_path):
    args = arguments(tmp_path, [TEXT])
    args["root_class_iri"] = ROOT
    adapter = LegacyFocusAdapter()
    runner = build_quality_guided_variant(
        ontology=focus_ontology(), adapter=adapter, focus_path=FOCUS, max_hops=3,
    )
    result = runner.run(**{key: value for key, value in args.items() if key != "run_fingerprint"})

    actual = {(task.subject.entity_id, task.predicate_iri, task.hop)
              for task in adapter.tasks if task.predicate_kind == "relationship"}
    assert actual == {
        (result.graph.root_ref.id, ROOT_EDGE, 0),
        ("first-device", SELF_EDGE, 1), ("second-device", EDGE, 2),
    }, result.diagnostics
    assert result.graph.progress.completion == "in_scope_complete"
