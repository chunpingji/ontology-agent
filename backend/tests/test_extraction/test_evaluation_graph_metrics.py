"""Evaluation must expose omissions and unsupported bindings, not self-validation."""

from copy import deepcopy

import pytest

from app.evaluation.graph_metrics import evaluate_graph


def anchor(evidence_id="u1", start=0, end=5):
    return {"document_hash": "doc", "structure_hash": "structure", "parser_version": "v1",
            "evidence_id": evidence_id, "section_node_id": "s1", "block_id": "b1",
            "paragraph_index": 0, "fragment_index": 0, "span_start": start, "span_end": end}


def entity(candidate_id="a", text="R-101", evidence_id="u1", **changes):
    return {"candidate_id": candidate_id, "revision": 1, "kind": "entity", "text": text,
            "class_iri": "urn:Equipment", "validation_status": "passed",
            "provenance": [{"kind": "document", "anchors": [anchor(evidence_id)]}], **changes}


def assertion(kind="property", subject="a", **changes):
    result = {"candidate_id": "p1", "revision": 1, "kind": kind,
              "subject": {"candidate_id": subject, "revision": 1},
              "predicate_iri": "urn:volume", "validation_status": "passed",
              "provenance": [{"kind": "document", "anchors": [anchor(start=6, end=11)]}]}
    if kind == "property":
        result["literal"] = {"kind": "number", "normalized_value": "500", "raw_value": "500 L",
                             "canonical_unit": "L", "operator": "eq"}
    else:
        result["object"] = {"candidate_id": "b", "revision": 1}
        result["predicate_iri"] = "urn:uses"
    return {**result, **changes}


def reference():
    return {
        "schema_version": 1, "annotation_level": "assistant_silver", "document_hash": "doc",
        "entities": [
            {"id": "equipment1", "class_iri": "urn:Equipment", "aliases": ["R-101"],
             "evidence": [{"evidence_id": "u1", "start": 0, "end": 5}]},
            {"id": "equipment2", "class_iri": "urn:Equipment", "aliases": ["R-102"],
             "evidence": [{"evidence_id": "u2", "start": 0, "end": 5}]},
        ],
        "properties": [{"id": "volume1", "subject": "equipment1", "predicate_iri": "urn:volume",
                        "literal": {"kind": "number", "normalized_value": "500",
                                    "canonical_unit": "L"},
                        "evidence": [{"evidence_id": "u1", "start": 6, "end": 11}]}],
        "relationships": [], "forbidden": [],
        "scopes": [{"id": "scope1", "evidence_ids": ["u1", "u2"], "exhaustive": {
            "entity_classes": ["urn:Equipment"], "property_predicates": ["urn:volume"],
            "relationship_predicates": ["urn:uses"],
        }}],
    }


def source_ir():
    return {"document_hash": "doc", "structure_hash": "structure", "parser_version": "v1",
            "evidence_units": [
                {"evidence_id": unit, "text": f"{name} 500 L", "section_node_id": "s1",
                 "block_id": "b1", "paragraph_index": 0, "fragment_index": 0}
                for unit, name in (("u1", "R-101"), ("u2", "R-102"))]}


def score(candidates, expected=None, **kwargs):
    return evaluate_graph({"candidates": candidates}, expected or reference(), **kwargs)


def test_without_reference_there_is_no_semantic_accuracy():
    result = evaluate_graph({"candidates": [entity()]}, None, ir=source_ir())
    assert not result["semantic_metrics_available"]
    assert result["raw"] is None
    assert result["provenance_replay"]["raw"]["replay_rate"] == 1


def test_exact_identity_and_one_to_one_duplicates_do_not_inflate_recall():
    result = score([entity(), entity("copy"), entity("merged", "R-101 / R-102")])["raw"]["entity"]
    assert (result["tp"], result["fp"], result["fn"]) == (1, 2, 1)
    assert result["false_positives"][0]["reason"] == "duplicate_reference_assertion"


