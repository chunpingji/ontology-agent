"""Small source-field checks shared by candidate freezing and proof inputs."""

from __future__ import annotations

import re
import unicodedata

from app.services.extraction.ontology_guided.field_bindings import contains, field_bindings

# Exact field values only. In particular NA, none, 0, false, 无 and 否 may be real
# identifiers or values and are not assigned missing semantics by this check.
MISSING_VALUES = frozenset({
    "n/a", "not available", "not applicable", "not provided", "not reported",
    "未提供", "未填写", "不适用", "暂无数据",
})
UNKNOWN_VALUES = frozenset({"unknown", "未知", "未明确", "待确认"})


def missing_value_kind(text: str) -> str | None:
    normalized = " ".join(unicodedata.normalize("NFKC", text).strip().casefold().split())
    if normalized in MISSING_VALUES:
        return "missing"
    if normalized in UNKNOWN_VALUES:
        return "unknown"
    return None


def missing_field_value_kind(*, quote, anchor, context, index) -> str | None:
    """Do not turn a substring in an ordinary value into a missing observation."""
    kind = missing_value_kind(quote.text)
    if kind is None or anchor is None:
        return None
    bindings = context.field_bindings or field_bindings(index, context.record_id)
    values = [value for binding in bindings for value in binding.target_value_refs
              if contains(value, anchor)]
    if values:
        return kind if all(index.ir.resolve(value).strip() == quote.text.strip()
                           for value in values) else None
    # An isolated whole source unit is also a complete value. Embedded words in
    # prose without a structural field boundary are left to semantic verification.
    unit = index.ir.unit(anchor.evidence_id)
    return kind if unit.text.strip() == quote.text.strip() else None


def identity_field_role_mismatch(*, value_anchor, predicate, properties, context, index) -> bool:
    """An explicit non-identity field cannot silently become a unique identity.

    Explicit name fields remain names even when the ontology has no matching
    ordinary name property. A declared identity property with that exact label
    still requires semantic verification; this check never establishes identity.
    """
    if value_anchor is None or not predicate.identity_key:
        return False

    def label(value):
        return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value)).strip(
            " :：\t\n",
        ).casefold()

    bindings = context.field_bindings or field_bindings(index, context.record_id)
    source_labels = {
        label(index.ir.resolve(ref))
        for binding in bindings
        if any(contains(value, value_anchor) for value in binding.target_value_refs)
        for ref in binding.label_refs
    }
    matching = [item for item in properties if label(item.label) in source_labels]
    if not matching and any(
        value.endswith("名称") or re.search(r"(?:^|[\s_])name$", value)
        for value in source_labels
    ):
        return True
    # More than one matching definition is ambiguous, not a deterministic rejection.
    return (len(matching) == 1 and matching[0].iri != predicate.iri
            and not matching[0].identity_key)
