"""Explicit work handlers for the source-first harness, with one paid batch at a time."""

from collections import defaultdict
from copy import deepcopy

from .evidence_gate import assertion_dependency_hash, precheck_assertion, try_rule_seed
from .observations import property_value
from .ontology import legal_property, model_menu
from .planning import admit_relation_work, collect_relation_seeds, context_window
from .protocols import stage_schema
from .referents import validate_bound_property
from .source import Window, identity, quote_for_reference, reference, references_cover
from .work import dependency_hash, endpoints, make_work, unique_refs


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


def acceptance(state, row, verdict):
    if verdict != "accepted":
        return verdict
    ids = [
        row["subject_id"],
        *row.get("object_ids", []),
        *([row["object_id"]] if row.get("object_id") else []),
    ]
    if any(state.get("entities", {}).get(key, {}).get("state") != "accepted" for key in ids):
        return "unresolved"
    if "object_ids" in row and row.get("participation") == "unknown":
        return "unresolved"
    return "accepted"


def invalidate_changes(engine, changes):
    if not engine.state.get("work"):
        return
    after = merged_state(engine.state, changes)
    affected = engine.work_index.affected(changes)
    # Assertion edits invalidate the corresponding current review, never all work.
    for domain in ("properties", "relations", "relation_groups"):
        for key in changes.get(domain, {}):
            review_key = identity("work", "evidence_review", [domain, key])
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
        if fingerprint != old["dependency_hash"]:
            ready = make_work(old["kind"], data, after, engine.catalog, engine.execution_policy)
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
                    verdict = acceptance(after, row, verification["semantic_verdict"])
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


def plan_work(engine, window):
    state, policy = engine.state, engine.execution_policy
    ids = set(state.get("window_entities", {}).get(window.id, {}).get("ids", [])) | {"document"}
    # Existing cues are revisited through the source index; no entity-pair matrix.
    delta = {
        "entities": {key: state["entities"][key] for key in ids if key in state["entities"]},
        "fields": {f["id"]: state["fields"].get(f["id"], f) for f in window.fields},
        "hints": {
            key: row
            for key, row in state.get("hints", {}).items()
            if row.get("window_id") == window.id
        },
    }
    seeds = collect_relation_seeds(
        engine.ir, engine.catalog, state, delta, engine.source_index, policy
    )
    ranking = (
        engine.rank_pairs
        if engine.policy.get("card_ranking", {}).get("semantic", {}).get("enabled")
        else None
    )
    rows = admit_relation_work(seeds, state, engine.catalog, policy, ranking)
    changes = {
        "work": {row["id"]: row for row in rows},
        "observations": engine.source_index.scope_changes,
        "reference_cues": engine.source_index.cue_changes,
    }
    for subject in state["entities"].values():
        if not subject.get("class_iri"):
            continue
        for field_id in subject.get("field_ids", []):
            field = state.get("fields", {}).get(field_id)
            if not field or (
                subject["id"] == "document" and not engine.field_visible(field_id, window)
            ):
                continue
            row = make_work(
                "property_alignment",
                {"subject_id": subject["id"], "field_id": field_id},
                state,
                engine.catalog,
                policy,
            )
            if subject["state"] != "accepted":
                row.update(status="waiting", reason_code="type_or_constraint_unresolved")
            if field.get("missing"):
                row.update(status="done", reason_code="missing_source_value")
            changes["work"][row["id"]] = row
    for cue in changes["reference_cues"].values():
        if cue.get("relation_label") is not None or cue.get("ambiguous_subject_members"):
            continue
        for target in cue.get("target_ids", []):
            if cue["subject_id"] == "document":
                continue
            row = make_work(
                "coreference_review",
                {
                    "left_mention_id": cue["subject_id"],
                    "right_mention_id": target,
                    "clue_refs": cue["evidence"],
                },
                state,
                engine.catalog,
                policy,
            )
            if any(
                state["entities"][key]["state"] != "accepted" for key in [cue["subject_id"], target]
            ):
                row.update(status="waiting", reason_code="type_or_constraint_unresolved")
            changes["work"][row["id"]] = row
    engine.commit(changes)


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
    state = merged_state(engine.state, changes)
    kind = (
        "group_interpretation"
        if domain == "relation_groups" and assertion["participation"] == "unknown"
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
        )
    )
    empty = Window(base_window.id if base_window else "work", [], [], [])
    window = context_window(engine.ir, empty, entities, engine.state.get("fields", {}), refs)
    engine._call_window = window
    engine._call_targets = [
        {"domain": "work", "id": row["id"], "dependency_hash": row["dependency_hash"]}
        for row in rows
    ]
    return window


