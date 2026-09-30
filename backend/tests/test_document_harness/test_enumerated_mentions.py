"""Member anchors and identifier fields remain separate from identity decisions."""

from copy import deepcopy

import jsonschema
import pytest
from docx import Document
from rdflib import Graph

from app.services.document_harness.controller import Engine, overlapping_referents
from app.services.document_harness.ontology import catalog_from_graph
from app.services.document_harness.protocols import Mention
from app.services.document_harness.source import build_windows
from app.services.extraction.document_ir import build_document_ir
from app.services.extraction.docx_structure import parse_docx_structure


def fixture(tmp_path, text):
    doc = Document()
    doc.add_paragraph(text)
    path = tmp_path / "enumeration.docx"
    doc.save(path)
    ir = build_document_ir(path, parse_docx_structure(path))
    catalog = catalog_from_graph(Graph().parse(data='''
        @prefix : <urn:enumeration:> .
        @prefix owl: <http://www.w3.org/2002/07/owl#> .
        @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
        :Report a owl:Class . :Area a owl:Class .
        :uses a owl:ObjectProperty ; rdfs:domain :Report ; rdfs:range :Area .
    ''', format="turtle"), "urn:enumeration:Report")
    return ir, catalog, build_windows(ir)[0]


def mention(number, *, occurrence=None):
    member = {"source_id": "S1", "text": number, "occurrence": occurrence}
    return {
        "local_id": "E" + number, "name": None, "anchor": member,
        "role": "生产车间", "evidence": ["S1"], "field_ids": [],
        "source_fields": [{"label": None, "value": member}],
    }


def discover(ir, catalog, window, entities):
    calls = []

    def invoke(stage, payload, schema):
        assert stage == "discover"
        answer = {
            "entities": entities, "document_field_ids": [], "document_source_fields": [],
            "unowned_fields": [], "relation_hints": [], "complete": True,
        }
        jsonschema.validate(answer, schema)
        calls.append(answer)
        return answer

    engine = Engine(
        ir=ir, catalog=catalog, state={}, invoke=invoke, save=lambda _: None,
        should_stop=lambda: bool(calls),
        rank=lambda *_: {"snapshot_id": catalog.snapshot_id, "selected_iris": []},
    )
    engine.windows = [window]
    engine.run()
    return engine


@pytest.mark.parametrize("separator", ["/", "／", " / "])
def test_shared_suffix_forms_three_distinct_locatable_mentions(tmp_path, separator):
    text = f"计划于{separator.join(['611', '642', '646'])}车间完成生产。"
    ir, catalog, window = fixture(tmp_path, text)
    engine = discover(ir, catalog, window, [mention(n) for n in ("611", "642", "646")])
    entities = [e for e in engine.state["entities"].values() if e["id"] != "document"]
    assert [e["label"] for e in entities] == ["611", "642", "646"]
    assert len({e["id"] for e in entities}) == 3
    assert all(e["state"] == "candidate" and e["class_iri"] is None for e in entities)
    assert not any(overlapping_referents(a, b) for i, a in enumerate(entities)
                   for b in entities[i + 1:])
    for entity, number in zip(entities, ("611", "642", "646"), strict=True):
        assert entity["referent"]["text"] == number
        assert entity["name"] is None
        assert [engine.state["fields"][f]["value"] for f in entity["field_ids"]] == [number]
        for ref in entity["evidence"]:
            assert ir.unit(ref["source_id"]).text[ref["start"]:ref["end"]] == ref["text"]
        model_input = engine.entity_input(entity, window)
        assert "name_parts" not in model_input
        assert model_input["anchor"]["text"] == number
        assert engine.review_sources_cover("entities", entity, [entity["referent"]])
        assert engine.review_sources_cover("entities", entity, entity["evidence"])

    # Pausing/restoring preserves each member and its separate identifier field.
    restored = Engine(ir=ir, catalog=catalog, state=deepcopy(engine.state),
                      invoke=lambda *_: pytest.fail("restoring must not call the model"),
                      save=lambda _: None, should_stop=lambda: True)
    restored.run()
    assert restored.state == engine.state


@pytest.mark.parametrize("mutation,reason", [
    ("fabricated", "source_quote_mismatch"),
    ("foreign", "source_outside_reading_window"),
])
def test_invalid_member_anchor_is_not_a_registered_entity(tmp_path, mutation, reason):
    ir, catalog, window = fixture(tmp_path, "611/642/646车间")
    item = mention("611")
    if mutation == "fabricated":
        item["anchor"]["text"] = "612"
    else:
        item["anchor"]["source_id"] = "S2"
        with pytest.raises(ValueError, match=reason):
            window.resolve_anchor(ir, Mention.model_validate(item).anchor)
        return
    engine = discover(ir, catalog, window, [item])
    assert set(engine.state["entities"]) == {"document"}
    assert not engine.state["windows"][window.id]["complete"]
    assert any(row["reason"] == reason for row in engine.state["observations"].values())


def test_repeated_number_requires_exact_occurrence(tmp_path):
    ir, _, window = fixture(tmp_path, "611/611/646车间")
    model = Mention.model_validate(mention("611"))
    with pytest.raises(ValueError, match="source_quote_ambiguous"):
        window.resolve_anchor(ir, model.anchor)
    refs = []
    for occurrence in (0, 1):
        model = Mention.model_validate(mention("611", occurrence=occurrence))
        anchor = window.resolve_anchor(ir, model.anchor)
        refs.append(anchor)
    assert refs[0]["end"] < refs[1]["start"]


def test_explicit_single_composite_identifier_is_not_mechanically_split(tmp_path):
    ir, catalog, window = fixture(tmp_path, "单个车间的完整编号为611/642/646，不代表多个车间。")
    item = mention("611/642/646")
    engine = discover(ir, catalog, window, [item])
    entities = [e for e in engine.state["entities"].values() if e["id"] != "document"]
    assert len(entities) == 1
    assert entities[0]["label"] == "611/642/646"
