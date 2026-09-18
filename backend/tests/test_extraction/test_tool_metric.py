import pytest

from app.services.extraction.ontology_guided.contracts import SlotSpec
from app.services.extraction.ontology_guided.value_constraints import XSD
from app.services.extraction.tool_validation.metric import validate_metric
from app.services.extraction.tool_validation.shacl import (
    build_metric_graph,
    claim_node,
    validate_graph,
)


def slot(unit="mg/day", datatype="decimal", iri="urn:predicate:pde"):
    return SlotSpec(
        iri=iri, label="PDE", datatype_iris=[XSD + datatype], canonical_unit=unit,
    )


def checked(raw, schema=None, **kwargs):
    return validate_metric(
        raw, schema or slot(), binding_issues=[], semantic_status="supported", **kwargs,
    )


def test_source_alias_and_exact_conversion_require_external_semantic_review():
    preflight = validate_metric("0.1", slot(), source_unit="g/天", binding_issues=[])
    assert preflight["validation_status"] == "incomplete"
    assert preflight["quantity"]["source_value"] == "0.1"
    assert preflight["normalized_value"] is None
    assert preflight["conversion_record"] is None
    assert preflight["shacl"]["conforms"] is None
    result = checked("0.1", source_unit="g/天")
    assert result["validation_status"] == "passed"
    assert result["normalized_value"] == "100"
    assert result["conversion_record"]["factor"] == "1000"
    assert result["conversion_record"]["to"] == "mg/day"
    assert result["fact_eligible"] is False


@pytest.mark.parametrize("raw", ["100", "7.5"])
def test_pde_values_remain_separate_candidates_and_business_upper_limits_are_not_shapes(raw):
    result = checked(raw, source_unit="mg/天", candidate_id="pde-" + raw)
    assert result["normalized_value"] == raw
    assert result["shacl"]["conforms"] is True
    assert result["shacl"]["coverage"]["complete"] is True


def test_target_card_cannot_invent_missing_source_units_for_noael():
    result = checked("200", slot("mg/kg/day"))
    assert result["validation_status"] == "incomplete"
    assert result["normalized_value"] is None
    assert result["source_unit"] is None
    assert "unit_source_missing" in result["issues"]


@pytest.mark.parametrize("raw,source_unit", [("0.2%", None), ("200", "mg/kg/day")])
def test_incompatible_dimension_does_not_convert(raw, source_unit):
    result = checked(raw, slot("ug"), source_unit=source_unit)
    assert result["validation_status"] == "failed"
    assert result["normalized_value"] is None
    assert "unit_missing_or_incompatible" in result["issues"]


def test_identical_dimensions_never_prove_metric_meaning():
    result = validate_metric(
        "100 mg/day", slot(), binding_issues=[], semantic_status="refuted",
    )
    assert result["unit_checks"]["status"] == "passed"
    assert result["validation_status"] == "failed"
    assert result["normalized_value"] is None
    assert result["shacl"]["evaluated"] is False


def test_unchecked_or_rejected_binding_cannot_be_overridden_by_semantics():
    unchecked = validate_metric("100 mg/day", slot(), semantic_status="supported")
    assert unchecked["validation_status"] == "incomplete"
    assert "binding_not_checked" in unchecked["issues"]
    rejected = validate_metric(
        "100 mg/day", slot(), binding_issues=["owner_row_mismatch"], semantic_status="supported",
    )
    assert rejected["validation_status"] == "failed"
    assert rejected["normalized_value"] is None


@pytest.mark.parametrize(
    "raw,kind,operator,lower,upper",
    [("≤25℃", "comparison", "le", None, None), ("3.8–6.6 kg", "range", "eq", "3.8", "6.6")],
)
def test_comparison_and_range_are_retained_not_coerced_into_scalar(
    raw, kind, operator, lower, upper,
):
    result = checked(raw, slot("°C" if kind == "comparison" else "kg"))
    assert result["validation_status"] == "incomplete"
    assert result["raw_value"] == raw
    assert result["quantity"]["kind"] == kind
    assert result["quantity"]["operator"] == operator
    assert result["quantity"]["source_lower"] == lower
    assert result["quantity"]["source_upper"] == upper
    assert result["normalized_value"] is None
    assert "scalar_value_required" in result["issues"]


def test_denominator_or_inline_source_conflict_is_rejected():
    result = checked("100 mg/kg/day", slot("mg/kg/day"), source_unit="mg")
    assert result["validation_status"] == "failed"
    assert "source_unit_conflict" in result["issues"]


def test_nonterminating_unit_conversion_never_silently_rounds():
    result = checked("1 s", slot("min"))
    assert result["validation_status"] == "incomplete"
    assert "unit_conversion_not_exact" in result["issues"]
    assert result["unit_checks"]["status"] == "incomplete"
    assert result["normalized_value"] is None


def test_unknown_registry_unit_stays_unresolved():
    result = checked("5 IU", slot("mg"))
    assert result["validation_status"] == "incomplete"
    assert "unit_unknown" in result["issues"]


def test_integer_slot_cannot_accept_fractional_converted_value():
    result = checked("500 mg", slot("g", "integer"))
    assert result["validation_status"] == "failed"
    assert result["normalized_value"] is None
    assert "datatype_mismatch" in result["issues"]


def test_partial_month_and_unknown_boolean_preserve_source_precision():
    result = checked("2026年6月", slot(None, "gYearMonth"))
    assert result["normalized_value"] == "2026-06"
    assert result["raw_value"] == "2026年6月"
    unknown = checked("—", slot(None, "boolean"))
    assert unknown["normalized_value"] is None
    assert unknown["raw_value"] == "—"


def test_tool_and_independent_shacl_entrypoint_have_identical_results():
    schema = slot()
    result = checked("100", schema, source_unit="mg/天", candidate_id="rat")
    direct = validate_graph(
        build_metric_graph(candidate_id="rat", raw="100", slot=schema, value="100", unit="mg/day"),
        slot=schema, expected_focus_nodes=[str(claim_node("rat"))],
    )
    assert result["shacl"] == direct


def test_technical_shacl_failure_is_not_a_data_failure(monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("validator unavailable")

    monkeypatch.setattr("pyshacl.validate", fail)
    result = checked("100", source_unit="mg/day")
    assert result["execution_status"] == "failed"
    assert result["validation_status"] == "incomplete"
    assert result["shacl"]["conforms"] is None
    assert result["normalized_value"] is None
