"""Versioned source ranges; structural scope permits retrieval, not attribution."""

from app.schemas.evidence import (
    Candidate,
    CandidateRef,
    DocumentProvenance,
    EvidenceAnchor,
    EvidenceRange,
    EvidenceScope,
    ScopeExpansion,
)
from app.services.extraction.document_ir import DocumentIR
from app.services.extraction.evidence_identity import stable_id


def document_anchors(candidate: Candidate) -> list[EvidenceAnchor]:
    return [
        anchor
        for source in candidate.provenance
        if isinstance(source, DocumentProvenance)
        for anchor in source.anchors
    ]


def candidate_ref(candidate: Candidate) -> CandidateRef:
    return CandidateRef(
        candidate_id=candidate.candidate_id,
        revision=candidate.revision,
        class_iri=candidate.class_iri,
        instance_iri=candidate.identity.get("instance_iri"),
    )


def scope_intervals(scope: EvidenceScope, ir: DocumentIR) -> dict[str, list[tuple[int, int]]]:
    included: dict[str, list[tuple[int, int]]] = {}
    for region in scope.ranges:
        text = ir.unit(region.evidence_id).text
        start, end = (region.start, region.end) if region.start is not None else (0, len(text))
        if not 0 <= start <= end <= len(text):
            raise ValueError("scope range outside source")
        included.setdefault(region.evidence_id, []).append((start, end))
    result = {}
    for identity, intervals in included.items():
        merged: list[tuple[int, int]] = []
        for start, end in sorted(intervals):
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
            else:
                merged.append((start, end))
        for excluded in scope.excluded_ranges:
            if excluded.evidence_id != identity:
                continue
            left = excluded.start or 0
            right = excluded.end if excluded.end is not None else len(ir.unit(identity).text)
            clipped = []
            for start, end in merged:
                if end <= left or start >= right:
                    clipped.append((start, end))
                else:
                    if start < left:
                        clipped.append((start, left))
                    if end > right:
                        clipped.append((right, end))
            merged = clipped
        result[identity] = merged
    return result


def scope_contains(scope: EvidenceScope | None, anchor: EvidenceAnchor, ir: DocumentIR) -> bool:
    ir.resolve(anchor)
    if scope is None or scope.document_hash != ir.document_hash:
        return False
    start = anchor.span_start or 0
    end = anchor.span_end if anchor.span_end is not None else len(ir.unit(anchor.evidence_id).text)
    return any(
        left <= start < end <= right
        for left, right in scope_intervals(scope, ir).get(anchor.evidence_id, [])
    )


def build_scope(
    ir: DocumentIR, subject: Candidate, competitors: list[Candidate] | None = None
) -> EvidenceScope:
    anchors = document_anchors(subject)
    if subject.kind != "entity" or subject.validation_status != "passed" or not anchors:
        raise ValueError("scope requires a validated subject with document identity")
    for anchor in anchors:
        ir.resolve(anchor)
    units = [ir.unit(anchor.evidence_id) for anchor in anchors]
    selected = {unit.evidence_id for unit in units}
    if subject.identity.get("document_root") == ir.document_hash:
        selected.update(u.evidence_id for u in ir.evidence_units if u.text)
    competitor_units = [
        ir.unit(anchor.evidence_id)
        for candidate in (competitors or [])
        for anchor in document_anchors(candidate)
    ]
    records = []
    for unit in units:
        if unit.kind == "heading":
            competing_heading = any(
                c.kind == "heading" and c.section_node_id == unit.section_node_id
                for c in competitor_units
            )
            if not competing_heading:
                selected.update(
                    u.evidence_id
                    for u in ir.evidence_units
                    if u.section_node_id == unit.section_node_id
                )
                records.append(unit.section_node_id)
        elif unit.table_path:
            selected.update(
                u.evidence_id
                for u in ir.evidence_units
                if u.table_path == unit.table_path and u.row_index == unit.row_index
            )
            records.append("/".join(unit.table_path) + f"/row:{unit.row_index}")
    payload = {
        "document_hash": ir.document_hash,
        "subject": candidate_ref(subject),
        "ranges": [
            EvidenceRange(evidence_id=u.evidence_id)
            for u in ir.evidence_units
            if u.evidence_id in selected and u.text
        ],
        "construction_evidence": anchors,
        "record_ids": records,
    }
    return EvidenceScope(scope_id=stable_id("scope", payload), **payload)


def expand_scope(
    scope: EvidenceScope, expansion: ScopeExpansion, ir: DocumentIR, max_expansions: int = 1
) -> EvidenceScope:
    if len(scope.expansion_history) >= min(1, max_expansions):
        raise ValueError("scope expansion budget exhausted")
    for anchor in expansion.evidence:
        ir.resolve(anchor)
    payload = scope.model_dump(mode="json", exclude={"scope_id"})
    payload.update(
        revision=scope.revision + 1,
        parent_scope_id=scope.scope_id,
        ranges=[*payload["ranges"], *[r.model_dump() for r in expansion.added_ranges]],
        expansion_history=[*payload["expansion_history"], expansion.model_dump(mode="json")],
    )
    updated = EvidenceScope(scope_id=stable_id("scope", payload), **payload)
    scope_intervals(updated, ir)
    return updated
