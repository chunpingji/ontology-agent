"""Explicit work handlers for the source-first harness, with one paid batch at a time."""

from collections import defaultdict
from copy import deepcopy

from .evidence_gate import (
    _related_hints,
    assertion_dependency_hash,
    check_candidate_structure,
    route_semantic_review,
    semantic_acceptance,
    try_rule_seed,
)
from .observations import property_value
from .ontology import legal_property, model_menu
from .planning import (
    admit_relation_work,
    build_source_index,
    collect_relation_seeds,
    context_window,
    resolve_reference_cues,
)
from .protocols import stage_schema
from .source import Window, identity, quote_for_reference, reference, references_cover
from .work import dependency_hash, endpoints, make_work, unique_refs, work_id


def gate_state(engine):
    return {**engine.state, "execution_policy": engine.execution_policy}


def merged_state(state, changes):
    result = {**state}
    for domain, rows in changes.items():
        result[domain] = {**state.get(domain, {})}
        for key, value in rows.items():
            if value is None:
                result[domain].pop(key, None)
            else:
                result[domain][key] = value
    return result


def invalidate_changes(engine, changes):
    if not engine.state.get("work"):
        return
    after = merged_state(engine.state, changes)
    affected = engine.work_index.affected(changes)
    # Assertion edits invalidate the corresponding current review, never all work.
    for domain in ("properties", "relations", "relation_groups"):
        for key in changes.get(domain, {}):
            review_key = work_id("evidence_review", {"domain": domain, "assertion_id": key})
            if review_key in engine.state.get("work", {}):
                affected.add(review_key)
    for key in affected:
        if key in changes.get("work", {}):
            continue
        old = engine.state["work"][key]
        data = deepcopy(old["input"])
        for hint in changes.get("hints", {}).values():
            if not hint or old["kind"] != "relation_alignment":
                continue
            objects = hint.get("object_ids", [hint.get("object_id")])
            if hint.get("subject_id") == data["subject_id"] and set(objects) == set(
                data["object_ids"]
            ):
                data["required_context_refs"] = unique_refs(
                    [
                        *data.get("required_context_refs", []),
                        *hint.get("evidence", []),
                    ]
                )
        fingerprint = dependency_hash(
            old["kind"], data, after, engine.catalog, engine.execution_policy
        )
        if old["kind"].endswith("calibration"):
            if fingerprint != old["dependency_hash"]:
                changes.setdefault("work", {})[key] = make_work(
                    old["kind"], data, after, engine.catalog, engine.execution_policy,
                    phase="deterministic",
                )
                domain = data.get("domain", "entities")
                oid = data.get("entity_id", data.get("assertion_id"))
                row = after.get(domain, {}).get(oid)
                if row:
                    changes.setdefault(domain, {})[oid] = {**row, "calibration": None}
            continue
        if fingerprint != old["dependency_hash"] and old["phase"] != "skeleton":
            ready = make_work(old["kind"], data, after, engine.catalog,
                              engine.execution_policy, phase=old["phase"])
            missing = any(
                eid not in after.get("entities", {}) for eid in old["dependencies"]["entity_ids"]
            )
            if missing:
                ready.update(status="waiting", reason_code="missing_endpoint")
            changes.setdefault("work", {})[key] = ready
            if old["kind"] == "coreference_review":
                for oid in old.get("output_ids", []):
                    row = after.get("coreferences", {}).get(oid)
                    if row:
                        changes.setdefault("coreferences", {})[oid] = {
                            **row,
                            "verdict": "unresolved",
                            "reason": "端点语义依赖已变化，待重新核对",
                        }
            for domain in ("properties", "relations", "relation_groups"):
                for oid in old.get("output_ids", []):
                    row = after.get(domain, {}).get(oid)
                    if row:
                        changes.setdefault(domain, {})[oid] = {
                            **row,
                            "state": "unresolved",
                            "reason": "相关证据或端点解释已变化，待重新核对",
                            "verification": None,
                            "calibration": None,
                        }
                        if domain == "relation_groups":
                            changes[domain][oid]["timing_state"] = "unresolved"
                        review = make_work(
                            "evidence_review",
                            {"domain": domain, "assertion_id": oid},
                            after,
                            engine.catalog,
                            engine.execution_policy,
                        )
                        review.update(status="ready", reason_code=None)
                        changes.setdefault("work", {}).setdefault(review["id"], review)
        else:
            # Only endpoint eligibility changed; semantic proof remains reusable.
            if old["status"] == "waiting" and old["reason_code"] == "type_or_constraint_unresolved":
                ids = old["dependencies"]["entity_ids"]
                if old["kind"] in {"property_alignment", "coreference_review"} and all(
                    after.get("entities", {}).get(eid, {}).get("state") == "accepted" for eid in ids
                ):
                    changes.setdefault("work", {})[key] = {
                        **old,
                        "status": "ready",
                        "reason_code": None,
                    }
            for domain in ("properties", "relations", "relation_groups"):
                for oid in old.get("output_ids", []):
                    row = after.get(domain, {}).get(oid)
                    verification = (row or {}).get("verification") or {}
                    if not row or not verification.get("semantic_verdict"):
                        continue
                    verdict = semantic_acceptance(after, row, verification["semantic_verdict"])
                    updated = {**row, "state": verdict}
                    if domain == "relation_groups":
                        updated["timing_state"] = (
                            row.get("source_timing_verdict", "unresolved")
                            if verdict == "accepted" and row.get("timing") != "unspecified"
                            else "unresolved"
                        )
                    if updated != row:
                        changes.setdefault(domain, {})[oid] = updated


def enforce_group_consistency(engine, changes):
    """Competing current participation readings remain unresolved as a whole."""
    touched = changes.get("relation_groups", {})
    if not touched:
        return
    grouped = defaultdict(list)
    for key, row in {**engine.state.get("relation_groups", {}), **touched}.items():
        if row and (row.get("verification") or {}).get("semantic_verdict") == "accepted":
            group_key = identity(
                "participation",
                row["subject_id"],
                row["predicate_iri"],
                sorted(row["object_ids"]),
                row["polarity"],
                row["conditions"],
            )
            grouped[group_key].append((key, row))
    for rows in grouped.values():
        if len({row["participation"] for _, row in rows}) < 2:
            continue
        for key, row in rows:
            updated = {
                **row,
                "state": "unresolved",
                "timing_state": "unresolved",
                "reason": "竞争关系参与解释相互冲突，保留未决",
            }
            changes["relation_groups"][key] = updated


def upsert_work(state, rows):
    """Replanning identical semantic inputs preserves paid bindings and menu progress."""
    changes = {}
    for row in rows:
        old = state.get("work", {}).get(row["id"])
        if old and old["dependency_hash"] == row["dependency_hash"]:
            continue
        changes[row["id"]] = row
    return changes


