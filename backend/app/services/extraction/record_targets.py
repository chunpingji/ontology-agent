"""Adapt the shared frozen RecordIndex to legacy task ranges without splitting rows."""

import re

from app.schemas.evidence import EvidenceRange, EvidenceScope, ScopeExpansion
from app.services.extraction.evidence_identity import stable_id
from app.services.extraction.evidence_scope import expand_scope, scope_contains
from app.services.extraction.ontology_guided.records import RecordIndex

RECORD_TARGET_VERSION = "record-targets-v4-local-owner-lookup"


def record_targets(index: RecordIndex, regions):
    """Return complete logical records touched by the requested retrieval ranges.

    This selects fact targets, not subject ownership. The runner still validates
    every target against the subject scope and independently verifies attribution.
    Table headers remain context. Headings outside tables retain their existing
    entity-discovery opportunity, without pretending to be data records.
    """
    selected = {region.evidence_id for region in regions}
    covered = set()
    batches = []
    for record in index.records:
        identities = {unit.evidence_id for unit in record.source_units}
        covered.update(identities)
        if not identities.intersection(selected):
            continue
        batches.append((
            min(index.positions[identity] for identity in identities),
            record.record_id,
            [EvidenceRange(evidence_id=unit.evidence_id, start=0, end=len(unit.text))
             for unit in record.source_units],
        ))
    for unit in index.ir.evidence_units:
        if (unit.evidence_id in selected - covered and unit.text and not unit.table_path):
            batches.append((index.positions[unit.evidence_id], None, [
                EvidenceRange(evidence_id=unit.evidence_id, start=0, end=len(unit.text)),
            ]))
    # Stable ties retain RecordIndex's logical-row order when a merged cell is
    # the first physical source of several rows; hashes must not reorder rows.
    return [(identity, values) for _, identity, values in sorted(
        batches, key=lambda batch: batch[0],
    )]


def validate_record_target(task, index):
    if task.target_record_id is None:
        return
    record = index.by_id.get(task.target_record_id)
    if record is None:
        raise ValueError("unknown_target_record")
    expected = [EvidenceRange(evidence_id=unit.evidence_id, start=0, end=len(unit.text))
                for unit in record.source_units]
    if (task.target_ranges != expected
        or task.target_evidence_ids != [region.evidence_id for region in expected]):
        raise ValueError("incomplete_record_target")


def field_group_scope(scope, subject, index):
    """Retrieve a named field group; require separate cross-record owner proof.

    Matching a field value is only a lookup hint. It never merges mentions or
    supplies a global identifier, and every added fact needs reference verification.
    """
    if subject.identity.get("document_root") or scope.expansion_history:
        return scope
    def name_hint(anchor):
        value = index.ir.resolve(anchor).strip()
        # A literal field value may occur inside a longer source label (a
        # project code followed by its role noun). This is retrieval only, with
        # token boundaries preventing prefix collisions such as X12 / X123.
        return bool(value and re.search(
            r"(?<![A-Za-z0-9])" + re.escape(value) + r"(?![A-Za-z0-9])", subject.text,
        ))

    groups = [group for group in index.field_groups if any(
        name_hint(anchor) for anchor in group.value_refs
    )]
    if not groups:
        return scope
    # Keep the field's label with its value. A bare repeated name hides the
    # original role (for example project name versus a comparator drug), making
    # independent attribution impossible even though the source explains it.
    owner_ids = {anchor.evidence_id for group in groups for anchor in group.value_refs
                 if name_hint(anchor)}
    owner_refs = [index.ir.anchor(unit.evidence_id) for group in groups
                  for record_id in group.record_ids
                  if any(unit.evidence_id in owner_ids
                         for unit in index.by_id[record_id].source_units)
                  for unit in index.by_id[record_id].source_units]
    units = {unit.evidence_id: unit for group in groups for record_id in group.record_ids
             for unit in index.by_id[record_id].source_units}
    added = [EvidenceRange(evidence_id=unit.evidence_id, start=0, end=len(unit.text))
             for unit in sorted(units.values(), key=lambda unit: index.positions[unit.evidence_id])
             if not scope_contains(scope, index.ir.anchor(unit.evidence_id), index.ir)]
    if not added:
        return scope
    updated = expand_scope(scope, ScopeExpansion(
        reason="field_group_reference_lookup", added_ranges=added, evidence=owner_refs,
        policy_version=RECORD_TARGET_VERSION,
    ), index.ir)
    # Reference verification must compare both original owner and the current
    # group. A same-name field alone never grants positive eligibility.
    payload = updated.model_dump(mode="python", exclude={"scope_id"})
    payload["reference_ranges"] = [*scope.reference_ranges, *added]
    return EvidenceScope(scope_id=stable_id("scope", payload), **payload)


