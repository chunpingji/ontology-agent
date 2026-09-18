"""Mechanical tool checks cannot create semantic proof or normalized facts."""

from copy import deepcopy

import pytest

from app.services.extraction.ontology_guided.contracts import SlotSpec
from app.services.extraction.tool_validation.evidence import (
    EMPTY,
    check_claim_binding,
    get_schema_card,
    inspect_evidence,
    resolve_citation,
)
from app.services.extraction.tool_validation.metric import validate_metric

STUDY = "urn:test:Study"


def citation(ref, quote, **kwargs):
    return {"ref": ref, "quote": quote, **kwargs}


def source(ref, text, row=None, column=None, *, headers=(), rows=None):
    return {"ref": ref, "text": text, "evidence_id": ref,
            "table_path": ["table"] if row is not None else None,
            "row_index": row, "column_index": column,
            "logical_rows": rows or ([row] if row is not None else []),
            "column_indices": [column] if column is not None else [],
            "is_header": row == 0, "cell_structure_valid": row is not None,
            "column_header_refs": list(headers)}


@pytest.fixture
def data():
    sources = {s["ref"]: s for s in [
        source("label", "PDE", 0, 2), source("unit", "(mg/天)", 0, 2),
        source("rat", "大鼠重复给药试验", 1, 1),
        source("dog", "犬重复给药试验", 2, 1),
        source("v1", "100", 1, 2, headers=("label", "unit")),
        source("v2", "7.5", 2, 2, headers=("label", "unit")),
        source("api", "HRS-1597", 1, 0, rows=[1, 2]),
    ]}
    subjects = {"s1": {"class_iri": STUDY, "anchor": citation("rat", "大鼠重复给药试验")},
                "s2": {"class_iri": STUDY, "anchor": citation("dog", "犬重复给药试验")}}
    prop = {"iri": "urn:test:pde", "label": "PDE", "kind": "property",
            "canonical_unit": "mg/day", "constraint_status": "resolved"}
    bindings = {subject: {"pde": prop} for subject in subjects}
    candidate = {"id": "claim1", "kind": "property", "subject_id": "s1", "field": "pde",
                 "proposal": {"raw": "100", "source": citation("v1", "100"),
                              "field": citation("label", "PDE"),
                              "unit": citation("unit", "(mg/天)"), "condition": EMPTY}}
    return sources, subjects, bindings, candidate


def checked(data):
    sources, subjects, bindings, candidate = data
    return check_claim_binding(candidate, subjects, bindings, sources)


def test_pde_source_binding_preserves_raw_and_does_not_claim_semantic_proof(data):
    result = checked(data)
    assert result["validation_status"] == "passed", result
    assert result["source_unit"] == "mg/天"
    assert result["fact"]["raw_value"] == "100"
    assert "value" not in result["fact"]
    assert result["semantic_status"] == "not_checked"
    assert result["fact_eligible"] is False


def test_shared_api_cell_does_not_make_pde_values_cross_row(data):
    data[3]["proposal"].update(raw="7.5", source=citation("v2", "7.5"))
    assert "owner_row_mismatch" in checked(data)["issues"]
    data[3]["subject_id"] = "s2"
    assert checked(data)["validation_status"] == "passed"
    data[3]["proposal"].update(raw="100", source=citation("v1", "100"))
    assert "owner_row_mismatch" in checked(data)["issues"]


def test_header_unit_cannot_be_truncated_at_denominator(data):
    data[0]["unit"]["text"] = "mg/kg/day"
    data[3]["proposal"]["unit"] = citation("unit", "mg")
    assert "unit_quote_partial" in checked(data)["issues"]


def test_missing_source_unit_is_incomplete_never_inferred_from_card(data):
    data[3]["proposal"]["unit"] = EMPTY
    result = checked(data)
    assert result["validation_status"] == "incomplete"
    assert result["source_unit"] is None


def test_coordinates_disambiguate_repeated_temperature_unit():
    sources = {"u": source("u", "温度先20℃再25℃")}
    with pytest.raises(ValueError, match="citation_quote_ambiguous"):
        resolve_citation(sources, citation("u", "℃"))
    assert resolve_citation(sources, citation("u", "℃", start=9, end=10))["start"] == 9
    subjects = {"document": {"class_iri": "urn:test:Document", "anchor": EMPTY}}
    prop = {"iri": "urn:test:temperature", "label": "温度", "kind": "property",
            "constraint_status": "resolved", "canonical_unit": "°C"}
    candidate = {"id": "c", "kind": "property", "subject_id": "document", "field": "temp",
                 "proposal": {"raw": "25", "source": citation("u", "25", start=7, end=9),
                              "field": citation("u", "温度"),
                              "unit": citation("u", "℃", start=9, end=10), "condition": EMPTY}}
    result = check_claim_binding(candidate, subjects, {"document": {"temp": prop}}, sources)
    assert result["validation_status"] == "passed", result
    candidate["proposal"]["unit"] = citation("u", "℃", start=5, end=6)
    assert "unit_not_bound_to_value" in check_claim_binding(
        candidate, subjects, {"document": {"temp": prop}}, sources,
    )["issues"]