def plan_skeleton_work(
    engine, *, commit=True, phase="skeleton", delta=None, kind=None, state=None,
    include_references=True,
):
    index = build_source_index(engine.ir, state) if state is not None else engine.source_index
    state, policy = state if state is not None else engine.state, engine.execution_policy
    delta = delta or {domain: state.get(domain, {}) for domain in ("entities", "fields", "hints")}
    seeds = [] if kind == "property_alignment" else collect_relation_seeds(
        engine.ir,
        engine.catalog,
        state,
        delta,
        index,
        policy,
        include_references=include_references,
    )
    ranking = (
        engine.rank_pairs
        if engine.policy.get("card_ranking", {})
        .get(
            "semantic",
            {},
        )
        .get("enabled")
        else None
    )
    rows = admit_relation_work(seeds, state, engine.catalog, policy, ranking)
    if phase != "skeleton":
        rows = [make_work(r["kind"], r["input"], state, engine.catalog, policy,
                          phase=phase, status=r["status"], reason=r["reason_code"]) for r in rows]
    for subject in (delta.get("entities", {}).values() if kind != "relation_alignment" else []):
        if not subject or subject.get("refined_member_ids") or not subject.get("class_iri"):
            continue
        for field_id in subject.get("field_ids", []):
            field = state.get("fields", {}).get(field_id)
            if not field:
                continue
            row = make_work(
                "property_alignment",
                {"subject_id": subject["id"], "field_id": field_id},
                state,
                engine.catalog,
                policy,
                phase=phase,
            )
            if field.get("missing"):
                row.update(status="done", reason_code="missing_source_value")
            rows.append(row)
    changes = {
        "work": upsert_work(state, rows),
        "observations": index.scope_changes,
        "reference_cues": index.cue_changes,
    }
    if commit:
        engine.commit(changes)
    return changes


def materialize_skeleton(engine, *, commit=True, state=None, delta=None):
    changes = defaultdict(dict)
    state = state or engine.state
    for subject in (delta or state)["entities"].values():
        for field_id in subject.get("field_ids", []):
            field = state.get("fields", {}).get(field_id)
            if not field or field.get("missing"):
                continue
            key = identity("property", subject["id"], "unmapped", field_id, "whole")
            if not any(row["subject_id"] == subject["id"] and row["field_id"] == field_id
                       for row in state.get("properties", {}).values()):
                changes["properties"][key] = {
                    "id": key, "subject_id": subject["id"], "field_id": field_id,
                    "predicate_iri": None, "alignment_class_iri": subject.get("class_iri"),
                    "label": field.get("label") or "原文字段", "value": field["value"],
                    "source_value": field["value"], "source_unit": None,
                    "source_unit_evidence": [], "value_component": "whole",
                    "value_evidence": field["value_evidence"], "evidence": field["evidence"],
                    "state": "candidate", "reason": "谓词待对齐", "calibration": None,
                    "verification": None,
                }
    for hint in (delta or state).get("hints", {}).values():
        objects = hint.get("object_ids", [hint.get("object_id")])
        subject = hint.get("subject_id")
        if (subject not in state["entities"] or not objects or not hint.get("evidence")
                or any(key not in state["entities"] for key in objects)):
            continue
        grouped = len(objects) > 1
        domain = "relation_groups" if grouped else "relations"
        key = identity("relation_draft", hint["id"], subject, objects, "unmapped")
        if key in state.get(domain, {}):
            continue
        row = {"id": key, "subject_id": subject, "predicate_iri": None,
               "origin_ids": [hint["id"]], "label": hint["label"],
               "alignment_class_iri": state["entities"][subject].get("class_iri"),
               "evidence": hint["evidence"], "state": "candidate", "reason": "谓词待对齐",
               "polarity": hint.get("polarity", "uncertain"),
               "conditions": hint.get("conditions", []), "calibration": None,
               "verification": None}
        if grouped:
            row.update(object_ids=objects, participation="unknown", selection="unspecified",
                       timing="unspecified", timing_state="candidate", timing_reason="时间待核对",
                       ordered_object_ids=None, order_evidence=[])
        else:
            row["object_id"] = objects[0]
        changes[domain][key] = row
    if commit:
        engine.commit(dict(changes))
    return dict(changes)


def plan_graph_work(engine):
    return plan_skeleton_work(engine)


def plan_semantic_work(engine, step, *, commit=True):
    state, rows = engine.state, []
    changes = defaultdict(dict)
    if step == "referents":
        from .referents import referent_groups

        for window in engine.windows:
            for group_id, _, entities in referent_groups(engine, window):
                rows.append(make_work("referent_alignment",
                                      {"group_id": group_id,
                                       "mention_ids": [e["id"] for e in entities]},
                                      state, engine.catalog, engine.execution_policy))
    elif step == "entities":
        for entity in state["entities"].values():
            if entity.get("refined_member_ids") or entity.get("role") == "document_root":
                continue
            row = make_work("entity_review", {"entity_id": entity["id"]}, state,
                            engine.catalog, engine.execution_policy)
            if not entity.get("class_iri") or entity.get("referent_unresolved"):
                row.update(status="waiting", reason_code="type_or_referent_unresolved")
            rows.append(row)
    elif step == "coreference":
        return plan_coreference_work(engine, commit=commit)
    else:
        # Refinement creates only affected alignment tasks, never replans the full skeleton.
        for domain in ("properties", "relations", "relation_groups"):
            for assertion in state.get(domain, {}).values():
                if assertion.get("reason") in {
                    "ambiguous_subject_members", "referent_alignment_failed",
                }:
                    continue
                review_for(engine, changes, domain, assertion)
    changes["work"].update(upsert_work(state, rows))
    if commit:
        engine.commit(dict(changes))
    return dict(changes)


def plan_calibration_work(engine, *, commit=True):
    rows = []
    for domain in ("entities", "properties", "relations", "relation_groups"):
        for row in engine.state.get(domain, {}).values():
            kind = "entity_calibration" if domain == "entities" else "assertion_calibration"
            data = {"entity_id": row["id"]} if domain == "entities" else {
                "domain": domain, "assertion_id": row["id"],
            }
            rows.append(make_work(kind, data, engine.state, engine.catalog,
                                  engine.execution_policy))
    changes = {"work": upsert_work(engine.state, rows)}
    if commit:
        engine.commit(changes)
    return changes


