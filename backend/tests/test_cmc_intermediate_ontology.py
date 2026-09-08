"""CMC synthesis intermediate T-Box and runtime schema contracts.

The product-level ``hasProcessIntermediate`` relation is derived through the
route/final-step that actually produces the product.  It must not be inferred
merely because a report mentions both a product and a synthesis route: one
report may describe multiple products and routes.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from rdflib import OWL, RDF, RDFS, XSD, BNode, Graph, Namespace, URIRef

from app.services.extraction.ontology_guided.contracts import SubjectRef
from app.services.extraction.ontology_guided.ontology_plan import (
    compile_local_menu,
    ontology_snapshot_from_engine,
)
from app.services.ontology_engine import OntologyEngine
from app.services.ontology_instance_writer import EvidenceInstanceWriter

ONTOLOGY_DIR = Path(__file__).resolve().parents[2] / "ontology" / "slpra"
CMC_TTL = ONTOLOGY_DIR / "slpra-drug-development.ttl"
DRUG_TTL = ONTOLOGY_DIR / "slpra-drug.ttl"
INTEGRATION_TTL = ONTOLOGY_DIR / "slpra-integration.ttl"

DEV = Namespace("https://ontology.pharma-gmp.cn/slpra/drug-development/")
DRUG = Namespace("https://ontology.pharma-gmp.cn/slpra/drug/")
INTEGRATION = Namespace("https://ontology.pharma-gmp.cn/slpra/integration/")


@pytest.fixture(scope="module")
def cmc_graph() -> Graph:
    graph = Graph().parse(CMC_TTL, format="turtle")
    # rdflib does not follow owl:imports; load the referenced class hierarchy
    # and integration annotations explicitly.
    graph.parse(DRUG_TTL, format="turtle")
    graph.parse(INTEGRATION_TTL, format="turtle")
    return graph


def _union_members(graph: Graph, subject: URIRef, predicate: URIRef) -> set[URIRef]:
    """Return the members of the subject's single OWL union expression."""
    expressions = list(graph.objects(subject, predicate))
    assert len(expressions) == 1, f"{subject} must have exactly one {predicate} expression"
    list_head = graph.value(expressions[0], OWL.unionOf)
    assert list_head is not None, f"{subject} {predicate} must use owl:unionOf"
    return set(graph.items(list_head))


def _chain_expression(graph: Graph, node) -> tuple[str, URIRef]:
    """Normalize a named property or anonymous inverse property expression."""
    if isinstance(node, URIRef):
        return ("direct", node)
    assert isinstance(node, BNode), f"unsupported property-chain member: {node!r}"
    inverses = list(graph.objects(node, OWL.inverseOf))
    assert len(inverses) == 1, f"inverse expression {node} must name exactly one property"
    assert isinstance(inverses[0], URIRef)
    return ("inverse", inverses[0])


def _property_chains(graph: Graph, predicate: URIRef) -> set[tuple[tuple[str, URIRef], ...]]:
    return {
        tuple(_chain_expression(graph, member) for member in graph.items(head))
        for head in graph.objects(predicate, OWL.propertyChainAxiom)
    }


def test_final_product_relation_accepts_route_or_step_and_product_or_api(cmc_graph):
    predicate = DEV.producesFinalProduct

    assert (predicate, RDF.type, OWL.ObjectProperty) in cmc_graph
    assert _union_members(cmc_graph, predicate, RDFS.domain) == {
        DEV.SynthesisRoute,
        DEV.SynthesisStep,
    }
    assert _union_members(cmc_graph, predicate, RDFS.range) == {
        DRUG.DrugProduct,
        DRUG.ActivePharmaceuticalIngredient,
    }


