"""Pure window-local discovery decoding and order-independent current-state merging."""

from collections import Counter, defaultdict
from copy import deepcopy

from .continuation import reading_prefix
from .source import identity, missing
from .work import digest, unique_refs


def merge_mention(old, new, window_order):
    if old is None:
        old = {}
    result = deepcopy(new)
    result["field_ids"] = sorted(set(old.get("field_ids", [])) | set(new.get("field_ids", [])))
    result["evidence"] = unique_refs([*old.get("evidence", []), *new.get("evidence", [])])
    if new["id"] == "document":
        return {**old, "field_ids": result["field_ids"], "evidence": result["evidence"]}
    roles = sorted(set(old.get("role_candidates", [])) | set(new["role_candidates"]))
    result["role_candidates"] = roles
    result["role"] = "；".join(roles)
    result["discovery_class_iris"] = sorted(
        set(old.get("discovery_class_iris", [])) | set(new["discovery_class_iris"])
    )
    names = unique_refs([*old.get("name_candidates", []), *new.get("name_candidates", [])])
    result["name_candidates"] = names
    result["name"] = names[0] if len({q["text"] for q in names}) == 1 else None
    result["label"] = (result["name"] or result["referent"])["text"]
    owners = {w for w in (old.get("window_id"), new.get("window_id")) if w is not None}
    result["window_id"] = min(owners, key=lambda w: (window_order[w], w))
    return result


def observation(window, label, reason, evidence=(), *, kind="validation", **context):
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


def field_observation(window, field, reason):
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


def _attribute_anchor(anchor, name, fields):
    """A label/value observation cannot supply an otherwise absent referent.

    Named objects inside a field (for example an explicit name or identifier)
    remain valid. This check uses located field spans, never domain keywords.
    """
    for field in fields:
        refs = field["evidence"]
        if not field.get("label") or not refs:
            continue
        if any(ref["source_id"] != anchor["source_id"] for ref in refs):
            continue
        start, end = min(ref["start"] for ref in refs), max(ref["end"] for ref in refs)
        if anchor["start"] >= start and anchor["end"] <= end:
            # Keep a separately located non-boolean value as a possible name or
            # identifier; reject the label itself and the complete field wrapper.
            if any(anchor["start"] < ref["end"] and anchor["end"] > ref["start"]
                   for ref in refs if ref not in field.get("value_evidence", [])):
                if name and any(
                    name["source_id"] == ref["source_id"]
                    and ref["start"] <= name["start"] < name["end"] <= ref["end"]
                    for ref in field.get("value_evidence", [])
                ):
                    continue
                return True
    return False


