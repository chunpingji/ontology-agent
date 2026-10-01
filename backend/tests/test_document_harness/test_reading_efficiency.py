"""Regressions for repeated context, inflated guidance and misleading progress."""

import json
from contextlib import nullcontext
from threading import RLock
from types import SimpleNamespace

import pytest
from rdflib import Graph, Literal, URIRef
from test_controller import inputs as inputs  # noqa: F401
from test_identity_guidance import catalog

from app.services.document_harness.ontology import freeze_catalog
from app.services.document_harness.protocols import SourceAnchor
from app.services.document_harness.ranking import reading_card, reading_guidance
from app.services.document_harness.source import build_windows
from app.services.ontology_engine import _schema_mutation
from tests.test_extraction.test_ontology_lexical_snapshot import (
    lexical_engine as lexical_engine,  # noqa: F401
)


def test_main_range_does_not_give_shared_context_entity_ownership(inputs):
    ir, _ = inputs
    window = build_windows(ir)[0]
    source = window.sources[-1]
    window.primary_ranges = [{"evidence_id": source["evidence_id"],
                              "start": source["offset"],
                              "end": source["offset"] + len(source["text"])}]
    assert window.payload()["reading_scope"] == [{"source_id": source["source_id"],
                                                  "start": 0, "end": len(source["text"])}]
    primary = window.resolve_anchor(ir, SourceAnchor(source_id=source["source_id"]))
    assert window.owns(primary)
    context = window.resolve_anchor(ir, SourceAnchor(source_id=window.sources[0]["source_id"]))
    assert not window.owns(context)
    assert window.quotes(ir, [window.sources[0]["source_id"]])  # Still valid evidence.


def test_reading_guidance_shares_inherited_semantics_without_losing_scoped_keys():
    cards = catalog()
    iris = ["urn:identity:Object", "urn:identity:Child"]
    bundle = reading_guidance(cards, iris)
    assert len(bundle["classes"]) == 2
    for card in bundle["classes"]:
        assert ["urn:identity:scope", "urn:identity:serial"] in card["identity_key_groups"]
        assert "urn:identity:unknown" in card["unavailable_identity_components"]
    serial = [d for d in bundle["identity_properties"] if d["iri"] == "urn:identity:serial"]
    assert len(serial) == 1 and set(serial[0]["class_iris"]) == set(iris)
    assert "associated site" in serial[0]["description"]
    assert bundle["annotation_contracts"][0]["iri"] == "urn:identity:mark"
    old = [reading_card(cards.classes[i], annotation_contracts=cards.annotation_contracts)
           for i in iris]
    assert len(json.dumps(bundle)) < len(json.dumps(old))


def test_catalog_cache_invalidated_by_existing_schema_mutation_boundary():
    graph = Graph().parse(data='''
        @prefix : <urn:cached:> . @prefix owl: <http://www.w3.org/2002/07/owl#> .
        :Root a owl:Class .
    ''', format="turtle")
    reads = []
    def properties(iri):
        reads.append(iri)
        return []
    world = SimpleNamespace(_lock=RLock(), lexical_read_scope=nullcontext,
                            _harness_catalogs={}, _semantic_schema=None,
                            _world=SimpleNamespace(as_rdflib_graph=lambda: graph),
                            get_data_properties_by_domain=properties)
    first = freeze_catalog(world, "urn:cached:Root")
    assert freeze_catalog(world, "urn:cached:Root") is first
    assert len(reads) == 1

    @_schema_mutation
    def change(engine):
        graph.add((URIRef("urn:cached:Root"), URIRef("urn:changed"), Literal("changed")))
    change(world)
    assert freeze_catalog(world, "urn:cached:Root").snapshot_id != first.snapshot_id
    assert len(reads) == 2


def test_live_engine_catalog_is_fresh_after_edit_and_reload(lexical_engine):
    iri = "https://test.example/lexical/Root"
    first = freeze_catalog(lexical_engine, iri)
    assert freeze_catalog(lexical_engine, iri) is first
    lexical_engine.upsert_class(iri, label="Changed root", module="test")
    changed = freeze_catalog(lexical_engine, iri)
    assert changed.snapshot_id != first.snapshot_id
    assert changed.classes[iri].label == "Changed root"
    assert first.classes[iri].label != "Changed root"
    lexical_engine.close()
    lexical_engine.load()
    reloaded = freeze_catalog(lexical_engine, iri)
    assert reloaded is not first
    assert reloaded.snapshot_id == first.snapshot_id


