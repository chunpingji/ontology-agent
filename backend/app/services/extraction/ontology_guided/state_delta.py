"""Versioned JSON tree identities and exact, copy-on-write state deltas.

Only changed subtrees are serialized for hashing. Detached prior values are
private immutable snapshots; equality is an optimization, never an identity rule.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from typing import Any


def _immutable_mutation(*args, **kwargs):
    raise TypeError("state snapshot data is immutable")


class FrozenDict(dict):
    __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = _immutable_mutation
    __ior__ = _immutable_mutation

    def __deepcopy__(self, memo):
        return self


class FrozenList(list):
    __setitem__ = __delitem__ = append = clear = extend = insert = pop = _immutable_mutation
    remove = reverse = sort = __iadd__ = __imul__ = _immutable_mutation

    def __deepcopy__(self, memo):
        return self


def freeze_json(value):
    """Detach at mutation boundaries; subsequent exports share immutable JSON."""
    if isinstance(value, (FrozenDict, FrozenList)):
        return value
    if isinstance(value, dict):
        return FrozenDict((key, freeze_json(item)) for key, item in value.items())
    if isinstance(value, (tuple, list)):
        return FrozenList(freeze_json(item) for item in value)
    return value


def thaw_json(value):
    if isinstance(value, dict):
        return {key: thaw_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [thaw_json(item) for item in value]
    return value


@dataclass(frozen=True)
class JsonState:
    value: Any
    digest: str
    children: Any = None


def _hash(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _kind(value):
    # Ranking snapshots use immutable dict/list subclasses. Their JSON kind is
    # unchanged; scalar bool/int/float identities must still remain distinct.
    return dict if isinstance(value, dict) else list if isinstance(value, list) else type(value)


def _equal(left, right):
    if left is right:
        return True
    if _kind(left) is not _kind(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(_equal(v, right[k]) for k, v in left.items())
    if isinstance(left, list):
        return len(left) == len(right) and all(_equal(a, b) for a, b in zip(left, right))
    return left == right


def snapshot_delta(value, previous: JsonState | None = None, path=()):
    """Return a detached tree and explicit operations from an optional prior tree."""
    if previous is not None and _equal(value, previous.value):
        return previous, []
    same_kind = previous is not None and _kind(value) is _kind(previous.value)
    operations = []
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("state keys must be strings")
        old = previous.children if same_kind else {}
        children = {}
        for key, item in value.items():
            children[key], edits = snapshot_delta(item, old.get(key), (*path, key))
            operations.extend(edits)
        for key in sorted(set(old) - set(value)):
            operations.append({"op": "remove", "path": [*path, key]})
        detached = value if isinstance(value, FrozenDict) else {
            key: child.value for key, child in children.items()}
        digest = _hash(["json-tree-v1", "object",
                        {key: child.digest for key, child in children.items()}])
    elif isinstance(value, list):
        old = previous.children if same_kind else []
        if len(value) == len(old):
            children = []
            for index, item in enumerate(value):
                child, edits = snapshot_delta(item, old[index], (*path, index))
                children.append(child)
                operations.extend(edits)
        else:
            prefix = 0
            while prefix < min(len(value), len(old)) and (
                _equal(value[prefix], old[prefix].value)
            ):
                prefix += 1
            suffix = 0
            while suffix < min(len(value), len(old)) - prefix and (
                _equal(value[-suffix - 1], old[-suffix - 1].value)
            ):
                suffix += 1
            middle = value[prefix:len(value) - suffix]
            new = [snapshot_delta(item)[0] for item in middle]
            children = [*old[:prefix], *new, *(old[len(old) - suffix:] if suffix else [])]
            operations = [{"op": "splice", "path": list(path), "index": prefix,
                           "delete_count": len(old) - prefix - suffix,
                           "values": [child.value for child in new]}]
        detached = value if isinstance(value, FrozenList) else [child.value for child in children]
        digest = _hash(["json-tree-v1", "array", [child.digest for child in children]])
    else:
        if value is not None and type(value) not in (str, int, float, bool):
            raise ValueError("state contains a non-JSON value")
        detached, children = value, None
        digest = _hash(["json-tree-v1", "scalar", value])
        operations = [{"op": "put", "path": list(path), "value": value}]
    tree = JsonState(detached, digest, children)
    if not same_kind:
        operations = [{"op": "put", "path": list(path), "value": detached}]
    return tree, operations


def apply_delta(value, operations):
    """Strict path operations; clone only ancestors, leaving the prior version intact."""
    def change(current, path, edit):
        if not path:
            if edit["op"] == "put":
                return snapshot_delta(edit["value"])[0].value
            if edit["op"] != "splice" or not isinstance(current, list):
                raise ValueError("invalid state delta target")
            index, count = edit["index"], edit["delete_count"]
            if (type(index) is not int or type(count) is not int or index < 0 or count < 0
                    or index + count > len(current) or not isinstance(edit["values"], list)):
                raise ValueError("invalid state delta splice")
            additions = snapshot_delta(edit["values"])[0].value
            return [*current[:index], *additions, *current[index + count:]]
        key, tail = path[0], path[1:]
        if isinstance(current, dict) and isinstance(key, str):
            result = dict(current)
            if not tail and edit["op"] == "remove":
                if key not in result:
                    raise ValueError("state delta removes an absent key")
                del result[key]
            elif not tail and edit["op"] == "put":
                result[key] = snapshot_delta(edit["value"])[0].value
            else:
                if key not in result:
                    raise ValueError("state delta path is absent")
                result[key] = change(result[key], tail, edit)
            return result
        if (isinstance(current, list) and type(key) is int and 0 <= key < len(current)
                and edit["op"] != "remove"):
            result = list(current)
            result[key] = change(result[key], tail, edit)
            return result
        raise ValueError("invalid state delta path")

    for edit in operations:
        keys = {"put": {"op", "path", "value"}, "remove": {"op", "path"},
                "splice": {"op", "path", "index", "delete_count", "values"}}
        if (not isinstance(edit, dict) or set(edit) != keys.get(edit.get("op"))
                or not isinstance(edit.get("path"), list)):
            raise ValueError("invalid state delta operation")
        value = change(value, edit["path"], edit)
    return value
