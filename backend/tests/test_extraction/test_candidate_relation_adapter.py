"""Candidate discovery publishes quoted relations without purchasing a proof verdict."""

from copy import deepcopy

import pytest

from app.services.extraction.evidence_identity import evidence_hash
from app.services.extraction.ontology_guided.claim_protocol import (
    EntityDependencyView,
    EntityProposal,
)
from app.services.extraction.ontology_guided.contracts import GraphNode, VersionedRef
from app.services.extraction.ontology_guided.current_work import validate_tool_protocol
from app.services.extraction.ontology_guided.record_discovery import RecordDiscoveryPolicy
from app.services.extraction.ontology_guided.source_citations import resolve_fragment_quote
from tests.test_extraction.test_batch_model_adapter import batch_setup
from tests.test_extraction.test_record_relation_adapter import enable_pipeline
from tests.test_extraction.test_tool_engine_adapter import setup_adapter

pytest_plugins = ["tests.test_extraction.test_tool_engine_freeze"]


@pytest.fixture
def candidate_source(tool_source):
    source = deepcopy(tool_source)
    source["registered_proposals"] = source["proposal"]["entities"]
    source["proposal"]["entities"] = []
    # A quote-backed candidate need not provide a complete source assertion or
    # an already proven choice. The full selection group must still survive.
    source["proposal"]["relations"][0]["selection_support"] = []
    return source


def configure(adapter, contexts, source):
    enable_pipeline(adapter, contexts)
    adapter.record_discovery = RecordDiscoveryPolicy(graph_phase="candidate_graph")
    nodes = {}
    for context in contexts:
        subject = GraphNode.model_validate(context.tool_inputs["subject_node"])
        nodes[subject.entity_id] = subject
        for raw in source["registered_proposals"]:
            proposal = EntityProposal.model_validate(raw)
            quote = proposal.mentions[0]
            anchor = resolve_fragment_quote(
                quote.evidence_id, quote.text, context.fragments,
            )[0]
            ref = VersionedRef(id=proposal.local_id, revision=2)
            value = dict(
                entity_ref=ref, class_iri=proposal.class_iri, grounding_kind="mention",
                root_origin=None, proposal=proposal, source_refs=[anchor], dependency_refs=[],
            )
            dependency = EntityDependencyView(**value, content_hash=evidence_hash(value))
            context.tool_inputs["entity_dependencies"].append(dependency.model_dump(mode="json"))
            nodes[ref.id] = GraphNode(
                entity_id=ref.id, revision=ref.revision, class_iri=proposal.class_iri,
                class_label="对象", label=quote.text, evidence_refs=[anchor],
                referent_ref=VersionedRef(id=f"referent-{ref.id}", revision=1),
                type_decision_ref=VersionedRef(id=f"type-{ref.id}", revision=1),
                referent_decision_ref=VersionedRef(id=f"identity-{ref.id}", revision=1),
            )
        context.tool_inputs["entity_nodes"] = [n.model_dump(mode="json") for n in nodes.values()]
    return nodes


def assert_unverified(outcome):
    assert outcome.complete and outcome.semantic_outcome == "not_checked"
    assert not outcome.nodes and not outcome.properties
    assert not outcome.proof_payloads and not outcome.decision_payloads
    relation, = [*outcome.edges, *outcome.relationship_groups]
    assert relation.decision_status == "not_checked"
    assert not relation.structural_valid and not relation.model_supported
    assert not relation.policy_eligible and relation.proof_ref is None
    assert relation.decision_refs == [] and relation.evidence_refs
    return relation


def test_single_candidate_uses_one_discovery_and_no_proof(candidate_source, monkeypatch):
    adapter, task, context, predicate, menu, stored, requests = setup_adapter(
        candidate_source, monkeypatch, budget=1,
    )
    configure(adapter, [context], candidate_source)
    outcome = adapter.inspect(task, context, predicate, menu)
    group = assert_unverified(outcome)
    assert group.selection == "one_of"
    assert [ref.id for ref in group.object_refs] == ["b", "c"]
    assert not group.selection_evidence_refs
    assert len(requests) == 1 and outcome.model_calls == 1
    assert stored["protocol"]["verification_ref"] is None
    assert stored["protocol"]["tool_calls_used"] == 0
    assert "不执行关系核验" in requests[0]["instructions"]
    assert_unverified(adapter.inspect(task, context, predicate, menu))
    assert len(requests) == 1


