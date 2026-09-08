"""Typed values, issue propagation and field-scoped consumption (design §7.5)."""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import Field

from app.services.extraction.evidence_identity import evidence_hash
from app.services.reporting.template_v2 import (
    Constraints,
    Model,
    ReportingError,
    State,
    TypeSpec,
)

RESOLVER_VERSION = "output-input-resolver-v2.1"
PRIORITY = ("invalid", "conflict", "unavailable", "pending_review", "incomplete", "missing")
UNUSABLE = frozenset(PRIORITY)


class Issue(Model):
    issue_id: str
    state: State
    code: str
    origin_node: str
    affected_scope: str = ""
    message: str = ""
    evidence_refs: list[str] = Field(default_factory=list)
    affected_consumers: list[str] = Field(default_factory=list)
    blocking_requirement_refs: list[str] = Field(default_factory=list)
    constraint: Literal["value", "identity", "cardinality", "discovery", "scope"] = "value"
    blocks_subtree: bool = False


class Discovery(Model):
    status: Literal["complete", "open", "unknown"] = "unknown"
    proof_ref: str | None = None
    expected_ids: list[str] = Field(default_factory=list)


class ResolvedValue(Model):
    node_id: str
    resolved_type: TypeSpec
    state: State = "missing"
    value: Any = None
    issues: list[Issue] = Field(default_factory=list)
    fields: dict[str, ResolvedValue] = Field(default_factory=dict)
    items: list[ResolvedValue] = Field(default_factory=list)
    entity_id: str | None = None
    subject_refs: list[str] = Field(default_factory=list)
    object_refs: list[str] = Field(default_factory=list)
    fact_refs: list[str] = Field(default_factory=list)
    provenance_refs: list[dict] = Field(default_factory=list)
    discovery: Discovery | None = None
    derivation: dict = Field(default_factory=dict)
    applicable_scope: dict = Field(default_factory=dict)
    input_id: str | None = None
    input_definition_hash: str | None = None
    execution_scope_id: str = ""
    display_action: str = "annotate"


def issue(state, code, node, *, scope="", refs=(), constraint="value", blocks=False, message=""):
    identity = evidence_hash([state, code, node, scope, sorted(refs), constraint])
    return Issue(
        issue_id=identity,
        state=state,
        code=code,
        origin_node=node,
        affected_scope=scope,
        evidence_refs=sorted(set(refs)),
        constraint=constraint,
        blocks_subtree=blocks,
        message=message,
    )


def all_issues(value):
    result = {i.issue_id: i for i in value.issues}
    for child in [*value.fields.values(), *value.items]:
        result.update({i.issue_id: i for i in all_issues(child)})
    return list(result.values())


def summarize(value):
    issues = all_issues(value)
    states = {i.state for i in issues}
    summary = next((s for s in PRIORITY if s in states), None)
    if summary:
        value.state = summary
    elif value.state not in {"not_applicable", "confirmed_absent"}:
        if value.resolved_type.kind == "record":
            value.state = "ready" if value.fields else "missing"
        elif value.resolved_type.kind == "list":
            value.state = (
                "ready"
                if value.discovery and value.discovery.status == "complete" or value.items
                else "missing"
            )
        else:
            value.state = "ready" if value.value is not None else "missing"
    if value.state in UNUSABLE:
        value.value = None
    return value


def unavailable_value(node, typ, state="missing", code="INPUT_MISSING", **kwargs):
    result = ResolvedValue(node_id=node, resolved_type=typ, **kwargs)
    result.issues.append(issue(state, code, node, scope=result.execution_scope_id))
    return summarize(result)


