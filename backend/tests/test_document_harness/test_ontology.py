"""Ontology legality tests independent of the previous extraction protocol."""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from rdflib import RDFS, Graph, Literal, Namespace

from app.services.document_harness.ontology import (
    SchemaCatalog,
    catalog_from_graph,
    freeze_catalog,
    get_class_card,
    legal_property,
    legal_relation,
    model_menu,
)

EX = Namespace("https://example.test/schema/")
PREFIX = """
@prefix : <https://example.test/schema/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
@prefix skos: <http://www.w3.org/2004/02/skos/core#> .
"""


def graph(body: str) -> Graph:
    return Graph().parse(data=PREFIX + body, format="turtle")


def test_union_and_parallel_ranges_are_not_conflated_and_inheritance_is_preserved():
    source = graph("""
        :Root a owl:Class .
        :A a owl:Class . :B a owl:Class .
        :AOnly a owl:Class ; rdfs:subClassOf :A .
        :Both a owl:Class ; rdfs:subClassOf :A, :B .
        :Either a owl:ObjectProperty ; rdfs:domain :Root ;
            rdfs:range [ owl:unionOf (:A :B) ] .
        :Conjunction a owl:ObjectProperty ; rdfs:domain :Root ; rdfs:range :A, :B .
        :Conflict a owl:ObjectProperty ; rdfs:domain :Root ; rdfs:range :AOnly, :B .
    """)
    catalog = catalog_from_graph(source, str(EX.Root))
    assert legal_relation(catalog, str(EX.Root), str(EX.Either), str(EX.AOnly))
    assert legal_relation(catalog, str(EX.Root), str(EX.Either), str(EX.B))
    assert legal_relation(catalog, str(EX.Root), str(EX.Conjunction), str(EX.Both))
    assert not legal_relation(catalog, str(EX.Root), str(EX.Conjunction), str(EX.AOnly))
    assert not legal_relation(catalog, str(EX.Root), str(EX.Conflict), str(EX.AOnly))
    rejected = next(item for item in catalog.classes[str(EX.Root)].relations
                    if item.iri == str(EX.Conflict))
    assert rejected.constraint_status == "unresolved"
    assert any("unsatisfied_relation_range" in value for value in catalog.diagnostics)


def test_multiple_domains_are_conjunctive_and_union_domains_are_disjunctive():
    catalog = catalog_from_graph(graph("""
        :Root a owl:Class . :A a owl:Class . :B a owl:Class .
        :Both a owl:Class ; rdfs:subClassOf :A, :B .
        :rootLink a owl:ObjectProperty ; rdfs:domain :Root ;
            rdfs:range [ owl:unionOf (:A :B) ] .
        :conjunction a owl:DatatypeProperty ; rdfs:domain :A, :B ; rdfs:range xsd:string .
        :union a owl:DatatypeProperty ; rdfs:domain [owl:unionOf (:A :B)] ;
            rdfs:range xsd:string .
    """), str(EX.Root))
    assert legal_property(catalog, str(EX.Both), str(EX.conjunction))
    assert not legal_property(catalog, str(EX.A), str(EX.conjunction))
    assert legal_property(catalog, str(EX.A), str(EX.union))
    assert legal_property(catalog, str(EX.B), str(EX.union))


def test_inherited_property_ranges_and_all_values_narrow_without_some_values_widening():
    catalog = catalog_from_graph(graph("""
        :Root a owl:Class . :A a owl:Class .
        :B a owl:Class ; rdfs:subClassOf :A .
        :Parent a owl:Class ; rdfs:subClassOf [
            a owl:Restriction ; owl:onProperty :link ; owl:allValuesFrom :B
        ] .
        :Child a owl:Class ; rdfs:subClassOf :Parent, [
            a owl:Restriction ; owl:onProperty :broad ; owl:someValuesFrom :B
        ] .
        :rootLink a owl:ObjectProperty ; rdfs:domain :Root ; rdfs:range :Parent .
        :link a owl:ObjectProperty ; rdfs:domain :Parent ; rdfs:range :A .
        :narrowLink a owl:ObjectProperty ; rdfs:subPropertyOf :link .
        :broad a owl:ObjectProperty ; rdfs:domain :Parent ; rdfs:range :A .
    """), str(EX.Root))
    assert legal_relation(catalog, str(EX.Child), str(EX.link), str(EX.B))
    assert not legal_relation(catalog, str(EX.Child), str(EX.link), str(EX.A))
    assert legal_relation(catalog, str(EX.Child), str(EX.narrowLink), str(EX.B))
    assert not legal_relation(catalog, str(EX.Child), str(EX.narrowLink), str(EX.A))
    assert legal_relation(catalog, str(EX.Child), str(EX.broad), str(EX.A))
    assert any(item.kind == "restriction" for item in catalog.classes[str(EX.Child)].definition)


