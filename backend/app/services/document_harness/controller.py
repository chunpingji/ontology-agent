"""Source-first discovery and alignment. State and model calls are injected ports."""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy

from pydantic import ValidationError

from .continuation import restore_window_plan, serialize_window_plan, split_reading_window
from .coreference import review_coreferences
from .lookup import run_discovery
from .model import request_size
from .observations import property_value, value_components
from .ontology import SchemaCatalog, identity_guidance, legal_property, legal_relation, model_menu
from .planning import alignment_jobs, context_window
from .protocols import (
    STAGES,
    AssertionAlignment,
    RelationGroupChoice,
    discovery_model,
    stage_schema,
)
from .ranking import GUIDANCE_RULE, CardRanker, reading_card
from .referents import binding_input, run_referent_alignment, validate_bound_property
from .relation_groups import add_groups, requires_group, review_groups
from .source import build_windows, identity, missing, quote_for_reference, references_cover


class Paused(Exception):
    pass


def overlapping_referents(left, right):
    a, b = left.get("referent"), right.get("referent")
    return bool(a and b and a["source_id"] == b["source_id"]
                and a["start"] < b["end"] and b["start"] < a["end"])


class Engine:
    def __init__(
        self, *, ir, catalog, state, invoke, save, should_stop, max_input_tokens=None, rank=None,
        lookup=None,
    ):
        self.ir = ir
        self.catalog = SchemaCatalog.model_validate(catalog)
        self.state = deepcopy(state)
        self.invoke, self.save, self.should_stop = invoke, save, should_stop
        self.max_input_tokens = max_input_tokens
        self.rank = rank or CardRanker().rank
        self.lookup = lookup
        self.windows = build_windows(ir, max_chars=min(4800, max_input_tokens // 6)
                                     if max_input_tokens is not None else 4800)
        self.windows.extend(
            restore_window_plan(ir, row["plan"])
            for row in sorted(
                self.state.get("extra_windows", {}).values(),
                key=lambda row: row["position"],
            )
        )

    def commit(self, changes):
        self.save(changes)
        for domain, rows in changes.items():
            for key, value in rows.items():
                if value is None:
                    self.state.setdefault(domain, {}).pop(key, None)
                else:
                    self.state.setdefault(domain, {})[key] = deepcopy(value)

    def raw_call(self, stage, payload, schema):
        if self.should_stop():
            raise Paused()
        if (self.max_input_tokens is not None
                and request_size(stage, payload, schema) > self.max_input_tokens):
            raise ValueError("harness_input_budget_exceeded")
        return self.invoke(stage, payload, schema)

    def call(self, stage, payload, schema):
        response_model = (discovery_model(payload.get("lookup_mode"))
                          if stage == "discover" else STAGES[stage])
        return response_model.model_validate(self.raw_call(stage, payload, schema))

    @staticmethod
    def timing_conflicts(error):
        indices = []
        for item in error.errors(include_input=False, include_context=False):
            location = item["loc"]
            if (len(location) != 2 or location[0] != "relation_groups"
                    or type(location[1]) is not int
                    or "timing_without_joint_participation" not in item["msg"]):
                raise error
            indices.append(location[1])
        if not indices:
            raise error
        return set(indices)

    def alignment_answer(self, payload, schema):
        """Correct contradictory group timing once; keep any remaining group unresolved."""
        raw = self.raw_call("assertion_alignment", payload, schema)
        try:
            return AssertionAlignment.model_validate(raw), []
        except ValidationError as error:
            invalid = self.timing_conflicts(error)
        original = [raw["relation_groups"][index] for index in sorted(invalid)]
        feedback = {
            **payload,
            "proposal_feedback": {
                "previous_answer": raw,
                "issues": [{"path": f"relation_groups[{index}]",
                            "code": "timing_without_joint_participation"}
                           for index in sorted(invalid)],
            },
        }
        if (self.max_input_tokens is None
                or request_size("assertion_alignment", feedback, schema)
                <= self.max_input_tokens):
            raw = self.raw_call("assertion_alignment", feedback, schema)
            try:
                answer = AssertionAlignment.model_validate(raw)
                invalid = set()
            except ValidationError as error:
                invalid = self.timing_conflicts(error)
                answer = AssertionAlignment.model_validate({**raw, "relation_groups": [
                    group for index, group in enumerate(raw["relation_groups"])
                    if index not in invalid
                ]})
        else:
            answer = AssertionAlignment.model_validate({**raw, "relation_groups": [
                group for index, group in enumerate(raw["relation_groups"])
                if index not in invalid
            ]})
        unresolved = [raw["relation_groups"][index] for index in sorted(invalid)]
        remaining = list(answer.relation_groups)
        for group in original:
            matching = [item for item in remaining
                        if item.predicate_iri == group["predicate_iri"]
                        and set(item.object_ids) == set(group["object_ids"])]
            if matching and all(item.participation == group["participation"]
                                for item in matching):
                continue
            # A correction may retract the group; it cannot promote unknown to all
            # merely to keep a sequence. Preserve the original source as unresolved.
            remaining = [item for item in remaining if item not in matching]
            unresolved.append(group)
        unique = {}
        for group in unresolved:
            key = (group["predicate_iri"], tuple(sorted(group["object_ids"])),
                   group["participation"], group["selection"], group["polarity"],
                   tuple(group["conditions"]))
            unique[key] = RelationGroupChoice.model_validate({**group, "timing": "unspecified"})
        return answer.model_copy(update={"relation_groups": remaining}), list(unique.values())

    def run(self):
        if not self.state.get("cursor"):
            root = self.catalog.classes[self.catalog.root_class_iri]
            self.commit(
                {
                    "cursor": {
                        "main": {
                            "window_index": 0,
                            "stage": "discover",
                            "windows_total": len(self.windows),
                            "windows_discovered": 0,
                            "windows_reviewed": 0,
                            "scope_complete": False,
                        }
                    },
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
        try:
            while not self.should_stop():
                cursor = self.state["cursor"]["main"]
                if cursor["window_index"] >= len(self.windows):
                    if cursor["stage"] == "complete":
                        return
                    if cursor["stage"] != "coreference_review":
                        self.commit({"cursor": {"main": {
                            **cursor, "stage": "coreference_review",
                        }}})
                    review_coreferences(self)
                    cursor = self.state["cursor"]["main"]
                    complete = all(
                        row.get("complete") or row.get("split_children")
                        for row in self.state.get("windows", {}).values()
                    )
                    self.commit(
                        {
                            "cursor": {
                                "main": {**cursor, "stage": "complete", "scope_complete": complete}
                            }
                        }
                    )
                    return
                window = self.windows[cursor["window_index"]]
                getattr(self, cursor["stage"])(window)
        except Paused:
            return

    def advance(self, stage, *, changes=None, complete=None, reviewed=True):
        changes = changes or {}
        cursor = dict(self.state["cursor"]["main"])
        window = self.windows[cursor["window_index"]]
        info = dict(changes.get("windows", {}).get(
            window.id, self.state.get("windows", {}).get(window.id, {"complete": True}),
        ))
        if complete is not None:
            info["complete"] = info["complete"] and complete
        if stage == "type_alignment":
            cursor["windows_discovered"] += 1
        if stage == "discover":
            if not info["complete"] and not info.get("split_children"):
                depth = self.state.get("extra_windows", {}).get(window.id, {}).get("depth", 0)
                children = split_reading_window(self.ir, window) if depth < 4 else []
                info["split_children"] = [child.id for child in children]
                for child in children:
                    changes.setdefault("extra_windows", {})[child.id] = {
                        "position": len(self.windows),
                        "depth": depth + 1,
                        "plan": serialize_window_plan(child),
                    }
                    self.windows.append(child)
                if not children:
                    key, item = self.observation(
                        window, "本范围未读完", "已保留候选；到达有界拆分下限，未宣称全文穷尽",
                        kind="scope",
                    )
                    changes.setdefault("observations", {})[key] = item
            cursor["window_index"] += 1
            cursor["windows_reviewed"] += int(reviewed)
        cursor["windows_total"] = len(self.windows)
        cursor["stage"] = stage
        changes.setdefault("windows", {})[window.id] = info
        changes["cursor"] = {"main": cursor}
        self.commit(changes)

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
            "id": key, "kind": "field", "field_id": field["id"],
            "label": field["label"] or "独立原文",
            "reason": reason, "evidence": field["evidence"], "window_id": window.id,
            "discovery_window_id": window.id,
        }

    def record_field_alignment(
        self, changes, window, field, subject, predicates, reason, state,
    ):
        """Keep current menu outcomes scoped to their subject and actual type card."""
        key, initial = self.field_observation(
            window, field, "原字段观察；采信状态见对应属性候选",
        )
        # An older field brought into this alignment window was not necessarily
        # discovered under this window's reading cards.
        initial["discovery_window_id"] = None
        row = deepcopy(changes["observations"].get(
            key, self.state.get("observations", {}).get(key, initial),
        ))
        predicate_iris = sorted(predicates)
        outcome_key = identity("alignment", subject["id"], subject["class_iri"], predicate_iris)
        row.setdefault("alignment_outcomes", {})[outcome_key] = {
            "subject_id": subject["id"], "class_iri": subject["class_iri"],
            "predicate_iris": predicate_iris, "reason": reason, "state": state,
        }
        changes["observations"][key] = row

    def pending_property_subjects(self):
        return [
            key for key, entity in self.state["entities"].items()
            if entity["state"] == "accepted" and entity.get("class_iri")
            and entity["role"] != "document_root"
            and any(
                entity.get("aligned_fields", {}).get(field_id) != entity["class_iri"]
                for field_id in entity["field_ids"]
            )
        ]

    def discover(self, window):
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
        if (self.max_input_tokens is not None
                and request_size("discover", payload, schema) > self.max_input_tokens):
            key, item = self.observation(
                window, "阅读输入超出预算", "调用前拆分阅读范围；本范围尚未完成发现或核对",
                kind="scope",
            )
            self.advance(
                "discover", changes={"observations": {key: item}},
                complete=False, reviewed=False,
            )
            return
        if self.should_stop():
            raise Paused()
        ranked = self.rank(
            self.catalog, window.payload(),
            max(0, self.max_input_tokens - request_size("discover", payload, schema))
            if self.max_input_tokens is not None else None,
        )
        guidance = ranked["selected_iris"]
        if (ranked["snapshot_id"] != self.catalog.snapshot_id
                or len(guidance) != len(set(guidance))
                or not set(guidance) <= set(self.catalog.reachable_class_iris)):
            raise ValueError("harness_ranking_catalog_mismatch")
        payload["schema_guidance"]["classes"] = [
            reading_card(self.catalog.classes[iri],
                         annotation_contracts=self.catalog.annotation_contracts)
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
                    window, mention.role, str(exc), evidence, kind="entity",
                )
                changes["observations"][key] = item
        for field in window.fields:
            changes["fields"][field["id"]] = field
            key, item = self.field_observation(
                window, field,
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
        proposed_fields = [
            (root, proposed, False) for proposed in answer.document_source_fields
        ] + [
            (changes["entities"].get(local.get(mention.local_id)), proposed, True)
            for mention in answer.entities for proposed in mention.source_fields
        ] + [(None, proposed, False) for proposed in [
            *answer.unowned_fields, *lookup_info.get("retained_fields", []),
        ]]
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
                    window, field,
                    "missing_source_value"
                    if field["missing"]
                    else reason,
                )
                changes["observations"][obs_id] = item
            except (ValueError, KeyError) as exc:
                key, item = self.observation(
                    window, proposed.label.text if proposed.label else "独立原文", str(exc),
                    kind="field",
                )
                changes["observations"][key] = item
        for hint in answer.relation_hints:
            try:
                subject, obj = local[hint.subject_id], local[hint.object_id]
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
                    window, hint.label, "原文关系线索，尚未映射合法谓词", evidence,
                    kind="relation", subject_id=subject, object_id=obj,
                )
                changes["observations"][obs_id] = item
            except (KeyError, ValueError) as exc:
                key, item = self.observation(window, hint.label, str(exc), kind="relation")
                changes["observations"][key] = item
        changes["window_entities"][window.id] = {"ids": list(dict.fromkeys(local.values()))}
        if lookup_info:
            current = dict(self.state["windows"][window.id])
            current.pop("lookup_work", None)
            current["lookup"] = {k: v for k, v in lookup_info.items()
                                 if k not in {"retained_fields", "candidates", "suggestions"}}
            changes["windows"][window.id] = current
            for suggestion in lookup_info.get("suggestions", []):
                entity_id = local.get(suggestion["local_id"])
                if entity_id not in changes["entities"]:
                    continue
                candidates = [lookup_info["candidates"][key]
                              for key in suggestion["candidate_ids"]]
                changes["source_candidates"][entity_id] = {
                    "entity_id": entity_id, "candidates": candidates,
                    "identity_status": "not_checked", "reason": suggestion["reason"],
                }
                key, item = self.observation(
                    window, "来源候选（身份未核对）",
                    "、".join(c["label"] for c in candidates) + "；" + suggestion["reason"],
                    changes["entities"][entity_id]["evidence"], kind="entity",
                    candidate_subject_ids=[entity_id],
                )
                changes["observations"][key] = item
            for issue in lookup_info.get("issues", []):
                key, item = self.observation(window, "来源查询未完成", issue)
                changes["observations"][key] = item
        capacity = (
            len(answer.entities) == 12
            or len(answer.relation_hints) == 16
            or len(proposed_fields) >= 16
            or len(answer.document_source_fields) == 8
            or any(len(mention.source_fields) == 8 for mention in answer.entities)
        )
        self.advance(
            "type_alignment", changes=dict(changes),
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
        return bool(field and field["evidence"] and all(
            quote_for_reference(window, ref) is not None for ref in field["evidence"]
        ))

    def context_entities(self, window, entities, *, required_field_ids=()):
        """Document metadata follows this reading scope, not its accumulated history."""
        required = set(required_field_ids)
        return [
            {**entity, "field_ids": [
                key for key in entity["field_ids"]
                if key in required or self.field_visible(key, window)
            ]} if entity["role"] == "document_root" else entity
            for entity in entities
        ]

    def entity_input(self, entity, window, *, typed=False):
        value = {
            "entity_id": self.entity_aliases(window)[entity["id"]],
            "label": entity["label"],
            "role": entity["role"],
            "name": quote_for_reference(window, entity["name"]) if entity.get("name") else None,
            "anchor": quote_for_reference(window, entity["referent"])
            if entity.get("referent") else None,
            "type_confirmed": entity["state"] == "accepted",
            "type_basis": (
                "user_selected" if entity["role"] == "document_root" else
                "model_review" if entity["state"] == "accepted" else "unconfirmed"
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
                        f, confirmed=entity["state"] == "accepted",
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
        entities = [e for e in self.entities(window) if e["state"] != "accepted"]
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
                self.ir, base_window,
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
            if (self.max_input_tokens is not None
                    and request_size("type_alignment", payload, schema) > self.max_input_tokens):
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
            answer = self.call("type_alignment", payload, schema)
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
                        if choice.class_iri and not evidence else None
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
                    iri for other in entities if other["id"] != entity["id"]
                    and overlapping_referents(entity, other)
                    for iri in proposals[other["id"]]
                }
            if len(proposed_iris) > 1 or proposed_iris and not proposals[entity["id"]]:
                # Shard confidence is not a calibrated ranking. Re-read the
                # referent against its entire candidate menu without treating
                # earlier proposal scores or rationales as source evidence.
                batch(
                    [card for card in classes if card["iri"] in proposed_iris],
                    [entity], compare=True,
                )
        changes = {"entities": {}}
        for entity in entities:
            options = choices[entity["id"]]
            selected = next((item for item in options
                             if item[0].class_iri and item[0].confidence >= 0.7
                             and item[1] and not item[2]), None)
            entity = dict(entity)
            if selected is None:
                entity.update(
                    state="unresolved",
                    reason="；".join(item[2] or item[0].reason for item in options[:2]),
                    class_iri=None, class_label=None, type_evidence=[],
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
        self.evidence_review(window, entities_only=True)

    def assertion_alignment(self, window):
        base_window = window
        current_ids = list(dict.fromkeys([
            *(e["id"] for e in self.entities(window)), *self.pending_property_subjects(),
        ]))
        if any(self.field_visible(key, base_window)
               for key in self.state["entities"]["document"]["field_ids"]):
            current_ids.append("document")
        changes = defaultdict(dict)
        complete = True
        property_subjects = set()
        def process_job(
            subject_id, object_ids, include_properties, *, selected_field_ids=None,
            include_relations=True,
        ):
            subject = self.state["entities"][subject_id]
            objects = [self.state["entities"][key] for key in object_ids]
            window = context_window(
                self.ir, base_window,
                self.context_entities(
                    base_window, [subject, *objects],
                    required_field_ids=[
                        key for key in subject["field_ids"]
                        if subject["state"] == "accepted"
                        and subject["role"] != "document_root"
                        and subject.get("aligned_fields", {}).get(key) != subject["class_iri"]
                    ],
                ),
                self.state.get("fields", {}),
            )
            aliases = self.entity_aliases(window)
            card_menu = model_menu(self.catalog, [subject["class_iri"]])
            card = card_menu["classes"][0]
            fields = [
                self.state["fields"][key]
                for key in subject["field_ids"]
                if self.field_alias(key, window)
                and include_properties
                and (selected_field_ids is None or key in selected_field_ids)
                and (subject_id in current_ids or subject_id == "document")
                and (self.field_visible(key, base_window)
                     or subject["state"] == "accepted"
                     and subject.get("aligned_fields", {}).get(key) != subject["class_iri"])
            ]
            properties = card["properties"] if fields else []
            eligible_fields = [field for field in fields if not field["missing"]]
            if not properties:
                for field in eligible_fields:
                    self.record_field_alignment(
                        changes, window, field, subject, [],
                        "当前类型卡无合法属性可对齐", "unmatched",
                    )
            if properties and len(eligible_fields) > 32:
                for start in range(0, len(eligible_fields), 32):
                    process_job(
                        subject_id, object_ids if start == 0 and include_relations else [], True,
                        selected_field_ids={
                            field["id"] for field in eligible_fields[start:start + 32]
                        },
                        include_relations=include_relations and start == 0,
                    )
                return
            relations = [
                r
                for r in card["relations"]
                if any(
                    legal_relation(
                        self.catalog,
                        subject["class_iri"],
                        r["iri"],
                        e["class_iri"],
                    )
                    for e in objects
                )
            ] if include_relations else []
            entries = [("properties", p) for p in properties] + [
                ("relations", r) for r in relations
            ]

            def batch(items):
                nonlocal complete
                props = [p for kind, p in items if kind == "properties"]
                rels = [r for kind, r in items if kind == "relations"]
                payload = {
                    "sources": window.payload()["sources"],
                    "subject": self.entity_input(subject, window, typed=True),
                    "property_field_ids": [
                        self.field_alias(f["id"], window)
                        for f in fields if props and not f["missing"]
                    ],
                    "objects": [self.entity_input(e, window, typed=True) for e in objects],
                    "card": {
                        **{k: v for k, v in card.items() if k not in {"properties", "relations"}},
                        "properties": props,
                        "relations": rels,
                    },
                    "annotation_contracts": card_menu["annotation_contracts"],
                    "relation_hints": [
                        {
                            "subject_id": aliases[h["subject_id"]],
                            "object_id": aliases[h["object_id"]],
                            "label": h["label"],
                        }
                        for h in self.state.get("hints", {}).values()
                        if h["window_id"] == window.id
                        and h["subject_id"] in aliases
                        and h["object_id"] in aliases
                    ],
                }
                schema = stage_schema(
                    "assertion_alignment",
                    source_ids=[s["source_id"] for s in window.sources],
                    entity_ids=[aliases[e["id"]] for e in objects],
                    field_ids=[
                        self.field_alias(f["id"], window)
                        for f in fields if props and not f["missing"]
                    ],
                    property_iris=[p["iri"] for p in props],
                    relation_iris=[r["iri"] for r in rels],
                )
                if (self.max_input_tokens is not None
                        and request_size("assertion_alignment", payload, schema)
                        > self.max_input_tokens):
                    if len(objects) >= 2:
                        middle = len(objects) // 2
                        left_groups = {(e.get("identity_binding") or {}).get("group_id")
                                       for e in objects[:middle]} - {None}
                        right_groups = {(e.get("identity_binding") or {}).get("group_id")
                                        for e in objects[middle:]} - {None}
                        if left_groups & right_groups:
                            if len(items) < 2:
                                raise ValueError("harness_relation_group_input_too_large")
                            cut = len(items) // 2
                            batch(items[:cut])
                            batch(items[cut:])
                            return
                        process_job(
                            subject_id, object_ids[:middle], include_properties,
                            selected_field_ids=selected_field_ids,
                            include_relations=include_relations,
                        )
                        process_job(
                            subject_id, object_ids[middle:], False,
                            include_relations=include_relations,
                        )
                        return
                    if len(items) < 2:
                        raise ValueError("harness_single_assertion_input_too_large")
                    middle = len(items) // 2
                    batch(items[:middle])
                    batch(items[middle:])
                    return
                answer, unresolved_groups = self.alignment_answer(payload, schema)
                if set(answer.properties) != set(payload["property_field_ids"]):
                    raise ValueError("property_alignment_field_set_mismatch")
                complete &= (
                    answer.complete and len(answer.relations) < 16
                    and len(answer.relation_groups) + len(unresolved_groups) < 8
                )
                for field_id, proposed in answer.properties.items():
                    field = next(
                        (
                            f
                            for f in fields
                            if self.field_alias(f["id"], window) == field_id
                        ),
                        None,
                    )
                    if field is None:
                        raise ValueError("property_field_outside_subject")
                    if not proposed.mappings:
                        self.record_field_alignment(
                            changes, window, field, subject, [p["iri"] for p in props],
                            proposed.reason, "unmatched",
                        )
                        continue
                    conflicting = {
                        mapping.predicate_iri for mapping in proposed.mappings
                        if len({other.value_component for other in proposed.mappings
                                if other.predicate_iri == mapping.predicate_iri}) > 1
                    }
                    for mapping in proposed.mappings:
                        if mapping.predicate_iri in conflicting:
                            self.record_field_alignment(
                                changes, window, field, subject, [mapping.predicate_iri],
                                "同一属性不能同时承载范围的不同组件；保留完整原值，待按定义对齐",
                                "invalid",
                            )
                            continue
                        prop = next((p for p in props if p["iri"] == mapping.predicate_iri), None)
                        valid = (not field["missing"] and prop and legal_property(
                            self.catalog, subject["class_iri"], mapping.predicate_iri,
                        ))
                        try:
                            if not valid:
                                raise ValueError(
                                    "property_outside_current_card_or_missing_value: "
                                    + mapping.predicate_iri
                                )
                            component = property_value(
                                field, mapping, window=window, ir=self.ir,
                                confirmed=subject["state"] == "accepted",
                            )
                            validate_bound_property(subject, field, mapping, component)
                        except ValueError as exc:
                            self.record_field_alignment(
                                changes, window, field, subject, [mapping.predicate_iri],
                                str(exc), "invalid",
                            )
                            continue
                        self.record_field_alignment(
                            changes, window, field, subject, [mapping.predicate_iri],
                            proposed.reason, "mapped",
                        )
                        key = identity(
                            "property", subject["id"], mapping.predicate_iri, field["id"],
                            mapping.value_component,
                            component["evidence"],
                        )
                        if self.state.get("properties", {}).get(key, {}).get("state") == "accepted":
                            continue
                        changes["properties"][key] = {
                            "id": key, "subject_id": subject["id"],
                            "alignment_class_iri": subject["class_iri"],
                            "predicate_iri": mapping.predicate_iri,
                            "label": prop["label"], "value": component["value"],
                            "source_value": field["value"], "source_unit": component["unit"],
                            "value_component": mapping.value_component,
                            "value_evidence": component["evidence"],
                            "field_id": field["id"], "state": "candidate",
                            "reason": proposed.reason, "evidence": field["evidence"],
                            "confidence": mapping.confidence, "window_id": window.id,
                        }
                covered = add_groups(
                    self, window, subject, objects, aliases, rels, answer.relation_groups, changes,
                )
                if unresolved_groups:
                    fallback = defaultdict(dict)
                    covered.update(add_groups(
                        self, window, subject, objects, aliases, rels, unresolved_groups, fallback,
                    ))
                    changes["observations"].update(fallback["observations"])
                    for key, row in fallback["relation_groups"].items():
                        if (self.state.get("relation_groups", {}).get(key, {}).get("state")
                                == "accepted"):
                            continue
                        changes["relation_groups"][key] = {
                            **row, "state": "unresolved", "timing_state": "unresolved",
                            "reason": "参与方式与时间判断冲突，未采信模型关系组；" + row["reason"],
                            "timing_reason": "原时间判断与成员参与方式冲突，保留原文依据待核对",
                        }
                for proposed in answer.relations:
                    obj = next((e for e in objects if aliases[e["id"]] == proposed.object_id), None)
                    relation = next((r for r in rels if r["iri"] == proposed.predicate_iri), None)
                    try:
                        if (
                            obj is None
                            or relation is None
                            or not legal_relation(
                                self.catalog,
                                subject["class_iri"],
                                proposed.predicate_iri,
                                obj["class_iri"],
                            )
                        ):
                            raise ValueError("relation_outside_current_card")
                        if ((proposed.predicate_iri, obj["id"]) in covered
                                or requires_group(obj, self.state["entities"].values())):
                            raise ValueError("grouped_referents_require_relation_group")
                        evidence = window.quotes(self.ir, proposed.evidence)
                        key = identity(
                            "relation",
                            subject["id"],
                            proposed.predicate_iri,
                            obj["id"],
                            proposed.polarity,
                            proposed.conditions,
                        )
                        if self.state.get("relations", {}).get(key, {}).get("state") == "accepted":
                            continue
                        changes["relations"][key] = {
                            "id": key,
                            "alignment_class_iri": subject["class_iri"],
                            "subject_id": subject["id"],
                            "object_id": obj["id"],
                            "predicate_iri": proposed.predicate_iri,
                            "label": relation["label"],
                            "state": "candidate",
                            "reason": proposed.reason,
                            "evidence": evidence,
                            "polarity": proposed.polarity,
                            "conditions": proposed.conditions,
                            "confidence": proposed.confidence,
                            "window_id": window.id,
                        }
                    except ValueError as exc:
                        key, item = self.observation(
                            window, proposed.predicate_iri, str(exc), kind="relation",
                            subject_id=subject["id"], object_id=obj["id"] if obj else None,
                            discovery_window_id=None,
                        )
                        changes["observations"][key] = item

            if entries and (fields or relations):
                batch(entries)
            if fields and subject["state"] == "accepted":
                saved = changes["entities"].get(subject_id, subject)
                changes["entities"][subject_id] = {
                    **saved, "aligned_fields": {
                        **saved.get("aligned_fields", {}),
                        **{field["id"]: subject["class_iri"] for field in fields},
                    },
                }
        for subject_id, object_ids in alignment_jobs(
            self.catalog, self.state["entities"], current_ids,
        ):
            process_job(subject_id, object_ids, subject_id not in property_subjects)
            property_subjects.add(subject_id)
        self.advance("evidence_review", changes=dict(changes), complete=complete)

    def evidence_review(self, window, *, entities_only=False):
        base_window = window
        entity_ids = {e["id"] for e in self.entities(window)}
        domains = ("entities",) if entities_only else ("entities", "properties", "relations")
        candidates = [
            (domain, row)
            for domain in domains
            for row in self.state.get(domain, {}).values()
            if row["state"] == "candidate"
            and (row["id"] in entity_ids if domain == "entities" else row["window_id"] == window.id)
        ]
        queued_entities = {row["id"] for domain, row in candidates if domain == "entities"}
        for domain, row in list(candidates):
            if domain == "relations":
                for key in (row["subject_id"], row["object_id"]):
                    entity = self.state["entities"][key]
                    if (entity["state"] == "unresolved" and key not in queued_entities
                            and not entity.get("referent_unresolved")):
                        candidates.append(("entities", entity))
                        queued_entities.add(key)
        changes = defaultdict(dict)
        judgments = {}

        def batch(items):
            endpoint_ids = {
                key
                for domain, row in items
                for key in (
                    [row["id"]]
                    if domain == "entities"
                    else [row["subject_id"], *([row["object_id"]] if domain == "relations" else [])]
                )
            }
            window = context_window(
                self.ir,
                base_window,
                self.context_entities(
                    base_window,
                    [self.state["entities"][key] for key in endpoint_ids],
                    required_field_ids=[row["field_id"] for domain, row in items
                                        if domain == "properties"],
                ),
                self.state.get("fields", {}),
                extra_refs=[ref for _, row in items for ref in row["evidence"]],
            )
            rows = []
            aliases = {row["id"]: f"C{i + 1}" for i, (_, row) in enumerate(items)}
            entity_aliases = self.entity_aliases(window)
            for domain, candidate in items:
                row = {
                    k: v
                    for k, v in candidate.items()
                    if k
                    in {
                        "id",
                        "label",
                        "role",
                        "class_iri",
                        "subject_id",
                        "object_id",
                        "predicate_iri",
                        "value",
                        "source_value",
                        "source_unit",
                        "value_component",
                        "polarity",
                        "conditions",
                    }
                }
                row["kind"] = domain
                row["id"] = aliases[candidate["id"]]
                for key in ("subject_id", "object_id"):
                    if key in row:
                        row[key] = entity_aliases[row[key]]
                row["evidence"] = [
                    q["source_id"]
                    for ref in candidate["evidence"]
                    if (q := quote_for_reference(window, ref))
                ]
                subject = (
                    candidate
                    if domain == "entities"
                    else self.state["entities"][candidate["subject_id"]]
                )
                card = model_menu(self.catalog, [subject["class_iri"]])["classes"][0]
                if domain == "entities":
                    row["class_definition"] = {
                        k: v
                        for k, v in card.items()
                        if k in {"iri", "label", "description", "definition"}
                    }
                    row["class_definition"].update(identity_guidance(
                        self.catalog.classes[subject["class_iri"]],
                        annotation_contracts=self.catalog.annotation_contracts,
                    ))

                def endpoint_input(entity):
                    verdict = judgments.get(entity["id"], (entity["state"],))[0]
                    return self.entity_input({**entity, "state": verdict}, window, typed=True)

                row["subject"] = endpoint_input(subject)
                if domain == "properties":
                    row["value_quote"] = [
                        quote_for_reference(window, ref) for ref in candidate["value_evidence"]
                    ]
                if domain != "entities":
                    row["predicate_definition"] = next(
                        p
                        for p in card["properties" if domain == "properties" else "relations"]
                        if p["iri"] == candidate["predicate_iri"]
                    )
                if domain == "relations":
                    row["object"] = endpoint_input(self.state["entities"][candidate["object_id"]])
                rows.append(row)
            payload = {"sources": window.payload()["sources"], "candidates": rows}
            stage = "entity_review" if items[0][0] == "entities" else "evidence_review"
            endpoint_aliases = {entity_aliases[key]: key for key in endpoint_ids}
            schema = stage_schema(
                stage,
                source_ids=[s["source_id"] for s in window.sources],
                candidate_ids=list(aliases.values()),
                entity_ids=list(endpoint_aliases),
            )
            if (
                len(items) > 6
                or (self.max_input_tokens is not None
                    and request_size(stage, payload, schema) > self.max_input_tokens)
            ):
                if len(items) < 2:
                    raise ValueError("harness_single_review_input_too_large")
                middle = len(items) // 2
                batch(items[:middle])
                batch(items[middle:])
                return
            answer = self.call(stage, payload, schema)
            if set(answer.judgments) != set(aliases.values()):
                raise ValueError("review_candidate_set_mismatch")
            by_alias = {aliases[row["id"]]: (domain, row) for domain, row in items}
            for candidate_id, judgment in answer.judgments.items():
                domain, candidate = by_alias[candidate_id]
                try:
                    support = window.quotes(self.ir, judgment.evidence)
                    verdict, reason = judgment.verdict, judgment.reason
                    if verdict == "accepted" and (not support or judgment.confidence < 0.85):
                        verdict, reason = (
                            "unresolved",
                            "acceptance_requires_source_and_high_confidence",
                        )
                    if verdict == "accepted" and not self.review_sources_cover(
                        domain,
                        candidate,
                        support,
                    ):
                        verdict, reason = "unresolved", "review_source_does_not_locate_claim"
                except ValueError as exc:
                    support, verdict, reason = [], "unresolved", str(exc)
                judgments[candidate["id"]] = (verdict, reason, support)

            if stage == "evidence_review":
                for concern in answer.type_concerns:
                    if concern.entity_id not in endpoint_aliases:
                        raise ValueError("type_concern_endpoint_outside_batch")
                    refs = window.quotes(self.ir, concern.evidence)
                    entity_id = endpoint_aliases[concern.entity_id]
                    key, item = self.observation(
                        window, "主体类型疑点（交由类型处理）", concern.reason, refs,
                        kind="entity", discovery_window_id=None,
                    )
                    key = identity(key, entity_id)
                    changes["observations"][key] = {
                        **item, "id": key, "candidate_subject_ids": [entity_id],
                    }

        entity_candidates = [item for item in candidates if item[0] == "entities"]
        if entity_candidates:
            groups = []
            for item in entity_candidates:
                group = next((group for group in groups if all(
                    not overlapping_referents(item[1], existing[1]) for existing in group
                )), None)
                if group is None:
                    group = []
                    groups.append(group)
                group.append(item)
            for group in groups:
                batch(group)
        assertion_candidates = [item for item in candidates if item[0] != "entities"]
        if assertion_candidates:
            batch(assertion_candidates)
        for domain, candidate in candidates:
            verdict, reason, support = judgments[candidate["id"]]
            source_verdict, source_reason = verdict, reason
            if domain != "entities" and verdict == "accepted":
                endpoints = [candidate["subject_id"]]
                if domain == "relations":
                    endpoints.append(candidate["object_id"])
                if any(
                    judgments.get(key, (self.state["entities"][key]["state"],))[0] != "accepted"
                    for key in endpoints
                ):
                    verdict, reason = "unresolved", "端点指称或类型尚未确认；保留候选及本项核对证据"
            changes[domain][candidate["id"]] = {
                **candidate,
                "state": verdict,
                "reason": reason,
                "review_evidence": support,
                "source_verdict": source_verdict,
                "source_reason": source_reason,
            }
        # A newly confirmed endpoint may release an already source-checked
        # candidate. This is a current dependency update, not a proof replay.
        for domain in ("properties", "relations"):
            for key, existing in self.state.get(domain, {}).items():
                candidate = changes[domain].get(key, existing)
                if (
                    candidate.get("source_verdict") != "accepted"
                    or candidate["state"] != "unresolved"
                ):
                    continue
                endpoint_ids = [candidate["subject_id"]]
                if domain == "relations":
                    endpoint_ids.append(candidate["object_id"])
                endpoints = [
                    changes["entities"].get(eid, self.state["entities"][eid])
                    for eid in endpoint_ids
                ]
                if not all(e["state"] == "accepted" for e in endpoints):
                    continue
                legal = (
                    legal_property(
                        self.catalog, endpoints[0]["class_iri"], candidate["predicate_iri"]
                    )
                    if domain == "properties"
                    else legal_relation(
                        self.catalog,
                        endpoints[0]["class_iri"],
                        candidate["predicate_iri"],
                        endpoints[1]["class_iri"],
                    )
                )
                if legal:
                    changes[domain][key] = {
                        **candidate,
                        "state": "accepted",
                        "reason": candidate["source_reason"],
                    }
        if entities_only:
            self.advance("assertion_alignment", changes=dict(changes))
        else:
            self.commit(dict(changes))
            review_groups(self, base_window)
            self.advance("assertion_alignment" if self.pending_property_subjects() else "discover")

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
