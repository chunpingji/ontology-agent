"""Source roles, proof-ready scheduling and compact independent verification."""

import json
from copy import deepcopy

import pytest

from app.services.extraction.ontology_guided import model_adapter
from app.services.extraction.ontology_guided.context import assemble_context
from app.services.extraction.ontology_guided.contracts import EdgeSpec, RangeClass, SlotSpec
from app.services.extraction.ontology_guided.heuristic_search import (
    HeuristicSearchIndex,
    HeuristicSearchPolicy,
)
from app.services.extraction.ontology_guided.model_adapter import RecognitionModelFailure
from app.services.extraction.ontology_guided.ontology_plan import compile_local_menu
from app.services.extraction.ontology_guided.records import RecordIndex
from app.services.extraction.ontology_guided.repair_adapter import EvidenceRepairAdapter
from tests.test_extraction.test_current_work_resume import merge
from tests.test_extraction.test_evidence_repair import _ontology, repaired_response
from tests.test_extraction.test_joint_evidence_validation import _fixture
from tests.test_extraction.test_layered_recognition import (
    CHILD_VALUE,
    HAS_CHILD,
    ROOT,
    Adapter,
    arguments,
    ontology,
)
from tests.test_extraction.test_sparse_candidate_planning import executor, resume_arguments


def build(snapshot, adapter, **kwargs):
    return executor(
        snapshot, adapter=adapter, layered_recognition=True, source_object_recognition=True,
        heuristic_policy=HeuristicSearchPolicy.durable(
            incremental=True, source_object_candidates=True,
            initial_page_size=1, expanded_page_size=1, exploration_page_size=1,
        ), **kwargs,
    )


def test_object_search_does_not_treat_attribute_labels_as_entity_mentions(tmp_path):
    lines = ["项目名称：A-01", "项目代码：P-01", "是否细胞毒药物：否",
             "是否是高致敏药物：否", "分子式：C23H28N6O5S", "分子量：500.60",
             "本报告另描述药物产品 B-02。", "项目名称：C-03"]
    args = arguments(tmp_path, lines)
    index = RecordIndex(args["ir"])
    search = HeuristicSearchIndex(index, args["metadata"])
    predicate = EdgeSpec(
        iri="urn:describes", label="描述", range_class_iris=["urn:DrugProduct"],
        range_classes=[RangeClass(iri="urn:DrugProduct", label="药物产品",
            direct_field_labels=["项目名称", "项目代码", "是否细胞毒药物", "是否高致敏药物",
                                 "分子量", "分子式"]),
            RangeClass(iri="urn:CytotoxicDrug", label="细胞毒药物"),
            RangeClass(iri="urn:SensitizingDrug", label="高致敏药物")],
    )
    old = search.candidates(predicate, subject_mentions=[],
                            policy=HeuristicSearchPolicy.durable(incremental=True))
    new = search.candidates(predicate, subject_mentions=[], policy=HeuristicSearchPolicy.durable(
        incremental=True, source_object_candidates=True,
    ))
    before = {index.by_id[rid].text for rid in old[0] + old[1]}
    after = {index.by_id[rid].text for rid in new[0] + new[1]}
    assert set(lines[2:6]) <= before
    assert not set(lines[2:6]) & after
    assert {lines[0], lines[1], lines[6], lines[7]} <= after
    assert len(search.record_ids) == len(lines)  # No source was deleted or marked examined.
    properties = search.candidates(SlotSpec(iri="urn:weight", label="分子量"), subject_mentions=[],
                                  policy=HeuristicSearchPolicy.durable(
                                      incremental=True, source_object_candidates=True))
    assert lines[5] in {index.by_id[rid].text for rid in properties[0]}


def test_proved_objects_continue_when_an_unrelated_root_branch_fails(tmp_path):
    args = arguments(tmp_path, ["第一属性：1", "后文清洗方法：未完成", "具有子项：子项",
                                "第二属性：1", "具有末级：末级", "第三属性：1"])
    snapshot = ontology(single=True)
    snapshot.classes[ROOT].declared_relationships.insert(0, EdgeSpec(
        iri="urn:cleaning", label="清洗方法", range_class_iris=[ROOT],
    ))
    adapter = Adapter(fail_survey=True)
    result = build(snapshot, adapter).run(**args)
    owners = {p.subject_ref.id for p in result.graph.properties if p.predicate_iri == CHILD_VALUE}
    assert owners == {"child-a", "child-b"}
    assert result.graph.progress.records_incomplete > 0
    assert result.graph.progress.completion == "incomplete"


@pytest.mark.parametrize("change", [
    {"polarity": "negated"}, {"conditions": ["特定批次"]},
    {"decision_status": "undetermined"}, {"policy_eligible": False},
])
def test_ready_order_does_not_expand_unproven_or_restricted_objects(tmp_path, change):
    class RestrictedAdapter(Adapter):
        def inspect(self, *args):
            outcome = super().inspect(*args)
            outcome.edges = [edge.model_copy(update=change) for edge in outcome.edges]
            return outcome

    args = arguments(tmp_path, ["第一属性：1", "具有子项：子项", "第二属性：1"])
    adapter = RestrictedAdapter()
    result = build(ontology(single=True), adapter).run(**args)
    assert not any(task.hop > 0 for task in adapter.tasks)
    assert not any(p.predicate_iri == CHILD_VALUE for p in result.graph.properties)


