"""Surgical-merge round-trip fidelity (T009, 宪章 II NON-NEGOTIABLE).

A managed class carrying simultaneously:
  • a **named-IRI external alignment** (`owl:equivalentClass <ChEBI…>`),
  • a **BNode criterion axiom** (`owl:equivalentClass _:c… [restriction]`), and
  • an **unmodelled annotation** (a predicate the workbench does not own),
plus a stale workbench BNode axiom that must be reclaimed.

Two consecutive export→parse→export cycles MUST be isomorphic (stable triple
set), the alignment and annotation MUST survive verbatim, the stale BNode
subgraph MUST be reclaimed (no orphan accumulation), and the deterministic
managed BNode MUST be re-emitted identically.
"""

from __future__ import annotations

from rdflib import OWL, RDF, RDFS, BNode, Graph, Literal, Namespace, URIRef
from rdflib.collection import Collection
from rdflib.compare import isomorphic

from app.services.ttl_merge import surgical_merge

DRUG = Namespace("https://ontology.pharma-gmp.cn/slpra/drug/")
EX = Namespace("http://example.org/curation/")
CHEBI = URIRef("http://purl.obolibrary.org/obo/CHEBI_35610")

C = URIRef(DRUG.CytotoxicDrug)
P = URIRef(DRUG.hasToxicityProfile)
FILLER = URIRef(DRUG.GenotoxicityProfile)


def _base_graph() -> Graph:
    """On-disk authoritative graph as a curator might have left it."""
    g = Graph()
    g.add((C, RDF.type, OWL.Class))
    g.add((C, RDFS.label, Literal("旧标签-应被受管覆盖")))
    # named-IRI external alignment — MUST be preserved verbatim
    g.add((C, OWL.equivalentClass, CHEBI))
    # unmodelled annotation (predicate not in MANAGED_PREDICATES) — preserve
    g.add((C, EX.curatorNote, Literal("preserve me verbatim")))
    # stale workbench BNode axiom — MUST be reclaimed (orphan prevention)
    stale = BNode("stale-old")
    g.add((C, OWL.equivalentClass, stale))
    g.add((stale, RDF.type, OWL.Restriction))
    g.add((stale, OWL.onProperty, P))
    g.add((stale, OWL.someValuesFrom, URIRef(DRUG.ObsoleteFiller)))
    # a fully unmodelled subject — must survive untouched
    g.add((URIRef(DRUG.SomethingElse), EX.foo, Literal("bar")))
    return g


def _managed_graph() -> Graph:
    """Workbench projection: deterministic BNode criterion + fresh label."""
    g = Graph()
    g.add((C, RDF.type, OWL.Class))
    g.add((C, RDFS.label, Literal("Cytotoxic Drug")))
    cexpr = BNode("c0001")  # deterministic, as build_managed_graph derives from PK
    g.add((C, OWL.equivalentClass, cexpr))
    g.add((cexpr, RDF.type, OWL.Restriction))
    g.add((cexpr, OWL.onProperty, P))
    g.add((cexpr, OWL.someValuesFrom, FILLER))
    return g


def _reparse(g: Graph) -> Graph:
    out = Graph()
    out.parse(data=g.serialize(format="turtle"), format="turtle")
    return out


def _add_union_constraint(
    graph: Graph,
    subject: URIRef,
    predicate: URIRef,
    name: str,
    members: list[URIRef],
) -> BNode:
    expression = BNode(f"{name}-expression")
    head = BNode(f"{name}-list")
    graph.add((subject, predicate, expression))
    graph.add((expression, RDF.type, OWL.Class))
    graph.add((expression, OWL.unionOf, head))
    Collection(graph, head, members)
    return expression


def _union_members(graph: Graph, subject: URIRef, predicate: URIRef) -> set[URIRef]:
    expression = graph.value(subject, predicate)
    assert isinstance(expression, BNode)
    head = graph.value(expression, OWL.unionOf)
    assert head is not None
    return set(graph.items(head))


def _add_property_chain(
    graph: Graph,
    predicate: URIRef,
    name: str,
    members: list[URIRef | BNode],
) -> None:
    head = BNode(f"{name}-list")
    graph.add((predicate, OWL.propertyChainAxiom, head))
    Collection(graph, head, members)


def _property_chains(graph: Graph, predicate: URIRef) -> set[tuple[tuple[str, URIRef], ...]]:
    chains = set()
    for head in graph.objects(predicate, OWL.propertyChainAxiom):
        members = []
        for member in graph.items(head):
            inverse = graph.value(member, OWL.inverseOf)
            if inverse is not None:
                assert isinstance(inverse, URIRef)
                members.append(("inverse", inverse))
            else:
                assert isinstance(member, URIRef)
                members.append(("direct", member))
        chains.add(tuple(members))
    return chains


