"""Compile immutable definitions into a typed dependency and consumption plan."""

from __future__ import annotations

from copy import deepcopy

from pydantic import ValidationError

from app.services.extraction.evidence_identity import evidence_hash
from app.services.reporting.template_v2 import (
    Diagnostic,
    InputRef,
    Projection,
    ReportingError,
    Step,
    TemplateV2,
    TypeField,
    TypeSpec,
)

COMPILER_VERSION = "output-compiler-v2.2"
XSD = "http://www.w3.org/2001/XMLSchema#"
DATATYPES = {
    XSD + name: kind
    for kind, names in {
        "string": ["string", "normalizedString", "token", "anyURI"],
        "boolean": ["boolean"],
        "integer": ["integer", "int", "long", "short", "nonNegativeInteger", "positiveInteger"],
        "decimal": ["decimal"],
        "date": ["date"],
        "datetime": ["dateTime"],
        "year_month": ["gYearMonth"],
    }.items()
    for name in names
}


def expression_refs(value):
    """Only structural InputRefs authorize data, never text or labels."""
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    if isinstance(value, dict):
        if value.get("kind") == "input_ref":
            yield value
        elif value.get("op") == "input" and value.get("input_ref"):
            yield value["input_ref"]
        else:
            for item in value.values():
                yield from expression_refs(item)
    elif isinstance(value, list):
        for item in value:
            yield from expression_refs(item)


def walk_groups(groups, repeats=()):
    for group in groups:
        current = (*repeats, group.repeat) if group.repeat else repeats
        yield group, current
        yield from walk_groups(group.groups, current)


def field_type(typ, path, *, iteration=False):
    from app.services.reporting.input_resolver import projection_type

    typ = TypeSpec.model_validate(typ) if isinstance(typ, dict) else typ
    if iteration and typ.kind == "list":
        typ = typ.item_type
    return projection_type(typ, path)


def migration_semantics_hash(template):
    data = TemplateV2.model_validate(template).model_dump(mode="json")
    for key in ("migration_issues", "template_revision_id", "revision_no"):
        data.pop(key)
    return evidence_hash(data)


def node_uses(nodes, guards=(), item_inputs=()):
    for node in nodes:
        if node.kind == "input_ref":
            yield {
                "input_id": node.input_id,
                "field_path": node.field_path,
                "guards": list(guards),
                "scope": node.scope,
                "iteration": any(i["input_id"] == node.input_id for i in item_inputs),
                "iterations": list(item_inputs),
                "format": node.format.model_dump(mode="json"),
            }
        elif node.kind == "if":
            for ref in expression_refs(node.condition):
                yield {
                    **ref,
                    "guards": list(guards),
                    "guard_parameter": True,
                    "iteration": any(i["input_id"] == ref["input_id"] for i in item_inputs),
                    "iterations": list(item_inputs),
                }
            for branch, children in (
                ("true", node.children),
                ("false", node.otherwise),
                ("unknown", node.unknown),
            ):
                guard = {"expression": node.condition.model_dump(mode="json"), "branch": branch}
                yield from node_uses(children, (*guards, guard), item_inputs)
        elif node.kind == "repeat":
            yield {
                "input_id": node.input_id,
                "field_path": node.field_path,
                "guards": list(guards),
                "enumeration": True,
                "scope": node.scope,
                "iterations": list(item_inputs),
                "iteration": node.scope == "item" and bool(item_inputs),
            }
            yield from node_uses(
                node.children,
                guards,
                (
                    *item_inputs,
                    {"input_id": node.input_id, "field_path": node.field_path, "scope": node.scope},
                ),
            )
        else:
            yield from node_uses(node.children, guards, item_inputs)


def render_uses(unit):
    guards = (
        [{"expression": unit.when.model_dump(mode="json"), "branch": "true"}] if unit.when else []
    )
    if unit.when:
        for ref in expression_refs(unit.when):
            yield {**ref, "guards": [], "guard_parameter": True}
    render = unit.render
    if render.kind == "table":
        base = render.rows.model_dump(mode="json")
        yield {**base, "guards": guards, "enumeration": True}
        for column in render.columns:
            if column.field_ref:
                yield {
                    **base,
                    "field_path": [*base["field_path"], column.field_ref],
                    "guards": guards,
                    "format": column.format.model_dump(mode="json"),
                }
        for field in [
            *[o.field_ref for o in render.order_by if o.field_ref],
            *render.group_by,
        ]:
            yield {
                **base,
                "field_path": [*base["field_path"], field],
                "guards": guards,
                "iteration": True,
            }
    elif render.kind == "form":
        for field in render.fields:
            if field.value:
                yield {
                    **field.value.model_dump(mode="json"),
                    "guards": guards,
                    "format": field.format.model_dump(mode="json"),
                }
    elif render.kind == "list":
        yield {**render.items.model_dump(mode="json"), "guards": guards, "enumeration": True}
        yield from node_uses(render.nodes, guards, (render.items.model_dump(mode="json"),))
    else:
        yield from node_uses(render.nodes, guards)
        if render.kind == "narrative":
            if render.prompt:
                for ref in [*render.prompt.input_refs, *render.prompt.required_refs]:
                    yield {**ref.model_dump(mode="json"), "guards": guards}
            if render.fallback:
                yield from node_uses(render.fallback, guards)


