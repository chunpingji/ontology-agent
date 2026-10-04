"""Ontology-scoped discovery keeps attributes without manufacturing their subjects."""

from copy import deepcopy

import pytest
from docx import Document
from rdflib import Graph
from test_controller import inputs as inputs  # noqa: F401

from app.services.document_harness.controller import Engine
from app.services.document_harness.ontology import catalog_from_graph
from app.services.document_harness.protocols import Discovery, stage_schema, validate_paid_output
from app.services.document_harness.ranking import reading_guidance
from app.services.document_harness.reading import decode_local_discovery
from app.services.document_harness.source import build_windows
from app.services.extraction.word_analysis import analyze_word_core


@pytest.fixture
def source(tmp_path):
    doc = Document()
    for value in ["药物Alpha", "是否存在细胞毒性：否", "编号：A7", "发生了一次清洗。"]:
        doc.add_paragraph(value)
    path = tmp_path / "source.docx"
    doc.save(path)
    ir = analyze_word_core(path).ir
    return ir, build_windows(ir)[0]


def answer(window, text, *, name=None, cls="urn:scope:Drug", fields=True):
    source = next(s for s in window.sources if text in s["text"])
    quote = {"source_id": source["source_id"], "text": text}
    return {
        "entities": [{"local_id": "a", "candidate_class_iri": cls,
                      "anchor": quote, "name": {**quote, "text": name,
                                               "occurrence": 1 if name == "否" else None}
                      if name else None,
                      "role": "原文对象", "evidence": [source["source_id"]],
                      "field_ids": [f["alias"] for f in window.fields] if fields else [],
                      "source_fields": []}],
        "document_field_ids": [], "document_source_fields": [], "unowned_fields": [],
        "relation_hints": [], "reference_cues": [], "complete": True,
    }


def decode(source, response, classes=("urn:scope:Drug", "urn:scope:Event")):
    ir, window = source
    return decode_local_discovery(ir, window, Discovery.model_validate(response), {},
                                  {"id": "document", "field_ids": []}, class_iris=classes)


@pytest.mark.parametrize("name", [None, "是否存在细胞毒性：否", "是否存在细胞毒性", "否"])
@pytest.mark.parametrize("assigned", [False, True])
def test_boolean_attribute_cannot_create_named_or_anonymous_subject(source, name, assigned):
    response = answer(source[1], "是否存在细胞毒性：否", name=name, fields=assigned)
    delta = decode(source, response)
    assert set(delta["changes"]["entities"]) == {"document"}
    assert any(f["label"] == "是否存在细胞毒性" and f["value"] == "否"
               for f in delta["changes"]["fields"].values())
    assert delta["complete"]  # Saving this observation must not cause rereading.


def test_model_supplied_field_has_same_boundary_and_keeps_its_value(source):
    ir, window = source
    response = answer(window, "是否存在细胞毒性：否", fields=False)
    sid = response["entities"][0]["anchor"]["source_id"]
    response["entities"][0]["source_fields"] = [{
        "label": {"source_id": sid, "text": "是否存在细胞毒性"},
        "value": {"source_id": sid, "text": "否", "occurrence": 1},
    }]
    window = deepcopy(window)
    window.fields = []
    delta = decode((ir, window), response)
    assert set(delta["changes"]["entities"]) == {"document"}
    assert [f["value"] for f in delta["changes"]["fields"].values()] == ["否"]
    assert delta["complete"]


def test_name_in_another_source_cannot_turn_a_field_into_a_physical_mention(source):
    response = answer(source[1], "是否存在细胞毒性：否")
    actual_object = answer(source[1], "药物Alpha")["entities"][0]["anchor"]
    response["entities"][0]["name"] = actual_object
    delta = decode(source, response)
    assert set(delta["changes"]["entities"]) == {"document"}
    assert delta["complete"]


@pytest.mark.parametrize("text,cls", [("药物Alpha", "urn:scope:Drug"),
                                      ("A7", "urn:scope:Drug"),
                                      ("发生了一次清洗。", "urn:scope:Event")])
def test_explicit_referents_and_unnamed_events_remain_candidates(source, text, cls):
    delta = decode(source, answer(source[1], text, cls=cls))
    entity = next(e for key, e in delta["changes"]["entities"].items() if key != "document")
    assert entity["discovery_class_iris"] == [cls]
    assert entity["class_iri"] is None and entity["state"] == "candidate"
    assert delta["complete"]


