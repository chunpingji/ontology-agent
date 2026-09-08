"""Deterministic rule branches and per-claim eligibility; no risk label mappings."""

from copy import deepcopy

from app.services.extraction.evidence_identity import evidence_hash
from app.services.reporting.condition_resolver import evaluate_expression, validate_bound_expression
from app.services.reporting.input_resolver import (
    Discovery,
    ResolvedValue,
    all_issues,
    check_identities,
    issue,
    project_value,
    summarize,
    typed_value,
    unavailable_value,
)
from app.services.reporting.template_v2 import ReportingError, TypeSpec


def parameter_value(ref, inputs):
    source = inputs.get(ref.input_id)
    if source is None:
        return None
    try:
        return project_value(source, ref.field_path)
    except ReportingError:
        return None


def observed_truth(value, bundle, scope_id):
    if value is None or value.state != "ready" or value.execution_scope_id != scope_id:
        return False
    if value.resolved_type.kind == "list":
        return (
            bool(value.items)
            and value.discovery is not None
            and (
                value.discovery.status == "complete"
                and all(observed_truth(item, bundle, scope_id) for item in value.items)
            )
        )
    proofs = value.derivation.get("condition_evaluations", [])
    nature = value.derivation.get("business_nature")
    return (
        value.resolved_type.kind == "boolean"
        and value.value is True
        and bool(value.fact_refs)
        and bool(value.subject_refs)
        and value.applicable_scope.get("applicable_at") == bundle.get("applicable_at")
        and (nature == "observed_fact" or bool(proofs))
        and all(
            p.get("business_nature") == "observed_fact" and p.get("projection_eligible")
            for p in proofs
        )
    )


def _lineage(parameters):
    def walk(value):
        yield value
        for child in [*value.fields.values(), *value.items]:
            yield from walk(child)

    nodes = [node for value in parameters.values() if value for node in walk(value)]
    return {
        "fact_refs": sorted({ref for value in nodes for ref in value.fact_refs}),
        "subject_refs": sorted({ref for value in nodes for ref in value.subject_refs}),
        "provenance_refs": [p for value in nodes for p in value.provenance_refs],
    }


def evidence_eligible(value, *, subjects, applicable_at, scope_id, nature):
    """Every consumed leaf must carry evidence for this subject, time and assertion nature."""
    if value is None or value.execution_scope_id != scope_id:
        return False
    if value.issues or (value.discovery and value.discovery.status != "complete"):
        return False
    children = [*value.fields.values(), *value.items]
    if children:
        return all(
            evidence_eligible(
                child,
                subjects=subjects,
                applicable_at=applicable_at,
                scope_id=scope_id,
                nature=nature,
            )
            for child in children
        )
    proofs = value.derivation.get("condition_evaluations", [])
    return (
        value.state == "ready"
        and bool(value.fact_refs)
        and bool(value.subject_refs)
        and set(value.subject_refs) <= subjects
        and value.applicable_scope.get("applicable_at") == applicable_at
        and value.derivation.get("business_nature") == nature
        and all(p.get("projection_eligible") and p.get("business_nature") == nature for p in proofs)
    )


