"""Qualified traversal keeps exact path scope without changing legacy task identity."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.schemas.evidence import EvidenceAnchor
from app.services.extraction.evidence_identity import evidence_hash, stable_id
from app.services.extraction.ontology_guided.contracts import (
    GraphEdge,
    GraphNode,
    GraphProperty,
    GraphRelationshipGroup,
    RunProgress,
    ScopeMember,
    SubjectRef,
    TraversalScope,
    VersionedRef,
)
from app.services.extraction.ontology_guided.dependencies import DependencyIndex
from app.services.extraction.ontology_guided.lazy_frontier import slot_key, subject_key
from app.services.extraction.ontology_guided.projection import (
    TOOL_EXTRACTION_PROTOCOL,
    frontier_eligibility,
    project_graph,
)
from app.services.extraction.ontology_guided.scheduler import FrontierScheduler, RecognitionTask


def ref(name, revision=1):
    return VersionedRef(id=name, revision=revision)


def scope(name="group", member="a", revision=1):
    return TraversalScope.create([
        ScopeMember(relation_ref=ref(name, revision), member_ref=ref(member)),
    ])


def anchor():
    return EvidenceAnchor(document_hash="a" * 64, structure_hash="b" * 64,
                          parser_version="test", evidence_id="e", section_node_id="s", block_id="b")


def relation(*, grouped=True, **changes):
    common = dict(candidate_id="group" if grouped else "edge", revision=1, subject_ref=ref("root"),
                  predicate_iri="urn:related", predicate_label="related", modality="asserted",
                  scope=TraversalScope.create(), decision_status="supported", structural_valid=True,
                  model_supported=True, policy_eligible=True, proof_ref=ref("proof"),
                  decision_refs=[ref("decision")], evidence_refs=[anchor()])
    common.update(changes)
    return (GraphRelationshipGroup(object_refs=[ref("a"), ref("b")], selection="all",
                                    selection_evidence_refs=[anchor()], **common)
            if grouped else GraphEdge(object_ref=ref("a"), **common))


def eligible(item, member="a", inherited=None, dependencies=None):
    return frontier_eligibility(item, member=ref(member),
                                inherited_scope=inherited or TraversalScope.create(),
                                dependencies=dependencies or DependencyIndex())


def test_plain_affirmed_edge_needs_no_extra_scope_but_group_does():
    assert eligible(relation(grouped=False)).scope == TraversalScope.create()
    result = eligible(relation())
    assert result.eligible and result.scope == scope()


@pytest.mark.parametrize("changes", [{"conditions": ["when stated"]},
                                      {"applicability": {"context": "local"}},
                                      {"modality": "required"}, {"modality": "possible"},
                                      {"modality": "planned"}])
def test_qualified_edges_expand_only_with_a_scope(changes):
    result = eligible(relation(grouped=False, **changes))
    assert result.eligible and result.scope == scope("edge")


@pytest.mark.parametrize("changes,reason", [
    ({"polarity": "negated"}, "relation_not_affirmed"),
    ({"modality": "unspecified"}, "modality_unspecified"),
    ({"structural_valid": False}, "relation_proof_incomplete"),
    ({"proof_ref": None}, "relation_proof_incomplete"),
    ({"decision_status": "undetermined"}, "relation_proof_incomplete"),
])
def test_unproved_or_negative_relations_do_not_expand(changes, reason):
    result = eligible(relation(**changes))
    assert not result.eligible and result.reason_code == reason


def test_unknown_selection_stays_unexecuted():
    group = relation().model_copy(update={"selection": "undetermined"})
    assert eligible(group).reason_code == "selection_undetermined"


def test_member_and_scope_revisions_cannot_be_rebound_to_new_heads():
    group = relation()
    assert not frontier_eligibility(group, member=ref("a", 2),
                                    inherited_scope=TraversalScope.create(),
                                    dependencies=DependencyIndex()).eligible
    assert eligible(group, inherited=scope(revision=2)).reason_code == "scope_revision_mismatch"


def test_one_of_scope_cannot_include_two_members_and_cycles_stop():
    group = relation().model_copy(update={"selection": "one_of"})
    first = eligible(group)
    assert first.eligible
    conflict = eligible(group, member="b", inherited=first.scope)
    assert not conflict.eligible and conflict.reason_code == "scope_conflict"
    cycle = eligible(group, inherited=first.scope)
    assert not cycle.eligible and cycle.reason_code == "scope_cycle"
    assert cycle.scope == first.scope


def test_inherited_scope_cannot_drop_a_claims_original_scope():
    inherited = scope("prior")
    item = relation(scope=inherited)
    assert eligible(item).reason_code == "scope_mismatch"
    result = eligible(item, inherited=inherited)
    assert result.eligible and len(result.scope.members) == 2


@pytest.mark.parametrize("invalid", ["group@1", "proof@1", "decision@1", "a@1"])
def test_every_exact_proof_and_endpoint_dependency_can_block_expansion(invalid):
    dependencies = DependencyIndex()
    dependencies.invalidate(invalid)
    assert not eligible(relation(), dependencies=dependencies).eligible
    # An unrelated revision does not invalidate this exact claim.
    other = DependencyIndex()
    other.invalidate(invalid.replace("@1", "@2"))
    assert eligible(relation(), dependencies=other).eligible


def make_task(path_scope=None, *, record="record"):
    return RecognitionTask.create(
        subject=SubjectRef(entity_id="a", revision=1, class_iri="urn:Class"),
        predicate_iri="urn:p", predicate_kind="property", record_id=record, phase=1, hop=1,
        dependency_hash="dependency", scope=path_scope,
    )


def test_legacy_task_serialization_and_identity_are_unchanged():
    task = make_task()
    assert "scope" not in task.model_dump()
    assert task.task_id == stable_id("recognition-task", [task.subject.model_dump(mode="json"),
                                                         "urn:p", "record", "dependency", None])
    assert task.claim_lineage_id == stable_id("claim-lineage", ["a", "urn:p", "record"])
    assert RecognitionTask.model_validate(task.model_dump()).model_dump() == task.model_dump()


def test_new_empty_scope_and_different_paths_have_distinct_task_and_lineage_ids():
    tasks = [make_task(value)
             for value in [None, TraversalScope.create(), scope(), scope(member="b")]]
    assert len({task.task_id for task in tasks}) == len(tasks)
    assert len({task.claim_lineage_id for task in tasks}) == len(tasks)
    assert len(slot_key(tasks[0].subject, "urn:p")) == 3
    assert slot_key(tasks[1].subject, "urn:p", tasks[1].scope) == (
        "a", 1, tasks[1].scope.scope_id, "urn:p",
    )
    assert subject_key(tasks[2].subject, tasks[2].scope) == ("a", 1, tasks[2].scope.scope_id)


def plan(task):
    return SimpleNamespace(subject=task.subject, predicate_iri=task.predicate_iri,
                           predicate_kind=task.predicate_kind, records=[
                               SimpleNamespace(record_id="record", phase=1, section_node_id="s"),
                           ])


@pytest.mark.parametrize("lazy", [False, True])
@pytest.mark.parametrize("current", [False, True])
def test_scoped_scheduler_dedup_peek_exclusion_ranking_and_cold_restore(lazy, current):
    tasks = [make_task(scope()), make_task(scope(member="b"))]
    scheduler = FrontierScheduler()
    if current:
        scheduler.enable_current_state()
    for task in tasks:
        if lazy:
            assert scheduler.enqueue_plan(plan(task), hop=1, dependency_hash="dependency",
                                          source_positions={"record": 0}, scope=task.scope) == 1
            assert not scheduler.enqueue(task)
        else:
            assert scheduler.enqueue(task)
    assert scheduler.pending == 2 and len(scheduler.pending_slots) == 2
    scheduler.reorder_slot(tasks[0].subject, "urn:p", ["record"], epoch_seq=1, scope=tasks[0].scope)
    first = scheduler.peek_fresh_slot(tasks[0].subject, "urn:p", scope=tasks[0].scope)
    second = scheduler.peek_fresh_slot(tasks[1].subject, "urn:p", scope=tasks[1].scope)
    assert first.task_id == tasks[0].task_id and first.ranking_epoch_seq == 1
    assert second.task_id == tasks[1].task_id and second.ranking_epoch_seq is None
    excluded = {slot_key(tasks[0].subject, "urn:p", tasks[0].scope)}
    assert scheduler.peek_task(excluded).task_id == tasks[1].task_id
    if current:
        restored = FrontierScheduler.from_current(scheduler.current_changes())
    else:
        restored = FrontierScheduler.from_snapshot(scheduler.snapshot())
    assert restored.pending_slots == scheduler.pending_slots
    assert [restored.next_task().task_id for _ in range(2)] == [
        scheduler.next_task().task_id for _ in range(2)
    ]


def test_discarding_one_path_keeps_an_independently_scheduled_path():
    tasks = [make_task(scope()), make_task(scope(member="b"))]
    scheduler = FrontierScheduler()
    for task in tasks:
        scheduler.enqueue(task)
    discarded = scheduler.discard_subject(tasks[0].subject, scope=tasks[0].scope)
    assert [task.task_id for task in discarded] == [tasks[0].task_id]
    assert scheduler.next_task().task_id == tasks[1].task_id


def test_legacy_lazy_snapshot_hash_does_not_gain_a_scope_field():
    scheduler = FrontierScheduler()
    task = make_task()
    scheduler.enqueue_plan(plan(task), hop=1, dependency_hash="dependency",
                           source_positions={"record": 0})
    original = scheduler.snapshot()
    frontier = original["logical_frontiers"][0]
    assert "scope" not in frontier
    payload = {key: value for key, value in frontier.items()
               if key not in {"content_hash", "template_priority", "consumed", "ranks"}}
    assert frontier["content_hash"] == evidence_hash(payload)
    assert FrontierScheduler.from_snapshot(original).snapshot() == original


def node(name, *, root=False, proved=True, **changes):
    data = dict(entity_id=name, revision=1, class_iri="urn:Class", class_label="Class", label=name,
                root=root, grounding_kind="document_root" if root else "mention",
                evidence_refs=[anchor()])
    if not root:
        data.update(referent_ref=ref("referent:" + name),
                    type_decision_ref=ref("type:" + name) if proved else None,
                    referent_decision_ref=ref("referent-decision:" + name) if proved else None)
    return GraphNode(**{**data, **changes})


def property_claim(**changes):
    data = relation(grouped=False).model_dump(exclude={"object_ref", "object_evidence_refs"})
    data.update(candidate_id="property", raw_value="value")
    return GraphProperty(**{**data, **changes})


def project(*, projection="verified", nodes=None, edges=None, groups=None, properties=None,
            dependencies=None, protocol=TOOL_EXTRACTION_PROTOCOL):
    return project_graph(
        recognition_run_id="run", run_revision=1, event_head=0, metadata_snapshot_id=None,
        root_ref=ref("root"), nodes=nodes or [node("root", root=True), node("a"), node("b")],
        edges=edges or [], properties=properties or [], relationship_groups=groups,
        coverage=[], progress=RunProgress(), dependency_index=dependencies,
        projection=projection, extraction_protocol=protocol,
    )


def test_verified_keeps_supported_group_and_independently_proved_isolated_nodes():
    result = project(nodes=[node("root", root=True), node("a"), node("b"), node("isolated"),
                            node("unproved", proved=False)], groups=[relation()])
    assert {n.entity_id for n in result.nodes} == {"root", "a", "b", "isolated"}
    assert len(result.relationship_groups) == 1 and not result.edges


@pytest.mark.parametrize("selection,visible", [("all", True), ("one_of", False),
                                               ("alternatives", False)])
def test_effective_group_reachability_never_creates_pairwise_facts(selection, visible):
    group = relation().model_copy(update={"selection": selection})
    result = project(projection="effective", groups=[group], properties=[
        property_claim(subject_ref=ref("a")),
    ])
    assert bool(result.relationship_groups) == visible
    assert bool(result.properties) == visible and not result.edges
    assert {n.entity_id for n in result.nodes} == ({"root", "a", "b"} if visible else {"root"})


@pytest.mark.parametrize("changes", [{"modality": "unspecified"}, {"modality": "required"},
                                      {"conditions": ["condition"]},
                                      {"applicability": {"scope": "local"}},
                                      {"polarity": "negated"}])
def test_effective_does_not_promote_verified_qualifiers(changes):
    edge = relation(grouped=False, **changes)
    assert len(project(edges=[edge]).edges) == 1
    assert not project(projection="effective", edges=[edge]).edges


def test_conditional_uses_inherited_scope_even_if_parent_group_is_filtered_out():
    group = relation().model_copy(update={"selection": "one_of"})
    prop = property_claim(subject_ref=ref("a"), scope=scope())
    result = project(projection="conditional", groups=[group], properties=[prop])
    assert not result.relationship_groups and result.properties == [prop]
    assert result.properties[0].scope == scope()


@pytest.mark.parametrize("failure", ["old_relation", "old_member", "missing_parent", "conflict"])
def test_verified_cannot_hide_unresolved_or_conflicting_scope(failure):
    group = relation().model_copy(update={"selection": "one_of"})
    inherited = scope()
    if failure == "old_relation":
        inherited = scope(revision=2)
    elif failure == "old_member":
        inherited = TraversalScope.create([
            ScopeMember(relation_ref=ref("group"), member_ref=ref("a", 2)),
        ])
    elif failure == "missing_parent":
        inherited = scope("missing")
    else:
        inherited = TraversalScope.create([*scope().members, *scope(member="b").members])
    prop = property_claim(subject_ref=ref("a"), scope=inherited)
    assert not project(groups=[group], properties=[prop]).properties
    assert project(projection="all", groups=[group], properties=[prop]).properties == [prop]


def test_dependency_invalidation_hides_group_and_scoped_child_without_rebinding():
    dependencies = DependencyIndex()
    dependencies.invalidate("group@1")
    result = project(groups=[relation()], properties=[property_claim(scope=scope())],
                     dependencies=dependencies)
    assert not result.relationship_groups and not result.properties


def test_current_graph_dependency_revision_is_checked_without_silent_head_substitution():
    group = relation(revision=2)
    prop = property_claim(dependency_refs=[ref("group", 1)])
    result = project(groups=[group], properties=[prop])
    assert result.relationship_groups == [group] and not result.properties


def test_duplicate_current_identity_does_not_select_an_arbitrary_revision():
    with pytest.raises(ValueError, match="current_graph_identity_ambiguous"):
        project(nodes=[node("root", root=True), node("a"), node("a", revision=2)])


def test_legacy_projection_rejects_verified_and_preserves_payload_hash_and_shape():
    with pytest.raises(ValueError, match="unsupported_projection"):
        project(protocol=None)
    result = project(projection="effective", protocol=None, edges=[relation(grouped=False)])
    assert "relationship_groups" not in result.model_dump()
    payload = {key: result.model_dump()[key] for key in (
        "recognition_run_id", "run_revision", "event_head", "metadata_snapshot_id", "root_ref",
        "projection", "nodes", "edges", "properties", "coverage", "progress",
    )}
    assert result.generated_from_hash == evidence_hash(payload)
