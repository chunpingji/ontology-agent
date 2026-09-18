"""Record-level lookahead for root relations; ranking never supplies proof."""

import re

from app.services.extraction.evidence_identity import evidence_hash, stable_id
from app.services.extraction.ontology_guided.contracts import (
    EdgeSpec,
    MetadataNode,
    MetadataSnapshot,
    RangeClass,
    SubjectRef,
)
from app.services.extraction.ontology_guided.records import IndexedRecord, RecordIndex
from app.services.extraction.ontology_guided.retrieval import plan_slot

PRIORITY_VERSION = "root-relation-records-v2-named-headings"


def range_classes(schema, predicate):
    declared = set(predicate.get("range", []))
    return sorted(iri for iri, value in schema.items()
                  if iri in declared or declared.intersection(value.get("parents", [])))


def _label_terms(labels):
    # Chinese field labels need segmentation for retrieval (e.g. a prefixed
    # relation label and an equipment heading). These terms never become facts.
    terms = list(dict.fromkeys(label for label in labels if label))
    for label in list(terms):
        for word in re.findall(r"[\u4e00-\u9fff]{2,}", label):
            terms.extend(word[i:i + 2] for i in range(len(word) - 1))
    return list(dict.fromkeys(terms))


def relation_records(ir, schema, root, priorities, *, index=None):
    """Return a frozen, complete ranking for each legal direct root relation."""
    index = index or RecordIndex(ir)
    nodes = {node["node_id"]: node for node in ir.nodes}
    summaries = []
    for node in ir.nodes:
        path, seen, cursor = [], set(), node
        while cursor and cursor["node_id"] not in seen:
            seen.add(cursor["node_id"])
            if cursor.get("heading"):
                path.insert(0, cursor["heading"])
            cursor = nodes.get(cursor.get("parent_id"))
        summaries.append(MetadataNode(node_id=node["node_id"], path=path))
    metadata = MetadataSnapshot(
        snapshot_id=evidence_hash([PRIORITY_VERSION, ir.analysis_id]),
        analysis_id=ir.analysis_id, document_hash=ir.document_hash,
        structure_hash=ir.structure_hash, summary_version=PRIORITY_VERSION,
        generation_source="structure_only", node_summaries=summaries,
        dependency_hash=evidence_hash(ir.nodes),
    )
    subject = SubjectRef(entity_id=root.candidate_id, revision=root.revision,
                         class_iri=root.class_iri, is_document_root=True)
    preferred = {path[0]: i for i, path in reversed(list(enumerate(priorities))) if path}
    predicates = sorted(schema[root.class_iri].get("relationships", []),
                        key=lambda p: (preferred.get(p["iri"], len(priorities)), p["iri"]))
    result = []
    for predicate in predicates:
        classes = range_classes(schema, predicate)
        if not classes:
            continue
        ranges = []
        for iri in classes:
            definition = schema[iri]
            labels = [predicate.get("label"), definition.get("label")]
            labels.extend(p.get("label") for p in definition.get("properties", []))
            ranges.append(RangeClass(
                iri=iri, label=definition.get("label") or iri,
                direct_field_labels=_label_terms(labels),
            ))
        edge = EdgeSpec(iri=predicate["iri"], label=predicate.get("label") or predicate["iri"],
                        range_class_iris=predicate["range"], range_classes=ranges)
        plan = plan_slot(subject, edge, index, metadata, ontology_hash=evidence_hash(schema))
        # Lookahead only: unmatched records remain in the subsequent full scan.
        records = [index.by_id[item.record_id] for item in plan.records
                   if item.phase == 1 and item.rank_score > 0]
        # A directly named section (for example a route heading) still deserves
        # entity discovery. It is not a data row and proves no relationship by
        # itself; recall/type/binding verification all retain their normal gates.
        named_headings = [unit for unit in ir.evidence_units if unit.kind == "heading"
                          and not unit.table_path and any(
                              value.label and value.label in unit.text for value in ranges
                          )]
        records = [IndexedRecord(
            record_id=stable_id("priority-heading", [ir.analysis_id, unit.evidence_id]),
            kind="heading", section_node_id=unit.section_node_id, source_units=(unit,),
        ) for unit in named_headings] + records
        result.append((predicate, classes, records))
    return result
