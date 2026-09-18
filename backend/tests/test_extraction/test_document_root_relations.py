"""Anchorless document roots use document-description proposals and bounded repair."""

import copy
import json

import pytest

from app.services.extraction.evidence_identity import evidence_hash
from app.services.extraction.ontology_guided.claim_freeze import freeze_proposal
from app.services.extraction.ontology_guided.claim_protocol import (
    DiscoveryEnvelope,
    EntityDependencyView,
    EntityProposal,
    compile_stage_schema,
)
from app.services.extraction.ontology_guided.source_assertions import relation_bridge_options
from app.services.llm import local_client
from tests.test_extraction.test_relation_source_assertions import evaluate, relation_case
from tests.test_extraction.test_tool_engine_adapter import setup_adapter
from tests.test_extraction.test_tool_engine_freeze import _root_dependency

pytest_plugins = ["tests.test_extraction.test_tool_engine_freeze"]


@pytest.fixture
def document_source(source):
    source["options"]["context"].subject_evidence_refs = []
    source["proposal"]["reference_bindings"] = []
    relation = source["proposal"]["relations"][0]
    relation["bridge_kind"] = "document_subject_description"
    relation["source_assertion"] = {
        "subject_support": [],
        "object_support": [{"object_id": entity["local_id"], "support": entity["mentions"]}
                           for entity in source["proposal"]["entities"]],
        "predicate_support": [source["quote"](source["source_unit"].text)], "binding_ids": [],
    }
    return source


def document_adapter(source, monkeypatch, **options):
    values = setup_adapter(source, monkeypatch, **options)
    values[0].reference_resolution = True
    return values


def view(request):
    return json.loads(request["input_items"][0]["content"][0]["text"])


def test_anchorless_document_relation_passes_full_harness_with_independent_checks(
    document_source, monkeypatch,
):
    adapter, task, context, predicate, menu, stored, requests = document_adapter(
        document_source, monkeypatch,
    )
    outcome = adapter.inspect(task, context, predicate, menu)
    assert outcome.complete and len(outcome.relationship_groups) == 1
    assert len(requests) == len(stored["reservations"]) == 3
    assert stored["protocol"]["tool_calls_used"] == 1
    assert not stored["protocol"]["recovery_used"]
    relation = requests[0]["text_format"]["schema"]["$defs"]["RelationProposal"]
    assert relation["properties"]["bridge_kind"]["enum"] == ["document_subject_description"]
    assert relation["properties"]["subject_id"]["enum"] == [task.subject.entity_id]
    root = view(requests[0])["registered_entities"][0]
    assert root["source_refs"] == [] and root["grounding_kind"] == "document_root"
    target = next(t for t in view(requests[-1])["verification_input"]["targets"]
                  if t["target_kind"] == "relation")
    assert target["payload"]["source_assertion"]["subject_support"] == []
    assert {"subject_binding", "object_binding", "predicate"} <= set(target["required_facets"])
    assert outcome.relationship_groups[0].subject_ref.id == task.subject.entity_id


