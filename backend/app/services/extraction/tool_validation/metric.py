"""Unit and literal checks with external, explicit semantic verification."""

from __future__ import annotations

from app.services.extraction.literal_normalizer import (
    UNIT_REGISTRY_VERSION,
    LiteralNormalizationError,
    canonical_unit,
    unit_definition,
)
from app.services.extraction.literal_normalizer import normalize_literal as parse_quantity
from app.services.extraction.ontology_guided.claim_protocol import (
    QuantityPolicy,
    QuantityValue,
    VerificationTargetSpec,
    VerifiedTarget,
)
from app.services.extraction.ontology_guided.contracts import SlotSpec, VersionedRef
from app.services.extraction.ontology_guided.tool_contracts import (
    BindingData,
    MetricData,
    ToolIssue,
)
from app.services.extraction.ontology_guided.value_constraints import (
    NUMERIC_DATATYPES,
    normalize_literal,
)
from app.services.extraction.tool_validation.shacl import (
    build_metric_graph,
    claim_node,
    unevaluated_shacl,
    validate_graph,
)

_INCOMPLETE = {
    "binding_not_checked", "unit_source_missing", "unit_binding_source_missing",
    "unit_unknown", "constraint_unresolved", "scalar_value_required",
    "unit_conversion_not_exact", "semantic_not_checked", "semantic_undetermined",
    "shacl_coverage_incomplete", "shacl_missing_fields",
    "owner_record_ambiguous",
}
_QUANTITY_INCOMPLETE = _INCOMPLETE | {
    "quantity_form_not_allowed", "quantity_endpoint_unresolved", "quantity_approximate",
    "unsupported numeric grammar", "unsupported interval grammar",
}


def _metric_failure(claim_ref: VersionedRef, codes: list[str]) -> MetricData:
    return MetricData(
        claim_ref=claim_ref, quantity=None, normalized_literal=None,
        validation_status=("incomplete" if all(code in _QUANTITY_INCOMPLETE for code in codes)
                           else "failed"),
        issues=[ToolIssue(code=code, field_path=None, message=code, evidence_ids=[])
                for code in dict.fromkeys(codes)],
    )


