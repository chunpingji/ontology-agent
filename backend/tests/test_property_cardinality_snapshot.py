"""Quantity declarations survive publication/restart and frozen-menu inheritance."""

import owlready2
import pytest
from rdflib import OWL, RDF, RDFS, XSD, BNode, Graph, Literal, Namespace

import app.services.ontology_engine as oe
from app.services.extraction.ontology_guided.contracts import (
    OntologyClassDefinition,
    OntologySnapshot,
    SlotSpec,
    SubjectRef,
)
from app.services.extraction.ontology_guided.ontology_plan import (
    compile_local_menu,
    ontology_snapshot_from_engine,
)
from app.services.ontology_cardinality import emit_cardinality_config, remove_cardinality_config

NS = Namespace("https://test.example/cardinality/")
BASE = f"""
@prefix owl: <{OWL}> .
@prefix rdfs: <{RDFS}> .
@prefix xsd: <{XSD}> .
@prefix : <{NS}> .
<{NS}> a owl:Ontology .
:Parent a owl:Class .
:Child a owl:Class ; rdfs:subClassOf :Parent .
:Target a owl:Class .
:amount a owl:DatatypeProperty ; rdfs:domain :Parent ; rdfs:range xsd:decimal .
:related a owl:ObjectProperty ; rdfs:domain :Parent ; rdfs:range :Target .
"""


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.setattr(oe, "MODULE_NAMES", {"test": str(NS)})
    monkeypatch.setattr(oe, "MODULE_FILES", {"test": "test.ttl"})
    monkeypatch.setattr(oe, "_LOAD_ORDER", ["test"])
    monkeypatch.setattr(oe, "_EXTERNAL_ONTOLOGIES", {})
    (tmp_path / "test.ttl").write_text(BASE)
    current = oe.OntologyEngine(tmp_path, tmp_path / "world.sqlite3")
    current.load()
    try:
        yield current
    finally:
        current.close()


def menu(engine, owner=NS.Parent):
    return compile_local_menu(
        ontology_snapshot_from_engine(engine),
        SubjectRef(entity_id="test", revision=1, class_iri=str(owner)),
    )


def quantity(prop):
    return prop.multiplicity, prop.min_count, prop.max_count, prop.constraint_status


@pytest.mark.parametrize("mode,minimum,maximum", [
    ("unspecified", None, None), ("single", None, 1), ("single", 1, 1),
    ("multiple", None, None), ("multiple", 1, 3), ("unspecified", 0, 0),
])
def test_publish_and_reload_have_identical_quantity_snapshot(engine, mode, minimum, maximum):
    graph = Graph().parse(data=BASE, format="turtle")
    payloads = []
    for ref, kind in ((NS.amount, "data_property"), (NS.related, "link_type")):
        emit_cardinality_config(graph, ref, NS.Parent, mode, minimum, maximum)
        payloads.append({
            "kind": kind, "iri": str(ref), "domain_iri": str(NS.Parent),
            "multiplicity": mode, "min_cardinality": minimum, "max_cardinality": maximum,
        })
    engine.project_entities(payloads, published_graph=graph)
    before = menu(engine)
    assert quantity(before.properties[0]) == (mode, minimum, maximum, "resolved")
    assert quantity(before.relationships[0]) == (mode, minimum, maximum, "resolved")
    graph.serialize(engine._ontology_dir / "slpra_managed.ttl", format="turtle")
    engine.close()
    engine.load()
    after = menu(engine)
    assert quantity(after.properties[0]) == quantity(before.properties[0])
    assert quantity(after.relationships[0]) == quantity(before.relationships[0])
    assert after.ontology_snapshot_id == before.ontology_snapshot_id


def test_explicit_clear_overrides_functional_in_original_module(engine):
    original = Graph().parse(data=BASE, format="turtle")
    original.add((NS.amount, RDF.type, OWL.FunctionalProperty))
    original.serialize(engine._ontology_dir / "test.ttl", format="turtle")
    managed = Graph()
    managed += original
    remove_cardinality_config(managed, NS.amount, remove_functional=True)
    emit_cardinality_config(managed, NS.amount, NS.Parent, "unspecified", None, None)
    managed.serialize(engine._ontology_dir / "slpra_managed.ttl", format="turtle")
    engine.close()
    engine.load()
    assert quantity(menu(engine).properties[0]) == ("unspecified", None, None, "resolved")
    assert owlready2.FunctionalProperty not in engine._world[str(NS.amount)].is_a


def test_new_property_and_domain_survive_restart(engine):
    graph = Graph().parse(data=BASE, format="turtle")
    graph.add((NS.NewClass, RDF.type, OWL.Class))
    graph.add((NS.newAmount, RDF.type, OWL.DatatypeProperty))
    graph.add((NS.newAmount, RDFS.domain, NS.NewClass))
    graph.add((NS.newAmount, RDFS.range, XSD.decimal))
    emit_cardinality_config(graph, NS.newAmount, NS.NewClass, "multiple", 1, 5)
    graph.serialize(engine._ontology_dir / "slpra_managed.ttl", format="turtle")
    engine.close()
    engine.load()
    assert quantity(menu(engine, NS.NewClass).properties[0]) == ("multiple", 1, 5, "resolved")


