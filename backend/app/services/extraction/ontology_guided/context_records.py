"""Retrieve complete local structures as binding evidence, never fact grants."""

import re

CONTEXT_RECORDS_VERSION = "ontology-context-records-v1"


def field_group_context(index, record_id, subject_label):
    groups = index.field_groups_by_record.get(record_id, ())
    units, owners = {}, []
    for group in groups:
        for identity in group.record_ids:
            units.update((unit.evidence_id, unit) for unit in index.by_id[identity].source_units)
        for anchor in group.value_refs:
            value = index.ir.resolve(anchor).strip()
            if value and subject_label and re.search(
                r"(?<![A-Za-z0-9])" + re.escape(value) + r"(?![A-Za-z0-9])", subject_label,
            ):
                # Preserve the role label, not just the repeated value.
                unit = index.ir.unit(anchor.evidence_id)
                owners.append(index.ir.anchor(anchor.evidence_id, 0, len(unit.text)))
    return list(units.values()), owners


def named_object_context(index, record_id, predicate, ontology):
    if predicate is None or predicate.kind != "relationship" or ontology is None:
        return []
    definitions = [ontology.classes[iri] for iri in predicate.range_class_iris
                   if iri in ontology.classes]
    record = index.by_id[record_id]
    captions = [unit for unit in record.source_units if not unit.table_path
                and len(unit.text) <= 120
                and any(definition.label in unit.text for definition in definitions)]
    if not captions:
        return []
    related = {iri for definition in definitions for edge in definition.declared_relationships
               for iri in edge.range_class_iris}
    labels = [definition.label for definition in definitions]
    labels.extend(ontology.classes[iri].label for iri in related if iri in ontology.classes)
    labels.extend(prop.label for definition in definitions
                  for prop in definition.declared_properties)
    terms = {word for label in labels for word in re.findall(r"[\u4e00-\u9fff]{2,}", label)}
    terms.update(term[i:i + 2] for term in list(terms) for i in range(len(term) - 1))
    nodes = index.nodes_by_id
    parents = {nodes[unit.section_node_id].get("parent_id") for unit in captions}
    parents.difference_update({None, "document"})
    headings = sorted(
        (unit for parent in parents for unit in index.headings_by_parent.get(parent, ())
         if any(term in unit.text for term in terms)),
        key=lambda unit: index.positions[unit.evidence_id],
    )
    units = {unit.evidence_id: unit for unit in headings}
    for heading in headings:
        first = index.first_record_by_section.get(heading.section_node_id)
        if first:
            units.update((unit.evidence_id, unit) for unit in (
                *first.header_units, *first.source_units, *first.note_units, *first.parent_units))
    return list(units.values())
