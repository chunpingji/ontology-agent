"""Read-only ontology snapshots and deterministic one-hop local menus."""

from __future__ import annotations

from collections import deque
from copy import deepcopy
from typing import Any

from app.services.extraction.evidence_identity import evidence_hash, stable_id
from app.services.extraction.ontology_guided.contracts import (
    EdgeSpec,
    LocalMenu,
    OntologyClassDefinition,
    OntologySnapshot,
    RangeClass,
    SlotSpec,
    SubjectRef,
)


def _safe_call(target: object, name: str, *args: Any, default: Any = None) -> Any:
    method = getattr(target, name, None)
    if not callable(method):
        return default
    try:
        value = method(*args)
    except (AttributeError, KeyError, TypeError, ValueError):
        return default
    return default if value is None else value


def _walk_classes(engine: object) -> dict[str, dict[str, Any]]:
    """Return each class once while retaining all discovered direct parents."""
    classes: dict[str, dict[str, Any]] = {}
    for module in _safe_call(engine, "get_modules", default=[]) or []:
        module_key = getattr(module, "key", None) or (
            module.get("key") if isinstance(module, dict) else None
        )
        if not module_key:
            continue
        pending = deque(
            (root, None)
            for root in _safe_call(engine, "get_class_hierarchy", module_key, default=[])
        )
        while pending:
            node, parent = pending.popleft()
            iri = getattr(node, "iri", None) or (
                node.get("iri") if isinstance(node, dict) else None
            )
            if not iri:
                continue
            name = getattr(node, "name", None) or (
                node.get("name") if isinstance(node, dict) else None
            )
            label = (
                getattr(node, "label", None)
                or (node.get("label") if isinstance(node, dict) else None)
                or name
                or iri.rsplit("/", 1)[-1]
            )
            entry = classes.setdefault(
                iri,
                {"iri": iri, "label": str(label), "parent_iris": set(), "children": []},
            )
            if parent:
                entry["parent_iris"].add(parent)
            children = getattr(node, "children", None)
            if children is None and isinstance(node, dict):
                children = node.get("children")
            for child in children or []:
                pending.append((child, iri))
    return classes


def _property_specs(engine: object, class_iri: str) -> tuple[list[SlotSpec], list[EdgeSpec]]:
    properties = []
    for prop in _safe_call(engine, "get_data_properties_by_domain", class_iri, default=[]) or []:
        iri = str(prop.get("iri") or "")
        if not iri:
            continue
        ranges = [str(value) for value in prop.get("range", []) if value]
        datatype = prop.get("datatype")
        if datatype and not ranges:
            ranges = [str(datatype)]
        properties.append(
            SlotSpec(
                iri=iri,
                label=str(prop.get("label") or prop.get("name") or iri.rsplit("/", 1)[-1]),
                description=str(prop.get("description") or ""),
                declared_by=[class_iri],
                max_count=prop.get("max_count"),
                datatype_iris=sorted(dict.fromkeys(ranges)),
                canonical_unit=prop.get("canonical_unit"),
                identity_key=prop.get("identity_key") is True,
                constraint_status="resolved" if ranges else "constraint_unresolved",
            )
        )
    relationships = []
    for prop in _safe_call(engine, "get_object_properties_by_domain", class_iri, default=[]) or []:
        iri = str(prop.get("iri") or "")
        if not iri:
            continue
        ranges = sorted({str(value) for value in prop.get("range", []) if value})
        # An object property without a resolvable formal range is not widened to
        # an arbitrary class menu.  It stays visible as a diagnostic only.
        if not ranges:
            continue
        relationships.append(
            EdgeSpec(
                iri=iri,
                label=str(prop.get("label") or prop.get("name") or iri.rsplit("/", 1)[-1]),
                description=str(prop.get("description") or ""),
                declared_by=[class_iri],
                max_count=prop.get("max_count"),
                range_class_iris=ranges,
            )
        )
    return properties, relationships


