"""Source-first discovery and alignment. State and model calls are injected ports."""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy

from .calls import MemoryCalls
from .continuation import restore_window_plan, serialize_window_plan, split_reading_window
from .lookup import finish_discovery, next_discovery
from .model import request_size
from .observations import value_components
from .ontology import SchemaCatalog, identity_guidance, model_menu
from .planning import build_source_index, context_window
from .protocols import (
    STAGES,
    discovery_model,
    stage_schema,
    validate_paid_output,
)
from .ranking import CardRanker, reading_guidance
from .reading import decode_local_discovery, merge_local_discovery
from .referents import binding_input, run_referent_alignment
from .source import build_windows, identity, quote_for_reference, references_cover
from .work import DEFAULT_POLICY, WorkIndex, digest


class Paused(Exception):
    pass


def overlapping_referents(left, right):
    a, b = left.get("referent"), right.get("referent")
    return bool(
        a
        and b
        and a["source_id"] == b["source_id"]
        and a["start"] < b["end"]
        and b["start"] < a["end"]
    )


class Engine:
    def __init__(
        self,
        *,
        ir,
        catalog,
        state,
        invoke,
        save,
        should_stop,
        max_request_bytes=None,
        rank=None,
        lookup=None,
        policy=None,
        prepare_batch=None,
        invoke_prepared=None,
        batch_request=None,
        rank_pairs=None,
        submit_prepared=None,
        collect_prepared=None,
        settle_prepared=None,
        call_result=None,
        calls=None,
    ):
        self.ir, self.catalog = ir, SchemaCatalog.model_validate(catalog)
        self.state = deepcopy(state)
        self.invoke, self.save, self.should_stop = invoke, save, should_stop
        self.policy = deepcopy(policy or {})
        self.execution_policy = {**DEFAULT_POLICY, **self.policy.get("execution_policy", {})}
        self.max_request_bytes = max_request_bytes or self.execution_policy["wire_bytes_per_call"]
        self.rank, self.lookup = rank or CardRanker().rank, lookup
        self.prepare_batch, self.invoke_prepared = prepare_batch, invoke_prepared
        self.batch_request, self.rank_pairs = batch_request, rank_pairs
        self._memory_calls = None
        if prepare_batch is None:
            calls = self._memory_calls = calls or MemoryCalls(invoke)
            self.prepare_batch, self.invoke_prepared = calls.prepare, calls.invoke_prepared
            self.batch_request, call_result = calls.request, calls.result
            submit_prepared, collect_prepared, settle_prepared = (
                calls.submit,
                calls.collect,
                calls.settle,
            )
        self.submit_prepared, self.collect_prepared = submit_prepared, collect_prepared
        self.settle_prepared, self.call_result = settle_prepared, call_result
        if (
            self.execution_policy["flow"] != "local_reading"
            or type(self.execution_policy["reading_concurrency"]) is not int
            or self.execution_policy["reading_concurrency"] not in (1, 2)
        ):
            raise ValueError("harness_new_run_required")
        self.windows = (
            [
                restore_window_plan(ir, row["plan"])
                for row in sorted(
                    self.state["windows"].values(),
                    key=lambda row: row["order"],
                )
            ]
            if self.state.get("windows")
            else build_windows(ir, max_chars=4800, max_sources=40)
        )
        self.source_index = build_source_index(ir, self.state)
        self.work_index = WorkIndex(self.state)

    @property
    def invoke(self):
        return self._invoke

    @invoke.setter
    def invoke(self, value):
        self._invoke = value
        if getattr(self, "_memory_calls", None) is not None:
            self._memory_calls.invoke = value

    def commit(self, changes, *, batch_id=None):
        from .work_execution import enforce_group_consistency, invalidate_changes

        changes = deepcopy(changes)
        invalidate_changes(self, changes)
        enforce_group_consistency(self, changes)
        cursor = deepcopy(
            changes.get("cursor", {}).get(
                "main",
                self.state.get("cursor", {}).get("main", {}),
            )
        )
        if batch_id is not None:
            if batch_id not in cursor.get("active_batches", {}):
                raise ValueError("harness_batch_not_active")
            del cursor["active_batches"][batch_id]
        if cursor:
            windows = {**self.state.get("windows", {}), **changes.get("windows", {})}
            leaves = [row for row in windows.values() if not row["children"]]
            complete = sum(row["reading_state"] == "complete" for row in leaves)
            incomplete = sum(row["reading_state"] == "incomplete" for row in leaves)
            cursor["reading_windows"] = {
                "total": len(leaves),
                "saved": complete + incomplete,
                "complete": complete,
                "incomplete": incomplete,
                "active": len(
                    {
                        b["window_id"]
                        for b in cursor.get("active_batches", {}).values()
                        if b["stage"] == "discover"
                    }
                ),
            }
            changes.setdefault("cursor", {})["main"] = cursor
        self.save(changes)
        for domain, rows in changes.items():
            self.state.setdefault(domain, {})
            for key, value in rows.items():
                if value is None:
                    self.state.setdefault(domain, {}).pop(key, None)
                else:
                    self.state.setdefault(domain, {})[key] = deepcopy(value)
        self.source_index.update(changes.get("entities", {}))
        self.work_index.update(changes.get("work", {}))

    def prepare_call(
        self, stage, payload, schema, *, window=None, targets=None, aliases=None, step=None
    ):
        if self.should_stop():
            raise Paused()
        if request_size(stage, payload, schema) > self.max_request_bytes:
            raise ValueError("HARNESS_EVIDENCE_CONTEXT_TOO_LARGE")
        cursor = self.state.get("cursor", {}).get("main", {})
        target = {
            "window_id": window.id if window else None,
            "step": step or stage,
            "targets": targets
            or [
                {
                    "domain": "windows",
                    "id": window.id if window else "final",
                    "dependency_hash": digest(payload),
                }
            ],
            "alias_bindings": {
                **(
                    {alias: key for key, alias in self.entity_aliases(window).items()}
                    if window and stage != "discover"
                    else {}
                ),
                **({f["alias"]: f["id"] for f in window.fields} if window else {}),
                **(aliases or {}),
            },
            "source_bindings": {
                s["source_id"]: [s["evidence_id"], s["offset"], s["offset"] + len(s["text"])]
                for s in window.sources
            }
            if window
            else {},
        }
        active = cursor.get("active_batches", {})
        existing = next((b for b in active.values() if b["window_id"] == target["window_id"]), None)
        if existing:
            saved = self.batch_request(existing)
            work_batch = all(t["domain"] == "work" for t in existing["targets"])
            if (
                existing["stage"] != stage
                or existing["step"] != target["step"]
                or existing["targets"] != target["targets"]
                or existing["source_bindings"] != target["source_bindings"]
                or existing["alias_bindings"] != target["alias_bindings"]
                or saved["schema"] != schema
                or (not work_batch and saved["payload"] != payload)
            ):
                raise ValueError("harness_active_batch_input_changed")
            return existing
        batch = self.prepare_batch(stage, payload, schema, target)
        changes = {
            "cursor": {
                "main": {
                    **cursor,
                    "active_batches": {
                        **active,
                        batch["batch_id"]: batch,
                    },
                }
            }
        }
        for item in batch["targets"]:
            if item["domain"] == "work":
                row = self.state["work"][item["id"]]
                changes.setdefault("work", {})[item["id"]] = {
                    **row,
                    "last_call_key": batch["call_key"],
                }
        self.commit(changes)
        return batch

    def call(self, stage, payload, schema, **context):
        batch = self.prepare_call(stage, payload, schema, **context)
        output = self.invoke_prepared(batch)
        validate_paid_output(stage, payload, output)
        response_model = (
            discovery_model(payload.get("lookup_mode")) if stage == "discover" else STAGES[stage]
        )
        return response_model.model_validate(output), batch["batch_id"]

    def run(self):
        # A new Engine is entered only for the initial run or explicit continue.
        # Keep the prepared batch and paid answer, but make failed work eligible.
        failed = {
            key: {**row, "status": "ready", "reason_code": None}
            for key, row in self.state.get("work", {}).items()
            if row["status"] == "failed"
        }
        if failed:
            self.commit({"work": failed})
        if not self.state.get("cursor"):
            root = self.catalog.classes[self.catalog.root_class_iri]
            self.commit(
                {
                    "cursor": {
                        "main": {
                            "phase": "reading",
                            "entity_window_id": None,
                            "active_batches": {},
                            "stage": "discover",
                            "scope_complete": False,
                            "reading": {
                                "total_characters": 0,
                                "processed_characters": 0,
                                "complete_characters": 0,
                                "complete": False,
                            },
                        }
                    },
                    "windows": {w.id: self.window_row(w, [i]) for i, w in enumerate(self.windows)},
                    "entities": {
                        "document": {
                            "id": "document",
                            "label": self.ir.title or root.label,
                            "role": "document_root",
                            "class_iri": root.iri,
                            "class_label": root.label,
                            "state": "accepted",
                            "reason": "用户选择的文档类型；不是正文实体身份",
                            "evidence": [],
                            "field_ids": [],
                            "window_id": None,
                        }
                    },
                }
            )
            self.update_reading()
        planned = set()
        try:
            while not self.should_stop():
                cursor = self.state["cursor"]["main"]
                phase = cursor["phase"]
                if phase == "reading":
                    self.read_windows()
                elif phase == "entities":
                    available = [
                        row
                        for row in self.state["windows"].values()
                        if row["entity_phase"] != "done"
                    ]
                    if not available:
                        self.set_phase("coreference", "coreference_review")
                        continue
                    key = cursor.get("entity_window_id")
                    if key is None:
                        row = min(available, key=lambda row: (row["order"], row["plan"]["id"]))
                        key = row["plan"]["id"]
                        self.commit(
                            {
                                "cursor": {
                                    "main": {
                                        **cursor,
                                        "entity_window_id": key,
                                        "stage": row["entity_phase"],
                                    }
                                }
                            }
                        )
                    window = next(w for w in self.windows if w.id == key)
                    getattr(self, self.state["windows"][key]["entity_phase"])(window)
                elif phase in {"coreference", "graph"}:
                    from .work_execution import plan_coreference_work, plan_graph_work

                    if phase not in planned:
                        (plan_coreference_work if phase == "coreference" else plan_graph_work)(self)
                        planned.add(phase)
                    if self.drain_work(None):
                        continue
                    if any(w["status"] == "failed" for w in self.state.get("work", {}).values()):
                        raise ValueError("harness_work_failed")
                    self.set_phase("graph", "planning") if phase == "coreference" else (
                        self.set_phase("done", "complete")
                    )
                elif phase == "done":
                    self.update_reading(stage="complete")
                    return
                else:
                    raise ValueError("harness_new_run_required")
        except Paused:
            return
        finally:
            if self.settle_prepared:
                self.settle_prepared()
            if self._memory_calls:
                self._memory_calls.close()

    def set_phase(self, phase, stage):
        cursor = self.state["cursor"]["main"]
        if cursor["active_batches"]:
            raise ValueError("harness_phase_has_pending_batches")
        self.commit(
            {
                "cursor": {
                    "main": {**cursor, "phase": phase, "stage": stage, "entity_window_id": None}
                }
            }
        )

    @staticmethod
    def window_row(window, order, parent_id=None, depth=0):
        return {
            "plan": serialize_window_plan(window),
            "parent_id": parent_id,
            "children": [],
            "order": order,
            "depth": depth,
            "reading_state": "pending",
            "entity_phase": "type_alignment",
        }

    def update_reading(self, *, stage=None):
        by_source, processed, total = defaultdict(list), defaultdict(list), defaultdict(list)
        for row in self.state["windows"].values():
            for ref in row["plan"]["primary_ranges"]:
                unit = self.ir.unit(ref["evidence_id"])
                if unit.navigation_role or not unit.text.strip():
                    continue
                if row["parent_id"] is None:
                    total[ref["evidence_id"]].append((ref["start"], ref["end"]))
                if row.get("result_saved"):
                    processed[ref["evidence_id"]].append((ref["start"], ref["end"]))
                if not row["children"] and row["reading_state"] == "complete":
                    by_source[ref["evidence_id"]].append((ref["start"], ref["end"]))

        def length(ranges):
            result = 0
            for intervals in ranges.values():
                end = 0
                for start, stop in sorted(intervals):
                    result += max(0, stop - max(start, end))
                    end = max(end, stop)
            return result

        complete = all(
            row["reading_state"] == "complete"
            for row in self.state["windows"].values()
            if not row["children"]
        )
        cursor = self.state["cursor"]["main"]
        self.commit(
            {
                "cursor": {
                    "main": {
                        **cursor,
                        "scope_complete": complete,
                        "stage": stage or cursor["stage"],
                        "reading": {
                            "total_characters": length(total),
                            "processed_characters": length(processed),
                            "complete_characters": length(by_source),
                            "complete": complete,
                        },
                    }
                }
            }
        )

    def advance(self, stage, *, changes=None):
        changes = changes or {}
        cursor = dict(self.state["cursor"]["main"])
        key = cursor["entity_window_id"]
        row = dict(changes.get("windows", {}).get(key, self.state["windows"][key]))
        row["entity_phase"] = "done" if stage == "planning" else stage
        cursor["stage"] = "entity_review" if stage == "planning" else stage
        if row["entity_phase"] == "done":
            cursor["entity_window_id"] = None
        changes.setdefault("windows", {})[key] = row
        changes["cursor"] = {"main": cursor}
        self.commit(changes)

    def drain_work(self, window):
        from .work_execution import drain_work

        return drain_work(self, window)

    def observation(self, window, label, reason, evidence=(), *, kind="validation", **context):
        key = identity("observation", window.id, label, reason, evidence, kind, context)
        return key, {
            "id": key,
            "label": label,
            "reason": reason,
            "evidence": list(evidence),
            "window_id": window.id,
            "discovery_window_id": window.id,
            "kind": kind,
            **context,
        }

    def field_observation(self, window, field, reason):
        key = identity("field_observation", field["id"], window.id)
        return key, {
            "id": key,
            "kind": "field",
            "field_id": field["id"],
            "label": field["label"] or "独立原文",
            "reason": reason,
            "evidence": field["evidence"],
            "window_id": window.id,
            "discovery_window_id": window.id,
        }

    def record_field_alignment(
        self,
        changes,
        window,
        field,
        subject,
        predicates,
        reason,
        state,
    ):
        """Keep current menu outcomes scoped to their subject and actual type card."""
        key, initial = self.field_observation(
            window,
            field,
            "原字段观察；采信状态见对应属性候选",
        )
        # An older field brought into this alignment window was not necessarily
        # discovered under this window's reading cards.
        initial["discovery_window_id"] = None
        current = {**self.state.get("observations", {}), **changes["observations"]}
        keys = [
            oid
            for oid, obs in current.items()
            if obs.get("kind") == "field" and obs.get("field_id") == field["id"]
        ] or [key]
        predicate_iris = sorted(predicates)
        outcome_key = identity("alignment", subject["id"], subject["class_iri"], predicate_iris)
        for oid in keys:
            row = deepcopy(current.get(oid, initial))
            row.setdefault("alignment_outcomes", {})[outcome_key] = {
                "subject_id": subject["id"],
                "class_iri": subject["class_iri"],
                "predicate_iris": predicate_iris,
                "reason": reason,
                "state": state,
            }
            changes["observations"][oid] = row

    def pending_property_subjects(self):
        return [
            key
            for key, entity in self.state["entities"].items()
            if entity["state"] == "accepted"
            and entity.get("class_iri")
            and entity["role"] != "document_root"
            and any(
                entity.get("aligned_fields", {}).get(field_id) != entity["class_iri"]
                for field_id in entity["field_ids"]
            )
        ]

    def discovery_input(self, window):
        payload = window.payload()
        payload["document"] = {
            "label": self.state["entities"]["document"]["label"],
            "type": self.catalog.classes[self.catalog.root_class_iri].label,
            "property_guidance": [
                {"label": prop.label, "description": prop.description}
                for prop in self.catalog.classes[self.catalog.root_class_iri].properties
                if prop.constraint_status == "resolved"
            ],
        }
        schema = stage_schema(
            "discover",
            source_ids=[s["source_id"] for s in window.sources],
            field_ids=[f["alias"] for f in window.fields],
            primary_source_ids=[r["source_id"] for r in payload["reading_scope"]],
        )
        payload["schema_guidance"] = reading_guidance(self.catalog, [])
        if request_size("discover", payload, schema) > self.max_request_bytes:
            key, item = self.observation(
                window, "阅读输入超出预算", "调用前拆分阅读范围", kind="scope"
            )
            self.finish_window(window, {"observations": {key: item}}, complete=False)
            return None
        info = self.state["windows"][window.id]
        guidance = info.get("guidance_class_iris")
        if guidance is None:
            ranked = self.rank(
                self.catalog,
                window.payload(),
                max(0, self.max_request_bytes - request_size("discover", payload, schema)),
            )
            guidance = ranked["selected_iris"]
            if (
                ranked["snapshot_id"] != self.catalog.snapshot_id
                or len(guidance) != len(set(guidance))
                or not set(guidance) <= set(self.catalog.reachable_class_iris)
            ):
                raise ValueError("harness_ranking_catalog_mismatch")
            self.commit({"windows": {window.id: {**info, "guidance_class_iris": guidance}}})
        payload["schema_guidance"] = reading_guidance(self.catalog, guidance)
        return payload, schema

    def finish_window(self, window, changes, *, complete, batch_id=None):
        row = {**self.state["windows"][window.id], **changes.get("windows", {}).get(window.id, {})}
        row.pop("lookup_work", None)
        children = (
            split_reading_window(self.ir, window)
            if (not complete and row["depth"] < self.execution_policy["reading_split_depth"])
            else []
        )
        row["reading_state"] = "split" if children else "complete" if complete else "incomplete"
        row["children"] = [w.id for w in children]
        for i, child in enumerate(children):
            changes.setdefault("windows", {})[child.id] = self.window_row(
                child,
                [*row["order"], i],
                window.id,
                row["depth"] + 1,
            )
            self.windows.append(child)
        changes.setdefault("windows", {})[window.id] = row
        self.commit(changes, batch_id=batch_id)
        self.update_reading()

    def register_discovery(self, window, answer, lookup_info, *, batch_id=None):
        delta = decode_local_discovery(
            self.ir, window, answer, lookup_info, self.state["entities"]["document"]
        )
        changes = merge_local_discovery(
            self.state,
            delta,
            {key: row["order"] for key, row in self.state["windows"].items()},
        )
        changes.setdefault("windows", {}).setdefault(window.id, {})["result_saved"] = True
        self.finish_window(window, changes, complete=delta["complete"], batch_id=batch_id)

    def reading_request(self, batch):
        window = next(w for w in self.windows if w.id == batch["window_id"])
        row = self.state["windows"][window.id]
        request = self.batch_request(batch)
        payload = window.payload()
        sources = {s["source_id"]: [s["evidence_id"], s["offset"], s["offset"] + len(s["text"])]
                   for s in window.sources}
        step = request["payload"].get("lookup_mode", "plain")
        if (row["reading_state"] != "pending" or batch["stage"] != "discover"
                or batch["step"] != step
                or (step == "refine") != bool(row.get("lookup_work"))
                or batch["source_bindings"] != sources
                or batch["alias_bindings"] != {f["alias"]: f["id"] for f in window.fields}
                or request["payload"]["sources"] != payload["sources"]
                or request["payload"]["fields"] != payload["fields"]
                or batch["targets"] != [{"domain": "windows", "id": window.id,
                                          "dependency_hash": digest(request["payload"])}]):
            raise ValueError("harness_active_batch_input_changed")
        return window, request

    def read_windows(self):
        try:
            while not self.should_stop():
                active = self.state["cursor"]["main"]["active_batches"]
                occupied = {b["window_id"] for b in active.values()}
                for batch in active.values():
                    self.reading_request(batch)
                    self.submit_prepared(batch)
                pending = sorted(
                    (
                        row
                        for key, row in self.state["windows"].items()
                        if row["reading_state"] == "pending" and key not in occupied
                    ),
                    key=lambda row: (row["order"], row["plan"]["id"]),
                )
                for row in pending:
                    if self.should_stop():
                        break
                    if (
                        len(self.state["cursor"]["main"]["active_batches"])
                        >= (self.execution_policy["reading_concurrency"])
                    ):
                        break
                    window = next(w for w in self.windows if w.id == row["plan"]["id"])
                    prepared = next_discovery(self, window)
                    if prepared is None:
                        continue
                    payload, schema, step = prepared
                    batch = self.prepare_call("discover", payload, schema, window=window, step=step)
                    self.submit_prepared(batch)
                active = list(self.state["cursor"]["main"]["active_batches"].values())
                if not active:
                    if any(
                        row["reading_state"] == "pending" for row in self.state["windows"].values()
                    ):
                        continue
                    self.set_phase("entities", "type_alignment")
                    return
                completed = self.collect_prepared(active)
                if self.should_stop():
                    return
                for batch, output, error in completed:
                    if error:
                        raise error
                    if self.should_stop():
                        return
                    window, request = self.reading_request(batch)
                    validate_paid_output(
                        "discover", request["payload"], output, schema=request["schema"]
                    )
                    answer = discovery_model(request["payload"].get("lookup_mode")).model_validate(
                        output,
                    )
                    finish_discovery(self, window, batch, answer)
        finally:
            self.settle_prepared()

    def entities(self, window):
        ids = (
            window.entity_ids
            if window.entity_ids is not None
            else self.state.get("window_entities", {}).get(window.id, {}).get("ids", [])
        )
        return [
            self.state["entities"][key]
            for key in sorted(ids, key=lambda key: self.source_index.position(
                self.state["entities"][key]) if key in self.state["entities"] else (-1, 0, key))
            if key in self.state["entities"]
            and (
                window.entity_ids is not None
                or self.state.get("cursor", {}).get("main", {}).get("phase") != "entities"
                or self.state["entities"][key].get("window_id") == window.id
            )
        ]

    def entity_aliases(self, window):
        return {
            entity["id"]: f"E{i}"
            for i, entity in enumerate(
                [
                    self.state["entities"]["document"],
                    *self.entities(window),
                ]
            )
        }

    def field_alias(self, field_id, window):
        return next((f["alias"] for f in window.fields if f["id"] == field_id), None)

    def field_visible(self, field_id, window):
        field = self.state.get("fields", {}).get(field_id)
        return bool(
            field
            and field["evidence"]
            and all(quote_for_reference(window, ref) is not None for ref in field["evidence"])
        )

    def context_entities(self, window, entities, *, required_field_ids=()):
        """Document metadata follows this reading scope, not its accumulated history."""
        required = set(required_field_ids)
        return [
            {
                **entity,
                "field_ids": [
                    key
                    for key in entity["field_ids"]
                    if key in required or self.field_visible(key, window)
                ],
            }
            if entity["role"] == "document_root"
            else entity
            for entity in entities
        ]

    def entity_input(self, entity, window, *, typed=False):
        value = {
            "entity_id": self.entity_aliases(window)[entity["id"]],
            "label": entity["label"],
            "role": entity["role"],
            "name": quote_for_reference(window, entity["name"]) if entity.get("name") else None,
            "name_candidates": [
                q
                for ref in entity.get("name_candidates", [])
                if (q := quote_for_reference(window, ref))
            ],
            "anchor": quote_for_reference(window, entity["referent"])
            if entity.get("referent")
            else None,
            "type_confirmed": entity["state"] == "accepted",
            "type_basis": (
                "user_selected"
                if entity["role"] == "document_root"
                else "model_review"
                if entity["state"] == "accepted"
                else "unconfirmed"
            ),
            "evidence": list(
                dict.fromkeys(
                    q["source_id"]
                    for ref in entity["evidence"]
                    if (q := quote_for_reference(window, ref))
                )
            ),
            "fields": [
                {
                    "field_id": self.field_alias(f["id"], window),
                    "label": f["label"],
                    "value": f["value"],
                    "missing": f["missing"],
                    "value_components": value_components(
                        f,
                        confirmed=entity["state"] == "accepted",
                    ),
                    "sources": list(
                        dict.fromkeys(
                            q["source_id"]
                            for ref in f["evidence"]
                            if (q := quote_for_reference(window, ref))
                        )
                    ),
                }
                for key in entity.get("field_ids", [])
                if (f := self.state.get("fields", {}).get(key)) and self.field_alias(key, window)
            ],
        }
        if typed:
            value["class_iri"] = entity["class_iri"]
        if entity.get("identity_binding"):
            value["identity_binding"] = binding_input(entity, window)
        return value

    def type_alignment(self, window):
        base_window = window

        def input_hash(entity):
            return digest(
                {
                    "referent": entity.get("referent"),
                    "evidence": entity.get("evidence"),
                    "name": entity.get("name"),
                    "name_candidates": entity.get("name_candidates", []),
                    "role": entity.get("role"),
                    "identity_binding": entity.get("identity_binding"),
                    "fields": {
                        key: {
                            name: self.state.get("fields", {}).get(key, {}).get(name)
                            for name in ("label", "value", "missing", "evidence", "value_evidence")
                        }
                        for key in sorted(entity.get("field_ids", []))
                    },
                    "ontology": self.catalog.ontology_hash,
                }
            )

        entities = [
            e
            for e in self.entities(window)
            if e["state"] != "accepted" and e.get("type_input_hash") != input_hash(e)
        ]
        if not entities:
            self.advance("referent_alignment")
            return
        classes = [
            {
                "iri": card.iri,
                "label": card.label,
                "aliases": list(card.aliases),
                "description": card.description,
                "definition": [
                    d.model_dump(mode="json", exclude_none=True) for d in card.definition
                ],
            }
            for iri in self.catalog.reachable_class_iris
            if (card := self.catalog.classes[iri])
        ]
        choices = defaultdict(list)

        def batch(items, selected_entities, *, compare=False):
            window = context_window(
                self.ir,
                base_window,
                self.context_entities(base_window, selected_entities),
                self.state.get("fields", {}),
            )
            aliases = self.entity_aliases(window)
            ids = {aliases[e["id"]]: e["id"] for e in selected_entities}
            payload = {
                "sources": window.payload()["sources"],
                "entities": [self.entity_input(e, window) for e in selected_entities],
                "classes": items,
            }
            schema = stage_schema(
                "type_alignment",
                source_ids=[s["source_id"] for s in window.sources],
                entity_ids=list(ids),
                class_iris=[c["iri"] for c in items],
            )
            if (
                self.max_request_bytes is not None
                and request_size("type_alignment", payload, schema) > self.max_request_bytes
            ):
                if compare:
                    # Splitting this menu would recreate incomparable shard choices.
                    raise ValueError("harness_single_type_comparison_input_too_large")
                guided = [card for card in items if card["iri"] in guidance]
                others = [card for card in items if card["iri"] not in guidance]
                if guided and others:
                    batch(guided, selected_entities)
                    batch(others, selected_entities)
                elif len(items) >= 2:
                    middle = len(items) // 2
                    batch(items[:middle], selected_entities)
                    batch(items[middle:], selected_entities)
                elif len(selected_entities) >= 2:
                    middle = len(selected_entities) // 2
                    batch(items, selected_entities[:middle])
                    batch(items, selected_entities[middle:])
                else:
                    raise ValueError("harness_single_type_input_too_large")
                return
            task_key = digest({"payload": payload, "schema": schema})
            previous = self.state.get("type_work", {}).get(task_key)
            if previous:
                answer = STAGES["type_alignment"].model_validate(previous["answer"])
            else:
                targets = [
                    {
                        "domain": "entities",
                        "id": entity["id"],
                        "dependency_hash": input_hash(entity),
                    }
                    for entity in selected_entities
                ]
                answer, batch_id = self.call(
                    "type_alignment", payload, schema, window=window, targets=targets
                )
                self.commit(
                    {"type_work": {task_key: {"answer": answer.model_dump(mode="json")}}},
                    batch_id=batch_id,
                )
            if set(answer.entities) != set(ids):
                raise ValueError("type_alignment_entity_set_mismatch")
            for entity_id, choice in answer.entities.items():
                if choice.class_iri is not None and choice.class_iri not in {
                    c["iri"] for c in items
                }:
                    raise ValueError("type_outside_current_menu")
                try:
                    evidence = window.quotes(self.ir, choice.evidence)
                    error = (
                        "type_candidate_without_source"
                        if choice.class_iri and not evidence
                        else None
                    )
                except ValueError as exc:
                    evidence, error = [], str(exc)
                # Short source aliases belong to this exact entity batch. Bind
                # them now, before comparing proposals from different batches.
                if compare:
                    choices[ids[entity_id]] = [(choice, evidence, error)]
                else:
                    choices[ids[entity_id]].append((choice, evidence, error))

        guidance = self.state.get("windows", {}).get(window.id, {}).get("guidance_class_iris", [])
        by_iri = {card["iri"]: card for card in classes}
        preferred = [by_iri[iri] for iri in guidance if iri in by_iri]
        remainder = [card for card in classes if card["iri"] not in guidance]
        # Keep a single call when everything fits. On overflow batch() keeps the
        # guided alternatives together before visiting the rest of the catalog.
        batch([*preferred, *remainder], entities)
        proposals = {
            entity["id"]: {
                choice.class_iri
                for choice, evidence, error in choices[entity["id"]]
                if choice.class_iri and choice.confidence >= 0.7 and evidence and not error
            }
            for entity in entities
        }
        for entity in entities:
            proposed_iris = proposals[entity["id"]]
            if not proposed_iris:
                # Overlapping referents can suppress one another in a batch.
                # Borrow only their type menu for an independent reading, never
                # their identity, proof, or verdict. Distinct spans stay separate.
                proposed_iris = {
                    iri
                    for other in entities
                    if other["id"] != entity["id"] and overlapping_referents(entity, other)
                    for iri in proposals[other["id"]]
                }
            if len(proposed_iris) > 1 or proposed_iris and not proposals[entity["id"]]:
                # Shard confidence is not a calibrated ranking. Re-read the
                # referent against its entire candidate menu without treating
                # earlier proposal scores or rationales as source evidence.
                batch(
                    [card for card in classes if card["iri"] in proposed_iris],
                    [entity],
                    compare=True,
                )
        changes = {
            "entities": {},
            "type_work": {key: None for key in self.state.get("type_work", {})},
        }
        for entity in entities:
            options = choices[entity["id"]]
            selected = next(
                (
                    item
                    for item in options
                    if item[0].class_iri and item[0].confidence >= 0.7 and item[1] and not item[2]
                ),
                None,
            )
            entity = dict(entity)
            entity["type_input_hash"] = input_hash(entity)
            if selected is None:
                entity.update(
                    state="unresolved",
                    reason="；".join(item[2] or item[0].reason for item in options[:2]),
                    class_iri=None,
                    class_label=None,
                    type_evidence=[],
                )
                entity.pop("confidence", None)
            else:
                choice, evidence, _ = selected
                card = self.catalog.classes[choice.class_iri]
                entity.update(
                    class_iri=card.iri,
                    class_label=card.label,
                    state="candidate",
                    reason=choice.reason,
                    type_evidence=evidence,
                    confidence=choice.confidence,
                )
            changes["entities"][entity["id"]] = entity
        self.advance("referent_alignment", changes=changes)

    def referent_alignment(self, window):
        run_referent_alignment(self, window)

    def entity_review(self, window):
        base_window = window
        candidates = [
            entity
            for entity in self.entities(window)
            if entity["state"] == "candidate" and entity.get("class_iri")
        ]

        def batch(items):
            window = context_window(
                self.ir,
                base_window,
                self.context_entities(base_window, items),
                self.state.get("fields", {}),
                extra_refs=[ref for row in items for ref in row["evidence"]],
            )
            aliases = {row["id"]: f"C{i + 1}" for i, row in enumerate(items)}
            rows = []
            for entity in items:
                card = model_menu(self.catalog, [entity["class_iri"]])["classes"][0]
                definition = {
                    k: v
                    for k, v in card.items()
                    if k in {"iri", "label", "description", "definition"}
                }
                definition.update(
                    identity_guidance(
                        self.catalog.classes[entity["class_iri"]],
                        annotation_contracts=self.catalog.annotation_contracts,
                    )
                )
                rows.append(
                    {
                        "id": aliases[entity["id"]],
                        "kind": "entities",
                        "label": entity["label"],
                        "role": entity["role"],
                        "class_iri": entity["class_iri"],
                        "class_definition": definition,
                        "subject": self.entity_input(entity, window, typed=True),
                        "evidence": [
                            quote_for_reference(window, ref)["source_id"]
                            for ref in entity["evidence"]
                        ],
                    }
                )
            payload = {"sources": window.payload()["sources"], "candidates": rows}
            schema = stage_schema(
                "entity_review",
                source_ids=[s["source_id"] for s in window.sources],
                candidate_ids=list(aliases.values()),
            )
            if (
                len(items) > 6
                or request_size("entity_review", payload, schema) > self.max_request_bytes
            ):
                if len(items) < 2:
                    raise ValueError("harness_single_review_input_too_large")
                middle = len(items) // 2
                batch(items[:middle])
                batch(items[middle:])
                return
            call_aliases = {alias: key for key, alias in aliases.items()}
            targets = [
                {"domain": "entities", "id": entity["id"], "dependency_hash": digest(rows[i])}
                for i, entity in enumerate(items)
            ]
            answer, batch_id = self.call(
                "entity_review",
                payload,
                schema,
                window=window,
                targets=targets,
                aliases=call_aliases,
            )
            changes = {}
            for entity in items:
                judgment = answer.judgments[aliases[entity["id"]]]
                try:
                    support = window.quotes(self.ir, judgment.evidence)
                    verdict, reason = judgment.verdict, judgment.reason
                    if verdict == "accepted" and (not support or judgment.confidence < 0.85):
                        verdict, reason = (
                            "unresolved",
                            "acceptance_requires_source_and_high_confidence",
                        )
                    if verdict == "accepted" and not self.review_sources_cover(
                        "entities", entity, support
                    ):
                        verdict, reason = "unresolved", "review_source_does_not_locate_claim"
                except ValueError as exc:
                    support, verdict, reason = [], "unresolved", str(exc)
                changes[entity["id"]] = {
                    **entity,
                    "state": verdict,
                    "reason": reason,
                    "review_evidence": support,
                    "source_verdict": verdict,
                    "source_reason": reason,
                }
            self.commit({"entities": changes}, batch_id=batch_id)

        groups = []
        for entity in candidates:
            group = next(
                (
                    group
                    for group in groups
                    if all(not overlapping_referents(entity, other) for other in group)
                ),
                None,
            )
            if group is None:
                group = []
                groups.append(group)
            group.append(entity)
        for group in groups:
            batch(group)
        self.advance("planning")

    def review_sources_cover(self, domain, candidate, support):
        """Exact claim anchors, without the former facet/composition proof graph."""
        required = []
        if domain == "entities":
            required.append(candidate["referent"])
        else:
            if domain == "properties":
                required.extend(self.state["fields"][candidate["field_id"]]["value_evidence"])
            endpoints = [candidate["subject_id"]]
            if domain == "relations":
                endpoints.append(candidate["object_id"])
            for key in endpoints:
                entity = self.state["entities"][key]
                if entity["role"] != "document_root":
                    required.append(entity["referent"])
        return all(references_cover(anchor, support) for anchor in required)