def calibrate_work(engine, rows, base_window):
    from .deterministic import calibrate, calibration_input_hash

    changes = defaultdict(dict)
    cursor = engine.state["cursor"]["main"]
    changes["cursor"]["main"] = {**cursor, "stage": (
        "identifier_check" if rows[0]["kind"] == "entity_calibration" else "literal_normalization")}
    for work in rows:
        domain = work["input"].get("domain", "entities")
        key = work["input"].get("entity_id", work["input"].get("assertion_id"))
        row = engine.state.get(domain, {}).get(key)
        if row is None:
            complete_work(changes, work, status="waiting", reason="missing_calibration_input")
            continue
        previous = row.get("calibration")
        value = (previous if previous and previous["input_hash"] == calibration_input_hash(
            domain, row, engine.catalog) else calibrate(domain, row, engine.catalog))
        changes[domain][key] = {**row, "calibration": value}
        error = any(c["status"] == "error" for c in value["checks"].values())
        work = {**work, "retryable": False if error else work["retryable"]}
        complete_work(changes, work, status="failed" if error else "done",
                      reason="calibration_tool_error" if error else None, outputs=[key])
    engine.commit(dict(changes))



def scoped_identifier_clues(engine):
    """Index complete local key+scope bindings; external source matches are not proof."""
    buckets = defaultdict(dict)
    for entity in engine.state.get("entities", {}).values():
        binding = entity.get("identity_binding") or {}
        card = engine.catalog.classes.get(entity.get("class_iri"))
        if card is None:
            continue
        identity_properties = {p.iri for p in card.properties if p.identity_key}
        identity_properties.update(p for group in card.identity_key_groups for p in group)
        identifiers = defaultdict(list)
        for item in binding.get("identifiers", []):
            if item.get("quote") and item.get("value") == item["quote"]["text"]:
                identifiers[item["property_iri"]].append(item)
        # Complete ontology-declared keys can locate a comparison without an
        # external mapping. Values remain source-bound clues, never an identity
        # verdict; composite keys must include every scope component.
        available = {p.iri for p in card.properties if p.constraint_status == "resolved"}
        for group in card.identity_key_groups:
            required = sorted(set(group))
            if not required or not set(required) <= available or any(
                len(identifiers[p]) != 1 for p in required
            ):
                continue
            values = tuple((p, identifiers[p][0]["value"]) for p in required)
            bucket = ("ontology_key", card.iri, tuple(required), values)
            buckets[bucket][entity["id"]] = [identifiers[p][0]["quote"] for p in required]
        work = engine.state.get("referent_work", {}).get(binding.get("group_id"), {})
        for capability in work.get("key_context", {}).get("lookup_capabilities", []):
            namespace = capability.get("identifier_namespace")
            if not namespace or capability.get("class_iri") != entity.get("class_iri"):
                continue
            for group in capability.get("lookup_key_groups", []):
                keys, scope = group["property_iris"], group["scope_property_iris"]
                required = sorted(set(keys + scope))
                if (
                    not keys
                    or not scope
                    or not set(keys) <= identity_properties
                    or any(len(identifiers[p]) != 1 for p in required)
                ):
                    continue
                values = tuple((p, identifiers[p][0]["value"]) for p in required)
                bucket = (namespace, tuple(sorted(keys)), tuple(sorted(scope)), values)
                buckets[bucket][entity["id"]] = [identifiers[p][0]["quote"] for p in required]
    return buckets.values()


def plan_coreference_work(engine, *, commit=True):
    state, policy = engine.state, engine.execution_policy
    cues = resolve_reference_cues(engine.ir, state, engine.source_index, policy)
    pairs, observations = {}, {}

    def add(left, right, refs):
        if left == right or "document" in (left, right):
            return
        left, right = sorted((left, right))
        key = (left, right)
        pairs[key] = unique_refs([*pairs.get(key, []), *refs])

    for cue in cues.values():
        if cue.get("reference_targets_truncated"):
            key = identity("coreference_scope", cue["id"])
            observations[key] = {
                "id": key,
                "kind": "scope",
                "label": "共指候选范围受限",
                "reason": "引用目标超过当前配额", "candidate_scope_limited": True,
                "evidence": cue["evidence"],
            }
        if cue.get("relation_label") is not None or cue.get("ambiguous_subject_members"):
            continue
        for target in cue.get("target_ids", []):
            add(cue["subject_id"], target, cue["evidence"])
    for bucket in scoped_identifier_clues(engine):
        ordered = sorted(
            bucket, key=lambda key: engine.source_index.position(state["entities"][key])
        )
        limit = policy["reference_targets_per_cue"]
        for key in ordered:
            targets = [target for target in ordered[:limit + 1] if target != key][:limit]
            for target in targets:
                add(key, target, [*bucket[key], *bucket[target]])
            if len(ordered) - 1 > limit:
                oid = identity("coreference_scope", key, bucket[key])
                observations[oid] = {
                    "id": oid,
                    "kind": "scope",
                    "label": "共指候选范围受限",
                    "reason": "编号目标超过当前配额", "candidate_scope_limited": True,
                    "evidence": bucket[key],
                }
    rows = []
    for (left, right), refs in sorted(pairs.items()):
        rows.append(
            make_work(
                "coreference_review",
                {"left_mention_id": left, "right_mention_id": right, "clue_refs": refs},
                state,
                engine.catalog,
                policy,
            )
        )
    changes = {"work": upsert_work(state, rows), "reference_cues": cues,
               "observations": observations}
    if commit:
        engine.commit(changes)
    return changes


def complete_work(changes, row, *, status="done", reason=None, outputs=()):
    changes.setdefault("work", {})[row["id"]] = {
        **row,
        "status": status,
        "reason_code": reason,
        "applied_dependency_hash": row["dependency_hash"],
        "output_ids": list(outputs),
    }


def review_for(engine, changes, domain, assertion):
    changes.setdefault(domain, {})[assertion["id"]] = assertion
    if engine.state["cursor"]["main"]["phase"] == "skeleton":
        return
    state = merged_state(engine.state, changes)
    kind = (
        "group_interpretation"
        if (domain == "relation_groups" and assertion["participation"] == "unknown"
            and assertion.get("predicate_iri"))
        else "evidence_review"
    )
    data = (
        {"group_id": assertion["id"]}
        if kind == "group_interpretation"
        else {
            "domain": domain,
            "assertion_id": assertion["id"],
        }
    )
    work = make_work(kind, data, state, engine.catalog, engine.execution_policy)
    changes.setdefault("work", {})[work["id"]] = work
    if "deterministic" in engine.state["cursor"]["main"].get("planned_steps", []):
        calibration = make_work(
            "assertion_calibration", {"domain": domain, "assertion_id": assertion["id"]},
            state, engine.catalog, engine.execution_policy,
        )
        changes["work"][calibration["id"]] = calibration


