"""Bounded relation work and source-complete cross-window reading contexts."""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable
from copy import deepcopy

from .ontology import legal_relation
from .source import Window, identity, reference, references_cover
from .work import digest, make_work, ref_key, unique_refs, work_id

FULL_SOURCE_LIMIT = 1800


def alignment_jobs(state, ready_work, policy):
    """Stable, bounded batches of actual work; never enumerate entity pairs."""
    groups = {}
    for row in sorted(ready_work, key=lambda r: r["id"]):
        data = row["input"]
        key = (
            row["kind"],
            data.get("region_id"),
            data.get("subject_id"),
            data.get("predicate_iri"),
        )
        groups.setdefault(key, []).append(row)
    for rows in groups.values():
        limit = policy["relation_items_per_call"] if rows[0]["kind"] == "relation_alignment" else 32
        for start in range(0, len(rows), limit):
            yield rows[start : start + limit]


def lookup_text(text):
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def label_matches(text, label):
    text, label = lookup_text(text), lookup_text(label)
    if not label:
        return False
    if re.fullmatch(r"[a-z0-9 _-]+", label):
        return re.search(r"(?<![a-z0-9])" + re.escape(label) + r"(?![a-z0-9])", text) is not None
    return label in text


def _clue_positions(refs, labels):
    return [(ref["source_id"], ref["start"] + match.start())
            for ref in refs for label in labels if label
            for match in re.finditer(re.escape(label), ref["text"], re.IGNORECASE)]


def _clue_distance(entity, positions):
    ref = entity.get("referent") or {}
    return min((abs(ref["start"] - offset) for source, offset in positions
                if source == ref.get("source_id")), default=float("inf"))


class SourceIndex:
    def __init__(self, ir, state):
        self.ir = ir
        self.by_source = defaultdict(set)
        self.by_record = defaultdict(set)
        self.by_text = defaultdict(set)
        self.by_class = defaultdict(set)
        self.by_field = defaultdict(set)
        self.by_group = defaultdict(set)
        self.source_order = {unit.evidence_id: i for i, unit in enumerate(ir.evidence_units)}
        self.rows = {}
        self.scope_changes = {}
        self.cue_changes = {}
        self.update(state.get("entities", {}))

    def record(self, source_id):
        unit = self.ir.unit(source_id)
        return (tuple(unit.table_path), unit.row_index) if unit.table_path else unit.block_id

    def region(self, refs):
        return identity(
            "region",
            self.ir.original_document_hash,
            sorted({str(self.record(ref_key(ref)[0])) for ref in refs}),
        )

    def update(self, changes):
        for key, row in changes.items():
            old = self.rows.pop(key, None)
            for current, add in ((old, False), (row, True)):
                if not current or current.get("refined_member_ids"):
                    continue
                values = [(self.by_field, field) for field in current.get("field_ids", [])]
                group = (current.get("identity_binding") or {}).get("group_id")
                if group:
                    values.append((self.by_group, group))
                if current.get("referent"):
                    source = current["referent"]["source_id"]
                    values.extend([
                        (self.by_source, source), (self.by_record, self.record(source)),
                        (self.by_class, current.get("class_iri")),
                    ])
                names = []
                if current.get("referent"):
                    names = [current.get("label", ""), current["referent"]["text"]]
                    names.extend(q["text"] for q in current.get("name_candidates", []))
                    names.extend(item.get("value", "") for item in
                                 (current.get("identity_binding") or {}).get("identifiers", []))
                values.extend((self.by_text, lookup_text(name)) for name in names if name)
                for index, value in values:
                    if add:
                        index[value].add(key)
                    else:
                        index[value].discard(key)
            if row:
                self.rows[key] = row

    def position(self, entity):
        ref = entity.get("referent") or {}
        return self.source_order.get(ref.get("source_id"), -1), ref.get("start", 0), entity["id"]

    def members(self, entity_id):
        group = (self.rows[entity_id].get("identity_binding") or {}).get("group_id")
        return sorted(self.by_group[group]) if group else [entity_id]


def build_source_index(ir, state):
    return SourceIndex(ir, state)


