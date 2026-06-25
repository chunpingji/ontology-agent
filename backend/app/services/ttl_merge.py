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

import json
import logging
from pathlib import Path

from rdflib import RDF, RDFS, OWL, XSD, BNode, Graph, Literal, Namespace, URIRef
from sqlalchemy.orm import Session

from app.models.ontology_meta import (
    OntologyAction,
    OntologyClass,
    OntologyClassificationCriterion,
    OntologyConflictPolicy,
    OntologyDataProperty,
    OntologyDecisionRule,
    OntologyLinkType,
    OntologyRestriction,
)

logger = logging.getLogger(__name__)

SLPRA = Namespace("https://ontology.pharma-gmp.cn/slpra/core/")
SLPRA_DRUG = Namespace("https://ontology.pharma-gmp.cn/slpra/drug/")
DCT = Namespace("http://purl.org/dc/terms/")

# OWL2 datatype facet for a numeric comparator (data-model.md §B1, R-DC3).
_FACET = {
    "gt": XSD.minExclusive,
    "ge": XSD.minInclusive,
    "lt": XSD.maxExclusive,
    "le": XSD.maxInclusive,
}

# slpra: vocabulary for the declarative rule layer (spec 006, data-model §B2/B3).
# These predicates only ever appear on DecisionRule_* / ConflictPolicy_* subjects
# (disjoint from E1–E5 subjects) so they are managed *per-subject* in the merge.
DECISION_RULE_PREDICATES = frozenset(
    {SLPRA.ruleGroup, SLPRA.antecedent, SLPRA.consequent, SLPRA.priority}
)
CONFLICT_POLICY_PREDICATES = frozenset(
    {SLPRA.dimension, SLPRA.strategy, SLPRA.overrideDirection, SLPRA.priorityLattice}
)

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
    | DECISION_RULE_PREDICATES
    | CONFLICT_POLICY_PREDICATES
)

# `dct:source` may legitimately appear on hand-authored subjects, so it is only
# stripped/re-emitted for the rule/policy named subjects the workbench owns.
_PER_RULE_PREDICATES = frozenset({DCT.source})

# Rule/policy named subjects (E12/E13) the workbench *fully* owns — the only
# subjects for which `dct:source` is workbench-managed (see surgical_merge).
_DECISION_RULE_PREFIX = str(SLPRA) + "DecisionRule_"
_CONFLICT_POLICY_PREFIX = str(SLPRA) + "ConflictPolicy_"

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
    g.bind("dct", DCT)
    subjects: set[URIRef] = set()

    # id -> IRI map for FK resolution (classes & link types referenced by id).
    # Also a local-name -> IRI index so the declarative criteria (which name
    # their property/filler by local name) project onto the right managed IRI.
    class_iri: dict = {}
    class_by_name: dict = {}
    for c in db.query(OntologyClass).all():
        class_iri[c.id] = c.slpra_iri
        class_by_name[c.slpra_iri.rsplit("/", 1)[-1]] = c.slpra_iri
    link_iri: dict = {}
    prop_by_name: dict = {}
    for lt in db.query(OntologyLinkType).all():
        link_iri[lt.id] = lt.slpra_iri
        prop_by_name[lt.slpra_iri.rsplit("/", 1)[-1]] = lt.slpra_iri
    dp_iri: dict = {}
    for dp in db.query(OntologyDataProperty).all():
        dp_iri[dp.id] = dp.slpra_iri
        prop_by_name[dp.slpra_iri.rsplit("/", 1)[-1]] = dp.slpra_iri

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

    # E11 classification criteria → owl:equivalentClass class expressions (T016)
    _emit_classification_criteria(g, db, class_iri, class_by_name, prop_by_name, subjects)

    # E12/E13 production rules + conflict policies → named slpra: resources (T032)
    _emit_decision_rules(g, db, subjects)
    _emit_conflict_policies(g, db, subjects)

    return g, subjects


# --------------------------------------------------------------------------- #
# E11 defined criteria → owl:equivalentClass BNode class expression (§B1)
# --------------------------------------------------------------------------- #
def _resolve_name(index: dict, name: str) -> URIRef:
    """Resolve a pattern's local property/class name to its managed IRI, falling
    back to the slpra-drug namespace (the module the current criteria target)."""
    return URIRef(index.get(name, str(SLPRA_DRUG) + name))


def _rdf_list(g: Graph, items: list, cells: list) -> object:
    """Materialise an rdf:List from `items` using the supplied deterministic
    BNode `cells` (one per item). Returns the list head (or rdf:nil if empty)."""
    if not items:
        return RDF.nil
    for i, (item, cell) in enumerate(zip(items, cells)):
        g.add((cell, RDF.first, item))
        g.add((cell, RDF.rest, cells[i + 1] if i + 1 < len(items) else RDF.nil))
    return cells[0]


