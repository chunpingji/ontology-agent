"""Replay quantity units and their physical binding before registered normalization."""

import re
from unicodedata import normalize

from app.services.extraction.literal_normalizer import canonical_unit
from app.services.extraction.ontology_guided.field_bindings import contains
from app.services.extraction.ontology_guided.source_citations import resolve_fragment_quote

_NUMBER = r"[+＋\-−－]?[\d]+(?:[.．][\d]+)?(?:[eE][+\-]?\d+)?"
_GAP = re.compile(rf"\s*(?:[\-–—~～至到]\s*{_NUMBER}\s*)?")
_TOKEN = r"(?:[^\W\d_]|[°℃%％])+[\d²³]*"
_FOLLOWING = re.compile(
    rf"\s*(?:[\-–—~～至到]\s*{_NUMBER}\s*)?({_TOKEN}(?:\s*[/／]\s*{_TOKEN})*)",
)


def replay_unit_binding(context, endpoint, binding_id, source_quote, support_quotes):
    """Return (raw unit, precise source refs, issue), without inferring facts.

    Independent semantic unit support is checked by the adapter. This gate
    prevents even a supported review from borrowing another row/column/unit.
    """
    if not source_quote:
        return None, [], "unit_source_missing"
    unit, raw_unit = resolve_fragment_quote(
        source_quote["evidence_id"], source_quote["text"], context.fragments,
        context_text=source_quote.get("context_text"),
    )
    supports = [resolve_fragment_quote(
        q["evidence_id"], q["text"], context.fragments,
    )[0] for q in support_quotes]
    refs = []  # Keep stable source order, including the exact unit span.
    for ref in [unit, *supports]:
        if ref not in refs:
            refs.append(ref)
    if not any(contains(ref, unit) for ref in supports) or not any(
        contains(ref, endpoint) for ref in supports
    ):
        return raw_unit, refs, "unit_binding_source_missing"

    def fragment_for(ref):
        return next(f for f in context.fragments if contains(f.anchor, ref))

    value_fragment = fragment_for(endpoint)
    unit_fragment = fragment_for(unit)
    value_start = endpoint.span_start - (value_fragment.anchor.span_start or 0)
    value_end = endpoint.span_end - (value_fragment.anchor.span_start or 0)
    unit_start = unit.span_start - (unit_fragment.anchor.span_start or 0)
    unit_end = unit.span_end - (unit_fragment.anchor.span_start or 0)
    # Neither a numeric substring (3.8 inside 13.8) nor a partial unit (kg in
    # kg/day or kgf) may be normalized into an apparently valid scalar.
    if (value_start and re.match(r"[\d.．]", value_fragment.text[value_start - 1])) or (
        value_end < len(value_fragment.text)
        and re.match(r"[\d.．]", value_fragment.text[value_end])
    ):
        return raw_unit, refs, "quantity_value_partial"
    for adjacent in (unit_fragment.text[max(0, unit_start - 1):unit_start],
                     unit_fragment.text[unit_end:].lstrip()[:1]):
        if adjacent and re.fullmatch(r"[A-Za-zａ-ｚＡ-Ｚµμ/／²³^力]", adjacent):
            return raw_unit, refs, "unit_quote_partial"
    if unit_fragment.text[unit_end:unit_end + 1].isdigit():
        return raw_unit, refs, "unit_quote_partial"

    inline = (unit.evidence_id == endpoint.evidence_id and unit_start >= value_end
              and _GAP.fullmatch(value_fragment.text[value_end:unit_start]))
    if not inline:
        binding = next((b for b in context.field_bindings
                        if b.field_binding_id == binding_id
                        and any(contains(r, endpoint) for r in b.target_value_refs)), None)
        if binding is None or not any(contains(label, unit) for label in binding.label_refs):
            return raw_unit, refs, "unit_record_mismatch"

    # An explicit suffix takes part in the check even when a review selected
    # a header. It cannot be silently relabelled with the header's unit.
    explicit = _FOLLOWING.match(normalize("NFKC", value_fragment.text[value_end:]))
    if explicit and canonical_unit(explicit[1]) != canonical_unit(raw_unit):
        return raw_unit, refs, "source_unit_conflict"
    return raw_unit, refs, None
