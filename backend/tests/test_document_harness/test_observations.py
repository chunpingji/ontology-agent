"""Deeper referents keep observations even while their graph paths remain unproved."""

from copy import deepcopy

import jsonschema
import pytest
from docx import Document
from rdflib import Graph

from app.services.document_harness.controller import Engine
from app.services.document_harness.observations import value_components
from app.services.document_harness.ontology import catalog_from_graph
from app.services.document_harness.source import make_window
from app.services.extraction.word_analysis import analyze_word_core


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("3.8- 6.6kg", ("3.8", "6.6", "kg")),
        ("3.8–6.6 kg", ("3.8", "6.6", "kg")),
        ("-6.6 至 -3.8 ℃", ("-6.6", "-3.8", "℃")),
        ("2.0 kg to 5.0 kg", ("2.0", "5.0", "kg")),
        ("N/A", None),
        ("不少于3.8 kg", None),
        ("3.8±0.2 kg", None),
        ("6.6-3.8 kg", None),
        ("3.8 g-6.6 kg", None),
        ("3.8-6.6 kg / 2批", None),
        ("2026-06-01", None),
    ],
)
def test_range_projection_is_lossless_and_rejects_ambiguous_or_mixed_unit_values(raw, expected):
    field = {"value": raw, "missing": raw == "N/A"}
    assert value_components(field, confirmed=False) == {"whole": {"value": raw, "unit": None}}
    values = value_components(field, confirmed=True)
    assert values["whole"]["value"] == raw and field["value"] == raw
    if expected is None:
        assert set(values) == {"whole"}
    else:
        assert (
            values["lower"]["value"],
            values["upper"]["value"],
            values["lower"]["unit"],
        ) == expected


@pytest.fixture
def deep_input(tmp_path):
    doc = Document()
    for text in (
        "本文描述记录甲；记录甲描述本次安排乙。",
        "本次安排乙涉及对象丙。",
        "对象丙的负载范围为3.8- 6.6kg。",
        "补充确认：对象丙关联对象丁。",
    ):
        doc.add_paragraph(text)
    path = tmp_path / "deep.docx"
    doc.save(path)
    ir = analyze_word_core(path).ir
    graph = Graph().parse(
        data="""
        @prefix : <urn:depth:> .
        @prefix owl: <http://www.w3.org/2002/07/owl#> .
        @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
        @prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
        :Report a owl:Class . :A a owl:Class . :B a owl:Class .
        :C a owl:Class . :D a owl:Class .
        :r1 a owl:ObjectProperty ; rdfs:domain :Report ; rdfs:range :A .
        :r2 a owl:ObjectProperty ; rdfs:domain :A ; rdfs:range :B .
        :r3 a owl:ObjectProperty ; rdfs:domain :B ; rdfs:range :C .
        :r4 a owl:ObjectProperty ; rdfs:domain :C ; rdfs:range :D .
        :p a owl:DatatypeProperty ; rdfs:label "负载下限(kg)" ;
            rdfs:domain :C ; rdfs:range xsd:decimal .
        :q a owl:DatatypeProperty ; rdfs:label "负载上限(kg)" ;
            rdfs:domain :C ; rdfs:range xsd:decimal .
    """,
        format="turtle",
    )
    units = ir.evidence_units
    windows = [
        make_window(
            ir, [(u.evidence_id, 0, len(u.text)) for u in chunk], [u.evidence_id for u in chunk]
        )
        for chunk in (units[:3], units[3:])
    ]
    return ir, catalog_from_graph(graph, "urn:depth:Report"), windows


def quote(payload, text):
    source = next(s for s in payload["sources"] if text in s["text"])
    return {"source_id": source["source_id"], "text": text, "occurrence": 0}


