"""Expert review must survey original sources and omissions."""

from copy import deepcopy

import pytest

from app.evaluation.adaptive_expert_review import (
    EXPOSED_DOCUMENTS,
    prepare_package,
    validate_review,
)
from tests.test_extraction.test_semantic_ranking_execution import fixture


def test_expert_review_requires_omission_survey_and_bound_package(tmp_path):
    ontology, args = fixture(tmp_path)
    ir = args["ir"]
    package = prepare_package(
        ir, ontology, root_class_iri=args["root_class_iri"], exposure="development"
    )
    review, reference = package["review"], package["reference"]
    with pytest.raises(ValueError, match="approval"):
        validate_review(package, ir=ir, ontology=ontology, reference=reference, review=review)
    review.update(
        status="approved",
        reviewer="CMC expert",
        reviewed_at="2026-09-11T00:00:00Z",
        annotation_version="1",
    )
    with pytest.raises(ValueError, match="output-only"):
        validate_review(package, ir=ir, ontology=ontology, reference=reference, review=review)
    review["whole_document_surveyed"] = True
    review["checks"] = dict.fromkeys(review["checks"], True)
    for record in review["records"]:
        record["status"] = "reviewed"
    reference["annotation_complete"] = True
    reference["expert_review"] = {
        "status": "approved",
        "reviewer": "CMC expert",
        "reviewed_at": "2026-09-11T00:00:00Z",
        "reference_hash": package["manifest"]["package_hash"],
    }
    result = validate_review(package, ir=ir, ontology=ontology, reference=reference, review=review)
    assert result["expert_reference_ready"] and not result["independent_sample"]
    assert result["formal_quality_gate"] != "passed"
    forged = deepcopy(package)
    forged["records"][0]["context"] = "changed source"
    with pytest.raises(ValueError, match="hashes"):
        validate_review(forged, ir=ir, ontology=ontology, reference=reference, review=review)


def test_exposed_hrs_cannot_be_relabelled_as_holdout(tmp_path):
    ontology, args = fixture(tmp_path)
    ir = args["ir"].model_copy(update={"document_hash": next(iter(EXPOSED_DOCUMENTS))})
    with pytest.raises(ValueError, match="development-exposed"):
        prepare_package(ir, ontology, root_class_iri=args["root_class_iri"], exposure="holdout")