def validate_scalar(value, typ):
    """No coercion of false/0/missing, floats, dates or unregistered units."""
    if value is None:
        raise ValueError("missing")
    kind = typ.kind
    if kind == "string":
        if not isinstance(value, str):
            raise ValueError("string required")
    elif kind == "boolean":
        if type(value) is not bool:
            raise ValueError("boolean required")
    elif kind == "integer":
        if type(value) is not int:
            raise ValueError("integer required")
        if typ.datatype_iri and typ.datatype_iri.endswith("#nonNegativeInteger") and value < 0:
            raise ValueError("nonnegative integer required")
        if typ.datatype_iri and typ.datatype_iri.endswith("#positiveInteger") and value <= 0:
            raise ValueError("positive integer required")
    elif kind == "decimal":
        if not isinstance(value, str) or not value.strip() or value != value.strip():
            raise ValueError("exact decimal string required")
        number = Decimal(value)
        if not number.is_finite():
            raise ValueError("finite decimal required")
        value = format(number, "f")
    elif kind == "date":
        if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
            raise ValueError("exact ISO date required")
    elif kind == "datetime":
        if not isinstance(value, str):
            raise ValueError("ISO datetime required")
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None or "T" not in value:
            raise ValueError("datetime requires timezone")
    elif kind == "year_month":
        if not isinstance(value, str) or len(value) != 7 or value[4] != "-":
            raise ValueError("year-month required")
        if not value[:4].isascii() or not value[:4].isdigit() or not value[5:].isdigit():
            raise ValueError("year-month required")
        date(int(value[:4]), int(value[5:]), 1)
    elif kind == "enum":
        if value not in typ.enum_values:
            raise ValueError("value outside registered vocabulary")
    elif kind == "quantity":
        if not isinstance(value, dict) or set(value) != {"value", "unit", "dimension"}:
            raise ValueError("typed quantity required")
        if value["unit"] != typ.unit or value["dimension"] != typ.dimension:
            raise ValueError("incompatible quantity unit or dimension")
        value = {**value, "value": validate_scalar(value["value"], TypeSpec(kind="decimal"))}
    elif kind == "range":
        if not isinstance(value, dict) or set(value) != {
            "lower",
            "upper",
            "lower_inclusive",
            "upper_inclusive",
        }:
            raise ValueError("explicit range endpoints and inclusivity required")
        for key in ("lower_inclusive", "upper_inclusive"):
            if type(value[key]) is not bool:
                raise ValueError("range inclusivity must be explicit")
            expected = getattr(typ, key)
            if expected is not None and value[key] != expected:
                raise ValueError("range inclusivity contradicts contract")
        lower = validate_scalar(value["lower"], typ.item_type)
        upper = validate_scalar(value["upper"], typ.item_type)

        def compare(endpoint):
            if typ.item_type.kind == "quantity":
                return Decimal(endpoint["value"])
            if typ.item_type.kind in {"integer", "decimal"}:
                return Decimal(endpoint)
            if typ.item_type.kind == "datetime":
                return datetime.fromisoformat(endpoint)
            return endpoint

        if compare(lower) > compare(upper):
            raise ValueError("range endpoints out of order")
        if compare(lower) == compare(upper) and not (
            value["lower_inclusive"] and value["upper_inclusive"]
        ):
            raise ValueError("empty range")
        value = {**value, "lower": lower, "upper": upper}
    elif kind == "entity":
        if not isinstance(value, dict) or not value.get("entity_id"):
            raise ValueError("canonical entity identity required")
        if value.get("class_iri") not in typ.class_iris:
            raise ValueError("entity type not in compiled constraint")
    return value


def typed_value(
    node,
    typ,
    value,
    *,
    refs=(),
    provenance=(),
    subject=(),
    scope="",
    identity=None,
    discovery=None,
    derivation=None,
):
    typ = TypeSpec.model_validate(typ) if isinstance(typ, dict) else typ
    result = ResolvedValue(
        node_id=node,
        resolved_type=typ,
        entity_id=identity,
        fact_refs=list(refs),
        provenance_refs=deepcopy(list(provenance)),
        subject_refs=list(subject),
        execution_scope_id=scope,
        discovery=discovery,
        derivation=derivation or {},
    )
    if typ.kind == "record":
        if not isinstance(value, dict):
            return unavailable_value(node, typ, "invalid", "INPUT_TYPE_MISMATCH")
        result.entity_id = identity or value.get("entity_id")
        extra = set(value) - set(typ.fields) - {"entity_id"}
        if extra:
            result.issues.append(issue("invalid", "UNKNOWN_RECORD_FIELD", node, blocks=True))
        for key, field in typ.fields.items():
            child = typed_value(
                node + "/" + key,
                field.type,
                value.get(key),
                refs=refs,
                provenance=provenance,
                subject=subject,
                scope=scope,
            )
            apply_constraints(child, field.constraints)
            result.fields[key] = child
    elif typ.kind == "list":
        if not isinstance(value, list):
            return unavailable_value(node, typ, "invalid", "INPUT_TYPE_MISMATCH")
        for index, item in enumerate(value):
            identity = item.get("entity_id") if isinstance(item, dict) else None
            result.items.append(
                typed_value(
                    node + "/" + (identity or str(index)),
                    typ.item_type,
                    item,
                    refs=refs,
                    provenance=provenance,
                    subject=subject,
                    scope=scope,
                    identity=identity,
                )
            )
        if result.discovery is None:
            result.discovery = Discovery(status="complete", proof_ref=evidence_hash([refs, value]))
        check_identities(result)
        apply_constraints(result, Constraints.model_validate(typ.cardinality.model_dump()))
    elif value is None:
        result.issues.append(issue("missing", "INPUT_MISSING", node, scope=scope))
    else:
        try:
            result.value = validate_scalar(value, typ)
            if typ.kind == "entity":
                result.entity_id = value["entity_id"]
        except (ValueError, TypeError, InvalidOperation, OverflowError):
            result.issues.append(
                issue("invalid", "INPUT_TYPE_MISMATCH", node, scope=scope, refs=refs)
            )
    return summarize(result)