def resolve_predicates(catalog, subject, objects, clue):
    card = catalog.classes.get(subject.get("class_iri"))
    if not objects:
        return {"legal_iris": [], "unresolved_iris": [], "incompatibility_confirmed": False}
    # With no subject type, only an explicit predicate name can identify work.
    available = (card.relations if card else tuple({
        rel.iri: rel for cls in catalog.classes.values() for rel in cls.relations
    }.values()))
    matching = [
        r
        for r in available
        if any(label_matches(clue, label) for label in (r.label, *r.aliases))
    ]
    missing_type = card is None or any(not obj.get("class_iri") for obj in objects)
    menu = matching if missing_type else matching or list(available)
    confirmed = all(e.get("state") == "accepted" for e in [subject, *objects])
    legal, unresolved = [], []
    for rel in menu:
        if missing_type or rel.constraint_status != "resolved":
            unresolved.append(rel.iri)
        elif all(
            legal_relation(catalog, subject["class_iri"], rel.iri, obj["class_iri"])
            for obj in objects
        ):
            legal.append(rel.iri)
        elif not confirmed:
            unresolved.append(rel.iri)
    return {
        "legal_iris": legal,
        "unresolved_iris": unresolved,
        "incompatibility_confirmed": not legal
        and not unresolved
        and not missing_type and confirmed,
    }


def resolve_reference_cues(ir, state, index, policy):
    """Resolve saved original-text cues against the full mention index, without graph work."""
    entities = state.get("entities", {})
    changes = {}
    for cue in state.get("reference_cues", {}).values():
        if cue.get("subject_id") not in entities or cue.get("ambiguous_subject_members"):
            changes[cue["id"]] = {**cue, "target_ids": [], "reference_targets_truncated": False}
            continue
        text = lookup_text(cue["reference"]["text"])
        targets = set(index.by_text.get(text, ()))
        # Exact identifiers/names may appear within a longer explicit reference.
        if not targets:
            for name, keys in index.by_text.items():
                if name and label_matches(text, name):
                    targets.update(keys)
        targets.discard(cue["subject_id"])
        if not targets and cue["kind"] == "anaphora":
            unit = ir.unit(cue["reference"]["source_id"])
            previous = [
                u
                for u in ir.evidence_units
                if u.section_node_id == unit.section_node_id
                and u.text.strip()
                and u.kind != "heading"
                and ir.evidence_units.index(u) < ir.evidence_units.index(unit)
            ]
            if previous:
                targets.update(index.by_source.get(previous[-1].evidence_id, ()))
        ordered = sorted(targets, key=lambda key: ref_key(entities[key]["referent"]))
        limited = len(ordered) > policy["reference_targets_per_cue"]
        ordered = ordered[: policy["reference_targets_per_cue"]]
        changes[cue["id"]] = {
            **cue,
            "target_ids": ordered,
            "reference_targets_truncated": limited,
        }
    return changes


