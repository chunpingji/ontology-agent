"""Current execution partitions, changed at their business mutation sites.

The store consumes drains; it never compares two complete execution snapshots.
Only JSON values cross the core/persistence boundary.
"""

from __future__ import annotations

import json
from functools import wraps


def json_value(value):
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(k): json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [json_value(v) for v in value]
    return value


class WorkMap(dict):
    """One current row per business key; in-place edits explicitly call touch."""

    def __init__(self, values=(), *, encode=json_value):
        super().__init__(values)
        self.positions = {key: i for i, key in enumerate(self)}
        self.next_position = len(self)
        self.changed = dict.fromkeys(self)
        self.encode = encode

    def touch(self, key):
        self.changed[key] = None

    def __setitem__(self, key, value):
        if key not in self:
            self.positions[key] = self.next_position
            self.next_position += 1
        super().__setitem__(key, value)
        self.touch(key)

    def __delitem__(self, key):
        super().__delitem__(key)
        self.touch(key)

    def pop(self, key, *default):
        if key in self:
            self.touch(key)
        return super().pop(key, *default)

    def update(self, *args, **kwargs):
        for key, value in dict(*args, **kwargs).items():
            self[key] = value

    def setdefault(self, key, default=None):
        if key not in self:
            self[key] = default
        self.touch(key)
        return self[key]

    def clear(self):
        self.changed.update(dict.fromkeys(self))
        super().clear()

    def drain(self):
        result = {
            json.dumps(key, ensure_ascii=False, separators=(",", ":")): {
                "key": json_value(key),
                "value": self.encode(self[key]),
                "position": self.positions[key],
            }
            if key in self
            else None
            for key in self.changed
        }
        self.changed.clear()
        return result

    @classmethod
    def load(cls, rows, *, decode=lambda value: value, encode=json_value):
        result = cls(encode=encode)
        for row in sorted(rows.values(), key=lambda r: r.get("position", 0)):
            key = row["key"]
            key = tuple(key) if isinstance(key, list) else key
            dict.__setitem__(result, key, decode(row["value"]))
            result.positions[key] = row.get("position", len(result) - 1)
            result.next_position = max(result.next_position, result.positions[key] + 1)
        result.changed.clear()
        return result


def search_mutation(method):
    @wraps(method)
    def changed(self, *args, **kwargs):
        callback = getattr(self, "on_work_change", None)
        # Readiness polling must not dirty every closed slot at each task.
        before = (self.stage, self.status, tuple(self._cursors.items()), tuple(self._active))
        result = method(self, *args, **kwargs)
        after = (self.stage, self.status, tuple(self._cursors.items()), tuple(self._active))
        if callback is not None and (
            method.__name__
            not in {
                "next_admission",
                "close_satisfied",
                "reopen_satisfied",
                "continue_search",
                "exhaust_dependency",
            }
            or result is not None
            or before != after
        ):
            callback()
        return result

    return changed


def search_payload(search):
    return {
        "state": search.snapshot(),
        "subject_mentions": search.subject_mentions,
        "seed_record_ids": search.seed_record_ids,
        "priority_record_ids": search.priority_record_ids,
    }


class WorkSet(set):
    """Changed membership rows for scheduler dedup and proof invalidation."""

    def __init__(self, values=()):
        super().__init__(values)
        self.changes = WorkMap({v: True for v in self})

    def add(self, value):
        if value not in self:
            self.changes[value] = True
        super().add(value)

    def discard(self, value):
        if value in self:
            self.changes[value] = False
        super().discard(value)

    def remove(self, value):
        if value not in self:
            raise KeyError(value)
        self.discard(value)

    def update(self, *values):
        for group in values:
            for value in group:
                self.add(value)

    def clear(self):
        for value in tuple(self):
            self.discard(value)

    def difference_update(self, *values):
        for group in values:
            for value in group:
                self.discard(value)

    def drain(self):
        rows = self.changes.drain()
        return {key: row if row["value"] else None for key, row in rows.items()}

    @classmethod
    def load(cls, rows):
        result = cls(
            tuple(row["key"]) if isinstance(row["key"], list) else row["key"]
            for row in rows.values()
        )
        result.changes.changed.clear()
        return result


class WorkLog(list):
    """Append-only per-business records; drain visits only new records."""

    def __init__(self, values=(), *, restored=False):
        super().__init__(values)
        self.saved = len(self) if restored else 0

    def drain(self):
        result = {
            str(i): {"key": i, "value": json_value(self[i])} for i in range(self.saved, len(self))
        }
        self.saved = len(self)
        return result


class SlotParts:
    """Replace only the affected slot's rows when its proof generation changes."""

    def __init__(self, rows=(), generations=()):
        self.generations = dict(generations)
        self.keys = {}
        for key, row in dict(rows).items():
            self.keys.setdefault(tuple(row["slot"]), set()).add(key)

    def changes(self, slot, generation, fragments):
        keys = self.keys.setdefault(slot, set())
        changes = {}
        if self.generations.get(slot) != generation:
            changes.update(dict.fromkeys(keys))
            keys.clear()
        self.generations[slot] = generation
        for name, values in fragments.items():
            for key, value in values.items():
                encoded = json.dumps([list(slot), name, key], ensure_ascii=False)
                changes[encoded] = (
                    {
                        "slot": list(slot),
                        "name": name,
                        "key": key,
                        "value": value,
                        "position": value.get("position", 0),
                    }
                    if value is not None
                    else None
                )
                if value is None:
                    keys.discard(encoded)
                else:
                    keys.add(encoded)
        return changes


