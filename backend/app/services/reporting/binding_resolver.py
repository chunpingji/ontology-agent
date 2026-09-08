"""Capability-based binding and exact projection over a frozen source bundle."""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass, field
from decimal import Decimal

from app.schemas.evidence import Candidate
from app.services.extraction.evidence_identity import evidence_hash
from app.services.fact_selector import FactSelector
from app.services.reporting.input_resolver import (
    Discovery,
    ResolvedValue,
    apply_constraints,
    business_value,
    check_identities,
    issue,
    select_nodes,
    sort_key,
    summarize,
    typed_value,
    unavailable_value,
)
from app.services.reporting.template_v2 import ReportingError, TypeSpec


@dataclass
class FactSelection:
    selector: FactSelector | None
    subjects: list[str] = field(default_factory=list)
    roots: list[str] = field(default_factory=list)
    support: dict = field(default_factory=dict)
    issues: list = field(default_factory=list)
    discovery: Discovery = field(default_factory=Discovery)
    source: dict = field(default_factory=dict)
    applicable_at: str | None = None
    conditions: dict = field(default_factory=dict)
    nature: str = "observed_fact"
    negatives: list = field(default_factory=list)
    scope_id: str = ""
    max_records: int = 1000
    budget: object = None
    _conflicts: set | None = None

    def consume(self, count=1):
        if self.budget:
            self.budget.consume(count)

    def positive(self, record):
        candidate = self.selector.candidates[record["assertion_id"]]
        if not self.selector._applicable(record, self.applicable_at):
            return False
        if candidate.positive_eligible and self.nature == "observed_fact":
            return True
        proof = self.conditions.get(record["assertion_id"], {})
        return (
            proof.get("projection_eligible")
            and proof.get("business_nature") == self.nature
            and proof.get("polarity") == "affirmed"
        )

    def negative(self, record):
        candidate = self.selector.candidates[record["assertion_id"]]
        if not self.selector._applicable(record, self.applicable_at):
            return False
        if (
            self.nature == "observed_fact"
            and candidate.assertion_status == "negated"
            and not (candidate.condition_anchors or candidate.condition_provenance_indexes)
        ):
            return True
        proof = self.conditions.get(record["assertion_id"], {})
        return (
            proof.get("projection_eligible")
            and proof.get("business_nature") == self.nature
            and proof.get("polarity") == "negated"
        )

    def conflict_ids(self):
        if self._conflicts is not None:
            return self._conflicts
        self.consume(len(self.selector.records))
        conflicts = self.selector.conflicts(self.applicable_at)
        positives, negatives = defaultdict(set), defaultdict(set)
        for record in self.selector.records:
            if self.positive(record):
                positives[self.selector._key(record)].add(record["assertion_id"])
            if self.negative(record):
                negatives[self.selector._key(record)].add(record["assertion_id"])
        for key in positives.keys() & negatives.keys():
            conflicts.update(positives[key] | negatives[key])
        self._conflicts = conflicts
        return conflicts

    def path(self, roots, steps, node, *, object_ids=None):
        if self.selector is None:
            return [], {}, list(self.issues), []
        frontier = {root: set(self.support.get(root, [])) for root in roots}
        problems, negatives = [], []
        conflicts = self.conflict_ids()
        for index, step in enumerate(steps):
            following = defaultdict(set)
            for current, refs in frontier.items():
                records = (
                    self.selector._by_subject
                    if step.direction == "forward"
                    else self.selector._by_object
                )[current]
                for record in records:
                    self.consume()
                    candidate = self.selector.candidates[record["assertion_id"]]
                    if (
                        candidate.kind != "relationship"
                        or candidate.predicate_iri != step.predicate_iri
                    ):
                        continue
                    if not self.selector._applicable(record, self.applicable_at):
                        continue
                    target = (
                        record.get("object_iri")
                        if step.direction == "forward"
                        else record["subject_iri"]
                    )
                    if (
                        index == len(steps) - 1
                        and object_ids is not None
                        and target not in object_ids
                    ):
                        continue
                    if record["assertion_id"] in conflicts:
                        problems.append(
                            issue(
                                "conflict",
                                "FACT_CONFLICT",
                                node,
                                refs=[record["assertion_id"]],
                                blocks=True,
                                constraint="identity",
                            )
                        )
                    elif self.positive(record):
                        if target in self.selector.entities:
                            if target not in following and len(following) >= self.max_records:
                                raise ReportingError("RESOLUTION_BUDGET_EXCEEDED")
                            following[target].update(refs | {record["assertion_id"]})
                        else:
                            problems.append(
                                issue(
                                    "incomplete",
                                    "OBJECT_IDENTITY_UNRESOLVED",
                                    node,
                                    refs=[record["assertion_id"]],
                                    constraint="identity",
                                    blocks=True,
                                )
                            )
                    elif self.negative(record) and index == len(steps) - 1:
                        negatives.append((target, record["assertion_id"]))
                    elif candidate.assertion_status == "conditional":
                        proof = self.conditions.get(record["assertion_id"], {})
                        if proof.get("result") != "FALSE":
                            problems.append(
                                issue(
                                    "incomplete",
                                    "CONDITION_BINDING_UNRESOLVED",
                                    node,
                                    refs=[record["assertion_id"]],
                                )
                            )
                problems.extend(self.pending(current, step.predicate_iri, node))
            frontier = following
            if len(frontier) > self.max_records:
                problems.append(
                    issue("incomplete", "OBJECT_UNIVERSE_OPEN", node, constraint="discovery")
                )
                frontier = dict(sorted(frontier.items())[: self.max_records])
        if object_ids is not None and not steps:
            frontier = {key: refs for key, refs in frontier.items() if key in object_ids}
        return sorted(frontier), {k: sorted(v) for k, v in frontier.items()}, problems, negatives

    def pending(self, subject, predicate, node):
        problems = []
        published = {
            (r["candidate"]["candidate_id"], r["candidate"]["revision"])
            for r in self.selector.records
        }
        names = {
            r["candidate"]["candidate_id"]
            for r in self.selector.records
            if r["subject_iri"] == subject and r["candidate"]["kind"] == "entity"
        }
        for raw in self.source.get("candidates", []):
            c = Candidate.model_validate(raw)
            if (c.candidate_id, c.revision) in published or c.review_status == "rejected":
                continue
            if c.applicable_at is not None and c.applicable_at != self.applicable_at:
                continue
            if (
                c.predicate_iri != predicate
                or c.subject is None
                or (c.subject.candidate_id not in names and c.subject.instance_iri != subject)
            ):
                continue
            state = "conflict" if c.validation_status == "conflict" else "pending_review"
            problems.append(issue(state, "FACT_NOT_PUBLISHED", node, refs=[c.candidate_id]))
        return problems

    def property(self, subject, predicate, typ, node):
        self.consume(len(self.selector._by_subject[subject]))
        records = [
            r
            for r in self.selector._by_subject[subject]
            if self.selector.candidates[r["assertion_id"]].kind == "property"
            and self.selector.candidates[r["assertion_id"]].predicate_iri == predicate
            and self.selector._applicable(r, self.applicable_at)
        ]
        conflicts = self.conflict_ids()
        positive = [r for r in records if self.positive(r)]
        unique = {self.selector._key(r) for r in positive}
        refs = [r["assertion_id"] for r in records]
        problems = self.pending(subject, predicate, node)
        if typ.datatype_iri and any(
            record["candidate"]["literal"].get("datatype_iri") != typ.datatype_iri
            for record in positive
        ):
            problems.append(issue("invalid", "INPUT_TYPE_MISMATCH", node, refs=refs))
        if len(unique) > 1 or any(r["assertion_id"] in conflicts for r in records):
            problems.append(issue("conflict", "CARDINALITY_CONFLICT", node, refs=refs))
        for record in records:
            candidate = self.selector.candidates[record["assertion_id"]]
            if candidate.assertion_status == "conditional" and not self.positive(record):
                proof = self.conditions.get(record["assertion_id"], {})
                if proof.get("result") != "FALSE":
                    problems.append(
                        issue(
                            "incomplete", "CONDITION_UNKNOWN", node, refs=[record["assertion_id"]]
                        )
                    )
        if not positive:
            result = unavailable_value(node, typ, execution_scope_id=self.scope_id)
        elif problems:
            result = ResolvedValue(
                node_id=node, resolved_type=typ, execution_scope_id=self.scope_id
            )
        else:
            literal = positive[0]["candidate"]["literal"]
            value = literal.get("normalized_value")
            if typ.kind == "integer" and isinstance(value, str):
                number = Decimal(value)
                value = int(number) if number == number.to_integral_value() else value
            elif typ.kind == "quantity":
                from app.services.extraction.literal_normalizer import unit_definition

                unit = literal.get("canonical_unit") or typ.unit
                dimension = literal.get("dimension") or typ.dimension
                try:
                    actual_dimension, scale = unit_definition(unit)
                    target_dimension, target_scale = unit_definition(typ.unit)
                    if actual_dimension == target_dimension and dimension == target_dimension:
                        ratio = scale / target_scale
                        number = (
                            Decimal(str(value))
                            * Decimal(ratio.numerator)
                            / Decimal(ratio.denominator)
                        )
                        if unit == "°C" and typ.unit == "K":
                            number += Decimal("273.15")
                        elif unit == "K" and typ.unit == "°C":
                            number -= Decimal("273.15")
                        value = str(number)
                        unit = typ.unit
                    else:
                        dimension = actual_dimension
                except (ValueError, ArithmeticError):
                    dimension = "invalid"
                value = {"value": value, "unit": unit, "dimension": dimension}
            elif typ.kind == "range":
                value = {
                    k: literal.get(k)
                    for k in ("lower", "upper", "lower_inclusive", "upper_inclusive")
                }
            result = typed_value(node, typ, value, subject=[subject], scope=self.scope_id)
        result.issues.extend(problems)
        result.subject_refs = [subject]
        result.fact_refs = sorted(set(refs))
        result.provenance_refs = [
            {"assertion_ref": r["assertion_id"], "sources": deepcopy(r["candidate"]["provenance"])}
            for r in records
        ]
        result.applicable_scope = {"subject_id": subject, "applicable_at": self.applicable_at}
        condition_proofs = [
            self.conditions[r["assertion_id"]]
            for r in positive
            if r["assertion_id"] in self.conditions
        ]
        if condition_proofs:
            result.derivation = {"condition_evaluations": condition_proofs}
        result.derivation["business_nature"] = self.nature
        if typ.type_contract_ref:
            result.derivation["type_contract_ref"] = typ.type_contract_ref
        return summarize(result)

    def project(self, projection, typ, node, subjects=None):
        self.consume()
        p, typ = projection, TypeSpec.model_validate(typ) if isinstance(typ, dict) else typ
        roots = self.subjects if subjects is None else subjects
        if self.selector is None:
            result = unavailable_value(node, typ, "unavailable", "SOURCE_UNAVAILABLE")
            result.issues.extend(self.issues)
            return summarize(result)
        selected, support, problems, negatives = self.path(roots, p.predicate_path, node)
        discovery = deepcopy(self.discovery)
        if p.predicate_path:
            key = evidence_hash(
                {
                    "root": roots[0] if len(roots) == 1 else roots,
                    "path": [step.model_dump() for step in p.predicate_path],
                    "applicable_at": self.applicable_at,
                }
            )
            proof = self.source.get("discovery", {}).get(key, {})
            established = set(selected) | {target for target, _ in negatives}
            discovery = Discovery(status="open")
            if (
                proof.get("status") == "complete"
                and proof.get("proof_ref")
                and proof.get("snapshot_id") == self.source["snapshot"]["snapshot_id"]
                and established == set(proof.get("object_ids", []))
            ):
                discovery = Discovery(
                    status="complete",
                    proof_ref=proof["proof_ref"],
                    expected_ids=proof["object_ids"],
                )
        elif subjects is not None:
            discovery = Discovery(
                status="complete", proof_ref=evidence_hash(roots), expected_ids=list(roots)
            )
        if p.class_iri:
            selected = [
                s
                for s in selected
                if self.selector.class_matches(
                    self.selector.entities[s]["candidate"]["class_iri"], p.class_iri
                )
            ]

        def finish(result):
            result.derivation["selection"] = {
                "roots": list(roots),
                "predicate_path": [step.model_dump() for step in p.predicate_path],
                "subjects": list(selected),
                "support": deepcopy(support),
                "negative_assertions": [ref for _, ref in negatives],
                "discovery": discovery.model_dump(mode="json"),
                "applicable_at": self.applicable_at,
            }
            return summarize(result)

        if p.kind in {"records", "entities"}:
            result = ResolvedValue(
                node_id=node,
                resolved_type=typ,
                discovery=discovery,
                execution_scope_id=self.scope_id,
            )
            singular = p.model_copy(
                update={"kind": "record" if p.kind == "records" else "entity", "predicate_path": []}
            )
            for subject in selected:
                child = self.project(singular, typ.item_type, node + "/" + subject, [subject])
                child.entity_id = subject
                child.fact_refs = sorted(set(child.fact_refs) | set(support.get(subject, [])))
                result.items.append(child)
            result.issues.extend(problems)
            if subjects is None:
                result.issues.extend(self.issues)
            if not selected and discovery.status != "complete":
                result.issues.append(issue("missing", "INPUT_MISSING", node))
            elif (
                not selected
                and (negatives or self.negatives)
                and discovery.expected_ids
                and (
                    set(discovery.expected_ids)
                    <= {target for target, _ in (negatives or self.negatives)}
                )
            ):
                result.state = "confirmed_absent"
                result.fact_refs = sorted({ref for _, ref in (negatives or self.negatives)})
            if result.discovery.status != "complete":
                result.issues.append(
                    issue("incomplete", "OBJECT_UNIVERSE_OPEN", node, constraint="discovery")
                )
            result.subject_refs = list(roots)
            check_identities(result)
            return finish(result)
        if p.kind == "relation_presence":
            if problems:
                result = unavailable_value(node, typ, "incomplete", "RELATION_UNRESOLVED")
            elif selected:
                result = typed_value(node, typ, True)
            elif (
                discovery.status == "complete"
                and discovery.expected_ids
                and set(discovery.expected_ids)
                <= {target for target, _ in (negatives or self.negatives)}
            ):
                result = typed_value(node, typ, False)
            else:
                result = unavailable_value(node, typ)
            result.fact_refs = sorted(
                {ref for refs in support.values() for ref in refs}
                | {ref for _, ref in (negatives or self.negatives)}
            )
            result.subject_refs = list(roots)
            result.applicable_scope = {"applicable_at": self.applicable_at}
            result.execution_scope_id = self.scope_id
            result.derivation["business_nature"] = self.nature
            result.issues.extend(problems)
            return finish(result)
        if len(selected) != 1:
            result = unavailable_value(
                node,
                typ,
                "conflict" if len(selected) > 1 else "missing",
                "SUBJECT_AMBIGUOUS" if len(selected) > 1 else "INPUT_MISSING",
            )
            result.issues.extend(problems)
            return finish(result)
        subject = selected[0]
        if p.kind == "property":
            result = self.property(subject, p.property_iri, typ, node)
        elif p.kind == "record":
            result = ResolvedValue(
                node_id=node,
                resolved_type=typ,
                entity_id=subject,
                subject_refs=[subject],
                execution_scope_id=self.scope_id,
            )
            for key, field in p.fields.items():
                child = self.project(field.value, typ.fields[key].type, node + "/" + key, [subject])
                apply_constraints(child, field.constraints)
                result.fields[key] = child
        else:
            record = self.selector.entities[subject]
            actual = record["candidate"]["class_iri"]
            expected = next(
                (c for c in typ.class_iris if self.selector.class_matches(actual, c)), None
            )
            value = {"entity_id": subject, "class_iri": expected or actual}
            result = typed_value(node, typ, value, subject=[subject], scope=self.scope_id)
            result.fact_refs = [record["assertion_id"]]
            result.provenance_refs = [
                {
                    "assertion_ref": record["assertion_id"],
                    "sources": record["candidate"]["provenance"],
                }
            ]
            if p.display_property_iri:
                label = self.property(
                    subject, p.display_property_iri, TypeSpec(kind="string"), node + "/display"
                )
                result.fields["display"] = label
        result.issues.extend(problems)
        if subjects is None:
            result.issues.extend(self.issues)
        result.fact_refs = sorted(set(result.fact_refs) | set(support.get(subject, [])))
        result.applicable_scope = {"subject_id": subject, "applicable_at": self.applicable_at}
        return finish(result)


