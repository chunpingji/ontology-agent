"""Small ontology view for mapped queries. OWL keys remain guidance, not resolution."""

from __future__ import annotations

import math
import re
from datetime import date, datetime

from rdflib import OWL, RDF, RDFS, XSD, BNode, Graph, Literal, URIRef

from app.models.ontology_meta import OntologyClass, OntologyDataProperty, OntologyLinkType
from app.services.integration.mock_entity_reader import is_iri

IDENTITY_KEY = URIRef("https://ontology.pharma-gmp.cn/slpra/integration/identityKey")
DATATYPES = tuple(
    str(XSD[name])
    for name in (
        "string",
        "integer",
        "decimal",
        "boolean",
        "date",
        "dateTime",
        "anyURI",
    )
)


def valid_value(value, datatype: str) -> bool:
    local = datatype.removeprefix(str(XSD))
    if datatype not in DATATYPES:
        return False
    if local == "boolean":
        return type(value) is bool
    if local == "integer":
        return type(value) is int
    if local == "decimal":
        return type(value) in {int, float} and math.isfinite(value)
    if not isinstance(value, str):
        return False
    if local == "string":
        return True
    if local == "anyURI":
        return is_iri(value)
    try:
        if local == "date":
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                return False
            date.fromisoformat(value)
        elif local == "dateTime":
            if "T" not in value:
                return False
            datetime.fromisoformat(value)
        return True
    except ValueError:
        return False


def transform_value(raw, kind: str, config: dict | None):
    """Only declared, deterministic conversions. Never vocabulary guessing or fallback."""
    config = config or {}
    if kind == "none":
        return raw
    if kind == "controlled_vocab":
        mapping = config["map"]
        if not isinstance(raw, str) or raw not in mapping:
            raise ValueError("explicit_value_map_miss")
        return mapping[raw]
    target = config["to"]
    if target == "string":
        if type(raw) not in {str, bool, int, float}:
            raise ValueError("invalid_scalar")
        return str(raw)
    if target == "integer":
        if type(raw) is int:
            return raw
        if isinstance(raw, str) and re.fullmatch(r"[+-]?\d+", raw):
            return int(raw)
        raise ValueError("invalid_integer")
    if target == "decimal":
        if type(raw) is bool:
            raise ValueError("invalid_decimal")
        value = float(raw)
    elif target == "boolean":
        if type(raw) is bool:
            return raw
        if raw not in ("true", "false", "1", "0"):
            raise ValueError("invalid_boolean")
        value = raw in ("true", "1")
    else:
        value = raw
    if not valid_value(value, str(XSD[target])):
        raise ValueError("invalid_cast")
    return value