@pytest.mark.parametrize("condition", ["PDE", "100", "(mg/天)"])
def test_header_value_or_unit_cannot_be_a_condition(data, condition):
    ref = {"PDE": "label", "100": "v1", "(mg/天)": "unit"}[condition]
    data[3]["proposal"]["condition"] = citation(ref, condition)
    assert "condition_role_unproven" in checked(data)["issues"]


def test_invented_condition_quote_is_rejected(data):
    data[3]["proposal"]["condition"] = citation("v1", "如果超标则重做")
    assert "citation_quote_not_in_source" in checked(data)["issues"]


def test_real_conditional_clause_remains_a_semantic_question(data):
    data[0]["condition"] = source("condition", "若不符合则重复重结晶", 1, 3)
    data[3]["proposal"]["condition"] = citation("condition", "若不符合则重复重结晶")
    result = checked(data)
    assert result["validation_status"] == "passed", result
    assert result["semantic_status"] == "not_checked"


def test_inspection_does_not_promote_actions_or_headers_to_legends(data):
    data[0]["v1"]["text"] = "采用HPLC检测，若不符合则重结晶"
    data[0]["legend"] = source("legend", "“—”代表研究数据不充分")
    result = inspect_evidence(data[0], ["v1", "legend"])
    units = {unit["ref"]: unit for unit in result["units"]}
    assert not units["v1"]["legend_candidate"]
    assert units["legend"]["legend_candidate"]
    assert not units["label"]["legend_candidate"]
    assert "dog" not in units
    assert units["v1"]["spans"][0]["quote"] == data[0]["v1"]["text"]


@pytest.mark.parametrize("raw,text", [("10", "100"), ("25", "不超过25℃"),
                                     ("3.8", "3.8–6.6 kg"), ("6.6", "3.8–6.6 kg")])
def test_numeric_substrings_and_discarded_comparisons_fail(data, raw, text):
    data[0]["v1"]["text"] = text
    data[3]["proposal"].update(raw=raw, source=citation("v1", text))
    result = checked(data)
    assert result["validation_status"] == "failed"
    assert set(result["issues"]) & {"numeric_substring_not_full_value", "scalar_value_required"}


def test_relation_direction_and_range_are_frozen_but_cooccurrence_is_not_proof(data):
    sources, subjects, bindings, _ = data
    subjects["document"] = {"class_iri": "urn:test:Document", "anchor": EMPTY}
    bindings["document"] = {"hasStudy": {"iri": "urn:test:hasStudy", "label": "试验",
                                        "kind": "relationship", "constraint_status": "resolved",
                                        "range_class_iris": [STUDY]}}
    candidate = {"id": "r", "kind": "relation", "subject_id": "document", "field": "hasStudy",
                 "proposal": {"target_id": "s1", "evidence": citation("rat", "大鼠重复给药试验"),
                              "condition": EMPTY, "polarity": "affirmed"}}
    result = check_claim_binding(candidate, subjects, bindings, sources)
    assert result["validation_status"] == "passed"
    assert result["fact_eligible"] is False
    subjects["s1"]["class_iri"] = "urn:test:Wrong"
    assert "range_mismatch" in check_claim_binding(candidate, subjects, bindings, sources)["issues"]
    candidate["subject_id"] = "s1"
    assert "subject_or_predicate_outside_scope" in check_claim_binding(
        candidate, subjects, bindings, sources,
    )["issues"]


def test_schema_access_is_scoped_and_card_is_not_mutable_evidence():
    card = {"class_iri": STUDY, "properties": []}
    catalog = {STUDY: card}
    assert get_schema_card(catalog, STUDY, [])["validation_status"] == "failed"
    result = get_schema_card(catalog, STUDY, [STUDY])
    result["card"]["properties"].append("changed")
    assert card == {"class_iri": STUDY, "properties": []}
    assert result["is_source_evidence"] is False


def test_tools_do_not_mutate_source_or_candidate(data):
    original = deepcopy(data)
    checked(data)
    inspect_evidence(data[0], ["v1"])
    assert data == original


def test_procedural_table_cell_does_not_require_a_fabricated_header(data):
    data[0]["v1"]["column_header_refs"] = []
    data[0]["v1"]["text"] = "质量标准：若不符合则重复重结晶"
    data[2]["s1"]["pde"]["canonical_unit"] = None
    data[3]["proposal"].update(
        raw="若不符合则重复重结晶", source=citation("v1", "质量标准：若不符合则重复重结晶"),
        field=citation("v1", "质量标准"), unit=EMPTY,
    )
    result = checked(data)
    assert result["validation_status"] == "passed", result
    assert result["semantic_status"] == "not_checked"


def test_empty_citation_can_include_null_optional_coordinates(data):
    data[3]["proposal"]["condition"] = {**EMPTY, "start": None, "end": None}
    assert checked(data)["validation_status"] == "passed"