@pytest.mark.parametrize("current", [False, True])
def test_ready_order_resumes_without_repeating_completed_work(tmp_path, current):
    args = arguments(tmp_path, ["第一属性：1", "具有子项：子项", "第二属性：1",
                                "具有末级：末级", "第三属性：1", "背景"])
    snapshot, batches, rows, boundaries = ontology(single=True), [], {}, []

    def commit(batch):
        batches.append(batch)
        if current:
            merge(rows, batch.work_changes)
            if batch.task.predicate_iri == HAS_CHILD and batch.outcome.edges:
                boundaries.append(deepcopy(rows))

    original = build(snapshot, Adapter(), current_state=current).run(
        **args, batch_hook=commit, work_hook=lambda changes: merge(rows, changes),
    )
    boundary = next(b for b in batches if b.task.predicate_iri == HAS_CHILD and b.outcome.edges)
    replay = Adapter()
    if current:
        saved = json.loads(json.dumps(boundaries[0]))
        control = saved["control"]["current"]
        resume = {"resume_state": {"work_state": saved, "frontier": control["frontier_policy"],
                                   "diagnostics": control["diagnostics"]}}
    else:
        resume = resume_arguments(boundary)
    restored = build(snapshot, replay, current_state=current).run(**args, **resume)
    assert original.graph.progress.completion == "policy_complete"
    assert restored.graph == original.graph
    assert boundary.task.task_id not in {task.task_id for task in replay.tasks}


def test_same_field_group_attributes_receive_existing_source_priority(tmp_path):
    args = arguments(tmp_path, ["第一属性：1", "具有子项：子项", "第二属性：1",
                                "具有末级：末级", "第三属性：1"])
    rows, observations = {}, []

    def commit(batch):
        merge(rows, batch.work_changes)
        if batch.task.predicate_iri == HAS_CHILD and batch.outcome.edges:
            observations.extend(deepcopy(list(rows.get("attribute_sources", {}).values())))

    build(ontology(single=True), Adapter(), current_state=True).run(
        **args, batch_hook=commit, work_hook=lambda changes: merge(rows, changes),
    )
    child = [row for row in observations if row["key"][-1] == CHILD_VALUE]
    assert child and all(row["value"] for row in child)
    index = RecordIndex(args["ir"])
    assert {index.by_id[rid].text for row in child for rid in row["value"]} == {"第二属性：1"}


def inspect_protocol(tmp_path, monkeypatch, *, compact, alter=None):
    _analysis, index, task, target, binding = _fixture(tmp_path)
    menu = compile_local_menu(_ontology(), task.subject, engine=object())
    predicate = next(p for p in menu.relationships if p.iri == task.predicate_iri)
    context = assemble_context(target, task.record_id, index, required_context_refs=[binding],
                               repair_enabled=True)
    context.incremental_performance = True
    context.compact_recognition = compact
    requests = []

    def respond(_client, *, user, schema, system, **_kwargs):
        request = json.loads(user)
        requests.append({"request": request, "schema": schema,
                         "characters": len(user) + len(system) + len(json.dumps(schema))})
        response = repaired_response(request)
        if alter:
            alter(request, response)
        return response

    monkeypatch.setattr(model_adapter, "chat_with_schema", respond)
    result = EvidenceRepairAdapter(object(), model_identity="compact-test").inspect(
        task, context, predicate, menu,
    )
    return result, requests, context


def test_compact_transport_preserves_independent_proof_and_source_anchors(tmp_path, monkeypatch):
    original, large, _ = inspect_protocol(tmp_path, monkeypatch, compact=False)
    compact, small, context = inspect_protocol(tmp_path, monkeypatch, compact=True)
    assert compact.edges == original.edges
    assert compact.nodes == original.nodes
    assert compact.model_calls == original.model_calls == 2
    assert compact.edges[0].policy_eligible
    assert sum(r["characters"] for r in small) < sum(r["characters"] for r in large) * 0.8
    candidate = small[1]["request"]["candidates"][0]
    assert candidate["candidate_id"] == "C1" and candidate["target_id"] == "T1"
    assert "target" not in candidate
    assert "method_granularity_instruction" not in small[0]["request"]
    assert context.protocol_state["frozen_assertions"][0]["candidate_id"] != "C1"
    assert all("unit_verdict" not in definition.get("properties", {})
               for definition in small[1]["schema"]["$defs"].values())


@pytest.mark.parametrize("failure", ["foreign_candidate", "foreign_target", "background_endpoint"])
def test_compact_transport_rejects_foreign_ids_and_background_endpoints(
    tmp_path, monkeypatch, failure,
):
    def alter(request, response):
        if request["stage"] == "verification" and failure.startswith("foreign"):
            field = "candidate_id" if failure == "foreign_candidate" else "target_id"
            response["verifications"][0][field] = "foreign-task"
        if request["stage"] == "discovery" and failure == "background_endpoint":
            source = next(f for f in request["fragments"] if not f["fact_eligible"])
            response["proposals"][0]["object_quote"] = {
                "evidence_id": source["evidence_id"], "text": source["text"],
            }

    with pytest.raises(RecognitionModelFailure, match="citation_invalid"):
        inspect_protocol(tmp_path, monkeypatch, compact=True, alter=alter)
