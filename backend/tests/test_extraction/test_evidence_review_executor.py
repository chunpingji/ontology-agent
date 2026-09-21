"""Source review traverses registered entities independently of parent acceptance."""

import pytest

from app.services.document_analysis import current_state
from tests.test_extraction.test_record_executor import (
    EDGE,
    ROOT_EDGE,
    VALUE,
    A,
    B,
    record_setup,
)

pytest_plugins = ["tests.test_extraction.test_tool_engine_resume"]


def test_joint_record_relations_survive_execution_projection_and_cold_resume(
    tmp_path, monkeypatch, current_run,
):
    from tests.test_extraction.test_record_executor import TEXT

    def transform(view, payload, *, record):
        if record and view["stage"] == "discovery" and len(payload["entities"]) == 2:
            evidence_id = view["evidence_units"][0]["evidence_id"]

            def quote(text):
                return dict(evidence_id=evidence_id, text=text, context_text=None)

            payload["relations"] = [dict(
                local_id="joint-link", subject_id="a", predicate_iri=EDGE, object_ids=["b"],
                selection="all", bridge_support=[quote(TEXT)], selection_support=[],
                qualifiers=dict(polarity="affirmed", modality="asserted",
                                condition_support=[], scope_qualifiers=[]),
                bridge_kind="explicit_assertion", bridge_ref_ids=[],
                source_assertion=dict(
                    subject_support=[quote("装置甲")], predicate_support=[quote(TEXT)],
                    object_support=[dict(object_id="b", support=[quote("部件乙")])], binding_ids=[],
                ),
            )]
        return payload

    args, factory, requests, hooks = record_setup(
        tmp_path, monkeypatch, current_run, transform=transform,
    )
    factory().adapter.record_discovery = factory().adapter.record_discovery.model_copy(
        update={"graph_phase": "evidence_review"},
    )
    result = factory().run(**args, **hooks)
    assert any(edge.predicate_iri == EDGE and edge.policy_eligible for edge in result.graph.edges)
    record_verification = [v for v in requests if "members" not in v
                           and v["stage"] == "verification"]
    assert any({t["target_kind"] for t in v["verification_input"]["targets"]}
               >= {"entity", "property", "relation"} for v in record_verification)
    members = [member for v in requests for member in v.get("members", [])]
    assert any(m.get("endpoint_attributes") for m in members)
    assert all(attr["value_quote"]["text"] in {"A", "B"} for m in members
               for attr in m.get("endpoint_attributes", []))
    store, run, _ = current_run
    before = len(requests)
    resumed = factory().run(
        **args, **hooks,
        resume_state=vars(current_state.restore_work(store, run, run.run_fingerprint)),
        model_call_state=current_state.restore_calls(store, run, run.run_fingerprint),
    )
    assert len(requests) == before
    assert resumed.graph.edges == result.graph.edges


@pytest.mark.parametrize("root_status", ["unsupported", "undetermined"])
def test_review_keeps_parent_reason_and_explores_descendants_and_attributes(
    tmp_path, monkeypatch, current_run, root_status,
):
    def transform(view, payload, *, record):
        if record and view["stage"] == "discovery":
            payload["properties"] = []
        if (not record and view["stage"] == "verification"
                and view["predicate_iri"] == ROOT_EDGE):
            for target in payload["verifications"]:
                for facet in target["facets"]:
                    facet.update(verdict=root_status, reason="原文不足以支持该报告归属关系")
        return payload

    args, factory, requests, hooks = record_setup(
        tmp_path, monkeypatch, current_run, transform=transform,
    )
    factory().adapter.record_discovery = factory().adapter.record_discovery.model_copy(
        update={"graph_phase": "evidence_review"},
    )
    result = factory().run(**args, **hooks)
    assert {node.class_iri for node in result.graph.nodes} >= {A, B}, result.diagnostics
    parent = [edge for edge in result.graph.edges if edge.predicate_iri == ROOT_EDGE]
    assert parent, result.diagnostics
    assert all(edge.decision_status == root_status and edge.reason_code and edge.reason
               and not edge.policy_eligible for edge in parent)
    assert all(edge.evidence_refs for edge in parent)
    assert any(edge.predicate_iri == EDGE and edge.decision_status == "supported"
               for edge in result.graph.edges), result.diagnostics
    assert {prop.raw_value for prop in result.graph.properties
            if prop.decision_status == "supported"} == {"A", "B"}, result.diagnostics
    local_requests = [view for view in requests if "members" in view]
    assert any(view["stage"] == "verification" and any(
        member["predicate_iri"] == VALUE for member in view["members"]
    ) for view in local_requests)
    assert not any(view.get("purpose") == "property_disambiguation" for view in requests)
    store, run, _ = current_run
    before = len(requests)
    resumed = factory().run(
        **args, **hooks,
        resume_state=vars(current_state.restore_work(store, run, run.run_fingerprint)),
        model_call_state=current_state.restore_calls(store, run, run.run_fingerprint),
    )
    assert len(requests) == before
    assert [(edge.candidate_id, edge.decision_status, edge.reason_code)
            for edge in resumed.graph.edges] == [
                (edge.candidate_id, edge.decision_status, edge.reason_code)
                for edge in result.graph.edges
            ]


