"""Pure window-local discovery decoding and order-independent current-state merging."""

from collections import defaultdict
from copy import deepcopy

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


def decode_local_discovery(ir, window, answer, lookup_info, document):
    """Decode against only fixed source and root metadata, never another window's output."""
    state = {
        "entities": {"document": {**deepcopy(document), "field_ids": []}},
        "windows": {window.id: {}},
    }
    changes = defaultdict(dict)
    fields = {f["alias"]: {**deepcopy(f), "alias": "", "source_aliases": []}
              for f in window.fields}
    local = {}
    discovery_valid = True
    for mention in answer.entities:
        evidence = []
        try:
            if mention.local_id in local:
                raise ValueError("duplicate_local_entity_id")
            evidence = window.quotes(ir, mention.evidence)
            name = window.resolve(ir, mention.name) if mention.name else None
            anchor = window.resolve_anchor(ir, mention.anchor)
            if not window.owns(anchor):
                raise ValueError("entity_anchor_outside_primary_range")
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
            existing = changes["entities"].get(key)
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
            changes["entities"][key] = merge_mention(
                existing,
                {
                    **entity,
                    "name_candidates": [name] if name else [],
                    "evidence": evidence,
                    "field_ids": [f["id"] for f in assigned],
                },
                {window.id: [0]},
            )
            for field in assigned:
                changes["fields"][field["id"]] = field
        except (ValueError, KeyError) as exc:
            discovery_valid = False
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
            raise ValueError("document_field_outside_reading_window")
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
            key, item = observation(
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
            key, item = observation(window, hint.label, str(exc), kind="relation")
            changes["observations"][key] = item
    for cue in answer.reference_cues:
        try:
            subject = (
                "document" if cue.local_subject_id == "document" else local[cue.local_subject_id]
            )
            ref = window.resolve(ir, cue.reference)
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
        except (KeyError, ValueError):
            discovery_valid = False
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
    capacity = (
        len(answer.entities) == 12
        or len(answer.relation_hints) == 16
        or len(answer.reference_cues) == 8
        or (len(answer.document_source_fields) + len(answer.unowned_fields)
            + sum(len(mention.source_fields) for mention in answer.entities)) >= 16
        or len(answer.document_source_fields) == 8
        or any(len(mention.source_fields) == 8 for mention in answer.entities)
    )
    return {
        "window_id": window.id,
        "changes": dict(changes),
        "complete": answer.complete and not capacity and discovery_valid,
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
