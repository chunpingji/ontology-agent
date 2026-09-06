"""Evidence/location validation is independent of semantic binding and projection."""

from app.schemas.evidence import Candidate, DocumentProvenance, ValidationIssue
from app.services.extraction.document_ir import DocumentIR
from app.services.extraction.evidence_scope import document_anchors, scope_contains


def validate_document_candidate(
    candidate: Candidate,
    ir: DocumentIR,
    candidates: dict[str, Candidate],
    *,
    semantic_supported: bool,
    allowed_predicates: set[str],
    allowed_classes: set[str] | None = None,
    allowed_binding_regions: list | None = None,
) -> Candidate:
    issues: list[ValidationIssue] = []

    def issue(code):
        if code not in {item.code for item in issues}:
            issues.append(ValidationIssue(code=code))

    if ir.document_role not in {"analysis_source", "default_source"}:
        issue("non_production_source")
    if candidate.kind == "entity":
        if allowed_classes is not None and candidate.class_iri not in allowed_classes:
            issue("unsupported_class")
    else:
        if candidate.predicate_iri not in allowed_predicates:
            issue("unsupported_predicate")
        if not candidate.bindings:
            issue("no_binding")
        if candidate.subject is None:
            issue("no_subject")
        if candidate.scope is None:
            issue("scope_violation")
        elif candidate.scope.subject != candidate.subject:
            issue("scope_subject_mismatch")
    for role, ref in (("subject", candidate.subject), ("object", candidate.object)):
        if ref is None:
            continue
        target = candidates.get(ref.candidate_id)
        if target is None or target.revision != ref.revision:
            issue(f"stale_{role}")
        elif target.kind != "entity" or not target.positive_eligible:
            issue(f"unvalidated_{role}")
        elif ref.class_iri is not None and target.class_iri != ref.class_iri:
            issue(f"invalid_{role}_class")
    excerpts = []
    for ref in candidate.dependency_refs:
        target = candidates.get(ref.candidate_id)
        if target is None or target.revision != ref.revision or not target.positive_eligible:
            issue("stale_or_ineligible_path_dependency")
    for source in candidate.provenance:
        if not isinstance(source, DocumentProvenance):
            issue("wrong_provenance_validator")
            continue
        if source.document_role != ir.document_role:
            issue("source_role_mismatch")
        actual = []
        for anchor in source.anchors:
            try:
                actual.append(ir.resolve(anchor))
                if candidate.kind != "entity" and not scope_contains(candidate.scope, anchor, ir):
                    issue("scope_violation")
            except ValueError:
                issue("invalid_anchor")
        excerpts.extend(actual)
        if source.excerpts and actual != source.excerpts:
            issue("source_excerpt_mismatch")
    if candidate.kind == "entity" and candidate.text not in excerpts:
        issue("entity_span_mismatch")
    if candidate.literal and candidate.literal.raw_value not in excerpts:
        issue("value_mismatch")
    if allowed_binding_regions is None:
        allowed_binding_regions = []
        if candidate.scope:
            from app.services.extraction.evidence_scope import scope_intervals

            for identity, intervals in scope_intervals(candidate.scope, ir).items():
                allowed_binding_regions.extend(
                    ir.anchor(identity, start, end) for start, end in intervals if start < end
                )
        for ref in (candidate.subject, candidate.object):
            if ref and ref.candidate_id in candidates:
                allowed_binding_regions.extend(document_anchors(candidates[ref.candidate_id]))

    def binding_allowed(anchor):
        return any(
            region.evidence_id == anchor.evidence_id
            and (region.span_start or 0) <= (anchor.span_start or 0)
            and (
                anchor.span_end
                if anchor.span_end is not None
                else len(ir.unit(anchor.evidence_id).text)
            )
            <= (
                region.span_end
                if region.span_end is not None
                else len(ir.unit(region.evidence_id).text)
            )
            for region in allowed_binding_regions
        )

    for binding in candidate.bindings:
        if candidate.subject and binding.subject_candidate_id != candidate.subject.candidate_id:
            issue("ambiguous_subject")
        for anchor in binding.anchors:
            try:
                ir.resolve(anchor)
                if not binding_allowed(anchor):
                    issue("binding_region_violation")
            except ValueError:
                issue("invalid_binding_anchor")
        if binding.method == "section_record":
            section_id = binding.record_mapping.get("section_node_id")
            if not section_id or any(a.section_node_id != section_id for a in binding.anchors):
                issue("record_mapping_mismatch")
        if binding.method == "table_record":
            table_path = binding.record_mapping.get("table_path")
            row = binding.record_mapping.get("row_index")
            if (
                not table_path
                or row is None
                or any(a.table_path != table_path or a.row_index != row for a in binding.anchors)
            ):
                issue("record_mapping_mismatch")
    for anchor in candidate.condition_anchors:
        try:
            ir.resolve(anchor)
            if not binding_allowed(anchor):
                issue("condition_region_violation")
        except ValueError:
            issue("invalid_condition_anchor")
    if candidate.assertion_status == "conditional" and not (
        candidate.condition_anchors or candidate.condition_provenance_indexes
    ):
        issue("condition_evidence_missing")
    if not semantic_supported:
        issue("cooccurrence_only" if candidate.kind != "entity" else "unsupported_type")
    # Negation/conditions are valid assertion states, not evidence failures.
    payload = candidate.model_dump(mode="json")
    payload.update(
        validation_status="rejected" if issues else "passed",
        validation_issues=[item.model_dump() for item in issues],
        review_status="pending",
        commit_status="not_requested",
    )
    return Candidate.model_validate(payload)
