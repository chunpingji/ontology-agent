"""Surgical TTL merge + triple-level diff (R3, FR-009a).

The metadata tables are the editing source of truth. To materialise the
authoritative TTL we build a *managed* RDF graph from those tables and merge it
**surgically** into the existing on-disk ontology: for every managed subject we
replace only the whitelisted predicates the workbench owns, leaving every other
triple (un-modelled axioms, annotations authored outside the workbench) byte-for
-byte intact. A triple-level diff is produced before any write so a reviewer can
see exactly what the release changes.

外科式合并：仅替换工作台受管的谓词，逐字保留未建模三元组；写入前给出三元组级 diff。
"""

from __future__ import annotations

import logging
from pathlib import Path

from rdflib import RDF, RDFS, OWL, XSD, BNode, Graph, Literal, Namespace, URIRef
from sqlalchemy.orm import Session

from app.models.ontology_meta import (
    OntologyAction,
    OntologyClass,
    OntologyDataProperty,
    OntologyLinkType,
    OntologyRestriction,
)

logger = logging.getLogger(__name__)

SLPRA = Namespace("https://ontology.pharma-gmp.cn/slpra/core/")

# Predicates the workbench *owns* for any managed subject. On merge these are
# stripped from the base graph for managed subjects and re-emitted from the
# metadata; everything else on those subjects is preserved verbatim.
MANAGED_PREDICATES = frozenset(
    {
        RDF.type,
        RDFS.label,
        RDFS.comment,
        RDFS.subClassOf,
        RDFS.domain,
        RDFS.range,
        OWL.inverseOf,
        SLPRA.actor,
        SLPRA.target,
    }
)

_XSD = {
    "string": XSD.string,
    "integer": XSD.integer,
    "decimal": XSD.decimal,
    "boolean": XSD.boolean,
    "date": XSD.date,
    "dateTime": XSD.dateTime,
    "anyURI": XSD.anyURI,
}


# --------------------------------------------------------------------------- #
# Building the managed graph from the metadata tables
# --------------------------------------------------------------------------- #
def build_managed_graph(db: Session) -> tuple[Graph, set[URIRef]]:
    """Return (graph, managed_subjects) projected from the metadata tables.

    Disabled entities are skipped (they are retained in the DB for traceability
    but must not be emitted into the authoritative TTL).
    """
    g = Graph()
    g.bind("slpra", SLPRA)
    g.bind("owl", OWL)
    subjects: set[URIRef] = set()

    # id -> IRI map for FK resolution (classes & link types referenced by id).
    class_iri: dict = {}
    for c in db.query(OntologyClass).all():
        class_iri[c.id] = c.slpra_iri
    link_iri: dict = {}
    for lt in db.query(OntologyLinkType).all():
        link_iri[lt.id] = lt.slpra_iri
    dp_iri: dict = {}
    for dp in db.query(OntologyDataProperty).all():
        dp_iri[dp.id] = dp.slpra_iri

    # E1 classes
    for c in db.query(OntologyClass).filter_by(is_disabled=False).all():
        s = URIRef(c.slpra_iri)
        subjects.add(s)
        g.add((s, RDF.type, OWL.Class))
        _label(g, s, c.label, c.comment)
        if c.parent_class_id and c.parent_class_id in class_iri:
            g.add((s, RDFS.subClassOf, URIRef(class_iri[c.parent_class_id])))

    # E2 object properties / relations
    for lt in db.query(OntologyLinkType).filter_by(is_disabled=False).all():
        s = URIRef(lt.slpra_iri)
        subjects.add(s)
        g.add((s, RDF.type, OWL.ObjectProperty))
        _label(g, s, lt.label, lt.comment)
        if lt.domain_class_id and lt.domain_class_id in class_iri:
            g.add((s, RDFS.domain, URIRef(class_iri[lt.domain_class_id])))
        if lt.range_class_id and lt.range_class_id in class_iri:
            g.add((s, RDFS.range, URIRef(class_iri[lt.range_class_id])))
        if lt.inverse_link_id and lt.inverse_link_id in link_iri:
            g.add((s, OWL.inverseOf, URIRef(link_iri[lt.inverse_link_id])))
        if lt.is_functional:
            g.add((s, RDF.type, OWL.FunctionalProperty))
        if lt.is_symmetric:
            g.add((s, RDF.type, OWL.SymmetricProperty))
        if lt.is_transitive:
            g.add((s, RDF.type, OWL.TransitiveProperty))

    # E3 data properties
    for dp in db.query(OntologyDataProperty).filter_by(is_disabled=False).all():
        s = URIRef(dp.slpra_iri)
        subjects.add(s)
        g.add((s, RDF.type, OWL.DatatypeProperty))
        _label(g, s, dp.label, dp.comment)
        if dp.domain_class_id and dp.domain_class_id in class_iri:
            g.add((s, RDFS.domain, URIRef(class_iri[dp.domain_class_id])))
        g.add((s, RDFS.range, _XSD.get(dp.datatype, XSD.string)))

    # E4 actions (definition only, R10) — typed as slpra:Action for round-trip
    for a in db.query(OntologyAction).filter_by(is_disabled=False).all():
        s = URIRef(a.slpra_iri)
        subjects.add(s)
        g.add((s, RDF.type, SLPRA.Action))
        _label(g, s, a.label, a.comment)
        if a.actor_class_id and a.actor_class_id in class_iri:
            g.add((s, SLPRA.actor, URIRef(class_iri[a.actor_class_id])))
        if a.target_class_id and a.target_class_id in class_iri:
            g.add((s, SLPRA.target, URIRef(class_iri[a.target_class_id])))

    # E5 restrictions → owl:Restriction blank nodes hung off the owner class.
    # Deterministic BNode ids (from the restriction PK) keep diffs stable.
    prop_iri = {**link_iri, **dp_iri}
    for r in db.query(OntologyRestriction).all():
        owner = class_iri.get(r.owner_class_id)
        if not owner:
            continue
        owner_ref = URIRef(owner)
        subjects.add(owner_ref)
        b = BNode(f"r{r.id.hex if hasattr(r.id, 'hex') else r.id}")
        g.add((owner_ref, RDFS.subClassOf, b))
        g.add((b, RDF.type, OWL.Restriction))
        if r.on_property_id and r.on_property_id in prop_iri:
            g.add((b, OWL.onProperty, URIRef(prop_iri[r.on_property_id])))
        filler = class_iri.get(r.filler_class_id) if r.filler_class_id else None
        if r.kind == "some" and filler:
            g.add((b, OWL.someValuesFrom, URIRef(filler)))
        elif r.kind == "only" and filler:
            g.add((b, OWL.allValuesFrom, URIRef(filler)))
        elif r.kind in ("exactly", "min", "max") and r.cardinality is not None:
            pred = {
                "exactly": OWL.cardinality,
                "min": OWL.minCardinality,
                "max": OWL.maxCardinality,
            }[r.kind]
            g.add((b, pred, Literal(r.cardinality, datatype=XSD.nonNegativeInteger)))

    return g, subjects


