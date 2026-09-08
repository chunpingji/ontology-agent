"""Deterministic chapter ordering; ranking is neither evidence nor authorization.

Only the current relation and the local menus of its legal range classes supply
ontology clues. Phase two retains every remaining non-heading record, including
unmatched chapters. A rejected proposal never changes this retrieval partition.
"""

from __future__ import annotations

from collections import defaultdict, deque
from copy import deepcopy

from app.evaluation.hierarchy_variant import _score
from app.services.extraction.evidence_identity import evidence_hash, stable_id
from app.services.extraction.evidence_scope import document_anchors

POLICY_VERSION = "evaluation-staged-relation-chapters-v1"


def _local_definitions(index, subject, predicate, schema):
    if evidence_hash(schema) != index.ontology_release:
        raise ValueError("staged retrieval schema does not match record index")
    if (subject.kind != "entity" or not subject.positive_eligible
            or subject.review_status == "rejected"):
        raise ValueError("staged retrieval requires a validated affirmative entity subject")
    if (subject.identity.get("document_root")
            and subject.identity["document_root"] != index.ir.document_hash):
        raise ValueError("subject document root does not match record index")
    for anchor in document_anchors(subject):
        index.ir.resolve(anchor)
    canonical = next((item for item in schema.get(subject.class_iri, {}).get("relationships", [])
                      if item["iri"] == predicate.get("iri")), None)
    if canonical is None:
        raise ValueError("relation is not declared in the current subject's local menu")
    ranges = set(canonical.get("range", [])) & set(schema)
    while True:
        expanded = ranges | {iri for iri, definition in schema.items()
                             if ranges.intersection(definition.get("parents", []))}
        if expanded == ranges:
            break
        ranges = expanded
    definitions = [canonical]
    for iri in sorted(ranges):
        definition = schema[iri]
        definitions.extend([definition, *definition.get("properties", []),
                            *definition.get("relationships", [])])
    # Aliases are ontology-owned labels, not source names or predicted identities.
    clues = [dict(item, label=label) for item in definitions
             for label in dict.fromkeys([item.get("label", ""), *item.get("aliases", [])])]
    return canonical, sorted(ranges), clues


