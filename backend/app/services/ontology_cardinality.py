"""Shared, conservative OWL cardinality projection and snapshot interpretation.

The declaration (unspecified/single/multiple) is kept separate from effective
OWL bounds. Absence of a functional axiom never declares a multi-value policy.
Only restrictions marked with ``cardinalityOwner`` are owned by this editor.
"""

from __future__ import annotations

from hashlib import sha256

from rdflib import OWL, RDF, RDFS, XSD, BNode, Graph, Literal, Namespace, URIRef

CORE = Namespace("https://ontology.pharma-gmp.cn/slpra/core/")
CARDINALITY_PREDICATES = frozenset({
    CORE.multiplicity, CORE.minCardinality, CORE.maxCardinality, CORE.cardinalityConfigVersion,
})
_MULTIPLICITIES = {"unspecified", "single", "multiple"}
_SIMPLE_BOUNDS = (OWL.minCardinality, OWL.maxCardinality, OWL.cardinality)
_QUALIFIED_BOUNDS = (
    OWL.minQualifiedCardinality, OWL.maxQualifiedCardinality, OWL.qualifiedCardinality,
)


def _count(value: object) -> int:
    """Reject malformed OWL numbers rather than silently truncating them."""
    parsed = value.toPython() if isinstance(value, Literal) else value
    if isinstance(parsed, bool) or not isinstance(parsed, int) or parsed < 0:
        raise ValueError(f"Invalid non-negative cardinality: {value!r}")
    return parsed


def _one(graph: Graph, subject: URIRef, predicate: URIRef) -> object | None:
    values = set(graph.objects(subject, predicate))
    if len(values) > 1:
        raise ValueError(f"Conflicting cardinality declarations: {subject} {predicate}")
    return next(iter(values), None)


def read_cardinality_config(graph: Graph, property_ref: URIRef) -> dict:
    """Read the editable declaration, falling back only to legacy Functional."""
    declaration = _one(graph, property_ref, CORE.multiplicity)
    functional = (property_ref, RDF.type, OWL.FunctionalProperty) in graph
    multiplicity = str(declaration) if declaration is not None else (
        "single" if functional else "unspecified"
    )
    if multiplicity not in _MULTIPLICITIES:
        raise ValueError(f"Invalid multiplicity for {property_ref}: {multiplicity}")
    lower = _one(graph, property_ref, CORE.minCardinality)
    upper = _one(graph, property_ref, CORE.maxCardinality)
    minimum = _count(lower) if lower is not None else None
    maximum = _count(upper) if upper is not None else None
    if multiplicity == "single" and maximum is None:
        maximum = 1
    return {
        "multiplicity": multiplicity,
        "min_cardinality": minimum,
        "max_cardinality": maximum,
    }