class EntityQuerySchema:
    def __init__(self, db, engine):
        self.graph: Graph = engine.entity_query_graph()
        if not isinstance(self.graph, Graph):
            raise RuntimeError("实体查询所需本体未加载")
        self.disabled = set()
        self._ancestor_cache = {}
        self._property_cache = {}
        for model in (OntologyClass, OntologyDataProperty, OntologyLinkType):
            self.disabled.update(
                row[0]
                for row in db.query(model.slpra_iri)
                .filter_by(
                    is_disabled=True,
                )
                .all()
            )

    def class_valid(self, iri: str) -> bool:
        ref = URIRef(iri)
        return iri not in self.disabled and (
            (ref, RDF.type, OWL.Class) in self.graph or (ref, RDF.type, RDFS.Class) in self.graph
        )

    def ancestors(self, iri: str) -> frozenset:
        if iri in self._ancestor_cache:
            return self._ancestor_cache[iri]
        visited, pending = set(), [URIRef(iri)]
        while pending:
            current = pending.pop()
            if current in visited:
                continue
            visited.add(current)
            pending.extend(self.graph.objects(current, RDFS.subClassOf))
            pending.extend(self.graph.objects(current, OWL.equivalentClass))
            pending.extend(self.graph.subjects(OWL.equivalentClass, current))
            for head in self.graph.objects(current, OWL.intersectionOf):
                pending.extend(self.graph.items(head))
        self._ancestor_cache[iri] = frozenset(visited)
        return self._ancestor_cache[iri]

    def subclass(self, child: str, parent: str) -> bool:
        return URIRef(parent) in self.ancestors(child) or parent == str(OWL.Thing)

    def _domain(self, iri: str, node, seen=frozenset()) -> bool:
        if node in self.ancestors(iri) or node == OWL.Thing:
            return True
        if node in seen:
            return False
        if isinstance(node, BNode):
            for pred, combine in ((OWL.unionOf, any), (OWL.intersectionOf, all)):
                head = self.graph.value(node, pred)
                if head is not None:
                    members = list(self.graph.items(head))
                    return bool(members) and combine(
                        self._domain(iri, member, seen | {node}) for member in members
                    )
        return False

    def _range(self, node, datatype: str, seen=frozenset()) -> bool:
        if node in seen:
            return False
        if isinstance(node, URIRef):
            return (
                str(node) == datatype
                or (node == XSD.decimal and datatype == str(XSD.integer))
                or node == RDFS.Literal
            )
        for pred, combine in ((OWL.unionOf, any), (OWL.intersectionOf, all)):
            head = self.graph.value(node, pred)
            if head is not None:
                members = list(self.graph.items(head))
                return bool(members) and combine(
                    self._range(member, datatype, seen | {node}) for member in members
                )
        return False

    def property(self, class_iri: str, iri: str) -> dict | None:
        key = (class_iri, iri)
        if key not in self._property_cache:
            self._property_cache[key] = self._property(class_iri, iri)
        return self._property_cache[key]

    def _property(self, class_iri: str, iri: str) -> dict | None:
        prop = URIRef(iri)
        if iri in self.disabled or (prop, RDF.type, OWL.DatatypeProperty) not in self.graph:
            return None
        # Multiple domain and range triples are intersections, never implicit unions.
        if not all(self._domain(class_iri, d) for d in self.graph.objects(prop, RDFS.domain)):
            return None
        ranges = list(self.graph.objects(prop, RDFS.range))
        datatypes = [dt for dt in DATATYPES if ranges and all(self._range(r, dt) for r in ranges)]
        return {"property_iri": iri, "label": self.label(iri), "datatype_iris": datatypes}

    def label(self, iri: str) -> str:
        labels = list(self.graph.objects(URIRef(iri), RDFS.label))
        labels.sort(key=lambda v: (getattr(v, "language", None) != "zh", str(v)))
        return str(labels[0]) if labels else iri

    def properties(self, class_iri: str) -> list[dict]:
        return [
            p
            for iri in sorted(set(self.graph.subjects(RDF.type, OWL.DatatypeProperty)))
            if (p := self.property(class_iri, str(iri))) is not None
        ]

    def identity_guidance(self, class_iri: str, mapped: set[str]) -> dict:
        properties = []
        for p in self.properties(class_iri):
            vals = self.graph.objects(URIRef(p["property_iri"]), IDENTITY_KEY)
            # identityKey is an annotation, not an automatic owl:hasKey axiom.
            if any(isinstance(v, Literal) and v.toPython() is True for v in vals):
                properties.append({**p, "available": p["property_iri"] in mapped})
        groups = set()
        for cls in self.ancestors(class_iri):
            for head in self.graph.objects(cls, OWL.hasKey):
                groups.add(tuple(str(p) for p in self.graph.items(head)))
        return {
            "identity_properties": properties,
            "identity_key_groups": [
                {
                    "property_iris": list(group),
                    "available": bool(group)
                    and all(p in mapped and self.property(class_iri, p) for p in group),
                    "unavailable_property_iris": [
                        p for p in group if p not in mapped or not self.property(class_iri, p)
                    ],
                }
                for group in sorted(groups)
            ],
        }
