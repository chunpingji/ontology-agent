"""Lossless original values and bounded, domain-independent range projections."""

from __future__ import annotations

import re
from decimal import Decimal

from .source import missing, references_cover

NUMBER = r"[+-]?(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][+-]?\d+)?"
UNIT = r"[A-Za-zµμ°℃%‰\u3400-\u9fff][A-Za-z0-9µμ°℃%‰/·^²³\u3400-\u9fff]*"
RANGE = re.compile(
    rf"\s*(?P<lower>{NUMBER})\s*(?P<first_unit>(?!至|到|to\b){UNIT})?\s*"
    rf"(?:[-–—~～至到]|\bto\b)\s*(?P<upper>{NUMBER})\s*(?P<unit>{UNIT})?\s*"
)


def value_components(field, *, confirmed):
    """Never replace the original range or derive values for an unconfirmed subject.

    Only a whole, unambiguous two-number range is eligible. Unit conversion and
    predicate semantics are deliberately not inferred here. The independent
    review sees both the original range and the selected component's unit.
    """
    raw = field["value"]
    result = {"whole": {"value": raw, "unit": None}}
    match = RANGE.fullmatch(raw) if confirmed and not field["missing"] else None
    if match is None:
        return result
    if any(len(match[key]) > 64 for key in ("lower", "upper")):
        return result
    lower, upper = Decimal(match["lower"]), Decimal(match["upper"])
    if (max(abs(lower.adjusted()), abs(upper.adjusted())) > 300
            or lower > upper
            or (match["first_unit"] and match["first_unit"] != match["unit"])):
        return result
    for key, number in (("lower", lower), ("upper", upper)):
        result[key] = {"value": format(number, "f"), "unit": match["unit"]}
    return result


def property_value(field, mapping, *, window, ir, confirmed):
    """Select an exact value inside this field, preserving the original observation."""
    if mapping.value_component == "span":
        ref = window.resolve(ir, mapping.value_quote)
        if not references_cover(ref, field["value_evidence"]):
            raise ValueError("property_value_quote_outside_field")
        if missing(ref["text"]):
            raise ValueError("missing_source_value")
        return {"value": ref["text"], "unit": None, "evidence": [ref]}
    component = value_components(field, confirmed=confirmed).get(mapping.value_component)
    if component is None:
        raise ValueError("range_projection_requires_confirmed_subject_and_exact_range")
    return {**component, "evidence": field["value_evidence"]}