def collect_relation_seeds(ir, catalog, state, delta, index, policy, *, include_references=True):
    """Only source-supported neighborhoods are enumerated, with early weak limits."""
    entities = state.get("entities", {})
    seeds = {}
    weak_by_bucket, weak_by_region = defaultdict(int), defaultdict(int)
    weak_regions, limited_regions = {}, {}
    index.scope_changes, index.cue_changes = {}, {}

    def add(
        subject_id,
        object_ids,
        clue,
        refs,
        *,
        origin,
        protected,
        polarity="uncertain",
        conditions=(),
        predicate=None,
    ):
        if subject_id not in entities or any(key not in entities for key in object_ids):
            return
        if subject_id in object_ids or not refs:
            return
        objects = [entities[key] for key in object_ids]
        resolution = resolve_predicates(catalog, entities[subject_id], objects, clue)
        iris = resolution["legal_iris"] + resolution["unresolved_iris"]
        if predicate:
            iris = [iri for iri in iris if iri == predicate]
            if not iris and any(
                not item.get("class_iri") for item in [entities[subject_id], *objects]
            ):
                if any(rel.iri == predicate for card in catalog.classes.values()
                       for rel in card.relations):
                    iris = [predicate]
        for iri in iris:
            seed = {
                "subject_id": subject_id,
                "object_ids": sorted(set(object_ids)),
                "predicate_iri": iri,
                "clue_refs": unique_refs(refs),
                "required_context_refs": [],
                "origin_ids": [origin],
                "priority": "explicit" if protected else "structural",
                "region_id": index.region(refs),
                "polarity_hint": polarity,
                "condition_hints": list(conditions),
                "endpoint_hypothesis": any(
                    e.get("state") != "accepted" for e in [entities[subject_id], *objects]
                ),
            }
            key = work_id("relation_alignment", seed)
            bucket = (subject_id, iri, seed["region_id"])
            if not protected:
                weak_regions[seed["region_id"]] = refs
            if not protected and key not in seeds:
                if (weak_by_bucket[bucket]
                        >= 2 * policy["weak_candidates_per_subject_predicate_region"]
                        or weak_by_region[seed["region_id"]]
                        >= policy["weak_candidates_per_region"]):
                    limited_regions[seed["region_id"]] = refs
                    continue
                weak_by_bucket[bucket] += 1
                weak_by_region[seed["region_id"]] += 1
            if key in seeds:
                seed["clue_refs"] = unique_refs([*seeds[key]["clue_refs"], *refs])
                seed["origin_ids"] = sorted(set([*seeds[key]["origin_ids"], origin]))
                if seeds[key]["priority"] == "explicit":
                    seed["priority"] = "explicit"
                    if not protected:
                        seed["polarity_hint"] = seeds[key]["polarity_hint"]
                        seed["condition_hints"] = seeds[key]["condition_hints"]
            seeds[key] = seed

    changed = set(delta.get("entities", {}))
    for hint in state.get("hints", {}).values():
        ids = hint.get("object_ids", [hint.get("object_id")])
        if hint["id"] not in delta.get("hints", {}) and not changed.intersection(
            [hint.get("subject_id"), *ids]
        ):
            continue
        if hint.get("ambiguous_subject_members"):
            continue
        add(
            hint["subject_id"],
            ids,
            hint["label"],
            hint["evidence"],
            origin=hint["id"],
            protected=True,
            polarity=hint.get("polarity", "uncertain"),
            conditions=hint.get("conditions", []),
        )

    index.cue_changes = (resolve_reference_cues(ir, state, index, policy)
                         if include_references else {})
    for cue in index.cue_changes.values():
        ordered = cue["target_ids"]
        if cue.get("relation_label") is None:
            continue
        for key in ordered:
            subject, obj = (
                (cue["subject_id"], key)
                if cue["direction"] == "outgoing"
                else (
                    key,
                    cue["subject_id"],
                )
            )
            add(
                subject,
                [obj],
                cue["relation_label"],
                cue["evidence"],
                origin=cue["id"],
                protected=True,
                polarity=cue["polarity"],
                conditions=cue["conditions"],
            )

    records = {
        index.record(e["referent"]["source_id"])
        for key in changed
        if (e := entities.get(key)) and e.get("referent")
    }
    records.update(
        index.record(ref["source_id"])
        for field in delta.get("fields", {}).values()
        if field
        for ref in field.get("evidence", [])
    )
    units_by_record = defaultdict(list)
    for unit in ir.evidence_units:
        record = index.record(unit.evidence_id)
        if record in records:
            units_by_record[record].append(unit)

    # The user-selected document root has no physical mention and therefore
    # cannot occur in by_record. Its legal direct targets still need explicit
    # source review, even when the text does not repeat the report title or verb.
    # These are bounded search candidates, never inferred positive relations.
    root = entities.get("document")
    if root and root.get("role") == "document_root":
        card = catalog.classes.get(root.get("class_iri"))
        for object_id in sorted(changed, key=lambda key: index.position(entities[key])
                                if entities.get(key) else (-1, 0, key)):
            obj = entities.get(object_id)
            if (not obj or not obj.get("referent") or obj.get("refined_member_ids")
                    or obj.get("state") in {"rejected", "invalid"}):
                continue
            unit = ir.unit(obj["referent"]["source_id"])
            if unit.kind == "heading" or unit.navigation_role:
                continue
            units = units_by_record.get(index.record(unit.evidence_id), [unit])
            refs = [reference(ir, u.evidence_id, 0, len(u.text)) for u in units
                    if u.text.strip() and not u.navigation_role]
            for rel in card.relations if card else ():
                if not legal_relation(catalog, card.iri, rel.iri, obj.get("class_iri")):
                    continue
                if any(seed["subject_id"] == root["id"]
                       and seed["predicate_iri"] == rel.iri
                       and object_id in seed["object_ids"]
                       and seed["priority"] == "explicit" for seed in seeds.values()):
                    # An explicit group/condition is not a license to propose
                    # independent unconditional edges for each of its members.
                    continue
                add(root["id"], index.members(object_id), rel.label, refs,
                    origin=identity("document_target", object_id, rel.iri),
                    protected=False, predicate=rel.iri)

    # An approved mapping identifies a pair of physical cells. It remains a
    # candidate until evidence_gate proves the frozen contract or reviews it.
    if policy.get("table_relation_rules"):
        from .evidence_gate import _table_cells, normalize_table_rules

        for rule in normalize_table_rules(policy["table_relation_rules"]):
            if rule["document_root_class_iri"] != catalog.root_class_iri:
                continue
            cells = _table_cells(ir, rule)
            if cells is None:
                continue
            for row_number in sorted({row for row, _ in cells if row >= rule["header_rows"]}):
                left = cells.get((row_number, rule["subject_column"]))
                right = cells.get((row_number, rule["object_column"]))
                if left is None or right is None or index.record(left.evidence_id) not in records:
                    continue
                subject_ids = index.by_source[left.evidence_id]
                object_ids = index.by_source[right.evidence_id]
                if len(subject_ids) != 1 or len(object_ids) != 1:
                    continue
                subject_id, object_id = next(iter(subject_ids)), next(iter(object_ids))
                if any(entities[key].get("class_iri") not in (None, expected) for key, expected in (
                    (subject_id, rule["subject_class_iri"]), (object_id, rule["object_class_iri"]),
                )):
                    continue
                refs = [reference(ir, unit.evidence_id, 0, len(unit.text)) for unit in (
                    left, right, *(cells[(h["row"], h["column"])] for h in rule["header_cells"]),
                ) if unit.text.strip()]
                add(subject_id, index.members(object_id), rule["predicate_iri"], refs,
                    origin=identity("table_rule", rule["rule_id"], rule["version"], row_number),
                    protected=True, polarity="positive", predicate=rule["predicate_iri"])

    # A column heading/field label authorizes a search inside its actual value,
    # using saved ownership. Other cells in the same row do not become objects.
    field_ids = set(delta.get("fields", {}))
    field_ids.update(field for key in changed if key in entities
                     for field in entities[key].get("field_ids", []))
    for field_id in sorted(field_ids):
        field = state.get("fields", {}).get(field_id)
        if not field or field.get("missing") or not field.get("label"):
            continue
        refs = field.get("evidence", [])
        value_refs = field.get("value_evidence", [])
        if not refs or not value_refs:
            continue
        objects = {key for ref in value_refs for key in index.by_source[ref["source_id"]]
                   if references_cover(entities[key]["referent"], value_refs)}
        for subject_id in sorted(
            index.by_field[field_id], key=lambda key: index.position(entities[key]),
        ):
            card = catalog.classes.get(entities[subject_id].get("class_iri"))
            if card is None:
                continue
            matching = [rel for rel in card.relations if any(
                label_matches(field["label"], name) for name in (rel.label, *rel.aliases)
            )]
            for rel in matching:
                for object_id in sorted(objects, key=lambda key: index.position(entities[key])):
                    add(subject_id, index.members(object_id), field["label"], refs,
                        origin=field_id, protected=False, predicate=rel.iri)
                    if index.region(refs) in limited_regions:
                        break
                if index.region(refs) in limited_regions:
                    break
            if index.region(refs) in limited_regions:
                break

    for record in sorted(records, key=str):
        ids = sorted(index.by_record[record], key=lambda key: index.position(entities[key]))
        units = units_by_record[record]
        refs = [reference(ir, u.evidence_id, 0, len(u.text)) for u in units if u.text.strip()]
        if not refs:
            continue
        region, emitted, truncated = index.region(refs), 0, False
        if any(unit.table_path for unit in units):
            # Table candidates need mapped field ownership or a frozen rule.
            continue
        for subject_id in ids:
            card = catalog.classes.get(entities[subject_id].get("class_iri"))
            if not card:
                continue
            for rel in sorted(card.relations, key=lambda r: r.iri):
                if not any(
                    label_matches(u.text, label)
                    for u in units
                    for label in (rel.label, *rel.aliases)
                ):
                    continue
                positions = _clue_positions(refs, (rel.label, *rel.aliases))
                objects = (
                    key
                    for key in sorted(ids, key=lambda key: (
                        _clue_distance(entities[key], positions), index.position(entities[key]),
                    ))
                    if key != subject_id
                    and legal_relation(
                        catalog,
                        card.iri,
                        rel.iri,
                        entities[key].get("class_iri"),
                    )
                )
                seen_groups = set()
                count = 0
                for key in objects:
                    if count >= 2 * policy["weak_candidates_per_subject_predicate_region"]:
                        truncated = True
                        break
                    if emitted >= policy["weak_candidates_per_region"]:
                        truncated = True
                        break
                    group = entities[key].get("identity_binding", {}).get("group_id")
                    if group and group in seen_groups:
                        continue
                    members = index.members(key)
                    seen_groups.add(group)
                    add(
                        subject_id,
                        members,
                        rel.label,
                        refs,
                        origin=region,
                        protected=False,
                        predicate=rel.iri,
                    )
                    count += 1
                    emitted += 1
                if emitted >= policy["weak_candidates_per_region"]:
                    break
            if emitted >= policy["weak_candidates_per_region"]:
                break
        oid = identity("candidate_scope", region)
        index.scope_changes[oid] = {
            "id": oid,
            "kind": "scope",
            "label": "候选范围",
            "reason": "弱候选范围受限" if truncated else "原文线索范围已规划",
            "region_id": region,
            "weak_pool_truncated": truncated,
            "evidence": refs,
            "window_id": None,
        }
    for region, refs in weak_regions.items():
        oid = identity("candidate_scope", region)
        if oid in index.scope_changes and region not in limited_regions:
            continue
        truncated = region in limited_regions
        index.scope_changes[oid] = {
            "id": oid, "kind": "scope", "label": "候选范围", "region_id": region,
            "reason": "弱候选范围受限" if truncated else "原文线索范围已规划",
            "weak_pool_truncated": truncated,
            "evidence": unique_refs(refs), "window_id": None,
        }
    return list(seeds.values())


