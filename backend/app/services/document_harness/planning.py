"""Bounded relation work and source-complete cross-window reading contexts."""

from __future__ import annotations

from collections.abc import Iterable
from copy import deepcopy

from .ontology import SchemaCatalog, legal_relation
from .source import Window, reference

MAX_OBJECTS_PER_JOB = 4
FULL_SOURCE_LIMIT = 1800


def alignment_jobs(
    catalog: SchemaCatalog, all_entities: dict[str, dict], current_entity_ids: list[str],
) -> list[tuple[str, list[str]]]:
    """Visit each new legal pair without requiring either endpoint's acceptance.

    Current subjects can see historical objects, and historical subjects can
    see current objects. The document root sees only current objects because
    its earlier pairs have already been considered. IDs are never name-based.
    """
    current = set(current_entity_ids)
    typed = {
        key: value for key, value in all_entities.items()
        if value.get("class_iri") in catalog.reachable_class_iris
        and not value.get("referent_unresolved")
    }
    jobs = []
    for subject_id in sorted(typed):
        subject = typed[subject_id]
        is_root = subject.get("role") == "document_root"
        eligible_ids = set(typed) if subject_id in current and not is_root else current
        objects = []
        for object_id in sorted(eligible_ids & typed.keys() - {subject_id}):
            obj = typed[object_id]
            if any(
                legal_relation(catalog, subject["class_iri"], relation.iri, obj["class_iri"])
                for relation in catalog.classes[subject["class_iri"]].relations
            ):
                objects.append(object_id)
        groups = {}
        for object_id in objects:
            group_id = (typed[object_id].get("identity_binding") or {}).get("group_id", object_id)
            groups.setdefault(group_id, []).append(object_id)
        batch = []
        for members in groups.values():
            if batch and len(batch) + len(members) > MAX_OBJECTS_PER_JOB:
                jobs.append((subject_id, batch))
                batch = []
            batch.extend(members)
        if batch:
            jobs.append((subject_id, batch))
        if not objects and subject_id in current and subject.get("field_ids"):
            jobs.append((subject_id, []))
    return jobs


def _contains(source: dict, ref: dict) -> bool:
    return (
        source["evidence_id"] == ref["source_id"]
        and source["offset"] <= ref["start"]
        and ref["end"] <= source["offset"] + len(source["text"])
    )


def context_window(
    ir, base_window: Window, entities: Iterable[dict], fields_by_id: dict[str, dict],
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
        start, end = (0, len(unit.text)) if len(unit.text) <= FULL_SOURCE_LIMIT else (
            ref["start"], ref["end"],
        )
        sources.append({
            "source_id": "", "evidence_id": unit.evidence_id, "offset": start,
            "text": unit.text[start:end], "section": unit.section_node_id,
            "kind": unit.kind, "table": unit.table_path,
            "row": unit.row_index, "column": unit.column_index,
        })
    for index, source in enumerate(sources, 1):
        source["source_id"] = f"S{index}"
    for index, field in enumerate(fields.values(), 1):
        field["alias"] = f"F{index}"
        field["source_aliases"] = [
            source["source_id"] for source in sources
            if any(_contains(source, ref) for ref in field["evidence"])
        ]
    result = Window(
        id=base_window.id, sources=sources, fields=list(fields.values()),
        primary_ids=list(base_window.primary_ids),
    )
    result.entity_ids = [
        entity["id"] for entity in selected if entity.get("role") != "document_root"
    ]
    return result
