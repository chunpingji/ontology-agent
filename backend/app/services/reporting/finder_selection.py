"""Typed projections of bounded Finder results, never published assertions."""

import re
from copy import deepcopy
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from app.services.extraction.evidence_identity import evidence_hash
from app.services.reporting.binding_resolver import FactSelection
from app.services.reporting.input_resolver import (
    Discovery,
    ResolvedValue,
    apply_constraints,
    issue,
    summarize,
    typed_value,
    unavailable_value,
)
from app.services.reporting.template_v2 import ReportingError, TypeSpec


def graph_index(source):
    root = source["root_entity_id"]
    nodes = {root: {"object_class_iri": source["root_class_iri"], "object_data_properties": []}}
    edges = []

    def visit(items, parent, address):
        for index, item in enumerate(items):
            path = [*address, index]
            identity = "finder:" + evidence_hash([source["execution_id"], path])
            nodes[identity] = item
            edges.append((parent, item["predicate_iri"], identity))
            visit(item.get("sub_relationships", []), identity, path)

    visit(source.get("relationships", []), root, [])
    return nodes, edges


def class_matches(schema, actual, expected):
    todo, seen = [actual], set()
    while todo:
        current = todo.pop()
        if current == expected:
            return True
        if current not in seen:
            seen.add(current)
            todo.extend(schema.get(current, {}).get("parents", []))
    return False


def literal_value(raw, typ):
    """Convert declared lexical values only; reject qualifiers/ranges as scalars."""
    if not isinstance(raw, str) or typ.kind in {"string", "enum"}:
        return raw
    value = raw.strip()
    if typ.kind == "boolean":
        return {"true": True, "false": False, "是": True, "否": False}.get(value.casefold(), raw)
    if typ.kind == "integer" and re.fullmatch(r"[+-]?\d+", value):
        return int(value)
    if typ.kind == "decimal":
        try:
            return str(Decimal(value)) if Decimal(value).is_finite() else raw
        except InvalidOperation:
            return raw
    if typ.kind == "quantity":
        match = re.fullmatch(r"([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*([^\d\s]*)", value)
        if match and match[2] in {"", typ.unit}:
            return {"value": match[1], "unit": typ.unit, "dimension": typ.dimension}
    return raw


