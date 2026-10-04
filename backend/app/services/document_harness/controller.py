"""Source-first discovery and alignment. State and model calls are injected ports."""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy

from .calls import MemoryCalls
from .continuation import (
    remaining_reading_window,
    restore_window_plan,
    serialize_window_plan,
    split_reading_window,
)
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
from .ranking import CardRanker, discovery_guidance, reading_card
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
            self.execution_policy["flow"] != "four_stage"
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
            else build_windows(ir)
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
        payload = {**payload, "execution_phase": cursor.get("phase", "discovery")}
        if cursor.get("phase") == "deterministic":
            raise ValueError("harness_deterministic_model_call_forbidden")
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
        self.resume_failed_work()
        if not self.state.get("cursor"):
            root = self.catalog.classes[self.catalog.root_class_iri]
            self.commit(
                {
                    "cursor": {
                        "main": {
                            "phase": "discovery",
                            "skeleton_window_id": None,
                            "semantic_step": None,
                            "semantic_window_id": None,
                            "planned_steps": [],
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
        try:
            while not self.should_stop():
                cursor = self.state["cursor"]["main"]
                phase = cursor["phase"]
                if phase == "discovery":
                    self.read_windows()
                elif phase == "skeleton":
                    self.run_skeleton()
                elif phase == "semantic":
                    self.run_semantic()
                elif phase == "deterministic":
                    from .work_execution import plan_calibration_work

                    self.plan_once("deterministic", plan_calibration_work)
                    if not self.drain_work(None):
                        self.set_phase("done", "complete")
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

    def resume_failed_work(self):
        failed = [row for row in self.state.get("work", {}).values()
                  if row["status"] == "failed" and row.get("retryable", False)]
        if not failed:
            return
        phases = ("skeleton", "semantic", "deterministic")
        phase = min((row["phase"] for row in failed), key=phases.index)
        steps = {"referent_alignment": "referents", "entity_review": "entities",
                 "coreference_review": "coreference"}
        semantic_step = min((steps.get(row["kind"], "assertions") for row in failed
                             if row["phase"] == "semantic"),
                            key=("referents", "entities", "coreference", "assertions").index,
                            default="assertions")
        cursor = self.state["cursor"]["main"]
        self.commit({"work": {row["id"]: {**row, "status": "ready", "reason_code": None}
                              for row in failed},
                     "cursor": {"main": {**cursor, "phase": phase,
                                          "semantic_step": semantic_step,
                                          "stage": "planning"}}})

    def plan_once(self, marker, planner):
        cursor = self.state["cursor"]["main"]
        if marker in cursor["planned_steps"]:
            return
        changes = planner(self, commit=False)
        current = changes.get("cursor", {}).get("main", cursor)
        changes["cursor"] = {"main": {**current,
                                      "planned_steps": [*current["planned_steps"], marker]}}
        self.commit(changes)

    def run_skeleton(self):
        from .work_execution import materialize_skeleton, plan_skeleton_work

        self.plan_once("skeleton:materialize", materialize_skeleton)
        cursor = self.state["cursor"]["main"]
        available = [row for row in self.state["windows"].values()
                     if row["skeleton_step"] != "done"]
        if not available:
            self.plan_once("skeleton:references", plan_skeleton_work)
            if not self.drain_work(None):
                self.set_phase("semantic", "referent_alignment", semantic_step="referents")
            return
        key = cursor.get("skeleton_window_id")
        if key is None:
            row = min(available, key=lambda row: (row["order"], row["plan"]["id"]))
            key = row["plan"]["id"]
            self.commit({"cursor": {"main": {**cursor, "skeleton_window_id": key,
                                             "stage": row["skeleton_step"]}}})
        window = next(w for w in self.windows if w.id == key)
        step = self.state["windows"][key]["skeleton_step"]
        if step == "type_alignment":
            self.type_alignment(window)
        else:
            ids = set(self.state.get("window_entities", {}).get(key, {}).get("ids", []))
            ids.update(entity["id"] for entity in self.state["entities"].values()
                       if entity.get("window_id") == key)
            delta = {"entities": {eid: self.state["entities"][eid] for eid in ids},
                     "hints": {hid: hint for hid, hint in self.state.get("hints", {}).items()
                               if hint.get("window_id") == key}}
            root = self.state["entities"]["document"]
            root_fields = [fid for fid in root.get("field_ids", [])
                           if self.field_visible(fid, window)]
            if root_fields:
                delta["entities"]["document"] = {**root, "field_ids": root_fields}
            self.plan_once("skeleton:window:" + key + ":" + step,
                           lambda engine, commit: plan_skeleton_work(
                               engine, commit=commit, delta=delta, kind=step,
                               include_references=False))
            if not self.drain_work(window, kind=step):
                self.advance("relation_alignment" if step == "property_alignment" else "done")

    def run_semantic(self):
        from .work_execution import plan_semantic_work

        step = self.state["cursor"]["main"]["semantic_step"]
        self.plan_once("semantic:" + step,
                       lambda engine, commit: plan_semantic_work(engine, step, commit=commit))
        if self.drain_work(None):
            return
        steps = ("referents", "entities", "coreference", "assertions")
        index = steps.index(step)
        if index == len(steps) - 1:
            self.set_phase("deterministic", "planning")
        else:
            self.set_phase("semantic", "planning", semantic_step=steps[index + 1])

    def set_phase(self, phase, stage, *, semantic_step=None):
        cursor = self.state["cursor"]["main"]
        if cursor["active_batches"]:
            raise ValueError("harness_phase_has_pending_batches")
        self.commit(
            {
                "cursor": {
                    "main": {**cursor, "phase": phase, "stage": stage, "skeleton_window_id": None,
                             "semantic_step": semantic_step, "semantic_window_id": None}
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
            "skeleton_step": "type_alignment",
        }

    def update_reading(self, *, stage=None):
        by_source, processed, total = defaultdict(list), defaultdict(list), defaultdict(list)
        for row in self.state["windows"].values():
            for ref in row.get("completed_ranges", []):
                by_source[ref["evidence_id"]].append((ref["start"], ref["end"]))
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
        key = cursor["skeleton_window_id"]
        row = dict(changes.get("windows", {}).get(key, self.state["windows"][key]))
        row["skeleton_step"] = stage
        cursor["stage"] = "planning" if stage == "done" else stage
        if row["skeleton_step"] == "done":
            cursor["skeleton_window_id"] = None
        changes.setdefault("windows", {})[key] = row
        changes["cursor"] = {"main": cursor}
        self.commit(changes)

    def drain_work(self, window, *, kind=None):
        from .work_execution import drain_work

        return drain_work(self, window, kind=kind)

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
        payload = {**window.discovery_payload(), "execution_phase": "discovery"}
        payload["document"] = {
            "entity_id": "document",
            "role": "document_root",
            "label": self.state["entities"]["document"]["label"],
            "type": self.catalog.classes[self.catalog.root_class_iri].label,
            "class_iri": self.catalog.root_class_iri,
            "relation_guidance": reading_card(
                self.catalog.classes[self.catalog.root_class_iri]
            )["relations"],
            "property_guidance": [
                {"label": prop.label, "description": prop.description}
                for prop in self.catalog.classes[self.catalog.root_class_iri].properties
                if prop.constraint_status == "resolved"
            ],
        }
        payload["schema_guidance"] = discovery_guidance(self.catalog, [])
        schema = stage_schema(
            "discover",
            class_iris=[c["iri"] for c in payload["schema_guidance"]["classes"]],
            source_ids=[s["source_id"] for s in window.sources],
            field_ids=[f["alias"] for f in window.fields],
            primary_source_ids=[r["source_id"] for r in payload["reading_scope"]],
            quote_fragments=window.citation_choices(),
        )
        if request_size("discover", payload, schema) > self.max_request_bytes:
            key, item = self.observation(
                window, "阅读输入超出预算", "调用前拆分阅读范围", kind="scope"
            )
            self.finish_window(window, {"observations": {key: item}}, complete=False)
            return None
        info = self.state["windows"][window.id]
        guidance = info.get("ranked_class_iris")
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
        payload["schema_guidance"] = discovery_guidance(self.catalog, guidance)
        class_iris = [c["iri"] for c in payload["schema_guidance"]["classes"]]
        self.commit({"windows": {window.id: {
            **info, "ranked_class_iris": guidance, "guidance_class_iris": class_iris,
        }}})
        schema = stage_schema(
            "discover", class_iris=class_iris,
            source_ids=[s["source_id"] for s in window.sources],
            field_ids=[f["alias"] for f in window.fields],
            primary_source_ids=[r["source_id"] for r in payload["reading_scope"]],
            quote_fragments=window.citation_choices(),
        )
        if request_size("discover", payload, schema) > self.max_request_bytes:
            key, item = self.observation(
                window, "阅读输入超出预算", "调用前拆分阅读范围", kind="scope"
            )
            self.finish_window(window, {"observations": {key: item}}, complete=False)
            return None
        return payload, schema

    def finish_window(self, window, changes, *, complete, batch_id=None):
        row = {**self.state["windows"][window.id], **changes.get("windows", {}).get(window.id, {})}
        row.pop("lookup_work", None)
        continued = not complete and bool(row.get("completed_ranges"))
        if continued:
            children = [remaining_reading_window(self.ir, window, row["completed_ranges"])]
        elif not complete and row["depth"] < self.execution_policy["reading_split_depth"]:
            children = split_reading_window(self.ir, window)
        else:
            children = []
        row["reading_state"] = "split" if children else "complete" if complete else "incomplete"
        row["children"] = [w.id for w in children]
        for i, child in enumerate(children):
            changes.setdefault("windows", {})[child.id] = self.window_row(
                child,
                [*row["order"], i],
                window.id,
                row["depth"] + int(not continued),
            )
            self.windows.append(child)
        changes.setdefault("windows", {})[window.id] = row
        self.commit(changes, batch_id=batch_id)
        self.update_reading()

    def register_discovery(self, window, answer, lookup_info, *, batch_id=None):
        delta = decode_local_discovery(
            self.ir, window, answer, lookup_info, self.state["entities"]["document"],
            class_iris=self.state["windows"][window.id].get("guidance_class_iris", []),
        )
        changes = merge_local_discovery(
            self.state,
            delta,
            {key: row["order"] for key, row in self.state["windows"].items()},
        )
        changes.setdefault("windows", {}).setdefault(window.id, {})["result_saved"] = True
        changes["windows"][window.id]["completed_ranges"] = delta["completed_ranges"]
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
                    self.set_phase("skeleton", "planning")
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
                or self.state.get("cursor", {}).get("main", {}).get("phase") != "skeleton"
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
                    "value_components": value_components(f),
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
            self.advance("property_alignment")
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

        def batch_input(items, selected_entities):
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
            return window, ids, payload, schema

        def batch(items, selected_entities, *, compare=False):
            window, ids, payload, schema = batch_input(items, selected_entities)
            if (
                self.max_request_bytes is not None
                and request_size("type_alignment", payload, schema) > self.max_request_bytes
            ):
                # Prefer two entity batches with complete competing menus. Do
                # not recursively multiply a large catalog across tiny batches
                # when two halves still cannot fit; shard that menu instead.
                if len(selected_entities) >= 2:
                    middle = len(selected_entities) // 2
                    halves = (selected_entities[:middle], selected_entities[middle:])
                    if compare or all(
                        request_size("type_alignment", part[2], part[3]) <= self.max_request_bytes
                        for part in (batch_input(items, half) for half in halves)
                    ):
                        for half in halves:
                            batch(items, half, compare=compare)
                        return
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
        # Prefer the complete menu when it fits in at most two entity batches;
        # otherwise retain guided alternatives together in the card shards.
        batch([*preferred, *remainder], entities)
        proposals = {
            entity["id"]: {
                choice.class_iri
                for choice, evidence, error in choices[entity["id"]]
                if choice.class_iri and choice.confidence >= 0.7 and evidence and not error
            }
            for entity in entities
        }
        comparisons = []
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
                group = next((group for menu, group in comparisons if menu == proposed_iris
                              and all(not overlapping_referents(entity, other)
                                      for other in group)), None)
                if group is None:
                    group = []
                    comparisons.append((proposed_iris, group))
                group.append(entity)
        for menu, group in comparisons:
            batch([card for card in classes if card["iri"] in menu], group, compare=True)
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
                    state="candidate",
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
        self.advance("property_alignment", changes=changes)

    def referent_alignment(self, window):
        run_referent_alignment(self, window)

    def entity_review(self, window, work_rows=None):
        base_window = window
        candidates = [
            entity
            for entity in self.entities(window)
            if entity["state"] == "candidate" and entity.get("class_iri")
            and not entity.get("refined_member_ids")
            and (work_rows is None or entity["id"] in {r["input"]["entity_id"] for r in work_rows})
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
            from .work_execution import complete_work

            updates = {"entities": changes}
            for work in work_rows or []:
                if work["input"]["entity_id"] in changes:
                    complete_work(updates, work, outputs=[work["input"]["entity_id"]])
            for row in changes.values():
                row["verification"] = {"method": "llm", "semantic_verdict": row["state"],
                                       "rule_id": None, "rule_version": None}
                row["calibration"] = None
            self.commit(updates, batch_id=batch_id)

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