def _criterion_class_expr(
    g: Graph, pattern: dict, cid: str, class_by_name: dict, prop_by_name: dict
):
    """Expand a `defined` criterion pattern into an OWL class expression rooted at
    a deterministic BNode `_:c<cid>` (data-model.md §B1, §C). Returns the root
    node, or None for an op outside the US1 defined-class vocabulary."""
    op = pattern.get("op")
    root = BNode(f"c{cid}")

    if op == "some_values_from":
        g.add((root, RDF.type, OWL.Restriction))
        g.add((root, OWL.onProperty, _resolve_name(prop_by_name, pattern["property"])))
        g.add((root, OWL.someValuesFrom, _resolve_name(class_by_name, pattern["filler_class"])))
        return root

    if op == "class_membership":
        # ∃ property . ( c1 ⊔ c2 … )
        union = BNode(f"c{cid}u")
        members = [_resolve_name(class_by_name, n) for n in pattern["classes"]]
        cells = [BNode(f"c{cid}l{i}") for i in range(len(members))]
        g.add((root, RDF.type, OWL.Restriction))
        g.add((root, OWL.onProperty, _resolve_name(prop_by_name, pattern["property"])))
        g.add((union, RDF.type, OWL.Class))
        g.add((union, OWL.unionOf, _rdf_list(g, members, cells)))
        g.add((root, OWL.someValuesFrom, union))
        return root

    if op == "datatype_facet":
        # ∃ property . datatype[ onDatatype xsd:integer ; withRestrictions ( [facet v] ) ]
        facet_pred = _FACET.get(pattern["cmp"])
        if facet_pred is None:  # eq/ne not facet-expressible
            logger.warning("criterion %s: cmp %r not facet-expressible", cid, pattern["cmp"])
            return None
        dt = BNode(f"c{cid}d")
        facet = BNode(f"c{cid}f")
        g.add((root, RDF.type, OWL.Restriction))
        g.add((root, OWL.onProperty, _resolve_name(prop_by_name, pattern["property"])))
        g.add((root, OWL.someValuesFrom, dt))
        g.add((dt, RDF.type, RDFS.Datatype))
        g.add((dt, OWL.onDatatype, XSD.integer))
        g.add((facet, facet_pred, Literal(pattern["value"], datatype=XSD.integer)))
        g.add((dt, OWL.withRestrictions, _rdf_list(g, [facet], [BNode(f"c{cid}r")])))
        return root

    if op == "boolean_has_value":
        g.add((root, RDF.type, OWL.Restriction))
        g.add((root, OWL.onProperty, _resolve_name(prop_by_name, pattern["property"])))
        g.add((root, OWL.hasValue, Literal(bool(pattern["value"]), datatype=XSD.boolean)))
        return root

    if op == "external_alignment":
        # ∃ property . <external class IRI>  — the filler is a full external
        # (ChEBI/ATC) IRI, emitted verbatim (NOT resolved through the managed
        # local-name index). The portable equivalentClass axiom lets a future DL
        # reasoner consume the same alignment the interpreter evaluates (T024/R1).
        g.add((root, RDF.type, OWL.Restriction))
        g.add((root, OWL.onProperty, _resolve_name(prop_by_name, pattern["property"])))
        g.add((root, OWL.someValuesFrom, URIRef(pattern["alignment"])))
        return root

    # `and` / `or` composite expressions are added with US3 (T034+).
    logger.warning("criterion %s: op %r outside defined vocabulary, skipped", cid, op)
    return None


def _emit_classification_criteria(
    g: Graph,
    db: Session,
    class_iri: dict,
    class_by_name: dict,
    prop_by_name: dict,
    subjects: set,
) -> None:
    """For each enabled `defined` E11 criterion, hang a deterministic-BNode class
    expression off its target class via owl:equivalentClass (data-model.md §B1).

    owl:equivalentClass is *not* in MANAGED_PREDICATES; surgical_merge strips only
    the (target, owl:equivalentClass, BNode) shape and reclaims the subgraph, so
    named-IRI external alignments on the same class survive verbatim (宪章 II)."""
    for crit in (
        db.query(OntologyClassificationCriterion)
        .filter_by(is_disabled=False, logic_role="defined")
        .all()
    ):
        target = class_iri.get(crit.target_class_id)
        if not target:
            logger.warning("criterion %s: target class unresolved, skipped", crit.criterion_key)
            continue
        cid = crit.id.hex if hasattr(crit.id, "hex") else str(crit.id).replace("-", "")
        expr = _criterion_class_expr(g, crit.pattern, cid, class_by_name, prop_by_name)
        if expr is None:
            continue
        target_ref = URIRef(target)
        subjects.add(target_ref)
        g.add((target_ref, OWL.equivalentClass, expr))