@pytest.mark.parametrize("budget,correct", [(4, True), (4, False), (3, True)])
def test_wrong_root_bridge_is_corrected_before_verification_without_overwriting_original(
    document_source, monkeypatch, budget, correct,
):
    original = document_source["proposal"]["relations"][0]
    original["bridge_kind"] = "explicit_assertion"
    original["source_assertion"]["subject_support"] = [
        document_source["quote"](document_source["source_unit"].text),
    ]
    adapter, task, context, predicate, menu, stored, requests = document_adapter(
        document_source, monkeypatch, budget=budget,
    )
    transport = local_client.responses_create

    def respond(client, **kwargs):
        turn = transport(client, **kwargs)
        if correct and len(requests) > 1 and view(kwargs)["stage"] == "discovery":
            answer = copy.deepcopy(document_source["proposal"])
            answer["relations"][0]["bridge_kind"] = "document_subject_description"
            answer["relations"][0]["source_assertion"]["subject_support"] = []
            turn.output_items[0]["content"][0]["text"] = json.dumps(answer)
        return turn

    monkeypatch.setattr(local_client, "responses_create", respond)
    outcome = adapter.inspect(task, context, predicate, menu)
    discoveries = sorted((r["value"] for r in stored["results"].values()
                          if r["field"] == "discovery"), key=lambda r: r["assertion_generation"])
    assert discoveries[0]["relations"][0]["bridge_kind"] == "explicit_assertion"
    assert discoveries[0]["claim_issues"]["r"] == ["document_subject_description_required"]
    assert document_source["proposal"]["relations"][0]["bridge_kind"] == "explicit_assertion"
    if budget == 4 and correct:
        assert outcome.complete and len(outcome.relationship_groups) == 1
        assert len(requests) == 4 and len(discoveries) == 2
        assert discoveries[1]["assertion_generation"] == 2
        target = next(t for t in view(requests[-1])["verification_input"]["targets"]
                      if t["target_kind"] == "relation")
        assert target["payload"]["bridge_kind"] == "document_subject_description"
        assert target["claim_ref"]["revision"] == 2
    else:
        assert not outcome.complete and not outcome.relationship_groups
        assert "document_subject_description_required" in outcome.reason
        assert len(discoveries) == 1
        assert len(requests) == (3 if budget == 4 else 2)
    assert stored["protocol"]["recovery_used"] is (budget == 4)
    assert len(stored["reservations"]) == len(requests) <= budget
    if budget == 4:
        assert any(f["code"] == "document_subject_description_required"
                   for f in view(requests[1])["feedback"])
        assert not requests[1]["tools"]
        assert view(requests[1])["turn"]["reserved_model_calls"] == 2


@pytest.mark.parametrize("facet", ["subject_binding", "predicate", "qualifiers"])
def test_document_identity_does_not_override_unsupported_semantics(
    document_source, monkeypatch, facet,
):
    adapter, task, context, predicate, menu, _, requests = document_adapter(
        document_source, monkeypatch,
    )
    transport = local_client.responses_create

    def respond(client, **kwargs):
        turn = transport(client, **kwargs)
        if view(kwargs)["stage"] == "verification" and kwargs.get("tool_choice") != "required":
            answer = json.loads(turn.output_items[0]["content"][0]["text"])
            for target in answer["verifications"]:
                for decision in target["facets"]:
                    if decision["name"] == facet:
                        decision["verdict"] = "unsupported"
            turn.output_items[0]["content"][0]["text"] = json.dumps(answer)
        return turn

    monkeypatch.setattr(local_client, "responses_create", respond)
    outcome = adapter.inspect(task, context, predicate, menu)
    assert not outcome.edges and not outcome.relationship_groups
    assert f"{facet}_not_supported" in outcome.reason
    assert len(requests) == 3


@pytest.mark.parametrize("cold_resume", [False, True])
def test_final_source_gate_feedback_reproposes_and_independently_reverifies(
    document_source, monkeypatch, cold_resume,
):
    adapter, task, context, predicate, menu, stored, requests = document_adapter(
        document_source, monkeypatch, budget=6,
    )
    transport = local_client.responses_create
    assertion = document_source["source_unit"].text.split("。", 1)[0] + "。"

    def respond(client, **kwargs):
        turn = transport(client, **kwargs)
        if kwargs.get("tool_choice") == "required":
            return turn
        answer = json.loads(turn.output_items[0]["content"][0]["text"])
        if view(kwargs)["stage"] == "verification":
            for target in answer["verifications"]:
                for facet in target["facets"]:
                    if facet["name"] == "predicate":
                        facet["support"] = [document_source["quote"](assertion)]
        elif len(requests) > 1:
            answer["relations"][0]["source_assertion"]["predicate_support"] = [
                document_source["quote"](assertion),
            ]
        turn.output_items[0]["content"][0]["text"] = json.dumps(answer)
        return turn

    monkeypatch.setattr(local_client, "responses_create", respond)
    if cold_resume:
        checkpoint = context._protocol_hook

        def pause(value):
            checkpoint(value)
            if (value["recovery_used"] and value["stage"] == "discovery"
                    and value["stage_input_items"]):
                raise RuntimeError("pause after gate feedback")

        context.bind_protocol_hook(pause)
        with pytest.raises(RuntimeError, match="pause after gate feedback"):
            adapter.inspect(task, context, predicate, menu)
        assert len(requests) == 3
        context.protocol_state = copy.deepcopy(stored["protocol"])
        context.protocol_results = copy.deepcopy(stored["results"])
        context.remaining_model_calls = 3
        context.bind_protocol_hook(checkpoint)
        adapter = copy.copy(adapter)
    outcome = adapter.inspect(task, context, predicate, menu)
    assert outcome.complete and len(outcome.relationship_groups) == 1
    assert len(requests) == len(stored["reservations"]) == 6
    assert stored["protocol"]["assertion_generation"] == 2
    assert any(f["code"] == "source_assertion_predicate_not_reviewed"
               for f in view(requests[3])["feedback"])
    assert requests[1]["tool_choice"] == requests[4]["tool_choice"] == "required"
    verifications = [r["value"] for r in stored["results"].values() if r["field"] == "verification"]
    assert len(verifications) == 2
    assert verifications[0] != verifications[1]