@pytest.mark.parametrize("minimum, expected", [(2, "resolved"), (4, "constraint_unresolved")])
def test_child_constraint_intersects_parent_without_losing_inherited_predicate(
    engine, minimum, expected,
):
    graph = Graph().parse(data=BASE, format="turtle")
    emit_cardinality_config(graph, NS.amount, NS.Parent, "multiple", None, 3)
    restriction = BNode()
    graph.add((NS.Child, RDFS.subClassOf, restriction))
    graph.add((restriction, RDF.type, OWL.Restriction))
    graph.add((restriction, OWL.onProperty, NS.amount))
    graph.add((restriction, OWL.minCardinality, Literal(minimum, datatype=XSD.nonNegativeInteger)))
    engine.project_entities([], published_graph=graph)
    assert quantity(menu(engine, NS.Child).properties[0]) == ("multiple", minimum, 3, expected)


def test_invalid_quantity_does_not_partially_project(engine):
    before = ontology_snapshot_from_engine(engine)
    with pytest.raises(ValueError):
        engine.project_entities([
            {"kind": "class", "iri": str(NS.MustNotExist)},
            {"kind": "data_property", "iri": str(NS.amount), "multiplicity": "single",
             "domain_iri": str(NS.Parent), "min_cardinality": 2, "max_cardinality": 1},
        ])
    assert engine._world[str(NS.MustNotExist)] is None
    assert ontology_snapshot_from_engine(engine) == before


def test_legacy_snapshot_keeps_original_payload_and_identity():
    prop = {"iri": str(NS.amount), "label": "数量", "datatype_iris": [str(XSD.decimal)]}
    legacy = OntologySnapshot(
        snapshot_id="historical", ontology_hash="recorded",
        classes={str(NS.Parent): OntologyClassDefinition(
            iri=str(NS.Parent), label="主体", source_hash="fixed",
            declared_properties=[SlotSpec(**prop)],
        )},
    ).model_dump(mode="json")
    restored = OntologySnapshot.model_validate(legacy, strict=True)
    assert restored.model_dump(mode="json") == legacy
    assert restored.classes[str(NS.Parent)].declared_properties[0].multiplicity == "unspecified"


def test_explicit_multiple_changes_snapshot_identity(engine):
    before = ontology_snapshot_from_engine(engine)
    engine.upsert_data_property(
        str(NS.amount), domain_iri=str(NS.Parent), multiplicity="multiple",
        min_cardinality=None, max_cardinality=None,
    )
    assert ontology_snapshot_from_engine(engine).snapshot_id != before.snapshot_id


@pytest.mark.parametrize("failure", ["entity", "ttl", "save"])
def test_failed_projection_restores_world_quantities_and_existing_facts(
    engine, monkeypatch, failure,
):
    with engine.modules["test"]:
        engine._world[str(NS.Parent)]("existingFact")
    before = ontology_snapshot_from_engine(engine)
    original = engine.upsert_data_property

    def fail(*args, **kwargs):
        if failure == "entity":
            raise RuntimeError("injected projection failure")
        return original(*args, **kwargs)

    def fail_write():
        raise OSError("injected TTL write failure")

    if failure == "save":
        save = engine._world.save
        calls = 0

        def fail_commit():
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("injected World commit failure")
            save()

        monkeypatch.setattr(engine._world, "save", fail_commit)
    monkeypatch.setattr(engine, "upsert_data_property", fail)
    with pytest.raises(oe.OntologyIntegrityError, match="rolled back"):
        engine.project_entities([
            {"kind": "class", "iri": str(NS.Parent), "label": "Unpublished edit"},
            {"kind": "link_type", "iri": str(NS.related), "domain_iri": str(NS.Parent),
             "multiplicity": "single", "min_cardinality": 1, "max_cardinality": 1},
            {"kind": "data_property", "iri": str(NS.amount)},
        ], publish_callback=fail_write if failure == "ttl" else None)
    assert ontology_snapshot_from_engine(engine) == before
    assert engine._world[str(NS.existingFact)] is not None
    assert owlready2.FunctionalProperty not in engine._world[str(NS.related)].is_a


def test_owned_bounds_materialize_in_world_and_clear_without_removing_external_axiom(engine):
    original_world = engine._world
    with engine.modules["test"]:
        parent = engine._world[str(NS.Parent)]
        prop = engine._world[str(NS.amount)]
        parent.is_a.append(prop.min(1))
    engine.project_entities([
        {"kind": "data_property", "iri": str(NS.amount), "domain_iri": str(NS.Parent),
         "multiplicity": "multiple", "min_cardinality": 1, "max_cardinality": 3},
    ])
    parent = engine._world[str(NS.Parent)]
    assert engine._world is original_world
    bounds = [item for item in parent.is_a if isinstance(item, owlready2.Restriction)]
    assert any(item.type == owlready2.MAX and item.cardinality == 3 for item in bounds)
    engine.project_entities([
        {"kind": "data_property", "iri": str(NS.amount), "domain_iri": str(NS.Parent),
         "multiplicity": "unspecified", "min_cardinality": None, "max_cardinality": None},
    ])
    parent = engine._world[str(NS.Parent)]
    bounds = [item for item in parent.is_a if isinstance(item, owlready2.Restriction)]
    assert len(bounds) == 1
    assert bounds[0].type == owlready2.MIN and bounds[0].cardinality == 1


