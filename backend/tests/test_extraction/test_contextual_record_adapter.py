"""Field-only calls preserve candidate scope, verification and paid-response reuse."""

from __future__ import annotations

import copy
import json

import pytest

from app.services.extraction.evidence_identity import evidence_hash
from app.services.extraction.ontology_guided.attribute_disambiguation import (
    AttributeField,
    field_payload,
)
from app.services.extraction.ontology_guided.claim_protocol import (
    EntityDependencyView,
    EntityProposal,
)
from app.services.extraction.ontology_guided.context import assemble_record_discovery_context
from app.services.extraction.ontology_guided.contracts import GraphNode, VersionedRef
from app.services.extraction.ontology_guided.model_adapter import RecognitionModelFailure
from app.services.extraction.ontology_guided.record_discovery import RecordDiscoveryTask
from app.services.llm import local_client
from tests.test_extraction.test_record_model_adapter import setup_record
from tests.test_extraction.test_record_model_adapter import source as source  # noqa: F401


def field_for(source):
    index, unit = source["index"], source["source_unit"]

    def anchor(text):
        start = unit.text.index(text)
        return index.ir.anchor(unit.evidence_id, start, start + len(text))

    return AttributeField(
        field_id=evidence_hash([anchor("数量"), anchor("5")]), record_id=source["record_id"],
        label="数量", value="5", label_refs=(anchor("数量"),), value_refs=(anchor("5"),),
        exclusive_record=False,
    )


def setup_attribute(source, monkeypatch, *, stop_at=None, no_go=None):
    adapter, _task, original, card, storage, requests, proposal = setup_record(
        source, monkeypatch, stop_at=stop_at,
    )
    field = field_for(source)
    entities, nodes = [], []
    for item in proposal["entities"]:
        reference = VersionedRef(id="registered-" + item["local_id"], revision=1)
        mention = item["mentions"][0]["text"]
        unit = source["source_unit"]
        start = unit.text.index(mention)
        anchor = source["index"].ir.anchor(unit.evidence_id, start, start + len(mention))
        type_ref = VersionedRef(id="type-" + reference.id, revision=1)
        referent_ref = VersionedRef(id="referent-" + reference.id, revision=1)
        data = dict(
            entity_ref=reference, class_iri=item["class_iri"], grounding_kind="mention",
            root_origin=None, proposal=EntityProposal.model_validate(item), source_refs=[anchor],
            dependency_refs=[type_ref, referent_ref],
        )
        entities.append(EntityDependencyView(**data, content_hash=evidence_hash(data)))
        nodes.append(GraphNode(
            entity_id=reference.id, revision=1, class_iri=item["class_iri"],
            class_label=item["class_iri"], label=mention, grounding_kind="mention",
            evidence_refs=[anchor], referent_ref=referent_ref,
            type_decision_ref=type_ref, referent_decision_ref=referent_ref,
            dependency_refs=[type_ref, referent_ref],
        ))
    task = RecordDiscoveryTask.create(
        run_fingerprint="run", record_id=source["record_id"], schema_card_id=card.schema_card_id,
        analysis_scope_ref=card.analysis_scope_ref, dependency_hash=evidence_hash("field-context"),
        source_record_ids=[source["record_id"]], field_id=field.field_id,
    )
    context = assemble_record_discovery_context(
        task, card, source["index"], document_context=original.target.document_context,
        ontology_hash=adapter.ontology.ontology_hash, entity_dependencies=entities,
    )
    context.remaining_model_calls = original.remaining_model_calls
    context.bind_protocol_hook(original._protocol_hook)
    context.bind_model_call_hook(original.before_model_call)
    options = [{"subject_ref": entities[0].entity_ref.model_dump(mode="json"),
                "class_iri": "urn:T", "predicate_iri": "urn:value"}]
    context.tool_inputs = {
        **original.tool_inputs, "target_seed": context.target.model_dump(mode="json"),
        "entity_dependencies": [item.model_dump(mode="json") for item in entities],
        "entity_nodes": [node.model_dump(mode="json") for node in nodes],
        "attribute_disambiguation": {
            **field_payload(field), "options": options, "reason_code": no_go,
            "signature": evidence_hash([field_payload(field), options]),
        },
    }
    property_proposal = copy.deepcopy(proposal["properties"][0])
    property_proposal["subject_id"] = entities[0].entity_ref.id
    proposal.update(entities=[], properties=[property_proposal], relations=[],
                    reference_bindings=[], external_links=[], observations=[])
    return adapter, task, context, card, storage, requests, proposal