def admit_relation_work(seeds, state, catalog, policy, rank_pairs=None):
    groups, result = defaultdict(list), []
    for seed in seeds:
        row = make_work("relation_alignment", seed, state, catalog, policy)
        card = catalog.classes.get(state["entities"][seed["subject_id"]].get("class_iri"))
        relation = next((r for r in card.relations if r.iri == seed["predicate_iri"]), None
                        ) if card else None
        objects = [state["entities"][key] for key in seed["object_ids"]]
        legal = relation and relation.constraint_status == "resolved" and all(
            obj.get("class_iri") and legal_relation(
                catalog, card.iri, relation.iri, obj["class_iri"],
            ) for obj in objects
        )
        if not legal and seed["priority"] != "explicit":
            confirmed_incompatible = (card is not None and relation is not None
                and relation.constraint_status == "resolved"
                and all(obj.get("class_iri") for obj in objects)
                and all(item.get("state") == "accepted"
                        for item in [state["entities"][seed["subject_id"]], *objects]))
            row.update(status="pruned" if confirmed_incompatible else "waiting",
                       reason_code="ontology_incompatible" if confirmed_incompatible
                       else "type_or_constraint_unresolved")
        if (
            seed["priority"] == "explicit"
            or seed["polarity_hint"] == "negative"
            or seed["condition_hints"]
        ):
            if row["status"] == "pruned" and row["reason_code"] == "weak_quota":
                row.update(status="ready", reason_code=None)
            row["input"].update(priority=seed["priority"], origin_ids=seed["origin_ids"])
            result.append(row)
        else:
            groups[(seed["subject_id"], seed["predicate_iri"], seed["region_id"])].append(row)
    for rows in groups.values():
        first = rows[0]["input"]
        card = catalog.classes.get(state["entities"][first["subject_id"]].get("class_iri"))
        predicate = next((r for r in card.relations if r.iri == first["predicate_iri"]), None
                         ) if card else None
        positions = _clue_positions(first["clue_refs"],
                                    (predicate.label, *predicate.aliases) if predicate else ())

        def order(row):
            data = row["input"]
            physical = min((
                _clue_distance(state["entities"][key], positions),
                ref_key(state["entities"][key]["referent"]), key,
            ) for key in data["object_ids"])
            direct_value = any(key in state.get("fields", {}) for key in data["origin_ids"])
            return not direct_value, *physical, row["id"]

        rows.sort(key=order)
        selection_hash = digest(sorted(
            (row["id"], row["expansion_basis_hash"]) for row in rows
        ))
        if all(row.get("selection_hash") == selection_hash for row in rows):
            result.extend(rows)
            continue
        eligible = [row for row in rows if row["status"] not in {"done", "waiting", "failed"}
                    and row["reason_code"] != "ontology_incompatible"]
        quota = policy["weak_candidates_per_subject_predicate_region"]
        if rank_pairs and len(eligible) > quota:
            pairs = []
            for row in eligible:
                data = row["input"]
                subject = state["entities"][data["subject_id"]]
                predicate = next(
                    r
                    for r in catalog.classes[subject["class_iri"]].relations
                    if r.iri == data["predicate_iri"]
                )
                pairs.append(
                    (
                        subject["label"]
                        + " "
                        + predicate.label
                        + " "
                        + predicate.description
                        + " 哪些原文表达该关系或它的否定、条件",
                        "\n".join(ref["text"] for ref in data["clue_refs"])
                        + " "
                        + " ".join(state["entities"][key]["label"] for key in data["object_ids"]),
                    )
                )
            scores = rank_pairs(pairs)
            if len(scores) != len(eligible):
                raise ValueError("relation_ranking_result_set_mismatch")
            eligible = [row for _, row in sorted(
                zip(scores, eligible), key=lambda p: (-p[0], order(p[1])),
            )]
        for index, row in enumerate(eligible):
            row.update(
                status="ready" if index < quota else "pruned",
                reason_code=None if index < quota else "weak_quota",
            )
        for row in rows:
            row["selection_hash"] = selection_hash
            result.append(row)
    return result