def _label(g: Graph, s: URIRef, label: str | None, comment: str | None) -> None:
    if label:
        g.add((s, RDFS.label, Literal(label)))
    if comment:
        g.add((s, RDFS.comment, Literal(comment)))


# --------------------------------------------------------------------------- #
# Loading the authoritative on-disk TTL
# --------------------------------------------------------------------------- #
def load_base_graph(ontology_dir: Path) -> Graph:
    """Parse every *.ttl under the authoritative ontology directory.

    Tolerates a missing directory (returns an empty graph) so export/diff work
    in test environments without the full ontology checked out.
    """
    g = Graph()
    g.bind("slpra", SLPRA)
    if not ontology_dir or not Path(ontology_dir).exists():
        return g
    for ttl in sorted(Path(ontology_dir).glob("*.ttl")):
        try:
            g.parse(str(ttl), format="turtle")
        except Exception as exc:  # pragma: no cover - corrupt source file
            logger.warning("Could not parse %s: %s", ttl, exc)
    return g


# --------------------------------------------------------------------------- #
# Surgical merge + diff
# --------------------------------------------------------------------------- #
def surgical_merge(base: Graph, managed: Graph, managed_subjects: set[URIRef]) -> Graph:
    """Replace only whitelisted predicates of managed subjects; preserve rest."""
    result = Graph()
    for prefix, ns in base.namespaces():
        result.bind(prefix, ns)
    result.bind("slpra", SLPRA)

    for triple in base:
        s, p, _ = triple
        if s in managed_subjects and p in MANAGED_PREDICATES:
            continue  # workbench-owned — will be re-emitted from metadata
        result.add(triple)

    for triple in managed:
        result.add(triple)
    return result


def _fmt(term) -> str:
    if isinstance(term, URIRef):
        return f"<{term}>"
    if isinstance(term, BNode):
        return f"_:{term}"
    if isinstance(term, Literal):
        return term.n3()
    return str(term)


def serialize_triple(triple) -> str:
    s, p, o = triple
    return f"{_fmt(s)} {_fmt(p)} {_fmt(o)} ."


def diff_graphs(old: Graph, new: Graph) -> tuple[list[str], list[str]]:
    """Return (added, removed) as N-Triples-style strings (blank nodes ignored
    for stability — they carry no stable identity across serialisations)."""
    def _comparable(g: Graph) -> set:
        return {t for t in g if not isinstance(t[0], BNode) and not isinstance(t[2], BNode)}

    old_t = _comparable(old)
    new_t = _comparable(new)
    added = sorted(serialize_triple(t) for t in (new_t - old_t))
    removed = sorted(serialize_triple(t) for t in (old_t - new_t))
    return added, removed


# --------------------------------------------------------------------------- #
# High-level entry points used by the meta store / API
# --------------------------------------------------------------------------- #
def export_ttl(db: Session, ontology_dir: Path) -> str:
    """Build the surgically merged authoritative graph and serialise to Turtle."""
    base = load_base_graph(ontology_dir)
    managed, subjects = build_managed_graph(db)
    merged = surgical_merge(base, managed, subjects)
    return merged.serialize(format="turtle")


def export_diff(db: Session, ontology_dir: Path) -> tuple[str, list[str], list[str]]:
    """Return (turtle_preview, triples_added, triples_removed) for a release."""
    base = load_base_graph(ontology_dir)
    managed, subjects = build_managed_graph(db)
    merged = surgical_merge(base, managed, subjects)
    added, removed = diff_graphs(base, merged)
    return merged.serialize(format="turtle"), added, removed


def parse_ttl(content: str | bytes) -> Graph:
    g = Graph()
    g.parse(data=content, format="turtle")
    return g
