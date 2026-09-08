"""Versioned source ranges; structural scope permits retrieval, not attribution."""

import re

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
from app.services.extraction.performance import timed
from app.services.extraction.table_records import table_records


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


@timed("scope_intervals")
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


def scope_contains(
    scope: EvidenceScope | None, anchor: EvidenceAnchor, ir: DocumentIR, *, intervals=None,
) -> bool:
    ir.resolve(anchor)
    if scope is None or scope.document_hash != ir.document_hash:
        return False
    start = anchor.span_start or 0
    end = anchor.span_end if anchor.span_end is not None else len(ir.unit(anchor.evidence_id).text)
    return any(
        left <= start < end <= right
        for left, right in (
            intervals if intervals is not None else scope_intervals(scope, ir)
        ).get(anchor.evidence_id, [])
    )


def _statement_bounds(text: str, start: int, end: int) -> tuple[int, int]:
    """Retrieve the same statement as a verified relation, preserving source offsets.

    Model relation quotes often end at the object mention, before comma-separated
    attributes. Sentence/semicolon boundaries keep sibling records separate;
    independent property binding still decides attribution within the statement.
    """
    def boundary(index):
        char = text[index]
        return char in "。！？!?；;\n\r" or (
            char == "." and (index + 1 == len(text) or text[index + 1].isspace())
        )

    while start > 0 and not boundary(start - 1):
        start -= 1
    last = end - 1
    while last >= start and text[last].isspace():
        last -= 1
    if last < start or not boundary(last):
        while end < len(text) and not boundary(end):
            end += 1
    return start, end


def _reference_continuations(ir, seed, end, subject, competitors):
    """Bounded retrieval of discourse continuations; attribution needs a verdict.

    Stop at a new record, heading, unlinked paragraph or named competing subject.
    The discourse markers select text for inspection; they never prove identity.
    """
    unit = ir.unit(seed.evidence_id)
    if unit.kind != "paragraph":
        return []
    names = {c.text for c in competitors if c.kind == "entity" and c.text != subject.text}
    units = ir.evidence_units
    index = units.index(unit)
    result = []
    for offset, following in enumerate(units[index:index + 3]):
        if following.kind != "paragraph" or following.section_node_id != unit.section_node_id:
            break
        cursor = end if offset == 0 else 0
        while cursor < len(following.text):
            while cursor < len(following.text) and following.text[cursor] in "。！？!?；;\r\n. \t":
                cursor += 1
            if cursor == len(following.text):
                break
            _, right = _statement_bounds(following.text, cursor, cursor + 1)
            text = following.text[cursor:right]
            if any(name in text for name in names) or not (
                text.startswith(subject.text)
                or re.match(r"(?:该|此|上述|其|[Ii]t\b|[Tt]his\b|[Tt]hese\b)", text)
            ):
                return result
            if right < len(following.text) and following.text[right] in "。！？!?；;":
                right += 1
            result.append(EvidenceRange(evidence_id=following.evidence_id, start=cursor, end=right))
            cursor = right
    return result


def build_scope(
    ir: DocumentIR, subject: Candidate, competitors: list[Candidate] | None = None,
    *, bound_relationships: list[Candidate] | None = None,
) -> EvidenceScope:
    anchors = document_anchors(subject)
    if subject.kind != "entity" or subject.validation_status != "passed" or not anchors:
        raise ValueError("scope requires a validated subject with document identity")
    shared_identity = subject.identity.get("instance_iri")
    aliases = [
        c for c in competitors or []
        if shared_identity and c.kind == "entity" and c.positive_eligible
        and c.review_status != "rejected" and c.class_iri == subject.class_iri
        and c.identity.get("instance_iri") == shared_identity
    ]
    anchors = [*anchors, *(a for alias in aliases for a in document_anchors(alias))]
    competitors = [c for c in competitors or [] if c not in aliases]
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
    tables = table_records(ir)
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
            for row in sorted(tables.rows(unit)):
                selected.update(u.evidence_id for u in tables.row_units(unit.table_path, row))
                records.append("/".join(unit.table_path) + f"/row:{row}")
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
        "reference_ranges": [],
    }
    # A relation may identify an object in prose far from its original mention.
    # Use the overlap of its source and independent binding as the seed, for this
    # exact object revision. Retrieve the same statement, not its parent's scope
    # or neighboring records. Every proposed property still needs its own binding.
    for relation in bound_relationships or []:
        if (
            relation.kind != "relationship"
            or not relation.positive_eligible
            or relation.review_status == "rejected"
            or relation.object.candidate_id != subject.candidate_id
            or relation.object.revision != subject.revision
        ):
            continue
        for binding in relation.bindings:
            if binding.object_candidate_id != subject.candidate_id:
                continue
            for source in document_anchors(relation):
                ir.resolve(source)
                for bound in binding.anchors:
                    ir.resolve(bound)
                    if source.evidence_id != bound.evidence_id:
                        continue
                    length = len(ir.unit(source.evidence_id).text)
                    start = max(source.span_start or 0, bound.span_start or 0)
                    end = min(
                        source.span_end if source.span_end is not None else length,
                        bound.span_end if bound.span_end is not None else length,
                    )
                    if start < end:
                        seed = ir.anchor(source.evidence_id, start, end)
                        start, end = _statement_bounds(
                            ir.unit(source.evidence_id).text, start, end
                        )
                        payload["ranges"].append(EvidenceRange(
                            evidence_id=source.evidence_id, start=start, end=end,
                        ))
                        continuation = _reference_continuations(
                            ir, seed, end, subject, competitors or [],
                        )
                        payload["ranges"].extend(continuation)
                        payload["reference_ranges"].extend(continuation)
                        payload["construction_evidence"].append(seed)
                        payload["record_ids"].append(
                            f"relationship:{relation.candidate_id}:{relation.revision}"
                        )
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