def _contains(source: dict, ref: dict) -> bool:
    return (
        source["evidence_id"] == ref["source_id"]
        and source["offset"] <= ref["start"]
        and ref["end"] <= source["offset"] + len(source["text"])
    )


def context_window(
    ir,
    base_window: Window,
    entities: Iterable[dict],
    fields_by_id: dict[str, dict],
    extra_refs: Iterable[dict] = (),
) -> Window:
    """Include the actual sources of every exposed entity and original field.

    This changes only the reading context. It does not merge mentions, create
    new fields, truncate values, or enlarge the primary document coverage.
    Long source units contribute exact cited spans rather than whole units.
    A cited span itself is never truncated to make a budget appear satisfied.
    """
    selected = sorted(entities, key=lambda entity: entity["id"])
    sources = deepcopy(base_window.sources)
    fields = {field["id"]: deepcopy(field) for field in base_window.fields}
    refs = list(extra_refs)
    for entity in selected:
        if entity.get("referent"):
            refs.append(entity["referent"])
        refs.extend(entity.get("evidence", ()))
        refs.extend(entity.get("type_evidence", ()))
        for field_id in entity.get("field_ids", ()):
            if field_id not in fields_by_id:
                raise ValueError("context_field_not_saved")
            fields.setdefault(field_id, deepcopy(fields_by_id[field_id]))
    for field in fields.values():
        refs.extend(field["evidence"])
        refs.extend(field.get("value_evidence", ()))
    for ref in refs:
        actual = reference(ir, ref["source_id"], ref["start"], ref["end"])
        if actual["text"] != ref["text"]:
            raise ValueError("context_reference_mismatch")
        if any(_contains(source, ref) for source in sources):
            continue
        unit = ir.unit(ref["source_id"])
        start, end = (
            (0, len(unit.text))
            if len(unit.text) <= FULL_SOURCE_LIMIT
            else (
                ref["start"],
                ref["end"],
            )
        )
        sources.append(
            {
                "source_id": "",
                "evidence_id": unit.evidence_id,
                "offset": start,
                "text": unit.text[start:end],
                "section": unit.section_node_id,
                "kind": unit.kind,
                "table": unit.table_path,
                "row": unit.row_index,
                "column": unit.column_index,
            }
        )
    # Dependency and entity traversal order is not document order. In particular,
    # a cited operation must not appear before its original step heading.
    source_order = {unit.evidence_id: index for index, unit in enumerate(ir.evidence_units)}
    sources.sort(key=lambda source: (source_order[source["evidence_id"]], source["offset"]))
    for index, source in enumerate(sources, 1):
        source["source_id"] = f"S{index}"
    for index, field in enumerate(fields.values(), 1):
        field["alias"] = f"F{index}"
        field["source_aliases"] = [
            source["source_id"]
            for source in sources
            if any(_contains(source, ref) for ref in field["evidence"])
        ]
    result = Window(
        id=base_window.id,
        sources=sources,
        fields=list(fields.values()),
        primary_ids=list(base_window.primary_ids),
    )
    result.entity_ids = [
        entity["id"] for entity in selected if entity.get("role") != "document_root"
    ]
    return result