def claim_eligibility(ref, bundle, parameters, rule_evaluation, scope_id):
    record = bundle["contracts"].get(ref, {})
    definition = record.get("definition", {})
    proof = None
    errors = []
    if (
        record.get("kind") != "claim"
        or record.get("status") != "published"
        or record.get("is_disabled")
        or not definition.get("review_ref")
    ):
        errors.append("CLAIM_PRECONDITION_UNPROVEN")
    else:
        expression = validate_bound_expression(definition["precondition"], set(parameters))
        proof = evaluate_expression(expression, {}, parameters)
        if proof["result"] != "TRUE":
            errors.append("CLAIM_PRECONDITION_UNPROVEN")
        category = definition.get("category")
        if category not in {"planned_control", "observed_fact", "risk_decision"}:
            errors.append("CLAIM_CATEGORY_INVALID")
        focus = parameters.get(definition.get("subject_parameter"))
        focus_refs = set()
        if focus and focus.state == "ready" and focus.execution_scope_id == scope_id:
            focus_refs = set(focus.subject_refs)
            if focus.entity_id:
                focus_refs = {focus.entity_id}
        if not focus_refs:
            errors.append("CLAIM_SCOPE_MISMATCH")
        for key, expected in definition.get("applicable_scope", {}).items():
            actual = (
                bundle.get("applicable_at")
                if key == "applicable_at"
                else ((focus.applicable_scope if focus else {}).get(key))
            )
            if actual != expected:
                errors.append("CLAIM_SCOPE_MISMATCH")
        for name in definition.get("evidence_parameters", []):
            value = parameters.get(name)
            if not evidence_eligible(
                value,
                subjects=focus_refs,
                applicable_at=bundle.get("applicable_at"),
                scope_id=scope_id,
                nature="planned_control" if category == "planned_control" else "observed_fact",
            ):
                errors.append("CLAIM_PRECONDITION_UNPROVEN")
        if category in {"observed_fact", "risk_decision"} and not definition.get(
            "evidence_parameters"
        ):
            errors.append("CLAIM_PRECONDITION_UNPROVEN")
        if category == "observed_fact" and definition.get("scope_quantifier") == "all":
            domain = parameters.get(definition.get("universe_parameter"))
            if not domain or not domain.discovery or domain.discovery.status != "complete":
                errors.append("OBJECT_UNIVERSE_OPEN")
    result = {
        "claim_id": ref,
        "claim_definition_hash": evidence_hash(record),
        "category": definition.get("category"),
        "text": definition.get("text", ""),
        "execution_scope_id": scope_id,
        "applicable_at": bundle.get("applicable_at"),
        "rule_evaluation_ref": rule_evaluation,
        "proof": proof,
        "eligible": not errors,
        "issues": errors,
        **_lineage(parameters),
    }
    return {**result, "eligibility_ref": evidence_hash(result)}


