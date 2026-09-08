import pytest

from app.services.extraction.literal_normalizer import LiteralNormalizationError, normalize_literal


@pytest.mark.parametrize(
    "raw,value,unit",
    [
        ("250 mg", "250", "mg"),
        ("0.25g", "0.25", "g"),
        ("−1.2e-3 kg", "-0.0012", "kg"),
        ("5", "5", None),
        ("１．５ mg", "1.5", "mg"),
    ],
)
def test_decimal_numbers_preserve_raw_and_do_not_invent_units(raw, value, unit):
    literal = normalize_literal(raw, datatype="decimal")
    assert literal.raw_value == raw
    assert literal.normalized_value == value
    assert literal.canonical_unit == unit


def test_ranges_comparisons_and_explicit_conversion():
    value = normalize_literal("1–2 mg", datatype="decimal")
    assert (value.kind, value.lower, value.upper) == ("range", "1", "2")
    value = normalize_literal("≤0.5 g", datatype="decimal", target_unit="mg")
    assert (value.kind, value.operator, value.normalized_value) == ("comparison", "le", "500")
    assert value.conversion_record["factor"] == "1000"
    assert value.raw_unit == "g"


@pytest.mark.parametrize("raw", ["五毫克", "NaN", "1abc", "1..2mg", "2–1 mg", "大概一些"])
def test_unknown_numeric_grammar_is_rejected_without_business_rules(raw):
    with pytest.raises(LiteralNormalizationError):
        normalize_literal(raw, datatype="decimal")


def test_text_boolean_and_date_are_distinct_and_unknown_unit_cannot_be_assumed():
    assert normalize_literal("5", datatype="string").kind == "text"
    assert normalize_literal("true", datatype="boolean").normalized_value is True
    assert normalize_literal("2026-09-05", datatype="date").normalized_value == "2026-09-05"
    with pytest.raises(LiteralNormalizationError, match="unit"):
        normalize_literal("5", datatype="decimal", target_unit="mg")


@pytest.mark.parametrize(
    "raw,expected", [("2026年6月", "2026-06"), ("2026年02月", "2026-02"), ("2026-09", "2026-09")]
)
def test_year_month_preserves_precision_and_original_evidence(raw, expected):
    value = normalize_literal(raw, datatype="http://www.w3.org/2001/XMLSchema#gYearMonth")
    assert value.raw_value == raw and value.normalized_value == expected
    assert value.datatype_iri.endswith("#gYearMonth")


@pytest.mark.parametrize(
    "raw", ["2026年13月", "0000-01", "2026-00", "2026-09-01", "2026年", "明年6月"]
)
def test_invalid_or_incomplete_year_month_is_not_guessed(raw):
    with pytest.raises(LiteralNormalizationError, match="year-month"):
        normalize_literal(raw, datatype="gYearMonth")


@pytest.mark.parametrize("raw,expected", [("是", True), ("否", False)])
def test_explicit_chinese_booleans(raw, expected):
    assert normalize_literal(raw, datatype="boolean").normalized_value is expected
    with pytest.raises(LiteralNormalizationError, match="boolean"):
        normalize_literal("可能" + raw, datatype="boolean")