def work_context(engine, rows, base_window):
    ids = {key for row in rows for key in endpoints(row["kind"], row["input"], engine.state)}
    refs = [ref for row in rows for ref in row["dependencies"]["source_refs"]]
    entities = [
        engine.state["entities"][key] for key in sorted(ids) if key in engine.state["entities"]
    ]
    if rows[0]["kind"] == "property_alignment":
        selected_fields = {row["input"]["field_id"] for row in rows}
        entities = [
            {
                **entity,
                "field_ids": [key for key in entity.get("field_ids", []) if key in selected_fields],
            }
            for entity in entities
        ]
    elif rows[0]["kind"] in {"relation_alignment", "evidence_review", "group_interpretation"}:
        selected_fields = {key for row in rows for key in row["dependencies"]["field_ids"]}
        selected_fields.update(
            binding["field_id"]
            for entity in entities
            for binding in (entity.get("identity_binding") or {}).get("identifiers", [])
            if binding.get("field_id")
        )
        entities = [
            {
                **entity,
                "field_ids": [key for key in entity.get("field_ids", []) if key in selected_fields],
            }
            for entity in entities
        ]
    sections = {entity["referent"]["section_id"] for entity in entities if entity.get("referent")}
    tables = {
        tuple(engine.ir.unit(entity["referent"]["source_id"]).table_path or [])
        for entity in entities
        if entity.get("referent")
    }
    # Row labels and condition cells are source context for relation ownership.
    # Keeping only row zero loses labels such as the operation's batch/phase.
    relation_rows = {
        (tuple(unit.table_path), unit.row_index)
        for ref in [*refs, *(entity["referent"] for entity in entities if entity.get("referent"))]
        if (unit := engine.ir.unit(ref["source_id"])).table_path
    } if rows[0]["kind"] in {
        "relation_alignment", "evidence_review", "group_interpretation",
    } else set()
    refs.extend(
        reference(engine.ir, unit.evidence_id, 0, len(unit.text))
        for unit in engine.ir.evidence_units
        if unit.text.strip()
        and (
            unit.kind == "heading"
            and unit.section_node_id in sections
            or unit.table_path
            and tuple(unit.table_path) in tables
            and unit.row_index == 0
            or unit.table_path
            and (tuple(unit.table_path), unit.row_index) in relation_rows
        )
    )
    empty = Window(base_window.id if base_window else "work", [], [], [])
    window = context_window(engine.ir, empty, entities, engine.state.get("fields", {}), refs)
    return window