def check_identities(value):
    if value.resolved_type.kind != "list" or not value.resolved_type.item_identity:
        return
    seen = set()
    for child in value.items:
        if not child.entity_id or child.entity_id in seen:
            value.issues.append(
                issue(
                    "conflict",
                    "RECORD_IDENTITY_CONFLICT",
                    value.node_id,
                    constraint="identity",
                    blocks=True,
                )
            )
        seen.add(child.entity_id)


def apply_constraints(value, constraints):
    if value.resolved_type.kind == "list":
        count = len(value.items)
        if constraints.max_count is not None and count > constraints.max_count:
            value.issues.append(
                issue(
                    "conflict",
                    "CARDINALITY_CONFLICT",
                    value.node_id,
                    constraint="cardinality",
                    blocks=True,
                )
            )
        if count < constraints.min_count:
            value.issues.append(
                issue(
                    "missing", "MINIMUM_CARDINALITY_UNMET", value.node_id, constraint="cardinality"
                )
            )
        if constraints.require_complete_set or constraints.quantifier == "all":
            if not value.discovery or value.discovery.status != "complete":
                value.issues.append(
                    issue(
                        "incomplete", "OBJECT_UNIVERSE_OPEN", value.node_id, constraint="discovery"
                    )
                )
    elif value.state in {"ready", "missing"} and value.value is not None:
        if constraints.nonempty and value.value == "":
            value.issues.append(issue("invalid", "EMPTY_VALUE", value.node_id))
        if constraints.unit and (
            value.resolved_type.kind != "quantity" or value.resolved_type.unit != constraints.unit
        ):
            value.issues.append(issue("invalid", "UNIT_INCOMPATIBLE", value.node_id))
        if constraints.minimum is not None or constraints.maximum is not None:
            try:
                val = value.value.get("value") if isinstance(value.value, dict) else value.value
                number = Decimal(val)
                if constraints.minimum is not None and number < Decimal(constraints.minimum):
                    raise ValueError("below bound")
                if constraints.maximum is not None and number > Decimal(constraints.maximum):
                    raise ValueError("above bound")
            except (ValueError, TypeError, InvalidOperation):
                value.issues.append(issue("invalid", "VALUE_CONSTRAINT_VIOLATION", value.node_id))
    return summarize(value)


def apply_status_policy(value, policy):
    value.display_action = getattr(policy, value.state, "annotate")
    if value.display_action == "block":
        # Only this node's own issues block its descendants. Sibling issues remain independent.
        for problem in value.issues:
            problem.blocks_subtree = True
    for child in [*value.fields.values(), *value.items]:
        apply_status_policy(child, policy)
    return value


def sort_key(value):
    raw = business_value(value)
    kind = value.resolved_type.kind
    if kind == "quantity":
        return Decimal(raw["value"])
    if kind in {"integer", "decimal"}:
        return Decimal(raw)
    if kind == "datetime":
        return datetime.fromisoformat(raw)
    if kind == "entity":
        return value.entity_id
    if kind not in {"string", "boolean", "date", "year_month", "enum"}:
        raise ReportingError("ORDER_TYPE_UNSUPPORTED")
    return raw


