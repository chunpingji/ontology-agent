"""Versioned three-valued expressions and scoped conditional assertion proofs."""

from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal

from pydantic import ValidationError

from app.services.extraction.evidence_identity import evidence_hash
from app.services.reporting.input_resolver import (
    all_issues,
    business_value,
    project_value,
    select_nodes,
    validate_scalar,
)
from app.services.reporting.template_v2 import Expression, ReportingError, TypeSpec


def expression_type(expression, inputs=None, parameters=None, items=None):
    """Infer the finite expression language without reading values or coercing types."""
    from app.services.reporting.input_resolver import projection_type

    expr = Expression.model_validate(expression)
    if expr.op == "input":
        source = (items or {}) if expr.input_ref.scope == "item" else (inputs or {})
        typ = source.get(expr.input_ref.input_id)
        if typ is None:
            raise ReportingError("UNRESOLVED_INPUT_REFERENCE")
        return projection_type(TypeSpec.model_validate(typ), expr.input_ref.field_path)
    if expr.op == "parameter":
        typ = (parameters or {}).get(expr.parameter)
        if typ is None:
            raise ReportingError("RULE_PARAMETER_MISMATCH")
        return TypeSpec.model_validate(typ)
    if expr.op == "literal":
        typ = expr.literal_type or TypeSpec(
            kind="boolean"
            if type(expr.value) is bool
            else "integer"
            if type(expr.value) is int
            else "string"
        )
        validate_scalar(expr.value, typ)
        return typ
    args = [expression_type(arg, inputs, parameters, items) for arg in expr.args]
    if expr.op in {"and", "or", "not"} and any(a.kind != "boolean" for a in args):
        raise ReportingError("CONDITION_TYPE_MISMATCH")
    if expr.op == "all" and (args[0].kind != "list" or args[0].item_type.kind != "boolean"):
        raise ReportingError("CONDITION_TYPE_MISMATCH")
    if expr.op in {"eq", "ne", "lt", "le", "gt", "ge"}:
        left, right = args
        compatible = {left.kind, right.kind} <= {"integer", "decimal"} or (
            left.kind == right.kind
            and left.kind in {"boolean", "string", "date", "datetime", "year_month", "enum"}
        )
        if not compatible or (expr.op in {"lt", "le", "gt", "ge"} and left.kind == "boolean"):
            raise ReportingError("CONDITION_TYPE_MISMATCH")
        if left.kind == "enum" and set(left.enum_values) != set(right.enum_values):
            raise ReportingError("CONDITION_TYPE_MISMATCH")
    return TypeSpec(kind="boolean")


EVALUATOR_VERSION = "condition-proof-v2.1"


def validate_bound_expression(expression, parameter_names=None):
    """Registered semantic definitions can only consume explicitly mapped parameters."""
    expression = Expression.model_validate(expression)
    if expression.op == "input":
        raise ReportingError("UNBOUND_SEMANTIC_INPUT")
    if expression.op == "parameter" and parameter_names is not None:
        if expression.parameter not in parameter_names:
            raise ReportingError("RULE_PARAMETER_MISMATCH")
    for arg in expression.args:
        validate_bound_expression(arg, parameter_names)
    return expression