def test_intermediate_identifier_is_a_string_on_process_intermediate(cmc_graph):
    predicate = DEV.intermediateIdentifier

    assert (predicate, RDF.type, OWL.DatatypeProperty) in cmc_graph
    assert (predicate, RDF.type, OWL.FunctionalProperty) not in cmc_graph
    assert (predicate, RDFS.domain, DEV.ProcessIntermediate) in cmc_graph
    assert (predicate, RDFS.range, XSD.string) in cmc_graph
    # A bare identifier is not globally unique across projects/routes.  The
    # current registry has no uniqueness-scope field, so this must remain off.
    assert list(cmc_graph.objects(predicate, INTEGRATION.identityKey)) == []


def test_product_intermediates_are_derived_through_the_actual_final_product_path(cmc_graph):
    predicate = DEV.hasProcessIntermediate

    assert (predicate, RDF.type, OWL.ObjectProperty) in cmc_graph
    assert _union_members(cmc_graph, predicate, RDFS.domain) == {
        DRUG.DrugProduct,
        DRUG.ActivePharmaceuticalIngredient,
    }
    assert (predicate, RDFS.range, DEV.ProcessIntermediate) in cmc_graph

    inverse_final = ("inverse", DEV.producesFinalProduct)
    direct_step = ("direct", DEV.hasStep)
    direct_intermediate = ("direct", DEV.producesIntermediate)
    assert _property_chains(cmc_graph, predicate) == {
        # Route --producesFinalProduct--> product/API.
        (inverse_final, direct_step, direct_intermediate),
        # Final step --producesFinalProduct--> product/API; first recover its route.
        (
            inverse_final,
            ("inverse", DEV.hasStep),
            direct_step,
            direct_intermediate,
        ),
    }


def test_product_intermediate_relation_is_optional_and_multi_valued(cmc_graph):
    """No functional/cardinality axiom may collapse the intended 0..* relation."""
    predicate = DEV.hasProcessIntermediate
    assert (predicate, RDF.type, OWL.FunctionalProperty) not in cmc_graph

    cardinality_predicates = {
        OWL.cardinality,
        OWL.minCardinality,
        OWL.maxCardinality,
        OWL.qualifiedCardinality,
        OWL.minQualifiedCardinality,
        OWL.maxQualifiedCardinality,
    }
    restrictions = set(cmc_graph.subjects(OWL.onProperty, predicate))
    assert not any(
        (restriction, cardinality_predicate, None) in cmc_graph
        for restriction in restrictions
        for cardinality_predicate in cardinality_predicates
    )


def test_step_ordering_remains_explicit_and_independent_of_collection_order(cmc_graph):
    assert (DEV.stepOrder, RDF.type, OWL.DatatypeProperty) in cmc_graph
    assert (DEV.stepOrder, RDFS.domain, DEV.SynthesisStep) in cmc_graph
    assert (DEV.stepOrder, RDFS.range, XSD.integer) in cmc_graph
    assert (DEV.nextStep, RDF.type, OWL.ObjectProperty) in cmc_graph
    assert (DEV.nextStep, RDFS.domain, DEV.SynthesisStep) in cmc_graph
    assert (DEV.nextStep, RDFS.range, DEV.SynthesisStep) in cmc_graph


def test_strict_writer_accepts_union_members_and_rejects_unrelated_classes(cmc_graph):
    final_domain = cmc_graph.value(DEV.producesFinalProduct, RDFS.domain)
    final_range = cmc_graph.value(DEV.producesFinalProduct, RDFS.range)
    intermediate_domain = cmc_graph.value(DEV.hasProcessIntermediate, RDFS.domain)

    for class_iri in (DEV.SynthesisRoute, DEV.SynthesisStep):
        assert EvidenceInstanceWriter._class_matches(cmc_graph, str(class_iri), final_domain)
    assert not EvidenceInstanceWriter._class_matches(
        cmc_graph, str(DEV.ProcessIntermediate), final_domain
    )

    # Include subclasses to exercise normal subclass closure inside each union arm.
    for class_iri in (
        DRUG.DrugProduct,
        DRUG.ClinicalTrialDrug,
        DRUG.ActivePharmaceuticalIngredient,
        DRUG.HighPotencyAPI,
    ):
        assert EvidenceInstanceWriter._class_matches(cmc_graph, str(class_iri), final_range)
        assert EvidenceInstanceWriter._class_matches(cmc_graph, str(class_iri), intermediate_domain)
    assert not EvidenceInstanceWriter._class_matches(
        cmc_graph, str(DEV.ProcessIntermediate), final_range
    )


