"""Quantity declarations survive editing, TTL releases and conservative OWL reads."""

from __future__ import annotations

import pytest
from rdflib import OWL, RDF, RDFS, XSD, BNode, Graph, Literal, Namespace
from rdflib.collection import Collection
from rdflib.compare import isomorphic

from app.models.ontology_meta import OntologyClass, OntologyDataProperty, OntologyLinkType
from app.services.ontology_cardinality import (
    CORE,
    apply_cardinality_overlay,
    emit_cardinality_config,
    read_cardinality_config,
    read_effective_cardinality,
    remove_cardinality_config,
)
from app.services.ttl_merge import (
    build_managed_graph,
    diff_graphs,
    load_base_graph,
    surgical_merge,
)

EX = Namespace("https://example.org/cardinality/")


def _restriction(graph, owner, prop, predicate, count):
    node = BNode()
    graph.add((owner, RDFS.subClassOf, node))
    graph.add((node, RDF.type, OWL.Restriction))
    graph.add((node, OWL.onProperty, prop))
    graph.add((node, predicate, Literal(count, datatype=XSD.nonNegativeInteger)))
    return node


def _reparse(graph):
    return Graph().parse(data=graph.serialize(format="turtle"), format="turtle")


@pytest.mark.parametrize("model,rdf_type", [
    (OntologyDataProperty, OWL.DatatypeProperty),
    (OntologyLinkType, OWL.ObjectProperty),
])
@pytest.mark.parametrize("mode,minimum,maximum", [
    ("unspecified", None, None),
    ("unspecified", 0, 4),
    ("single", None, 1),
    ("single", 1, 1),
    ("multiple", None, None),
    ("multiple", 1, 3),
])
def test_metadata_ttl_roundtrip_preserves_declaration_and_effective_bounds(
    db, model, rdf_type, mode, minimum, maximum,
):
    owner = OntologyClass(slpra_iri=str(EX.Owner), label="Owner")
    db.add(owner)
    db.flush()
    prop = model(
        slpra_iri=str(EX.value), label="Value", domain_class_id=owner.id,
        multiplicity=mode, min_cardinality=minimum, max_cardinality=maximum,
    )
    if model is OntologyLinkType:
        prop.is_functional = mode == "single"
    db.add(prop)
    db.flush()

    managed, subjects = build_managed_graph(db)
    first = surgical_merge(Graph(), managed, subjects)
    second = surgical_merge(_reparse(first), managed, subjects)
    assert isomorphic(first, second)
    assert (EX.value, RDF.type, rdf_type) in second
    assert read_cardinality_config(second, EX.value) == {
        "multiplicity": mode, "min_cardinality": minimum, "max_cardinality": maximum,
    }
    assert read_effective_cardinality(second, EX.value, EX.Owner) == {
        "multiplicity": mode, "min_count": minimum, "max_count": maximum,
        "constraint_status": "resolved",
    }
    assert ((EX.value, RDF.type, OWL.FunctionalProperty) in second) == (mode == "single")
    if mode == "single" and minimum is None:
        assert not list(second.triples((None, OWL.minCardinality, None)))


def test_legacy_functional_is_single_but_absence_does_not_declare_multiple():
    graph = Graph()
    assert read_cardinality_config(graph, EX.value)["multiplicity"] == "unspecified"
    graph.add((EX.value, RDF.type, OWL.FunctionalProperty))
    assert read_cardinality_config(graph, EX.value) == {
        "multiplicity": "single", "min_cardinality": None, "max_cardinality": 1,
    }


def test_inherited_limits_intersect_without_rewriting_unspecified_policy():
    graph = Graph()
    graph.add((EX.Child, RDFS.subClassOf, EX.Parent))
    graph.add((EX.Parent, RDFS.subClassOf, EX.Grandparent))
    graph.add((EX.Grandparent, RDFS.subClassOf, EX.Parent))  # ancestor cycle is finite
    _restriction(graph, EX.Grandparent, EX.value, OWL.maxCardinality, 4)
    _restriction(graph, EX.Parent, EX.value, OWL.maxCardinality, 2)
    _restriction(graph, EX.Child, EX.value, OWL.cardinality, 1)
    _restriction(graph, EX.Other, EX.value, OWL.minCardinality, 9)
    assert read_effective_cardinality(graph, EX.value, EX.Child) == {
        "multiplicity": "unspecified", "min_count": 1, "max_count": 1,
        "constraint_status": "resolved",
    }


