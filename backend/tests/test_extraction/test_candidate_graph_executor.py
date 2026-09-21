"""Document candidate graphs explore entities without incoming relation proofs."""

import pytest

from app.services.document_analysis import current_state
from app.services.extraction.ontology_guided.record_discovery import RecordDiscoveryPolicy
from tests.test_extraction.test_record_executor import EDGE, ROOT_EDGE, A, B, record_setup
from tests.test_extraction.test_record_reference_pipeline import (
    DEVICE,
    reference_setup,
)

pytest_plugins = ["tests.test_extraction.test_tool_engine_resume"]


@pytest.mark.parametrize("root_candidate", [False, True])
def test_candidate_phase_explores_orphans_without_relation_or_attribute_verification(
    tmp_path, monkeypatch, current_run, root_candidate,
):
    def transform(view, payload, *, record):
        if record and view["stage"] == "discovery":
            payload["properties"] = []
        if (not record and view["stage"] == "discovery"
                and view["predicate_iri"] == ROOT_EDGE and not root_candidate):
            payload["relations"] = []
        return payload

    args, factory, requests, hooks = record_setup(
        tmp_path, monkeypatch, current_run, transform=transform,
    )
    runner = factory()
    runner.adapter.record_discovery = runner.adapter.record_discovery.model_copy(
        update={"graph_phase": "candidate_graph"},
    )
    # The coordinator reads its frozen policy at construction, as the service does.
    runner = factory()
    result = runner.run(**args, **hooks)
    assert {node.class_iri for node in result.graph.nodes} >= {A, B}, result.diagnostics
    assert not result.graph.properties and not result.graph.attribute_candidates
    assert not any(member.get("predicate_kind") == "property"
                   for request in requests for member in request.get("members", []))
    assert any(request["stage"] == "verification" and "members" not in request
               for request in requests), "Entity identity/type checks still run."
    relation_calls = [request for request in requests if "members" in request]
    assert relation_calls and all(request["stage"] == "discovery" for request in relation_calls)
    assert any(member["predicate_iri"] == EDGE
               for request in relation_calls for member in request["members"])
    assert any(edge.predicate_iri == EDGE for edge in result.graph.edges), result.diagnostics
    assert any(edge.predicate_iri == ROOT_EDGE for edge in result.graph.edges) is root_candidate
    for edge in result.graph.edges:
        assert edge.decision_status == "not_checked"
        assert edge.proof_ref is None and not edge.decision_refs
        assert not edge.model_supported and not edge.policy_eligible
        assert edge.evidence_refs
        assert all(args["ir"].resolve(ref).strip() for ref in edge.evidence_refs)
    store, run, _token = current_run
    before = len(requests)
    factory().run(
        **args, **hooks,
        resume_state=vars(current_state.restore_work(store, run, run.run_fingerprint)),
        model_call_state=current_state.restore_calls(store, run, run.run_fingerprint),
    )
    assert len(requests) == before


def test_candidate_graph_keeps_entity_reference_binding_and_source_links(
    tmp_path, monkeypatch, current_run,
):
    args, factory, requests, hooks = reference_setup(tmp_path, monkeypatch, current_run)
    factory().adapter.record_discovery = RecordDiscoveryPolicy(graph_phase="candidate_graph")
    result = factory().run(**args, **hooks)
    assert len([node for node in result.graph.nodes if node.class_iri == DEVICE]) == 1
    store, run, _ = current_run
    restored = vars(current_state.restore_work(store, run, run.run_fingerprint))
    assert any(item["value"].get("binding_origin")
               for item in restored["work_state"]["reference_resolutions"].values())
    relations = [member for request in requests for member in request.get("members", [])]
    assert any(member["reference_dependencies"] for member in relations)
    assert all(request["stage"] == "discovery" for request in requests if "members" in request)
    assert result.graph.edges
    for edge in result.graph.edges:
        assert edge.decision_status == "not_checked" and edge.proof_ref is None
        assert edge.evidence_refs
        assert all(args["ir"].resolve(ref).strip() for ref in edge.evidence_refs)