def plan_relation_chapters(index, subject, predicate, schema, *, phase1_section_limit=3):
    """Return two complete, disjoint phases of unchanged source-record IDs.

    Phase one contains up to ``phase1_section_limit`` highest-scoring matched
    sections. All other records remain in phase two, including zero-score ones.
    Each phase round-robins sections, so rejection in one chapter cannot consume
    another chapter's chance. Only existing IR source/headers and validated cue
    metadata are ranked; no identities, summaries or headings become facts.
    """
    if type(phase1_section_limit) is not int or phase1_section_limit < 1:
        raise ValueError("phase1_section_limit must be a positive integer")
    canonical, ranges, clues = _local_definitions(index, subject, predicate, schema)

    def relevance(text):
        return max((_score(text, definition) for definition in clues), default=0.0)

    grouped = defaultdict(list)
    for record in index.records:
        if record.kind == "heading":
            continue
        text = "\n".join(index.ir.unit(region.evidence_id).text[region.start:region.end]
                         for region in [*record.source_ranges, *record.binding_ranges])
        source_score = relevance(text)
        cues = index.cues["nodes"].get(record.section_node_id, {}).get("cues", [])
        heading_score = sum(0.5 ** cue["distance"] * relevance(cue["heading"]) for cue in cues)
        summary_score = sum(0.5 ** cue["distance"] * relevance(cue["summary"]) for cue in cues)
        rationale = [name for name, score in (
            ("original_source_or_column_header_matches_local_ontology", source_score),
            ("chapter_heading_matches_local_ontology", heading_score),
            ("chapter_summary_matches_local_ontology", summary_score),
        ) if score > 0] or ["unmatched_record_retained_for_fallback"]
        grouped[record.section_node_id].append({
            "record_id": record.record_id,
            "section_node_id": record.section_node_id,
            "record_kind": record.kind,
            "rank_score": round(4 * source_score + 2 * heading_score + summary_score, 6),
            "rationale": rationale,
            "score_components": {"original_source_and_headers": round(source_score, 6),
                                 "headings": round(heading_score, 6),
                                 "summaries": round(summary_score, 6)},
            "source_position": min(index.positions[r.evidence_id] for r in record.source_ranges),
        })
    sections = []
    for section_id, records in grouped.items():
        records.sort(key=lambda item: (-item["rank_score"], item["source_position"]))
        # Multiple relevant fields provide chapter evidence without letting long
        # chapters win merely because they contain more paragraphs.
        top_scores = [item["rank_score"] for item in records[:3]]
        sections.append({
            "section_node_id": section_id,
            "rank_score": round(max(top_scores) + sum(top_scores) / len(top_scores), 6),
            "source_position": min(item["source_position"] for item in records),
        })
    sections.sort(key=lambda item: (-item["rank_score"], item["source_position"]))
    primary = {item["section_node_id"] for item in sections if item["rank_score"] > 0}
    primary = {item["section_node_id"] for item in sections[:phase1_section_limit]} & primary
    phases = []
    for phase in (1, 2):
        selected_sections = [item for item in sections
                             if (item["section_node_id"] in primary) == (phase == 1)]
        queues = [deque(grouped[item["section_node_id"]]) for item in selected_sections]
        records = []
        while any(queues):
            for queue in queues:
                if queue:
                    records.append({**queue.popleft(), "phase": phase})
        phases.append({"phase": phase, "records": records,
                       "section_node_ids": [item["section_node_id"] for item in selected_sections]})
    records = [deepcopy(record) for phase in phases for record in phase["records"]]
    output = {
        "policy_version": POLICY_VERSION,
        "analysis_id": index.ir.analysis_id,
        "ontology_release": index.ontology_release,
        "metadata_dependency": index.cues["dependency_hash"],
        "subject": {"candidate_id": subject.candidate_id, "revision": subject.revision,
                    "class_iri": subject.class_iri},
        "predicate_iri": canonical["iri"],
        "range_class_iris": ranges,
        "phase1_section_limit": phase1_section_limit,
        "phases": phases,
        "records": records,
        "sections": sections,
        "excluded_heading_record_ids": [record.record_id for record in index.records
                                        if record.kind == "heading"],
        "coverage": {"total_records": len(records), "unsearched_records": len(records),
                     "complete": False, "all_nonheading_records_retained": True},
        "authorization": "ranking_only; original record fact permissions remain unchanged",
        "rejection_policy": "A rejected candidate is not a global negative; continue all "
                            "remaining records in both phases.",
        "diagnostics": deepcopy(index.cues.get("diagnostics", [])),
    }
    output["plan_id"] = stable_id("staged-relation-chapters", output)
    return output


def context_ranges(index, record, *, radius=None):
    """Return detached, **binding-only** neighboring paragraph ranges, never facts.

    The default includes the entire contiguous non-table paragraph block in the
    same section, excluding the candidate's own paragraph. Heading, table and
    section boundaries stop expansion. No character truncation is performed;
    an oversized verification context must be reported by the caller. A finite
    radius limits neighboring *whole paragraphs* per side, not their contents.
    Table-row/header/parent-container rules are untouched: nonparagraph records
    return no additional ranges. Callers must add these ranges only to binding
    permission/fragments, never source targets, fact permissions or saved scope.
    """
    if radius is not None and (type(radius) is not int or radius < 0):
        raise ValueError("radius must be a nonnegative integer or None")
    current = index.by_id.get(record.record_id)
    if current is None:
        raise ValueError("context record does not belong to index")
    if current.kind != "paragraph":
        return []
    position = next(i for i, item in enumerate(index.records)
                    if item.record_id == current.record_id)
    neighbors = []
    for direction in (-1, 1):
        offset = position + direction
        count = 0
        while 0 <= offset < len(index.records) and (radius is None or count < radius):
            other = index.records[offset]
            if other.kind != "paragraph" or other.section_node_id != current.section_node_id:
                break
            neighbors.extend(other.source_ranges)
            offset += direction
            count += 1
    # Check physical evidence too: a header-only table can have no SourceRecord
    # and must still prevent paragraph-context expansion across its cells.
    current_positions = [index.positions[r.evidence_id] for r in current.source_ranges]
    left, right = min(current_positions), max(current_positions)
    permitted = []
    for region in neighbors:
        target = index.positions[region.evidence_id]
        interval = (target, left) if target < left else (right + 1, target + 1)
        if all(unit.kind == "paragraph" and not unit.table_path
               and unit.section_node_id == current.section_node_id
               for unit in index.ir.evidence_units[interval[0]:interval[1]]):
            permitted.append(region.model_copy(deep=True))
    return index._unique_ranges(permitted)