def bounded_call(
    engine, stage, rows, window, payload, schema, base_window, handler, *, aliases=None
):
    from .controller import request_size

    if request_size(stage, payload, schema) > engine.max_request_bytes:
        if len(rows) == 1:
            raise ValueError("HARNESS_EVIDENCE_CONTEXT_TOO_LARGE")
        return handler(engine, rows[: len(rows) // 2], base_window)
    cursor = engine.state["cursor"]["main"]
    if cursor["stage"] != stage:
        engine.commit({"cursor": {"main": {**cursor, "stage": stage}}})
    targets = [
        {"domain": "work", "id": row["id"], "dependency_hash": row["dependency_hash"]}
        for row in rows
    ]
    return engine.call(stage, payload, schema, window=window, targets=targets, aliases=aliases)


def align_properties(engine, rows, base_window, selected_predicates=None):
    from .controller import request_size

    window = work_context(engine, rows, base_window)
    subject = engine.state["entities"][rows[0]["input"]["subject_id"]]
    card = model_menu(engine.catalog, [subject["class_iri"]])["classes"][0]
    all_predicates = {p["iri"] for p in card["properties"]}
    remaining = all_predicates - set(rows[0].get("processed_predicate_iris", []))
    selected_predicates = remaining if selected_predicates is None else selected_predicates
    card = {
        **card,
        "properties": [p for p in card["properties"] if p["iri"] in selected_predicates],
    }
    changes = defaultdict(dict)
    if not card["properties"]:
        for row in rows:
            field = engine.state["fields"][row["input"]["field_id"]]
            engine.record_field_alignment(
                changes,
                window,
                field,
                subject,
                [],
                "当前类型卡无合法属性可对齐",
                "unmatched",
            )
            complete_work(changes, row, reason="no_legal_property")
        engine.commit(dict(changes))
        return
    payload = {
        "sources": window.payload()["sources"],
        "subject": engine.entity_input(subject, window, typed=True),
        "property_field_ids": [
            engine.field_alias(row["input"]["field_id"], window) for row in rows
        ],
        "card": {**card, "relations": []},
        "annotation_contracts": model_menu(engine.catalog, [subject["class_iri"]])[
            "annotation_contracts"
        ],
    }
    schema = stage_schema(
        "property_alignment",
        source_ids=[s["source_id"] for s in window.sources],
        field_ids=payload["property_field_ids"],
        property_iris=[p["iri"] for p in card["properties"]],
    )
    if request_size("property_alignment", payload, schema) > engine.max_request_bytes:
        if len(rows) > 1:
            return align_properties(
                engine, rows[: len(rows) // 2], base_window, selected_predicates
            )
        if len(card["properties"]) > 1:
            selected = {p["iri"] for p in card["properties"][: len(card["properties"]) // 2]}
            return align_properties(engine, rows, base_window, selected)
        raise ValueError("HARNESS_EVIDENCE_CONTEXT_TOO_LARGE")
    response = bounded_call(
        engine, "property_alignment", rows, window, payload, schema, base_window, align_properties
    )
    if response is None:
        return
    answer, batch_id = response
    for row in rows:
        field = engine.state["fields"][row["input"]["field_id"]]
        proposed = answer.properties[engine.field_alias(field["id"], window)]
        output_ids, seen = list(row.get("output_ids", [])), set()
        mapping_errors = []
        conflicting = {
            mapping.predicate_iri
            for mapping in proposed.mappings
            if len(
                {
                    other.value_component
                    for other in proposed.mappings
                    if other.predicate_iri == mapping.predicate_iri
                }
            )
            > 1
        }
        for mapping in proposed.mappings:
            try:
                if mapping.predicate_iri in conflicting:
                    raise ValueError(
                        "同一属性不能同时承载范围的不同组件；保留完整原值，待按定义对齐"
                    )
                if (
                    mapping.predicate_iri not in selected_predicates
                    or mapping.predicate_iri in seen
                    or not legal_property(
                        engine.catalog, subject["class_iri"], mapping.predicate_iri
                    )
                ):
                    raise ValueError(
                        "property_outside_current_card_or_missing_value: " + mapping.predicate_iri
                    )
                seen.add(mapping.predicate_iri)
                component = property_value(
                    field,
                    mapping,
                    window=window,
                    ir=engine.ir,
                )
                prop = next(p for p in card["properties"] if p["iri"] == mapping.predicate_iri)
                draft = next((p for p in engine.state.get("properties", {}).values()
                              if p["subject_id"] == subject["id"] and p["field_id"] == field["id"]
                              and p.get("predicate_iri") in (None, mapping.predicate_iri)
                              and p["id"] not in output_ids), None)
                key = draft["id"] if draft else identity(
                    "property", subject["id"], mapping.predicate_iri, field["id"],
                    mapping.value_component, component["evidence"],
                )
                unit_ref = (window.resolve(engine.ir, mapping.unit_quote)
                            if mapping.unit_quote else None)
                assertion = {
                    "id": key,
                    "subject_id": subject["id"],
                    "alignment_class_iri": subject["class_iri"],
                    "predicate_iri": mapping.predicate_iri,
                    "label": prop["label"],
                    "value": component["value"],
                    "source_value": field["value"],
                    "source_unit": unit_ref["text"] if unit_ref else None,
                    "source_unit_evidence": [unit_ref] if unit_ref else [],
                    "calibration": None,
                    "value_component": mapping.value_component,
                    "value_evidence": component["evidence"],
                    "field_id": field["id"],
                    "state": "candidate",
                    "reason": proposed.reason,
                    "evidence": field["evidence"],
                    "confidence": mapping.confidence,
                    "window_id": base_window.id if base_window else None,
                }
                old = engine.state.get("properties", {}).get(key)
                if old and old.get("verification"):
                    assertion["verification"] = old["verification"]
                failure = check_candidate_structure(engine.ir, engine.catalog,
                                                    engine.state, assertion)
                if failure:
                    raise ValueError(failure["reason_code"])
                review_for(engine, changes, "properties", assertion)
                output_ids.append(key)
                engine.record_field_alignment(
                    changes,
                    window,
                    field,
                    subject,
                    [mapping.predicate_iri],
                    proposed.reason,
                    "mapped",
                )
            except ValueError as exc:
                mapping_errors.append(str(exc))
                engine.record_field_alignment(
                    changes, window, field, subject, [mapping.predicate_iri], str(exc), "invalid"
                )
        if not proposed.mappings:
            engine.record_field_alignment(
                changes,
                window,
                field,
                subject,
                list(selected_predicates),
                proposed.reason,
                "unmatched",
            )
        processed = sorted(set(row.get("processed_predicate_iris", [])) | selected_predicates)
        updated = {**row, "processed_predicate_iris": processed}
        if mapping_errors:
            complete_work(changes, {**updated, "retryable": False}, status="failed",
                          reason=mapping_errors[0], outputs=output_ids)
        elif all_predicates <= set(processed):
            complete_work(changes, updated, outputs=output_ids)
        else:
            changes["work"][row["id"]] = {**updated, "status": "ready", "output_ids": output_ids}
    engine.commit(dict(changes), batch_id=batch_id)


def align_relations(engine, rows, base_window):
    changes = defaultdict(dict)
    unresolved = []
    active = next(iter(engine.state["cursor"]["main"].get("active_batches", {}).values()), None)
    for row in rows:
        proof = (
            None
            if active or engine.state["cursor"]["main"]["phase"] == "skeleton"
            else try_rule_seed(
                engine.ir,
                engine.catalog,
                gate_state(engine),
                row["input"],
                engine.execution_policy["table_relation_rules"],
            )
        )
        if proof:
            assertion, rule = proof
            assertion["verification"] = {
                "method": "rule",
                "semantic_verdict": "accepted",
                **{k: v for k, v in rule.items() if k != "evidence_refs"},
            }
            assertion["state"] = semantic_acceptance(engine.state, assertion, "accepted")
            changes["relations"][assertion["id"]] = assertion
            complete_work(changes, row, outputs=[assertion["id"]])
        else:
            unresolved.append(row)
    if changes:
        engine.commit(dict(changes))
        return
    rows = unresolved
    window = work_context(engine, rows, base_window)
    entity_ids = sorted(
        {key for row in rows for key in endpoints(row["kind"], row["input"], engine.state)}
    )
    aliases = engine.entity_aliases(window)
    predicates = {}
    for row in rows:
        seed = row["input"]
        predicates[seed["predicate_iri"]] = next(
            r.model_dump(mode="json")
            for card in engine.catalog.classes.values() for r in card.relations
            if r.iri == seed["predicate_iri"]
        )
    payload = {
        "sources": window.payload()["sources"],
        "entities": [
            engine.entity_input(engine.state["entities"][key], window, typed=True)
            for key in entity_ids
        ],
        "predicates": list(predicates.values()),
        "items": [
            {
                "candidate_id": row["id"],
                "subject_id": aliases[row["input"]["subject_id"]],
                "object_ids": [aliases[key] for key in row["input"]["object_ids"]],
                "predicate_iri": row["input"]["predicate_iri"],
                "clue_sources": [
                    quote_for_reference(window, ref)["source_id"]
                    for ref in row["input"]["clue_refs"]
                ],
                "polarity_hint": row["input"]["polarity_hint"],
                "condition_hints": row["input"]["condition_hints"],
            }
            for row in rows
        ],
    }
    schema = stage_schema(
        "relation_alignment",
        source_ids=[s["source_id"] for s in window.sources],
        relation_items=payload["items"],
    )
    response = bounded_call(
        engine, "relation_alignment", rows, window, payload, schema, base_window, align_relations
    )
    if response is None:
        return
    answer, batch_id = response
    for row in rows:
        proposed = answer.proposals[row["id"]]
        if proposed.verdict != "proposed":
            if (
                proposed.verdict == "unresolved"
                and proposed.missing_context != "none"
                and expand_work(engine, row, changes)
            ):
                continue
            complete_work(
                changes,
                row,
                status="waiting" if proposed.verdict == "unresolved" else "done",
                reason="insufficient_context"
                if proposed.verdict == "unresolved"
                else "no_relation",
            )
            continue
        seed = row["input"]
        group = len(seed["object_ids"]) > 1
        domain = "relation_groups" if group else "relations"
        key = identity(
            "relation_group" if group else "relation",
            seed["subject_id"],
            seed["predicate_iri"],
            seed["object_ids"] if group else seed["object_ids"][0],
            *([proposed.participation, proposed.selection, proposed.timing] if group else []),
            proposed.polarity,
            proposed.conditions,
        )
        draft = next((r for r in engine.state.get(domain, {}).values()
                      if r["subject_id"] == seed["subject_id"]
                      and r.get("object_ids", [r.get("object_id")]) == seed["object_ids"]
                      and r.get("predicate_iri") in (None, seed["predicate_iri"])
                      and set(r.get("origin_ids", [])).intersection(seed["origin_ids"])), None)
        if draft:
            key = draft["id"]
        order = temporal_order(proposed, seed["object_ids"], aliases, window, engine.ir)
        assertion = {
            "id": key,
            "subject_id": seed["subject_id"],
            "predicate_iri": seed["predicate_iri"],
            "alignment_class_iri": engine.state["entities"][seed["subject_id"]]["class_iri"],
            "label": predicates[seed["predicate_iri"]]["label"],
            "state": "candidate",
            "reason": proposed.reason,
            "proposal_reason": proposed.reason,
            "origin_ids": seed["origin_ids"],
            "calibration": None,
            "evidence": unique_refs(
                [*window.quotes(engine.ir, proposed.evidence), *seed["required_context_refs"]]
            ),
            "polarity": proposed.polarity,
            "conditions": proposed.conditions,
            "confidence": proposed.confidence,
            "window_id": base_window.id if base_window else None,
        }
        if group:
            assertion.update(
                object_ids=seed["object_ids"],
                participation=proposed.participation,
                selection=proposed.selection,
                timing=proposed.timing,
                timing_state="candidate",
                timing_reason="时间待独立核对",
                **order,
            )
        else:
            assertion["object_id"] = seed["object_ids"][0]
        old = engine.state.get(domain, {}).get(key)
        if old:
            assertion["evidence"] = unique_refs([*old["evidence"], *assertion["evidence"]])
            assertion["verification"] = old.get("verification")
        review_for(engine, changes, domain, assertion)
        complete_work(changes, row, outputs=[key])
    engine.commit(dict(changes), batch_id=batch_id)


def expand_work(engine, row, changes):
    if row["expansions_used"] >= engine.execution_policy["context_expansions_per_dependency"]:
        return False
    current = row["dependencies"]["source_refs"]
    units = engine.ir.evidence_units
    selected = []
    for ref in current:
        unit = engine.ir.unit(ref["source_id"])
        index = units.index(unit)
        if unit.table_path:
            selected.extend(
                u for u in units if u.table_path == unit.table_path and u.row_index == 0
            )
        for step in (-1, 1):
            position = index + step
            while 0 <= position < len(units):
                other = units[position]
                if other.section_node_id != unit.section_node_id:
                    break
                if other.text.strip() and not other.navigation_role and other.kind != "heading":
                    selected.append(other)
                    break
                position += step
    refs = unique_refs(
        [
            *current,
            *(reference(engine.ir, u.evidence_id, 0, len(u.text)) for u in selected if u.text),
        ]
    )
    if refs == unique_refs(current):
        return False
    data = {**row["input"], "required_context_refs": refs}
    updated = make_work(row["kind"], data, engine.state, engine.catalog, engine.execution_policy,
                        phase=row["phase"])
    updated.update(expansion_basis_hash=row["expansion_basis_hash"], expansions_used=1)
    changes.setdefault("work", {})[row["id"]] = updated
    return True


def temporal_order(proposal, objects, aliases, window, ir):
    if proposal.timing != "sequential":
        return {"ordered_object_ids": None, "order_evidence": []}
    inverse = {alias: key for key, alias in aliases.items()}
    if set(proposal.ordered_object_ids) != {aliases[key] for key in objects}:
        raise ValueError("sequential_object_set_mismatch")
    return {"ordered_object_ids": [inverse[key] for key in proposal.ordered_object_ids],
            "order_evidence": [window.resolve(ir, quote) for quote in proposal.order_evidence]}


def interpret_group(engine, rows, base_window):
    row = rows[0]
    changes = defaultdict(dict)
    group = engine.state["relation_groups"][row["input"]["group_id"]]
    if group["participation"] != "unknown":
        review_for(engine, changes, "relation_groups", group)
        complete_work(changes, row, outputs=[group["id"]])
        engine.commit(dict(changes))
        return
    if row["expansions_used"] == 0:
        if not expand_work(engine, row, changes):
            complete_work(changes, row, status="waiting", reason="insufficient_context")
            changes["relation_groups"][group["id"]] = {
                **group,
                "state": "unresolved",
                "timing_state": "unresolved",
                "reason": "参与方式缺少新的补充证据",
            }
        engine.commit(dict(changes))
        return
    window = work_context(engine, [row], base_window)
    payload = {
        "sources": window.payload()["sources"],
        "group": {
            **{
                k: group[k]
                for k in (
                    "predicate_iri",
                    "participation",
                    "selection",
                    "timing",
                    "polarity",
                    "conditions",
                )
            },
            "subject": engine.entity_input(
                engine.state["entities"][group["subject_id"]], window, typed=True
            ),
            "objects": [
                engine.entity_input(engine.state["entities"][key], window, typed=True)
                for key in group["object_ids"]
            ],
        },
    }
    schema = stage_schema(
        "group_interpretation", source_ids=[s["source_id"] for s in window.sources],
        entity_ids=[engine.entity_aliases(window)[key] for key in group["object_ids"]]
    )
    response = bounded_call(
        engine, "group_interpretation", [row], window, payload, schema, base_window, interpret_group
    )
    answer, batch_id = response
    if answer.verdict == "supported":
        key = group["id"]
        updated = {
            **group,
            "id": key,
            "participation": answer.participation,
            "selection": answer.selection,
            "timing": answer.timing,
            "evidence": unique_refs(
                [
                    *group["evidence"],
                    *window.quotes(engine.ir, answer.evidence),
                ]
            ),
            "reason": answer.reason,
            "state": "candidate",
            "verification": None,
            "calibration": None,
            **temporal_order(answer, group["object_ids"], engine.entity_aliases(window),
                             window, engine.ir),
        }
        review_for(engine, changes, "relation_groups", updated)
        completed = make_work(
            "group_interpretation",
            {**row["input"], "group_id": key},
            merged_state(engine.state, changes),
            engine.catalog,
            engine.execution_policy,
        )
        completed.update(expansions_used=row["expansions_used"], last_call_key=row["last_call_key"])
        if completed["id"] != row["id"]:
            changes["work"][row["id"]] = None
        complete_work(changes, completed, outputs=[key])
    else:
        changes["relation_groups"][group["id"]] = {
            **group,
            "state": "unresolved",
            "timing_state": "unresolved",
            "reason": answer.reason,
        }
        complete_work(
            changes, row, status="waiting", reason="insufficient_context", outputs=[group["id"]]
        )
    engine.commit(dict(changes), batch_id=batch_id)


def review_assertions(engine, rows, base_window):
    changes, semantic = defaultdict(dict), []
    active = next(iter(engine.state["cursor"]["main"].get("active_batches", {}).values()), None)
    for work in rows:
        domain, key = work["input"]["domain"], work["input"]["assertion_id"]
        row = engine.state.get(domain, {}).get(key)
        if not row:
            complete_work(changes, work, status="waiting", reason="missing_assertion")
            continue
        if active:
            semantic.append(work)
            continue
        gate = route_semantic_review(engine.ir, engine.catalog, gate_state(engine), row)
        action = gate["action"]
        if action == "semantic":
            semantic.append(work)
            continue
        if action in {"proven", "reuse"}:
            verification = (
                gate["reused_verification"]
                if action == "reuse"
                else {
                    "method": "rule",
                    "semantic_verdict": "accepted",
                    **gate["proof"],
                }
            )
            verification = {k: v for k, v in verification.items() if k != "evidence_refs"}
            changes[domain][key] = {
                **row,
                "verification": verification,
                "state": semantic_acceptance(engine.state, row, verification["semantic_verdict"]),
            }
            if domain == "relation_groups":
                changes[domain][key]["timing_state"] = (
                    row.get("source_timing_verdict", "unresolved")
                    if changes[domain][key]["state"] == "accepted"
                    and row["timing"] != "unspecified"
                    else "unresolved"
                )
            complete_work(changes, work, outputs=[key])
        elif action == "waiting" and expand_work(engine, work, changes):
            continue
        else:
            changes[domain][key] = {**row, "state": "unresolved", "reason": gate["reason_code"]}
            complete_work(
                changes,
                work,
                status="waiting" if action == "waiting" else "done",
                reason=gate["reason_code"],
                outputs=[key],
            )
    if changes:
        engine.commit(dict(changes))
        return
    rows = semantic
    if not rows:
        return
    window = work_context(engine, rows, base_window)
    candidates, bindings = [], {}
    for work in rows:
        domain, key = work["input"]["domain"], work["input"]["assertion_id"]
        row = engine.state[domain][key]
        subject = engine.state["entities"][row["subject_id"]]
        card = model_menu(engine.catalog, [subject["class_iri"]])["classes"][0]
        candidate = {
            k: row[k]
            for k in (
                "value",
                "source_value",
                "source_unit",
                "value_component",
                "polarity",
                "conditions",
                "participation",
                "selection",
            )
            if k in row
        }
        candidate.update(
            id=f"C{len(candidates) + 1}",
            kind=domain,
            subject=engine.entity_input(subject, window, typed=True),
            predicate_definition=next(
                p
                for p in card["properties" if domain == "properties" else "relations"]
                if p["iri"] == row["predicate_iri"]
            ),
            evidence=[quote_for_reference(window, ref)["source_id"] for ref in row["evidence"]],
        )
        if domain == "properties":
            candidate["unit_quote"] = [quote_for_reference(window, r)
                                       for r in row.get("source_unit_evidence", [])]
            candidate["value_quote"] = [
                quote_for_reference(window, r) for r in row["value_evidence"]
            ]
        elif domain == "relations":
            candidate["object"] = engine.entity_input(
                engine.state["entities"][row["object_id"]], window, typed=True
            )
        else:
            candidate["objects"] = [
                engine.entity_input(engine.state["entities"][oid], window, typed=True)
                for oid in row["object_ids"]
            ]
        candidate["related_clues"] = [
            {"evidence": [quote_for_reference(window, ref) for ref in hint.get("evidence", [])],
             "polarity": hint.get("polarity", "uncertain"),
             "conditions": hint.get("conditions", [])}
            for hint in _related_hints(engine.state, row)
        ]
        candidates.append(candidate)
        bindings[candidate["id"]] = (work, domain, row, False)
        if domain == "relation_groups" and row["timing"] != "unspecified":
            timing = {
                **candidate,
                "id": f"C{len(candidates) + 1}",
                "kind": "relation_timing",
                "timing": row["timing"],
                "ordered_object_ids": [engine.entity_aliases(window)[key]
                                       for key in row.get("ordered_object_ids") or []] or None,
                "order_evidence": [quote_for_reference(window, r)
                                   for r in row.get("order_evidence", [])],
            }
            candidates.append(timing)
            bindings[timing["id"]] = (work, domain, row, True)
    payload = {"sources": window.payload()["sources"], "candidates": candidates}
    aliases = {alias: row["id"] for alias, (_, _, row, _) in bindings.items()}
    schema = stage_schema(
        "evidence_review",
        source_ids=[s["source_id"] for s in window.sources],
        candidate_ids=list(bindings),
        entity_ids=list(engine.entity_aliases(window).values()),
    )
    response = bounded_call(
        engine,
        "evidence_review",
        rows,
        window,
        payload,
        schema,
        base_window,
        review_assertions,
        aliases=aliases,
    )
    if response is None:
        return
    answer, batch_id = response
    for alias, judgment in answer.judgments.items():
        work, domain, row, timing = bindings[alias]
        refs = window.quotes(engine.ir, judgment.evidence)
        verdict, reason = judgment.verdict, judgment.reason
        required = [
            engine.state["entities"][key]["referent"]
            for key in endpoints("evidence_review", work["input"], engine.state)
            if engine.state["entities"][key].get("referent")
        ]
        if domain == "properties":
            required.extend(engine.state["fields"][row["field_id"]]["value_evidence"])
            required.extend(row.get("source_unit_evidence", []))
        if timing:
            required.extend(row.get("order_evidence", []))
        if verdict == "accepted" and (
            judgment.confidence < 0.85
            or not refs
            or not all(references_cover(ref, refs) for ref in required)
        ):
            verdict, reason = "unresolved", "acceptance_requires_source_and_high_confidence"
        updated = changes[domain].get(row["id"], dict(row))
        if timing:
            updated.update(
                timing_state=semantic_acceptance(engine.state, row, verdict),
                source_timing_verdict=verdict,
                timing_reason=reason,
                timing_evidence=refs,
            )
        else:
            updated.update(
                state=semantic_acceptance(engine.state, row, verdict),
                reason=reason,
                review_evidence=refs,
                source_verdict=verdict,
                source_reason=reason,
                verification={
                    "method": "llm",
                    "semantic_verdict": verdict,
                    "dependency_hash": assertion_dependency_hash(
                        engine.ir, engine.catalog, gate_state(engine), row
                    ),
                    "rule_id": None,
                    "rule_version": None,
                    "rule_hash": None,
                },
            )
        changes[domain][row["id"]] = updated
        complete_work(changes, work, outputs=[row["id"]])
    for row in changes.get("relation_groups", {}).values():
        if row["timing"] == "unspecified" or row["state"] != "accepted":
            row.update(timing_state="unresolved", timing_reason="未证明参与关系或时间")
    aliases = {value: key for key, value in engine.entity_aliases(window).items()}
    for concern in answer.type_concerns:
        if concern.entity_id not in aliases:
            raise ValueError("type_concern_endpoint_outside_batch")
        key, item = engine.observation(
            window,
            "主体类型疑点（交由类型处理）",
            concern.reason,
            window.quotes(engine.ir, concern.evidence),
            kind="entity",
            candidate_subject_ids=[aliases[concern.entity_id]],
        )
        changes["observations"][key] = item
    engine.commit(dict(changes), batch_id=batch_id)


def drain_work(engine, base_window, *, phase=None, kind=None):
    cursor = engine.state["cursor"]["main"]
    phase = phase or cursor["phase"]
    steps = {"referents": {"referent_alignment"}, "entities": {"entity_review"},
             "coreference": {"coreference_review"},
             "assertions": {"property_alignment", "relation_alignment", "evidence_review",
                            "group_interpretation"}}
    batch = next(iter(cursor.get("active_batches", {}).values()), None)
    if batch and any(t["domain"] == "work" for t in batch["targets"]):
        rows = [engine.state.get("work", {}).get(target["id"]) for target in batch["targets"]]
        if any(
            row is None or row["dependency_hash"] != target["dependency_hash"]
            for row, target in zip(rows, batch["targets"])
        ):
            cursor = engine.state["cursor"]["main"]
            engine.commit({}, batch_id=batch["batch_id"])
            return True
    else:
        local_entities = (
            set(engine.state.get("window_entities", {}).get(base_window.id, {}).get("ids", []))
            if base_window
            else set()
        )
        local_sources = (
            {source["evidence_id"] for source in base_window.sources} if base_window else set()
        )
        rows = sorted(
            (
                row
                for row in engine.state.get("work", {}).values()
                if row["status"] == "ready"
                and row["phase"] == phase
                and (kind is None or row["kind"] == kind)
                and (phase != "semantic" or row["kind"] in steps[cursor["semantic_step"]])
                and (
                    base_window is None
                    or local_entities.intersection(row["dependencies"]["entity_ids"])
                    or local_sources.intersection(
                        ref["source_id"] for ref in row["dependencies"]["source_refs"]
                    )
                    or local_sources.intersection(
                        ref["source_id"]
                        for field_id in row["dependencies"]["field_ids"]
                        for ref in engine.state.get("fields", {})
                        .get(field_id, {})
                        .get("evidence", [])
                    )
                )
            ),
            key=lambda row: (row["kind"] != "property_alignment", row["id"]),
        )
        if not rows:
            return False
        first = rows[0]
        kind = first["kind"]
        if kind == "property_alignment":
            rows = [
                r
                for r in rows
                if r["kind"] == kind
                and r["input"]["subject_id"] == first["input"]["subject_id"]
                and r.get("processed_predicate_iris", [])
                == first.get("processed_predicate_iris", [])
            ][:32]
        elif kind == "relation_alignment":
            data = first["input"]
            # Each item already carries its own endpoints, predicate and clues.
            # The source region is a candidate-planning boundary, not a reason
            # to send one model request per row/subject/predicate in a section.
            sections = {
                engine.ir.unit(ref["source_id"]).section_node_id
                for ref in first["dependencies"]["source_refs"]
            }
            rows = [
                r
                for r in rows
                if r["kind"] == kind
                and (
                    r["input"]["region_id"] == data["region_id"]
                    or sections.intersection(
                        engine.ir.unit(ref["source_id"]).section_node_id
                        for ref in r["dependencies"]["source_refs"]
                    )
                )
            ][: engine.execution_policy["relation_items_per_call"]]
        elif kind == "evidence_review":
            # A whole group contributes up to two judgments, never split R/T.
            selected, size = [], 0

            def scope(work):
                return {
                    engine.ir.unit(ref["source_id"]).section_node_id
                    for ref in work["dependencies"]["source_refs"]
                }

            shared_scope = scope(first)
            for row in rows:
                if row["kind"] != kind or (
                    row is not first and not shared_scope.intersection(scope(row))
                ):
                    continue
                assertion = engine.state.get(row["input"]["domain"], {}).get(
                    row["input"]["assertion_id"], {}
                )
                cost = 2 if assertion.get("timing", "unspecified") != "unspecified" else 1
                if size + cost > engine.execution_policy["review_judgments_per_call"]:
                    break
                selected.append(row)
                size += cost
            rows = selected
        elif kind == "entity_review":
            entity = engine.state["entities"][first["input"]["entity_id"]]
            from .controller import overlapping_referents

            rows = [first]
            for row in list(engine.state.get("work", {}).values()):
                if row["kind"] != kind or row["status"] != "ready" or row is first:
                    continue
                other = engine.state["entities"][row["input"]["entity_id"]]
                if other.get("window_id") == entity.get("window_id") and all(
                    not overlapping_referents(
                        other, engine.state["entities"][r["input"]["entity_id"]])
                    for r in rows
                ):
                    rows.append(row)
                if len(rows) == 6:
                    break
        elif kind == "coreference_review":
            rows = [r for r in rows if r["kind"] == kind][:6]
        else:
            rows = [first]
    try:
        kind = rows[0]["kind"]
        if kind == "referent_alignment":
            from .referents import run_referent_alignment

            entity = engine.state["entities"][rows[0]["input"]["mention_ids"][0]]
            window = next(w for w in engine.windows if w.id == entity["window_id"])
            run_referent_alignment(engine, window, work_row=rows[0])
        elif kind == "entity_review":
            entity = engine.state["entities"][rows[0]["input"]["entity_id"]]
            window = next(w for w in engine.windows if w.id == entity["window_id"])
            engine.entity_review(window, work_rows=rows)
        elif kind == "coreference_review":
            from .coreference import review_coreferences

            review_coreferences(engine, work_rows=rows)
        else:
            {
                "property_alignment": align_properties,
                "relation_alignment": align_relations,
                "evidence_review": review_assertions,
                "group_interpretation": interpret_group,
                "entity_calibration": calibrate_work,
                "assertion_calibration": calibrate_work,
            }[kind](engine, rows, base_window)
    except Exception as exc:
        from app.services.llm.model_runtime import ModelCancelled

        from .controller import Paused

        if isinstance(exc, (Paused, ModelCancelled)):
            raise
        # Complete valid JSON can contain a local logical error. Keep the graph,
        # clear that paid application batch and continue unrelated work.
        local = isinstance(exc, ValueError) and not str(exc).startswith((
            "harness_model", "harness_active_batch", "harness_paid", "HARNESS_EVIDENCE",
        ))
        reason = ("referent_group_budget_exceeded" if kind == "referent_alignment"
                  and str(exc) == "HARNESS_EVIDENCE_CONTEXT_TOO_LARGE" else str(exc))
        local = local or reason == "referent_group_budget_exceeded"
        changes = {"work": {row["id"]: {**row, "status": "failed", "reason_code": reason,
                                         "retryable": not local} for row in rows}}
        if kind == "referent_alignment" and local:
            ids = set(rows[0]["input"]["mention_ids"])
            changes["entities"] = {key: {**engine.state["entities"][key], "state": "unresolved",
                                         "referent_unresolved": True,
                                         "reason": "referent_alignment_failed"} for key in ids}
            for domain in ("properties", "relations", "relation_groups"):
                changes[domain] = {key: {**claim, "state": "unresolved",
                                         "reason": "referent_alignment_failed",
                                         "verification": None, "calibration": None}
                                   for key, claim in engine.state.get(domain, {}).items()
                                   if ids.intersection([claim["subject_id"], claim.get("object_id"),
                                                        *claim.get("object_ids", [])])}
        active = engine.state["cursor"]["main"].get("active_batches", {})
        engine.commit(changes, batch_id=next(iter(active)) if local and active else None)
        if not local:
            raise
    return True