def emit_cardinality_config(
    graph: Graph,
    property_ref: URIRef,
    domain_ref: URIRef | None,
    multiplicity: str,
    min_cardinality: int | None,
    max_cardinality: int | None,
) -> None:
    """Emit the versioned declaration and its owned, unqualified OWL axioms.

    Caller validates metadata combinations. Single emits an optional maximum of
    one even without a domain; it never emits a required minimum by itself.
    """
    if multiplicity not in _MULTIPLICITIES:
        raise ValueError(f"Invalid multiplicity: {multiplicity}")
    if multiplicity == "single":
        if max_cardinality not in (None, 1):
            raise ValueError("Single-valued property must have maximum 1")
        max_cardinality = 1
    if min_cardinality is not None:
        min_cardinality = _count(min_cardinality)
    if max_cardinality is not None:
        max_cardinality = _count(max_cardinality)
    if multiplicity == "multiple" and max_cardinality is not None and max_cardinality < 2:
        raise ValueError("Multi-valued property maximum must be at least 2")
    if (min_cardinality is not None and max_cardinality is not None
            and min_cardinality > max_cardinality):
        raise ValueError("Minimum cardinality exceeds maximum")
    if domain_ref is None and (
        min_cardinality is not None
        or (max_cardinality is not None and multiplicity != "single")
    ):
        raise ValueError("Numeric cardinality requires a named domain class")
    graph.add((property_ref, CORE.cardinalityConfigVersion, Literal(1, datatype=XSD.integer)))
    graph.add((property_ref, CORE.multiplicity, Literal(multiplicity)))
    if multiplicity == "single":
        graph.add((property_ref, RDF.type, OWL.FunctionalProperty))
    for name, value, annotation, predicate in (
        ("min", min_cardinality, CORE.minCardinality, OWL.minCardinality),
        ("max", max_cardinality, CORE.maxCardinality, OWL.maxCardinality),
    ):
        if value is None:
            continue
        graph.add((property_ref, annotation, Literal(value, datatype=XSD.nonNegativeInteger)))
        if domain_ref is None:
            continue  # FunctionalProperty already carries global max=1.
        identity = sha256(f"{property_ref}\n{domain_ref}\n{name}".encode()).hexdigest()
        restriction = BNode(f"cardinality-{identity}")
        graph.add((domain_ref, RDFS.subClassOf, restriction))
        graph.add((restriction, RDF.type, OWL.Restriction))
        graph.add((restriction, OWL.onProperty, property_ref))
        graph.add((restriction, CORE.cardinalityOwner, property_ref))
        graph.add((restriction, predicate, Literal(value, datatype=XSD.nonNegativeInteger)))


def remove_cardinality_config(
    graph: Graph, property_ref: URIRef, *, remove_functional: bool = False,
) -> None:
    """Remove this configuration, preserving all independently authored axioms.

    ``remove_functional`` is for a metadata edit explicitly taking ownership of
    a legacy functional flag. By default only a previous single declaration
    owns the functional axiom.
    """
    owned_functional = (property_ref, CORE.multiplicity, Literal("single")) in graph
    for restriction in list(graph.subjects(CORE.cardinalityOwner, property_ref)):
        graph.remove((None, RDFS.subClassOf, restriction))
        graph.remove((restriction, None, None))
    for predicate in CARDINALITY_PREDICATES:
        graph.remove((property_ref, predicate, None))
    if owned_functional or remove_functional:
        graph.remove((property_ref, RDF.type, OWL.FunctionalProperty))


def apply_cardinality_overlay(graph: Graph, overlay: Graph) -> None:
    """Use versioned release declarations over earlier module definitions.

    Releases are complete merged files alongside older module files. A plain
    RDF union would resurrect a cleared FunctionalProperty or old owned bound.
    Only this explicitly versioned configuration is overlaid; other axioms in
    the module graphs remain intact.
    """
    for property_ref in set(overlay.subjects(CORE.cardinalityConfigVersion, None)):
        remove_cardinality_config(graph, property_ref, remove_functional=True)
        # Quantity restrictions belong to the released domain. A module/release
        # RDF union would otherwise leave both the old and new named domains,
        # changing their OWL meaning to an intersection and making reseeding
        # choose a domain arbitrarily. Anonymous union domains are copied whole.
        old_domains = list(graph.objects(property_ref, RDFS.domain))
        graph.remove((property_ref, RDFS.domain, None))
        for old_domain in old_domains:
            if not isinstance(old_domain, BNode):
                continue
            closure = _anonymous_triples(graph, old_domain)
            nodes = {triple[0] for triple in closure}
            if not any(subject not in nodes for node in nodes
                       for subject in graph.subjects(None, node)):
                for triple in closure:
                    graph.remove(triple)
        for domain in overlay.objects(property_ref, RDFS.domain):
            graph.add((property_ref, RDFS.domain, domain))
            if isinstance(domain, BNode):
                for triple in _anonymous_triples(overlay, domain):
                    graph.add(triple)
        for predicate in CARDINALITY_PREDICATES:
            for value in overlay.objects(property_ref, predicate):
                graph.add((property_ref, predicate, value))
        if (property_ref, RDF.type, OWL.FunctionalProperty) in overlay:
            graph.add((property_ref, RDF.type, OWL.FunctionalProperty))
        for restriction in overlay.subjects(CORE.cardinalityOwner, property_ref):
            for triple in overlay.triples((restriction, None, None)):
                graph.add(triple)
            for owner in overlay.subjects(RDFS.subClassOf, restriction):
                graph.add((owner, RDFS.subClassOf, restriction))