def test_unknown_constraints_remain_diagnostic_without_authorizing_arbitrary_predicates():
    catalog = catalog_from_graph(graph("""
        :Root a owl:Class . :A a owl:Class . :B a owl:Class .
        :unknown a owl:ObjectProperty ; rdfs:domain :Root ;
            rdfs:range [ owl:complementOf :A ] .
        :unknownDomain a owl:ObjectProperty ;
            rdfs:domain [owl:complementOf :B] ; rdfs:range :A .
        :partialUnion a owl:ObjectProperty ; rdfs:domain :Root ;
            rdfs:range [ owl:unionOf (:A [owl:complementOf :B]) ] .
        :noRange a owl:ObjectProperty ; rdfs:domain :Root .
        :noDatatype a owl:DatatypeProperty ; rdfs:domain :Root .
    """), str(EX.Root))
    assert not legal_relation(catalog, str(EX.Root), str(EX.unknown), str(EX.B))
    assert not legal_relation(catalog, str(EX.Root), str(EX.unknownDomain), str(EX.A))
    assert not legal_relation(catalog, str(EX.Root), str(EX.noRange), str(EX.A))
    assert not legal_property(catalog, str(EX.Root), str(EX.noDatatype))
    assert legal_relation(catalog, str(EX.Root), str(EX.partialUnion), str(EX.A))
    assert not legal_relation(catalog, str(EX.Root), str(EX.partialUnion), str(EX.B))
    menu = model_menu(catalog, [str(EX.Root)])["classes"][0]
    assert [item["iri"] for item in menu["relations"]] == [str(EX.partialUnion)]
    assert menu["properties"] == []
    assert catalog.diagnostics


def test_reachable_catalog_and_menus_never_accept_aliases_or_unrelated_classes():
    catalog = catalog_from_graph(graph("""
        :Root a owl:Class . :Object a owl:Class . :Unrelated a owl:Class .
        :detail a owl:Class .
        :describes a owl:ObjectProperty ; rdfs:label "describes" ;
            rdfs:domain :Root ; rdfs:range :Object .
        :detailLink a owl:ObjectProperty ; rdfs:domain :Object ; rdfs:range :detail .
        :foreign a owl:DatatypeProperty ; rdfs:domain :Unrelated ; rdfs:range xsd:string .
    """), str(EX.Root))
    assert set(catalog.reachable_class_iris) == {str(EX.Root), str(EX.Object), str(EX.detail)}
    assert str(EX.Unrelated) in catalog.classes
    assert not legal_relation(catalog, str(EX.Root), "describes", str(EX.Object))
    assert not legal_relation(catalog, str(EX.Object), str(EX.describes), str(EX.Root))
    assert not legal_property(catalog, str(EX.Unrelated), str(EX.foreign))
    with pytest.raises(ValueError, match="root-reachable"):
        get_class_card(catalog, str(EX.Unrelated))
    with pytest.raises(ValueError, match="root-reachable"):
        model_menu(catalog, [str(EX.Unrelated)])


def test_identity_keys_annotations_and_full_definitions_are_explicit_not_name_inferred():
    catalog = catalog_from_graph(graph("""
        :Root a owl:Class ; rdfs:label "根"@zh, "Root"@en ;
            skos:definition "Complete definition with its role and qualifiers."@en ;
            rdfs:comment "完整描述。"@zh ; owl:hasKey (:serial :batch) .
        :name a owl:DatatypeProperty ; rdfs:domain :Root ; rdfs:range xsd:string ;
            rdfs:label "唯一名称"@zh .
        :serial a owl:DatatypeProperty ; rdfs:domain :Root ; rdfs:range xsd:string .
        :batch a owl:DatatypeProperty ; rdfs:domain :Root ; rdfs:range xsd:string .
        :unit a owl:AnnotationProperty ; rdfs:label "Unit contract" ;
            rdfs:comment "Value storage unit; original unit still requires evidence." .
        :mass a owl:DatatypeProperty ; rdfs:domain :Root ; rdfs:range xsd:decimal ;
            :unit "kg" ; rdfs:comment "Mass of the described instance." .
    """), str(EX.Root))
    root = catalog.classes[str(EX.Root)]
    assert root.label == "根"
    assert "Complete definition" in root.description and "完整描述" in root.description
    assert root.identity_key_groups == ((str(EX.batch), str(EX.serial)),)
    props = {item.iri: item for item in root.properties}
    assert props[str(EX.serial)].identity_key
    assert not props[str(EX.name)].identity_key
    assert props[str(EX.mass)].description == "Mass of the described instance."
    menu = model_menu(catalog)
    assert any(item["iri"] == str(EX.unit) for item in menu["annotation_contracts"])
    assert "names never imply identity" in menu["identity_rule"]