class DeepModel:
    def __init__(self, endpoint="accepted", *, invalid_owner=False, forged_component=False):
        self.calls = []
        self.endpoint = endpoint
        self.invalid_owner = invalid_owner
        self.forged_component = forged_component

    def __call__(self, stage, payload, schema):
        self.calls.append((stage, deepcopy(payload)))
        supplemental = any("补充确认" in s["text"] for s in payload["sources"])
        if stage == "discover":
            mentions = (
                [("D", "对象丁")]
                if supplemental
                else [
                    ("A", "记录甲"),
                    ("B", "本次安排乙"),
                    ("C", "对象丙"),
                ]
            )
            response = {
                "entities": [
                    {
                        "local_id": key,
                        "name": None,
                        "anchor": {**quote(payload, text), "text": "invalid"}
                        if key == "C" and self.invalid_owner else quote(payload, text),
                        "role": key,
                        "evidence": [quote(payload, text)["source_id"]],
                        "field_ids": [],
                        "source_fields": [
                            {
                                "label": quote(payload, "负载范围"),
                                "value": quote(payload, "3.8- 6.6kg"),
                            }
                        ] if key == "C" else [],
                    }
                    for key, text in mentions
                ],
                "unowned_fields": [],
                "document_field_ids": [],
                "document_source_fields": [],
                "relation_hints": [] if supplemental else [{
                    "subject_id": subject, "object_id": obj, "label": label,
                    "evidence": [quote(payload, text)["source_id"]],
                    "polarity": "positive", "conditions": [],
                } for subject, obj, label, text in (
                    ("document", "A", "r1", "本文描述记录甲"),
                    ("A", "B", "r2", "记录甲描述本次安排乙"),
                    ("B", "C", "r3", "本次安排乙涉及对象丙"),
                )],
                "complete": True,
            }
        elif stage == "type_alignment":
            response = {
                "entities": {
                    entity["entity_id"]: {
                        "class_iri": "urn:depth:" + entity["role"],
                        "confidence": 0.96,
                        "evidence": entity["evidence"],
                        "reason": "具体指称和类型定义一致",
                    }
                    for entity in payload["entities"]
                }
            }
        elif stage == "property_alignment":
            fields = {f["field_id"]: f for f in payload["subject"]["fields"]}
            response = {
                "properties": {
                    field_id: {
                        "mappings": [
                            {
                                "predicate_iri": "urn:depth:" + pred,
                                "value_component": component, "value_quote": None,
                                "confidence": 0.96,
                            }
                            for pred, component in (("p", "lower"), ("q", "upper"))
                            if component in fields[field_id]["value_components"]
                            or self.forged_component
                        ],
                        "reason": "完整原范围映射；主体未确认时保留观察",
                    }
                    for field_id in payload["property_field_ids"]
                },
            }
        elif stage == "relation_alignment":
            response = {"proposals": {item["candidate_id"]: {
                "verdict": "proposed", "evidence": item["clue_sources"],
                "polarity": item["polarity_hint"], "conditions": item["condition_hints"],
                "participation": None, "selection": None, "timing": None,
                "missing_context": "none", "reason": "按明确原文线索提出路径", "confidence": 0.96,
            } for item in payload["items"]}}
        else:
            assert stage in {"entity_review", "evidence_review"}
            response = {
                **({"type_concerns": []} if stage == "evidence_review" else {}),
                "judgments": {
                    item["id"]: {
                        "verdict": (
                            "unresolved"
                            if item["kind"] == "relations"
                            else self.endpoint
                            if item["kind"] == "entities"
                            and item["role"] == "C"
                            and not supplemental
                            else "accepted"
                        ),
                        "confidence": 0.96,
                        "evidence": list(
                            dict.fromkeys(
                                [
                                    *item["evidence"],
                                    *item["subject"]["evidence"],
                                    *item.get("object", {}).get("evidence", []),
                                ]
                            )
                        ),
                        "reason": "按原文核对，与关系路径是否被采信无关",
                    }
                    for item in payload["candidates"]
                }
            }
        jsonschema.validate(response, schema)
        return response


def runner(inputs, model, *, state=None, stop=lambda: False, all_windows=False):
    ir, catalog, windows = inputs
    engine = Engine(
        ir=ir,
        catalog=catalog,
        state=state or {},
        invoke=model,
        save=lambda changes: None,
        should_stop=stop,
    )
    engine.windows = windows if all_windows else windows[:1]
    engine.run()
    return engine