def test_ordinary_entity_cannot_adopt_document_description_in_schema_or_freeze(source):
    options = source["options"]
    values = _root_dependency(options).model_dump(exclude={"content_hash"})
    values.update(grounding_kind="mention", root_origin=None, proposal=EntityProposal(
        local_id="registered-subject", class_iri=options["task"].subject.class_iri,
        representation="mention", mentions=[source["quote"]("主体甲")],
        record_components=[], identifier_claims=[],
    ))
    dependency = EntityDependencyView(**values, content_hash=evidence_hash(values))
    allowed = relation_bridge_options(dependency.entity_ref,
                                     options["context"].target.document_context.root_ref,
                                     [dependency])
    schema = compile_stage_schema("discovery", card=options["card"], evidence_ids=[], targets=[],
                                  reference_resolution=True, relation_bridges=allowed)
    assert "document_subject_description" not in (
        schema["$defs"]["RelationProposal"]["properties"]["bridge_kind"]["enum"]
    )
    proposal = copy.deepcopy(source["proposal"])
    relation = proposal["relations"][0]
    relation["bridge_kind"] = "document_subject_description"
    relation["source_assertion"] = dict(
        subject_support=[source["quote"]("主体甲")],
        object_support=[dict(object_id=e["local_id"], support=e["mentions"])
                        for e in proposal["entities"]],
        predicate_support=relation["bridge_support"], binding_ids=[],
    )
    frozen = freeze_proposal(DiscoveryEnvelope.model_validate(proposal), **options,
                             entity_dependencies=[dependency], reference_resolution=True)
    assert "document_description_subject_invalid" in frozen.claim_issues["r"]


def test_final_gate_rejects_document_bridge_on_a_grounded_ordinary_subject():
    case = relation_case(
        {"source": "设备甲包含零件乙。"}, {"p": ("source", "设备甲"), "o": ("source", "零件乙")},
        subject="p", objects=["o"], subject_quote=[("source", "设备甲")],
        object_quotes={"o": [("source", "零件乙")]},
        predicate_quotes=[("source", "设备甲包含零件乙。")],
        bridge="document_subject_description",
    )
    result = evaluate(case)
    assert "document_description_subject_invalid" in result.issues
    assert not result.steps


@pytest.mark.parametrize("tamper", ["origin", "revision"])
def test_document_bridge_requires_exact_configured_root_identity(tamper):
    case = relation_case(
        {"title": "报告", "source": "记录对象乙。"},
        {"report": ("title", "报告"), "o": ("source", "对象乙")},
        subject="report", objects=["o"], subject_quote=[],
        object_quotes={"o": [("source", "对象乙")]},
        predicate_quotes=[("source", "记录对象乙。")],
        bridge="document_subject_description", root="report",
    )
    dependency = case.verification_input.entity_dependencies[0]
    dependency.source_refs = []
    assert not evaluate(case).issues
    if tamper == "origin":
        dependency.root_origin = "recognized"
    else:
        dependency.entity_ref = dependency.entity_ref.model_copy(update={"revision": 2})
    assert "document_description_subject_invalid" in evaluate(case).issues