def test_out_of_scope_predictions_are_unscored_not_false_positives():
    result = score([entity(), entity("other", "R-999", "u99")])["raw"]["entity"]
    assert (result["tp"], result["fp"], result["fn"]) == (1, 0, 1)
    assert result["unscored_candidate_ids"] == ["other"]


def test_wrong_subject_property_is_false_positive_even_when_value_is_correct():
    result = score([entity(), entity("b", "R-102", "u2"), assertion(subject="b")])
    assert (result["raw"]["property"]["tp"], result["raw"]["property"]["fp"],
            result["raw"]["property"]["fn"]) == (0, 1, 1)


def test_decimal_equivalence_preserves_unit_and_operator():
    candidate = assertion()
    candidate["literal"]["normalized_value"] = "500.00"
    assert score([entity(), candidate])["raw"]["property"]["tp"] == 1
    candidate["literal"]["canonical_unit"] = "mL"
    assert score([entity(), candidate])["raw"]["property"]["tp"] == 0
    candidate["literal"].update(canonical_unit="L", operator="lt")
    assert score([entity(), candidate])["raw"]["property"]["tp"] == 0


def test_rejected_candidate_still_scores_raw_but_not_validated():
    result = score([entity(), assertion(validation_status="rejected")])
    assert result["raw"]["property"]["tp"] == 1
    assert result["validated"]["property"]["tp"] == 0
    assert result["validated"]["property"]["fn"] == 1
    assert "assistant silver" in result["interpretation"]


def test_relationship_endpoints_direction_and_negation_are_scored():
    expected = reference()
    expected["relationships"] = [{"id": "rel1", "subject": "equipment1", "object": "equipment2",
                                   "predicate_iri": "urn:uses", "assertion_status": "negated"}]
    candidates = [entity(), entity("b", "R-102", "u2"), assertion("relationship")]
    result = score(candidates, expected)["raw"]["relationship"]
    assert (result["tp"], result["fp"], result["fn"]) == (0, 1, 1)
    candidates[-1]["assertion_status"] = "negated"
    assert score(candidates, expected)["raw"]["relationship"]["tp"] == 1
    candidates[-1]["subject"], candidates[-1]["object"] = (
        candidates[-1]["object"], candidates[-1]["subject"])
    assert score(candidates, expected)["raw"]["relationship"]["tp"] == 0


def test_explicit_negative_is_scored_without_exhaustive_scope():
    expected = reference()
    expected["scopes"] = []
    expected["forbidden"] = [{"id": "forbidden1", "kind": "relationship", "subject": "equipment1",
                              "object": "equipment2", "predicate_iri": "urn:uses"}]
    result = score([entity(), entity("b", "R-102", "u2"), assertion("relationship")], expected)
    assert result["raw"]["relationship"]["fp"] == 1
    assert result["raw"]["relationship"]["negative_cases"]["violated_count"] == 1


def test_provenance_replay_does_not_certify_semantic_correctness():
    candidate = assertion(subject="b")
    result = score([entity(), entity("b", "R-102", "u2"), candidate], ir=source_ir())
    assert result["provenance_replay"]["raw"]["replay_rate"] == 1
    assert result["raw"]["property"]["tp"] == 0
    candidate["provenance"][0]["anchors"][0]["structure_hash"] = "wrong"
    result = score([candidate], ir=source_ir())
    assert result["provenance_replay"]["raw"]["invalid_anchor_count"] == 1


def test_evidence_disambiguates_repeated_same_name_subjects():
    expected = reference()
    expected["entities"][1]["aliases"] = ["R-101"]
    result = score([entity(), entity("b", "R-101", "u2"), assertion(subject="b")], expected)
    assert result["raw"]["entity"]["tp"] == 2
    assert result["raw"]["property"]["tp"] == 0


def test_reference_hash_and_intervals_are_validated():
    expected = reference()
    expected["document_hash"] = "different-document"
    with pytest.raises(ValueError, match="document_hash"):
        score([], expected, ir=source_ir())
    expected = reference()
    expected["entities"][0]["evidence"][0]["end"] = 1000
    with pytest.raises(ValueError, match="outside"):
        score([], expected, ir=source_ir())