def test_attribute_compares_only_authorized_pairs_without_tools_and_requires_verification(
    source, monkeypatch,
):
    adapter, task, context, card, storage, requests, _ = setup_attribute(source, monkeypatch)
    outcome = adapter.inspect_record(task, context, card)

    assert outcome.complete and not outcome.nodes
    assert len(outcome.properties) == 1
    assert outcome.properties[0].subject_ref.id == "registered-b"
    assert outcome.properties[0].raw_value == "5"
    assert outcome.properties[0].value_evidence_refs == list(field_for(source).value_refs)
    assert len(requests) == outcome.model_calls == len(storage["reservations"]) == 2
    for request in requests:
        assert not request.get("tools")
    discovery = json.loads(requests[0]["input_items"][0]["content"][0]["text"])
    assert discovery["task"]["purpose"] == "property_disambiguation"
    assert discovery["attribute_disambiguation"]["options"] == [
        {"subject_ref": {"id": "registered-b", "revision": 1},
         "class_iri": "urn:T", "predicate_iri": "urn:value"},
    ]
    schema = requests[0]["text_format"]["schema"]
    assert schema["properties"]["properties"]["maxItems"] == 1
    assert schema["$defs"]["PropertyProposal"]["properties"]["subject_id"]["enum"] == [
        "registered-b",
    ]
    for name in ("entities", "relations", "external_links", "reference_bindings"):
        assert schema["properties"][name]["maxItems"] == 0
    verification = json.loads(requests[1]["input_items"][0]["content"][0]["text"])
    targets = verification["verification_input"]["targets"]
    assert len(targets) == 1 and targets[0]["target_kind"] == "property"
    assert "subject_binding" in targets[0]["required_facets"]


@pytest.mark.parametrize("violation", ["subject", "value_span", "new_entity", "second_field"])
def test_attribute_rejects_other_subjects_expanded_values_and_additional_claims(
    source, monkeypatch, violation,
):
    adapter, task, context, card, storage, requests, proposal = setup_attribute(source, monkeypatch)
    if violation == "subject":
        proposal["properties"][0]["subject_id"] = "registered-c"
    elif violation == "value_span":
        proposal["properties"][0]["value_quote"] = source["quote"]("5 mg")
    elif violation == "new_entity":
        proposal["entities"] = copy.deepcopy(source["proposal"]["entities"])
    else:
        proposal["properties"].append({**proposal["properties"][0], "local_id": "extra-field"})
    with pytest.raises(RecognitionModelFailure, match="attribute_answer_outside_scope"):
        adapter.inspect_record(task, context, card)
    assert len(requests) == len(storage["reservations"]) == 1
    assert storage["protocol"]["discovery_ref"] is None
    assert storage["protocol"]["verification_ref"] is None


def test_ambiguous_field_stays_unresolved_without_a_paid_empty_verification(source, monkeypatch):
    adapter, task, context, card, storage, requests, proposal = setup_attribute(source, monkeypatch)
    proposal.update(properties=[], observations=[{
        "subject_id": None, "predicate_iri": "urn:value", "quote": source["quote"]("5"),
        "kind": "ambiguous", "reason": "原文不能排除另一主体解释。",
    }])
    outcome = adapter.inspect_record(task, context, card)
    assert not outcome.complete and outcome.reason_code == "attribute_ambiguous"
    assert not outcome.nodes and not outcome.properties
    assert len(requests) == outcome.model_calls == 1
    assert storage["protocol"]["outcome_ref"]
    assert storage["protocol"]["verification_ref"]


