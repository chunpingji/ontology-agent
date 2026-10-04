"""Grounded discovery feedback uses real mapped rows without pre-splitting source text."""

from contextlib import nullcontext
from copy import deepcopy
from types import SimpleNamespace

import jsonschema
import pytest
from docx import Document
from rdflib import Graph, Literal, URIRef
from sqlalchemy import event

from app.models.mock_data import MockProductionArea
from app.models.ontology_meta import OntologyClass, OntologyClassMapping, OntologyPropertyBinding
from app.services.document_harness.controller import Engine
from app.services.document_harness.lookup_adapter import lookup_current
from app.services.document_harness.ontology import freeze_catalog
from app.services.extraction.document_ir import build_document_ir
from app.services.extraction.docx_structure import parse_docx_structure

NS = "urn:lookup-test:"


@pytest.fixture
def lookup_fixture(db, tmp_path):
    graph = Graph().parse(data='''
        @prefix : <urn:lookup-test:> .
        @prefix owl: <http://www.w3.org/2002/07/owl#> .
        @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
        @prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
        :Report a owl:Class . :Object a owl:Class ; rdfs:label "Managed object" .
        :uses a owl:ObjectProperty ; rdfs:domain :Report ; rdfs:range :Object .
        :code a owl:DatatypeProperty ; rdfs:domain :Object ; rdfs:range xsd:string .
    ''', format="turtle")
    world = SimpleNamespace(
        is_loaded=True, lexical_read_scope=nullcontext,
        _world=SimpleNamespace(as_rdflib_graph=lambda: graph),
        entity_query_graph=lambda: graph,
        get_data_properties_by_domain=lambda _: [],
    )
    catalog = freeze_catalog(world, NS + "Report")
    cls = OntologyClass(slpra_iri=NS + "Object", label="Managed object")
    db.add(cls)
    db.flush()
    mapping = OntologyClassMapping(
        class_id=cls.id, mapping_type="mock_dataset", target="production_areas",
        source_system="builtin_mock", query_config={
            "label_path": "col:label", "entity_iri_path": "col:iri",
            "identifier_namespace": "urn:fixture:objects",
            "lookup_key_groups": [{"property_iris": [NS + "code"], "scope_property_iris": []}],
        },
    )
    db.add(mapping)
    db.flush()
    db.add(OntologyPropertyBinding(class_mapping_id=mapping.id, property_iri=NS + "code",
                                   source_path="col:code"))
    db.add_all([MockProductionArea(code=x, label=x + " object", iri="urn:source:" + x)
                for x in ("A1", "A2")])
    db.commit()
    doc = Document()
    doc.add_paragraph("Use A1/A2 objects for the trial; total 5 batches.")
    path = tmp_path / "input.docx"
    doc.save(path)
    ir = build_document_ir(path, parse_docx_structure(path))
    calls = []

    def lookup(operation, schema, argument):
        calls.append((operation, deepcopy(argument)))
        return lookup_current(db, world, operation, schema, argument)

    return ir, catalog, lookup, calls, graph, mapping


def quote(text):
    return {"source_id": "S1", "text": text, "occurrence": None}


def mention(name):
    return {"candidate_class_iri": NS + "Object",
            "local_id": name, "name": None, "anchor": quote(name), "role": "object",
            "evidence": ["S1"], "field_ids": [],
            "source_fields": [{"label": None, "value": quote(name)}]}


def discovery(names):
    return {"entities": [mention(n) for n in names], "document_field_ids": [],
            "document_source_fields": [], "unowned_fields": [], "relation_hints": [],
            "complete": True}


def request(text):
    return {"capability_id": "L1", "anchor": quote("A1/A2"), "name": None,
            "properties": [{"property_iri": NS + "code", "value": quote(text)}],
            "reason": "Competing interpretation supported by the literal text"}


def runner(fixture, invoke, state=None, save=None, **kwargs):
    ir, catalog, lookup, *_ = fixture
    current = deepcopy(state or {})
    from app.services.document_harness.calls import MemoryCalls

    if state is None or not hasattr(invoke, "_call_ports"):
        invoke._call_ports = MemoryCalls(invoke)

    def persist(changes):
        if save:
            save(changes)
        for domain, rows in changes.items():
            current.setdefault(domain, {}).update(deepcopy(rows))

    engine = Engine(
        ir=ir, catalog=catalog, state=current, invoke=invoke, save=persist,
        calls=invoke._call_ports,
        should_stop=lambda: current.get("cursor", {}).get("main", {}).get("stage")
        not in (None, "discover"), lookup=lookup,
        rank=lambda *_: {"snapshot_id": catalog.snapshot_id, "selected_iris": [NS + "Object"]},
        **kwargs,
    )
    return engine, current