def test_wrong_column_field_cannot_use_its_valid_quote(data):
    data[0]["other"] = source("other", "NOAEL", 0, 3)
    data[3]["proposal"]["field"] = citation("other", "NOAEL")
    assert checked(data)["validation_status"] == "failed"


def test_inline_suffix_conflicting_with_header_is_rejected(data):
    data[0]["v1"]["text"] = "100 kg"
    assert "source_unit_conflict" in checked(data)["issues"]


@pytest.mark.parametrize("ref,raw", [("v1", "100"), ("v2", "7.5")])
def test_shared_multirow_owner_does_not_identify_either_metric_record(data, ref, raw):
    data[1]["s1"]["anchor"] = citation("api", "HRS-1597")
    data[3]["proposal"].update(raw=raw, source=citation(ref, raw))
    result = checked(data)
    assert result["validation_status"] == "incomplete", result
    assert result["issues"] == ["owner_record_ambiguous"]
    assert result["fact_eligible"] is False


@pytest.mark.parametrize("same_unit", [True, False])
def test_shared_api_own_name_with_same_cell_and_span_remains_bindable(data, same_unit):
    data[1]["s1"]["anchor"] = citation("api", "HRS-1597")
    data[0]["api"]["source_cell_id"] = "shared-api-cell"
    ref = "api"
    if not same_unit:
        ref = "api-second-paragraph"
        data[0][ref] = {**data[0]["api"], "ref": ref, "evidence_id": ref}
    data[2]["s1"]["pde"].update(canonical_unit=None, label="名称")
    data[3]["proposal"].update(raw="HRS-1597", source=citation(ref, "HRS-1597"),
                                field=citation(ref, "HRS-1597"), unit=EMPTY)
    result = checked(data)
    assert result["validation_status"] == "passed", result
    assert result["semantic_status"] == "not_checked"


@pytest.mark.parametrize("subject,ref,raw", [("s1", "v1", "100"), ("s2", "v2", "7.5")])
def test_single_row_study_owner_keeps_its_own_pde(data, subject, ref, raw):
    data[3]["subject_id"] = subject
    data[3]["proposal"].update(raw=raw, source=citation(ref, raw))
    result = checked(data)
    assert result["validation_status"] == "passed", result
    assert result["source_unit"] == "mg/天"


@pytest.mark.parametrize("text,raw,expected_issue", [
    ("温度不超过25℃", "25℃", "scalar_value_required"),
    ("温度50~55℃", "55℃", "scalar_value_required"),
    ("温度[1,2]℃", "1", "scalar_value_required"),
    ("温度(1,2]℃", "2", "scalar_value_required"),
    ("温度[2,1]℃", "1", "scalar_value_required"),
    ("温度［1，2）℃", "2", "scalar_value_required"),
    ("温度25℃", "5℃", "numeric_substring_not_full_value"),
    ("温度25℃", "25℃", None),
    ("温度不超过25℃", "不超过25℃", None),
    ("温度≤25℃", "≤25℃", None),
    ("温度50~55℃", "50~55℃", None),
    ("温度[1,2)℃", "[1,2)℃", None),
])
def test_quantity_units_cannot_bypass_source_boundaries_before_metric_check(
    text, raw, expected_issue,
):
    sources = {"u": source("u", text)}
    subjects = {"document": {"class_iri": "urn:test:Document", "anchor": EMPTY}}
    prop = {"iri": "urn:test:temperature", "label": "温度", "kind": "property",
            "constraint_status": "resolved", "canonical_unit": "°C",
            "datatype_iris": ["http://www.w3.org/2001/XMLSchema#decimal"]}
    candidate = {"id": "c", "kind": "property", "subject_id": "document", "field": "temp",
                 "proposal": {"raw": raw, "source": citation("u", text),
                              "field": citation("u", "温度"), "unit": citation("u", "℃"),
                              "condition": EMPTY}}
    binding = check_claim_binding(candidate, subjects, {"document": {"temp": prop}}, sources)
    metric = validate_metric(
        raw, SlotSpec.model_validate(prop), source_unit=binding["source_unit"],
        binding_issues=binding["issues"], semantic_status="supported", candidate_id="c",
    )
    if expected_issue:
        assert expected_issue in binding["issues"]
        assert binding["validation_status"] == "failed"
        assert metric["validation_status"] != "passed"
        assert metric["normalized_value"] is None
    else:
        assert binding["validation_status"] == "passed", binding
        if raw == "25℃":
            assert metric["validation_status"] == "passed", metric
            assert metric["normalized_value"] == "25"
        elif raw == "不超过25℃":
            # The existing numeric grammar does not parse this Chinese phrase.
            # Keep it verbatim and fail scalar representation, never return 25.
            assert metric["validation_status"] == "failed", metric
            assert metric["raw_value"] == raw
            assert metric["normalized_value"] is None
        else:
            assert metric["validation_status"] == "incomplete", metric
            assert metric["quantity"]["kind"] in {"range", "comparison"}
            assert metric["normalized_value"] is None