def enable_search_parts(search, *, restored=False):
    """Search state uses per-record rows instead of an accumulated slot snapshot."""
    names = ["observed", "_attempts", "_candidates", "_matches"]
    if hasattr(search, "_dispositions"):
        names += [
            "_dispositions",
            "_decisions",
            "_results",
            "_hits",
            "_stage_results",
            "_group_context",
        ]
    search.work_maps = names
    for name in names:
        values = WorkMap(getattr(search, name))
        if restored:
            values.changed.clear()
        setattr(search, name, values)
    search.work_logs = ["_attempt_history", "_pages", "events"]
    if hasattr(search, "_rounds"):
        search.work_logs += ["_rounds", "_reactivations"]
    for name in search.work_logs:
        setattr(search, name, WorkLog(getattr(search, name), restored=restored))
    search.admitted = WorkSet(search.admitted)
    if restored:
        search.admitted.changes.changed.clear()
    if hasattr(search, "_unavailable"):
        search._unavailable = WorkSet(search._unavailable)
        if restored:
            search._unavailable.changes.changed.clear()


def search_current_changes(search):
    from dataclasses import asdict, is_dataclass

    parts = {name: getattr(search, name).drain() for name in search.work_maps}
    for name in search.work_logs:
        values = getattr(search, name)
        if name == "_pages":
            parts[name] = {
                str(i): {
                    "key": i,
                    "value": asdict(values[i]) if is_dataclass(values[i]) else values[i],
                }
                for i in range(values.saved, len(values))
            }
            values.saved = len(values)
        else:
            parts[name] = values.drain()
    parts["admitted"] = search.admitted.drain()
    if hasattr(search, "_unavailable"):
        parts["_unavailable"] = search._unavailable.drain()
    control = {
        name: json_value(getattr(search, name))
        for name in (
            "_cursors",
            "_active",
            "stage",
            "status",
            "_reason",
            "_semantic_epoch_id",
            "_semantic_received",
            "_semantic_epochs",
            "_semantic_skip_reason",
            "_exploration_pages",
            "_supported_count",
            "_pass_supported_baseline",
        )
    }
    if hasattr(search, "_dependency_exhausted"):
        control["_dependency_exhausted"] = search._dependency_exhausted
    control["_part_counts"] = {
        name: len(getattr(search, name))
        for name in (
            *search.work_maps,
            *search.work_logs,
            "admitted",
            *(("_unavailable",) if hasattr(search, "_unavailable") else ()),
        )
    }
    parts["control"] = {"current": control}
    return parts


def restore_search_parts(search, parts):
    from app.services.extraction.ontology_guided.heuristic_search import AdmissionPage

    enable_search_parts(search, restored=True)
    for name in search.work_maps:
        setattr(search, name, WorkMap.load(parts.get(name, {})))
    for name in search.work_logs:
        values = [
            r["value"] for _, r in sorted(parts.get(name, {}).items(), key=lambda i: int(i[0]))
        ]
        if name == "_pages":
            values = [AdmissionPage(**v) for v in values]
        setattr(search, name, WorkLog(values, restored=True))
    search.admitted = WorkSet.load(parts.get("admitted", {}))
    if not search.admitted <= search.record_universe:
        raise ValueError("current search admits foreign source records")
    if hasattr(search, "_unavailable"):
        search._unavailable = WorkSet.load(parts.get("_unavailable", {}))
    control = parts["control"]["current"]
    for name, count in control["_part_counts"].items():
        if len(getattr(search, name)) != count:
            raise ValueError("current search partition is missing records")
    for name, value in control.items():
        if name != "_part_counts":
            setattr(search, name, value)


class WorkRecords(list):
    """Current planned records, indexed by record ID and published independently."""

    def __init__(self, values=(), *, restored=False):
        super().__init__(values)
        self.positions = {v.record_id: i for i, v in enumerate(self)}
        self.rows = WorkMap({v.record_id: v for v in self})
        if restored:
            self.rows.changed.clear()

    def append(self, value):
        self.positions[value.record_id] = len(self)
        super().append(value)
        self.rows[value.record_id] = value

    def __setitem__(self, position, value):
        if isinstance(position, slice):
            raise TypeError("planned record changes require an exact record")
        super().__setitem__(position, value)
        self.rows[value.record_id] = value


def enable_plan_parts(plan, *, restored=False):
    ledger = CoverageLedger(plan.ledger)
    if restored:
        ledger.changed.clear()
    return plan.model_copy(
        update={"ledger": ledger, "records": WorkRecords(plan.records, restored=restored)}
    )


def plan_header(plan):
    return {
        **plan.model_dump(mode="json", exclude={"records", "ledger"}),
        "current_record_count": len(plan.records),
    }


class CoverageLedger(WorkMap):
    """Current coverage totals maintained when a single record changes."""

    def __init__(self, values=()):
        from collections import Counter

        super().__init__(values)
        self.counts = Counter()
        self.task_ids = set()
        for value in self.values():
            self._count(value, 1)

    def _count(self, value, sign):
        self.counts[value.coverage_state] += sign
        self.counts["phase" + str(value.phase)] += sign
        if value.coverage_state != "unattempted":
            self.counts["executed_phase" + str(value.phase)] += sign
        for outcome in value.semantic_outcomes:
            self.counts["semantic:" + outcome] += sign
        if sign > 0:
            self.task_ids.update(value.task_ids)

    def __setitem__(self, key, value):
        if key in self:
            self._count(self[key], -1)
        super().__setitem__(key, value)
        self._count(value, 1)