def evaluate_rule(binding, bundle, inputs, scope_id):
    record = bundle["contracts"].get(binding.contract_ref, {})
    definition = record.get("definition", {})
    typ = TypeSpec.model_validate(definition["output_type"])
    if (
        record.get("status") != "published"
        or record.get("is_disabled")
        or (not definition.get("claims_reviewed") or not definition.get("review_ref"))
    ):
        return unavailable_value(binding.binding_id, typ, "invalid", "RULE_REVISION_NOT_PUBLISHED")
    parameters = {key: parameter_value(ref, inputs) for key, ref in binding.parameters.items()}
    try:
        for branch in definition.get("branches", []):
            for expr in [
                branch["when"],
                *[
                    expression
                    for output in branch["fields"].values()
                    for expression in (output["value"], output.get("precondition"))
                    if expression
                ],
            ]:
                validate_bound_expression(expr, set(parameters))
    except ReportingError:
        return unavailable_value(binding.binding_id, typ, "invalid", "UNBOUND_SEMANTIC_INPUT")
    lineage = _lineage(parameters)
    result = ResolvedValue(
        node_id=binding.binding_id, resolved_type=typ, execution_scope_id=scope_id, **lineage
    )
    row_type = typ.item_type if typ.kind == "list" else typ
    branches, claims = [], []
    for branch in definition.get("branches", []):
        proof = evaluate_expression(branch["when"], {}, parameters)
        row_id = evidence_hash([binding.contract_ref, scope_id, branch["branch_id"]])
        row = ResolvedValue(
            node_id=row_id,
            entity_id=row_id,
            resolved_type=row_type,
            execution_scope_id=scope_id,
            **lineage,
        )
        evaluation = {
            "rule_revision_ref": binding.contract_ref,
            "rule_revision_hash": evidence_hash(record),
            "branch_id": branch["branch_id"],
            "execution_scope_id": scope_id,
            "source_bundle_id": bundle["source_bundle_id"],
            "applicable_at": bundle.get("applicable_at"),
            "proof": proof,
            "applicability": proof["result"],
            "parameter_hashes": {
                key: evidence_hash(v) if v else None for key, v in parameters.items()
            },
        }
        evaluation_ref = evidence_hash(evaluation)
        for key, field in row_type.fields.items():
            output = branch.get("fields", {}).get(key)
            node = row_id + "/" + key
            if proof["result"] != "TRUE" or output is None:
                value = unavailable_value(node, field.type, "missing", "RULE_DECISION_UNPROVEN")
            else:
                eligible = True
                guard_proof = None
                if output.get("precondition"):
                    guard_proof = evaluate_expression(output["precondition"], {}, parameters)
                    eligible = guard_proof["result"] == "TRUE"
                claim = None
                if output.get("claim_ref"):
                    claim = claim_eligibility(
                        output["claim_ref"], bundle, parameters, evaluation_ref, scope_id
                    )
                    eligible = eligible and claim["eligible"]
                expression = evaluate_expression(output["value"], {}, parameters)
                if claim and (
                    expression.get("value") != claim["text"]
                    or output.get("category") != claim["category"]
                ):
                    claim["eligible"] = False
                    claim["issues"].append("CLAIM_TEXT_MISMATCH")
                    eligible = False
                if eligible and expression["available"]:
                    value = typed_value(
                        node,
                        field.type,
                        expression["value"],
                        refs=lineage["fact_refs"],
                        provenance=lineage["provenance_refs"],
                        subject=lineage["subject_refs"],
                        scope=scope_id,
                    )
                else:
                    value = unavailable_value(
                        node, field.type, "missing", "CLAIM_PRECONDITION_UNPROVEN"
                    )
                value.derivation = {
                    "evaluation_ref": evaluation_ref,
                    "field_proof": expression,
                    "eligibility_proof": guard_proof,
                    "category": output.get("category"),
                }
                # Actual post-control decisions must declare and consume implementation
                # evidence in their reviewed field contract; plans cannot occupy that role.
                if output.get("requires_implementation"):
                    names = output.get("implementation_parameters", [])
                    if not names or any(
                        not observed_truth(parameters.get(name), bundle, scope_id) for name in names
                    ):
                        value = unavailable_value(
                            node, field.type, "missing", "IMPLEMENTATION_EVIDENCE_UNPROVEN"
                        )
                if claim:
                    if value.state != "ready":
                        claim["eligible"] = False
                        claim["issues"].append("CLAIM_OUTPUT_UNUSABLE")
                    claim["eligibility_ref"] = evidence_hash(
                        {k: v for k, v in claim.items() if k != "eligibility_ref"}
                    )
                    claims.append(claim)
            row.fields[key] = value
        if proof["result"] == "UNKNOWN":
            for parameter in parameters.values():
                if parameter:
                    row.issues.extend(deepcopy(all_issues(parameter)))
        row.derivation = {"rule_evaluation_ref": evaluation_ref}
        branches.append({**evaluation, "evaluation_ref": evaluation_ref})
        if typ.kind == "list":
            result.items.append(summarize(row))
        else:
            if result.fields:
                result.issues.append(
                    issue("conflict", "RULE_BRANCH_CONFLICT", binding.binding_id, blocks=True)
                )
            result.fields = row.fields
    if typ.kind == "list":
        result.discovery = Discovery(
            status="complete",
            proof_ref=evidence_hash(branches),
            expected_ids=[row.entity_id for row in result.items],
        )
        check_identities(result)
    result.derivation = {
        "rule_evaluation": branches,
        "claim_eligibility": claims,
        "claim_catalog": [c for c in claims if c["eligible"]],
        "rule_definition_hash": evidence_hash(record),
    }
    return summarize(result)