def ontology_snapshot_from_engine(
    engine: object, root_class_iri: str | None = None
) -> OntologySnapshot:
    """Freeze ontology labels, direct declarations and their declaring class.

    The function intentionally does not consume ``get_relation_schema`` because
    that API pre-expands multiple hops and loses declaration provenance.
    """
    raw_classes = _walk_classes(engine)
    diagnostics: list[str] = []
    if root_class_iri and root_class_iri not in raw_classes:
        detail = _safe_call(engine, "get_class_detail", root_class_iri)
        relation_schema = (
            _safe_call(engine, "get_relation_schema", root_class_iri, 1, default=[]) or []
        )
        if detail is not None or relation_schema:
            label = (
                getattr(detail, "label_zh", None)
                or getattr(detail, "label_en", None)
                or getattr(detail, "name", None)
                or next(
                    (
                        item.get("domain_class_label")
                        for item in relation_schema
                        if item.get("domain_class_iri") == root_class_iri
                    ),
                    None,
                )
                or root_class_iri.rsplit("/", 1)[-1]
            )
            raw_classes[root_class_iri] = {
                "iri": root_class_iri,
                "label": str(label),
                "parent_iris": set(getattr(detail, "parent_iris", []) or []),
                "children": [],
            }
            diagnostics.append("root_class_recovered_from_direct_engine_lookup")
    definitions: dict[str, OntologyClassDefinition] = {}
    for iri, raw in sorted(raw_classes.items()):
        detail = _safe_call(engine, "get_class_detail", iri)
        description = str(getattr(detail, "comment", "") or "")
        properties, relationships = _property_specs(engine, iri)
        if not relationships:
            # Older engines expose only the relation-schema compatibility API.
            # Fold its direct (hop=1) declarations into the frozen snapshot now;
            # workers must not consult a newer live ontology later.
            relationships = _legacy_direct_relationships(engine, iri)
        # Engine/RDF enumeration order is not declaration semantics. Keep
        # parallel same-IRI constraints distinct, with full-payload tie breaks;
        # never merge their ranges or rewrite an already frozen snapshot.
        properties.sort(key=lambda item: (item.iri, evidence_hash(item)))
        relationships.sort(key=lambda item: (item.iri, evidence_hash(item)))
        payload = {
            "iri": iri,
            "label": raw["label"],
            "description": description,
            "parent_iris": sorted(raw["parent_iris"]),
            "declared_properties": [item.model_dump(mode="json") for item in properties],
            "declared_relationships": [item.model_dump(mode="json") for item in relationships],
        }
        definitions[iri] = OntologyClassDefinition(
            **payload,
            source_hash=evidence_hash(payload),
        )
    if not definitions:
        diagnostics.append("ontology_snapshot_empty")
    serializable = {iri: item.model_dump(mode="json") for iri, item in definitions.items()}
    ontology_hash = evidence_hash(serializable)
    return OntologySnapshot(
        snapshot_id=stable_id("ontology-snapshot", [ontology_hash, "one-hop-declarations"]),
        ontology_hash=ontology_hash,
        classes=definitions,
        diagnostics=diagnostics,
    )


def _legacy_direct_relationships(engine: object, class_iri: str) -> list[EdgeSpec]:
    """Read only hop=1 edges when an older engine lacks direct-domain helpers."""
    result: list[EdgeSpec] = []
    for edge in _safe_call(engine, "get_relation_schema", class_iri, 1, default=[]) or []:
        if edge.get("domain_class_iri") != class_iri or edge.get("hop", 1) != 1:
            continue
        range_iri = edge.get("range_class_iri")
        if not range_iri:
            continue
        ranges = [str(range_iri)]
        ranges.extend(
            str(item["iri"]) for item in edge.get("range_subclasses", []) if item.get("iri")
        )
        result.append(
            EdgeSpec(
                iri=str(edge["predicate_iri"]),
                label=str(edge.get("predicate_label") or edge["predicate_iri"]),
                declared_by=[class_iri],
                range_class_iris=sorted(dict.fromkeys(ranges)),
            )
        )
    return result


def _ancestors(snapshot: OntologySnapshot, class_iri: str) -> list[str]:
    pending = list(snapshot.classes[class_iri].parent_iris)
    result: list[str] = []
    seen = {class_iri}
    while pending:
        iri = pending.pop(0)
        if iri in seen:
            continue
        seen.add(iri)
        if iri not in snapshot.classes:
            continue
        result.append(iri)
        pending.extend(snapshot.classes[iri].parent_iris)
    return result


def _merge_properties(declarations: list[SlotSpec], diagnostics: list[str]) -> list[SlotSpec]:
    grouped: dict[str, list[SlotSpec]] = {}
    for declaration in declarations:
        grouped.setdefault(declaration.iri, []).append(declaration)
    merged: list[SlotSpec] = []
    for iri, values in sorted(grouped.items()):
        latest = values[-1]
        ranges = sorted({item for value in values for item in value.datatype_iris})
        if len({tuple(value.datatype_iris) for value in values}) > 1:
            diagnostics.append(f"parallel_datatype_constraints_preserved:{iri}")
        merged.append(
            latest.model_copy(
                update={
                    "declared_by": sorted(
                        {owner for value in values for owner in value.declared_by}
                    ),
                    "datatype_iris": ranges,
                    "constraint_status": (
                        "resolved"
                        if all(value.constraint_status == "resolved" for value in values)
                        else "constraint_unresolved"
                    ),
                }
            )
        )
    return merged