class Compiler:
    def __init__(self, template, schema, contracts):
        self.template = TemplateV2.model_validate(template).model_copy(deep=True)
        self.authored_hash = evidence_hash(self.template)
        self.schema, self.contracts = deepcopy(schema), deepcopy(contracts)
        self.diagnostics, self.contract_hashes = [], {}
        self.generated_contracts = {}
        self.graph, self.input_types, self.binding_types, self.uses = {}, {}, {}, {}
        self.requirements, self.order = [], []
        self.inputs = self.template.definitions.inputs
        self.bindings = self.template.definitions.bindings
        self.slots = {s.source_slot_id: s for s in self.template.source_slots}

    def error(self, code, path="", **kwargs):
        self.diagnostics.append(Diagnostic(code=code, schema_path=path, **kwargs).model_dump())

    def contract(self, ref, kinds, path=""):
        record = self.contracts.get(ref)
        if not record or record.get("kind") not in kinds:
            self.error("UNKNOWN_CONTRACT", path, actual=ref, expected=sorted(kinds))
            return {}
        generated = self.generated_contracts.get(ref) == record
        if (record.get("status") != "published" and not generated) or record.get("is_disabled"):
            self.error(
                "RULE_REVISION_NOT_PUBLISHED" if "rule" in kinds else "CONTRACT_NOT_PUBLISHED",
                path,
                actual=ref,
            )
        definition = record.get("definition", {})
        if record["kind"] in {"rule", "condition", "claim"}:
            from app.services.reporting.condition_resolver import validate_bound_expression

            names = set(definition.get("parameters", {})) if record["kind"] != "claim" else None
            for expression in _all_nodes(definition):
                if "op" in expression:
                    try:
                        validate_bound_expression(expression, names)
                    except (ReportingError, ValidationError) as exc:
                        self.error(getattr(exc, "code", "CONTRACT_SCHEMA_INVALID"), path)
            branches = [b.get("branch_id") for b in definition.get("branches", [])]
            if len(set(branches)) != len(branches):
                self.error("DUPLICATE_RULE_BRANCH", path)
        expected = record.get("definition_hash")
        if expected and expected != evidence_hash(definition):
            self.error("CONTRACT_HASH_MISMATCH", path, actual=ref)
        self.contract_hashes[ref] = evidence_hash(record)
        return definition

    def class_matches(self, actual, expected):
        return actual == expected or expected in self.schema.get(actual, {}).get("parents", [])

    def require_class(self, iri, path):
        if iri not in self.schema:
            self.error("UNKNOWN_ONTOLOGY_REFERENCE", path, actual=iri)

    def properties(self, iri, kind):
        data = {}
        for parent in [*self.schema.get(iri, {}).get("parents", []), iri]:
            data.update({p["iri"]: p for p in self.schema.get(parent, {}).get(kind, [])})
        return data

    def path_types(self, classes, steps, path):
        current = set(classes)
        for index, step in enumerate(steps):
            following = set()
            if index >= self.template.budget.max_hops:
                self.error("TRAVERSAL_BUDGET_EXCEEDED", path)
                break
            for cls in current:
                if step.direction == "forward":
                    prop = self.properties(cls, "relationships").get(step.predicate_iri)
                    if prop:
                        ranges = prop.get("range", [])
                        following.update([ranges] if isinstance(ranges, str) else ranges)
                    else:
                        self.error(
                            "ONTOLOGY_PATH_TYPE_MISMATCH",
                            path,
                            actual=step.predicate_iri,
                            expected={"domain": cls, "kind": "relationship"},
                        )
                else:
                    matches = {
                        domain
                        for domain in self.schema
                        for prop in self.properties(domain, "relationships").values()
                        if prop["iri"] == step.predicate_iri
                        and any(
                            self.class_matches(cls, end)
                            for end in (
                                [prop["range"]]
                                if isinstance(prop.get("range"), str)
                                else prop.get("range", [])
                            )
                        )
                    }
                    following.update(matches)
                    if not matches:
                        self.error("ONTOLOGY_PATH_TYPE_MISMATCH", path, actual=step.predicate_iri)
            current = following
            for iri in current:
                self.require_class(iri, path)
        return sorted(current)

    def projection_type(self, projection, base, path):
        p = projection
        entity = base.item_type if base.kind == "list" else base
        if p.kind == "identity" and entity.kind != "entity":
            return base
        if p.kind == "field":
            return field_type(base, p.field_path)
        if entity.kind != "entity":
            if p.kind in {"record", "records"}:
                return self.record_type(p, base, path)
            raise ReportingError(
                "INPUT_TYPE_MISMATCH", "projection requires entity or record fields"
            )
        classes = self.path_types(entity.class_iris, p.predicate_path, path)
        if p.class_iri:
            self.require_class(p.class_iri, path)
            if not any(
                self.class_matches(c, p.class_iri) or self.class_matches(p.class_iri, c)
                for c in classes
            ):
                self.error(
                    "ONTOLOGY_PATH_TYPE_MISMATCH", path, actual=p.class_iri, expected=classes
                )
            classes = [p.class_iri]
        if p.kind in {"identity", "entity", "entities"}:
            result = TypeSpec(
                kind="entity",
                class_iris=classes or entity.class_iris,
                display_property_iri=p.display_property_iri,
            )
            if p.display_property_iri:
                self.projection_type(
                    Projection(kind="property", property_iri=p.display_property_iri),
                    result,
                    path + ".display_property_iri",
                )
            return (
                TypeSpec(kind="list", item_type=result, item_identity=p.item_identity)
                if p.kind == "entities"
                else result
            )
        if p.kind == "relation_presence":
            return TypeSpec(kind="boolean")
        if p.kind in {"record", "records"}:
            return self.record_type(p, TypeSpec(kind="entity", class_iris=classes), path)
        specs = []
        for cls in classes:
            prop = self.properties(cls, "properties").get(p.property_iri)
            if not prop:
                relationship = any(
                    p.property_iri in self.properties(c, "relationships") for c in self.schema
                )
                self.error(
                    "ONTOLOGY_PATH_TYPE_MISMATCH" if relationship else "UNKNOWN_ONTOLOGY_REFERENCE",
                    path,
                    actual=p.property_iri,
                )
                continue
            if prop.get("unsupported_constraints") or prop.get("pattern") or prop.get("facets"):
                self.error("UNSUPPORTED_ONTOLOGY_CONSTRAINT", path, actual=prop)
            if prop.get("type"):
                specs.append(TypeSpec.model_validate(prop["type"]))
                continue
            ranges = prop.get("range", [])
            if isinstance(ranges, str):
                ranges = [ranges]
            datatype = prop.get("datatype")
            if not ranges and datatype:
                # The engine's datatype is a registered XSD primitive, never an IRI guess.
                ranges = [datatype if datatype in DATATYPES else XSD + datatype]
            if len(ranges) != 1 or ranges[0] not in DATATYPES:
                self.error("UNSUPPORTED_DATATYPE", path, actual=ranges)
                continue
            primitive = DATATYPES[ranges[0]]
            if prop.get("canonical_unit"):
                from app.services.extraction.literal_normalizer import unit_definition

                try:
                    dimension, _ = unit_definition(prop["canonical_unit"])
                    if primitive not in {"integer", "decimal"}:
                        raise ValueError("canonical unit requires numeric datatype")
                    specs.append(
                        TypeSpec(
                            kind="quantity",
                            datatype_iri=ranges[0],
                            unit=prop["canonical_unit"],
                            dimension=dimension,
                        )
                    )
                except ValueError:
                    self.error("UNSUPPORTED_ONTOLOGY_UNIT", path, actual=prop["canonical_unit"])
            else:
                specs.append(TypeSpec(kind=primitive, datatype_iri=ranges[0]))
        if not specs:
            return TypeSpec(kind="string")
        if any(s != specs[0] for s in specs[1:]):
            self.error("INPUT_TYPE_MISMATCH", path, actual="incompatible union property types")
        result = specs[0]
        if p.type_contract_ref:
            declared = self.contract(p.type_contract_ref, {"property_type"}, path)
            if (
                declared.get("ontology_release_ref") != self.template.ontology_release_ref
                or declared.get("property_iri") != p.property_iri
                or not all(
                    self.class_matches(c, declared.get("subject_class_iri")) for c in classes
                )
            ):
                raise ReportingError("PROPERTY_TYPE_SCOPE_MISMATCH")
            typ = TypeSpec.model_validate(declared["output_type"])
            if not (
                compatible(result, typ)
                or (result.kind in {"integer", "decimal"} and typ.kind == "quantity")
                or (result.kind == "string" and typ.kind == "enum")
            ):
                raise ReportingError("INPUT_TYPE_MISMATCH")
            result = typ.model_copy(
                update={
                    "type_contract_ref": p.type_contract_ref,
                    "datatype_iri": result.datatype_iri,
                }
            )
        self.validate_type(result, path)
        return result

    def record_type(self, p, base, path):
        fields = {}
        for key, field in p.fields.items():
            typ = self.projection_type(field.value, base, path + ".fields." + key)
            fields[key] = TypeField(
                type=typ,
                semantic_ref=field.value.property_iri or evidence_hash(field.value),
                required=field.required,
                constraints=field.constraints,
            )
        record = TypeSpec(kind="record", fields=fields, item_identity=p.item_identity)
        return (
            TypeSpec(kind="list", item_type=record, item_identity=p.item_identity)
            if p.kind == "records"
            else record
        )

    def dependency(self, kind, ref):
        key = kind + ":" + ref
        if key in self.graph:
            return key
        self.graph[key] = set()
        if kind == "input":
            item = self.inputs.get(ref)
            if not item:
                self.error("UNRESOLVED_INPUT_REFERENCE", "definitions.inputs." + ref)
            else:
                self.graph[key].add(self.dependency("binding", item.binding_ref))
        elif kind == "binding":
            item = self.bindings.get(ref)
            if not item:
                self.error("UNRESOLVED_BINDING_REFERENCE", "definitions.bindings." + ref)
            else:
                for dep in item.dependencies:
                    self.graph[key].add(self.dependency(dep.kind, dep.ref))
                if item.kind == "facts":
                    if item.scope.root.kind == "binding":
                        self.graph[key].add(self.dependency("binding", item.scope.root.binding_ref))
                    for condition in item.scope.condition_refs:
                        self.graph[key].add(self.dependency("condition", condition))
                elif item.kind == "derived":
                    for child in expression_refs(item.model_dump(mode="json")):
                        self.graph[key].add(self.dependency("input", child["input_id"]))
        elif kind == "condition":
            definition = self.contract(ref, {"condition_binding"}, key)
            condition = self.contract(definition.get("condition_ref"), {"condition"}, key)
            if set(definition.get("parameters", {})) != set(condition.get("parameters", {})):
                self.error("RULE_PARAMETER_MISMATCH", key)
            for child in definition.get("parameters", {}).values():
                try:
                    mapped = InputRef.model_validate(child)
                    self.graph[key].add(self.dependency("input", mapped.input_id))
                except ValidationError:
                    self.error("UNRESOLVED_INPUT_REFERENCE", key)
        return key

    def sort_graph(self):
        done, visiting = set(), []

        def visit(key):
            if key in visiting:
                self.error("DEPENDENCY_CYCLE", key, actual=[*visiting[visiting.index(key) :], key])
                return
            if key in done:
                return
            visiting.append(key)
            for dep in sorted(self.graph[key]):
                visit(dep)
            visiting.pop()
            done.add(key)
            self.order.append(key)

        for key in sorted(self.graph):
            visit(key)

    def infer_binding(self, ref):
        item = self.bindings[ref]
        path = "definitions.bindings." + ref
        if item.kind == "facts":
            contract, scope = item.contract_ref, item.scope
            if contract.release_ref not in {
                self.template.ontology_release_ref,
                "template.ontology_release_ref",
            }:
                self.error("ONTOLOGY_RELEASE_MISMATCH", path)
            else:
                contract.release_ref = self.template.ontology_release_ref
            slot = self.slots.get(scope.source_slot)
            if (
                contract.root_class_iri == "auto:class"
                and scope.root.kind == "source_root"
                and slot
            ):
                contract.root_class_iri = slot.class_iri
            self.require_class(contract.root_class_iri, path)
            if not slot:
                self.error("UNKNOWN_SOURCE_SLOT", path, actual=scope.source_slot)
            elif scope.root.kind == "source_root" and not self.class_matches(
                slot.class_iri, contract.root_class_iri
            ):
                self.error("ONTOLOGY_PATH_TYPE_MISMATCH", path, actual=slot.class_iri)
            for source in scope.fact_source_refs:
                if source not in self.slots:
                    self.error("UNKNOWN_SOURCE_SLOT", path, actual=source)
            result = self.path_types([contract.root_class_iri], scope.predicate_path, path)
            if contract.result_class_iri == "auto:class" and len(result) == 1:
                contract.result_class_iri = result[0]
            self.require_class(contract.result_class_iri, path)
            if not any(
                self.class_matches(c, contract.result_class_iri)
                or self.class_matches(contract.result_class_iri, c)
                for c in result
            ):
                self.error(
                    "ONTOLOGY_PATH_TYPE_MISMATCH",
                    path,
                    expected=result,
                    actual=contract.result_class_iri,
                )
            return TypeSpec(
                kind="list",
                item_type=TypeSpec(kind="entity", class_iris=[contract.result_class_iri]),
                item_identity="entity_id",
                cardinality=scope.cardinality,
            )
        expected_kinds = (
            {"context", "parameter"}
            if item.kind == "context"
            else {"workflow"}
            if item.kind == "workflow"
            else {"rule"}
            if item.provider == "rule_result"
            else {"calculation"}
            if item.provider == "calculation"
            else {"view"}
        )
        if (
            item.kind == "derived"
            and item.provider == "view"
            and (item.contract_ref == "auto:view" or item.contract_ref.startswith("unresolved:"))
        ):
            self.generate_view(item, path)
        definition = self.contract(item.contract_ref, expected_kinds, path)
        if item.kind == "derived" and item.provider == "calculation":
            check = next(
                (c for c in self.template.calculation_checks if c.check_id == item.check_ref), None
            )
            if not check or check.contract_ref != item.contract_ref:
                self.error("CALCULATION_CHECK_UNBOUND", path)
        if not definition.get("output_type"):
            raise ReportingError("INPUT_TYPE_MISMATCH", "contract requires explicit output_type")
        if item.kind == "derived" and item.provider == "rule_result":
            if not definition.get("claims_reviewed") or not definition.get("review_ref"):
                self.error("CLAIM_PRECONDITION_UNPROVEN", path)
            if set(item.parameters) != set(definition.get("parameters", {})):
                self.error("RULE_PARAMETER_MISMATCH", path)
            for param, input_ref in item.parameters.items():
                declared = definition.get("parameters", {}).get(param, {})
                actual = self.input_types.get(input_ref.input_id)
                if actual:
                    actual = field_type(actual, input_ref.field_path)
                    expected = declared.get("type", declared)
                    if expected.get("kind") and not compatible(
                        actual, TypeSpec.model_validate(expected)
                    ):
                        self.error("INPUT_TYPE_MISMATCH", path + ".parameters." + param)
            for branch in definition.get("branches", []):
                for output in branch.get("fields", {}).values():
                    if output.get("claim_ref"):
                        claim = self.contract(output["claim_ref"], {"claim"}, path)
                        if claim.get("category") != output.get("category"):
                            self.error("CLAIM_CATEGORY_INVALID", path)
                        if output["value"].get("op") != "literal" or output["value"].get(
                            "value"
                        ) != claim.get("text"):
                            self.error("CLAIM_TEXT_MISMATCH", path)
                        for name, parameter in claim.get("parameters", {}).items():
                            actual = definition.get("parameters", {}).get(name, {}).get("type")
                            if not actual or not compatible(
                                TypeSpec.model_validate(actual),
                                TypeSpec.model_validate(parameter["type"]),
                            ):
                                self.error("CLAIM_PARAMETER_MISMATCH", path)
        typ = TypeSpec.model_validate(definition["output_type"])
        self.validate_type(typ, path)
        if item.kind == "derived" and item.provider == "view":
            self.validate_view(item, definition, typ, path)
        return typ

    def validate_type(self, typ, path):
        for iri in typ.class_iris:
            self.require_class(iri, path)
        if typ.kind == "enum":
            vocabulary = self.contract(typ.vocabulary_ref, {"vocabulary"}, path)
            if typ.enum_values != vocabulary.get("values"):
                self.error("VOCABULARY_TYPE_MISMATCH", path)
        if typ.kind == "range" and (typ.lower_inclusive is None or typ.upper_inclusive is None):
            self.error("RANGE_BOUNDARY_UNRESOLVED", path)
        if typ.item_type:
            self.validate_type(typ.item_type, path + ".item_type")
        for name, field in typ.fields.items():
            self.validate_type(field.type, path + "." + name)

    def generate_view(self, binding, path):
        """Infer a mechanical view protocol from selected inputs and operations."""
        op = binding.operation
        source = field_type(self.input_types[op.source.input_id], op.source.field_path)
        if op.kind == "range":
            lower = field_type(source, [op.lower_field])
            upper = field_type(source, [op.upper_field])
            if not compatible(lower, upper):
                raise ReportingError("INPUT_TYPE_MISMATCH")
            explicit = binding.output_type
            typ = TypeSpec(
                kind="range",
                item_type=lower,
                lower_inclusive=op.lower_inclusive
                if op.lower_inclusive is not None
                else explicit.lower_inclusive
                if explicit
                else None,
                upper_inclusive=op.upper_inclusive
                if op.upper_inclusive is not None
                else explicit.upper_inclusive
                if explicit
                else None,
            )
        elif op.kind == "unit_convert":
            conversion = self.contract(op.conversion_ref, {"conversion"}, path)
            typ = source.model_copy(update={"unit": conversion["to_unit"]})
        elif op.kind in {"filter", "sort"}:
            typ = source.model_copy(deep=True)
        else:
            if source.kind != "list":
                raise ReportingError("INPUT_TYPE_MISMATCH")
            row, fields = source.item_type, {}
            if op.kind == "group":
                fields = {
                    key: TypeField(
                        type=field_type(row, [key]), semantic_ref=f"view:{binding.binding_id}:{key}"
                    )
                    for key in op.keys
                }
                fields["items"] = TypeField(type=source, semantic_ref="view:" + binding.binding_id)
            else:
                for name, selected in op.fields.items():
                    root = row
                    if op.kind == "join":
                        if not selected or selected[0] not in {"left", "right"}:
                            raise ReportingError("JOIN_SCOPE_INVALID")
                        if selected[0] == "right":
                            root = field_type(
                                self.input_types[op.right.input_id], op.right.field_path
                            ).item_type
                        selected = selected[1:]
                    fields[name] = TypeField(
                        type=field_type(root, selected),
                        semantic_ref=f"view:{binding.binding_id}:{name}",
                    )
            typ = TypeSpec(
                kind="list",
                item_type=TypeSpec(kind="record", fields=fields),
                item_identity=source.item_identity,
            )
        definition = {
            "output_type": typ.model_dump(mode="json"),
            "allowed_operations": [op.kind],
            "description": "由模板取数操作生成",
        }
        ref = "urn:template-view:" + evidence_hash([definition, op])
        contract = {
            "contract_id": ref,
            "kind": "view",
            "family_id": "template-view",
            "revision_no": 1,
            "status": "compiled",
            "is_disabled": False,
            "definition": definition,
            "definition_hash": evidence_hash(definition),
            "origin": "compiler_generated",
            "decision_refs": [],
        }
        self.generated_contracts[ref] = self.contracts[ref] = contract
        binding.contract_ref = ref

    def validate_view(self, binding, definition, typ, path):
        from app.services.reporting.condition_resolver import expression_type

        op = binding.operation
        if op.kind not in definition.get("allowed_operations", []):
            raise ReportingError("VIEW_OPERATION_NOT_REGISTERED")
        source = field_type(self.input_types[op.source.input_id], op.source.field_path)

        def matches(actual, expected):
            if not compatible(actual, expected):
                raise ReportingError("INPUT_TYPE_MISMATCH")

        if op.kind == "range":
            if source.kind != "record" or typ.kind != "range":
                raise ReportingError("INPUT_TYPE_MISMATCH")
            matches(field_type(source, [op.lower_field]), typ.item_type)
            matches(field_type(source, [op.upper_field]), typ.item_type)
            return
        if op.kind == "unit_convert":
            conversion = self.contract(op.conversion_ref, {"conversion"}, path)
            if (
                source.kind != "quantity"
                or typ.kind != "quantity"
                or conversion.get("from_unit") != source.unit
                or conversion.get("to_unit") != typ.unit
                or conversion.get("dimension") != source.dimension
                or source.dimension != typ.dimension
            ):
                raise ReportingError("UNIT_INCOMPATIBLE")
            return
        if source.kind != "list" or typ.kind != "list":
            raise ReportingError("INPUT_TYPE_MISMATCH")
        row, target = source.item_type, typ.item_type
        if op.kind in {"filter", "sort"}:
            matches(source, typ)
            for order in op.order_by:
                if order.field_ref:
                    field_type(row, [order.field_ref])
            if op.kind == "filter":
                if (
                    expression_type(
                        op.predicate, inputs=self.input_types, items={op.source.input_id: row}
                    ).kind
                    != "boolean"
                ):
                    raise ReportingError("CONDITION_TYPE_MISMATCH")
        elif op.kind == "group":
            if target.kind != "record" or "items" not in target.fields:
                raise ReportingError("INPUT_TYPE_MISMATCH")
            for key in op.keys:
                matches(field_type(row, [key]), field_type(target, [key]))
            matches(source, field_type(target, ["items"]))
        elif op.kind in {"project", "join"}:
            if target.kind != "record" or set(op.fields) != set(target.fields):
                raise ReportingError("INPUT_TYPE_MISMATCH")
            right = None
            if op.kind == "join":
                right = field_type(self.input_types[op.right.input_id], op.right.field_path)
                if right.kind != "list" or len(op.keys) != len(op.right_keys) or not op.keys:
                    raise ReportingError("JOIN_SCOPE_INVALID")
                right = right.item_type
                for left_key, right_key in zip(op.keys, op.right_keys):
                    matches(field_type(row, [left_key]), field_type(right, [right_key]))
            for name, selected in op.fields.items():
                root = row
                if op.kind == "join":
                    if not selected or selected[0] not in {"left", "right"}:
                        raise ReportingError("JOIN_SCOPE_INVALID")
                    root, selected = (row if selected[0] == "left" else right), selected[1:]
                matches(field_type(root, selected), target.fields[name].type)

    def closure(self, key):
        seen, stack = set(), [key]
        while stack:
            node = stack.pop()
            if node not in seen:
                seen.add(node)
                stack.extend(self.graph.get(node, []))
        return seen

    def requirement(
        self,
        input_id,
        path,
        origin,
        *,
        required=False,
        guards=(),
        constraints=None,
        iterations=(),
        scope="execution",
    ):
        definition = self.inputs.get(input_id)
        if not definition:
            return
        record = {
            "input_id": input_id,
            "field_path": path,
            "origin_refs": [origin],
            "required": required,
            "guards": list(guards),
            "iterations": list(iterations),
            "scope": scope,
            "constraints": constraints or definition.constraints.model_dump(mode="json"),
            "binding_ref": definition.binding_ref,
            "selector": self.bindings[definition.binding_ref].model_dump(mode="json")
            if definition.binding_ref in self.bindings
            else None,
        }
        identity = evidence_hash({k: v for k, v in record.items() if k != "origin_refs"})
        found = next((r for r in self.requirements if r["requirement_id"] == identity), None)
        if found:
            if origin not in found["origin_refs"]:
                found["origin_refs"].append(origin)
        else:
            self.requirements.append({"requirement_id": identity, **record})

    def compile(self):
        from app.services.reasoning.rule_service import check_plan
        from app.services.reasoning.rule_service import method as builtin_contract
        from app.services.reporting.template_v2 import CalculationCheck

        t = self.template
        for slot in t.source_slots:
            self.require_class(slot.class_iri, "source_slots." + slot.source_slot_id)
        model = self.contracts.get(t.ontology_release_ref)
        if model:
            self.contract_hashes[t.ontology_release_ref] = evidence_hash(model)
            if model.get("status") == "captured":
                self.error(
                    "ONTOLOGY_MODEL_NOT_PUBLISHED",
                    "ontology_release_ref",
                    severity="warning",
                    message="已固定模型快照；正式发布需完成本体发布流程",
                )
        t.calculation_checks = [
            CalculationCheck.model_validate(c)
            for c in check_plan(
                [s.model_dump(mode="json") for s in t.source_slots],
                [c.model_dump(mode="json") for c in t.calculation_checks],
            )
        ]
        # The catalog is the single source for automatically required methods.
        for check in t.calculation_checks:
            builtin = builtin_contract(check.contract_ref)
            if builtin and check.contract_ref not in self.contracts:
                self.contracts[check.contract_ref] = builtin
        seen_checks = set()
        for check in t.calculation_checks:
            definition = self.contract(check.contract_ref, {"calculation"}, "calculation_checks")
            builtin = builtin_contract(check.contract_ref)
            if not builtin or self.contracts.get(check.contract_ref) != builtin:
                self.error("CALCULATION_METHOD_MISMATCH", check.check_id)
            slot = self.slots.get(check.source_slot)
            if not slot or slot.class_iri != definition.get("root_class_iri"):
                self.error("CALCULATION_SOURCE_MISMATCH", check.check_id)
            if builtin:
                method = builtin["definition"]
                root, subject = method["root_class_iri"], method["subject_class_iri"]
                self.require_class(root, check.check_id)
                self.require_class(subject, check.check_id)
                ends = self.path_types(
                    [root],
                    [Step(predicate_iri=p) for p in method["predicate_path"]],
                    check.check_id,
                )
                if subject not in ends:
                    self.error("CALCULATION_SUBJECT_UNBOUND", check.check_id)
                properties = self.properties(subject, "properties")
                for name, parameter in method["parameters"].items():
                    if parameter["predicate_iri"] not in properties:
                        self.error("CALCULATION_PARAMETER_UNBOUND", check.check_id, actual=name)
            if check.check_id in seen_checks:
                self.error("DUPLICATE_ID", check.check_id)
            seen_checks.add(check.check_id)
        self.contract(t.style_profile_ref, {"style"}, "style_profile_ref")
        policy = self.contract(t.publication_policy_ref, {"policy"}, "publication_policy_ref")
        for issue in t.migration_issues:
            # A client's resolved_by string is not an approval receipt.
            resolution = self.contracts.get(issue.resolution_ref, {})
            proof = resolution.get("definition", {})
            problem = issue.model_dump(mode="json", exclude={"resolved_by", "resolution_ref"})
            if (
                resolution.get("status") != "published"
                or resolution.get("kind") != "migration_review"
                or resolution.get("is_disabled")
                or not proof.get("review_ref")
                or proof.get("issue_hash") != evidence_hash(problem)
                or proof.get("legacy_schema_hash") != (t.legacy.schema_hash if t.legacy else None)
                or proof.get("target_semantics_hash") != migration_semantics_hash(t)
            ):
                self.error(
                    issue.code, "migration_issues", message=issue.message, actual=issue.old_id
                )
            else:
                self.contract(issue.resolution_ref, {"migration_review"}, "migration_issues")
        ids, names = set(), set()
        for kind, collection in (("binding", self.bindings), ("input", self.inputs)):
            for key, item in collection.items():
                if key != getattr(item, kind + "_id"):
                    self.error("DEFINITION_ID_MISMATCH", "definitions." + kind + "s." + key)
                if kind == "input":
                    if item.name in names:
                        self.error("DUPLICATE_INPUT_NAME", key, actual=item.name)
                    names.add(item.name)
        units, unit_context = [], {}
        for section in t.sections:
            if section.section_id in ids:
                self.error("DUPLICATE_ID", section.section_id)
            ids.add(section.section_id)
            for req in section.completeness_requirements:
                self.dependency("input", req.input_ref)
                self.requirement(
                    req.input_ref,
                    req.field_path,
                    section.section_id,
                    required=True,
                    constraints=req.constraints.model_dump(mode="json"),
                    guards=[{"expression": req.when.model_dump(mode="json"), "branch": "true"}]
                    if req.when
                    else [],
                )
            for group, repeats in walk_groups(section.groups):
                if group.group_id in ids:
                    self.error("DUPLICATE_ID", group.group_id)
                ids.add(group.group_id)
                if len(repeats) > t.budget.max_repeat_depth:
                    self.error("REPEAT_DEPTH_EXCEEDED", group.group_id)
                for repeat in repeats:
                    self.dependency("input", repeat.input_ref)
                for unit in group.units:
                    if unit.output_id in ids:
                        self.error("DUPLICATE_ID", unit.output_id)
                    ids.add(unit.output_id)
                    units.append(unit)
                    unit_context[unit.output_id] = repeats
                    aliases = [u.alias for u in unit.inputs]
                    if len(set(aliases)) != len(aliases):
                        self.error("DUPLICATE_INPUT_ALIAS", unit.output_id)
                    allowed = {u.input_ref for u in unit.inputs}
                    uses = list(render_uses(unit))
                    self.uses[unit.output_id] = uses
                    for use in uses:
                        ref = use["input_id"]
                        if ref not in allowed or ref not in self.inputs:
                            self.error("UNRESOLVED_INPUT_REFERENCE", unit.output_id, input_ref=ref)
                        self.dependency("input", ref)
                    for use in unit.inputs:
                        self.dependency("input", use.input_ref)
                    if unit.render.kind == "static":
                        approved = self.contract(
                            unit.render.approval_ref, {"static"}, unit.output_id
                        )
                        if approved.get("nodes_hash") != evidence_hash(unit.render.nodes):
                            self.error("STATIC_CONTENT_NOT_APPROVED", unit.output_id)
                        if uses:
                            self.error("STATIC_DYNAMIC_REFERENCE", unit.output_id)
                    if unit.render.kind == "narrative" and unit.render.prompt:
                        self.contract(unit.render.prompt.policy_ref, {"prompt"}, unit.output_id)
                    for node in _all_nodes(unit.render.model_dump(mode="json")):
                        if node.get("signature_region_id") and not any(
                            s.get("region_id") == node["signature_region_id"]
                            for s in policy.get("signature_slots", [])
                        ):
                            self.error("UNKNOWN_SIGNATURE_REGION", unit.output_id)
        if len(units) > t.budget.max_units:
            self.error("OUTPUT_BUDGET_EXCEEDED", "sections")
        self.sort_graph()
        if not any(d["code"] == "DEPENDENCY_CYCLE" for d in self.diagnostics):
            for key in self.order:
                kind, ref = key.split(":", 1)
                try:
                    if kind == "binding" and ref in self.bindings:
                        inferred = self.infer_binding(ref)
                        declared = self.bindings[ref].output_type
                        if declared and not compatible(inferred, declared):
                            self.error(
                                "INPUT_TYPE_MISMATCH",
                                key,
                                expected=declared.model_dump(),
                                actual=inferred.model_dump(),
                            )
                        self.binding_types[ref] = inferred.model_dump(mode="json")
                    elif kind == "input" and ref in self.inputs:
                        item = self.inputs[ref]
                        base = self.binding_types.get(item.binding_ref)
                        if base:
                            typ = self.projection_type(
                                item.projection,
                                TypeSpec.model_validate(base),
                                "definitions.inputs." + ref,
                            )
                            if item.expected_type and not compatible(typ, item.expected_type):
                                self.error(
                                    "INPUT_TYPE_MISMATCH",
                                    ref,
                                    expected=item.expected_type.model_dump(),
                                    actual=typ.model_dump(),
                                )
                            self.input_types[ref] = typ.model_dump(mode="json")
                except (ReportingError, ValidationError, KeyError, TypeError, ValueError) as exc:
                    self.error(getattr(exc, "code", "INPUT_TYPE_MISMATCH"), key, message=str(exc))
        for unit in units:
            from app.services.reporting.condition_resolver import expression_type

            authorized_claims = set()
            for use in unit.inputs:
                for dependency in self.closure("input:" + use.input_ref):
                    binding = (
                        self.bindings.get(dependency[8:])
                        if dependency.startswith("binding:")
                        else None
                    )
                    if binding and binding.kind == "derived" and binding.provider == "rule_result":
                        definition = self.contracts.get(binding.contract_ref, {}).get(
                            "definition", {}
                        )
                        authorized_claims.update(
                            node["claim_ref"]
                            for node in _all_nodes(definition)
                            if node.get("claim_ref")
                        )
            for node in _all_nodes(unit.render.model_dump(mode="json")):
                requested = (
                    [node["claim_id"]]
                    if node.get("kind") == "claim_ref"
                    else node.get("claim_refs", [])
                )
                if set(requested) - authorized_claims:
                    self.error("CLAIM_INPUT_UNDECLARED", unit.output_id)
            guards = [(unit.when, [])] if unit.when else []
            for use in self.uses[unit.output_id]:
                guards.extend(
                    (guard["expression"], use.get("iterations", []))
                    for guard in use.get("guards", [])
                )
            for expression, iterations in guards:
                try:
                    items = {}
                    for iteration in iterations:
                        context = items if iteration.get("scope") == "item" else self.input_types
                        typ = field_type(
                            context[iteration["input_id"]], iteration.get("field_path", [])
                        )
                        if typ.kind != "list":
                            raise ReportingError("INPUT_ITERATION_REQUIRED")
                        items[iteration["input_id"]] = typ.item_type
                    if expression_type(expression, self.input_types, items=items).kind != "boolean":
                        raise ReportingError("CONDITION_TYPE_MISMATCH")
                except (ReportingError, KeyError, ValueError) as exc:
                    self.error(getattr(exc, "code", "CONDITION_TYPE_MISMATCH"), unit.output_id)
            declared = {use.binding_ref for use in unit.bindings}
            for use in unit.inputs:
                closure = self.closure("input:" + use.input_ref)
                missing = {k[8:] for k in closure if k.startswith("binding:")} - declared
                if missing:
                    self.error(
                        "UNDECLARED_BINDING_DEPENDENCY", unit.output_id, actual=sorted(missing)
                    )
                definition = self.inputs.get(use.input_ref)
                if definition:
                    guards = (
                        [{"expression": unit.when.model_dump(mode="json"), "branch": "true"}]
                        if unit.when
                        else []
                    )
                    self.requirement(
                        use.input_ref,
                        [],
                        unit.output_id,
                        required=use.required or definition.required,
                        guards=guards,
                    )
            for use in self.uses[unit.output_id]:
                typ = self.input_types.get(use["input_id"])
                if typ:
                    try:
                        if use.get("scope") == "item":
                            items = {}
                            for iteration in use.get("iterations", []):
                                context = (
                                    items if iteration.get("scope") == "item" else self.input_types
                                )
                                rows = field_type(
                                    context[iteration["input_id"]], iteration.get("field_path", [])
                                )
                                if rows.kind != "list":
                                    raise ReportingError("INPUT_ITERATION_REQUIRED")
                                items[iteration["input_id"]] = rows.item_type
                            if use["input_id"] not in items:
                                raise ReportingError("INPUT_ITERATION_REQUIRED")
                            typ = items[use["input_id"]]
                        selected = field_type(typ, use.get("field_path", []))
                        if use.get("enumeration") and selected.kind != "list":
                            raise ReportingError("INPUT_TYPE_MISMATCH", "enumeration requires list")
                        if use.get("scope") == "item" and not use.get("iteration"):
                            raise ReportingError("INPUT_ITERATION_REQUIRED")
                        fmt = use.get("format")
                        scalar = selected
                        while scalar.kind == "list":
                            scalar = scalar.item_type
                        if fmt:
                            allowed = {
                                "decimal": {"decimal", "integer"},
                                "date": {"date", "datetime", "year_month"},
                                "quantity": {"quantity"},
                                "entity_labels": {"entity"},
                                "entity_id": {"entity"},
                            }
                            if fmt["kind"] in allowed and scalar.kind not in allowed[fmt["kind"]]:
                                raise ReportingError("OUTPUT_FORMAT_INCOMPATIBLE")
                            if (
                                scalar.kind == "entity"
                                and fmt["kind"] != "entity_id"
                                and (
                                    not scalar.display_property_iri
                                    or (
                                        fmt.get("label_property_iri")
                                        and fmt["label_property_iri"] != scalar.display_property_iri
                                    )
                                )
                            ):
                                raise ReportingError("ENTITY_DISPLAY_UNDECLARED")
                    except ReportingError as exc:
                        self.error(exc.code, unit.output_id, message=str(exc))
                    self.requirement(
                        use["input_id"],
                        use.get("field_path", []),
                        unit.output_id,
                        guards=use.get("guards", []),
                        iterations=use.get("iterations", []),
                        scope=use.get("scope", "execution"),
                    )
            for repeat in unit_context[unit.output_id]:
                if self.input_types.get(repeat.input_ref, {}).get("kind") != "list":
                    self.error("INPUT_TYPE_MISMATCH", repeat.repeat_id, expected="list")
                for use in unit.inputs:
                    for node in self.closure("input:" + repeat.input_ref):
                        if node.startswith("binding:"):
                            b = self.bindings.get(node[8:])
                            if (
                                b
                                and b.kind == "facts"
                                and b.scope.root.repeat_ref == repeat.repeat_id
                            ):
                                self.error(
                                    "DEPENDENCY_CYCLE",
                                    repeat.repeat_id,
                                    actual=[repeat.repeat_id, node, repeat.repeat_id],
                                )
                self.requirement(repeat.input_ref, [], unit.output_id, required=True)
        for node, deps in self.graph.items():
            if node.startswith(("binding:", "condition:")):
                for dep in deps:
                    if dep.startswith("input:"):
                        self.requirement(dep[6:], [], node, required=True)
        for ref in self.inputs:
            if "input:" + ref not in self.graph:
                self.error("UNUSED_DEFINITION", "definitions.inputs." + ref, severity="warning")
        result = {
            "compiler_version": COMPILER_VERSION,
            "template": t.model_dump(mode="json"),
            "schema_hash": self.authored_hash,
            "ontology_hash": evidence_hash(self.schema),
            "contract_hashes": self.contract_hashes,
            "generated_contracts": self.generated_contracts,
            "node_order": self.order,
            "dependencies": {key: sorted(value) for key, value in self.graph.items()},
            "input_types": self.input_types,
            "binding_types": self.binding_types,
            "requirements": self.requirements,
            "uses": self.uses,
            "diagnostics": self.diagnostics,
            "valid": not any(d["severity"] == "error" for d in self.diagnostics),
        }
        return {**result, "compilation_id": evidence_hash(result)}