class BindingResolver:
    def __init__(
        self, plan, bundle, inputs, bindings, conditions, scope_id, repeat_items=None, budget=None
    ):
        self.plan, self.bundle = plan, bundle
        self.inputs, self.bindings, self.conditions = inputs, bindings, conditions
        self.scope_id, self.repeat_items = scope_id, repeat_items or {}
        self.budget = budget
        self.calculation_checks = {}

    def resolve(self, binding):
        typ = TypeSpec.model_validate(self.plan["binding_types"][binding.binding_id])
        if binding.kind == "facts":
            for slot in {binding.scope.source_slot, *binding.scope.fact_source_refs}:
                source = self.bundle.get("sources", {}).get(slot, {})
                problem = next(
                    (
                        c
                        for c in source.get("model_compatibility", [])
                        if c["status"] in {"unknown", "incompatible"}
                    ),
                    None,
                )
                if problem:
                    return unavailable_value(binding.binding_id, typ, "incomplete", problem["code"])
            return self.facts(binding)
        if binding.kind in {"context", "workflow"}:
            record = self.bundle.get("records", {}).get(binding.scope.record_slot)
            if not record:
                return unavailable_value(
                    binding.binding_id, typ, "unavailable", "SOURCE_UNAVAILABLE"
                )
            if record.get("contract_id") != binding.contract_ref:
                return unavailable_value(
                    binding.binding_id, typ, "invalid", "SOURCE_CONTRACT_MISMATCH"
                )
            if binding.scope.subject_ref and binding.scope.subject_ref != record.get("subject_id"):
                return unavailable_value(
                    binding.binding_id, typ, "invalid", "SUBJECT_SCOPE_MISMATCH"
                )
            applicable_at = binding.scope.applicable_at or self.bundle.get("applicable_at")
            if record.get("applicable_at") is not None and record["applicable_at"] != applicable_at:
                return unavailable_value(
                    binding.binding_id, typ, "unavailable", "RECORD_SCOPE_MISMATCH"
                )
            if record.get("state") != "ready":
                return unavailable_value(
                    binding.binding_id,
                    typ,
                    record.get("state", "pending_review"),
                    "WORKFLOW_PENDING_REVIEW",
                )
            result = typed_value(
                binding.binding_id,
                typ,
                record["values"],
                refs=[record["record_id"]],
                provenance=[record["provenance"]],
                subject=[record["subject_id"]] if record.get("subject_id") else [],
                scope=self.scope_id,
            )
            _set_scope(
                result, {"applicable_at": applicable_at, "subject_id": record.get("subject_id")}
            )
            return result
        if binding.provider == "rule_result":
            from app.services.reporting.claim_catalog import evaluate_rule

            return evaluate_rule(binding, self.bundle, self.inputs, self.scope_id)
        if binding.provider == "calculation":
            from app.services.reporting.calculation_execution import calculation_value

            return calculation_value(binding, self.calculation_checks, typ, self.scope_id)
        return self.view(binding, typ)

    def facts(self, binding):
        scope = binding.scope
        source = self.bundle.get("sources", {}).get(scope.source_slot)
        selected = FactSelection(
            None,
            scope_id=self.scope_id,
            applicable_at=scope.applicable_at or self.bundle.get("applicable_at"),
            nature=binding.assertion_nature,
            max_records=self.plan["template"]["budget"]["max_records"],
            budget=self.budget,
        )
        if not source or not source.get("snapshot"):
            selected.issues.append(
                issue("unavailable", "SOURCE_UNAVAILABLE", binding.binding_id, blocks=True)
            )
            return selected
        sources = [scope.source_slot, *scope.fact_source_refs]
        records, candidates, snapshots = {}, [], []
        for key in dict.fromkeys(sources):
            joined = self.bundle.get("sources", {}).get(key)
            if not joined or not joined.get("snapshot"):
                selected.issues.append(
                    issue("unavailable", "SOURCE_UNAVAILABLE", binding.binding_id, blocks=True)
                )
                continue
            snapshots.append(joined["snapshot"]["snapshot_id"])
            for record in joined["snapshot"]["assertions"]:
                previous = records.get(record["assertion_id"])
                if previous and previous != record:
                    selected.issues.append(
                        issue("conflict", "SOURCE_VERSION_CHANGED", binding.binding_id, blocks=True)
                    )
                records[record["assertion_id"]] = deepcopy(record)
            candidates.extend(joined.get("candidates", []))
        selected.source = {**source, "candidates": candidates}
        if len(set(sources)) > 1:
            selected.source["discovery"] = {}
        selected.consume(len(records) + len(candidates))
        selected.selector = FactSelector(
            {"snapshot_id": evidence_hash(snapshots), "assertions": list(records.values())},
            schema=self.bundle["schema"],
        )
        for ref in scope.condition_refs:
            proof = self.conditions.get(ref)
            if proof:
                selected.conditions[proof["assertion_ref"]] = proof
        if scope.root.kind == "source_root":
            roots = [source["root_entity_id"]]
        elif scope.root.kind == "entity_ref":
            roots = [scope.root.entity_id]
        elif scope.root.kind == "repeat_item":
            item = self.repeat_items.get(scope.root.repeat_ref)
            roots = [item.entity_id] if item and item.entity_id else []
        else:
            upstream = self.bindings.get(scope.root.binding_ref)
            roots = upstream.subjects if isinstance(upstream, FactSelection) else []
        roots = [
            r
            for r in roots
            if r in selected.selector.entities
            and selected.selector.class_matches(
                selected.selector.entities[r]["candidate"]["class_iri"],
                binding.contract_ref.root_class_iri,
            )
        ]
        if not roots:
            selected.issues.append(
                issue(
                    "missing",
                    "SUBJECT_UNRESOLVED",
                    binding.binding_id,
                    constraint="identity",
                    blocks=True,
                )
            )
        selected.roots = roots
        subjects, support, problems, negatives = selected.path(
            roots,
            scope.predicate_path,
            binding.binding_id,
            object_ids=scope.object_ids,
        )
        selected.subjects = [
            s
            for s in subjects
            if selected.selector.class_matches(
                selected.selector.entities[s]["candidate"]["class_iri"],
                binding.contract_ref.result_class_iri,
            )
        ]
        selected.support, selected.negatives = support, negatives
        selected.issues.extend(problems)
        count = len(selected.subjects)
        if scope.cardinality.max_count is not None and count > scope.cardinality.max_count:
            selected.issues.append(
                issue(
                    "conflict",
                    "SUBJECT_AMBIGUOUS",
                    binding.binding_id,
                    constraint="cardinality",
                    blocks=True,
                )
            )
        if count < scope.cardinality.min_count:
            selected.issues.append(
                issue(
                    "missing",
                    "MINIMUM_CARDINALITY_UNMET",
                    binding.binding_id,
                    constraint="cardinality",
                )
            )
        key = evidence_hash(
            {
                "root": roots[0] if len(roots) == 1 else roots,
                "path": [s.model_dump() for s in scope.predicate_path],
                "applicable_at": selected.applicable_at,
            }
        )
        proof = source.get("discovery", {}).get(key, {})
        if scope.discovery_ref and scope.discovery_ref not in {key, proof.get("proof_ref")}:
            proof = {}
        if scope.object_ids is not None:
            selected.discovery = Discovery(
                status="complete",
                proof_ref=evidence_hash(scope.object_ids),
                expected_ids=scope.object_ids,
            )
        elif not scope.predicate_path:
            selected.discovery = Discovery(
                status="complete", proof_ref=evidence_hash(roots), expected_ids=roots
            )
        elif (
            len(set(sources)) == 1
            and proof.get("status") == "complete"
            and proof.get("proof_ref")
            and proof.get("snapshot_id") == source["snapshot"]["snapshot_id"]
        ):
            selected.discovery = Discovery(
                status="complete",
                proof_ref=proof["proof_ref"],
                expected_ids=proof.get("object_ids", []),
            )
            if not set(selected.subjects) <= set(selected.discovery.expected_ids):
                selected.discovery.status = "open"
        else:
            selected.discovery = Discovery(status="open")
        established = set(selected.subjects) | {target for target, _ in selected.negatives}
        if selected.discovery.status == "complete" and established != set(
            selected.discovery.expected_ids
        ):
            # Declaring an object universe does not prove every member's path.
            selected.issues.append(
                issue(
                    "incomplete",
                    "OBJECT_UNIVERSE_MEMBERS_UNPROVEN",
                    binding.binding_id,
                    constraint="discovery",
                    refs=sorted(set(selected.discovery.expected_ids) - set(selected.subjects)),
                )
            )
            selected.discovery.status = "open"
        if any(i.constraint == "discovery" for i in selected.issues):
            selected.discovery.status = "open"
        return selected

    def view(self, binding, typ):
        from app.services.reporting.condition_resolver import evaluate_expression

        op = binding.operation
        source = self.inputs.get(op.source.input_id)
        if source is None:
            return unavailable_value(binding.binding_id, typ, "missing", "VIEW_INPUT_MISSING")
        nodes, ancestors = select_nodes(source, op.source.field_path)
        if ancestors or len(nodes) != 1:
            return unavailable_value(
                binding.binding_id, typ, "conflict", "INPUT_CONSUMPTION_BLOCKED"
            )
        source = nodes[0]
        definition = self.bundle["contracts"][binding.contract_ref]["definition"]
        if op.kind not in definition.get("allowed_operations", []):
            return unavailable_value(
                binding.binding_id, typ, "invalid", "VIEW_OPERATION_NOT_REGISTERED"
            )
        if op.kind in {"range", "unit_convert"}:
            try:
                if op.kind == "range":
                    lower = business_value(source, [op.lower_field])
                    upper = business_value(source, [op.upper_field])
                    raw = {
                        "lower": lower,
                        "upper": upper,
                        "lower_inclusive": typ.lower_inclusive,
                        "upper_inclusive": typ.upper_inclusive,
                    }
                    result = typed_value(
                        binding.binding_id,
                        typ,
                        raw,
                        scope=self.scope_id,
                        subject=source.subject_refs,
                    )
                    result.fields = {
                        "lower": source.fields[op.lower_field].model_copy(deep=True),
                        "upper": source.fields[op.upper_field].model_copy(deep=True),
                    }
                else:
                    conversion = self.bundle["contracts"].get(op.conversion_ref, {})
                    contract = conversion.get("definition", {})
                    raw = business_value(source)
                    if (
                        conversion.get("status") != "published"
                        or conversion.get("kind") != "conversion"
                        or contract.get("from_unit") != raw["unit"]
                        or contract.get("to_unit") != typ.unit
                        or contract.get("dimension") != raw["dimension"]
                    ):
                        raise ReportingError("UNIT_INCOMPATIBLE")
                    number = Decimal(raw["value"]) * Decimal(contract["factor"])
                    number += Decimal(contract.get("offset", "0"))
                    result = typed_value(
                        binding.binding_id,
                        typ,
                        {
                            "value": format(number, "f"),
                            "unit": typ.unit,
                            "dimension": typ.dimension,
                        },
                    )
                result.fact_refs = sorted({r for n in _values(source) for r in n.fact_refs})
                result.provenance_refs = [
                    deepcopy(p) for n in _values(source) for p in n.provenance_refs
                ]
                result.subject_refs = sorted({s for n in _values(source) for s in n.subject_refs})
                result.execution_scope_id = self.scope_id
                _set_scope(result, source.applicable_scope)
                result.derivation = {
                    "upstream_derivation": deepcopy(source.derivation),
                    "operation": op.model_dump(mode="json"),
                    "upstream_hash": evidence_hash(source),
                }
                return result
            except (ReportingError, KeyError, ValueError):
                return unavailable_value(
                    binding.binding_id, typ, "incomplete", "VIEW_INPUT_UNUSABLE"
                )
        if source.resolved_type.kind != "list" or typ.kind != "list":
            return unavailable_value(binding.binding_id, typ, "invalid", "INPUT_TYPE_MISMATCH")
        result = ResolvedValue(
            node_id=binding.binding_id,
            resolved_type=typ,
            discovery=deepcopy(source.discovery),
            issues=deepcopy(source.issues),
            execution_scope_id=self.scope_id,
        )
        if any(i.blocks_subtree for i in result.issues):
            return summarize(result)
        rows = list(source.items)

        def key_for(row, keys):
            return tuple(evidence_hash(business_value(row, [key])) for key in keys)

        try:
            right_index = {}
            if op.kind == "join":
                right = self.inputs[op.right.input_id]
                selected, blocked = select_nodes(right, op.right.field_path)
                if blocked or len(selected) != 1 or selected[0].resolved_type.kind != "list":
                    raise ReportingError("JOIN_SCOPE_INVALID")
                right = selected[0]
                if not right.discovery or right.discovery.status != "complete":
                    raise ReportingError("OBJECT_UNIVERSE_OPEN")
                for row in right.items:
                    key = key_for(row, op.right_keys)
                    if key in right_index:
                        raise ReportingError("JOIN_CARDINALITY_CONFLICT")
                    right_index[key] = row
                if op.cardinality == "one_to_one":
                    keys = [key_for(row, op.keys) for row in rows]
                    if len(set(keys)) != len(keys):
                        raise ReportingError("JOIN_CARDINALITY_CONFLICT")
            if op.kind == "sort":
                rows.sort(key=lambda row: row.entity_id or "")
                for order in reversed(op.order_by):
                    rows.sort(
                        key=lambda row: (
                            sort_key(row.fields[order.field_ref])
                            if order.field_ref
                            else row.entity_id or ""
                        ),
                        reverse=order.direction == "desc",
                    )
            if op.kind == "group":
                groups = defaultdict(list)
                for row in rows:
                    groups[key_for(row, op.keys)].append(row)
                for key, members in sorted(groups.items()):
                    record = ResolvedValue(
                        node_id=binding.binding_id + "/" + evidence_hash(key),
                        entity_id=evidence_hash(key),
                        resolved_type=typ.item_type,
                    )
                    for name in op.keys:
                        record.fields[name] = members[0].fields[name].model_copy(deep=True)
                    member_type = typ.item_type.fields["items"].type
                    record.fields["items"] = ResolvedValue(
                        node_id=record.node_id + "/items",
                        resolved_type=member_type,
                        items=deepcopy(members),
                        discovery=deepcopy(source.discovery),
                    )
                    summarize(record.fields["items"])
                    result.items.append(summarize(record))
            else:
                for row in rows:
                    if op.kind == "filter":
                        proof = evaluate_expression(
                            op.predicate, self.inputs, items={op.source.input_id: row}
                        )
                        if proof["result"] == "FALSE":
                            continue
                        if proof["result"] == "UNKNOWN":
                            result.discovery = Discovery(status="open")
                            result.issues.append(
                                issue(
                                    "incomplete",
                                    "VIEW_FILTER_UNKNOWN",
                                    binding.binding_id,
                                    constraint="discovery",
                                )
                            )
                    if op.kind in {"project", "join"}:
                        joined = (
                            right_index.get(key_for(row, op.keys)) if op.kind == "join" else None
                        )
                        item = ResolvedValue(
                            node_id=binding.binding_id + "/" + row.entity_id,
                            entity_id=row.entity_id,
                            resolved_type=typ.item_type,
                        )
                        for name, path in op.fields.items():
                            root = row
                            if op.kind == "join":
                                root, path = (row if path[0] == "left" else joined), path[1:]
                            if root is None:
                                item.fields[name] = unavailable_value(
                                    item.node_id + "/" + name, typ.item_type.fields[name].type
                                )
                            else:
                                selected, blocked = select_nodes(root, path)
                                item.fields[name] = (
                                    selected[0].model_copy(deep=True)
                                    if len(selected) == 1 and not blocked
                                    else unavailable_value(
                                        item.node_id + "/" + name,
                                        typ.item_type.fields[name].type,
                                        "conflict",
                                        "INPUT_CONSUMPTION_BLOCKED",
                                    )
                                )
                        if joined is None and op.kind == "join" and op.unmatched == "block":
                            item.issues.append(
                                issue("missing", "JOIN_RECORD_MISSING", item.node_id, blocks=True)
                            )
                        item.derivation = {
                            "operation": op.model_dump(mode="json"),
                            "left": evidence_hash(row),
                            "right": evidence_hash(joined) if joined else None,
                        }
                        result.items.append(summarize(item))
                    else:
                        result.items.append(row.model_copy(deep=True))
        except (ReportingError, KeyError, TypeError) as exc:
            result.issues.append(
                issue(
                    "conflict" if "CARDINALITY" in str(exc) else "incomplete",
                    getattr(exc, "code", "VIEW_INPUT_UNUSABLE"),
                    binding.binding_id,
                    blocks=True,
                )
            )
        result.derivation = {
            "operation": op.model_dump(mode="json"),
            "upstream_hash": evidence_hash(source),
        }
        result.subject_refs = deepcopy(source.subject_refs)
        result.applicable_scope = deepcopy(source.applicable_scope)
        for node in _values(result):
            node.execution_scope_id = self.scope_id
            if not node.applicable_scope:
                node.applicable_scope = deepcopy(source.applicable_scope)
        check_identities(result)
        return summarize(result)


def _values(value):
    yield value
    for child in [*value.fields.values(), *value.items]:
        yield from _values(child)


def _set_scope(value, scope):
    value.applicable_scope = deepcopy(scope)
    for child in [*value.fields.values(), *value.items]:
        _set_scope(child, scope)
