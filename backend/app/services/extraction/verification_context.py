"""Request-only ontology projection; authoritative scopes and hashes remain intact."""

from copy import deepcopy


def verification_payload(payload, proposed, schema, competing_classes=()):
    result = deepcopy(payload)
    proposal_types = {item["class_iri"] for item in proposed} | set(competing_classes)
    seeds = set(proposal_types)
    seeds.add(payload.get("effective_class", ""))

    def collect(value):
        if isinstance(value, dict):
            if value.get("class_iri"):
                seeds.add(value["class_iri"])
            for item in value.values():
                collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)

    collect(payload["subjects"])
    collect({k: v for k, v in payload["task"].items() if k != "predicate_definition"})

    # Keep competing sibling types (nearest parents, not every descendant of Thing).
    def direct_parents(iri):
        parents = set(schema.get(iri, {}).get("parents", []))
        inherited = {p for parent in parents for p in schema.get(parent, {}).get("parents", [])}
        return (
            parents
            - inherited
            - {
                "http://www.w3.org/2002/07/owl#Thing",
                "http://www.w3.org/2000/01/rdf-schema#Resource",
            }
        )

    parents = {p for iri in seeds for p in direct_parents(iri)}
    selected = seeds | {iri for iri in schema if direct_parents(iri) & parents}
    # A proposal of a broad type still competes with its more specific subtypes.
    selected.update(
        iri
        for iri, definition in schema.items()
        if set(definition.get("parents", [])) & proposal_types
    )
    # One-hop relationship roles distinguish the entity from its containers and
    # related objects. Do not transitively expand an entire connected ontology.
    selected.update(
        r
        for iri in proposal_types
        for p in schema.get(iri, {}).get("relationships", [])
        for r in p.get("range", [])
    )
    selected.update(
        iri
        for iri, definition in schema.items()
        if any(
            set(p.get("range", [])) & proposal_types for p in definition.get("relationships", [])
        )
    )
    pending = list(selected)
    while pending:
        iri = pending.pop()
        for parent in schema.get(iri, {}).get("parents", []):
            if parent not in selected:
                selected.add(parent)
                pending.append(parent)
    original = payload["task"].get("predicate_definition", {}).get("classes", {})
    classes = {}
    for iri in sorted(selected):
        if iri in original:
            classes[iri] = deepcopy(original[iri])
        elif iri in schema:
            classes[iri] = {
                k: v
                for k, v in schema[iri].items()
                if k in {"iri", "label", "description", "parents"}
            } | {
                "identity_properties": [
                    p for p in schema[iri].get("properties", []) if p.get("identity_key") is True
                ]
            }
        if iri in classes and iri in proposal_types:
            # Type-only menus can mistake an attribute value for an entity (for
            # example an atmosphere value for a unit operation). Keep the actual
            # ontology's property/relationship roles for proposed types, too.
            classes[iri]["property_roles"] = deepcopy(schema.get(iri, {}).get("properties", []))
            classes[iri]["relationship_roles"] = deepcopy(
                schema.get(iri, {}).get("relationships", [])
            )
    result["task"]["predicate_definition"] = {
        **result["task"].get("predicate_definition", {}),
        "classes": classes,
    }
    return result