def bounded_call(engine, stage, rows, window, payload, schema, base_window, handler):
    from .controller import request_size

    if request_size(stage, payload, schema) > engine.max_request_bytes:
        if len(rows) == 1:
            raise ValueError("HARNESS_EVIDENCE_CONTEXT_TOO_LARGE")
        # Dispatch only the first half. The rest remains ready for the next turn.
        return handler(engine, rows[: len(rows) // 2], base_window)
    engine._call_window = window
    engine._call_targets = [
        {"domain": "work", "id": row["id"], "dependency_hash": row["dependency_hash"]}
        for row in rows
    ]
    cursor = engine.state["cursor"]["main"]
    if cursor["stage"] != stage:
        aliases = engine._call_aliases
        engine.commit({"cursor": {"main": {**cursor, "stage": stage}}})
        engine._call_aliases = aliases
        engine._call_targets = [
            {"domain": "work", "id": row["id"], "dependency_hash": row["dependency_hash"]}
            for row in rows
        ]
    return engine.call(stage, payload, schema)


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
    answer = bounded_call(
        engine, "property_alignment", rows, window, payload, schema, base_window, align_properties
    )
    if answer is None:
        return
    for row in rows:
        field = engine.state["fields"][row["input"]["field_id"]]
        proposed = answer.properties[engine.field_alias(field["id"], window)]
        output_ids, seen = list(row.get("output_ids", [])), set()
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
                    confirmed=subject["state"] == "accepted",
                )
                validate_bound_property(subject, field, mapping, component)
                prop = next(p for p in card["properties"] if p["iri"] == mapping.predicate_iri)
                key = identity(
                    "property",
                    subject["id"],
                    mapping.predicate_iri,
                    field["id"],
                    mapping.value_component,
                    component["evidence"],
                )
                assertion = {
                    "id": key,
                    "subject_id": subject["id"],
                    "alignment_class_iri": subject["class_iri"],
                    "predicate_iri": mapping.predicate_iri,
                    "label": prop["label"],
                    "value": component["value"],
                    "source_value": field["value"],
                    "source_unit": component["unit"],
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
        if all_predicates <= set(processed):
            complete_work(changes, updated, outputs=output_ids)
        else:
            changes["work"][row["id"]] = {**updated, "status": "ready", "output_ids": output_ids}
    engine.commit(dict(changes))


def align_relations(engine, rows, base_window):
    changes = defaultdict(dict)
    unresolved = []
    active = engine.state["cursor"]["main"].get("active_batch")
    for row in rows:
        proof = (
            None
            if active
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
            assertion["state"] = acceptance(engine.state, assertion, "accepted")
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
        subject = engine.state["entities"][seed["subject_id"]]
        predicates[seed["predicate_iri"]] = next(
            r
            for r in model_menu(engine.catalog, [subject["class_iri"]])["classes"][0]["relations"]
            if r["iri"] == seed["predicate_iri"]
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
    answer = bounded_call(
        engine, "relation_alignment", rows, window, payload, schema, base_window, align_relations
    )
    if answer is None:
        return
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
        assertion = {
            "id": key,
            "subject_id": seed["subject_id"],
            "predicate_iri": seed["predicate_iri"],
            "alignment_class_iri": engine.state["entities"][seed["subject_id"]]["class_iri"],
            "label": predicates[seed["predicate_iri"]]["label"],
            "state": "candidate",
            "reason": proposed.reason,
            "proposal_reason": proposed.reason,
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
            )
        else:
            assertion["object_id"] = seed["object_ids"][0]
        old = engine.state.get(domain, {}).get(key)
        if old:
            assertion["evidence"] = unique_refs([*old["evidence"], *assertion["evidence"]])
            assertion["verification"] = old.get("verification")
        review_for(engine, changes, domain, assertion)
        complete_work(changes, row, outputs=[key])
    engine.commit(dict(changes))


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
    updated = make_work(row["kind"], data, engine.state, engine.catalog, engine.execution_policy)
    updated.update(expansion_basis_hash=row["expansion_basis_hash"], expansions_used=1)
    changes.setdefault("work", {})[row["id"]] = updated
    return True


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
        "group_interpretation", source_ids=[s["source_id"] for s in window.sources]
    )
    answer = bounded_call(
        engine, "group_interpretation", [row], window, payload, schema, base_window, interpret_group
    )
    if answer.verdict == "supported":
        key = identity(
            "relation_group",
            group["subject_id"],
            group["predicate_iri"],
            sorted(group["object_ids"]),
            answer.participation,
            answer.selection,
            answer.timing,
            group["polarity"],
            group["conditions"],
        )
        existing = engine.state.get("relation_groups", {}).get(key, {})
        updated = {
            **group,
            "id": key,
            "participation": answer.participation,
            "selection": answer.selection,
            "timing": answer.timing,
            "evidence": unique_refs(
                [
                    *group["evidence"],
                    *existing.get("evidence", []),
                    *window.quotes(engine.ir, answer.evidence),
                ]
            ),
            "reason": answer.reason,
            "state": "candidate",
            "verification": None,
        }
        if key != group["id"]:
            changes["relation_groups"][group["id"]] = None
            old_review = identity("work", "evidence_review", ["relation_groups", group["id"]])
            if old_review in engine.state.get("work", {}):
                changes["work"][old_review] = None
            for work_id, work in engine.state.get("work", {}).items():
                if group["id"] in work.get("output_ids", []) and work_id != row["id"]:
                    changes["work"][work_id] = {
                        **work,
                        "output_ids": list(
                            dict.fromkeys(
                                key if oid == group["id"] else oid for oid in work["output_ids"]
                            )
                        ),
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
    engine.commit(dict(changes))


def review_assertions(engine, rows, base_window):
    changes, semantic = defaultdict(dict), []
    active = engine.state["cursor"]["main"].get("active_batch")
    for work in rows:
        domain, key = work["input"]["domain"], work["input"]["assertion_id"]
        row = engine.state.get(domain, {}).get(key)
        if not row:
            complete_work(changes, work, status="waiting", reason="missing_assertion")
            continue
        if active:
            semantic.append(work)
            continue
        gate = precheck_assertion(engine.ir, engine.catalog, gate_state(engine), row)
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
                "state": acceptance(engine.state, row, verification["semantic_verdict"]),
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
        candidates.append(candidate)
        bindings[candidate["id"]] = (work, domain, row, False)
        if domain == "relation_groups" and row["timing"] != "unspecified":
            timing = {
                **candidate,
                "id": f"C{len(candidates) + 1}",
                "kind": "relation_timing",
                "timing": row["timing"],
            }
            candidates.append(timing)
            bindings[timing["id"]] = (work, domain, row, True)
    payload = {"sources": window.payload()["sources"], "candidates": candidates}
    engine._call_aliases = {alias: row["id"] for alias, (_, _, row, _) in bindings.items()}
    schema = stage_schema(
        "evidence_review",
        source_ids=[s["source_id"] for s in window.sources],
        candidate_ids=list(bindings),
        entity_ids=list(engine.entity_aliases(window).values()),
    )
    answer = bounded_call(
        engine, "evidence_review", rows, window, payload, schema, base_window, review_assertions
    )
    if answer is None:
        return
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
        if verdict == "accepted" and (
            judgment.confidence < 0.85
            or not refs
            or not all(references_cover(ref, refs) for ref in required)
        ):
            verdict, reason = "unresolved", "acceptance_requires_source_and_high_confidence"
        updated = changes[domain].get(row["id"], dict(row))
        if timing:
            updated.update(
                timing_state=acceptance(engine.state, row, verdict),
                source_timing_verdict=verdict,
                timing_reason=reason,
                timing_evidence=refs,
            )
        else:
            updated.update(
                state=acceptance(engine.state, row, verdict),
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
    engine.commit(dict(changes))


def drain_work(engine, base_window):
    batch = engine.state["cursor"]["main"].get("active_batch")
    if batch and any(t["domain"] == "work" for t in batch["targets"]):
        rows = [engine.state.get("work", {}).get(target["id"]) for target in batch["targets"]]
        if any(
            row is None or row["dependency_hash"] != target["dependency_hash"]
            for row, target in zip(rows, batch["targets"])
        ):
            cursor = engine.state["cursor"]["main"]
            engine.commit({"cursor": {"main": {**cursor, "active_batch": None}}})
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
            key=lambda row: row["id"],
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
            rows = [
                r
                for r in rows
                if r["kind"] == kind
                and all(
                    r["input"][k] == data[k] for k in ("region_id", "subject_id", "predicate_iri")
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
        elif kind == "coreference_review":
            rows = [r for r in rows if r["kind"] == kind][:6]
        else:
            rows = [first]
    try:
        kind = rows[0]["kind"]
        if kind == "coreference_review":
            from .coreference import review_coreferences

            review_coreferences(engine, work_rows=rows)
        else:
            {
                "property_alignment": align_properties,
                "relation_alignment": align_relations,
                "evidence_review": review_assertions,
                "group_interpretation": interpret_group,
            }[kind](engine, rows, base_window)
    except Exception as exc:
        from app.services.llm.model_runtime import ModelCancelled

        from .controller import Paused

        if isinstance(exc, (Paused, ModelCancelled)):
            raise
        changes = {
            "work": {
                row["id"]: {**row, "status": "failed", "reason_code": "technical_failure"}
                for row in rows
            }
        }
        engine._paid_ready = False
        engine.commit(changes)
        raise
    return True