def select_nodes(value, path):
    """Return selected nodes and blocking ancestor issues, without sibling contamination."""
    ancestors = [i for i in value.issues if i.blocks_subtree]
    if not path:
        return [value], ancestors
    if value.resolved_type.kind == "list":
        nodes, issues = [], list(ancestors)
        for child in value.items:
            selected, child_issues = select_nodes(child, path)
            nodes.extend(selected)
            issues.extend(child_issues)
        return nodes, issues
    child = value.fields.get(path[0])
    if child is None:
        return [], [*ancestors, issue("invalid", "INPUT_FIELD_UNKNOWN", value.node_id)]
    selected, issues = select_nodes(child, path[1:])
    return selected, [*ancestors, *issues]


def project_value(value, path=()):
    """Preserve declared list shape, discovery, identity and ancestor constraints."""
    if not path:
        return value.model_copy(deep=True)
    if value.resolved_type.kind == "list":
        prototype = ResolvedValue(
            node_id=value.node_id, resolved_type=value.resolved_type.item_type
        )
        projected_type = projection_type(prototype.resolved_type, path)
        result = value.model_copy(deep=True)
        result.resolved_type = TypeSpec(
            kind="list", item_type=projected_type, item_identity=value.resolved_type.item_identity
        )
        result.items = [project_value(child, path) for child in value.items]
        result.fields = {}
        return summarize(result)
    child = value.fields.get(path[0])
    if child is None:
        raise ReportingError("INPUT_FIELD_UNKNOWN")
    result = project_value(child, path[1:])
    result.issues.extend(deepcopy([i for i in value.issues if i.blocks_subtree]))
    return summarize(result)


def projection_type(typ, path):
    if not path:
        return typ
    if typ.kind == "list":
        return TypeSpec(
            kind="list",
            item_type=projection_type(typ.item_type, path),
            item_identity=typ.item_identity,
        )
    if typ.kind != "record" or path[0] not in typ.fields:
        raise ReportingError("INPUT_FIELD_UNKNOWN")
    return projection_type(typ.fields[path[0]].type, path[1:])


def business_value(value, path=(), *, allow_partial=False):
    selected = project_value(value, path)

    def convert(node):
        if node.resolved_type.kind == "list":
            if any(i.blocks_subtree for i in node.issues):
                raise ReportingError("INPUT_CONSUMPTION_BLOCKED")
            if not allow_partial and (not node.discovery or node.discovery.status != "complete"):
                raise ReportingError("OBJECT_UNIVERSE_OPEN")
            return [convert(item) for item in node.items]
        if node.resolved_type.kind == "record":
            if any(i.blocks_subtree for i in node.issues):
                raise ReportingError("INPUT_CONSUMPTION_BLOCKED")
            return {
                "entity_id": node.entity_id,
                **{key: convert(child) for key, child in node.fields.items()},
            }
        if node.state != "ready":
            raise ReportingError(
                "INPUT_CONSUMPTION_BLOCKED", issue_refs=[i.issue_id for i in all_issues(node)]
            )
        return deepcopy(node.value)

    return convert(selected)