@pytest.mark.parametrize("verdict", ["unsupported", "undetermined"])
def test_single_candidate_with_unsupported_binding_does_not_become_a_property(
    source, monkeypatch, verdict,
):
    adapter, task, context, card, _, requests, _ = setup_attribute(source, monkeypatch)
    transport = local_client.responses_create

    def unsupported_binding(client, **kwargs):
        response = transport(client, **kwargs)
        view = json.loads(kwargs["input_items"][0]["content"][0]["text"])
        if view["stage"] == "verification":
            answer = json.loads(response.output_items[0]["content"][0]["text"])
            facet = next(item for item in answer["verifications"][0]["facets"]
                         if item["name"] == "subject_binding")
            facet.update(verdict=verdict, support=[], reason="归属被反证或证据不足。")
            response.output_items[0]["content"][0]["text"] = json.dumps(answer)
        return response

    monkeypatch.setattr(local_client, "responses_create", unsupported_binding)
    outcome = adapter.inspect_record(task, context, card)
    assert not outcome.complete and outcome.reason_code == "attribute_not_supported"
    assert outcome.semantic_outcome == verdict
    assert not outcome.properties and not outcome.nodes
    assert len(requests) == 2


@pytest.mark.parametrize("reason", [
    "attribute_value_missing", "ontology_property_missing", "attribute_subject_missing",
    "attribute_candidate_capacity",
])
def test_no_go_field_never_reserves_or_calls_a_model(source, monkeypatch, reason):
    adapter, task, context, card, storage, requests, _ = setup_attribute(
        source, monkeypatch, no_go=reason,
    )
    outcome = adapter.inspect_record(task, context, card)
    assert not outcome.complete and outcome.reason_code == reason
    assert not outcome.properties and not outcome.nodes
    assert outcome.model_calls == 0 and not requests and not storage["reservations"]
    assert storage["protocol"]["outcome_ref"]


@pytest.mark.parametrize("stop_at", ["model_turn", "discovery", "verification"])
def test_field_cold_resume_reuses_paid_results_and_keeps_total_budget(source, monkeypatch, stop_at):
    adapter, task, context, card, saved, paid, _ = setup_attribute(
        source, monkeypatch, stop_at=stop_at,
    )
    with pytest.raises(RuntimeError, match="pause after durable commit"):
        adapter.inspect_record(task, context, card)
    saved, paid_count = copy.deepcopy(saved), len(paid)
    adapter, task, context, card, storage, resumed, _ = setup_attribute(source, monkeypatch)
    storage.update(copy.deepcopy(saved))
    context.protocol_state, context.protocol_results = saved["protocol"], saved["results"]
    context.remaining_model_calls = 6 - paid_count
    outcome = adapter.inspect_record(task, context, card)
    assert outcome.complete and len(outcome.properties) == 1 and not outcome.nodes
    assert paid_count + len(resumed) == len(storage["reservations"]) == 2
    assert storage["protocol"]["request_attempt"] == 2
    assert not any(result["field"] == "tool_result" for result in storage["results"].values())


def test_mixed_record_keeps_entities_and_other_properties_while_deferring_one_field(
    source, monkeypatch,
):
    adapter, task, context, card, storage, requests, _ = setup_record(source, monkeypatch)
    context.tool_inputs["deferred_property_fields"] = [field_payload(field_for(source))]
    outcome = adapter.inspect_record(task, context, card)
    assert {node.class_iri for node in outcome.nodes} == {"urn:T", "urn:Other"}
    assert [prop.raw_value for prop in outcome.properties] == ["样品丙"]
    frozen = next(result["value"] for result in storage["results"].values()
                  if result["field"] == "discovery")
    assert "attribute_deferred_to_disambiguation" in frozen["claim_issues"]["pb"]
    verified = json.loads(requests[-1]["input_items"][0]["content"][0]["text"])
    assert len(verified["verification_input"]["targets"]) == 3
