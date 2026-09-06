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