@dataclass
class FinderSelection(FactSelection):
    nodes: dict = field(default_factory=dict)
    edges: list = field(default_factory=list)
    schema: dict = field(default_factory=dict)

    @classmethod
    def create(cls, resolver, binding, source):
        nodes, edges = graph_index(source)
        result = cls(
            None,
            source=source,
            nodes=nodes,
            edges=edges,
            schema=resolver.bundle["schema"],
            scope_id=resolver.scope_id,
            budget=resolver.budget,
            max_records=resolver.plan["template"]["budget"]["max_records"],
        )
        scope = binding.scope
        result.consume(len(nodes))
        if (
            scope.fact_source_refs
            or scope.condition_refs
            or binding.assertion_nature != "observed_fact"
        ):
            result.issues.append(
                issue(
                    "unavailable", "FINDER_SEMANTICS_UNSUPPORTED", binding.binding_id, blocks=True
                )
            )
        if source.get("state") != "ready":
            result.issues.append(
                issue(
                    "unavailable",
                    source.get("code", "SOURCE_UNAVAILABLE"),
                    binding.binding_id,
                    blocks=True,
                )
            )
        if scope.root.kind == "source_root":
            roots = [source["root_entity_id"]]
        elif scope.root.kind == "entity_ref":
            roots = [scope.root.entity_id]
        elif scope.root.kind == "repeat_item":
            item = resolver.repeat_items.get(scope.root.repeat_ref)
            roots = [item.entity_id] if item else []
        else:
            upstream = resolver.bindings.get(scope.root.binding_ref)
            roots = upstream.subjects if isinstance(upstream, FinderSelection) else []
        result.roots = [
            r
            for r in roots
            if r in nodes
            and class_matches(
                result.schema, nodes[r]["object_class_iri"], binding.contract_ref.root_class_iri
            )
        ]
        result.subjects = [
            r
            for r in result.select(result.roots, scope.predicate_path)
            if class_matches(
                result.schema, nodes[r]["object_class_iri"], binding.contract_ref.result_class_iri
            )
            and (scope.object_ids is None or r in scope.object_ids)
        ]
        count = len(result.subjects)
        if scope.cardinality.max_count is not None and count > scope.cardinality.max_count:
            result.issues.append(
                issue(
                    "conflict",
                    "SUBJECT_AMBIGUOUS",
                    binding.binding_id,
                    constraint="cardinality",
                    blocks=True,
                )
            )
        if count < scope.cardinality.min_count:
            result.issues.append(issue("missing", "MINIMUM_CARDINALITY_UNMET", binding.binding_id))
        # Completeness describes this returned demo collection, never document discovery.
        result.discovery = Discovery(
            status="complete",
            expected_ids=result.subjects,
            proof_ref="finder-result:" + source["execution_id"],
        )
        if scope.require_complete_set:
            result.discovery.status = "open"
            result.issues.append(
                issue(
                    "incomplete",
                    "FINDER_COVERAGE_UNVERIFIED",
                    binding.binding_id,
                    constraint="discovery",
                )
            )
        return result

    def select(self, roots, steps):
        frontier = list(roots)
        for step in steps:
            following = []
            for parent, predicate, child in self.edges:
                self.consume()
                start, end = (parent, child) if step.direction == "forward" else (child, parent)
                if start in frontier and predicate == step.predicate_iri and end not in following:
                    following.append(end)
            frontier = following
        if len(frontier) > self.max_records:
            raise ReportingError("RESOLUTION_BUDGET_EXCEEDED")
        return frontier

    def provenance(self, subject, prop=None):
        item = prop if prop is not None else self.nodes[subject]
        return {
            "kind": "finder_demo",
            "source_job_id": self.source.get("job_id"),
            "template_id": self.source.get("template_id"),
            "execution_id": self.source["execution_id"],
            "object_id": subject,
            "source": deepcopy(item.get("source", {})),
            "verified_fact": False,
        }

    def project(self, projection, typ, node, subjects=None):
        self.consume()
        p, typ = projection, TypeSpec.model_validate(typ) if isinstance(typ, dict) else typ
        roots = self.subjects if subjects is None else subjects
        selected = self.select(roots, p.predicate_path)
        if p.class_iri:
            selected = [
                s
                for s in selected
                if class_matches(self.schema, self.nodes[s]["object_class_iri"], p.class_iri)
            ]
        if any(i.blocks_subtree for i in self.issues):
            value = unavailable_value(node, typ, "unavailable", "FINDER_INPUT_UNAVAILABLE")
            value.issues.extend(deepcopy(self.issues))
            return summarize(value)
        if p.kind in {"records", "entities"}:
            result = ResolvedValue(
                node_id=node,
                resolved_type=typ,
                execution_scope_id=self.scope_id,
                discovery=deepcopy(self.discovery),
            )
            result.discovery.expected_ids = selected
            singular = p.model_copy(
                update={"kind": "record" if p.kind == "records" else "entity", "predicate_path": []}
            )
            for identity in selected:
                child = self.project(singular, typ.item_type, node + "/" + identity, [identity])
                child.entity_id = identity
                result.items.append(child)
            if not selected:
                result.issues.append(issue("missing", "FINDER_COLLECTION_EMPTY", node))
        elif p.kind == "relation_presence":
            result = typed_value(node, typ, True) if selected else unavailable_value(node, typ)
        elif len(selected) != 1:
            result = unavailable_value(
                node,
                typ,
                "conflict" if selected else "missing",
                "SUBJECT_AMBIGUOUS" if selected else "INPUT_MISSING",
            )
        else:
            subject = selected[0]
            obj = self.nodes[subject]
            if p.kind == "record":
                result = ResolvedValue(
                    node_id=node,
                    resolved_type=typ,
                    entity_id=subject,
                    execution_scope_id=self.scope_id,
                )
                for key, field in p.fields.items():
                    child = self.project(
                        field.value, typ.fields[key].type, node + "/" + key, [subject]
                    )
                    apply_constraints(child, field.constraints)
                    result.fields[key] = child
            elif p.kind in {"property", "source_field"}:
                matches = [
                    prop
                    for prop in obj.get("object_data_properties", [])
                    if (
                        prop.get("iri") == p.property_iri
                        if p.kind == "property"
                        else prop.get("field_key") == p.source_field
                    )
                ]
                values = {evidence_hash(prop.get("value")): prop for prop in matches}
                if len(values) != 1:
                    result = unavailable_value(
                        node,
                        typ,
                        "conflict" if values else "missing",
                        "FACT_CONFLICT" if values else "INPUT_MISSING",
                    )
                else:
                    prop = next(iter(values.values()))
                    result = typed_value(
                        node,
                        typ,
                        literal_value(prop.get("value"), typ),
                        provenance=[self.provenance(subject, v) for v in matches],
                    )
            elif p.kind == "source_text":
                result = typed_value(
                    node, typ, obj.get("object_text") or None, provenance=[self.provenance(subject)]
                )
            else:
                actual = obj["object_class_iri"]
                expected = next(
                    (c for c in typ.class_iris if class_matches(self.schema, actual, c)), actual
                )
                result = typed_value(
                    node,
                    typ,
                    {"entity_id": subject, "class_iri": expected},
                    provenance=[self.provenance(subject)],
                )
                if p.display_property_iri:
                    display = self.project(
                        p.model_copy(
                            update={
                                "kind": "property",
                                "property_iri": p.display_property_iri,
                                "predicate_path": [],
                            }
                        ),
                        TypeSpec(kind="string"),
                        node + "/display",
                        [subject],
                    )
                    result.fields["display"] = display
            result.subject_refs = [subject]
            result.entity_id = subject if typ.kind in {"record", "entity"} else result.entity_id
        if subjects is None:
            result.issues.extend(deepcopy(self.issues))
        result.execution_scope_id = self.scope_id
        result.derivation.update(source_kind="finder_demo", verified_fact=False)
        return summarize(result)