def field_group_context(task, index):
    """Return the complete retrieved form for independent local attribution.

    A form's field labels, competing owners and boundary must remain visible;
    two isolated adjacent values cannot convey the source's record structure.
    These records are binding context, never additional fact targets.
    """
    owners = {anchor.evidence_id for expansion in (task.scope.expansion_history
              if task.scope else []) if expansion.reason == "field_group_reference_lookup"
              for anchor in expansion.evidence}
    targets = set(task.target_evidence_ids)
    result = []
    for group in index.field_groups if owners else []:
        units = [unit for record_id in group.record_ids
                 for unit in index.by_id[record_id].source_units]
        identities = {unit.evidence_id for unit in units}
        if identities.intersection(owners) and identities.intersection(targets):
            result.append((group.field_group_id, units))
    return result


def related_heading_context(task, index, schema):
    """Retrieve related sibling headings for a named caption, without fact grants.

    A caption can name an aggregate while its contents live in sibling sections
    (Word heading levels are not necessarily the caption's numbering hierarchy).
    Type/range labels rank those headings; an independent verifier must still
    distinguish the aggregate from an individual component or a bare reference.
    """
    if task.task_kind != "entity":
        return []
    classes = [schema[iri] for iri in task.target_class_iris if iri in schema]
    captions = [index.ir.unit(identity) for identity in task.target_evidence_ids
                if any(definition.get("label") and definition["label"]
                       in index.ir.unit(identity).text for definition in classes)]
    captions = [unit for unit in captions if not unit.table_path and len(unit.text) <= 120]
    if not captions:
        return []
    related = {iri for definition in classes for relation in definition.get("relationships", [])
               for iri in relation.get("range", [])}
    labels = [definition.get("label", "") for definition in classes]
    labels.extend(schema.get(iri, {}).get("label", "") for iri in related)
    terms = {word for label in labels for word in re.findall(r"[\u4e00-\u9fff]{2,}", label)}
    terms.update(term[i:i + 2] for term in list(terms) for i in range(len(term) - 1))
    nodes = {node["node_id"]: node for node in index.ir.nodes}
    parents = {nodes[unit.section_node_id].get("parent_id") for unit in captions}
    parents.discard(None)
    parents.discard("document")
    headings = [unit for unit in index.ir.evidence_units
                if unit.kind == "heading" and not unit.table_path
                and nodes[unit.section_node_id].get("parent_id") in parents
                and unit.evidence_id not in task.target_evidence_ids
                and any(term in unit.text for term in terms)]
    units = {unit.evidence_id: unit for unit in headings}
    for heading in headings:
        record = next((record for record in index.records
                       if record.section_node_id == heading.section_node_id), None)
        if record:
            # Retain one complete source record to distinguish a real expanded
            # section from another bare title. Never split a logical table row.
            units.update((unit.evidence_id, unit) for unit in (
                *record.header_units, *record.source_units,
                *record.note_units, *record.parent_units,
            ))
    return [index.ir.anchor(unit.evidence_id) for unit in sorted(
        units.values(), key=lambda unit: index.positions[unit.evidence_id],
    )]