def responses(stage, payload, schema):
    assert stage == "discover"
    if payload["lookup_mode"] == "draft":
        result = {**discovery(["A1/A2"]), "lookup_refinement_required": True,
                  "lookup_requests": [request(x) for x in ("A1/A2", "A1", "A2")]}
    else:
        rows = payload["lookup_feedback"]["results"]
        assert [r["outcome"] for r in rows] == ["no_match", "matches", "matches"]
        result = {"replacement": discovery(["A1", "A2"]), "source_suggestions": [
            {"local_id": name, "candidate_ids": [row["candidates"][0]["candidate_id"]],
             "reason": "Possible source only"}
            for name, row in zip(("A1", "A2"), rows[1:], strict=True)
        ]}
    jsonschema.validate(result, schema)
    return result


def test_query_feedback_replaces_whole_mention_without_old_edges_or_source_identity(
    lookup_fixture,
):
    engine, _ = runner(lookup_fixture, responses)
    engine.run()
    entities = [e for e in engine.state["entities"].values() if e["id"] != "document"]
    assert [e["label"] for e in entities] == ["A1", "A2"]
    assert all(e["state"] == "candidate" and e["class_iri"] is None for e in entities)
    assert not engine.state.get("relations")
    assert all(c["identity_status"] == "not_checked"
               for c in engine.state["source_candidates"].values())
    assert not any(w.get("lookup_work") for w in engine.state["windows"].values())
    # The withdrawn whole identifier remains a source observation, never an entity/owner.
    fields = engine.state["fields"]
    orphan = next(f for f in fields.values() if f["value"] == "A1/A2")
    assert all(orphan["id"] not in e["field_ids"] for e in entities)
    assert [c[0] for c in lookup_fixture[3]] == ["capabilities", "query"]


def test_clear_mention_defers_lookup_to_semantics_without_feedback_call(lookup_fixture):
    paid = []

    def invoke(stage, payload, schema):
        paid.append(payload["lookup_mode"])
        assert payload["lookup_mode"] == "draft"
        return {**discovery(["A1"]), "lookup_requests": [],
                "lookup_refinement_required": False}

    engine, _ = runner(lookup_fixture, invoke)
    engine.run()
    assert paid == ["draft"]
    assert [c[0] for c in lookup_fixture[3]] == ["capabilities"]
    assert next(iter(engine.state["windows"].values()))["lookup"]["status"] == (
        "deferred_to_semantic"
    )
    assert [e["label"] for key, e in engine.state["entities"].items()
            if key != "document"] == ["A1"]


def test_capability_metadata_reads_no_mock_rows(lookup_fixture, db):
    _, catalog, lookup, *_ = lookup_fixture
    statements = []
    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    event.listen(db.get_bind(), "before_cursor_execute", record)
    try:
        result = lookup("capabilities", catalog, [NS + "Object"])
    finally:
        event.remove(db.get_bind(), "before_cursor_execute", record)
    assert len(result["capabilities"]) == 1
    assert all("mock_production_areas" not in s for s in statements)
    assert all(s.lstrip().split()[0].upper() == "SELECT" for s in statements)


def test_invalid_values_do_not_query_and_valid_siblings_continue(lookup_fixture):
    def invoke(stage, payload, schema):
        if payload["lookup_mode"] == "draft":
            return {**discovery(["A1/A2"]), "lookup_refinement_required": True,
                    "lookup_requests": [request("invented"), request("5"), request("A1")]}
        feedback = payload["lookup_feedback"]
        assert [q["status"] for q in feedback["requests"]] == ["invalid", "invalid", "ready"]
        assert [q["query_id"] for q in feedback["results"]] == ["Q3"]
        return {"replacement": discovery(["A1", "A2"]), "source_suggestions": []}
    engine, _ = runner(lookup_fixture, invoke)
    engine.run()
    assert len(lookup_fixture[3][-1][1]["queries"]) == 1


def test_schema_change_disables_lookup_without_removing_document_entities(lookup_fixture):
    graph = lookup_fixture[4]
    graph.add((URIRef(NS + "Object"), URIRef("urn:changed"), Literal("changed")))
    def invoke(stage, payload, schema):
        assert "lookup_mode" not in payload
        return discovery(["A1", "A2"])
    engine, _ = runner(lookup_fixture, invoke)
    engine.run()
    assert len(engine.state["entities"]) == 3
    assert next(iter(engine.state["windows"].values()))["lookup"]["issues"] == [
        "lookup_ontology_changed",
    ]


