"""Independent, read-only ontology cards for the document harness.

Cards describe legal predicates, never instance identity or document evidence.
This module deliberately does not use the former extraction engine's contracts.
"""

from __future__ import annotations

import hashlib
import json
from collections import deque
from collections.abc import Iterable
from typing import Any
from typing import Literal as TypingLiteral

from pydantic import BaseModel, ConfigDict
from rdflib import OWL, RDF, RDFS, SKOS, XSD, BNode, Graph, Literal, URIRef
from rdflib.compare import to_isomorphic


class FrozenCard(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AnnotationValue(FrozenCard):
    predicate_iri: str
    value: str
    language: str | None = None
    datatype_iri: str | None = None
    value_is_iri: bool = False


class AnnotationContract(FrozenCard):
    iri: str
    label: str
    description: str


class ClassExpression(FrozenCard):
    kind: TypingLiteral["named", "union", "intersection", "restriction", "unknown"]
    iri: str | None = None
    operands: tuple[ClassExpression, ...] = ()
    property_iri: str | None = None
    # Preserve restriction semantics for later type alignment. Existential and
    # cardinality axioms are not reinterpreted as document recognition gates.
    facets: tuple[tuple[str, str], ...] = ()
    reason: str | None = None


class PropertyCard(FrozenCard):
    iri: str
    label: str
    description: str
    aliases: tuple[str, ...] = ()
    domain_class_iris: tuple[str, ...] = ()
    domain: tuple[ClassExpression, ...] = ()
    range: tuple[ClassExpression, ...] = ()
    datatype_iris: tuple[str, ...] = ()
    identity_key: bool = False
    annotations: tuple[AnnotationValue, ...] = ()
    constraint_status: TypingLiteral["resolved", "unresolved"] = "resolved"
    diagnostics: tuple[str, ...] = ()


class RelationCard(FrozenCard):
    iri: str
    label: str
    description: str
    aliases: tuple[str, ...] = ()
    domain_class_iris: tuple[str, ...] = ()
    range_class_iris: tuple[str, ...] = ()
    domain: tuple[ClassExpression, ...] = ()
    range: tuple[ClassExpression, ...] = ()
    direction: TypingLiteral["subject_to_object"] = "subject_to_object"
    inverse_iris: tuple[str, ...] = ()
    annotations: tuple[AnnotationValue, ...] = ()
    constraint_status: TypingLiteral["resolved", "unresolved"] = "resolved"
    diagnostics: tuple[str, ...] = ()


class ClassCard(FrozenCard):
    iri: str
    label: str
    description: str
    aliases: tuple[str, ...] = ()
    parent_iris: tuple[str, ...] = ()
    definition: tuple[ClassExpression, ...] = ()
    properties: tuple[PropertyCard, ...] = ()
    relations: tuple[RelationCard, ...] = ()
    identity_key_groups: tuple[tuple[str, ...], ...] = ()
    annotations: tuple[AnnotationValue, ...] = ()


class SchemaCatalog(FrozenCard):
    schema_version: TypingLiteral["document-harness-ontology/1"] = "document-harness-ontology/1"
    snapshot_id: str
    ontology_hash: str
    root_class_iri: str
    classes: dict[str, ClassCard]
    reachable_class_iris: tuple[str, ...]
    annotation_contracts: tuple[AnnotationContract, ...] = ()
    diagnostics: tuple[str, ...] = ()


def _local_name(iri: str) -> str:
    return iri.rsplit("#", 1)[-1].rsplit("/", 1)[-1]


def _terms(graph: Graph, subject: URIRef, predicates: Iterable[URIRef]) -> list[Literal]:
    return sorted(
        {
            value for predicate in predicates for value in graph.objects(subject, predicate)
            if isinstance(value, Literal) and str(value).strip()
        },
        key=lambda value: (
            0 if (value.language or "").startswith("zh") else 1,
            value.language or "", str(value),
        ),
    )


def _labels(graph: Graph, subject: URIRef) -> tuple[str, tuple[str, ...]]:
    labels = _terms(graph, subject, (RDFS.label, SKOS.prefLabel))
    aliases = tuple(dict.fromkeys(
        str(item) for item in labels + _terms(graph, subject, (SKOS.altLabel,))
    ))
    return (str(labels[0]) if labels else _local_name(str(subject)), aliases)


def _description(graph: Graph, subject: URIRef) -> str:
    return "\n".join(dict.fromkeys(
        str(value) for value in _terms(graph, subject, (RDFS.comment, SKOS.definition))
    ))


def _rdf_list(graph: Graph, head: Any) -> tuple[Any, ...] | None:
    values: list[Any] = []
    seen: set[Any] = set()
    while head != RDF.nil:
        if head in seen:
            return None
        seen.add(head)
        first = list(graph.objects(head, RDF.first))
        rest = list(graph.objects(head, RDF.rest))
        if len(first) != 1 or len(rest) != 1:
            return None
        values.append(first[0])
        head = rest[0]
    return tuple(values)


def _expression(graph: Graph, node: Any, seen: frozenset[Any] = frozenset()) -> ClassExpression:
    if isinstance(node, URIRef):
        return ClassExpression(kind="named", iri=str(node))
    if not isinstance(node, BNode) or node in seen:
        return ClassExpression(kind="unknown", reason="invalid_or_cyclic_class_expression")
    visited = seen | {node}
    union = list(graph.objects(node, OWL.unionOf))
    intersection = list(graph.objects(node, OWL.intersectionOf))
    if union or intersection:
        if len(union) + len(intersection) != 1:
            return ClassExpression(kind="unknown", reason="ambiguous_boolean_expression")
        values = _rdf_list(graph, (union or intersection)[0])
        if not values:
            return ClassExpression(kind="unknown", reason="empty_or_invalid_boolean_expression")
        operands = tuple(sorted(
            (_expression(graph, item, visited) for item in values),
            key=lambda item: item.model_dump_json(),
        ))
        return ClassExpression(
            kind="union" if union else "intersection", operands=operands,
        )
    on_property = list(graph.objects(node, OWL.onProperty))
    if len(on_property) == 1 and isinstance(on_property[0], URIRef):
        facets = []
        for predicate, value in graph.predicate_objects(node):
            if predicate in (RDF.type, OWL.onProperty):
                continue
            encoded = (
                _expression(graph, value, visited).model_dump_json()
                if isinstance(value, BNode) else value.n3()
            )
            facets.append((str(predicate), encoded))
        return ClassExpression(
            kind="restriction", property_iri=str(on_property[0]), facets=tuple(sorted(facets)),
        )
    return ClassExpression(kind="unknown", reason="unsupported_anonymous_class_expression")


def _named_superclasses(expression: ClassExpression) -> set[str]:
    if expression.kind == "named":
        return {expression.iri} if expression.iri else set()
    if expression.kind == "intersection":
        return {iri for item in expression.operands for iri in _named_superclasses(item)}
    return set()


def _closure(start: str, links: dict[str, set[str]]) -> set[str]:
    found: set[str] = set()
    pending = [start]
    while pending:
        value = pending.pop()
        if value not in found:
            found.add(value)
            pending.extend(links.get(value, ()))
    return found


def _matches(
    expression: ClassExpression, candidate: str, ancestors: dict[str, set[str]],
) -> bool | None:
    if expression.kind == "named":
        if expression.iri == str(OWL.Thing):
            return True
        if expression.iri == str(OWL.Nothing):
            return False
        return expression.iri in ancestors.get(candidate, {candidate})
    if expression.kind not in ("union", "intersection"):
        return None
    values = [_matches(item, candidate, ancestors) for item in expression.operands]
    # An unresolved union branch cannot make other, explicitly legal branches
    # illegal. An unresolved conjunct cannot authorize additional classes.
    if expression.kind == "union":
        return True if True in values else (None if None in values else False)
    return False if False in values else (None if None in values else True)


def _allowed(
    expressions: tuple[ClassExpression, ...], ancestors: dict[str, set[str]],
) -> tuple[set[str], bool]:
    if not expressions:
        return set(), True
    allowed: set[str] = set()
    unresolved = False
    for candidate in ancestors:
        values = [_matches(item, candidate, ancestors) for item in expressions]
        unresolved |= None in values
        if all(value is True for value in values):
            allowed.add(candidate)
    return allowed, unresolved


_DATATYPE_PARENTS = {
    "normalizedString": "string", "token": "normalizedString", "language": "token",
    "Name": "token", "NCName": "Name", "NMTOKEN": "token", "ID": "NCName",
    "IDREF": "NCName", "ENTITY": "NCName", "integer": "decimal", "long": "integer",
    "int": "long", "short": "int", "byte": "short", "nonPositiveInteger": "integer",
    "negativeInteger": "nonPositiveInteger", "nonNegativeInteger": "integer",
    "positiveInteger": "nonNegativeInteger", "unsignedLong": "nonNegativeInteger",
    "unsignedInt": "unsignedLong", "unsignedShort": "unsignedInt", "unsignedByte": "unsignedShort",
    "dateTimeStamp": "dateTime",
}


def _datatypes(expressions: tuple[ClassExpression, ...]) -> tuple[tuple[str, ...], bool]:
    names: set[str] = set()

    def collect(item: ClassExpression) -> None:
        if item.kind == "named" and item.iri:
            names.add(item.iri)
        for operand in item.operands:
            collect(operand)

    for expression in expressions:
        collect(expression)
    links = {str(XSD[child]): {str(XSD[parent])} for child, parent in _DATATYPE_PARENTS.items()}
    ancestors = {name: _closure(name, links) | {str(RDFS.Literal)} for name in names}
    allowed, unresolved = _allowed(expressions, ancestors)
    return tuple(sorted(allowed)), unresolved


def _annotations(
    graph: Graph, subject: URIRef, predicates: set[URIRef],
) -> tuple[AnnotationValue, ...]:
    values = []
    for predicate in predicates:
        for value in graph.objects(subject, predicate):
            if not isinstance(value, (URIRef, Literal)):
                continue
            values.append(AnnotationValue(
                predicate_iri=str(predicate), value=str(value),
                language=value.language if isinstance(value, Literal) else None,
                datatype_iri=(
                    str(value.datatype) if isinstance(value, Literal) and value.datatype else None
                ),
                value_is_iri=isinstance(value, URIRef),
            ))
    return tuple(sorted(values, key=lambda item: item.model_dump_json()))


def _restrictions(graph: Graph, nodes: Iterable[Any]) -> list[Any]:
    pending = list(nodes)
    seen = set()
    result = []
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        if list(graph.objects(current, OWL.onProperty)):
            result.append(current)
        for head in graph.objects(current, OWL.intersectionOf):
            pending.extend(_rdf_list(graph, head) or ())
    return result


def catalog_from_graph(
    graph: Graph, root_class_iri: str, *, identity_property_iris: Iterable[str] = (),
) -> SchemaCatalog:
    """Freeze cards without querying instance data or importing execution contracts.

    ``identity_property_iris`` is explicit metadata from an ontology adapter,
    never inferred from a property's name. Composite owl:hasKey declarations
    remain grouped; an identity flag is not a claim of global uniqueness.
    """
    class_refs = {
        node for kind in (OWL.Class, RDFS.Class) for node in graph.subjects(RDF.type, kind)
        if isinstance(node, URIRef)
    }
    for subject, target in graph.subject_objects(RDFS.subClassOf):
        class_refs.update(value for value in (subject, target) if isinstance(value, URIRef))
    for subject, target in graph.subject_objects(OWL.equivalentClass):
        class_refs.update(value for value in (subject, target) if isinstance(value, URIRef))
    class_refs.discard(OWL.Nothing)
    if URIRef(root_class_iri) not in class_refs:
        raise ValueError(f"Root class is not declared in the frozen ontology: {root_class_iri}")

    annotation_refs = {
        value for value in graph.subjects(RDF.type, OWL.AnnotationProperty)
        if isinstance(value, URIRef)
    } | {RDFS.label, RDFS.comment, SKOS.definition, SKOS.prefLabel, SKOS.altLabel}
    direct_nodes: dict[str, list[Any]] = {}
    definitions: dict[str, tuple[ClassExpression, ...]] = {}
    parents: dict[str, set[str]] = {}
    key_groups: dict[str, tuple[tuple[str, ...], ...]] = {}
    diagnostics: set[str] = set()
    identity = set(identity_property_iris)
    for ref in sorted(class_refs):
        iri = str(ref)
        nodes = list(graph.objects(ref, RDFS.subClassOf))
        nodes.extend(graph.objects(ref, OWL.equivalentClass))
        nodes.extend(graph.subjects(OWL.equivalentClass, ref))
        direct_nodes[iri] = nodes
        definitions[iri] = tuple(sorted(
            {_expression(graph, node) for node in nodes}, key=lambda item: item.model_dump_json(),
        ))
        parents[iri] = {
            target for item in definitions[iri] for target in _named_superclasses(item)
            if target != iri
        }
        groups = []
        for head in graph.objects(ref, OWL.hasKey):
            keys = _rdf_list(graph, head)
            if not keys or any(not isinstance(item, URIRef) for item in keys):
                diagnostics.add(f"{iri}: invalid owl:hasKey declaration")
                continue
            group = tuple(sorted(str(item) for item in keys))
            groups.append(group)
        key_groups[iri] = tuple(sorted(set(groups)))
    ancestors = {iri: _closure(iri, parents) for iri in parents}
    class_restrictions = {
        iri: _restrictions(graph, (
            node for ancestor in lineage for node in direct_nodes.get(ancestor, ())
        )) for iri, lineage in ancestors.items()
    }
    property_parents: dict[str, set[str]] = {}
    for subject, target in graph.subject_objects(RDFS.subPropertyOf):
        if isinstance(subject, URIRef) and isinstance(target, URIRef):
            property_parents.setdefault(str(subject), set()).add(str(target))
    properties: dict[str, list[PropertyCard]] = {iri: [] for iri in ancestors}
    relations: dict[str, list[RelationCard]] = {iri: [] for iri in ancestors}
    for kind in (OWL.DatatypeProperty, OWL.ObjectProperty):
        for ref in sorted(set(graph.subjects(RDF.type, kind))):
            if not isinstance(ref, URIRef):
                continue
            lineage = _closure(str(ref), property_parents)
            domains = tuple(sorted({
                _expression(graph, value) for parent in lineage
                for value in graph.objects(URIRef(parent), RDFS.domain)
            }, key=lambda item: item.model_dump_json()))
            ranges = tuple(sorted({
                _expression(graph, value) for parent in lineage
                for value in graph.objects(URIRef(parent), RDFS.range)
            }, key=lambda item: item.model_dump_json()))
            domain_classes, uncertain_domain = _allowed(domains, ancestors)
            if uncertain_domain:
                diagnostics.add(f"{ref}: missing or unresolved domain constraint")
            if domains and not domain_classes:
                diagnostics.add(f"{ref}: no declared class satisfies every domain constraint")
            label, aliases = _labels(graph, ref)
            for class_iri in ancestors:
                restrictions = [
                    node for node in class_restrictions[class_iri]
                    if any(
                        URIRef(parent) in graph.objects(node, OWL.onProperty) for parent in lineage
                    )
                ]
                # A local restriction can declare applicability when the
                # property has no global domain. It cannot override one.
                if class_iri not in domain_classes and (domains or not restrictions):
                    continue
                local_range = tuple(sorted({
                    *ranges,
                    *(
                        _expression(graph, value) for node in restrictions
                        for value in graph.objects(node, OWL.allValuesFrom)
                    ),
                }, key=lambda item: item.model_dump_json()))
                common = {
                    "iri": str(ref), "label": label, "aliases": aliases,
                    "description": _description(graph, ref),
                    "domain_class_iris": tuple(sorted(domain_classes | {class_iri})),
                    "domain": domains, "range": local_range,
                    "annotations": _annotations(graph, ref, annotation_refs),
                }
                if kind == OWL.DatatypeProperty:
                    datatypes, unresolved = _datatypes(local_range)
                    reasons = (() if datatypes else ("missing_or_unsatisfied_datatype_range",))
                    if unresolved:
                        reasons += ("unresolved_datatype_constraint",)
                    groups = (
                        group for ancestor in ancestors[class_iri]
                        for group in key_groups.get(ancestor, ())
                    )
                    properties[class_iri].append(PropertyCard(
                        **common, datatype_iris=datatypes,
                        identity_key=(
                            str(ref) in identity or any(str(ref) in group for group in groups)
                        ),
                        constraint_status="resolved" if datatypes else "unresolved",
                        diagnostics=reasons,
                    ))
                else:
                    targets, unresolved = _allowed(local_range, ancestors)
                    reasons = (() if targets else ("missing_or_unsatisfied_relation_range",))
                    if unresolved:
                        reasons += ("unresolved_relation_constraint",)
                    inverses = {
                        value for value in (
                            *graph.objects(ref, OWL.inverseOf), *graph.subjects(OWL.inverseOf, ref),
                        ) if isinstance(value, URIRef)
                    }
                    relations[class_iri].append(RelationCard(
                        **common, range_class_iris=tuple(sorted(targets)),
                        inverse_iris=tuple(sorted(str(value) for value in inverses)),
                        constraint_status="resolved" if targets else "unresolved",
                        diagnostics=reasons,
                    ))
                for reason in reasons:
                    diagnostics.add(f"{class_iri} / {ref}: {reason}")
    cards = {}
    for ref in sorted(class_refs):
        iri = str(ref)
        label, aliases = _labels(graph, ref)
        cards[iri] = ClassCard(
            iri=iri, label=label, aliases=aliases, description=_description(graph, ref),
            parent_iris=tuple(sorted(parents[iri])), definition=definitions[iri],
            properties=tuple(properties[iri]), relations=tuple(relations[iri]),
            identity_key_groups=tuple(sorted({
                group for ancestor in ancestors[iri] for group in key_groups.get(ancestor, ())
            })),
            annotations=_annotations(graph, ref, annotation_refs),
        )
    reachable: set[str] = set()
    pending = deque([root_class_iri])
    while pending:
        iri = pending.popleft()
        if iri in reachable:
            continue
        reachable.add(iri)
        for relation in cards[iri].relations:
            if relation.constraint_status == "resolved":
                pending.extend(relation.range_class_iris)
    graph_hash = str(to_isomorphic(graph).graph_digest())
    ontology_hash = hashlib.sha256(json.dumps(
        {"graph": graph_hash, "identity_property_iris": sorted(identity)}, sort_keys=True,
    ).encode()).hexdigest()
    snapshot_id = hashlib.sha256(f"{ontology_hash}\n{root_class_iri}".encode()).hexdigest()
    return SchemaCatalog(
        snapshot_id=snapshot_id, ontology_hash=ontology_hash, root_class_iri=root_class_iri,
        classes=cards, reachable_class_iris=tuple(sorted(reachable)),
        annotation_contracts=tuple(
            AnnotationContract(
                iri=str(ref), label=_labels(graph, ref)[0], description=_description(graph, ref),
            ) for ref in sorted(annotation_refs)
        ),
        diagnostics=tuple(sorted(diagnostics)),
    )


def freeze_catalog(engine: Any, root_class_iri: str) -> SchemaCatalog:
    """Read one published World under its read lock; no old-engine fallback.

    The ontology engine's property API supplies its explicit custom identity
    annotation interpretation. Only that metadata is used; all domain/range
    expressions are read directly from RDF to preserve conjunction semantics.
    """
    with engine.lexical_read_scope():
        graph = Graph()
        for triple in engine._world.as_rdflib_graph():
            graph.add(triple)
        identity: set[str] = set()
        class_refs = {
            ref for kind in (OWL.Class, RDFS.Class) for ref in graph.subjects(RDF.type, kind)
            if isinstance(ref, URIRef)
        }
        for ref in sorted(class_refs):
            identity.update(
                item["iri"] for item in engine.get_data_properties_by_domain(str(ref))
                if item.get("identity_key") is True
            )
        return catalog_from_graph(graph, root_class_iri, identity_property_iris=identity)


def get_class_card(catalog: SchemaCatalog, class_iri: str) -> ClassCard:
    if class_iri not in catalog.reachable_class_iris:
        raise ValueError(f"Class is outside the frozen root-reachable catalog: {class_iri}")
    return catalog.classes[class_iri]


def identity_guidance(card: ClassCard, *, annotation_contracts=()) -> dict[str, Any]:
    """Project declared identity semantics without inferring keys from names."""
    members = {iri for group in card.identity_key_groups for iri in group}
    selected = [prop for prop in card.properties if prop.identity_key or prop.iri in members]
    selected.extend(rel for rel in card.relations if rel.iri in members)
    annotations = set()
    properties = []
    for prop in selected:
        custom = [a for a in prop.annotations if a.predicate_iri not in {
            str(RDFS.label), str(RDFS.comment), str(SKOS.definition),
            str(SKOS.prefLabel), str(SKOS.altLabel),
        }]
        annotations.update(a.predicate_iri for a in custom)
        properties.append({
            "iri": prop.iri, "label": prop.label, "aliases": list(prop.aliases),
            "description": prop.description,
            "property_kind": "data" if isinstance(prop, PropertyCard) else "object",
            "domain": [item.model_dump(mode="json", exclude_none=True) for item in prop.domain],
            "range": [item.model_dump(mode="json", exclude_none=True) for item in prop.range],
            **({"datatype_iris": list(prop.datatype_iris), "identity_key": prop.identity_key}
               if isinstance(prop, PropertyCard) else {}),
            "annotations": [a.model_dump(mode="json", exclude_none=True) for a in custom],
            "constraint_status": prop.constraint_status,
        })
    available = {p.iri for p in selected if p.constraint_status == "resolved"}
    return {
        "identity_properties": properties,
        "identity_key_groups": [list(group) for group in card.identity_key_groups],
        "unavailable_identity_components": sorted(members - available),
        "identity_rule": (
            "身份标记仅提供核对依据，编号含义及作用域按属性定义和原文解释。"
            "完整键组保留组合语义；缺少组件不能单独充当身份键。"
            "同号不自动同一，异号不自动不同；标记不声明全局唯一、必填或单值。"
        ),
        "annotation_contracts": [a.model_dump(mode="json") for a in annotation_contracts
                                 if a.iri in annotations],
    }


def legal_relation(
    catalog: SchemaCatalog, subject_class: str, predicate_iri: str, object_class: str,
) -> bool:
    if subject_class not in catalog.reachable_class_iris:
        return False
    return any(
        item.iri == predicate_iri and item.constraint_status == "resolved"
        and object_class in item.range_class_iris
        for item in catalog.classes[subject_class].relations
    )


def legal_property(catalog: SchemaCatalog, class_iri: str, predicate_iri: str) -> bool:
    if class_iri not in catalog.reachable_class_iris:
        return False
    return any(
        item.iri == predicate_iri and item.constraint_status == "resolved"
        for item in catalog.classes[class_iri].properties
    )


def model_menu(catalog: SchemaCatalog, class_iris: Iterable[str] | None = None) -> dict[str, Any]:
    """Full semantics, compact structure, actual IRIs; no alias/reference protocol."""
    selected = sorted(set(class_iris if class_iris is not None else catalog.reachable_class_iris))
    cards = [get_class_card(catalog, iri) for iri in selected]
    referenced_annotations: set[str] = set()
    rows = []
    for card in cards:
        props = []
        relations = []
        def metadata(item: PropertyCard | RelationCard) -> dict[str, Any]:
            value: dict[str, Any] = {
                "iri": item.iri, "label": item.label, "description": item.description,
            }
            aliases = [alias for alias in item.aliases if alias != item.label]
            if aliases:
                value["aliases"] = aliases
            # Labels and definitions are already above. Keep custom annotations
            # with their declared contracts, without printing that text twice.
            annotations = [
                annotation for annotation in item.annotations if annotation.predicate_iri not in {
                    str(RDFS.label), str(RDFS.comment), str(SKOS.definition),
                    str(SKOS.prefLabel), str(SKOS.altLabel),
                }
            ]
            if annotations:
                value["annotations"] = [
                    annotation.model_dump(mode="json", exclude_none=True)
                    for annotation in annotations
                ]
                referenced_annotations.update(
                    annotation.predicate_iri for annotation in annotations
                )
            if item.diagnostics:
                value["diagnostics"] = list(item.diagnostics)
            return value

        for prop in card.properties:
            if prop.constraint_status == "resolved":
                props.append({
                    **metadata(prop), "datatype_iris": list(prop.datatype_iris),
                    "identity_key": prop.identity_key,
                })
        for relation in card.relations:
            if relation.constraint_status == "resolved":
                relations.append({
                    **metadata(relation), "range_class_iris": list(relation.range_class_iris),
                    "direction": relation.direction, "inverse_iris": list(relation.inverse_iris),
                })
        rows.append({
            "iri": card.iri, "label": card.label, "aliases": list(card.aliases),
            "description": card.description,
            "definition": [
                item.model_dump(mode="json", exclude_none=True) for item in card.definition
            ],
            "identity_key_groups": [list(item) for item in card.identity_key_groups],
            "properties": props, "relations": relations,
        })
    return {
        "snapshot_id": catalog.snapshot_id, "classes": rows,
        "identity_rule": "Identity metadata requires source and scope; names never imply identity.",
        "annotation_contracts": [
            item.model_dump(mode="json") for item in catalog.annotation_contracts
            if item.iri in referenced_annotations
        ],
    }