def test_rejected_property_keeps_source_and_reason_through_public_snapshot(
    tmp_path, monkeypatch, current_run,
):
    def transform(view, payload, *, record):
        if record and view["stage"] == "discovery":
            payload["properties"] = []
        if (not record and view["stage"] == "verification"
                and view["predicate_iri"] == VALUE):
            for target in payload["verifications"]:
                for facet in target["facets"]:
                    facet.update(verdict="unsupported", reason="该字段未被证明属于当前主体")
        return payload

    args, factory, _requests, hooks = record_setup(
        tmp_path, monkeypatch, current_run, transform=transform,
    )
    factory().adapter.record_discovery = factory().adapter.record_discovery.model_copy(
        update={"graph_phase": "evidence_review"},
    )
    result = factory().run(**args, **hooks)
    assert {prop.raw_value for prop in result.graph.properties} == {"A", "B"}, result.diagnostics
    for prop in result.graph.properties:
        assert prop.decision_status == "unsupported"
        assert not prop.policy_eligible
        assert prop.reason_code and prop.reason
        assert prop.value_evidence_refs
        assert all(args["ir"].resolve(ref).strip() for ref in prop.value_evidence_refs)


def test_pending_review_resumes_verification_without_repeating_discovery(
    tmp_path, monkeypatch, current_run,
):
    from app.services.extraction.ontology_guided.batch_model_adapter import BatchRecognitionRun
    from app.services.llm.local_client import StructuredModelError

    def transform(view, payload, *, record):
        if record and view["stage"] == "discovery":
            payload["properties"] = []
        return payload

    args, factory, requests, hooks = record_setup(
        tmp_path, monkeypatch, current_run, transform=transform,
    )
    factory().adapter.record_discovery = factory().adapter.record_discovery.model_copy(
        update={"graph_phase": "evidence_review"},
    )
    original = BatchRecognitionRun.run_group
    unavailable = True

    def run_group(batch):
        if unavailable and batch.context.protocol_state["stage"] == "verification":
            raise StructuredModelError("review_service_unavailable")
        return original(batch)

    monkeypatch.setattr(BatchRecognitionRun, "run_group", run_group)
    paused = factory().run(**args, **hooks)
    pending = [*paused.graph.edges, *paused.graph.properties]
    assert pending, paused.diagnostics
    assert all(item.decision_status == "not_checked" and item.proof_ref is None
               and item.reason_code == "review_service_unavailable" for item in pending)
    assert paused.graph.progress.stop_reason == "evidence_review_pending"
    paid_discoveries = [view for view in requests if "members" in view
                       and view["stage"] == "discovery"]
    assert paid_discoveries
    task_ids = {member["task_id"] for view in paid_discoveries for member in view["members"]}
    store, run, _ = current_run
    saved = vars(current_state.restore_work(store, run, run.run_fingerprint))
    assert saved["work_state"]["control"]["current"]["active_unit_ref"]
    before = len(requests)
    unavailable = False
    resumed = factory().run(
        **args, **hooks, resume_state=saved,
        model_call_state=current_state.restore_calls(store, run, run.run_fingerprint),
    )
    assert not any(task_ids.intersection(member["task_id"] for member in view["members"])
                   for view in requests[before:] if "members" in view
                   and view["stage"] == "discovery")
    results = {item.candidate_id: item
               for item in [*resumed.graph.edges, *resumed.graph.properties]}
    for item in pending:
        accepted = results[item.candidate_id]
        assert accepted.revision > item.revision
        assert accepted.decision_status == "supported" and accepted.proof_ref
    assert not any("revision_conflict" in reason for reason in resumed.diagnostics)