def test_pause_before_registration_reuses_saved_query_and_paid_feedback(lookup_fixture):
    paid, calls = {}, []
    def invoke(stage, payload, schema):
        key = repr((stage, payload, schema))
        if key not in paid:
            calls.append(payload["lookup_mode"])
            paid[key] = responses(stage, payload, schema)
        return deepcopy(paid[key])
    def interrupted(changes):
        if len(changes.get("entities", {})) > 1:
            raise RuntimeError("pause_before_registration")
    engine, state = runner(lookup_fixture, invoke, save=interrupted)
    with pytest.raises(RuntimeError, match="pause_before_registration"):
        engine.run()
    assert set(state["entities"]) == {"document"}
    restored, _ = runner(lookup_fixture, invoke, state=state)
    restored.run()
    assert len(restored.state["entities"]) == 3
    assert calls == ["draft", "refine"]
    assert [c[0] for c in lookup_fixture[3]] == ["capabilities", "query"]


@pytest.mark.parametrize("empty", [True, False])
def test_no_hit_or_incomplete_query_does_not_delete_source_mentions(lookup_fixture, empty):
    original = lookup_fixture[2]
    def lookup(operation, catalog, argument):
        result = original(operation, catalog, argument)
        if operation == "query":
            for row in result["results"]:
                row.update(candidates=[], outcome="no_match" if empty else "unresolved",
                           complete=empty, truncated=not empty)
        return result
    fixture = (*lookup_fixture[:2], lookup, *lookup_fixture[3:])
    paid = []
    def invoke(stage, payload, schema):
        paid.append(payload["lookup_mode"])
        assert payload["lookup_mode"] == "draft"
        return {**discovery(["A1", "A2"]), "lookup_refinement_required": True,
                    "lookup_requests": [request("A1/A2")]}
    engine, _ = runner(fixture, invoke)
    engine.run()
    assert len(engine.state["entities"]) == 3
    assert not engine.state.get("source_candidates")
    assert paid == ["draft"]
    status = next(iter(engine.state["windows"].values()))["lookup"]
    assert status["results"][0]["complete"] == empty


def test_composite_identifier_is_not_split_by_matching_members(lookup_fixture):
    def invoke(stage, payload, schema):
        if payload["lookup_mode"] == "draft":
            return {**discovery(["A1/A2"]), "lookup_refinement_required": True,
                    "lookup_requests": [request(x) for x in ("A1/A2", "A1", "A2")]}
        return {"replacement": None, "source_suggestions": []}
    engine, _ = runner(lookup_fixture, invoke)
    engine.run()
    assert [e["label"] for key, e in engine.state["entities"].items() if key != "document"] == [
        "A1/A2",
    ]


def test_mapping_change_between_draft_and_query_is_not_a_no_match(lookup_fixture, db):
    def invoke(stage, payload, schema):
        if payload["lookup_mode"] == "draft":
            lookup_fixture[5].version += 1
            db.commit()
            return {**discovery(["A1", "A2"]), "lookup_refinement_required": True,
                    "lookup_requests": [request("A1")]}
        raise AssertionError("No candidates must not trigger a second model call")
    engine, _ = runner(lookup_fixture, invoke)
    engine.run()
    assert len(engine.state["entities"]) == 3

    status = next(iter(engine.state["windows"].values()))["lookup"]
    assert status["issues"] == ["lookup_mapping_changed"]
    assert status["results"] == []


def test_oversized_feedback_retains_draft_without_silent_truncation(lookup_fixture):
    original = lookup_fixture[2]
    def lookup(operation, catalog, argument):
        result = original(operation, catalog, argument)
        if operation == "query":
            result["results"][0]["candidates"][0]["label"] = "x" * 40000
        return result
    fixture = (*lookup_fixture[:2], lookup, *lookup_fixture[3:])
    calls = []
    def invoke(stage, payload, schema):
        calls.append(payload["lookup_mode"])
        assert payload["lookup_mode"] == "draft"
        return {**discovery(["A1/A2"]), "lookup_refinement_required": True,
                    "lookup_requests": [request("A1")]}
    engine, _ = runner(fixture, invoke, max_request_bytes=32768)
    engine.run()
    assert calls == ["draft"]
    status = next(iter(engine.state["windows"].values()))["lookup"]
    assert status == {"status": "not_completed", "issues": ["lookup_feedback_budget_exceeded"]}
    assert not engine.state.get("source_candidates")


def test_invented_candidate_reference_cannot_be_saved(lookup_fixture):
    def invoke(stage, payload, schema):
        if payload["lookup_mode"] == "draft":
            return {**discovery(["A1/A2"]), "lookup_refinement_required": True,
                    "lookup_requests": [request("A1")]}
        return {"replacement": discovery(["A1", "A2"]), "source_suggestions": [
            {"local_id": "A1", "candidate_ids": ["foreign"], "reason": "Unsupported proposal"},
        ]}
    engine, _ = runner(lookup_fixture, invoke)
    with pytest.raises(ValueError, match="harness_output_schema_mismatch"):
        engine.run()
    assert not engine.state.get("source_candidates")
    assert engine.state["cursor"]["main"]["active_batches"]  # Paid invalid answer stays visible.
