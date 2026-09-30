"""Paid discovery is visible while graph acceptance and source authenticity remain separate."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.services.document_analysis.discovery_projection import project_saved_discovery
from tests.test_extraction.test_record_model_adapter import setup_record
from tests.test_extraction.test_record_model_adapter import source as source

pytest_plugins = ["tests.test_extraction.test_record_model_adapter"]


def test_saved_candidates_show_before_verification_without_creating_facts(source, monkeypatch):
    adapter, task, context, card, saved, _, _ = setup_record(
        source, monkeypatch, stop_at="discovery",
    )
    with pytest.raises(RuntimeError, match="pause after durable commit"):
        adapter.inspect_record(task, context, card)
    graph = SimpleNamespace(entities=[], properties=[], relationships=[], relationship_groups=[])
    summary, selections = project_saved_discovery(
        run_id="run-a", results=saved["results"], requests={}, index=source["index"], graph=graph,
    )
    assert summary.completed_calls == 1 and summary.candidate_count == 4
    assert all(item.state == "pending" for item in summary.items)
    assert graph.entities == []
    assert selections and all(s.selection_ref in selections for item in summary.items
                              for s in item.sources)
    for selection in selections.values():
        from app.schemas.evidence import EvidenceAnchor

        assert source["index"].ir.resolve(EvidenceAnchor.model_validate(selection["anchors"][0]))
    _, other_selections = project_saved_discovery(
        run_id="run-b", results=saved["results"], requests={}, index=source["index"], graph=graph,
    )
    assert not set(selections) & set(other_selections)


def test_source_failures_have_no_links_and_each_rejection_keeps_its_own_reason(source, monkeypatch):
    def transform(answer, view, ordinal):
        if view["stage"] == "discovery":
            answer["properties"][0]["value_quote"]["text"] = "虚构原值"
        else:
            for row in answer["verifications"]:
                target = next(t for t in view["verification_input"]["targets"]
                              if t["target_id"] == row["target_id"])
                if target["payload"]["local_id"] == "b":
                    for facet in row["facets"]:
                        if facet["name"] == "type":
                            facet.update(verdict="undetermined", reason="原文未证明对象层级")

    adapter, task, context, card, saved, _, _ = setup_record(source, monkeypatch,
                                                           transform=transform)
    adapter.inspect_record(task, context, card)
    graph = SimpleNamespace(entities=[], properties=[], relationships=[], relationship_groups=[])
    summary, registry = project_saved_discovery(
        run_id="run-a", results=saved["results"], requests={}, index=source["index"], graph=graph,
    )
    fabricated = next(i for i in summary.items if i.label == "虚构原值")
    assert fabricated.state == "rejected" and fabricated.reasons
    assert all(s.selection_ref is None for s in fabricated.sources)
    entity = next(i for i in summary.items if i.kind == "entity" and i.label == "对象乙")
    assert entity.state == "pending"
    assert any("原文未证明对象层级" in reason for reason in entity.reasons)
    assert all(s.selection_ref in registry for s in entity.sources)
    assert not any(i.state == "accepted" for i in summary.items)


def test_truncated_paid_answer_remains_a_failure_not_candidate_growth():
    result = {"lineage_id": "lineage", "field": "model_turn", "value": {
        "attempt": 1, "stage": "discovery", "response_status": "completed", "error": None,
        "output_items": [{"content": [{"type": "output_text", "text": '{"entities":['}]}],
    }}
    graph = SimpleNamespace(entities=[], properties=[], relationships=[], relationship_groups=[])
    summary, registry = project_saved_discovery(
        run_id="run", results={"turn": result}, requests={}, index=None, graph=graph,
    )
    assert summary.completed_calls == 1 and summary.candidate_count == 0
    assert summary.items[0].state == "failed" and not registry


def test_registered_mention_uses_exact_resolution_and_not_unrelated_outcome_reason(
    source, monkeypatch,
):
    from app.schemas.document_analysis import GraphEntity

    adapter, task, context, card, saved, _, _ = setup_record(source, monkeypatch)
    outcome = adapter.inspect_record(task, context, card)
    node = outcome.nodes[0]
    entity = GraphEntity(
        entity_id=node.entity_id, revision=node.revision, class_iri=node.class_iri,
        class_label=node.class_label, label=node.label,
        source_selection_refs=["source"], grounding_kind=node.grounding_kind,
        type_decision_ref=node.type_decision_ref.model_dump(),
        referent_decision_ref=node.referent_decision_ref.model_dump(),
    )
    graph = SimpleNamespace(entities=[entity], invalidated_refs=[])
    summary, _ = project_saved_discovery(
        run_id="run", results=saved["results"], requests={}, index=source["index"], graph=graph,
    )
    item = next(i for i in summary.items if i.kind == "entity" and i.label == node.label)
    assert item.state == "accepted"
    # A stale claim version cannot borrow acceptance from a current same-name entity.
    stale = deepcopy(saved["results"])
    for row in stale.values():
        if row["field"] == "outcome":
            for resolution in row["value"]["reference_resolutions"]:
                resolution["claim_ref"]["revision"] += 1
            row["value"].update(complete=False, reason="其他属性失败")
    summary, _ = project_saved_discovery(
        run_id="run", results=stale, requests={}, index=source["index"], graph=graph,
    )
    item = next(i for i in summary.items if i.kind == "entity" and i.label == node.label)
    assert item.state == "pending" and "其他属性失败" not in item.reasons
    graph.invalidated_refs = [SimpleNamespace(id=entity.entity_id, revision=entity.revision)]
    summary, _ = project_saved_discovery(
        run_id="run", results=saved["results"], requests={}, index=source["index"], graph=graph,
    )
    assert not any(i.state == "accepted" for i in summary.items)


def test_saved_source_selection_works_before_first_graph_cache(source, monkeypatch):
    from app.services.document_analysis.application import DocumentAnalysisApplication

    adapter, task, context, card, saved, _, _ = setup_record(
        source, monkeypatch, stop_at="discovery",
    )
    with pytest.raises(RuntimeError, match="pause after durable commit"):
        adapter.inspect_record(task, context, card)
    summary, registry = project_saved_discovery(
        run_id="run", results=saved["results"], requests={}, index=source["index"],
        graph=SimpleNamespace(entities=[]),
    )
    application = object.__new__(DocumentAnalysisApplication)
    application._artifact_payload = lambda run, kind: (
        None if kind == "source_selections"
        else ({"analysis": source["index"].ir.model_dump(mode="json")}, "ready")
    )
    application.saved_discovery_response = lambda run: (summary, registry)
    run = SimpleNamespace(recognition_run_id="run")
    selected = application.source_selection_response(run, selection_ref=next(iter(registry)))
    assert selected["recognition_run_id"] == "run"
    assert selected["analysis_id"] == source["index"].ir.analysis_id
    assert selected["document_hash"] == source["index"].ir.document_hash
    assert selected["structure_hash"] == source["index"].ir.structure_hash
    assert selected["anchors"]