def evaluate_expression(expression, inputs, parameters=None, *, items=None):
    expression = Expression.model_validate(expression)
    parameters = parameters or {}
    leaves = []

    def unavailable(code, issue_refs=()):
        return {
            "available": False,
            "value": None,
            "type": None,
            "result": "UNKNOWN",
            "issues": list(issue_refs),
            "code": code,
            "proof": [],
        }

    def visit(expr):
        if expr.op in {"input", "parameter"}:
            if expr.op == "input":
                context = (items or {}) if expr.input_ref.scope == "item" else inputs
                value = context.get(expr.input_ref.input_id)
                path = expr.input_ref.field_path
            else:
                value = parameters.get(expr.parameter)
                path = []
            if value is None:
                return unavailable("CONDITION_PARAMETER_MISSING")
            nodes, ancestor_issues = select_nodes(value, path)
            issues = [i.issue_id for i in ancestor_issues]
            for node in nodes:
                issues.extend(i.issue_id for i in all_issues(node))
            try:
                projected = project_value(value, path)
                actual = business_value(projected)
                typ = projected.resolved_type.kind
            except ReportingError:
                result = unavailable("CONDITION_UNKNOWN", issues)
                # A published absence is evidence of absence only at this exact node;
                # exists consumes the state, other operators cannot coerce it to false.
                if len(nodes) == 1 and nodes[0].state == "confirmed_absent" and not ancestor_issues:
                    result["absence_proof"] = nodes[0].fact_refs
                leaves.append(
                    {
                        "ref": expr.model_dump(mode="json"),
                        "nodes": [n.model_dump(mode="json") for n in nodes],
                        "result": result,
                    }
                )
                return result
            result = {
                "available": True,
                "value": actual,
                "type": typ,
                "result": "TRUE" if actual is True else "FALSE" if actual is False else "UNKNOWN",
                "issues": issues,
                "proof": [evidence_hash(n) for n in nodes],
                "complete": not projected.discovery or projected.discovery.status == "complete",
            }
            leaves.append(
                {
                    "ref": expr.model_dump(mode="json"),
                    "nodes": [n.model_dump(mode="json") for n in nodes],
                    "result": result,
                }
            )
            return result
        if expr.op == "literal":
            typ = (
                "boolean"
                if type(expr.value) is bool
                else "integer"
                if type(expr.value) is int
                else "string"
            )
            if expr.literal_type:
                typ = expr.literal_type.kind
                validate_scalar(expr.value, expr.literal_type)
            return {
                "available": expr.value is not None,
                "value": expr.value,
                "type": typ,
                "result": "TRUE"
                if expr.value is True
                else "FALSE"
                if expr.value is False
                else "UNKNOWN",
                "issues": [],
                "proof": [evidence_hash(expr)],
            }
        args = [visit(arg) for arg in expr.args]
        issues = sorted({i for arg in args for i in arg["issues"]})
        proof = [p for arg in args for p in arg["proof"]]
        result = "UNKNOWN"
        used = list(range(len(args)))
        if expr.op in {"and", "or"}:
            decisive = "FALSE" if expr.op == "and" else "TRUE"
            opposite = "TRUE" if expr.op == "and" else "FALSE"
            if any(a["result"] == decisive for a in args):
                result = decisive
                used = [next(i for i, a in enumerate(args) if a["result"] == decisive)]
            elif all(a["result"] == opposite for a in args):
                result = opposite
        elif expr.op == "not":
            result = {"TRUE": "FALSE", "FALSE": "TRUE", "UNKNOWN": "UNKNOWN"}[args[0]["result"]]
        elif expr.op == "exists":
            if args[0].get("absence_proof"):
                result = "FALSE"
            elif args[0]["available"]:
                value = args[0]["value"]
                if isinstance(value, list):
                    if value:
                        result = "TRUE"
                    elif args[0].get("complete"):
                        result = "FALSE"
                else:
                    result = "TRUE"
        elif expr.op == "all":
            arg = args[0]
            if arg["available"] and arg["type"] == "list" and arg.get("complete"):
                if all(type(v) is bool for v in arg["value"]):
                    result = "TRUE" if all(arg["value"]) else "FALSE"
        elif all(a["available"] for a in args):
            left, right = args
            numeric = {"integer", "decimal"}
            if left["type"] in numeric and right["type"] in numeric:
                a, b = Decimal(left["value"]), Decimal(right["value"])
            elif left["type"] == right["type"] and left["type"] in {
                "boolean",
                "string",
                "date",
                "datetime",
                "year_month",
                "enum",
            }:
                a, b = left["value"], right["value"]
                if left["type"] == "datetime":
                    a, b = datetime.fromisoformat(a), datetime.fromisoformat(b)
                elif left["type"] == "date":
                    a, b = date.fromisoformat(a), date.fromisoformat(b)
            else:
                return unavailable("CONDITION_TYPE_MISMATCH", issues)
            if expr.op in {"lt", "le", "gt", "ge"} and left["type"] == "boolean":
                return unavailable("CONDITION_TYPE_MISMATCH", issues)
            operations = {
                "eq": lambda: a == b,
                "ne": lambda: a != b,
                "lt": lambda: a < b,
                "le": lambda: a <= b,
                "gt": lambda: a > b,
                "ge": lambda: a >= b,
            }
            result = "TRUE" if operations[expr.op]() else "FALSE"
        return {
            "available": result != "UNKNOWN",
            "value": result == "TRUE" if result != "UNKNOWN" else None,
            "type": "boolean",
            "result": result,
            "issues": issues,
            "proof": proof,
            "used_branches": used,
            "unused_branches": [i for i in range(len(args)) if i not in used],
            "operands": args,
        }

    result = visit(expression)
    evaluation = {
        "evaluator_version": EVALUATOR_VERSION,
        "expression_hash": evidence_hash(expression),
        **result,
        "parameters": leaves,
    }
    return {**evaluation, "evaluation_hash": evidence_hash(evaluation)}