def _range_class(snapshot: OntologySnapshot, iri: str) -> RangeClass:
    """Describe a legal range using only the frozen ontology snapshot."""
    definition = snapshot.classes.get(iri)
    label = definition.label if definition else iri.rsplit("/", 1)[-1]
    props = definition.declared_properties if definition else []
    return RangeClass(
        iri=iri,
        label=str(label),
        description=definition.description if definition else "",
        parent_iris=list(definition.parent_iris) if definition else [],
        identity_property_iris=[prop.iri for prop in props if prop.identity_key],
        direct_field_labels=[prop.label for prop in props],
    )


def _range_closure(snapshot: OntologySnapshot, range_iris: set[str]) -> set[str]:
    """Expand named ranges to their frozen descendants without a live-engine read."""
    expanded = set(range_iris)
    pending = deque(range_iris)
    children: dict[str, set[str]] = {}
    for iri, definition in snapshot.classes.items():
        for parent_iri in definition.parent_iris:
            children.setdefault(parent_iri, set()).add(iri)
    while pending:
        parent_iri = pending.popleft()
        for child_iri in sorted(children.get(parent_iri, set())):
            if child_iri in expanded:
                continue
            expanded.add(child_iri)
            pending.append(child_iri)
    return expanded


def _merge_relationships(
    declarations: list[EdgeSpec],
    *,
    snapshot: OntologySnapshot,
    diagnostics: list[str],
) -> list[EdgeSpec]:
    grouped: dict[str, list[EdgeSpec]] = {}
    for declaration in declarations:
        grouped.setdefault(declaration.iri, []).append(declaration)
    merged: list[EdgeSpec] = []
    for iri, values in sorted(grouped.items()):
        latest = values[-1]
        # Parallel/inherited rdfs:range constraints are conjunctive.  Expand
        # each declaration through the *frozen* subclass graph before taking
        # the intersection, so a child-class override can narrow a parent
        # declaration without being mistaken for an unrelated union.
        declared_ranges = [
            _range_closure(snapshot, set(value.range_class_iris)) for value in values
        ]
        # Multiple inherited declarations are conjunctive constraints.  Prefer
        # their intersection; if the engine projection cannot represent one,
        # preserve the narrowest declared set and surface a diagnostic.
        ranges = set.intersection(*declared_ranges) if declared_ranges else set()
        status = "resolved"
        if not ranges:
            ranges = min(declared_ranges, key=len) if declared_ranges else set()
            status = "constraint_unresolved"
            diagnostics.append(f"relationship_constraint_unresolved:{iri}")
        range_classes = [_range_class(snapshot, item) for item in sorted(ranges)]
        merged.append(
            latest.model_copy(
                update={
                    "declared_by": sorted(
                        {owner for value in values for owner in value.declared_by}
                    ),
                    "range_class_iris": sorted(ranges),
                    "range_classes": range_classes,
                    "constraint_status": status,
                }
            )
        )
    return merged


def compile_local_menu(
    ontology: OntologySnapshot,
    subject: SubjectRef,
    *,
    engine: object | None = None,
) -> LocalMenu:
    """Compile exactly one predicate hop for a concrete, typed subject.

    Legal range subclasses are candidate types for this edge only.  Their own
    outgoing predicates are not included until an instance of that type becomes
    an eligible subject and this function is called again for it.
    """
    # ``engine`` remains a compatibility-only keyword for existing callers.
    # Menu compilation is deliberately a pure projection of ``ontology``;
    # querying a mutable engine here would invalidate the run fingerprint.
    _ = engine
    if subject.class_iri not in ontology.classes:
        raise ValueError("subject class is absent from frozen ontology snapshot")
    owners = [*_ancestors(ontology, subject.class_iri), subject.class_iri]
    property_declarations: list[SlotSpec] = []
    relationship_declarations: list[EdgeSpec] = []
    for owner in owners:
        definition = ontology.classes[owner]
        property_declarations.extend(deepcopy(definition.declared_properties))
        relationship_declarations.extend(deepcopy(definition.declared_relationships))
    diagnostics = list(ontology.diagnostics)
    properties = _merge_properties(property_declarations, diagnostics)
    relationships = _merge_relationships(
        relationship_declarations,
        snapshot=ontology,
        diagnostics=diagnostics,
    )
    identity = {
        "ontology_snapshot_id": ontology.snapshot_id,
        "subject": subject.model_dump(mode="json"),
        "properties": [item.model_dump(mode="json") for item in properties],
        "relationships": [item.model_dump(mode="json") for item in relationships],
        "policy": "ontology-guided-local-menu-v2",
    }
    return LocalMenu(
        menu_id=stable_id("local-menu", identity),
        ontology_snapshot_id=ontology.snapshot_id,
        subject=subject,
        properties=properties,
        relationships=relationships,
        diagnostics=list(dict.fromkeys(diagnostics)),
    )
