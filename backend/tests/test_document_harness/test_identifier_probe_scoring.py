"""Evaluation must not score invented labels or cross-source values as correct bindings."""

from copy import deepcopy

from app.evaluation.document_identifier_probe import fixture, score_discovery


def test_scoring_checks_all_quotes_not_only_the_identifier_value(tmp_path):
    text = "设备编号A7。"
    ir, window = fixture(tmp_path, "quote-check", [text, "另一台设备编号B9。"])
    output = {
        "entities": [{"candidate_class_iri": "urn:test:Equipment",
                      "local_id": "a", "anchor": {"source_id": "S1", "text": "A7"},
                      "name": None, "role": "设备", "evidence": ["S1"], "field_ids": [],
                      "source_fields": [{"label": None,
                                         "value": {"source_id": "S1", "text": "A7"}}]}],
        "document_field_ids": [], "document_source_fields": [], "unowned_fields": [],
        "relation_hints": [], "complete": True,
    }
    reference = [("case", text, "urn:test:Equipment", [["A7"]])]
    assert score_discovery(output, reference, ir, window)["case"]["passed"]
    invalid = deepcopy(output)
    invalid["entities"][0]["source_fields"][0]["label"] = {
        "source_id": "S1", "text": "资产编号",
    }
    result = score_discovery(invalid, reference, ir, window)["case"]
    assert result["count_ok"] and not result["passed"]
    assert result["errors"] == ["source_quote_mismatch"]
    invalid = deepcopy(output)
    invalid["entities"][0]["source_fields"][0]["value"] = {
        "source_id": "S2", "text": "B9",
    }
    result = score_discovery(invalid, reference, ir, window)["case"]
    assert not result["passed"]
    assert result["errors"] == ["fixture_field_assigned_across_independent_sources"]
    empty = {**output, "entities": []}
    result = score_discovery(empty, reference, ir, window)["case"]
    assert not result["passed"] and not result["locatable_nonoverlapping"]
