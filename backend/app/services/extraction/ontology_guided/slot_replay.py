"""Compact per-slot search receipts for deterministic task-prefix replay."""

from __future__ import annotations

from copy import deepcopy

from app.services.extraction.evidence_identity import evidence_hash

VERSION = "slot-search-delta-v1"


def _changes(old, new, path=()):
    if old == new:
        return []
    if isinstance(old, dict) and isinstance(new, dict):
        operations = []
        for key in sorted(old.keys() - new.keys()):
            operations.append([list((*path, key)), "remove"])
        for key in sorted(new):
            if key not in old:
                operations.append([list((*path, key)), "set", deepcopy(new[key])])
            else:
                operations.extend(_changes(old[key], new[key], (*path, key)))
        return operations
    if isinstance(old, list) and isinstance(new, list):
        start = 0
        while start < min(len(old), len(new)) and old[start] == new[start]:
            start += 1
        suffix = 0
        while (suffix < min(len(old), len(new)) - start
               and old[len(old) - suffix - 1] == new[len(new) - suffix - 1]):
            suffix += 1
        return [[list(path), "splice", start, len(old) - start - suffix,
                 deepcopy(new[start:len(new) - suffix])]]
    return [[list(path), "set", deepcopy(new)]]


def _apply(state, operations):
    for operation in operations:
        if not isinstance(operation, list) or len(operation) < 2:
            raise ValueError("invalid slot search delta operation")
        path, kind = operation[:2]
        if not isinstance(path, list):
            raise ValueError("invalid slot search delta path")
        parent = state
        try:
            for key in path[:-1]:
                parent = parent[key]
            if kind == "set" and len(operation) == 3:
                if not path:
                    state = deepcopy(operation[2])
                else:
                    parent[path[-1]] = deepcopy(operation[2])
            elif kind == "remove" and len(operation) == 2 and path:
                del parent[path[-1]]
            elif kind == "splice" and len(operation) == 5:
                target = parent[path[-1]] if path else state
                start, count, values = operation[2:]
                if (not isinstance(target, list) or type(start) is not int
                        or type(count) is not int or not isinstance(values, list)
                        or start < 0 or count < 0 or start + count > len(target)):
                    raise ValueError("invalid slot search delta splice")
                target[start:start + count] = deepcopy(values)
            else:
                raise ValueError("unsupported slot search delta operation")
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError("slot search delta does not match its base") from exc
    return state


class SlotSearchReplay:
    """Keep one in-memory head per plan; persisted outcomes carry only changes.

    Neither delta contents nor a hash alone grant proof authority. The normal
    search.restore identity and content checks remain the final consumer gate.
    """

    def __init__(self):
        self._heads = {}
        self._hashes = {}

    def capture(self, plan_id, state):
        if state.get("plan_id") != plan_id:
            raise ValueError("slot search receipt belongs to another plan")
        digest = evidence_hash(state)
        previous = self._heads.get(plan_id)
        payload = {"version": VERSION, "plan_id": plan_id, "state_hash": digest}
        if previous is None:
            payload["initial_state"] = deepcopy(state)
        else:
            payload.update(base_hash=self._hashes[plan_id], changes=_changes(previous, state))
        self._heads[plan_id] = deepcopy(state)
        self._hashes[plan_id] = digest
        return payload

    def restore(self, plan_id, payload):
        if payload.get("version") != VERSION or payload.get("plan_id") != plan_id:
            raise ValueError("slot search delta identity mismatch")
        if "initial_state" in payload:
            if plan_id in self._heads or "base_hash" in payload or "changes" in payload:
                raise ValueError("slot search delta cannot replace an existing prefix")
            state = deepcopy(payload["initial_state"])
        else:
            if (plan_id not in self._heads or payload.get("base_hash") != self._hashes[plan_id]
                    or not isinstance(payload.get("changes"), list)):
                raise ValueError("slot search delta prefix mismatch")
            state = _apply(deepcopy(self._heads[plan_id]), payload["changes"])
        if (not isinstance(state, dict) or state.get("plan_id") != plan_id
                or evidence_hash(state) != payload.get("state_hash")):
            raise ValueError("slot search delta content hash mismatch")
        self._heads[plan_id] = deepcopy(state)
        self._hashes[plan_id] = payload["state_hash"]
        return state