def _anonymous_triples(graph: Graph, root: BNode) -> set:
    triples: set = set()
    seen: set = set()
    pending = [root]
    while pending:
        node = pending.pop()
        if node in seen:
            continue
        seen.add(node)
        for triple in graph.triples((node, None, None)):
            triples.add(triple)
            if isinstance(triple[2], BNode):
                pending.append(triple[2])
    return triples


def read_effective_cardinality(
    graph: Graph, property_ref: URIRef, class_ref: URIRef,
) -> dict:
    """Intersect global functionality and simple class/ancestor OWL bounds.

    Qualified restrictions limit only matching values, so this flat snapshot
    must mark them unresolved instead of using their maximum for all values.
    Unions are not conjunctions and are never flattened into stronger bounds.
    """
    status = "resolved"
    try:
        config = read_cardinality_config(graph, property_ref)
        multiplicity = config["multiplicity"]
    except ValueError:
        multiplicity = "unspecified"
        config = {}
        status = "constraint_unresolved"
    lower: list[int] = []
    upper: list[int] = []
    functional = (property_ref, RDF.type, OWL.FunctionalProperty) in graph
    if functional or multiplicity == "single":
        upper.append(1)
    if functional and multiplicity == "multiple":
        status = "constraint_unresolved"

    seen: set = set()
    pending = [class_ref]
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        for parent in graph.objects(current, RDFS.subClassOf):
            if isinstance(parent, URIRef):
                pending.append(parent)
            elif isinstance(parent, BNode):
                pending.append(parent)
        # Equivalent expressions are necessary as well as sufficient. Follow
        # both RDF directions of the symmetric relation, keeping the same
        # conservative intersection-only treatment below.
        pending.extend(graph.objects(current, OWL.equivalentClass))
        pending.extend(graph.subjects(OWL.equivalentClass, current))
        # Intersection conjuncts are necessarily true; union members are not.
        for head in graph.objects(current, OWL.intersectionOf):
            try:
                pending.extend(graph.items(head))
            except ValueError:
                status = "constraint_unresolved"
        if (current, OWL.onProperty, property_ref) not in graph:
            continue
        if any(graph.objects(current, OWL.onClass)) or any(
            graph.objects(current, OWL.onDataRange)
        ) or any(any(graph.objects(current, pred)) for pred in _QUALIFIED_BOUNDS):
            status = "constraint_unresolved"
            continue
        for predicate in _SIMPLE_BOUNDS:
            for value in graph.objects(current, predicate):
                try:
                    count = _count(value)
                except ValueError:
                    status = "constraint_unresolved"
                    continue
                if predicate in (OWL.minCardinality, OWL.cardinality):
                    lower.append(count)
                if predicate in (OWL.maxCardinality, OWL.cardinality):
                    upper.append(count)

    # Annotations are editor declarations; numeric meaning is materialized as
    # OWL restrictions above, never applied outside the corresponding domain.
    minimum = max(lower) if lower else None
    maximum = min(upper) if upper else None
    if minimum is not None and maximum is not None and minimum > maximum:
        status = "constraint_unresolved"
    if multiplicity == "multiple" and maximum is not None and maximum < 2:
        status = "constraint_unresolved"
    if config.get("min_cardinality") is not None and config.get("max_cardinality") is not None:
        if config["min_cardinality"] > config["max_cardinality"]:
            status = "constraint_unresolved"
    if multiplicity == "single" and config.get("max_cardinality") != 1:
        status = "constraint_unresolved"
    return {
        "multiplicity": multiplicity,
        "min_count": minimum,
        "max_count": maximum,
        "constraint_status": status,
    }