def evaluate_uses(plan, inputs, evaluations=None):
    from app.services.reporting.condition_resolver import evaluate_expression

    outputs, coverage, blockers = {}, [], {}

    def activation(guards, items):
        uncertain = False
        for guard in guards:
            evaluated = evaluate_expression(guard["expression"], inputs, items=items)
            result = evaluated["result"]
            branch = guard["branch"]
            if result != "UNKNOWN" and branch != result.lower():
                return "inactive"
            if result == "UNKNOWN":
                # An explicit unknown branch is usable for diagnostics; business branches
                # stay pending and retain their mandatory requirements.
                uncertain |= branch != "unknown"
        return "pending" if uncertain else "active"

    def inspect(value, paths, *, enumeration=False, required=False, constraints=None):
        nodes, ancestor_issues = select_nodes(value, paths)
        issues = {i.issue_id: i for i in ancestor_issues}
        for node in nodes:
            current = (
                node.issues
                if enumeration or (required and node.resolved_type.kind in {"record", "list"})
                else all_issues(node)
            )
            issues.update({i.issue_id: i for i in current})
            if constraints:
                checked = node.model_copy(deep=True)
                apply_constraints(checked, Constraints.model_validate(constraints))
                issues.update({i.issue_id: i for i in checked.issues})
            if (
                required
                and node.resolved_type.kind not in {"record", "list"}
                and node.state in {"missing", "pending_review", "unavailable"}
                and not current
            ):
                i = issue(node.state, "REQUIRED_INPUT_UNMET", node.node_id)
                issues[i.issue_id] = i
            if required and node.state in {"confirmed_absent", "not_applicable"}:
                allowed = (constraints or {}).get(
                    "allow_absence" if node.state == "confirmed_absent" else "allow_not_applicable",
                    False,
                )
                if not allowed or (
                    node.state == "confirmed_absent" and (constraints or {}).get("min_count", 0) > 0
                ):
                    i = issue("missing", "REQUIRED_INPUT_UNMET", node.node_id)
                    issues[i.issue_id] = i
        if required and not nodes:
            i = issue("missing", "REQUIRED_INPUT_UNMET", value.node_id)
            issues[i.issue_id] = i
        return list(issues.values())

    def contexts(entry):
        contexts = [{}]
        for iteration in entry.get("iterations", []):
            contexts_next = []
            for items in contexts:
                source = (items if iteration.get("scope") == "item" else inputs).get(
                    iteration["input_id"]
                )
                if source is None:
                    continue
                selected = project_value(source, iteration.get("field_path", []))
                for row in selected.items:
                    scoped = row.model_copy(deep=True)
                    scoped.issues.extend(deepcopy([i for i in selected.issues if i.blocks_subtree]))
                    contexts_next.append({**items, iteration["input_id"]: scoped})
            contexts = contexts_next
        return contexts

    def expanded(entries):
        for entry in entries:
            for items in contexts(entry):
                yield (
                    entry,
                    items,
                    {key: row.entity_id or row.node_id for key, row in items.items()},
                )

    for output, uses in plan["uses"].items():
        resolutions = []
        for use, items, item_refs in expanded(uses):
            active = activation(use.get("guards", []), items)
            source = items if use.get("scope") == "item" else inputs
            value = source.get(use["input_id"])
            found = (
                inspect(value, use.get("field_path", []), enumeration=use.get("enumeration", False))
                if value
                else []
            )
            status = (
                "inactive"
                if active == "inactive"
                else "blocked"
                if any(i.blocks_subtree for i in found)
                else "diagnostic_only"
                if found or active == "pending"
                else "usable"
            )
            resolutions.append(
                {
                    **use,
                    "status": status,
                    "item_refs": item_refs,
                    "issue_refs": [i.issue_id for i in found],
                }
            )
            if active != "inactive":
                for i in found:
                    if i.state in {"invalid", "conflict"}:
                        blockers[i.issue_id] = i
        outputs[output] = resolutions
    for req, items, item_refs in expanded(plan["requirements"]):
        source = items if req.get("scope") == "item" else inputs
        value = source.get(req["input_id"])
        active = activation(req.get("guards", []), items)
        required = req["required"]
        found = []
        if value:
            if required:
                found = inspect(
                    value, req["field_path"], required=True, constraints=req["constraints"]
                )
            # Required fields belong to the declared input requirement; a conditional
            # field use must not re-introduce every sibling under that guard.
            if not req["field_path"] and not req.get("iterations"):

                def required_fields(record):
                    if record.resolved_type.kind == "list":
                        for child in record.items:
                            required_fields(child)
                    for key, field in record.resolved_type.fields.items():
                        if key in record.fields:
                            if field.required:
                                found.extend(
                                    inspect(
                                        record.fields[key],
                                        [],
                                        required=True,
                                        constraints=field.constraints.model_dump(),
                                    )
                                )
                            required_fields(record.fields[key])

                required_fields(value)
        elif required:
            found = [issue("missing", "REQUIRED_INPUT_UNMET", req["input_id"])]
        if active == "pending" and required:
            found.append(issue("incomplete", "CONDITION_UNKNOWN", req["input_id"]))
        satisfied = active == "inactive" or not found
        for i in found:
            if active != "inactive" and i.state in UNUSABLE:
                blockers[i.issue_id] = i
        coverage.append(
            {
                **req,
                "item_refs": item_refs,
                "activation": active,
                "satisfied": satisfied,
                "issue_refs": [i.issue_id for i in found],
            }
        )
    states = {i.state for i in blockers.values()}
    material = (
        "invalid"
        if "invalid" in states
        else "conflict"
        if "conflict" in states
        else "incomplete"
        if states
        else "ready"
    )
    return {
        "use_resolutions": outputs,
        "coverage": coverage,
        "material_status": material,
        "blocking_issue_refs": sorted(blockers),
        "blocking_issues": [i.model_dump(mode="json") for i in blockers.values()],
    }