def test_multiple_predicates_share_discovery_without_verification(candidate_source, monkeypatch):
    adapter, unit, context, menu, stored, requests = batch_setup(
        candidate_source, monkeypatch, size=2, relation=2,
    )
    nodes = configure(adapter, [m.context for m in context.members], candidate_source)
    context.remaining_model_calls_by_member = {task.task_id: 1 for task in unit.members}
    reviewed = adapter.inspect_work_unit(unit, context, menu)
    assert not reviewed.member_errors
    assert set(reviewed.member_result_refs) == {task.task_id for task in unit.members}
    assert all("verification_ref" not in value for value in reviewed.member_result_refs.values())
    for task in unit.members:
        outcome = adapter.finalize_reviewed_member(
            task, context, current_entities=nodes, current_resolutions=[],
        )
        assert assert_unverified(outcome).predicate_iri == task.predicate_iri
    assert len(requests) == 1 and not stored["protocol"]["verification_refs"]
    assert all(state["tool_calls_used"] == 0
               for state in stored["protocol"]["member_states"].values())
    adapter.inspect_work_unit(unit, context, menu)
    assert len(requests) == 1


@pytest.mark.parametrize("invalid", ["quote", "predicate", "endpoint", "range"])
def test_candidate_basic_authority_is_still_enforced(candidate_source, monkeypatch, invalid):
    relation = candidate_source["proposal"]["relations"][0]
    if invalid == "quote":
        relation["bridge_support"][0]["text"] = "原文不存在的关系"
    elif invalid == "predicate":
        relation["predicate_iri"] = "urn:outside-menu"
    elif invalid == "endpoint":
        relation["object_ids"][0] = "unregistered"
    else:
        candidate_source["registered_proposals"][0]["class_iri"] = "urn:Wrong"
    adapter, task, context, predicate, menu, _, requests = setup_adapter(
        candidate_source, monkeypatch, budget=1,
    )
    configure(adapter, [context], candidate_source)
    outcome = adapter.inspect(task, context, predicate, menu)
    assert not outcome.complete
    assert not outcome.edges and not outcome.relationship_groups
    assert len(requests) == 1


def test_single_edge_preserves_negation_modality_conditions(candidate_source, monkeypatch):
    relation = candidate_source["proposal"]["relations"][0]
    relation.update(object_ids=["b"], selection="all")
    relation["qualifiers"].update(
        polarity="negated", modality="possible",
        condition_support=[candidate_source["quote"]("恰选其一")],
    )
    adapter, task, context, predicate, menu, _, _ = setup_adapter(candidate_source, monkeypatch)
    configure(adapter, [context], candidate_source)
    edge = assert_unverified(adapter.inspect(task, context, predicate, menu))
    assert edge.object_ref == VersionedRef(id="b", revision=2)
    assert edge.direction == "outbound" and edge.polarity == "negated"
    assert edge.modality == "possible" and edge.conditions == ["恰选其一"]
    assert edge.condition_evidence_refs


def test_incomplete_bridge_proof_keeps_exact_candidate_source_roles(candidate_source, monkeypatch):
    relation = candidate_source["proposal"]["relations"][0]
    quote = candidate_source["quote"]
    relation.update(
        bridge_kind="resolved_reference_chain", bridge_ref_ids=[], bridge_support=[],
        source_assertion=dict(
            subject_support=[quote("主体甲")],
            object_support=[dict(object_id="b", support=[quote("对象乙")])],
            predicate_support=[quote("主体甲关联对象乙或对象丙，恰选其一")],
            binding_ids=[], binding_dependency_refs=[],
        ),
    )
    adapter, task, context, predicate, menu, _, _ = setup_adapter(candidate_source, monkeypatch)
    configure(adapter, [context], candidate_source)
    group = assert_unverified(adapter.inspect(task, context, predicate, menu))
    assert len(group.object_refs) == 2
    assert group.subject_evidence_refs and group.object_evidence_refs
    assert group.predicate_evidence_refs


def test_batch_current_endpoint_revision_is_required(candidate_source, monkeypatch):
    adapter, unit, context, menu, _, requests = batch_setup(
        candidate_source, monkeypatch, size=1, relation=1,
    )
    nodes = configure(adapter, [m.context for m in context.members], candidate_source)
    adapter.inspect_work_unit(unit, context, menu)
    nodes["b"] = nodes["b"].model_copy(update={"revision": 3})
    outcome = adapter.finalize_reviewed_member(
        unit.members[0], context, current_entities=nodes, current_resolutions=[],
    )
    assert not outcome.complete and not outcome.relationship_groups
    assert outcome.reason_code == "candidate_relation_endpoint_revision_mismatch"
    assert len(requests) == 1


