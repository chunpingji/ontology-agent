"""Pure resolution and canonical identities. No database or live provider reads."""

from copy import deepcopy

from app.services.extraction.evidence_identity import evidence_hash
from app.services.reporting.binding_resolver import BindingResolver, FactSelection
from app.services.reporting.condition_resolver import evaluate_condition
from app.services.reporting.input_resolver import (
    RESOLVER_VERSION,
    apply_constraints,
    apply_status_policy,
    evaluate_uses,
    issue,
    project_value,
    select_nodes,
    summarize,
    unavailable_value,
)
from app.services.reporting.resolution_budget import ResolutionBudget
from app.services.reporting.template_compiler import require_valid
from app.services.reporting.template_v2 import ReportingError, TemplateV2


def resolve_snapshot(plan, bundle):
    require_valid(plan)
    plan, bundle = deepcopy(plan), deepcopy(bundle)
    bundle.setdefault("contracts", {}).update(plan.get("generated_contracts", {}))
    template = TemplateV2.model_validate(plan["template"])
    from app.services.reporting.calculation_execution import execute_checks

    calculation_checks = execute_checks(template, bundle)
    scopes, scope_values, instances = {}, {}, []
    root_scope = evidence_hash(
        {
            "roots": {
                slot: source.get("root_entity_id")
                for slot, source in bundle.get("sources", {}).items()
            },
            "applicable_at": bundle.get("applicable_at"),
            "repeat": [],
        }
    )
    rule_results, condition_results = {}, {}
    executed_nodes, binding_scopes = {}, {}
    budget = ResolutionBudget(template.budget.max_nodes)
    budget_issues = {}

    def exhausted(code="RESOLUTION_BUDGET_EXCEEDED"):
        problem = issue("incomplete", code, "report", constraint="scope", blocks=True)
        budget_issues[problem.issue_id] = problem.model_dump(mode="json")

    def resolve_scope(scope_id, repeat_items):
        if scope_id in scope_values:
            return scope_values[scope_id]
        inputs, bindings, conditions = {}, {}, {}
        resolver = BindingResolver(
            plan, bundle, inputs, bindings, conditions, scope_id, repeat_items, budget=budget
        )
        resolver.calculation_checks = calculation_checks
        blocked = set()
        try:
            for node in plan["node_order"]:
                kind, ref = node.split(":", 1)
                dependencies = plan["dependencies"].get(node, [])
                if any(d in blocked for d in dependencies):
                    blocked.add(node)
                    continue
                budget.consume()
                if kind == "binding":
                    binding = template.definitions.bindings[ref]
                    if (
                        binding.kind == "facts"
                        and binding.scope.root.kind == "repeat_item"
                        and (binding.scope.root.repeat_ref not in repeat_items)
                    ):
                        blocked.add(node)
                        continue
                    bindings[ref] = resolver.resolve(binding)
                elif kind == "condition":
                    conditions[ref] = evaluate_condition(ref, bundle, inputs, scope_id)
                else:
                    definition = template.definitions.inputs[ref]
                    binding = bindings.get(definition.binding_ref)
                    typ = plan["input_types"][ref]
                    if isinstance(binding, FactSelection):
                        result = binding.project(definition.projection, typ, ref)
                    elif binding is not None:
                        if definition.projection.kind == "field":
                            result = project_value(binding, definition.projection.field_path)
                        elif definition.projection.kind in {"record", "records"}:
                            result = _record_projection(binding, definition.projection, typ, ref)
                        else:
                            result = binding.model_copy(deep=True)
                    else:
                        result = unavailable_value(ref, typ, "unavailable", "BINDING_UNAVAILABLE")
                    result.input_id, result.input_definition_hash = ref, evidence_hash(definition)
                    result.node_id = ref
                    result.execution_scope_id = scope_id
                    apply_constraints(result, definition.constraints)
                    apply_status_policy(result, definition.status_policy)
                    inputs[ref] = summarize(result)
        except ReportingError as exc:
            if exc.code != "RESOLUTION_BUDGET_EXCEEDED":
                raise
            exhausted()
            for ref, typ in plan["input_types"].items():
                if ref not in inputs:
                    inputs[ref] = unavailable_value(
                        ref, typ, "incomplete", exc.code, execution_scope_id=scope_id
                    )
        binding_scopes[scope_id] = {
            key: {
                "roots": value.roots,
                "subjects": value.subjects,
                "discovery": value.discovery.model_dump(mode="json"),
                "issues": [problem.model_dump(mode="json") for problem in value.issues],
                "support": value.support,
            }
            for key, value in bindings.items()
            if isinstance(value, FactSelection)
        }
        scope_values[scope_id] = inputs
        executed_nodes[scope_id] = set(plan["node_order"]) - blocked
        rule_results[scope_id] = {
            key: value.derivation
            for key, value in bindings.items()
            if not isinstance(value, FactSelection) and value.derivation.get("rule_evaluation")
        }
        condition_results[scope_id] = conditions
        return inputs

    root_inputs = resolve_scope(root_scope, {})
    outputs_by_scope = {}

    def visit(groups, scope_id, repeat_items, chain, section_id):
        for group in groups:
            if len(instances) >= template.budget.max_units:
                exhausted("OUTPUT_BUDGET_EXCEEDED")
                return

            def contexts():
                if not group.repeat:
                    yield scope_id, repeat_items, chain
                    return
                current = resolve_scope(scope_id, repeat_items)
                driver = current.get(group.repeat.input_ref)
                emitted = False
                if driver and not any(i.blocks_subtree for i in driver.issues):
                    if len(driver.items) > template.budget.max_records:
                        exhausted()
                    for row in driver.items[: template.budget.max_records]:
                        if len(instances) >= template.budget.max_units or budget_issues:
                            exhausted("OUTPUT_BUDGET_EXCEEDED")
                            break
                        if not row.entity_id or any(i.blocks_subtree for i in row.issues):
                            continue
                        next_chain = [*chain, [group.repeat.repeat_id, row.entity_id]]
                        child_scope = evidence_hash([root_scope, next_chain])
                        next_items = {**repeat_items, group.repeat.repeat_id: row}
                        resolve_scope(child_scope, next_items)
                        emitted = True
                        yield child_scope, next_items, next_chain
                if not emitted:
                    yield scope_id, repeat_items, chain

            for current_scope, items, item_chain in contexts():
                for unit in group.units:
                    if len(instances) >= template.budget.max_units:
                        exhausted("OUTPUT_BUDGET_EXCEEDED")
                        return
                    instances.append(
                        {
                            "output_id": unit.output_id,
                            "execution_scope_id": current_scope,
                            "output_instance_id": evidence_hash([unit.output_id, item_chain]),
                            "section_id": section_id,
                            "group_id": group.group_id,
                            "repeat_chain": item_chain,
                        }
                    )
                    outputs_by_scope.setdefault(current_scope, set()).add(unit.output_id)
                visit(group.groups, current_scope, items, item_chain, section_id)

    for section in template.sections:
        visit(section.groups, root_scope, {}, [], section.section_id)
    all_blockers, coverage = dict(budget_issues), []
    for slot, source in bundle.get("sources", {}).items():
        for comparison in source.get("model_compatibility", []):
            if comparison["status"] in {"unknown", "incompatible"}:
                problem = issue(
                    "incomplete" if comparison["status"] == "unknown" else "invalid",
                    comparison["code"], slot, blocks=True,
                    message="来源模型版本无法验证或字段定义不兼容，请核对来源版本及受影响字段",
                    refs=[comparison["ontology_release"]],
                ).model_dump(mode="json")
                all_blockers[problem["issue_id"]] = problem
    for check in calculation_checks.values():
        all_blockers.update({i["issue_id"]: i for i in check["issues"]})
    for scope_id, values in scope_values.items():
        allowed_outputs = outputs_by_scope.get(scope_id, set())
        selected_plan = {
            **plan,
            "uses": {k: v for k, v in plan["uses"].items() if k in allowed_outputs},
            "requirements": [
                r
                for r in plan["requirements"]
                if any(
                    origin in allowed_outputs
                    or (
                        scope_id == root_scope
                        and any(origin == s.section_id for s in template.sections)
                    )
                    or origin in executed_nodes[scope_id]
                    for origin in r["origin_refs"]
                )
            ],
        }
        resolved = evaluate_uses(selected_plan, values, condition_results[scope_id])
        scopes[scope_id] = {
            "inputs": {key: value.model_dump(mode="json") for key, value in values.items()},
            "binding_scopes": binding_scopes[scope_id],
            **resolved,
        }
        all_blockers.update({i["issue_id"]: i for i in resolved["blocking_issues"]})
        coverage.extend({**item, "execution_scope_id": scope_id} for item in resolved["coverage"])
    if len(instances) > template.budget.max_units:
        problem = issue("incomplete", "OUTPUT_BUDGET_EXCEEDED", "report")
        all_blockers[problem.issue_id] = problem.model_dump(mode="json")
    states = {i["state"] for i in all_blockers.values()}
    material = (
        "invalid"
        if "invalid" in states
        else "conflict"
        if "conflict" in states
        else "incomplete"
        if states
        else "ready"
    )
    result = {
        "resolver_version": RESOLVER_VERSION,
        "source_bundle": bundle,
        "root_scope_id": root_scope,
        "inputs": {key: value.model_dump(mode="json") for key, value in root_inputs.items()},
        "scopes": scopes,
        "output_instances": instances[: template.budget.max_units],
        "condition_evaluations": condition_results,
        "rule_evaluations": rule_results,
        "calculation_checks": calculation_checks,
        "coverage": coverage,
        "material_status": material,
        "blocking_issue_refs": sorted(all_blockers),
        "blocking_issues": list(all_blockers.values()),
        "compilation_id": plan["compilation_id"],
        "budget_usage": {
            "nodes": budget.spent,
            "max_nodes": budget.limit,
            "outputs": len(instances),
        },
    }
    return {**result, "input_snapshot_id": evidence_hash(result)}


def _record_projection(source, projection, typ, node):
    from app.services.reporting.input_resolver import ResolvedValue
    from app.services.reporting.template_v2 import TypeSpec

    typ = TypeSpec.model_validate(typ)
    if projection.kind == "records":
        result = ResolvedValue(
            node_id=node,
            resolved_type=typ,
            discovery=deepcopy(source.discovery),
            issues=deepcopy(source.issues),
        )
        singular = projection.model_copy(update={"kind": "record"})
        for item in source.items:
            result.items.append(
                _record_projection(
                    item, singular, typ.item_type.model_dump(), node + "/" + item.entity_id
                )
            )
    else:
        result = ResolvedValue(
            node_id=node,
            resolved_type=typ,
            entity_id=source.entity_id,
            subject_refs=source.subject_refs,
            issues=deepcopy(source.issues),
        )
        for key, field in projection.fields.items():
            selected, blocked = select_nodes(source, field.value.field_path)
            result.fields[key] = (
                selected[0].model_copy(deep=True)
                if len(selected) == 1 and not blocked
                else unavailable_value(node + "/" + key, typ.fields[key].type)
            )
    return summarize(result)