def _emit_decision_rules(g: Graph, db: Session, subjects: set) -> None:
    """Project each enabled E12 rule onto its named `slpra:DecisionRule_<key>`
    resource (data-model.md §B2). antecedent/consequent round-trip as JSON-string
    literals (sorted keys → stable release diffs); `dct:source` carries the
    regulation reference and is workbench-managed *only* on these named subjects
    (see surgical_merge / _is_rule_or_policy_subject)."""
    for rule in db.query(OntologyDecisionRule).filter_by(is_disabled=False).all():
        s = URIRef(rule.slpra_iri)
        subjects.add(s)
        g.add((s, RDF.type, SLPRA.DecisionRule))
        _label(g, s, rule.label, rule.comment)
        g.add((s, SLPRA.ruleGroup, Literal(rule.rule_group)))
        g.add((s, SLPRA.antecedent, Literal(json.dumps(rule.antecedent, ensure_ascii=False, sort_keys=True))))
        g.add((s, SLPRA.consequent, Literal(json.dumps(rule.consequent, ensure_ascii=False, sort_keys=True))))
        g.add((s, SLPRA.priority, Literal(int(rule.priority), datatype=XSD.integer)))
        if rule.regulation_ref:
            g.add((s, DCT.source, Literal(rule.regulation_ref)))


def _emit_conflict_policies(g: Graph, db: Session, subjects: set) -> None:
    """Project each enabled E13 policy onto its named `slpra:ConflictPolicy_<dimension>`
    resource (data-model.md §B3). `overrideDirection`/`priorityLattice` are
    nullable — emitted only when set; `priorityLattice` is a JSON-string literal."""
    for pol in db.query(OntologyConflictPolicy).filter_by(is_disabled=False).all():
        s = URIRef(pol.slpra_iri)
        subjects.add(s)
        g.add((s, RDF.type, SLPRA.ConflictPolicy))
        _label(g, s, pol.label, pol.comment)
        g.add((s, SLPRA.dimension, Literal(pol.dimension)))
        g.add((s, SLPRA.strategy, Literal(pol.strategy)))
        if pol.override_direction:
            g.add((s, SLPRA.overrideDirection, Literal(pol.override_direction)))
        if pol.priority_lattice is not None:
            g.add((
                s, SLPRA.priorityLattice,
                Literal(json.dumps(pol.priority_lattice, ensure_ascii=False, sort_keys=True)),
            ))
        if pol.regulation_ref:
            g.add((s, DCT.source, Literal(pol.regulation_ref)))


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
def _is_rule_or_policy_subject(s) -> bool:
    """True for the brand-new E12/E13 named subjects the workbench fully owns.

    `dct:source` is workbench-managed *only* on these — on hand-authored E1–E5
    subjects a `dct:source` annotation is preserved verbatim.
    """
    text = str(s)
    return text.startswith(_DECISION_RULE_PREFIX) or text.startswith(_CONFLICT_POLICY_PREFIX)


def _bnode_closure(graph: Graph, root: BNode) -> set:
    """Every triple reachable from `root`, following BNode objects.

    Captures the full class-expression subgraph (restriction nodes, rdf:List
    cells for owl:unionOf, nested datatype facets) hung off an
    `owl:equivalentClass` blank node, so the whole thing can be reclaimed when
    the managed graph stops emitting it — preventing orphan-triple accumulation
    (data-model.md §B2, 宪章 II).
    """
    triples: set = set()
    seen: set = {root}
    stack = [root]
    while stack:
        node = stack.pop()
        for p, o in graph.predicate_objects(node):
            triples.add((node, p, o))
            if isinstance(o, BNode) and o not in seen:
                seen.add(o)
                stack.append(o)
    return triples


def surgical_merge(base: Graph, managed: Graph, managed_subjects: set[URIRef]) -> Graph:
    """Replace only workbench-owned content of managed subjects; preserve rest.

    Three strip modes (research.md R2, 宪章 II NON-NEGOTIABLE):
      1. predicate-level — whitelisted ``MANAGED_PREDICATES`` on managed subjects.
      2. object-shape-aware — ``(s, owl:equivalentClass, BNode)`` class
         expressions on managed subjects are dropped (and their BNode subgraph
         recursively reclaimed), while ``(s, owl:equivalentClass, <namedIRI>)``
         external alignments are preserved *verbatim* (owl:equivalentClass is
         deliberately NOT in MANAGED_PREDICATES).
      3. per-rule — ``dct:source`` only on DecisionRule_*/ConflictPolicy_* subjects.
    Everything else on those subjects, and every unmodelled triple, survives.
    """
    result = Graph()
    for prefix, ns in base.namespaces():
        result.bind(prefix, ns)
    result.bind("slpra", SLPRA)
    result.bind("dct", DCT)

    # BNode class-expression subgraphs to reclaim: an owl:equivalentClass on a
    # managed subject whose object is a blank node (workbench-authored axiom).
    reclaimed: set = set()
    for s in managed_subjects:
        for o in base.objects(s, OWL.equivalentClass):
            if isinstance(o, BNode):
                reclaimed |= _bnode_closure(base, o)

    for triple in base:
        s, p, o = triple
        if s in managed_subjects:
            if p in MANAGED_PREDICATES:
                continue  # (1) predicate-level — re-emitted from metadata
            if p == OWL.equivalentClass and isinstance(o, BNode):
                continue  # (2) object-shape-aware — drop only BNode class exprs
            if p in _PER_RULE_PREDICATES and _is_rule_or_policy_subject(s):
                continue  # (3) per-rule dct:source
        if triple in reclaimed:
            continue  # reclaim stripped BNode subgraph (orphan prevention)
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