def _all_nodes(value):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _all_nodes(item)
    elif isinstance(value, list):
        for item in value:
            yield from _all_nodes(item)


def compatible(actual, expected):
    if actual.kind != expected.kind:
        return False
    if actual.kind == "list":
        return compatible(actual.item_type, expected.item_type) and (
            not expected.item_identity or actual.item_identity == expected.item_identity
        )
    if actual.kind == "range":
        return compatible(actual.item_type, expected.item_type) and all(
            getattr(expected, key) is None or getattr(actual, key) == getattr(expected, key)
            for key in ("lower_inclusive", "upper_inclusive")
        )
    if actual.kind == "record":
        return all(
            key in actual.fields
            and compatible(actual.fields[key].type, field.type)
            and actual.fields[key].semantic_ref == field.semantic_ref
            for key, field in expected.fields.items()
        )
    return all(
        not getattr(expected, key) or getattr(actual, key) == getattr(expected, key)
        for key in ("unit", "dimension", "datatype_iri", "class_iris", "vocabulary_ref")
    )


def compile_template(template, schema, contracts):
    try:
        return Compiler(template, schema, contracts).compile()
    except ValidationError as exc:
        return {
            "valid": False,
            "diagnostics": [
                Diagnostic(
                    code="TEMPLATE_SCHEMA_INVALID",
                    schema_path=".".join(map(str, e["loc"])),
                    message=e["msg"],
                ).model_dump()
                for e in exc.errors()
            ],
        }


def require_valid(plan):
    if not plan.get("valid"):
        raise ReportingError("TEMPLATE_COMPILATION_FAILED", diagnostics=plan.get("diagnostics", []))
    return plan