def test_conflicting_inherited_and_global_limits_remain_explicit():
    graph = Graph()
    graph.add((EX.value, RDF.type, OWL.FunctionalProperty))
    graph.add((EX.Child, RDFS.subClassOf, EX.Parent))
    _restriction(graph, EX.Parent, EX.value, OWL.minCardinality, 2)
    assert read_effective_cardinality(graph, EX.value, EX.Child) == {
        "multiplicity": "single", "min_count": 2, "max_count": 1,
        "constraint_status": "constraint_unresolved",
    }


def test_equivalent_restriction_and_intersection_apply_as_necessary_bounds():
    graph = Graph()
    lower = _restriction(graph, EX.Unrelated, EX.value, OWL.minCardinality, 1)
    upper = _restriction(graph, EX.Unrelated, EX.value, OWL.maxCardinality, 3)
    expression, head = BNode(), BNode()
    graph.add((EX.Owner, OWL.equivalentClass, expression))
    graph.add((expression, OWL.intersectionOf, head))
    Collection(graph, head, [EX.Parent, lower])
    graph.add((upper, OWL.equivalentClass, EX.Parent))  # equality works in either direction
    assert read_effective_cardinality(graph, EX.value, EX.Owner) == {
        "multiplicity": "unspecified", "min_count": 1, "max_count": 3,
        "constraint_status": "resolved",
    }


def test_qualified_limit_is_not_total_limit_and_union_is_not_intersection():
    graph = Graph()
    qualified = _restriction(graph, EX.Owner, EX.value, OWL.maxQualifiedCardinality, 1)
    graph.add((qualified, OWL.onClass, EX.SpecialValue))
    union, head = BNode(), BNode()
    candidate = _restriction(graph, EX.Unrelated, EX.value, OWL.maxCardinality, 0)
    graph.add((EX.Owner, RDFS.subClassOf, union))
    graph.add((union, OWL.unionOf, head))
    Collection(graph, head, [candidate, EX.Other])
    result = read_effective_cardinality(graph, EX.value, EX.Owner)
    assert result["max_count"] is None
    assert result["constraint_status"] == "constraint_unresolved"


def test_malformed_cardinality_is_unresolved_not_truncated():
    graph = Graph()
    node = _restriction(graph, EX.Owner, EX.value, OWL.maxCardinality, 1)
    graph.set((node, OWL.maxCardinality, Literal("1.5", datatype=XSD.decimal)))
    result = read_effective_cardinality(graph, EX.value, EX.Owner)
    assert result["max_count"] is None
    assert result["constraint_status"] == "constraint_unresolved"


def test_clearing_owned_counts_preserves_independent_owl_structures():
    base = Graph()
    base.add((EX.Owner, RDF.type, OWL.Class))
    base.add((EX.value, RDF.type, OWL.DatatypeProperty))
    emit_cardinality_config(base, EX.value, EX.Owner, "single", 1, 1)
    external = _restriction(base, EX.Owner, EX.value, OWL.maxCardinality, 5)
    equivalent = BNode()
    base.add((EX.Owner, OWL.equivalentClass, equivalent))
    base.add((equivalent, OWL.someValuesFrom, EX.Foreign))
    chain_head = BNode()
    base.add((EX.relation, OWL.propertyChainAxiom, chain_head))
    Collection(base, chain_head, [EX.first, EX.second])
    swrl = Namespace("http://www.w3.org/2003/11/swrl#")
    rule = BNode()
    base.add((rule, RDF.type, swrl.Imp))
    base.add((rule, swrl.body, chain_head))
    preserved = {
        triple for triple in base
        if triple[0] in {external, equivalent, chain_head, rule}
        or triple == (EX.Owner, RDFS.subClassOf, external)
        or triple == (EX.Owner, OWL.equivalentClass, equivalent)
        or triple == (EX.relation, OWL.propertyChainAxiom, chain_head)
    }
    managed = Graph()
    managed.add((EX.Owner, RDF.type, OWL.Class))
    managed.add((EX.value, RDF.type, OWL.DatatypeProperty))
    emit_cardinality_config(managed, EX.value, EX.Owner, "unspecified", None, None)
    merged = surgical_merge(base, managed, {EX.Owner, EX.value})
    assert preserved <= set(merged)
    assert not list(merged.subjects(CORE.cardinalityOwner, EX.value))
    assert (EX.value, RDF.type, OWL.FunctionalProperty) not in merged
    assert read_effective_cardinality(merged, EX.value, EX.Owner)["max_count"] == 5


def test_moving_domain_does_not_leave_old_owned_constraints():
    base, managed = Graph(), Graph()
    emit_cardinality_config(base, EX.value, EX.OldOwner, "multiple", 1, 3)
    emit_cardinality_config(managed, EX.value, EX.NewOwner, "multiple", 0, 4)
    merged = surgical_merge(base, managed, {EX.value, EX.NewOwner})
    assert not list(merged.objects(EX.OldOwner, RDFS.subClassOf))
    assert read_effective_cardinality(merged, EX.value, EX.NewOwner)["max_count"] == 4


