"""Parse source syntax before binding or semantic acceptance.

This observation deliberately carries no accepted/passed flag. Its datatype
describes the source expression and cannot supply missing calendar precision,
units, ownership, or a business predicate.
"""

from __future__ import annotations

import re
from datetime import date
from types import SimpleNamespace

from app.schemas.attribute_value import ParsedAttributeValue
from app.services.extraction.literal_normalizer import (
    LiteralNormalizationError,
)
from app.services.extraction.literal_normalizer import (
    normalize_literal as parse_literal,
)
from app.services.extraction.ontology_guided.value_constraints import (
    NUMERIC_DATATYPES,
    XSD,
    normalize_literal,
)


def parse_attribute_value(
    raw: str, *, expected_datatype: str | None = None, source_unit: str | None = None,
) -> ParsedAttributeValue:
    """Infer a bounded source grammar; expected type is a constraint, not proof."""
    text = raw.strip()
    value = ParsedAttributeValue()
    if not text:
        value.issues.append("value_empty")
        return value
    if expected_datatype == XSD + "string":
        # Identifiers and text slots must retain their lexical identity.
        return ParsedAttributeValue(value=text, datatype_iri=XSD + "string")
    month = re.fullmatch(r"\d{4}(?:-\d{2}|年\d{1,2}月)", text)
    day = re.fullmatch(r"(\d{4})(?:-(\d{2})-(\d{2})|年(\d{1,2})月(\d{1,2})日)", text)
    if month or day:
        value.datatype_iri = XSD + ("gYearMonth" if month else "date")
        value.precision = "month" if month else "day"
        try:
            if month:
                value.value = parse_literal(text, datatype="gYearMonth").normalized_value
            else:
                value.value = date(
                    int(day[1]), int(day[2] or day[4]), int(day[3] or day[5]),
                ).isoformat()
        except (ValueError, LiteralNormalizationError):
            value.issues.append("datatype_mismatch")
    elif text.casefold() in {"true", "false", "是", "否"} or (
        expected_datatype == XSD + "boolean" and text in {"0", "1"}
    ):
        value.value = text.casefold() in {"true", "是", "1"}
        value.datatype_iri = XSD + "boolean"
    else:
        try:
            parsed = parse_literal(text, datatype="decimal", source_unit=source_unit)
        except LiteralNormalizationError as exc:
            numeric_start = re.match(r"[+\-\d.≤≥<>≈~\[(]|约|大于|小于|不超过", text)
            value.value = None if numeric_start else text
            value.datatype_iri = None if numeric_start else XSD + "string"
            if numeric_start:
                value.issues.append(str(exc))
        else:
            datatype = XSD + ("integer" if re.fullmatch(r"[+-]?\d+", text) else "decimal")
            if expected_datatype in NUMERIC_DATATYPES:
                slot = SimpleNamespace(
                    datatype_iris=[expected_datatype], constraint_status="resolved",
                    canonical_unit=None,
                )
                numbers = ([parsed.lower, parsed.upper] if parsed.kind == "range"
                           else [parsed.normalized_value])
                if all(normalize_literal(number, slot)[1] is None for number in numbers):
                    datatype = expected_datatype
                else:
                    value.issues.append("datatype_mismatch")
            value.datatype_iri = datatype
            value.value = parsed.normalized_value if parsed.kind == "number" else None
            lower_bound = parsed.kind == "comparison" and parsed.operator in {"gt", "ge"}
            upper_bound = parsed.kind == "comparison" and parsed.operator in {"lt", "le"}
            value.quantity = {
                "kind": parsed.kind, "operator": parsed.operator,
                "scalar": (parsed.normalized_value
                           if parsed.kind == "number" or parsed.operator == "approx" else None),
                "lower": parsed.normalized_value if lower_bound else parsed.lower,
                "upper": parsed.normalized_value if upper_bound else parsed.upper,
                "lower_inclusive": (parsed.operator == "ge" if lower_bound else
                                    parsed.lower_inclusive if parsed.kind == "range" else None),
                "upper_inclusive": (parsed.operator == "le" if upper_bound else
                                    parsed.upper_inclusive if parsed.kind == "range" else None),
                "source_unit": parsed.raw_unit, "dimension": parsed.dimension,
            }
            if parsed.operator == "approx":
                value.issues.append("quantity_approximate")
    if (expected_datatype is not None and expected_datatype != value.datatype_iri
            and "datatype_mismatch" not in value.issues):
        value.issues.append("datatype_mismatch")
    return value


def normalize_field_support(proposal, *, context, index):
    """Narrow one complete inline field citation to its unique structural label.

    The caller has copied the model proposal. The original model response stays
    intact; only this server-derived frozen citation is narrowed. A neighboring
    field, a different row, an ambiguous quote, or an unauthorized value is never
    repaired by searching elsewhere in the document.
    """
    from app.services.extraction.ontology_guided.claim_protocol import Quote
    from app.services.extraction.ontology_guided.field_bindings import contains, field_bindings
    from app.services.extraction.ontology_guided.source_citations import resolve_fragment_quote

    bindings = context.field_bindings or field_bindings(index, context.record_id)

    def anchor(quote, *, fact_required=False):
        result, _ = resolve_fragment_quote(
            quote.evidence_id, quote.text, context.fragments, context_text=quote.context_text,
            fact_required=fact_required,
        )
        if index.ir.resolve(result) != quote.text:
            raise ValueError("source_excerpt_mismatch")
        return result

    for prop in proposal.properties:
        if len(prop.field_support) != 1:
            continue
        try:
            value = anchor(prop.value_quote, fact_required=True)
            original = anchor(prop.field_support[0])
        except ValueError:
            continue
        matching = [binding for binding in bindings if binding.kind == "field_group"
                    and len(binding.label_refs) == len(binding.target_value_refs) == 1
                    and contains(binding.target_value_refs[0], value)]
        if len(matching) != 1:
            continue
        binding = matching[0]
        label, target = binding.label_refs[0], binding.target_value_refs[0]
        if (any(ref.span_start is None for ref in (label, target, value, original))
                or label.evidence_id != value.evidence_id
                or original.evidence_id != value.evidence_id
                or not contains(original, label) or not contains(original, value)
                or original.span_start != label.span_start
                or original.span_end > target.span_end):
            continue
        raw = index.ir.resolve(original)
        label_text = index.ir.resolve(label)
        separator = raw[label.span_end - original.span_start:value.span_start - original.span_start]
        suffix = raw[value.span_end - original.span_start:]
        if ("\n" in raw or "\r" in raw or raw.count(label_text) != 1
                or re.fullmatch(r"\s*[:：]\s*", separator) is None or suffix.strip()):
            continue
        if any(
            other.field_binding_id != binding.field_binding_id
            and ref.evidence_id == original.evidence_id
            and (ref.span_start is None
                 or ref.span_start < original.span_end and ref.span_end > original.span_start)
            for other in bindings for ref in other.label_refs
        ):
            continue
        narrowed = Quote(evidence_id=label.evidence_id, text=label_text, context_text=raw)
        try:
            if anchor(narrowed) != label:
                continue
        except ValueError:
            continue
        prop.field_support = [narrowed]
    return proposal