@pytest.mark.parametrize("cls", [None, "urn:foreign:Drug"])
def test_unmatched_types_keep_source_and_fields_as_observations(source, cls):
    delta = decode(source, answer(source[1], "药物Alpha", cls=cls))
    assert set(delta["changes"]["entities"]) == {"document"}
    assert any(o["kind"] == "entity" and o["evidence"][0]["text"] == "药物Alpha"
               for o in delta["changes"]["observations"].values())
    assert delta["changes"]["fields"] and delta["complete"]


@pytest.mark.parametrize("valid_quote", [True, False])
def test_deferred_reference_keeps_negation_but_cannot_bypass_quote_checks(source, valid_quote):
    response = answer(source[1], "药物Alpha", cls=None)
    anchor = response["entities"][0]["anchor"]
    response["reference_cues"] = [{
        "local_subject_id": "a", "reference": {**anchor,
            "text": anchor["text"] if valid_quote else "不存在的引文"},
        "relation_label": None, "direction": "outgoing", "kind": "alias",
        "evidence": [anchor["source_id"]], "polarity": "negative", "conditions": ["条件未满足"],
    }]
    delta = decode(source, response)
    assert delta["complete"] is valid_quote
    assert not delta["changes"].get("reference_cues")
    if valid_quote:
        observation = next(o for o in delta["changes"]["observations"].values()
                           if o.get("direction") == "outgoing")
        assert observation["polarity"] == "negative"
        assert observation["conditions"] == ["条件未满足"]


@pytest.mark.parametrize("mode", [None, "draft", "refine"])
def test_paid_discovery_enforces_the_actual_type_menu(source, mode):
    window = source[1]
    schema = stage_schema("discover", class_iris=["urn:scope:Drug"],
                          source_ids=[s["source_id"] for s in window.sources],
                          field_ids=[f["alias"] for f in window.fields], discovery_mode=mode)
    response = answer(window, "药物Alpha")
    if mode == "draft":
        response.update(lookup_requests=[], lookup_refinement_required=False)
    if mode == "refine":
        response = {"replacement": response, "source_suggestions": []}
    payload = {"lookup_mode": mode}
    validate_paid_output("discover", payload, response, schema)
    mention = (response["replacement"] if mode == "refine" else response)["entities"][0]
    mention["candidate_class_iri"] = "urn:foreign:Drug"
    with pytest.raises(ValueError, match="harness_output_schema_mismatch"):
        validate_paid_output("discover", payload, response, schema)
    del mention["candidate_class_iri"]
    with pytest.raises(ValueError):
        validate_paid_output("discover", payload, response, schema)


def test_property_semantics_are_shared_with_domains_and_boolean_range():
    catalog = catalog_from_graph(Graph().parse(data='''
        @prefix : <urn:scope:> . @prefix owl: <http://www.w3.org/2002/07/owl#> .
        @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
        @prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
        :Report a owl:Class . :Drug a owl:Class . :Child a owl:Class ; rdfs:subClassOf :Drug .
        :describes a owl:ObjectProperty ; rdfs:domain :Report ; rdfs:range :Drug .
        :flag a owl:DatatypeProperty ; rdfs:domain :Drug ; rdfs:range xsd:boolean ;
              rdfs:label "是否存在细胞毒性" ; rdfs:comment "原文否定仍是属性值，不是实体。" .
    ''', format="turtle"), "urn:scope:Report")
    guidance = reading_guidance(catalog, ["urn:scope:Drug", "urn:scope:Child"])
    assert len(guidance["properties"]) == 1
    prop = guidance["properties"][0]
    assert set(prop["class_iris"]) == {"urn:scope:Drug", "urn:scope:Child"}
    assert prop["description"] == "原文否定仍是属性值，不是实体。"
    assert prop["domain"][0]["iri"] == "urn:scope:Drug"
    assert prop["datatype_iris"] == ["http://www.w3.org/2001/XMLSchema#boolean"]


def test_attribute_only_response_finishes_one_window_without_model_retry(source, inputs):
    ir, window = source
    calls = []

    def invoke(stage, payload, schema):
        calls.append(stage)
        return answer(window, "是否存在细胞毒性：否", cls="urn:test:Thing")

    engine = Engine(ir=ir, catalog=inputs[1], state={}, invoke=invoke,
                    save=lambda _: None,
                    should_stop=lambda: engine.state.get("cursor", {}).get("main", {}).get(
                        "phase") not in (None, "discovery"),
                    rank=lambda *_: {"snapshot_id": inputs[1].snapshot_id,
                                     "selected_iris": ["urn:test:Thing"]})
    engine.windows = [window]
    engine.run()
    assert calls == ["discover"]
    assert set(engine.state["entities"]) == {"document"}
    assert engine.state["cursor"]["main"]["reading"]["complete"]