def normalize_metric(
    raw: str, slot: SlotSpec, *, quantity_policy: QuantityPolicy,
    source_unit: str | None, target_unit: str | None, binding: BindingData,
    verified: VerifiedTarget, target: VerificationTargetSpec, candidate_ref: VersionedRef,
) -> MetricData:
    """Pure normalization of one frozen, source-checked claim; SHACL runs separately.

    The controller supplies the binding, complete verified target and controlled
    unit policy. Neither a model-supplied verdict nor a bare decision ID grants
    permission to produce a trusted value.
    """
    if (candidate_ref != target.claim_ref or binding.claim_ref != candidate_ref
            or verified.target_id != target.target_id
            or verified.content_hash != target.content_hash):
        return _metric_failure(candidate_ref, ["metric_claim_mismatch"])
    if (target.target_kind != "property" or target.payload.value_quote.text != raw
            or target.payload.predicate_iri != slot.iri
            or quantity_policy.predicate_iri != slot.iri):
        return _metric_failure(candidate_ref, ["metric_claim_mismatch"])
    if binding.validation_status != "passed" or binding.issues or not binding.resolved_role_refs:
        codes = [issue.code for issue in binding.issues] or ["binding_not_checked"]
        result = _metric_failure(candidate_ref, codes)
        if binding.validation_status != "passed":
            result.validation_status = binding.validation_status
        return result
    if source_unit != binding.source_unit:
        return _metric_failure(candidate_ref, ["source_unit_conflict"])
    decisions = verified.decisions
    if (verified.missing_facets or verified.validation_issues
            or len(decisions) != len(target.required_facets)
            or {d.check_kind for d in decisions} != set(target.required_facets)
            or any(d.target_id != target.target_id or d.verdict != "supported"
                   or not d.support_refs for d in decisions)):
        rejected = any(d.verdict == "unsupported" for d in decisions)
        return _metric_failure(candidate_ref, [
            "semantic_not_supported" if rejected else "semantic_undetermined",
        ])
    if slot.constraint_status != "resolved" or len(set(slot.datatype_iris)) != 1:
        return _metric_failure(candidate_ref, ["constraint_unresolved"])
    datatype = slot.datatype_iris[0]
    if datatype not in NUMERIC_DATATYPES:
        if target_unit is not None or source_unit is not None or slot.canonical_unit is not None:
            return _metric_failure(candidate_ref, ["constraint_unresolved"])
        value, issue = normalize_literal(raw, slot)
        if issue:
            return _metric_failure(candidate_ref, [issue])
        return MetricData(
            claim_ref=candidate_ref, validation_status="passed", quantity=None,
            normalized_literal=value, issues=[],
        )
    try:
        parsed = parse_quantity(raw, datatype="decimal", source_unit=source_unit)
        if parsed.operator == "approx":
            raise LiteralNormalizationError("quantity_approximate")
        form = {"number": "scalar", "range": "interval", "comparison": (
            "lower_bound" if parsed.operator in {"gt", "ge"} else "upper_bound"
        )}[parsed.kind]
        if form not in quantity_policy.allowed_forms:
            raise LiteralNormalizationError("quantity_form_not_allowed")
        if quantity_policy.endpoint_role is not None and form != "interval":
            raise LiteralNormalizationError("quantity_endpoint_unresolved")
        requirement = quantity_policy.unit_requirement
        if requirement == "physical" and parsed.raw_unit is None:
            raise LiteralNormalizationError("unit_source_missing")
        if requirement == "count" and parsed.raw_unit is not None:
            raise LiteralNormalizationError("unit_missing_or_incompatible")
        if requirement == "dimensionless" and parsed.dimension not in {None, "ratio"}:
            raise LiteralNormalizationError("unit_missing_or_incompatible")
        if requirement == "physical" and parsed.dimension == "ratio":
            raise LiteralNormalizationError("unit_missing_or_incompatible")
        if target_unit is not None and canonical_unit(target_unit) not in {
            canonical_unit(unit) for unit in quantity_policy.allowed_target_units
        }:
            raise LiteralNormalizationError("target_unit_not_allowed")
        destination = target_unit if target_unit is not None else slot.canonical_unit
        # With no declared destination retain the source unit and its scale.
        # The list of allowed conversions does not select a preferred unit.
        destination = destination or parsed.canonical_unit
        if destination is not None:
            parsed = parse_quantity(
                raw, datatype="decimal", source_unit=source_unit, target_unit=destination,
            )
        numbers = [parsed.lower, parsed.upper] if form == "interval" else [parsed.normalized_value]
        scalar_slot = slot.model_copy(update={"canonical_unit": None})
        for number in numbers:
            _, issue = normalize_literal(number, scalar_slot)
            if issue:
                raise LiteralNormalizationError(issue)
        lower_bound, upper_bound = form == "lower_bound", form == "upper_bound"
        cited_unit = source_unit if source_unit is not None else parsed.raw_unit
        record = {key: str(value) for key, value in parsed.conversion_record.items()}
        if record:
            record["mapping_kind"] = (
                "conversion" if record["from"] != record["to"] else
                "identity" if cited_unit == record["to"] else "alias"
            )
        quantity = QuantityValue(
            form=form, raw=raw, source_unit=cited_unit, target_unit=parsed.canonical_unit,
            scalar=parsed.normalized_value if form == "scalar" else None,
            lower=(parsed.lower if form == "interval" else
                   parsed.normalized_value if lower_bound else None),
            upper=(parsed.upper if form == "interval" else
                   parsed.normalized_value if upper_bound else None),
            lower_inclusive=(parsed.lower_inclusive if form == "interval" else
                             parsed.operator == "ge" if lower_bound else None),
            upper_inclusive=(parsed.upper_inclusive if form == "interval" else
                             parsed.operator == "le" if upper_bound else None),
            comparator=parsed.operator if lower_bound or upper_bound else None,
            endpoint_role=quantity_policy.endpoint_role, datatype_iri=datatype,
            dimension=parsed.dimension, conversion_record=record,
        )
    except LiteralNormalizationError as exc:
        return _metric_failure(candidate_ref, [str(exc)])
    value = quantity.scalar
    if quantity.endpoint_role is not None:
        value = getattr(quantity, quantity.endpoint_role)
    return MetricData(
        claim_ref=candidate_ref, validation_status="passed", quantity=quantity,
        normalized_literal=value, issues=[],
    )


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
        numeric = datatype in NUMERIC_DATATYPES
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
