"""Unit and literal checks with external, explicit semantic verification."""

from __future__ import annotations

from app.services.extraction.literal_normalizer import (
    UNIT_REGISTRY_VERSION,
    LiteralNormalizationError,
    unit_definition,
)
from app.services.extraction.literal_normalizer import normalize_literal as parse_quantity
from app.services.extraction.ontology_guided.contracts import SlotSpec
from app.services.extraction.ontology_guided.value_constraints import XSD, normalize_literal
from app.services.extraction.tool_validation.shacl import (
    build_metric_graph,
    claim_node,
    unevaluated_shacl,
    validate_graph,
)

_NUMERIC = {XSD + name for name in (
    "decimal", "double", "float", "integer", "int", "nonNegativeInteger", "positiveInteger",
)}
_INCOMPLETE = {
    "binding_not_checked", "unit_source_missing", "unit_binding_source_missing",
    "unit_unknown", "constraint_unresolved", "scalar_value_required",
    "unit_conversion_not_exact", "semantic_not_checked", "semantic_undetermined",
    "shacl_coverage_incomplete", "shacl_missing_fields",
    "owner_record_ambiguous",
}


def validate_metric(
    raw: str, slot: SlotSpec, *, source_unit: str | None = None,
    binding_issues: list[str] | None = None, semantic_status: str = "not_checked",
    candidate_id: str = "candidate",
) -> dict:
    """Validate a source-bound candidate, never determine meaning or commit a fact.

    ``binding_issues=None`` means source/owner validation has not run. The
    trusted caller passes an empty list only after all required binding checks.
    The model tool adapter must not accept a model-supplied semantic status.
    """
    issues = list(binding_issues) if binding_issues is not None else ["binding_not_checked"]
    result = {
        "tool": "validate_metric", "candidate_id": candidate_id,
        "execution_status": "completed", "validation_status": "incomplete",
        "raw_value": raw, "source_unit": source_unit,
        "structural_checks": {
            "source_binding": "not_checked" if binding_issues is None else (
                "failed" if binding_issues else "passed"
            ),
            "datatype": "not_checked",
        },
        "unit_checks": {"status": "not_required", "registry_version": UNIT_REGISTRY_VERSION},
        "quantity": None, "normalized_value": None, "conversion_record": None,
        "semantic_status": semantic_status, "fact_eligible": False,
        "shacl": unevaluated_shacl("preconditions_incomplete"), "issues": issues,
    }
    datatypes = set(slot.datatype_iris)
    if slot.constraint_status != "resolved" or len(datatypes) != 1:
        issues.append("constraint_unresolved")
    else:
        datatype = next(iter(datatypes))
        numeric = datatype in _NUMERIC
        if numeric:
            try:
                quantity = parse_quantity(raw, datatype="decimal", source_unit=source_unit)
                result["quantity"] = {
                    "kind": quantity.kind, "operator": quantity.operator,
                    "source_value": quantity.normalized_value,
                    "source_lower": quantity.lower, "source_upper": quantity.upper,
                    "source_unit": quantity.raw_unit, "dimension": quantity.dimension,
                }
                if quantity.kind != "number":
                    issues.append("scalar_value_required")
                if slot.canonical_unit:
                    if quantity.raw_unit is None:
                        issues.append("unit_source_missing")
                    elif unit_definition(slot.canonical_unit)[0] != quantity.dimension:
                        issues.append("unit_missing_or_incompatible")
                elif quantity.raw_unit is not None:
                    # No target unit in the slot means there is no authorized
                    # conversion or removal of the physical unit.
                    issues.append("unit_missing_or_incompatible")
            except LiteralNormalizationError as exc:
                issues.append(str(exc))
        if numeric or slot.canonical_unit:
            unit_issues = [issue for issue in issues if "unit" in issue]
            result["unit_checks"]["status"] = (
                "incomplete" if unit_issues and all(i in _INCOMPLETE for i in unit_issues)
                else "failed" if unit_issues else "passed"
            )
        if not issues:
            if semantic_status == "supported":
                conversion = {}
                value, issue = normalize_literal(
                    raw, slot, source_unit=source_unit, normalization_record=conversion,
                )
                if issue:
                    issues.append(issue)
                    result["structural_checks"]["datatype"] = "failed"
                    if "unit" in issue:
                        result["unit_checks"]["status"] = (
                            "incomplete" if issue in _INCOMPLETE else "failed"
                        )
                else:
                    result["structural_checks"]["datatype"] = "passed"
                    shacl = validate_graph(
                        build_metric_graph(
                            candidate_id=candidate_id, raw=raw, slot=slot, value=value,
                            unit=conversion.get("to"),
                        ),
                        slot=slot, expected_focus_nodes=[str(claim_node(candidate_id))],
                    )
                    result["shacl"] = shacl
                    if shacl["execution_status"] == "failed":
                        result["execution_status"] = "failed"
                    if shacl["validation_status"] == "passed":
                        result["normalized_value"] = value
                        result["conversion_record"] = conversion
                    else:
                        issues.extend(shacl["issues"])
            elif not numeric:
                # Syntax can be inspected early, but no formal converted value
                # or SHACL-conformant claim is returned before semantic review.
                _, issue = normalize_literal(raw, slot, source_unit=source_unit)
                if issue:
                    issues.append(issue)
                result["structural_checks"]["datatype"] = "failed" if issue else "passed"
    if semantic_status != "supported":
        issues.append(
            "semantic_undetermined" if semantic_status in {"undetermined", "incomplete"}
            else "semantic_not_checked" if semantic_status == "not_checked"
            else "semantic_not_supported"
        )
    result["issues"] = sorted(set(issues))
    if not issues and result["shacl"]["validation_status"] == "passed":
        result["validation_status"] = "passed"
    elif result["execution_status"] == "failed" or all(i in _INCOMPLETE for i in issues):
        result["validation_status"] = "incomplete"
    else:
        result["validation_status"] = "failed"
    return result