def test_datatype_intersection_retains_narrower_type_and_conflicts_are_unresolved():
    catalog = catalog_from_graph(graph("""
        :Root a owl:Class .
        :narrow a owl:DatatypeProperty ; rdfs:domain :Root ;
            rdfs:range xsd:decimal, xsd:integer .
        :conflict a owl:DatatypeProperty ; rdfs:domain :Root ;
            rdfs:range xsd:string, xsd:integer .
    """), str(EX.Root))
    props = {item.iri: item for item in catalog.classes[str(EX.Root)].properties}
    assert props[str(EX.narrow)].datatype_iris == ("http://www.w3.org/2001/XMLSchema#integer",)
    assert props[str(EX.conflict)].datatype_iris == ()
    assert not legal_property(catalog, str(EX.Root), str(EX.conflict))


def test_snapshot_hash_is_blank_node_stable_and_freeze_is_detached_from_live_graph():
    turtle = """
        :Root a owl:Class . :A a owl:Class . :B a owl:Class .
        :rel a owl:ObjectProperty ; rdfs:domain :Root ; rdfs:range [owl:unionOf (:A :B)] .
    """
    first_graph = graph(turtle)
    first = catalog_from_graph(first_graph, str(EX.Root))
    second = catalog_from_graph(graph(turtle), str(EX.Root))
    assert first.snapshot_id == second.snapshot_id
    assert first == SchemaCatalog.model_validate(first.model_dump(mode="json"))
    first_graph.add((EX.Root, RDFS.comment, Literal("changed")))
    changed = catalog_from_graph(first_graph, str(EX.Root))
    assert first.snapshot_id != changed.snapshot_id
    assert first.classes[str(EX.Root)].description == ""


def test_engine_adapter_freezes_under_lock_and_only_uses_explicit_identity_metadata():
    source = graph("""
        :Root a owl:Class .
        :identifier a owl:DatatypeProperty ; rdfs:domain :Root ; rdfs:range xsd:string .
    """)

    class Engine:
        locked = False

        @contextmanager
        def lexical_read_scope(self):
            self.locked = True
            try:
                yield
            finally:
                self.locked = False

        @property
        def _world(self):
            assert self.locked
            return SimpleNamespace(as_rdflib_graph=lambda: source)

        def get_data_properties_by_domain(self, class_iri):
            assert self.locked
            assert class_iri == str(EX.Root)
            return [{"iri": str(EX.identifier), "identity_key": True}]

    engine = Engine()
    catalog = freeze_catalog(engine, str(EX.Root))
    assert not engine.locked
    assert catalog.classes[str(EX.Root)].properties[0].identity_key
    source.add((EX.Root, RDFS.comment, Literal("new version")))
    assert catalog.classes[str(EX.Root)].description == ""


def test_unknown_root_and_incomplete_engine_fail_instead_of_using_old_protocol_fallback():
    with pytest.raises(ValueError, match="not declared"):
        catalog_from_graph(graph(":Root a owl:Class ."), str(EX.Missing))
    with pytest.raises(AttributeError):
        freeze_catalog(SimpleNamespace(get_relation_schema=lambda: []), str(EX.Root))


def test_named_intersection_equivalence_has_all_parents_and_inherits_fields():
    catalog = catalog_from_graph(graph("""
        :Root a owl:Class . :A a owl:Class . :B a owl:Class .
        :Both a owl:Class ; owl:equivalentClass [owl:intersectionOf (:A :B)] .
        :rel a owl:ObjectProperty ; rdfs:domain :Root ; rdfs:range :A, :B .
        :aField a owl:DatatypeProperty ; rdfs:domain :A ; rdfs:range xsd:string .
        :bField a owl:DatatypeProperty ; rdfs:domain :B ; rdfs:range xsd:string .
    """), str(EX.Root))
    assert legal_relation(catalog, str(EX.Root), str(EX.rel), str(EX.Both))
    assert legal_property(catalog, str(EX.Both), str(EX.aField))
    assert legal_property(catalog, str(EX.Both), str(EX.bField))
    assert set(catalog.classes[str(EX.Both)].parent_iris) == {str(EX.A), str(EX.B)}


def test_missing_domain_is_not_universal_but_explicit_local_restriction_is_retained():
    catalog = catalog_from_graph(graph("""
        :Root a owl:Class ; rdfs:subClassOf [ a owl:Restriction ;
            owl:onProperty :local ; owl:allValuesFrom :Object ] .
        :Object a owl:Class .
        :local a owl:ObjectProperty .
        :unscoped a owl:ObjectProperty ; rdfs:range :Object .
    """), str(EX.Root))
    assert legal_relation(catalog, str(EX.Root), str(EX.local), str(EX.Object))
    assert not legal_relation(catalog, str(EX.Root), str(EX.unscoped), str(EX.Object))