def test_diff_contains_changed_blank_node_axioms_and_ignores_relabeling():
    old, new = Graph(), Graph()
    emit_cardinality_config(old, EX.value, EX.Owner, "multiple", 1, 3)
    emit_cardinality_config(new, EX.value, EX.Owner, "multiple", 1, 5)
    assert diff_graphs(old, _reparse(old)) == ([], [])
    added, removed = diff_graphs(old, new)
    assert any(f"<{OWL.maxCardinality}>" in triple and '"5"' in triple for triple in added)
    assert any(f"<{OWL.maxCardinality}>" in triple and '"3"' in triple for triple in removed)


def test_release_overlay_does_not_resurrect_cleared_module_configuration(tmp_path):
    original, release = Graph(), Graph()
    original.add((EX.value, RDF.type, OWL.DatatypeProperty))
    emit_cardinality_config(original, EX.value, EX.Owner, "single", 1, 1)
    _restriction(original, EX.Owner, EX.value, OWL.maxCardinality, 5)
    for triple in original:
        release.add(triple)
    remove_cardinality_config(release, EX.value)
    emit_cardinality_config(release, EX.value, EX.Owner, "unspecified", None, None)
    original.serialize(tmp_path / "original.ttl", format="turtle")
    release.serialize(tmp_path / "slpra_managed.ttl", format="turtle")
    loaded = load_base_graph(tmp_path)
    assert read_cardinality_config(loaded, EX.value)["multiplicity"] == "unspecified"
    assert (EX.value, RDF.type, OWL.FunctionalProperty) not in loaded
    assert not list(loaded.subjects(CORE.cardinalityOwner, EX.value))
    assert read_effective_cardinality(loaded, EX.value, EX.Owner)["max_count"] == 5


def test_moved_domain_release_reloads_and_seeds_only_the_released_domain(
    db, fake_engine, tmp_path, monkeypatch,
):
    from app.config import settings
    from app.services.ontology_meta_store import OntologyMetaStore

    original, managed = Graph(), Graph()
    for graph in (original, managed):
        graph.add((CORE.OldOwner, RDF.type, OWL.Class))
        graph.add((CORE.NewOwner, RDF.type, OWL.Class))
        graph.add((CORE.value, RDF.type, OWL.DatatypeProperty))
    original.add((CORE.value, RDFS.domain, CORE.OldOwner))
    emit_cardinality_config(original, CORE.value, CORE.OldOwner, "multiple", 1, 3)
    managed.add((CORE.value, RDFS.domain, CORE.NewOwner))
    emit_cardinality_config(managed, CORE.value, CORE.NewOwner, "multiple", 0, 4)
    release = surgical_merge(original, managed, {CORE.value, CORE.NewOwner, CORE.OldOwner})
    original.serialize(tmp_path / "original.ttl", format="turtle")
    release.serialize(tmp_path / "slpra_managed.ttl", format="turtle")
    loaded = load_base_graph(tmp_path)
    assert list(loaded.objects(CORE.value, RDFS.domain)) == [CORE.NewOwner]
    assert not list(loaded.objects(CORE.OldOwner, RDFS.subClassOf))
    monkeypatch.setattr(settings, "ontology_dir", tmp_path)
    OntologyMetaStore(db=db, engine=fake_engine).project_from_ttl()
    prop = db.query(OntologyDataProperty).filter_by(slpra_iri=str(CORE.value)).one()
    owner = db.get(OntologyClass, prop.domain_class_id)
    assert owner.slpra_iri == str(CORE.NewOwner)
    assert (prop.multiplicity, prop.min_cardinality, prop.max_cardinality) == ("multiple", 0, 4)


def test_overlay_preserves_anonymous_domain_union_without_old_orphan_list():
    base, release = Graph(), Graph()
    base.add((EX.value, RDFS.domain, EX.OldOwner))
    domain, head = BNode(), BNode()
    release.add((EX.value, RDFS.domain, domain))
    release.add((domain, OWL.unionOf, head))
    Collection(release, head, [EX.FirstOwner, EX.SecondOwner])
    emit_cardinality_config(release, EX.value, None, "single", None, 1)
    apply_cardinality_overlay(base, release)
    assert list(base.objects(EX.value, RDFS.domain)) == [domain]
    assert list(base.items(base.value(domain, OWL.unionOf))) == [EX.FirstOwner, EX.SecondOwner]
    reparsed = _reparse(base)
    apply_cardinality_overlay(reparsed, release)
    assert isomorphic(base, reparsed)