def test_api_save_release_file_reload_and_clear_form_one_path(
    engine, db, client, analyst_headers, monkeypatch,
):
    from app.config import settings
    from app.services.ontology_meta_store import OntologyMetaStore
    from tests.test_api.test_ontology_release import BASE, _fully_mapped_class

    monkeypatch.setattr(settings, "ontology_dir", engine._ontology_dir)
    owner = _fully_mapped_class(client, analyst_headers, "ReleaseOwner")
    store = OntologyMetaStore(db=db, engine=engine)
    monkeypatch.setattr(store, "_commit_ttl", lambda _release_no: None)
    rows = []
    for path, local, mode, minimum, maximum in (
        ("data-properties", "amount", "multiple", 1, 3),
        ("link-types", "related", "single", None, 1),
    ):
        response = client.post(f"/api/ontology/{path}", headers=analyst_headers, json={
            "slpra_iri": BASE + local, "label": local, "domain_iri": owner,
            **({"datatype": "decimal"} if path == "data-properties" else {"range_iri": owner}),
            "multiplicity": mode, "min_cardinality": minimum, "max_cardinality": maximum,
        })
        assert response.status_code == 201
        rows.append((path, response.json()))

    def publish_and_reload(title):
        release = store.create_release(title, "analyst")
        store.submit_release(release["id"], "analyst")
        assert store.publish_release(release["id"], "analyst")["status"] == "published"
        before = menu(engine, owner)
        engine.close()
        engine.load()
        after = menu(engine, owner)
        assert [quantity(p) for p in after.properties] == [quantity(p) for p in before.properties]
        assert [quantity(p) for p in after.relationships] == [
            quantity(p) for p in before.relationships
        ]
        return after

    published = publish_and_reload("configured quantities")
    assert quantity(published.properties[0]) == ("multiple", 1, 3, "resolved")
    assert quantity(published.relationships[0]) == ("single", None, 1, "resolved")
    for path, saved in rows:
        response = client.put(
            f"/api/ontology/{path}/{saved['slpra_iri']}", headers=analyst_headers, json={
                "expected_version": saved["version"], "multiplicity": "unspecified",
                "min_cardinality": None, "max_cardinality": None,
            },
        )
        assert response.status_code == 200
    cleared = publish_and_reload("cleared quantities")
    for prop in [*cleared.properties, *cleared.relationships]:
        assert quantity(prop) == ("unspecified", None, None, "resolved")


@pytest.mark.parametrize("kind,expected_menu,expected_dependency", [
    (
        "property",
        "7972ccafa601df73e1625dec76d284b0d5f86200a5df0348cebbc925ee0aeec7",
        "c31b40aa66dc7dcebe742bb8d7803461025eba284ea038a0ca98ca82ea57909f",
    ),
    (
        "relationship",
        "e06f19e9efef01eea87be5611166fc67f76cef63e1a20a5a1fe6ab1b606a67d7",
        "cc4a4d63f1df38f8f9262fcd9f1d9673ebdb50e9a19d03a5531293004a33958c",
    ),
])
def test_resumed_legacy_menu_preserves_recorded_dependency_identity(
    kind, expected_menu, expected_dependency,
):
    from app.services.extraction.evidence_identity import evidence_hash

    # These identities were recorded with the pre-cardinality menu compiler.
    # A restored old snapshot must not acquire new fields during menu merging.
    predicate = {"iri": "p", "label": "P", "max_count": 1}
    if kind == "property":
        predicate["datatype_iris"] = [str(XSD.string)]
        declaration = "declared_properties"
    else:
        predicate["range_class_iris"] = ["T"]
        declaration = "declared_relationships"
    snapshot = OntologySnapshot.model_validate({
        "snapshot_id": "old-snapshot", "ontology_hash": "old-hash",
        "classes": {
            "C": {"iri": "C", "label": "C", "source_hash": "old", declaration: [predicate]},
            "T": {"iri": "T", "label": "T", "source_hash": "target"},
        },
    })
    subject = SubjectRef(entity_id="s", revision=1, class_iri="C")
    local = compile_local_menu(snapshot, subject)
    frozen_predicate = local.properties[0] if kind == "property" else local.relationships[0]
    payload = frozen_predicate.model_dump(mode="json")
    assert "multiplicity" not in payload
    assert "min_count" not in payload
    assert local.menu_id == expected_menu
    assert evidence_hash([
        subject.model_dump(mode="json"), payload, "old-analysis", local.menu_id, 0,
    ]) == expected_dependency
