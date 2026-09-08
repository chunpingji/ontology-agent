"""Validation boundary for human entries and allowlisted structured sources."""

from datetime import datetime, timezone

from app.schemas.evidence import (
    BindingEvidence,
    Candidate,
    DocumentProvenance,
    ExternalRecordProvenance,
    ManualProvenance,
    ValidationIssue,
)
from app.services.extraction.document_ir import DocumentIR
from app.services.extraction.evidence_identity import evidence_hash, stable_id
from app.services.extraction.external_records import ExternalRecordRegistry
from app.services.extraction.literal_normalizer import normalize_literal
from app.services.extraction.semantic_binding import validate_document_candidate


def validate_entered_candidate(
    candidate: Candidate,
    candidates: dict[str, Candidate],
    schema: dict,
    *,
    actor: str,
    reason: str,
    records: ExternalRecordRegistry,
    ir: DocumentIR | None = None,
) -> Candidate:
    payload = candidate.model_dump(mode="json")
    payload.update(
        validation_status="pending",
        validation_issues=[],
        review_status="pending",
        review_source=None,
        review_reason="",
        commit_status="not_requested",
        ontology_release=evidence_hash(schema),
        model_identity="structured-or-manual-v1",
    )
    candidate = Candidate.model_validate(payload)
    if any(isinstance(source, DocumentProvenance) for source in candidate.provenance):
        if ir is None:
            raise ValueError("document analysis unavailable; re-extract the source")
        # A reviewer editing text does not magically become a semantic model.
        # Document candidates must be re-extracted or explicitly entered as a
        # manual assertion. A forged passed flag cannot bypass this boundary.
        return validate_document_candidate(
            candidate,
            ir,
            candidates,
            semantic_supported=False,
            allowed_predicates={
                p["iri"]
                for c in schema.values()
                for key in ("properties", "relationships")
                for p in c.get(key, [])
            },
            allowed_classes=set(schema),
        )
    for ref in [candidate.subject, candidate.object, *candidate.dependency_refs]:
        if ref is None:
            continue
        target = candidates.get(ref.candidate_id)
        if (
            target is None
            or target.revision != ref.revision
            or target.kind != "entity"
            or not target.positive_eligible
        ):
            raise ValueError("missing, stale or ineligible endpoint")
        if ref.class_iri and ref.class_iri != target.class_iri:
            raise ValueError("endpoint class mismatch")
        if ref.instance_iri:
            raise ValueError("client cannot assign endpoint instance identity")
    predicate = None
    if candidate.kind == "entity":
        if candidate.class_iri not in schema:
            raise ValueError("unknown entity class")
    else:
        subject = candidates[candidate.subject.candidate_id]
        key = "properties" if candidate.kind == "property" else "relationships"
        predicate = next(
            (
                p
                for p in schema[subject.class_iri].get(key, [])
                if p["iri"] == candidate.predicate_iri
            ),
            None,
        )
        if predicate is None:
            raise ValueError("unsupported predicate or domain")
        if candidate.object:
            target = candidates[candidate.object.candidate_id]
            if not (
                {target.class_iri, *schema[target.class_iri].get("parents", [])}
                & set(predicate.get("range", []))
            ):
                raise ValueError("predicate range mismatch")
        if candidate.literal:
            payload["literal"] = normalize_literal(
                candidate.literal.raw_value,
                datatype=predicate.get("datatype") or "string",
                target_unit=predicate.get("canonical_unit"),
            ).model_dump(mode="json")
    sources, bindings = [], []
    has_manual = False
    for index, source in enumerate(candidate.provenance):
        if isinstance(source, ManualProvenance):
            has_manual = True
            value = (
                candidate.text
                if candidate.kind == "entity"
                else (
                    candidate.literal.raw_value
                    if candidate.literal
                    else {
                        "subject": candidate.subject.model_dump(),
                        "predicate_iri": candidate.predicate_iri,
                        "object": candidate.object.model_dump(),
                        "assertion_status": candidate.assertion_status,
                        "assertion_text": source.value,
                    }
                )
            )
            sources.append(
                ManualProvenance(
                    actor=actor,
                    review_id=stable_id(
                        "manual-entry",
                        [candidate.candidate_id, candidate.revision, actor, value, reason],
                    ),
                    value=value,
                    reason=reason,
                    entered_at=datetime.now(timezone.utc).isoformat(),
                    previous_value=source.previous_value,
                )
            )
            if candidate.subject:
                bindings.append(
                    BindingEvidence(
                        method="manual_decision",
                        subject_candidate_id=candidate.subject.candidate_id,
                        predicate_iri=candidate.predicate_iri,
                        object_candidate_id=candidate.object.candidate_id
                        if candidate.object
                        else None,
                        provenance_indexes=[index],
                    )
                )
        elif isinstance(source, ExternalRecordProvenance):
            record = records.resolve(source)
            if source.identity_match_evidence:
                if ir is None:
                    raise ValueError("identity match analysis unavailable")
                for anchor in source.identity_match_evidence:
                    # Exact identifier equality is structured identity matching,
                    # not evidence for any external field value.
                    if ir.resolve(anchor) != record.key:
                        raise ValueError("external identity evidence mismatch")
            source = source.model_copy(
                update={
                    "record_snapshot": record.fields,
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            if candidate.kind == "entity":
                if (
                    candidate.text != str(record.fields[record.label_field])
                    or source.field_path != record.label_field
                ):
                    raise ValueError("external entity must use the record identity field")
                if candidate.class_iri != record.class_iri:
                    raise ValueError("external entity class mismatch")
                payload["identity"].update(
                    system=record.system, dataset=record.dataset, record_key=record.key
                )
            else:
                if (
                    candidate.assertion_status != "affirmed"
                    or candidate.condition_provenance_indexes
                    or candidate.condition_anchors
                ):
                    raise ValueError("structured adapter does not declare negation or conditions")
                identity = candidates[candidate.subject.candidate_id].identity
                if (
                    identity.get("system"),
                    identity.get("dataset"),
                    identity.get("record_key"),
                ) != (
                    record.system,
                    record.dataset,
                    record.key,
                ):
                    raise ValueError("external field belongs to a different subject")
                if record.field_predicates.get(source.field_path) != candidate.predicate_iri:
                    raise ValueError("external field has no declared predicate mapping")
                if candidate.literal is None or candidate.literal.raw_value != str(source.value):
                    raise ValueError("external value mismatch or unsupported object mapping")
                bindings.append(
                    BindingEvidence(
                        method="external_mapping",
                        subject_candidate_id=candidate.subject.candidate_id,
                        predicate_iri=candidate.predicate_iri,
                        provenance_indexes=[index],
                        record_mapping={
                            "system": record.system,
                            "dataset": record.dataset,
                            "record_key": record.key,
                            "record_version": record.version,
                            "field_path": source.field_path,
                        },
                    )
                )
            sources.append(source)
        else:
            raise ValueError("derived assertions require an internal versioned derivation executor")
    if candidate.scope is not None:
        raise ValueError("non-document assertions cannot fabricate a Word scope")
    if candidate.condition_anchors:
        if ir is None:
            raise ValueError("condition analysis unavailable")
        for anchor in candidate.condition_anchors:
            ir.resolve(anchor)
    issues = []
    if candidate.assertion_status == "conditional" and not (
        candidate.condition_anchors or candidate.condition_provenance_indexes
    ):
        issues.append(ValidationIssue(code="condition_evidence_missing"))
    if has_manual and not reason.strip():
        raise ValueError("manual assertion requires a reason")
    payload.update(
        provenance=[s.model_dump(mode="json") for s in sources],
        bindings=[b.model_dump(mode="json") for b in bindings],
        validation_status="rejected" if issues else "passed",
        validation_issues=[issue.model_dump() for issue in issues],
    )
    return Candidate.model_validate(payload)
