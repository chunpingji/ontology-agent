import pytest

from app.schemas.evidence import (
    BindingEvidence,
    Candidate,
    CandidateRef,
    DocumentProvenance,
    LiteralValue,
)
from app.services.extraction.evidence_scope import build_scope
from app.services.extraction.semantic_binding import validate_document_candidate


def property_candidate(ir, subject, status="affirmed"):
    unit = ir.evidence_units[1]
    return Candidate(
        candidate_id="strength",
        kind="property",
        predicate_iri="urn:strength",
        subject=CandidateRef(
            candidate_id=subject.candidate_id, revision=1, class_iri=subject.class_iri
        ),
        literal=LiteralValue(
            kind="number",
            raw_value="250 mg",
            normalized_value="250",
            raw_unit="mg",
            canonical_unit="mg",
        ),
        provenance=[
            DocumentProvenance(anchors=[ir.anchor(unit.evidence_id, 3, 9)], excerpts=["250 mg"])
        ],
        bindings=[
            BindingEvidence(
                method="section_record",
                subject_candidate_id=subject.candidate_id,
                predicate_iri="urn:strength",
                anchors=[ir.anchor(unit.evidence_id)],
                record_mapping={"section_node_id": unit.section_node_id},
            )
        ],
        assertion_status=status,
        scope=build_scope(ir, subject),
    )


def test_supported_negative_validates_without_positive_projection(subject_document):
    ir, subjects = subject_document
    candidate = property_candidate(ir, subjects[0], "negated")
    # Semantic support comes from a separate verifier, not client state or location checks.
    result = validate_document_candidate(
        candidate,
        ir,
        {s.candidate_id: s for s in subjects},
        semantic_supported=True,
        allowed_predicates={"urn:strength"},
    )
    assert result.validation_status == "passed"
    assert not result.positive_eligible


@pytest.mark.parametrize(
    "change,code",
    [
        ({"bindings": []}, "no_binding"),
        ({"predicate_iri": "urn:unknown", "bindings": []}, "unsupported_predicate"),
    ],
)
def test_unbound_or_unsupported_assertions_cannot_pass(subject_document, change, code):
    ir, subjects = subject_document
    candidate = property_candidate(ir, subjects[0]).model_copy(update=change)
    result = validate_document_candidate(
        candidate,
        ir,
        {s.candidate_id: s for s in subjects},
        semantic_supported=True,
        allowed_predicates={"urn:strength"},
    )
    assert result.validation_status == "rejected"
    assert code in {issue.code for issue in result.validation_issues}


def test_location_checks_do_not_replace_independent_binding_verification(subject_document):
    ir, subjects = subject_document
    candidate = property_candidate(ir, subjects[0])
    result = validate_document_candidate(
        candidate,
        ir,
        {s.candidate_id: s for s in subjects},
        semantic_supported=False,
        allowed_predicates={"urn:strength"},
    )
    assert result.validation_status == "rejected"
    assert "cooccurrence_only" in {issue.code for issue in result.validation_issues}


def test_value_from_wrong_section_and_stale_subject_are_rejected(subject_document):
    ir, (a, b) = subject_document
    candidate = property_candidate(ir, a)
    anchor = ir.anchor(ir.evidence_units[3].evidence_id, 3, 9)
    candidate = candidate.model_copy(
        update={
            "provenance": [DocumentProvenance(anchors=[anchor], excerpts=["500 mg"])],
            "subject": CandidateRef(candidate_id=a.candidate_id, revision=2),
        }
    )
    result = validate_document_candidate(
        candidate,
        ir,
        {a.candidate_id: a, b.candidate_id: b},
        semantic_supported=True,
        allowed_predicates={"urn:strength"},
    )
    codes = {issue.code for issue in result.validation_issues}
    assert {"scope_violation", "stale_subject", "value_mismatch"} <= codes
