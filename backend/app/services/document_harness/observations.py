"""Lossless original values and bounded, domain-independent range projections."""

from __future__ import annotations

import re

from .source import missing, references_cover

NUMBER = r"[+-]?(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][+-]?\d+)?"
UNIT = r"[A-Za-zµμ°℃%‰\u3400-\u9fff][A-Za-z0-9µμ°℃%‰/·^²³\u3400-\u9fff]*"
RANGE = re.compile(
    rf"\s*(?P<lower>{NUMBER})\s*(?P<first_unit>(?!至|到|to\b){UNIT})?\s*"
    rf"(?:[-–—~～至到]|\bto\b)\s*(?P<upper>{NUMBER})\s*(?P<unit>{UNIT})?\s*"
)


def value_components(field):
    """Select exact original substrings; range validity and units are checked later."""
    raw = field["value"]
    result = {"whole": {"value": raw, "unit": None}}
    match = RANGE.fullmatch(raw) if not field["missing"] else None
    if match is not None:
        for key in ("lower", "upper"):
            result[key] = {"value": match[key], "unit": None,
                           "span": list(match.span(key))}
    return result


def property_value(field, mapping, *, window, ir):
    """Select an exact value inside this field, preserving the original observation."""
    if mapping.value_component == "span":
        ref = window.resolve(ir, mapping.value_quote)
        if not references_cover(ref, field["value_evidence"]):
            raise ValueError("property_value_quote_outside_field")
        if missing(ref["text"]):
            raise ValueError("missing_source_value")
        return {"value": ref["text"], "unit": None, "evidence": [ref]}
    component = value_components(field).get(mapping.value_component)
    if component is None:
        raise ValueError("range_projection_requires_exact_range")
    if mapping.value_component in {"lower", "upper"}:
        from .source import reference

        refs = field["value_evidence"]
        if len(refs) != 1 or refs[0]["text"] != field["value"]:
            raise ValueError("range_endpoint_location_ambiguous")
        start, end = component["span"]
        ref = reference(ir, refs[0]["source_id"], refs[0]["start"] + start,
                        refs[0]["start"] + end)
        return {"value": component["value"], "unit": None, "evidence": [ref]}
    return {**component, "evidence": field["value_evidence"]}
