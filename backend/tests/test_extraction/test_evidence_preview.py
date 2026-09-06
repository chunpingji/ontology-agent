from app.schemas.evidence import (
    BindingEvidence,
    Candidate,
    CandidateRef,
    LiteralValue,
    ManualProvenance,
)
from app.services.extraction.evidence_preview import preview_relationships


def _source(value):
    return ManualProvenance(actor="extractor", review_id="preview", value=value, reason="test")


def test_relationship_preview_includes_target_property_values_and_schema_labels():
    report = Candidate(
        candidate_id="report", kind="entity", class_iri="urn:Report", text="风险评估",
        provenance=[_source("风险评估")], validation_status="passed",
    )
    product = Candidate(
        candidate_id="product", kind="entity", class_iri="urn:Product", text="产品 A",
        provenance=[_source("产品 A")], validation_status="passed",
    )
    strength = Candidate(
        candidate_id="strength", kind="property",
        subject=CandidateRef(candidate_id="product", revision=1),
        predicate_iri="urn:strength",
        literal=LiteralValue(kind="text", raw_value="250 mg", normalized_value="250 mg"),
        provenance=[_source("250 mg")], validation_status="passed",
        bindings=[BindingEvidence(
            method="manual_decision", subject_candidate_id="product",
            predicate_iri="urn:strength", provenance_indexes=[0],
        )],
    )
    describes = Candidate(
        candidate_id="describes", kind="relationship",
        subject=CandidateRef(candidate_id="report", revision=1),
        object=CandidateRef(candidate_id="product", revision=1), predicate_iri="urn:describes",
        provenance=[_source("风险评估描述产品 A")], validation_status="passed",
        bindings=[BindingEvidence(
            method="manual_decision", subject_candidate_id="report",
            object_candidate_id="product", predicate_iri="urn:describes",
            provenance_indexes=[0],
        )],
    )
    schema = {
        "urn:Report": {
            "label": "CMC 报告",
            "relationships": [{"iri": "urn:describes", "label": "描述"}],
        },
        "urn:Product": {
            "label": "药品",
            "properties": [{"iri": "urn:strength", "label": "规格"}],
        },
    }

    result = preview_relationships([report, product, strength, describes], schema)

    assert result[0]["predicate_label"] == "描述"
    assert result[0]["object_data_properties"] == [{
        "iri": "urn:strength", "label": "规格", "value": "250 mg",
        "candidate_id": "strength",
    }]
