"""Identity declarations reach all consumers without name-based inference."""

import json
from pathlib import Path

from rdflib import Graph
from test_controller import Model, execute
from test_controller import inputs as inputs  # noqa: F401
from test_enumerated_mentions import discover, fixture, mention

from app.services.document_harness.ontology import catalog_from_graph
from app.services.document_harness.protocols import INSTRUCTIONS, Mention
from app.services.document_harness.ranking import rank_cards, reading_card

PREFIX = '''
@prefix : <urn:identity:> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
'''


def catalog():
    return catalog_from_graph(Graph().parse(data=PREFIX + '''
    :Report a owl:Class . :Object a owl:Class; rdfs:label "Object";
        owl:hasKey (:serial :scope), (:unknown :broken) .
    :Child a owl:Class; rdfs:subClassOf :Object . :Site a owl:Class .
    :describes a owl:ObjectProperty; rdfs:domain :Report; rdfs:range :Object .
    :serial a owl:DatatypeProperty; rdfs:domain :Object; rdfs:range xsd:string;
        rdfs:label "登记值"; rdfs:comment "Only meaningful within the associated site." .
    :scope a owl:ObjectProperty; rdfs:domain :Object; rdfs:range :Site;
        rdfs:comment "The site maintaining this register." .
    :broken a owl:DatatypeProperty; rdfs:domain :Object .
    :identifier a owl:DatatypeProperty; rdfs:domain :Object; rdfs:range xsd:string;
        rdfs:label "ID编号" .
    :mark a owl:AnnotationProperty; rdfs:comment "Scoped identity hint." .
    :opaque a owl:DatatypeProperty; rdfs:domain :Object; rdfs:range xsd:string;
        :mark true; rdfs:comment "A locally assigned string, not globally unique." .
    ''', format="turtle"), "urn:identity:Report",
        identity_property_iris=["urn:identity:opaque"])


def test_complete_inherited_key_groups_include_object_members_and_missing_components():
    cards = catalog()
    card = reading_card(cards.classes["urn:identity:Child"],
                        annotation_contracts=cards.annotation_contracts)
    assert card["identity_key_groups"] == [
        ["urn:identity:broken", "urn:identity:unknown"],
        ["urn:identity:scope", "urn:identity:serial"],
    ]
    props = {p["iri"]: p for p in card["identity_properties"]}
    assert "urn:identity:identifier" not in props  # A label is not an identity declaration.
    assert props["urn:identity:scope"]["property_kind"] == "object"
    assert props["urn:identity:scope"]["range"][0]["iri"] == "urn:identity:Site"
    assert props["urn:identity:serial"]["datatype_iris"] == [
        "http://www.w3.org/2001/XMLSchema#string",
    ]
    assert "associated site" in props["urn:identity:serial"]["description"]
    assert card["unavailable_identity_components"] == [
        "urn:identity:broken", "urn:identity:unknown",
    ]
    assert card["annotation_contracts"][0]["iri"] == "urn:identity:mark"
    assert reading_card(cards.classes["urn:identity:Site"])["identity_properties"] == []


def test_budget_accounts_for_the_actual_identity_payload_without_truncation():
    cards = catalog()
    payload = {"sources": [{"text": "Object"}], "fields": []}
    result = rank_cards(cards, payload, 100000)
    for row in result["candidates"]:
        card = reading_card(cards.classes[row["iri"]],
                            annotation_contracts=cards.annotation_contracts)
        assert row["card_bytes"] == len(json.dumps(card, ensure_ascii=False).encode()) + 2
    from app.services.document_harness.ranking import guidance_bytes

    assert result["card_bytes_used"] == guidance_bytes(cards, result["selected_iris"])


def test_entity_review_uses_actual_aligned_type_identity_metadata(inputs):
    ir, cards = inputs
    thing = cards.classes["urn:test:Thing"]
    prop = thing.properties[0].model_copy(update={"identity_key": True})
    cards = cards.model_copy(update={"classes": {
        **cards.classes, thing.iri: thing.model_copy(update={"properties": (prop,)}),
    }})
    model = Model()
    execute((ir, cards), model)
    rows = [row for stage, payload, _ in model.calls if stage == "entity_review"
            for row in payload["candidates"]]
    assert rows and all(row["class_definition"]["iri"] == thing.iri for row in rows)
    assert all(row["class_definition"]["identity_properties"][0]["iri"] == prop.iri
               for row in rows)


def test_shared_sentence_does_not_programmatically_prove_identifier_ownership(tmp_path):
    ir, cards, window = fixture(tmp_path, "611/642/646车间")
    item = mention("611")
    item["source_fields"] = [{"label": None, "value": {"source_id": "S1", "text": "642"}}]
    engine = discover(ir, cards, window, [item])
    entity = next(e for e in engine.state["entities"].values() if e["id"] != "document")
    assert entity["state"] == "candidate"
    assert engine.state["fields"][entity["field_ids"][0]]["value"] == "642"
    assert not engine.state.get("properties")  # Locatable does not mean accepted ownership.
    entity.update(class_iri="urn:enumeration:Area", class_label="Area")
    def accept_local_referent(stage, payload, schema):
        assert stage == "entity_review"
        return {"judgments": {row["id"]: {
            "verdict": "accepted", "confidence": 0.99, "evidence": ["S1"],
            "reason": "Local referent is supported; identifier ownership needs attribute review.",
        } for row in payload["candidates"]}}
    engine.invoke = accept_local_referent
    engine.should_stop = lambda: False
    engine.state["cursor"]["main"]["skeleton_window_id"] = window.id
    engine.entity_review(window)
    assert engine.state["entities"][entity["id"]]["state"] == "accepted"
    assert not engine.state.get("properties")


def test_no_previous_workshop_special_cases_in_runtime_contract():
    text = json.dumps(INSTRUCTIONS, ensure_ascii=False) + json.dumps(Mention.model_json_schema())
    assert not any(word in text for word in ("611", "642", "646", "车间", "name_parts"))
    runtime = Path(__file__).resolve().parents[2] / "app/services/document_harness"
    assert not any("ProductionArea" in path.read_text() for path in runtime.glob("*.py"))
    assert not any("name_parts" in path.read_text() for path in runtime.glob("*.py"))