def test_runtime_engine_exposes_new_cmc_properties(tmp_path):
    """Union domains/ranges must survive Owlready2 loading and schema projection."""
    engine = OntologyEngine(
        ontology_dir=ONTOLOGY_DIR,
        store_path=tmp_path / "cmc-intermediate.sqlite3",
    )
    try:
        engine.load()

        for domain in (str(DRUG.DrugProduct), str(DRUG.ActivePharmaceuticalIngredient)):
            properties = {
                definition["iri"]: definition
                for definition in engine.get_object_properties_by_domain(domain)
            }
            intermediate = properties[str(DEV.hasProcessIntermediate)]
            assert intermediate["range"] == [str(DEV.ProcessIntermediate)]
            assert intermediate["max_count"] is None

        expected_final_ranges = {
            str(DRUG.DrugProduct),
            str(DRUG.ActivePharmaceuticalIngredient),
        }
        for domain in (str(DEV.SynthesisRoute), str(DEV.SynthesisStep)):
            properties = {
                definition["iri"]: definition
                for definition in engine.get_object_properties_by_domain(domain)
            }
            assert set(properties[str(DEV.producesFinalProduct)]["range"]) == expected_final_ranges

        data_properties = {
            definition["iri"]: definition
            for definition in engine.get_data_properties_by_domain(str(DEV.ProcessIntermediate))
        }
        identifier = data_properties[str(DEV.intermediateIdentifier)]
        assert identifier["datatype"] == "string"
        assert identifier["max_count"] is None
        assert identifier.get("identity_key", False) is False

        # Owlready2 must retain both inverse-containing chains.  Merely loading
        # them does not run a reasoner or materialize A-Box facts.
        product_intermediate = engine._world.search_one(iri=str(DEV.hasProcessIntermediate))

        def normalize_chain_member(member):
            inverse = getattr(member, "property", None)
            return (
                "inverse" if inverse is not None else "direct",
                (inverse or member).iri,
            )

        runtime_chains = {
            tuple(normalize_chain_member(member) for member in chain.properties)
            for chain in product_intermediate.property_chain
        }
        assert runtime_chains == {
            (
                ("inverse", str(DEV.producesFinalProduct)),
                ("direct", str(DEV.hasStep)),
                ("direct", str(DEV.producesIntermediate)),
            ),
            (
                ("inverse", str(DEV.producesFinalProduct)),
                ("inverse", str(DEV.hasStep)),
                ("direct", str(DEV.hasStep)),
                ("direct", str(DEV.producesIntermediate)),
            ),
        }

        # The document-rooted relation schema is the extraction/UI menu source.
        edges = engine.get_relation_schema(str(DEV.CMCReport), max_hops=4)
        final_edges = {
            (edge["domain_class_iri"], edge["range_class_iri"])
            for edge in edges
            if edge["predicate_iri"] == str(DEV.producesFinalProduct)
        }
        assert final_edges == {
            (str(DEV.SynthesisRoute), str(DRUG.DrugProduct)),
            (str(DEV.SynthesisRoute), str(DRUG.ActivePharmaceuticalIngredient)),
            (str(DEV.SynthesisStep), str(DRUG.DrugProduct)),
            (str(DEV.SynthesisStep), str(DRUG.ActivePharmaceuticalIngredient)),
        }
        product_intermediate_edges = {
            (edge["domain_class_iri"], edge["range_class_iri"])
            for edge in edges
            if edge["predicate_iri"] == str(DEV.hasProcessIntermediate)
        }
        assert product_intermediate_edges == {
            (str(DRUG.DrugProduct), str(DEV.ProcessIntermediate)),
            (str(DRUG.ActivePharmaceuticalIngredient), str(DEV.ProcessIntermediate)),
        }
    finally:
        engine.close()


