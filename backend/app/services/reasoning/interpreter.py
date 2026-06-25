"""Declarative rule interpreter (research.md R1).

A *generic, bounded* interpreter that evaluates the declarative classification
criteria / decision-rule antecedents authored as restricted-vocabulary pattern
ASTs (data-model.md §C). It deliberately is **not** a full DL reasoner
(constitution V: minimal deps): the `owl:equivalentClass`/facet axioms are still
written to the authoritative TTL as portable, auditable, future-DL-ready truth,
but at runtime they are executed by this Python interpreter over a small,
closed vocabulary that is provably sufficient for the current rule set.

OWA / three-valued semantics (FR-010 / SC-006): evaluation returns one of
`TRUE / FALSE / UNKNOWN`. A datum required by a pattern that is *absent* yields
`UNKNOWN` — it MUST NOT collapse to `FALSE`. A class is only lit when its
criterion evaluates `TRUE`; `UNKNOWN` neither lights the class nor asserts a
negative. This is the semantic home of the expected "否→未知" improvement.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any

# --------------------------------------------------------------------------- #
# Three-valued (Kleene) truth
# --------------------------------------------------------------------------- #
class Ternary(enum.Enum):
    TRUE = "TRUE"
    FALSE = "FALSE"
    UNKNOWN = "UNKNOWN"

    def __bool__(self) -> bool:  # guard: forbid accidental truthiness collapse
        raise TypeError(
            "Ternary must not be coerced to bool — compare against "
            "Ternary.TRUE explicitly (OWA: UNKNOWN != FALSE)."
        )


TRUE = Ternary.TRUE
FALSE = Ternary.FALSE
UNKNOWN = Ternary.UNKNOWN


def t_and(values: list[Ternary]) -> Ternary:
    """Kleene conjunction: FALSE if any FALSE; TRUE if all TRUE; else UNKNOWN."""
    if any(v is FALSE for v in values):
        return FALSE
    if all(v is TRUE for v in values):
        return TRUE
    return UNKNOWN


def t_or(values: list[Ternary]) -> Ternary:
    """Kleene disjunction: TRUE if any TRUE; FALSE if all FALSE; else UNKNOWN."""
    if any(v is TRUE for v in values):
        return TRUE
    if all(v is FALSE for v in values):
        return FALSE
    return UNKNOWN


# --------------------------------------------------------------------------- #
# Fact view consumed by the interpreter
# --------------------------------------------------------------------------- #
@dataclass
class Facts:
    """A normalised view of the ABox individual(s) under assessment.

    Built by the engine from the drug + API individuals. Absence is represented
    by a missing key (object/data) so the interpreter can return UNKNOWN rather
    than fabricating a negative.
    """

    drug_classes: list[str] = field(default_factory=list)
    # object property short-name -> related individuals' class names / IRIs
    relations: dict[str, list[str]] = field(default_factory=dict)
    # datatype property short-name -> value (key ABSENT == not asserted == UNKNOWN)
    data_values: dict[str, Any] = field(default_factory=dict)
    # API alignment: relation property -> external class IRIs the API aligns to
    alignments: dict[str, list[str]] = field(default_factory=dict)
    # generic scalar facts for production literal_eq / literal_cmp (R-CP/R-SC)
    scalars: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Restricted-mode vocabulary: op -> required field names (T006)
# --------------------------------------------------------------------------- #
_REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "some_values_from": ("property", "filler_class"),
    "class_membership": ("property", "classes"),
    "datatype_facet": ("property", "cmp", "value"),
    "boolean_has_value": ("property", "value"),
    "external_alignment": ("property", "alignment"),
    "class_present": ("class",),
    "literal_eq": ("key", "value"),
    "literal_cmp": ("key", "cmp", "value"),
    "and": ("operands",),
    "or": ("operands",),
}
_CMP_OPS = ("gt", "ge", "lt", "le", "eq", "ne")
VOCABULARY = frozenset(_REQUIRED_FIELDS)


class PatternError(ValueError):
    """Raised when a pattern AST is outside the restricted-mode vocabulary."""


def validate_pattern(node: Any, *, _path: str = "$") -> None:
    """Validate a pattern AST against the restricted vocabulary (T006, FR-004/14).

    Raises PatternError on an unknown `op`, a missing required field, or a bad
    comparator. Recurses into `and`/`or` operands.
    """
    if not isinstance(node, dict):
        raise PatternError(f"{_path}: pattern node must be an object, got {type(node).__name__}")
    op = node.get("op")
    if op not in _REQUIRED_FIELDS:
        raise PatternError(f"{_path}: unknown op {op!r} (allowed: {sorted(VOCABULARY)})")
    for field_name in _REQUIRED_FIELDS[op]:
        if field_name not in node or node[field_name] is None:
            raise PatternError(f"{_path}.{op}: missing required field {field_name!r}")
    if op in ("datatype_facet", "literal_cmp") and node["cmp"] not in _CMP_OPS:
        raise PatternError(f"{_path}.{op}: bad cmp {node['cmp']!r} (allowed: {list(_CMP_OPS)})")
    if op == "class_membership" and not isinstance(node["classes"], list):
        raise PatternError(f"{_path}.class_membership: 'classes' must be a list")
    if op in ("and", "or"):
        operands = node["operands"]
        if not isinstance(operands, list) or not operands:
            raise PatternError(f"{_path}.{op}: 'operands' must be a non-empty list")
        for i, child in enumerate(operands):
            validate_pattern(child, _path=f"{_path}.{op}[{i}]")


# --------------------------------------------------------------------------- #
# Evaluation (T007 core + T015/T024 op handlers)
# --------------------------------------------------------------------------- #
def _name_match(value: Any, name: str) -> bool:
    """Mirror the legacy `name in str(value)` membership test."""
    return name in str(value)


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _cmp(left: float, op: str, right: float) -> bool:
    return {
        "gt": left > right,
        "ge": left >= right,
        "lt": left < right,
        "le": left <= right,
        "eq": left == right,
        "ne": left != right,
    }[op]


def evaluate(node: dict, facts: Facts) -> Ternary:
    """Evaluate a (pre-validated) pattern node against `facts` → Ternary."""
    op = node["op"]

    if op == "and":
        return t_and([evaluate(c, facts) for c in node["operands"]])
    if op == "or":
        return t_or([evaluate(c, facts) for c in node["operands"]])

    if op == "some_values_from":
        # ∃ property . filler_class  — existential over asserted relation values.
        prop = node["property"]
        if prop not in facts.relations:
            return UNKNOWN  # relation not asserted → cannot prove existence (OWA)
        values = facts.relations[prop]
        if any(_name_match(v, node["filler_class"]) for v in values):
            return TRUE
        return UNKNOWN  # no asserted match, but open world cannot deny it

    if op == "class_membership":
        prop = node["property"]
        if prop not in facts.relations:
            return UNKNOWN
        values = facts.relations[prop]
        names = node["classes"]
        if any(_name_match(v, n) for v in values for n in names):
            return TRUE
        return UNKNOWN

    if op == "datatype_facet":
        if node["property"] not in facts.data_values:
            return UNKNOWN
        raw = facts.data_values[node["property"]]
        if raw is None:
            return UNKNOWN
        left, right = _to_float(raw), _to_float(node["value"])
        if left is None or right is None:
            return UNKNOWN
        return TRUE if _cmp(left, node["cmp"], right) else FALSE

    if op == "boolean_has_value":
        if node["property"] not in facts.data_values:
            return UNKNOWN
        raw = facts.data_values[node["property"]]
        if raw is None:
            return UNKNOWN
        return TRUE if bool(raw) == bool(node["value"]) else FALSE

    if op == "external_alignment":
        # API (via `property`) aligns to an external class (ChEBI/ATC IRI).
        prop = node["property"]
        if prop not in facts.alignments:
            return UNKNOWN
        if any(_name_match(a, node["alignment"]) for a in facts.alignments[prop]):
            return TRUE
        return UNKNOWN

    if op == "class_present":
        if not facts.drug_classes:
            return UNKNOWN
        return TRUE if any(_name_match(c, node["class"]) for c in facts.drug_classes) else FALSE

    if op == "literal_eq":
        if node["key"] not in facts.scalars:
            return UNKNOWN
        raw = facts.scalars[node["key"]]
        if raw is None:
            return UNKNOWN
        return TRUE if raw == node["value"] else FALSE

    if op == "literal_cmp":
        if node["key"] not in facts.scalars:
            return UNKNOWN
        raw = facts.scalars[node["key"]]
        if raw is None:
            return UNKNOWN
        left, right = _to_float(raw), _to_float(node["value"])
        if left is not None and right is not None:
            return TRUE if _cmp(left, node["cmp"], right) else FALSE
        # non-numeric eq/ne fallback (e.g. dosage-form string comparison)
        if node["cmp"] == "eq":
            return TRUE if raw == node["value"] else FALSE
        if node["cmp"] == "ne":
            return TRUE if raw != node["value"] else FALSE
        return UNKNOWN

    raise PatternError(f"no evaluator for op {op!r}")  # unreachable post-validate


def referenced_facts(node: dict, facts: Facts) -> dict[str, Any]:
    """Collect the fact values a pattern references, for `rules_fired.inputs`
    provenance (FR-002 / SC-005). Recurses through and/or operands."""
    out: dict[str, Any] = {}
    op = node["op"]
    if op in ("and", "or"):
        for child in node["operands"]:
            out.update(referenced_facts(child, facts))
        return out
    if op in ("some_values_from", "class_membership"):
        prop = node["property"]
        out[prop] = facts.relations.get(prop, [])
    elif op in ("datatype_facet", "boolean_has_value"):
        prop = node["property"]
        out[prop] = facts.data_values.get(prop)
    elif op == "external_alignment":
        prop = node["property"]
        out[prop] = facts.alignments.get(prop, [])
    elif op == "class_present":
        out["drug_classes"] = list(facts.drug_classes)
    elif op in ("literal_eq", "literal_cmp"):
        out[node["key"]] = facts.scalars.get(node["key"])
    return out