def test_roundtrip_is_stable_and_preserves_external_axioms():
    base = _base_graph()
    managed = _managed_graph()
    subjects = {C}

    merged1 = surgical_merge(base, managed, subjects)
    merged2 = surgical_merge(_reparse(merged1), managed, subjects)

    # 1. Triple set stable across export→parse→export (BNode-aware).
    assert isomorphic(merged1, merged2), "round-trip not idempotent"

    for merged in (merged1, merged2):
        # 2. Named-IRI external alignment survives verbatim.
        assert (C, OWL.equivalentClass, CHEBI) in merged
        # 3. Unmodelled annotation survives verbatim.
        assert (C, EX.curatorNote, Literal("preserve me verbatim")) in merged
        # 3b. Unmodelled subject untouched.
        assert (URIRef(DRUG.SomethingElse), EX.foo, Literal("bar")) in merged
        # 4. Workbench-managed label replaced (old stripped, new emitted).
        assert (C, RDFS.label, Literal("Cytotoxic Drug")) in merged
        assert (C, RDFS.label, Literal("旧标签-应被受管覆盖")) not in merged
        # 5. Stale BNode axiom reclaimed — no orphan, obsolete filler gone.
        assert (None, OWL.someValuesFrom, URIRef(DRUG.ObsoleteFiller)) not in merged
        # 6. Deterministic managed criterion BNode re-emitted intact.
        assert (C, OWL.someValuesFrom, None) not in merged  # it hangs off a BNode
        eq_bnodes = [o for o in merged.objects(C, OWL.equivalentClass) if isinstance(o, BNode)]
        assert len(eq_bnodes) == 1  # exactly the managed class expression
        (cexpr,) = eq_bnodes
        assert (cexpr, OWL.someValuesFrom, FILLER) in merged
        assert (cexpr, OWL.onProperty, P) in merged


def test_union_and_property_chains_survive_when_metadata_cannot_replace_them():
    prop = URIRef(DRUG.producesFinalProduct)
    derived_prop = URIRef(DRUG.hasProcessIntermediate)
    route = URIRef(DRUG.SynthesisRoute)
    step = URIRef(DRUG.SynthesisStep)
    product = URIRef(DRUG.DrugProduct)
    api = URIRef(DRUG.ActivePharmaceuticalIngredient)
    has_step = URIRef(DRUG.hasStep)
    produces_intermediate = URIRef(DRUG.producesIntermediate)
    base = Graph()
    base.add((prop, RDF.type, OWL.ObjectProperty))
    _add_union_constraint(base, prop, RDFS.domain, "domain", [route, step])
    _add_union_constraint(base, prop, RDFS.range, "range", [product, api])
    base.add((derived_prop, RDF.type, OWL.ObjectProperty))
    inverse_final_route = BNode("inverse-final-route")
    inverse_final_step = BNode("inverse-final-step")
    inverse_has_step = BNode("inverse-has-step")
    base.add((inverse_final_route, OWL.inverseOf, prop))
    base.add((inverse_final_step, OWL.inverseOf, prop))
    base.add((inverse_has_step, OWL.inverseOf, has_step))
    _add_property_chain(
        base,
        derived_prop,
        "route-chain",
        [inverse_final_route, has_step, produces_intermediate],
    )
    _add_property_chain(
        base,
        derived_prop,
        "final-step-chain",
        [inverse_final_step, inverse_has_step, has_step, produces_intermediate],
    )
    managed = Graph()
    managed.add((prop, RDF.type, OWL.ObjectProperty))
    managed.add((prop, RDFS.label, Literal("managed label")))
    managed.add((derived_prop, RDF.type, OWL.ObjectProperty))
    managed.add((derived_prop, RDFS.label, Literal("managed derived label")))

    managed_subjects = {prop, derived_prop}
    merged1 = surgical_merge(base, managed, managed_subjects)
    merged2 = surgical_merge(_reparse(merged1), managed, managed_subjects)

    assert isomorphic(merged1, merged2)
    expected_chains = {
        (
            ("inverse", prop),
            ("direct", has_step),
            ("direct", produces_intermediate),
        ),
        (
            ("inverse", prop),
            ("inverse", has_step),
            ("direct", has_step),
            ("direct", produces_intermediate),
        ),
    }
    for merged in (merged1, merged2):
        assert _union_members(merged, prop, RDFS.domain) == {route, step}
        assert _union_members(merged, prop, RDFS.range) == {product, api}
        assert _property_chains(merged, derived_prop) == expected_chains


def test_named_domain_replacement_reclaims_only_the_old_domain_union():
    prop = URIRef(DRUG.producesFinalProduct)
    old_route = URIRef(DRUG.OldRoute)
    old_step = URIRef(DRUG.OldStep)
    product = URIRef(DRUG.DrugProduct)
    api = URIRef(DRUG.ActivePharmaceuticalIngredient)
    replacement = URIRef(DRUG.SynthesisRoute)
    base = Graph()
    old_domain = _add_union_constraint(base, prop, RDFS.domain, "old-domain", [old_route, old_step])
    _add_union_constraint(base, prop, RDFS.range, "preserved-range", [product, api])
    managed = Graph()
    managed.add((prop, RDFS.domain, replacement))

    merged = surgical_merge(base, managed, {prop})

    assert list(merged.objects(prop, RDFS.domain)) == [replacement]
    assert not list(merged.triples((old_domain, None, None)))
    assert not list(merged.triples((None, RDF.first, old_route)))
    assert not list(merged.triples((None, RDF.first, old_step)))
    assert _union_members(merged, prop, RDFS.range) == {product, api}