def test_authoritative_snapshot_compiles_union_domains_and_ranges_into_local_menus(
    tmp_path,
):
    """The frozen worker menu must preserve both arms of each approved union."""
    engine = OntologyEngine(
        ontology_dir=ONTOLOGY_DIR,
        store_path=tmp_path / "cmc-intermediate-menu.sqlite3",
    )
    try:
        engine.load()
        snapshot = ontology_snapshot_from_engine(engine, str(DEV.CMCReport))
        assert snapshot.created_from == "ontology_engine"

        final_range_arms = {
            str(DRUG.DrugProduct),
            str(DRUG.ActivePharmaceuticalIngredient),
        }
        for domain in (DEV.SynthesisRoute, DEV.SynthesisStep):
            declarations = [
                edge
                for edge in snapshot.classes[str(domain)].declared_relationships
                if edge.iri == str(DEV.producesFinalProduct)
            ]
            assert len(declarations) == 1
            assert declarations[0].declared_by == [str(domain)]
            assert set(declarations[0].range_class_iris) == final_range_arms

        for domain in (DRUG.DrugProduct, DRUG.ActivePharmaceuticalIngredient):
            declarations = [
                edge
                for edge in snapshot.classes[str(domain)].declared_relationships
                if edge.iri == str(DEV.hasProcessIntermediate)
            ]
            assert len(declarations) == 1
            assert declarations[0].declared_by == [str(domain)]
            assert declarations[0].range_class_iris == [str(DEV.ProcessIntermediate)]
            assert declarations[0].max_count is None

        def relationship(class_iri: URIRef, predicate_iri: URIRef):
            menu = compile_local_menu(
                snapshot,
                SubjectRef(
                    entity_id=f"subject:{class_iri}",
                    revision=1,
                    class_iri=str(class_iri),
                ),
            )
            matches = [edge for edge in menu.relationships if edge.iri == str(predicate_iri)]
            assert len(matches) == 1
            return matches[0]

        for domain in (DEV.SynthesisRoute, DEV.SynthesisStep):
            final_product = relationship(domain, DEV.producesFinalProduct)
            assert final_product.constraint_status == "resolved"
            assert final_product.declared_by == [str(domain)]
            assert final_range_arms <= set(final_product.range_class_iris)
            # Named union arms are expanded only to their real descendants.
            assert str(DRUG.ClinicalTrialDrug) in final_product.range_class_iris
            assert str(DRUG.HighPotencyAPI) in final_product.range_class_iris
            assert str(DEV.ProcessIntermediate) not in final_product.range_class_iris

        for domain in (DRUG.DrugProduct, DRUG.ActivePharmaceuticalIngredient):
            intermediates = relationship(domain, DEV.hasProcessIntermediate)
            assert intermediates.constraint_status == "resolved"
            assert intermediates.declared_by == [str(domain)]
            assert set(intermediates.range_class_iris) == {
                str(DEV.ProcessIntermediate),
                str(DEV.CrudeProduct),
            }
            assert intermediates.max_count is None

        intermediate_menu = compile_local_menu(
            snapshot,
            SubjectRef(
                entity_id="subject:process-intermediate",
                revision=1,
                class_iri=str(DEV.ProcessIntermediate),
            ),
        )
        identifiers = [
            slot
            for slot in intermediate_menu.properties
            if slot.iri == str(DEV.intermediateIdentifier)
        ]
        assert len(identifiers) == 1
        assert identifiers[0].datatype_iris == [str(XSD.string)]
        assert identifiers[0].max_count is None
        assert identifiers[0].identity_key is False

        step_menu = compile_local_menu(
            snapshot,
            SubjectRef(
                entity_id="subject:synthesis-step",
                revision=1,
                class_iri=str(DEV.SynthesisStep),
            ),
        )
        step_order = next(slot for slot in step_menu.properties if slot.iri == str(DEV.stepOrder))
        next_step = next(edge for edge in step_menu.relationships if edge.iri == str(DEV.nextStep))
        assert step_order.datatype_iris == [str(XSD.integer)]
        assert str(DEV.SynthesisStep) in next_step.range_class_iris

        # The union domain must not widen either relationship onto the report
        # or onto an unrelated range class.
        for unrelated in (DEV.CMCReport, DEV.ProcessIntermediate):
            menu = compile_local_menu(
                snapshot,
                SubjectRef(
                    entity_id=f"unrelated:{unrelated}",
                    revision=1,
                    class_iri=str(unrelated),
                ),
            )
            assert str(DEV.producesFinalProduct) not in {edge.iri for edge in menu.relationships}
            assert str(DEV.hasProcessIntermediate) not in {edge.iri for edge in menu.relationships}
    finally:
        engine.close()