def decode_local_discovery(ir, window, answer, lookup_info, document, *, class_iris=()):
    """Decode against only fixed source and root metadata, never another window's output."""
    state = {
        "entities": {"document": {**deepcopy(document), "field_ids": []}},
        "windows": {window.id: {}},
    }
    changes = defaultdict(dict)
    fields = {f["alias"]: {**deepcopy(f), "alias": "", "source_aliases": []}
              for f in window.fields}
    local = {}
    local_counts = Counter(m.local_id for m in answer.entities)
    deferred = set()
    observation_reasons = {
        "attribute_is_not_entity", "entity_type_outside_reading_cards",
        "entity_type_not_proposed", "missing_or_boolean_value_is_not_entity_name",
        "whole_field_is_not_entity_name",
    }
    failed_sources = set()
    unlocated_failure = False
    primary_sources = {r["evidence_id"] for r in window.primary()}

    def fail(label, exc, quotes=(), refs=(), *, kind="validation", **context):
        """Keep a failed proposal visible and withhold only its attributable source coverage."""
        nonlocal unlocated_failure
        located = []
        for quote in quotes:
            source_id = quote if isinstance(quote, str) else quote.source_id if quote else None
            if source_id and window.citation_source(source_id) is not None:
                located.extend(window.quotes(ir, [source_id]))
        affected = {ref["source_id"] for ref in located} & primary_sources
        if not affected:
            affected = {ref["source_id"] for ref in refs} & primary_sources
        failed_sources.update(affected)
        if not affected:
            unlocated_failure = True
        key, item = observation(window, label, str(exc), unique_refs([*refs, *located]),
                                kind=kind, **context)
        changes["observations"][key] = item

    for mention in answer.entities:
        evidence = []
        try:
            if local_counts[mention.local_id] != 1:
                raise ValueError("duplicate_local_entity_id")
            anchor = window.resolve_anchor(ir, mention.anchor)
            if not window.owns(anchor):
                raise ValueError("entity_anchor_outside_primary_range")
            evidence = [anchor]
            for source_id in mention.evidence:
                try:
                    evidence.extend(window.quotes(ir, [source_id]))
                except ValueError as exc:
                    fail(mention.role, exc, [source_id], [anchor], kind="entity")
            name = None
            if mention.name:
                try:
                    name = window.resolve(ir, mention.name, within=anchor)
                except ValueError as exc:
                    fail("实体名称引用待核对", exc, [mention.name], [anchor], kind="entity")
            if (
                missing((name or anchor)["text"])
                or (name or anchor)["text"].strip().casefold()
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
            assigned = []
            for alias in mention.field_ids:
                if alias in fields:
                    assigned.append(fields[alias])
                else:
                    fail("实体字段引用待核对", "unknown_field_id", refs=[anchor], kind="field")
            boundary_fields = list(fields.values())
            for proposed in mention.source_fields:
                if proposed.label:
                    try:
                        label = window.resolve(ir, proposed.label)
                        value = window.resolve(ir, proposed.value)
                    except ValueError:
                        # The independent field pass records and localizes the error.
                        # An uncertain field boundary must not manufacture an entity.
                        source = window.citation_source(proposed.label.source_id)
                        if (name is None and source
                                and source["evidence_id"] == anchor["source_id"]
                                and (proposed.label.text in anchor["text"]
                                     or anchor["text"] in proposed.label.text)):
                            raise ValueError("entity_boundary_requires_valid_field_quote") from None
                        continue
                    boundary_fields.append({"label": label["text"],
                                            "evidence": [label, value],
                                            "value_evidence": [value]})
            if _attribute_anchor(anchor, name, boundary_fields):
                raise ValueError("attribute_is_not_entity")
            # Also reject a named object that copies its complete source field.
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
            if mention.candidate_class_iri is None:
                raise ValueError("entity_type_not_proposed")
            if mention.candidate_class_iri not in class_iris:
                raise ValueError("entity_type_outside_reading_cards")
            key = identity("mention", ir.document_hash, anchor["source_id"],
                           anchor["start"], anchor["end"])
            existing = changes["entities"].get(key)
            # A physical mention survives role/type paraphrases and rereading.
            # Separate source positions still require proven coreference.
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
            changes["entities"][key] = merge_mention(
                existing,
                {
                    **entity,
                    "role_candidates": [mention.role],
                    "discovery_class_iris": [mention.candidate_class_iri],
                    "name_candidates": [name] if name else [],
                    "evidence": evidence,
                    "field_ids": [f["id"] for f in assigned],
                },
                {window.id: [0]},
            )
            local[mention.local_id] = key
            for field in assigned:
                changes["fields"][field["id"]] = field
        except (ValueError, KeyError) as exc:
            if str(exc) in observation_reasons:
                deferred.add(mention.local_id)
            else:
                fail(mention.role, exc, [mention.anchor], evidence, kind="entity")
                continue
            key, item = observation(
                window,
                mention.role,
                str(exc),
                evidence,
                kind="entity",
            )
            changes["observations"][key] = item
    for field in fields.values():
        if not any(window.owns(ref) for ref in field.get("value_evidence", field["evidence"])):
            continue
        changes["fields"][field["id"]] = field
        key, item = field_observation(
            window,
            field,
            "missing_source_value" if field["missing"] else "原字段观察；采信状态见对应属性候选",
        )
        changes["observations"][key] = item
    root = deepcopy(state["entities"]["document"])
    for alias in answer.document_field_ids:
        if alias not in fields:
            fail("文档字段引用待核对", "document_field_outside_reading_window", kind="field")
            continue
        if not any(window.owns(ref) for ref in fields[alias].get(
            "value_evidence", fields[alias]["evidence"],
        )):
            continue
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
            label = window.resolve(ir, proposed.label) if proposed.label else None
            value = window.resolve(ir, proposed.value)
            if (owner is None or owner.get("id") == "document") and not window.owns(value):
                continue
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
            obs_id, item = field_observation(
                window,
                field,
                "missing_source_value" if field["missing"] else reason,
            )
            changes["observations"][obs_id] = item
        except (ValueError, KeyError) as exc:
            fail(
                proposed.label.text if proposed.label else "独立原文",
                exc, [proposed.label, proposed.value],
                [owner["referent"]] if owner and owner.get("referent") else [],
                kind="field",
            )
    for hint in answer.relation_hints:
        try:
            if hint.subject_id in deferred or hint.object_id in deferred:
                key, item = observation(
                    window, hint.label, "关系端点仍为原文观察，尚未进入实体候选",
                    window.quotes(ir, hint.evidence), kind="relation",
                    polarity=hint.polarity, conditions=hint.conditions,
                )
                changes["observations"][key] = item
                continue
            subject = "document" if hint.subject_id == "document" else local[hint.subject_id]
            obj = local[hint.object_id]
            evidence = window.quotes(ir, hint.evidence)
            if subject == obj:
                raise ValueError("self_relation_hint_requires_explicit_review")
            key = identity(
                "hint", subject, hint.label, obj, evidence, hint.polarity, hint.conditions
            )
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
            obs_id, item = observation(
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
            fail(hint.label, exc, hint.evidence, kind="relation",
                 polarity=hint.polarity, conditions=hint.conditions)
    for cue in answer.reference_cues:
        try:
            ref = window.resolve(ir, cue.reference)
            if cue.local_subject_id in deferred:
                key, item = observation(
                    window, ref["text"], "引用主体仍为原文观察，尚未进入实体候选",
                    [ref, *window.quotes(ir, cue.evidence)], kind="entity",
                    polarity=cue.polarity, conditions=cue.conditions, direction=cue.direction,
                )
                changes["observations"][key] = item
                continue
            subject = (
                "document" if cue.local_subject_id == "document" else local[cue.local_subject_id]
            )
            refs = window.quotes(ir, cue.evidence)
            key = identity(
                "reference_cue",
                subject,
                ref,
                cue.direction,
                cue.relation_label,
                cue.polarity,
                cue.conditions,
            )
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
        except (KeyError, ValueError) as exc:
            fail("原文回指引用待核对", exc, [cue.reference, *cue.evidence], kind="relation",
                 polarity=cue.polarity, conditions=cue.conditions, direction=cue.direction)
    changes["window_entities"][window.id] = {"ids": list(dict.fromkeys(local.values()))}
    if lookup_info:
        current = dict(state["windows"][window.id])
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
            key, item = observation(
                window,
                "来源候选（身份未核对）",
                "、".join(c["label"] for c in candidates) + "；" + suggestion["reason"],
                changes["entities"][entity_id]["evidence"],
                kind="entity",
                candidate_subject_ids=[entity_id],
            )
            changes["observations"][key] = item
        for issue in lookup_info.get("issues", []):
            key, item = observation(window, "来源查询未完成", issue)
            changes["observations"][key] = item
    completed = window.primary() if answer.complete else []
    if not answer.complete and answer.read_through_source_id is not None:
        try:
            completed = reading_prefix(window, answer.read_through_source_id)
        except ValueError as exc:
            key, item = observation(window, "阅读进度无效", str(exc), kind="scope")
            changes["observations"][key] = item
    completed = ([] if unlocated_failure else
                 [ref for ref in completed if ref["evidence_id"] not in failed_sources])
    complete = answer.complete and not failed_sources and not unlocated_failure
    return {
        "window_id": window.id,
        "changes": dict(changes),
        "complete": complete,
        "completed_ranges": [] if complete else completed,
    }


def merge_local_discovery(state, delta, window_order):
    """Merge a decoded answer into current rows; never replace a captured graph snapshot."""
    changes = deepcopy(delta["changes"])
    for key, new in list(changes.get("entities", {}).items()):
        row = merge_mention(state.get("entities", {}).get(key), new, window_order)
        changes["entities"][key] = row
        if len({q["text"] for q in row.get("name_candidates", [])}) > 1:
            oid = identity("mention_name_conflict", key)
            changes.setdefault("observations", {})[oid] = {
                "id": oid,
                "label": "实体名称提议不一致",
                "reason": "保留原文名称候选，待实体核验",
                "reason_code": "conflicting_mention_names",
                "kind": "entity",
                "field_id": None,
                "candidate_subject_ids": [key],
                "evidence": row["name_candidates"],
            }
    for key, row in changes.get("window_entities", {}).items():
        row["ids"] = sorted(
            set(row["ids"]) | set(state.get("window_entities", {}).get(key, {}).get("ids", []))
        )
    for key, row in changes.get("windows", {}).items():
        changes["windows"][key] = {**state.get("windows", {}).get(key, {}), **row}
    for domain in ("hints", "reference_cues", "observations", "source_candidates"):
        for key, row in changes.get(domain, {}).items():
            old = state.get(domain, {}).get(key)
            if old is None:
                continue
            # Scalar choices must not depend on a previously accumulated evidence set.
            merged = {name: deepcopy(min([value[name] for value in (old, row) if name in value],
                                         key=digest)) for name in old.keys() | row.keys()}
            for field in ("evidence", "candidate_subject_ids", "candidates"):
                if field in old or field in row:
                    values = {digest(v): v for v in [*old.get(field, []), *row.get(field, [])]}
                    merged[field] = [values[k] for k in sorted(values)]
            if "window_id" in old and "window_id" in row:
                merged["window_id"] = min(
                    (old["window_id"], row["window_id"]), key=lambda w: (window_order[w], w)
                )
            changes[domain][key] = merged
    return changes
