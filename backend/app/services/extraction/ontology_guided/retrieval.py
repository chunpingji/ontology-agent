"""Deterministic complete two-phase retrieval for every in-scope predicate."""

from __future__ import annotations

import re
from collections import defaultdict, deque

from app.services.extraction.evidence_identity import evidence_hash, stable_id
from app.services.extraction.ontology_guided.contracts import (
    EdgeSpec,
    MetadataSnapshot,
    PlannedRecord,
    RecallEntry,
    RetrievalPlan,
    SlotSpec,
    SubjectRef,
)
from app.services.extraction.ontology_guided.records import RecordIndex


def _terms(predicate: SlotSpec | EdgeSpec) -> list[str]:
    values = [predicate.label, predicate.iri.rsplit("/", 1)[-1].rsplit("#", 1)[-1]]
    if isinstance(predicate, EdgeSpec):
        for item in predicate.range_classes:
            values.extend([item.label, *item.direct_field_labels])
    result: list[str] = []
    for value in values:
        compact = re.sub(r"\s+", "", value or "").lower()
        if compact and compact not in result:
            result.append(compact)
    return result


def _match_score(text: str, terms: list[str]) -> float:
    compact = re.sub(r"\s+", "", text).lower()
    if not compact:
        return 0.0
    return float(sum(1 for term in terms if term and term in compact))


def plan_slot(
    subject_ref: SubjectRef,
    slot_spec: SlotSpec | EdgeSpec,
    index: RecordIndex,
    metadata: MetadataSnapshot,
    *,
    ontology_hash: str,
    phase1_section_limit: int = 3,
) -> RetrievalPlan:
    """Partition all non-heading records into disjoint primary/fallback phases."""
    if phase1_section_limit < 1:
        raise ValueError("phase1_section_limit must be positive")
    if metadata.analysis_id != index.ir.analysis_id:
        raise ValueError("metadata and record index belong to different analyses")
    terms = _terms(slot_spec)
    node_metadata = {node.node_id: node for node in metadata.node_summaries}
    grouped: dict[str, list[dict]] = defaultdict(list)
    positions = index.source_positions
    for record in index.records:
        node = node_metadata.get(record.section_node_id)
        source_score = _match_score(
            index.source_text(record.record_id, include_context=True), terms
        )
        heading_score = _match_score(" / ".join(node.path if node else []), terms)
        summary_score = _match_score(node.summary or "", terms) if node else 0.0
        rank_score = 4 * source_score + 2 * heading_score + summary_score
        grouped[record.section_node_id].append(
            {
                "record": record,
                "rank_score": rank_score,
                "score_components": {
                    "source_labels": source_score,
                    "heading": heading_score,
                    "summary": summary_score,
                },
                "rationale": [
                    name
                    for name, value in (
                        ("source_label", source_score),
                        ("heading", heading_score),
                        ("summary_retrieval_only", summary_score),
                    )
                    if value > 0
                ]
                or ["unmatched_record_retained_for_phase2"],
            }
        )
    sections = []
    for section_node_id, values in grouped.items():
        ordered = sorted(
            values,
            key=lambda item: (-item["rank_score"], positions[item["record"].record_id]),
        )
        top = [item["rank_score"] for item in ordered[:3]]
        section_score = max(top) + sum(top) / len(top) if top else 0.0
        sections.append(
            (
                section_node_id,
                section_score,
                min(positions[item["record"].record_id] for item in ordered),
            )
        )
        grouped[section_node_id] = ordered
    sections.sort(key=lambda item: (-item[1], item[2]))
    primary = {section_id for section_id, score, _ in sections[:phase1_section_limit] if score > 0}
    planned: list[PlannedRecord] = []
    for phase in (1, 2):
        queues = [
            deque(grouped[section_id])
            for section_id, _, _ in sections
            if (section_id in primary) == (phase == 1)
        ]
        while any(queues):
            for queue in queues:
                if not queue:
                    continue
                item = queue.popleft()
                record = item["record"]
                planned.append(
                    PlannedRecord(
                        record_id=record.record_id,
                        phase=phase,
                        section_node_id=record.section_node_id,
                        record_kind=record.kind,
                        rank_score=item["rank_score"],
                        score_components=item["score_components"],
                        rationale=item["rationale"],
                    )
                )
    ledger = {
        item.record_id: RecallEntry(record_id=item.record_id, phase=item.phase) for item in planned
    }
    identity = {
        "subject": subject_ref.model_dump(mode="json"),
        "predicate": slot_spec.model_dump(mode="json"),
        "analysis": index.ir.analysis_id,
        "metadata": metadata.snapshot_id,
        "ontology_hash": ontology_hash,
        "phase1_section_limit": phase1_section_limit,
        "record_ids": [item.record_id for item in planned],
    }
    return RetrievalPlan(
        plan_id=stable_id("retrieval-plan", identity),
        subject=subject_ref,
        predicate_iri=slot_spec.iri,
        predicate_kind=slot_spec.kind,
        metadata_snapshot_id=metadata.snapshot_id,
        ontology_hash=ontology_hash,
        phase1_section_limit=phase1_section_limit,
        records=planned,
        ledger=ledger,
        frozen_record_ids=[record.record_id for record in index.records],
        frozen_record_hash=evidence_hash([record.record_id for record in index.records]),
    )


def validate_record_universe(plan: RetrievalPlan, index: RecordIndex) -> None:
    """Compare with independently rebuilt U, not merely two mutable plan lists."""
    expected = [record.record_id for record in index.records]
    if (
        plan.frozen_record_ids != expected
        or plan.frozen_record_hash != evidence_hash(expected)
        or {record.record_id for record in plan.records} != set(expected)
        or set(plan.ledger) != set(expected)
    ):
        raise ValueError("retrieval plan differs from the frozen RecordIndex universe")


def mark_record(
    plan: RetrievalPlan,
    record_id: str,
    *,
    coverage_state: str,
    execution_state: str,
    semantic_outcomes: list[str] | None = None,
    task_id: str | None = None,
    call_id: str | None = None,
    reason_code: str | None = None,
) -> RetrievalPlan:
    """Return a revised immutable plan while preserving coverage conservation."""
    if record_id not in plan.ledger:
        raise ValueError("record is outside this retrieval plan")
    updated = plan.model_copy(deep=True)
    entry = updated.ledger[record_id]
    entry.coverage_state = coverage_state
    entry.execution_state = execution_state
    if semantic_outcomes is not None:
        entry.semantic_outcomes = semantic_outcomes
    if task_id and task_id not in entry.task_ids:
        entry.task_ids.append(task_id)
    if call_id and call_id not in entry.call_ids:
        entry.call_ids.append(call_id)
    if reason_code and reason_code not in entry.reason_codes:
        entry.reason_codes.append(reason_code)
    return RetrievalPlan.model_validate(updated.model_dump(mode="json"))
