"""019 evidence contracts: assertions retain meaning independently of projection."""

import pytest
from pydantic import ValidationError

from app.schemas.evidence import (
    BindingEvidence,
    Candidate,
    CandidateRef,
    DocumentProvenance,
    EvidenceAnchor,
    ExternalRecordProvenance,
    LiteralValue,
    ManualProvenance,
)


def anchor(**overrides):
    data = dict(
        document_hash="a" * 64,
        parser_version="4",
        structure_hash="b" * 64,
        evidence_id="paragraph:1:0",
        section_node_id="section:0",
        block_id="paragraph:1:0",
        span_start=0,
        span_end=8,
    )
    return EvidenceAnchor(**(data | overrides))


def relationship(status="affirmed", **overrides):
    data = dict(
        candidate_id="edge-a-e",
        kind="relationship",
        subject=CandidateRef(candidate_id="drug-a", revision=1),
        object=CandidateRef(candidate_id="equipment-e", revision=1),
        predicate_iri="urn:test:usesEquipment",
        assertion_status=status,
        provenance=[DocumentProvenance(anchors=[anchor()])],
        bindings=[BindingEvidence(
            method="explicit_assertion",
            subject_candidate_id="drug-a",
            predicate_iri="urn:test:usesEquipment",
            object_candidate_id="equipment-e",
            anchors=[anchor()],
        )],
        validation_status="passed",
    )
    if status == "conditional":
        data["condition_anchors"] = [anchor(span_end=2)]
    return Candidate(**(data | overrides))


def test_anchor_requires_complete_nonempty_half_open_span():
    for values in ({"span_start": -1}, {"span_end": 0}, {"span_end": None}):
        with pytest.raises(ValidationError):
            anchor(**values)
    assert anchor(span_start=None, span_end=None).span_start is None


def test_sources_are_discriminated_and_extra_fields_rejected():
    with pytest.raises(ValidationError):
        DocumentProvenance(anchors=[anchor()], record_version="not-a-document-field")
    with pytest.raises(ValidationError):
        ExternalRecordProvenance(
            system="archive", dataset="equipment", record_key="E",
            record_version="", field_path="material", value="steel",
        )


def test_external_value_has_record_provenance_without_word_anchor():
    source = ExternalRecordProvenance(
        system="archive", dataset="equipment", record_key="E",
        record_version="v2", field_path="material", value="steel",
    )
    result = Candidate(
        candidate_id="material-e", kind="property",
        subject=CandidateRef(candidate_id="equipment-e", revision=1),
        predicate_iri="urn:test:material",
        literal=LiteralValue(kind="text", raw_value="steel", normalized_value="steel"),
        provenance=[source],
        bindings=[BindingEvidence(
            method="external_mapping", subject_candidate_id="equipment-e",
            predicate_iri="urn:test:material", provenance_indexes=[0],
            record_mapping={"record_key": "E", "field_path": "material"},
        )],
    )
    assert result.scope is None
    assert result.model_dump(mode="json")["provenance"][0]["kind"] == "external_record"


def test_entity_without_properties_is_a_valid_independent_candidate():
    entity = Candidate(
        candidate_id="drug-a", kind="entity", class_iri="urn:test:Drug",
        text="药品 A", provenance=[DocumentProvenance(anchors=[anchor()])],
    )
    assert entity.literal is None
    assert entity.kind == "entity"


@pytest.mark.parametrize("status", ["negated", "conditional"])
def test_supported_negative_and_conditional_assertions_can_pass_without_projection(status):
    candidate = relationship(status)
    assert candidate.validation_status == "passed"
    assert not candidate.positive_eligible
    restored = Candidate.model_validate(candidate.model_dump(mode="json"))
    assert restored.assertion_status == status
    assert not restored.positive_eligible


def test_affirmed_requires_validated_binding_before_positive_projection():
    assert relationship().positive_eligible
    assert not relationship(validation_status="pending").positive_eligible
    with pytest.raises(ValidationError):
        relationship(bindings=[])
    with pytest.raises(ValidationError):
        relationship(provenance=[])


def test_property_requires_a_subject_and_relationship_requires_two_endpoints():
    with pytest.raises(ValidationError):
        Candidate(
            candidate_id="value", kind="property", predicate_iri="urn:test:strength",
            literal=LiteralValue(kind="number", raw_value="250", normalized_value="250"),
        )
    with pytest.raises(ValidationError):
        relationship(object=None)


def test_candidate_cannot_claim_a_competing_subject_binding():
    with pytest.raises(ValidationError):
        relationship(bindings=[BindingEvidence(
            method="explicit_assertion", subject_candidate_id="drug-b",
            predicate_iri="urn:test:usesEquipment", object_candidate_id="equipment-e",
            anchors=[anchor()],
        )])


def test_template_sample_is_not_a_production_fact_source():
    with pytest.raises(ValidationError):
        relationship(provenance=[DocumentProvenance(
            document_role="template_sample", anchors=[anchor()],
        )])


def test_manual_value_requires_an_actor_and_an_audit_record():
    with pytest.raises(ValidationError):
        ManualProvenance(actor="", review_id="", value="steel", reason="correction")
