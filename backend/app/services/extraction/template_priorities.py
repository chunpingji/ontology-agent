"""Read declared fact paths as scheduling preferences, never as extraction rules."""


def template_priority_paths(schema: dict, root_class: str) -> list[tuple[str, ...]]:
    paths = []

    def forward(steps):
        if any(isinstance(s, dict) and s.get("direction", "forward") != "forward" for s in steps):
            return None
        result = tuple(s if isinstance(s, str) else s["predicate_iri"] for s in steps)
        return result

    if schema.get("schema_version") != 2:
        for section in schema.get("sections", []):
            for binding in section.get("coverage", []):
                if (
                    binding.get("kind") != "ontology_relation"
                    or binding.get("doc_class_iri") != root_class
                ):
                    continue
                path = forward(binding.get("predicate_path") or [binding["predicate_iri"]])
                if path:
                    paths.extend((*path, prop) for prop in binding.get("required_properties", []))
                    paths.append(path)
        return list(dict.fromkeys(paths))

    definitions = schema.get("definitions", {})
    bindings = definitions.get("bindings", {})
    from app.services.reasoning.pde_calculation import builtin_contract

    for check in schema.get("calculation_checks", []):
        contract = builtin_contract(check.get("contract_ref"))
        if not contract or contract["definition"]["root_class_iri"] != root_class:
            continue
        method = contract["definition"]
        path = tuple(method["predicate_path"])
        paths.append(path)
        paths.extend(
            (*path, parameter["predicate_iri"]) for parameter in method["parameters"].values()
        )

    def binding_path(identity, visited=frozenset()):
        binding = bindings.get(identity, {})
        if identity in visited or binding.get("kind") != "facts":
            return None
        scope = binding.get("scope", {})
        root = scope.get("root", {})
        path = forward(scope.get("predicate_path", []))
        if path is None:
            return None
        if root.get("kind") == "source_root":
            if binding.get("contract_ref", {}).get("root_class_iri") == root_class:
                return path
        elif root.get("kind") == "binding":
            parent = binding_path(root.get("binding_ref"), visited | {identity})
            if parent is not None:
                return (*parent, *path)
        return None

    def projection_paths(projection, path):
        steps = forward(projection.get("predicate_path", []))
        if steps is None:
            return
        path = (*path, *steps)
        prop = projection.get("property_iri") or projection.get("display_property_iri")
        if prop:
            paths.append((*path, prop))
        for field in projection.get("fields", {}).values():
            projection_paths(field.get("value", {}), path)

    for item in definitions.get("inputs", {}).values():
        path = binding_path(item.get("binding_ref"))
        if path is not None:
            projection_paths(item.get("projection", {}), path)
    for identity in bindings:
        path = binding_path(identity)
        if path:
            paths.append(path)
    return list(dict.fromkeys(paths))