@pytest.mark.parametrize("pause", ["model_turn", "discovery"])
def test_candidate_batch_cold_resume_consumes_paid_discovery_without_more_calls(
    candidate_source, monkeypatch, pause,
):
    adapter, unit, context, menu, stored, requests = batch_setup(
        candidate_source, monkeypatch, size=2, relation=2, stop_at=pause,
    )
    nodes = configure(adapter, [m.context for m in context.members], candidate_source)
    with pytest.raises(RuntimeError, match="pause after durable commit"):
        adapter.inspect_work_unit(unit, context, menu)
    assert len(requests) == 1
    original_results = deepcopy(stored["results"])
    resumed = context.model_copy(deep=True)
    resumed.protocol_state = deepcopy(stored["protocol"])
    resumed.protocol_results = deepcopy(stored["results"])
    resumed.remaining_model_calls_by_member = dict.fromkeys([t.task_id for t in unit.members], 0)

    def checkpoint(value):
        value = deepcopy(value)
        changes = value.pop("result_changes", {})
        validate_tool_protocol(value)
        stored["protocol"] = value
        stored["results"].update(changes)

    resumed.bind_protocol_hook(checkpoint)
    reviewed = adapter.inspect_work_unit(unit, resumed, menu)
    assert not reviewed.member_errors and len(reviewed.member_result_refs) == 2
    for task in unit.members:
        assert_unverified(adapter.finalize_reviewed_member(
            task, resumed, current_entities=nodes, current_resolutions=[],
        ))
    assert len(requests) == len(stored["reservations"]) == 1
    assert not stored["protocol"]["verification_refs"]
    assert all(stored["results"][key] == value for key, value in original_results.items())


def test_candidate_single_cold_resume_after_discovery_needs_no_verification_budget(
    candidate_source, monkeypatch,
):
    adapter, task, context, predicate, menu, stored, requests = setup_adapter(
        candidate_source, monkeypatch, stop_at="discovery", budget=1,
    )
    configure(adapter, [context], candidate_source)
    with pytest.raises(RuntimeError, match="pause after durable commit"):
        adapter.inspect(task, context, predicate, menu)
    assert len(requests) == 1
    saved = deepcopy(stored)
    adapter, task, context, predicate, menu, stored, requests = setup_adapter(
        candidate_source, monkeypatch, budget=0,
    )
    configure(adapter, [context], candidate_source)
    stored.update(saved)
    context.protocol_state, context.protocol_results = saved["protocol"], saved["results"]
    assert_unverified(adapter.inspect(task, context, predicate, menu))
    assert not requests and stored["protocol"]["verification_ref"] is None


@pytest.mark.parametrize("relations,phase,discovery_only", [
    (2, "candidate_graph", True),
    (1, "candidate_graph", False),
    (0, "candidate_graph", False),
    (2, "evidence_verification", False),
])
def test_batch_capacity_reserves_only_stages_that_will_run(
    candidate_source, monkeypatch, relations, phase, discovery_only,
):
    adapter, unit, context, menu, _, requests = batch_setup(
        candidate_source, monkeypatch, size=2, relation=relations,
    )
    configure(adapter, [member.context for member in context.members], candidate_source)
    adapter.record_discovery = RecordDiscoveryPolicy(graph_phase=phase)
    adapter.stage_output_tokens = {"discovery": 8192, "verification": 16384}
    measured = adapter.measure_work_unit(unit, context, menu)
    adapter.max_input_tokens = measured
    # This context can hold the complete discovery request and its output, but
    # cannot reserve the larger verification response. Only candidate-only
    # relation units should be admitted at this boundary.
    adapter.max_context_tokens = measured + 8192
    assert adapter.work_unit_fits(unit, context, menu) is discovery_only
    adapter.max_context_tokens = measured + 8191
    assert not adapter.work_unit_fits(unit, context, menu)
    adapter.max_context_tokens = measured + 16384
    assert adapter.work_unit_fits(unit, context, menu)
    adapter.max_input_tokens = measured - 1
    assert not adapter.work_unit_fits(unit, context, menu)
    assert not requests