def test_observation_persists_before_confirmation_then_two_bounds_survive_unproved_three_hop_path(
    deep_input,
):
    model = DeepModel()
    paused = runner(deep_input, model, stop=lambda: len(model.calls) == 1)
    raw = next(f for f in paused.state["fields"].values() if f["label"] == "负载范围")
    owner = next(e for e in paused.state["entities"].values() if raw["id"] in e["field_ids"])
    assert owner["class_iri"] is None and owner["state"] == "candidate"
    assert owner["name"] is None and owner["referent"]["text"] == "对象丙"
    assert raw["value"] == "3.8- 6.6kg" and not paused.state.get("properties")
    assert any(o.get("field_id") == raw["id"] for o in paused.state["observations"].values())
    resumed = runner(deep_input, model, state=paused.state)
    assert len([c for c in model.calls if c[0] == "discover"]) == 1
    assert all(r["state"] == "unresolved" for r in resumed.state["relations"].values())
    props = list(resumed.state["properties"].values())
    assert {p["value"] for p in props} == {"3.8", "6.6"}
    assert all(
        p["state"] == "accepted"
        and p["source_value"] == raw["value"]
        and p["source_unit"] == "kg"
        and p["field_id"] == raw["id"]
        for p in props
    )
    assert resumed.state["fields"][raw["id"]] == raw
    # The graph has only Report -> A -> B -> C; neither A nor B has a shortcut to C.
    assert {r["predicate_iri"] for r in resumed.state["relations"].values()} == {
        "urn:depth:r1",
        "urn:depth:r2",
        "urn:depth:r3",
    }
    for _, request in model.calls:
        for subject in [request.get("subject", {})]:
            if any("lower" in f["value_components"] for f in subject.get("fields", [])):
                assert subject["type_confirmed"] is True


@pytest.mark.parametrize("endpoint", ["unresolved", "rejected"])
def test_unconfirmed_endpoint_preserves_value_and_owner_without_deriving_bounds(
    deep_input, endpoint
):
    result = runner(deep_input, DeepModel(endpoint))
    owner = next(e for e in result.state["entities"].values() if e["role"] == "C")
    assert owner["state"] == endpoint and owner["field_ids"]
    assert result.state["fields"][owner["field_ids"][0]]["value"] == "3.8- 6.6kg"
    assert not result.state.get("properties")


def test_later_confirmation_consumes_saved_range_without_refinding_it(deep_input):
    model = DeepModel("unresolved")
    result = runner(deep_input, model, all_windows=True)
    owner = next(row for row in result.state["entities"].values() if row["role"] == "C")
    before = deepcopy(result.state["fields"])
    discoveries = sum(stage == "discover" for stage, _ in model.calls)
    # Apply the explicit endpoint-review result; mere later co-occurrence cannot confirm it.
    result.commit({"entities": {owner["id"]: {**owner, "state": "accepted"}}})
    result.run()
    props = list(result.state["properties"].values())
    assert len(props) == 2 and all(p["state"] == "accepted" for p in props)
    assert {p["value"] for p in props} == {"3.8", "6.6"}
    assert result.state["cursor"]["main"]["scope_complete"]
    assert result.state["fields"] == before
    assert sum(stage == "discover" for stage, _ in model.calls) == discoveries


def test_invalid_owner_does_not_discard_exact_raw_observation(deep_input):
    model = DeepModel(invalid_owner=True)
    # Inspect the original reading before its now-required smaller continuations.
    result = runner(deep_input, model, stop=lambda: len(model.calls) == 1)
    observation = next(
        o for o in result.state["observations"].values() if "候选主体引用无效" in o["reason"]
    )
    assert result.state["fields"][observation["field_id"]]["value"] == "3.8- 6.6kg"
    assert not any(
        observation["field_id"] in e["field_ids"] for e in result.state["entities"].values()
    )
    assert not result.state["windows"][observation["window_id"]]["discovery_complete"]


def test_model_cannot_derive_bounds_for_an_unconfirmed_subject(deep_input):
    model = DeepModel("unresolved", forged_component=True)
    result = runner(deep_input, model)
    assert not result.state.get("properties")
    assert not any(stage == "property_alignment" for stage, _ in model.calls)
    work = [row for row in result.state["work"].values() if row["kind"] == "property_alignment"]
    assert work and all(row["status"] == "waiting" for row in work)
    assert all(row["reason_code"] == "type_or_constraint_unresolved" for row in work)


def test_range_cannot_become_two_unrelated_values_of_the_same_predicate(deep_input):
    original = DeepModel()

    def model(stage, payload, schema):
        answer = original(stage, payload, schema)
        if stage == "property_alignment":
            for choice in answer["properties"].values():
                for mapping in choice["mappings"]:
                    mapping["predicate_iri"] = "urn:depth:p"
        return answer

    result = runner(deep_input, model)
    assert not result.state.get("properties")
    assert any("同一属性不能同时承载" in o["reason"]
               for row in result.state["observations"].values()
               for o in row.get("alignment_outcomes", {}).values())
    field = next(f for f in result.state["fields"].values() if f["label"] == "负载范围")
    assert field["value"] == "3.8- 6.6kg"