def test_public_reading_counts_do_not_confuse_partial_with_complete():
    from app.schemas.document_harness import HarnessReading

    result = HarnessReading(total_characters=100, processed_characters=100,
                            complete_characters=20, complete=False)
    assert result.complete is False
    with pytest.raises(ValueError):
        HarnessReading(total_characters=100, processed_characters=20,
                       complete_characters=100, complete=False)


def test_entity_anchor_cannot_cross_from_primary_into_context(inputs):
    from app.services.document_harness.protocols import Discovery
    from app.services.document_harness.reading import decode_local_discovery

    ir, _ = inputs
    window = build_windows(ir)[0]
    source = next(s for s in window.sources if "Alpha" in s["text"])
    start = source["text"].index("Alpha")
    window.primary_ranges = [{"evidence_id": source["evidence_id"],
                              "start": source["offset"] + start,
                              "end": source["offset"] + start + 3}]
    anchor = {"source_id": source["source_id"], "text": "Alpha", "occurrence": None}
    answer = Discovery.model_validate({
        "entities": [{"local_id": "a", "name": anchor, "anchor": anchor,
                      "role": "object", "evidence": [source["source_id"]],
                      "field_ids": [], "source_fields": []}],
        "document_field_ids": [], "document_source_fields": [], "unowned_fields": [],
        "relation_hints": [], "complete": True,
    })
    delta = decode_local_discovery(ir, window, answer, {}, {"id": "document"})
    changes = delta["changes"]
    assert not delta["complete"]
    assert set(changes["entities"]) == {"document"}
    assert any(o["reason"] == "entity_anchor_outside_primary_range"
               for o in changes["observations"].values())


def test_saved_parent_progress_is_not_double_counted_with_complete_child(inputs):
    from app.services.document_harness.continuation import split_reading_window
    from app.services.document_harness.controller import Engine

    ir, cards = inputs
    engine = Engine(ir=ir, catalog=cards, state={}, invoke=lambda *_: None,
                    save=lambda _: None, should_stop=lambda: True)
    engine.run()
    parent = engine.windows[0]
    children = split_reading_window(ir, parent)
    assert len(children) == 2
    engine.state["windows"][parent.id].update(
        result_saved=True, reading_state="incomplete", children=[w.id for w in children],
    )
    for i, child in enumerate(children):
        row = engine.window_row(child, [0, i], parent_id=parent.id, depth=1)
        row.update(result_saved=(i == 0), reading_state="complete" if i == 0 else "pending")
        engine.state["windows"][child.id] = row
    engine.update_reading()
    reading = engine.state["cursor"]["main"]["reading"]
    assert reading["processed_characters"] == reading["total_characters"]
    assert 0 < reading["complete_characters"] < reading["processed_characters"]
    assert not reading["complete"]


def test_shared_relations_keep_class_specific_range_intersections():
    from app.services.document_harness.ontology import catalog_from_graph

    graph = Graph().parse(data='''
        @prefix : <urn:scoped:> . @prefix owl: <http://www.w3.org/2002/07/owl#> .
        @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
        :Root a owl:Class . :A a owl:Class . :B a owl:Class .
        :Child a owl:Class ; rdfs:subClassOf :Root,
            [ a owl:Restriction ; owl:onProperty :related ; owl:allValuesFrom :A ] .
        :related a owl:ObjectProperty ; rdfs:domain :Root ;
            rdfs:range [ a owl:Class ; owl:unionOf (:A :B) ] .
    ''', format="turtle")
    cards = catalog_from_graph(graph, "urn:scoped:Root")
    bundle = reading_guidance(cards, ["urn:scoped:Root", "urn:scoped:Child"])
    relations = bundle["relations"]
    assert len(relations) == 2
    by_class = {r["class_iris"][0]: r["range"] for r in relations}
    assert any(r["kind"] == "union" for r in by_class["urn:scoped:Root"])
    assert len(by_class["urn:scoped:Child"]) == 2
    assert by_class["urn:scoped:Root"] != by_class["urn:scoped:Child"]