def test_property_chains_are_not_materialized_without_running_a_reasoner(tmp_path):
    """Both complete base A-Box paths remain base facts until reasoning runs."""
    engine = OntologyEngine(
        ontology_dir=ONTOLOGY_DIR,
        store_path=tmp_path / "cmc-intermediate-abox.sqlite3",
    )
    try:
        engine.load()
        world = engine._world
        facts = world.get_ontology("https://example.test/cmc-intermediate-facts/")
        route_class = world.search_one(iri=str(DEV.SynthesisRoute))
        step_class = world.search_one(iri=str(DEV.SynthesisStep))
        product_class = world.search_one(iri=str(DRUG.DrugProduct))
        intermediate_class = world.search_one(iri=str(DEV.ProcessIntermediate))
        has_step = world.search_one(iri=str(DEV.hasStep))
        produces_final = world.search_one(iri=str(DEV.producesFinalProduct))
        produces_intermediate = world.search_one(iri=str(DEV.producesIntermediate))
        has_intermediate = world.search_one(iri=str(DEV.hasProcessIntermediate))

        with facts:
            route = route_class("route-final")
            route_step = step_class("route-producing-step")
            route_product = product_class("route-product")
            route_intermediate = intermediate_class("route-intermediate")
            has_step[route].append(route_step)
            produces_final[route].append(route_product)
            produces_intermediate[route_step].append(route_intermediate)

            step_route = route_class("step-final-route")
            final_step = step_class("final-step")
            producing_step = step_class("other-producing-step")
            step_product = product_class("step-product")
            step_intermediate = intermediate_class("step-intermediate")
            has_step[step_route].extend([final_step, producing_step])
            produces_final[final_step].append(step_product)
            produces_intermediate[producing_step].append(step_intermediate)

        graph = world.as_rdflib_graph()
        premise_triples = {
            (
                URIRef(route.iri),
                DEV.producesFinalProduct,
                URIRef(route_product.iri),
            ),
            (URIRef(route.iri), DEV.hasStep, URIRef(route_step.iri)),
            (
                URIRef(route_step.iri),
                DEV.producesIntermediate,
                URIRef(route_intermediate.iri),
            ),
            (URIRef(step_route.iri), DEV.hasStep, URIRef(final_step.iri)),
            (URIRef(step_route.iri), DEV.hasStep, URIRef(producing_step.iri)),
            (
                URIRef(final_step.iri),
                DEV.producesFinalProduct,
                URIRef(step_product.iri),
            ),
            (
                URIRef(producing_step.iri),
                DEV.producesIntermediate,
                URIRef(step_intermediate.iri),
            ),
        }
        assert premise_triples <= set(graph)

        derived_triples = {
            (
                URIRef(route_product.iri),
                DEV.hasProcessIntermediate,
                URIRef(route_intermediate.iri),
            ),
            (
                URIRef(step_product.iri),
                DEV.hasProcessIntermediate,
                URIRef(step_intermediate.iri),
            ),
        }
        # No sync_reasoner call occurs: the production World contains only the
        # premises, and neither property-chain entailment is presented as an
        # explicitly materialized fact.
        assert derived_triples.isdisjoint(set(graph))
        assert list(has_intermediate[route_product]) == []
        assert list(has_intermediate[step_product]) == []
    finally:
        engine.close()
