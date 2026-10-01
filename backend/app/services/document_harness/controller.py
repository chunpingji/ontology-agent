"""Source-first discovery and alignment. State and model calls are injected ports."""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy

from .continuation import restore_window_plan, serialize_window_plan, split_reading_window
from .lookup import run_discovery
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
from .ranking import GUIDANCE_RULE, CardRanker, reading_card
from .referents import binding_input, run_referent_alignment
from .source import build_windows, identity, missing, quote_for_reference, references_cover
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
        self._call_window, self._call_targets = None, []
        self._paid_ready = False
        self._call_aliases = {}

    def commit(self, changes):
        from .work_execution import enforce_group_consistency, invalidate_changes

        changes = deepcopy(changes)
        invalidate_changes(self, changes)
        enforce_group_consistency(self, changes)
        if self._paid_ready:
            cursor = changes.setdefault("cursor", {}).get(
                "main", self.state.get("cursor", {}).get("main", {})
            )
            changes["cursor"]["main"] = {**cursor, "active_batch": None}
        self.save(changes)
        for domain, rows in changes.items():
            for key, value in rows.items():
                if value is None:
                    self.state.setdefault(domain, {}).pop(key, None)
                else:
                    self.state.setdefault(domain, {})[key] = deepcopy(value)
        self.source_index.update(changes.get("entities", {}))
        self.work_index.update(changes.get("work", {}))
        self._paid_ready = False
        self._call_targets = []
        self._call_aliases = {}

    def raw_call(self, stage, payload, schema):
        if self.should_stop():
            raise Paused()
        if request_size(stage, payload, schema) > self.max_request_bytes:
            raise ValueError("HARNESS_EVIDENCE_CONTEXT_TOO_LARGE")
        cursor = self.state.get("cursor", {}).get("main", {})
        if not self.prepare_batch:
            result = self.invoke(stage, payload, schema)
        else:
            batch = cursor.get("active_batch")
            window = self._call_window
            if batch is None:
                target = {
                    "window_id": cursor.get("active_window_id"),
                    "window_phase": self.state.get("windows", {})
                    .get(
                        cursor.get("active_window_id"),
                        {},
                    )
                    .get("phase"),
                    "targets": self._call_targets
                    or [
                        {
                            "domain": "windows",
                            "id": cursor.get("active_window_id") or "final",
                            "dependency_hash": digest(payload),
                        }
                    ],
                    "alias_bindings": {
                        **(
                            {alias: key for key, alias in self.entity_aliases(window).items()}
                            if window
                            else {}
                        ),
                        **(
                            {field["alias"]: field["id"] for field in window.fields}
                            if window
                            else {}
                        ),
                        **self._call_aliases,
                    },
                    "source_bindings": {
                        s["source_id"]: [
                            s["evidence_id"],
                            s["offset"],
                            s["offset"] + len(s["text"]),
                        ]
                        for s in window.sources
                    }
                    if window
                    else {},
                }
                batch = self.prepare_batch(stage, payload, schema, target)
                self.state["cursor"]["main"] = {**cursor, "active_batch": batch}
                for target in batch["targets"]:
                    if target["domain"] == "work":
                        self.state["work"][target["id"]]["last_call_key"] = batch["call_key"]
            else:
                saved = self.batch_request(batch)
                work_batch = all(target["domain"] == "work" for target in batch["targets"])
                sources = (
                    {
                        source["source_id"]: [
                            source["evidence_id"],
                            source["offset"],
                            source["offset"] + len(source["text"]),
                        ]
                        for source in window.sources
                    }
                    if window
                    else {}
                )
                if (
                    batch["stage"] != stage
                    or saved["schema"] != schema
                    or (not work_batch and saved["payload"] != payload)
                    or batch.get("source_bindings", {}) != sources
                ):
                    raise ValueError("harness_active_batch_input_changed")
            result = self.invoke_prepared(batch)
        self._paid_ready = True
        return result

    def call(self, stage, payload, schema):
        response_model = (
            discovery_model(payload.get("lookup_mode")) if stage == "discover" else STAGES[stage]
        )
        output = self.raw_call(stage, payload, schema)
        validate_paid_output(stage, payload, output)
        return response_model.model_validate(output)

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
                            "active_window_id": None,
                            "active_batch": None,
                            "stage": "discover",
                            "windows_total": len(self.windows),
                            "windows_discovered": 0,
                            "windows_reviewed": 0,
                            "scope_complete": False,
                            "reading": {
                                "total_characters": 0,
                                "processed_characters": 0,
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
                active = cursor.get("active_window_id")
                if active:
                    window = next(w for w in self.windows if w.id == active)
                    self._call_window = window
                    phase = self.state["windows"][active]["phase"]
                    getattr(self, phase)(window)
                    continue
                promoted = {
                    key: {**row, "phase": "type_alignment"}
                    for key, row in self.state["windows"].items()
                    if row["phase"] == "waiting_children"
                    and all(
                        self.state["windows"][child]["phase"] == "done" for child in row["children"]
                    )
                }
                if promoted:
                    self.commit({"windows": promoted})
                available = [
                    row
                    for row in self.state["windows"].values()
                    if row["phase"] not in {"done", "waiting_children"}
                ]
                if available:
                    row = min(available, key=lambda item: item["order"])
                    self.commit(
                        {
                            "cursor": {
                                "main": {
                                    **self.state["cursor"]["main"],
                                    "active_window_id": row["plan"]["id"],
                                    "stage": self.public_stage(row["phase"]),
                                }
                            }
                        }
                    )
                    continue
                if self.drain_work(None):
                    continue
                if any(w["status"] == "failed" for w in self.state.get("work", {}).values()):
                    raise ValueError("harness_work_failed")
                self.update_reading(stage="complete")
                return
        except Paused:
            return

    @staticmethod
    def window_row(window, order, parent_id=None, depth=0):
        return {
            "plan": serialize_window_plan(window),
            "parent_id": parent_id,
            "children": [],
            "order": order,
            "depth": depth,
            "phase": "discover",
            "discovery_complete": False,
            "discovery_attempted": False,
            "reading_incomplete": False,
        }

    @staticmethod
    def public_stage(phase):
        return {"waiting_children": "discover", "drain_work": "planning", "done": "planning"}.get(
            phase, phase
        )

    def update_reading(self, *, stage=None):
        by_source = defaultdict(list)
        total = defaultdict(list)
        for row in self.state["windows"].values():
            for ref in row["plan"]["primary_ranges"]:
                unit = self.ir.unit(ref["evidence_id"])
                if unit.navigation_role or not unit.text.strip():
                    continue
                if row["parent_id"] is None:
                    total[ref["evidence_id"]].append((ref["start"], ref["end"]))
                if not row["children"] and row["discovery_complete"]:
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
            row["discovery_complete"]
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
                            "processed_characters": length(by_source),
                            "complete": complete,
                        },
                        "windows_total": len(self.state["windows"]),
                        "windows_discovered": sum(
                            r["discovery_attempted"] for r in self.state["windows"].values()
                        ),
                        "windows_reviewed": sum(
                            r["phase"] == "done" for r in self.state["windows"].values()
                        ),
                    }
                }
            }
        )

    def advance(self, stage, *, changes=None, complete=None, reviewed=True):
        changes = changes or {}
        cursor = dict(self.state["cursor"]["main"])
        key = cursor["active_window_id"]
        window = next(w for w in self.windows if w.id == key)
        row = dict(changes.get("windows", {}).get(key, self.state["windows"][key]))
        if row["phase"] == "discover" and complete is not None:
            row.update(discovery_attempted=True, discovery_complete=complete)
            children = (
                split_reading_window(self.ir, window)
                if not complete and row["depth"] < self.execution_policy["reading_split_depth"]
                else []
            )
            if children:
                row.update(children=[w.id for w in children], phase="waiting_children")
                for i, child in enumerate(children):
                    changes.setdefault("windows", {})[child.id] = self.window_row(
                        child,
                        [*row["order"], i],
                        key,
                        row["depth"] + 1,
                    )
                    self.windows.append(child)
                cursor["active_window_id"] = None
            else:
                row.update(phase="type_alignment", reading_incomplete=not complete)
        else:
            row["phase"] = "done" if stage == "discover" else stage
            if row["phase"] == "done":
                cursor["active_window_id"] = None
        cursor["stage"] = self.public_stage(row["phase"])
        changes.setdefault("windows", {})[key] = row
        changes["cursor"] = {"main": cursor}
        self.commit(changes)
        self.update_reading()

    def planning(self, window):
        from .work_execution import plan_work

        plan_work(self, window)
        self.advance("drain_work")

    def drain_work(self, window):
        from .work_execution import drain_work

        changed = drain_work(self, window)
        if window is not None and not changed:
            self.advance("discover")
        return changed

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
        row = deepcopy(
            changes["observations"].get(
                key,
                self.state.get("observations", {}).get(key, initial),
            )
        )
        predicate_iris = sorted(predicates)
        outcome_key = identity("alignment", subject["id"], subject["class_iri"], predicate_iris)
        row.setdefault("alignment_outcomes", {})[outcome_key] = {
            "subject_id": subject["id"],
            "class_iri": subject["class_iri"],
            "predicate_iris": predicate_iris,
            "reason": reason,
            "state": state,
        }
        changes["observations"][key] = row

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

    def discover(self, window):
        self._call_window = window
        if self.state.get("windows", {}).get(window.id, {}).get("lookup_work"):
            answer, lookup_info = run_discovery(self, window)
            self.register_discovery(window, answer, lookup_info)
            return
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
        payload["known_mentions"] = [
            {"label": e["label"], "role": e["role"], "name": q}
            for e in self.state.get("entities", {}).values()
            if e.get("referent") and (q := quote_for_reference(window, e["referent"]))
        ][:12]
        schema = stage_schema(
            "discover",
            source_ids=[s["source_id"] for s in window.sources],
            field_ids=[f["alias"] for f in window.fields],
        )
        payload["schema_guidance"] = {"rule": GUIDANCE_RULE, "classes": []}
        if (
            self.max_request_bytes is not None
            and request_size("discover", payload, schema) > self.max_request_bytes
        ):
            key, item = self.observation(
                window,
                "阅读输入超出预算",
                "调用前拆分阅读范围；本范围尚未完成发现或核对",
                kind="scope",
            )
            self.advance(
                "discover",
                changes={"observations": {key: item}},
                complete=False,
                reviewed=False,
            )
            return
        if self.should_stop():
            raise Paused()
        ranked = self.rank(
            self.catalog,
            window.payload(),
            max(0, self.max_request_bytes - request_size("discover", payload, schema))
            if self.max_request_bytes is not None
            else None,
        )
        guidance = ranked["selected_iris"]
        if (
            ranked["snapshot_id"] != self.catalog.snapshot_id
            or len(guidance) != len(set(guidance))
            or not set(guidance) <= set(self.catalog.reachable_class_iris)
        ):
            raise ValueError("harness_ranking_catalog_mismatch")
        payload["schema_guidance"]["classes"] = [
            reading_card(
                self.catalog.classes[iri], annotation_contracts=self.catalog.annotation_contracts
            )
            for iri in guidance
        ]
        info = self.state.get("windows", {}).get(window.id, {"complete": True})
        self.commit({"windows": {window.id: {**info, "guidance_class_iris": guidance}}})
        answer, lookup_info = None, {}
        if self.lookup:
            capability_result = self.lookup("capabilities", self.catalog, guidance)
            capabilities = capability_result["capabilities"]
            lookup_info = {"status": "unavailable", "issues": capability_result.get("issues", [])}
            if capabilities:
                answer, lookup_info = run_discovery(self, window, payload, capabilities)
        if answer is None:
            answer = self.call("discover", payload, schema)
        self.register_discovery(window, answer, lookup_info)

    def register_discovery(self, window, answer, lookup_info):
        changes = defaultdict(dict)
        fields = {f["alias"]: f for f in window.fields}
        local = {}
        discovery_valid = True
        for mention in answer.entities:
            evidence = []
            try:
                if mention.local_id in local:
                    raise ValueError("duplicate_local_entity_id")
                evidence = window.quotes(self.ir, mention.evidence)
                name = window.resolve(self.ir, mention.name) if mention.name else None
                anchor = window.resolve_anchor(self.ir, mention.anchor)
                if name and (
                    missing(name["text"])
                    or name["text"].strip().casefold()
                    in {
                        "是",
                        "否",
                        "yes",
                        "no",
                        "true",
                        "false",
                    }
                ):
                    raise ValueError("missing_or_boolean_value_is_not_entity_name")
                if name and name not in evidence:
                    evidence.insert(0, name)
                if anchor not in evidence:
                    evidence.insert(0, anchor)
                assigned = [fields[f] for f in mention.field_ids]
                # Reject a whole field masquerading as a named object. A record
                # hypothesis without a name remains possible, pending review.
                if (
                    name
                    and any(
                        name["source_id"] == ref["source_id"]
                        and name["start"] <= ref["start"]
                        and name["end"] >= ref["end"]
                        for f in assigned
                        for ref in f["evidence"][:1]
                    )
                    and any(
                        name["text"].strip() == s["text"].strip()
                        for s in window.sources
                        if s["evidence_id"] == name["source_id"]
                    )
                ):
                    raise ValueError("whole_field_is_not_entity_name")
                key = identity("mention", anchor, mention.role)
                local[mention.local_id] = key
                existing = self.state.get("entities", {}).get(key)
                # Only exact same physical referent and role reuses an ID.
                # A second mention with the same spelling never resolves identity.
                entity = existing or {
                    "id": key,
                    "label": name["text"] if name else anchor["text"],
                    "name": name,
                    "role": mention.role,
                    "class_iri": None,
                    "class_label": None,
                    "state": "candidate",
                    "reason": "原文提及已保存，类型和归属待对齐",
                    "evidence": evidence,
                    "window_id": window.id,
                    "field_ids": [],
                    "referent": anchor,
                }
                changes["entities"][key] = {
                    **entity,
                    "field_ids": sorted(set(entity["field_ids"] + [f["id"] for f in assigned])),
                }
                for field in assigned:
                    changes["fields"][field["id"]] = field
            except (ValueError, KeyError) as exc:
                discovery_valid = False
                key, item = self.observation(
                    window,
                    mention.role,
                    str(exc),
                    evidence,
                    kind="entity",
                )
                changes["observations"][key] = item
        for field in window.fields:
            changes["fields"][field["id"]] = field
            key, item = self.field_observation(
                window,
                field,
                "missing_source_value"
                if field["missing"]
                else "原字段观察；采信状态见对应属性候选",
            )
            changes["observations"][key] = item
        root = deepcopy(self.state["entities"]["document"])
        for alias in answer.document_field_ids:
            if alias not in fields:
                raise ValueError("document_field_outside_reading_window")
            if fields[alias]["id"] not in root["field_ids"]:
                root["field_ids"].append(fields[alias]["id"])
        changes["entities"]["document"] = root
        proposed_fields = (
            [(root, proposed, False) for proposed in answer.document_source_fields]
            + [
                (changes["entities"].get(local.get(mention.local_id)), proposed, True)
                for mention in answer.entities
                for proposed in mention.source_fields
            ]
            + [
                (None, proposed, False)
                for proposed in [
                    *answer.unowned_fields,
                    *lookup_info.get("retained_fields", []),
                ]
            ]
        )
        for owner, proposed, requires_owner in proposed_fields:
            try:
                label = window.resolve(self.ir, proposed.label) if proposed.label else None
                value = window.resolve(self.ir, proposed.value)
                evidence = [label, value] if label else [value]
                key = identity("field", evidence)
                field = {
                    "id": key,
                    "alias": "",
                    "label": label["text"] if label else "",
                    "value": value["text"],
                    "missing": missing(value["text"]),
                    "evidence": evidence,
                    "value_evidence": [value],
                    "source_aliases": [],
                    "row": None,
                }
                changes["fields"][key] = field
                reason = "原文补充字段，归属和谓词待对齐"
                if owner is not None:
                    if key not in owner["field_ids"]:
                        owner["field_ids"].append(key)
                elif requires_owner:
                    reason = "候选主体引用无效；保留原字段，归属待定"
                obs_id, item = self.field_observation(
                    window,
                    field,
                    "missing_source_value" if field["missing"] else reason,
                )
                changes["observations"][obs_id] = item
            except (ValueError, KeyError) as exc:
                key, item = self.observation(
                    window,
                    proposed.label.text if proposed.label else "独立原文",
                    str(exc),
                    kind="field",
                )
                changes["observations"][key] = item
        for hint in answer.relation_hints:
            try:
                subject = "document" if hint.subject_id == "document" else local[hint.subject_id]
                obj = local[hint.object_id]
                evidence = window.quotes(self.ir, hint.evidence)
                if subject == obj:
                    raise ValueError("self_relation_hint_requires_explicit_review")
                key = identity("hint", subject, hint.label, obj, evidence)
                changes["hints"][key] = {
                    "id": key,
                    "subject_id": subject,
                    "object_id": obj,
                    "label": hint.label,
                    "evidence": evidence,
                    "polarity": hint.polarity,
                    "conditions": hint.conditions,
                    "window_id": window.id,
                }
                obs_id, item = self.observation(
                    window,
                    hint.label,
                    "原文关系线索，尚未映射合法谓词",
                    evidence,
                    kind="relation",
                    subject_id=subject,
                    object_id=obj,
                )
                changes["observations"][obs_id] = item
            except (KeyError, ValueError) as exc:
                key, item = self.observation(window, hint.label, str(exc), kind="relation")
                changes["observations"][key] = item
        for cue in answer.reference_cues:
            try:
                subject = (
                    "document"
                    if cue.local_subject_id == "document"
                    else local[cue.local_subject_id]
                )
                ref = window.resolve(self.ir, cue.reference)
                refs = window.quotes(self.ir, cue.evidence)
                key = identity("reference_cue", subject, ref, cue.direction, cue.relation_label)
                changes["reference_cues"][key] = {
                    "id": key,
                    "subject_id": subject,
                    "reference": ref,
                    "relation_label": cue.relation_label,
                    "direction": cue.direction,
                    "kind": cue.kind,
                    "evidence": refs,
                    "polarity": cue.polarity,
                    "conditions": cue.conditions,
                }
            except (KeyError, ValueError):
                discovery_valid = False
        changes["window_entities"][window.id] = {"ids": list(dict.fromkeys(local.values()))}
        if lookup_info:
            current = dict(self.state["windows"][window.id])
            current.pop("lookup_work", None)
            current["lookup"] = {
                k: v
                for k, v in lookup_info.items()
                if k not in {"retained_fields", "candidates", "suggestions"}
            }
            changes["windows"][window.id] = current
            for suggestion in lookup_info.get("suggestions", []):
                entity_id = local.get(suggestion["local_id"])
                if entity_id not in changes["entities"]:
                    continue
                candidates = [lookup_info["candidates"][key] for key in suggestion["candidate_ids"]]
                changes["source_candidates"][entity_id] = {
                    "entity_id": entity_id,
                    "candidates": candidates,
                    "identity_status": "not_checked",
                    "reason": suggestion["reason"],
                }
                key, item = self.observation(
                    window,
                    "来源候选（身份未核对）",
                    "、".join(c["label"] for c in candidates) + "；" + suggestion["reason"],
                    changes["entities"][entity_id]["evidence"],
                    kind="entity",
                    candidate_subject_ids=[entity_id],
                )
                changes["observations"][key] = item
            for issue in lookup_info.get("issues", []):
                key, item = self.observation(window, "来源查询未完成", issue)
                changes["observations"][key] = item
        capacity = (
            len(answer.entities) == 12
            or len(answer.relation_hints) == 16
            or len(answer.reference_cues) == 8
            or len(proposed_fields) >= 16
            or len(answer.document_source_fields) == 8
            or any(len(mention.source_fields) == 8 for mention in answer.entities)
        )
        self.advance(
            "type_alignment",
            changes=dict(changes),
            complete=answer.complete and not capacity and discovery_valid,
        )

    def entities(self, window):
        ids = (
            window.entity_ids
            if window.entity_ids is not None
            else self.state.get("window_entities", {}).get(window.id, {}).get("ids", [])
        )
        return [self.state["entities"][key] for key in ids]

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
            self._call_window = window
            task_key = digest({"payload": payload, "schema": schema})
            previous = self.state.get("type_work", {}).get(task_key)
            if previous:
                answer = STAGES["type_alignment"].model_validate(previous["answer"])
            else:
                self._call_targets = [
                    {"domain": "entities", "id": entity["id"],
                     "dependency_hash": input_hash(entity)}
                    for entity in selected_entities
                ]
                answer = self.call("type_alignment", payload, schema)
                self.commit({"type_work": {task_key: {"answer": answer.model_dump(mode="json")}}})
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
            self._call_window = window
            self._call_aliases = {alias: key for key, alias in aliases.items()}
            self._call_targets = [
                {"domain": "entities", "id": entity["id"], "dependency_hash": digest(rows[i])}
                for i, entity in enumerate(items)
            ]
            answer = self.call("entity_review", payload, schema)
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
            self.commit({"entities": changes})

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