def evaluate_condition(ref, bundle, inputs, scope_id):
    contracts = bundle["contracts"]
    record = contracts.get(ref, {})
    binding = record.get("definition", {})
    definition_record = contracts.get(binding.get("condition_ref"), {})
    definition = definition_record.get("definition", {})
    result = {
        "binding_ref": ref,
        "binding_hash": evidence_hash(record),
        "definition_hash": evidence_hash(definition_record),
        "source_bundle_id": bundle["source_bundle_id"],
        "execution_scope_id": scope_id,
        "applicable_at": bundle.get("applicable_at"),
        "result": "UNKNOWN",
        "issues": [],
        "assertion_ref": binding.get("assertion_id"),
        "business_nature": binding.get("business_nature"),
        "polarity": binding.get("polarity"),
    }
    records = [
        r
        for source in bundle.get("sources", {}).values()
        for r in (source.get("snapshot") or {}).get("assertions", [])
        if r["assertion_id"] == binding.get("assertion_id")
    ]
    if (
        record.get("status") != "published"
        or definition_record.get("status") != "published"
        or not binding.get("association_review_ref")
        or not records
    ):
        result["issues"].append("CONDITION_BINDING_UNRESOLVED")
    else:
        assertion = records[0]
        candidate = assertion["candidate"]
        if (
            candidate["assertion_status"] != "conditional"
            or not (
                candidate.get("condition_anchors") or candidate.get("condition_provenance_indexes")
            )
            or binding.get("subject_id") != assertion["subject_iri"]
            or binding.get("object_id") != assertion.get("object_iri")
            or binding.get("applicable_at") != bundle.get("applicable_at")
            or candidate.get("applicable_at") != binding.get("applicable_at")
        ):
            result["issues"].append("CONDITION_SCOPE_MISMATCH")
        else:
            parameters = {}
            for key, mapped in binding.get("parameters", {}).items():
                value = inputs.get(mapped["input_id"])
                if not value:
                    result["issues"].append("CONDITION_UNKNOWN")
                    continue
                nodes, ancestors = select_nodes(value, mapped.get("field_path", []))
                if ancestors or len(nodes) != 1:
                    result["issues"].append("CONDITION_SCOPE_MISMATCH")
                    continue
                node = nodes[0]
                expected_subject = binding.get("parameter_subjects", {}).get(key)
                if not expected_subject or expected_subject not in node.subject_refs:
                    result["issues"].append("CONDITION_SCOPE_MISMATCH")
                    continue
                if node.applicable_scope.get("applicable_at") != binding.get("applicable_at"):
                    result["issues"].append("CONDITION_SCOPE_MISMATCH")
                    continue
                parameters[key] = node
            if set(parameters) != set(definition.get("parameters", {})):
                result["issues"].append("CONDITION_UNKNOWN")
            if not result["issues"]:
                try:
                    expression = validate_bound_expression(
                        definition["expression"], set(definition.get("parameters", {}))
                    )
                    evaluated = evaluate_expression(expression, {}, parameters)
                    result.update(result=evaluated["result"], proof=evaluated)
                except (ValidationError, KeyError, ReportingError):
                    result["issues"].append("CONDITION_UNSUPPORTED")
    result["projection_eligible"] = (
        result["result"] == "TRUE"
        and not result["issues"]
        and result["polarity"] in {"affirmed", "negated"}
        and result["business_nature"] in {"observed_fact", "planned_control"}
    )
    result["condition_evaluation_ref"] = evidence_hash(result)
    return deepcopy(result)
