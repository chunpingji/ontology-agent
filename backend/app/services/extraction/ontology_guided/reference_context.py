"""Bounded candidate selection from accepted entities in the current document.

Names and proximity only select sources to authorize for independent verification.
They never establish identity or grant permission to discover additional facts.
"""

from __future__ import annotations

import re
import unicodedata


def normalized_reference_name(text: str) -> str:
    return "".join(unicodedata.normalize("NFKC", text).split()).casefold()


def select_reference_entities(task, index, nodes, origins, resolutions, classes, *, limit=8):
    record = index.by_id[task.record_id]
    text = normalized_reference_name(record.text)
    units = {unit.evidence_id for unit in record.source_units}
    has_anaphora = bool(re.search(
        r"该|此|其|上述|前述|本品|它|\b(?:it|its|they|their|this|that|these|those)\b",
        record.text, re.IGNORECASE,
    ))
    aliases = {}
    for resolution in resolutions.values():
        if resolution["scope"] != task.scope.model_dump(mode="json"):
            continue
        if any(anchor["evidence_id"] in units for anchor in resolution["source_refs"]):
            aliases[resolution["entity_ref"]["id"]] = True
    positions = {value.record_id: position for position, value in enumerate(index.records)}
    ranked = []
    for node in nodes.values():
        origin = origins.get(node.entity_id)
        if node.root or node.class_iri not in classes or origin is None:
            continue
        physical = node.entity_id in aliases or any(
            anchor.evidence_id in units for anchor in node.evidence_refs
        )
        if not physical and origin["scope"] != task.scope.model_dump(mode="json"):
            continue
        name = any(normalized_reference_name(label) in text
                   for label in node.label.split(" / ") if normalized_reference_name(label))
        records = [linked for anchor in node.evidence_refs
                   for linked in index.records_by_evidence.get(anchor.evidence_id, [])]
        distance = min((positions[record.record_id] - positions[linked.record_id]
                        for linked in records
                        if linked.section_node_id == record.section_node_id
                        and positions[linked.record_id] <= positions[record.record_id]),
                       default=None)
        local = has_anaphora and distance is not None and distance <= 3
        if physical or name or local:
            ranked.append((0 if physical else 1 if name else 2,
                           distance if distance is not None else len(positions), node.entity_id))
    return [identity for _, _, identity in sorted(ranked)[:limit]]
