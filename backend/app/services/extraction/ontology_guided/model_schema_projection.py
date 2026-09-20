"""Compact generated answer schemas without changing their accepted JSON values."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

_SCHEMA_MAPS = {"properties", "patternProperties", "$defs", "definitions", "dependentSchemas"}
_SCHEMA_LISTS = {"allOf", "anyOf", "oneOf", "prefixItems"}
_SCHEMA_VALUES = {
    "items", "additionalItems", "additionalProperties", "unevaluatedProperties",
    "unevaluatedItems", "contains", "propertyNames", "not", "if", "then", "else",
    "contentSchema",
}


def _compact_node(value: Any) -> Any:
    if not isinstance(value, dict):
        return deepcopy(value)
    result = {}
    for key, item in value.items():
        if key == "title":
            continue
        if key in _SCHEMA_MAPS:
            result[key] = {name: _compact_node(child) for name, child in item.items()}
        elif key in _SCHEMA_LISTS or key == "items" and isinstance(item, list):
            result[key] = [_compact_node(child) for child in item]
        elif key in _SCHEMA_VALUES:
            result[key] = _compact_node(item)
        else:
            # Enum/default/const objects are data, including any key named title.
            result[key] = deepcopy(item)
    if result.get("type") == "array" and result.get("maxItems") == 0:
        # Strict providers require a typed items schema even for an empty array.
        # No element can be emitted, so its original constraints are unreachable.
        result["items"] = {"type": "string"}
        result.pop("prefixItems", None)
    return result


def _references(value: Any):
    if not isinstance(value, dict):
        return
    if isinstance(value.get("$ref"), str):
        yield value["$ref"]
    for key, item in value.items():
        if key in _SCHEMA_MAPS - {"$defs", "definitions"}:
            for child in item.values():
                yield from _references(child)
        elif key in _SCHEMA_LISTS or key == "items" and isinstance(item, list):
            for child in item:
                yield from _references(child)
        elif key in _SCHEMA_VALUES:
            yield from _references(item)


def compact_answer_schema(schema: dict) -> dict:
    """Copy a Pydantic answer schema, retaining only reachable root definitions.

    Generated answer schemas use root-local ``#/$defs/...`` references. Follow
    their transitive closure, including recursive definitions; never inspect
    literal enum/default values as schemas. Descriptions and all reachable
    validation constraints remain intact.
    """
    compact = _compact_node(schema)
    definitions = compact.get("$defs")
    if not isinstance(definitions, dict):
        return compact
    reachable: set[str] = set()
    pending = list(_references(compact))
    while pending:
        reference = pending.pop()
        if not reference.startswith("#/$defs/"):
            continue
        name = reference[len("#/$defs/"):].split("/", 1)[0].replace("~1", "/").replace("~0", "~")
        if name in reachable or name not in definitions:
            continue
        reachable.add(name)
        pending.extend(_references(definitions[name]))
    if reachable:
        compact["$defs"] = {key: value for key, value in definitions.items() if key in reachable}
    else:
        compact.pop("$defs")
    return compact