def test_stale_revision_cannot_bind_to_current_entity():
    candidate = assertion()
    candidate["subject"]["revision"] = 2
    assert score([entity(), candidate])["raw"]["property"]["tp"] == 0


def test_matching_maximizes_pairs_instead_of_greedy_alias_order():
    expected = reference()
    expected["entities"][0]["aliases"] = ["R-101", "R-102"]
    for item in expected["entities"]:
        item.pop("evidence")
    result = score([entity("b", "R-102"), entity()], expected)["raw"]["entity"]
    assert result["tp"] == 2


def test_duplicate_assertions_remain_false_positives_without_closed_scope():
    expected = reference()
    expected["scopes"] = []
    duplicate = deepcopy(assertion())
    duplicate["candidate_id"] = "duplicate"
    result = score([entity(), assertion(), duplicate], expected)["raw"]["property"]
    assert (result["tp"], result["fp"]) == (1, 1)


def test_document_root_requires_source_hash_not_synthetic_title_guess():
    expected = reference()
    expected["entities"][0]["is_document_root"] = True
    candidate = entity(text="synthetic document title", identity={"document_root": "doc"})
    result = score([candidate], expected)
    assert result["raw"]["entity"]["tp"] == 1
    assert result["supplied_metadata_entity_count"] == 1
    candidate["identity"]["document_root"] = "different"
    assert score([candidate], expected)["raw"]["entity"]["tp"] == 0


def test_source_record_matching_requires_the_specific_identity_evidence():
    expected = reference()
    expected["entities"][0]["identity_mode"] = "source_record"
    candidate = entity(text="generated equipment label")
    assert score([candidate], expected)["raw"]["entity"]["tp"] == 1
    candidate["provenance"][0]["anchors"][0].update(span_start=6, span_end=11)
    assert score([candidate], expected)["raw"]["entity"]["tp"] == 0


def test_primary_metrics_exclude_supplied_root_but_preserve_assertion_endpoints():
    expected = reference()
    expected["entities"][0]["is_document_root"] = True
    candidate = entity(identity={"document_root": "doc"})
    result = score([candidate, assertion()], expected)
    assert result["raw"]["entity"]["tp"] == 1
    assert result["raw"]["extracted_only"]["entity"]["tp"] == 0
    assert result["raw"]["extracted_only"]["entity"]["reference_count"] == 1
    assert result["raw"]["extracted_only"]["property"]["tp"] == 1
    assert result["raw"]["extracted_only"]["micro"]["tp"] == 1


def test_root_only_reference_has_no_primary_extracted_accuracy():
    expected = reference()
    expected["entities"] = [expected["entities"][0]]
    expected["entities"][0]["is_document_root"] = True
    expected["properties"] = []
    candidate = entity(identity={"document_root": "doc"})
    result = score([candidate], expected)["raw"]["extracted_only"]
    assert result["entity"]["reference_count"] == 0
    assert result["micro"]["tp"] == result["micro"]["fp"] == result["micro"]["fn"] == 0
    assert result["micro"]["precision"] is None
    assert result["micro"]["recall"] is None
    assert result["micro"]["f1"] is None


def test_passed_property_cannot_bind_to_rejected_entity_in_validated_metrics():
    result = score([entity(validation_status="rejected"), assertion()])
    assert result["raw"]["property"]["tp"] == 1
    assert result["validated"]["property"]["tp"] == 0
    assert result["validated"]["property"]["fn"] == 1
    failure = result["validated"]["binding_resolution"]["unresolved_assertions"][0]
    assert failure["endpoints"][0]["reason"] == "endpoint_missing_from_evaluated_subset"


def test_duplicate_endpoint_keys_are_unresolved_and_unscored_outside_closed_scope():
    expected = reference()
    expected["scopes"] = []
    result = score([entity(), entity(), assertion()], expected)["raw"]
    assert result["property"]["tp"] == 0
    assert result["property"]["fn"] == 1
    assert result["property"]["unscored_count"] == 1
    failure = result["binding_resolution"]["unresolved_assertions"][0]
    assert failure["endpoints"][0]["reason"] == "duplicate_candidate_identity_revision"
