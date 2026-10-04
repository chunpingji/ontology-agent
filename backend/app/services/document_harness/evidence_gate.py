"""Exact evidence prechecks and narrowly configured, source-bound relation proofs.

These checks never treat ontology compatibility, matching identifiers, or nearby
text as a semantic proof. No model, repository, or network access belongs here.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .observations import value_components
from .ontology import SchemaCatalog, legal_property, legal_relation
from .source import identity, missing, reference, references_cover


def _hash(value):
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()


class FrozenHeaderCell(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    row: int = Field(ge=0)
    column: int = Field(ge=0)
    text: str = Field(min_length=1)


class FrozenExactTableRule(BaseModel):
    """A business-approved mapping; all configuration is frozen with the run."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    rule_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    document_root_class_iri: str = Field(min_length=1)
    table_path: list[str] = Field(min_length=1)
    header_rows: int = Field(ge=1)
    header_cells: list[FrozenHeaderCell] = Field(min_length=2)
    subject_column: int = Field(ge=0)
    object_column: int = Field(ge=0)
    subject_class_iri: str = Field(min_length=1)
    predicate_iri: str = Field(min_length=1)
    object_class_iri: str = Field(min_length=1)
    static_scope_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    polarity: Literal["positive"] = "positive"
    conditions: list[str] = Field(default_factory=list, max_length=0)

    @model_validator(mode="after")
    def exact_mapping(self):
        if self.subject_column == self.object_column:
            raise ValueError("table_rule_columns_must_differ")
        if any(not part.strip() for part in self.table_path):
            raise ValueError("table_rule_empty_path_component")
        cells = {(cell.row, cell.column) for cell in self.header_cells}
        if len(cells) != len(self.header_cells):
            raise ValueError("table_rule_duplicate_header")
        if any(cell.row >= self.header_rows for cell in self.header_cells):
            raise ValueError("table_rule_header_outside_header_rows")
        columns = {cell.column for cell in self.header_cells}
        if not {self.subject_column, self.object_column} <= columns:
            raise ValueError("table_rule_missing_endpoint_header")
        return self


def normalize_table_rules(value: str | list) -> list[dict]:
    """Fail closed on invalid configuration instead of disabling a broken rule."""
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, list):
        raise ValueError("table_rules_must_be_a_list")
    rules = [FrozenExactTableRule.model_validate(item).model_dump(mode="json") for item in value]
    if len({(item["rule_id"], item["version"]) for item in rules}) != len(rules):
        raise ValueError("duplicate_table_rule_version")
    return rules


def _policy(state):
    policy = state.get("execution_policy", state.get("policy", {}))
    return policy.get("main", policy)


def _endpoint_ids(assertion):
    return [assertion.get("subject_id"), *(
        assertion.get("object_ids", []) if "object_ids" in assertion
        else [assertion["object_id"]] if assertion.get("object_id") else []
    )]


def _refs(row):
    result = []
    for key in ("evidence", "value_evidence", "type_evidence",
                "source_unit_evidence", "order_evidence"):
        result.extend(row.get(key, []))
    for key in ("name", "referent", "quote"):
        if isinstance(row.get(key), dict):
            result.append(row[key])
    return result


def _ref_keys(refs):
    return sorted({(ref["source_id"], ref["start"], ref["end"]) for ref in refs})


def _valid_reference(ir, ref):
    if not isinstance(ref, dict):
        return False
    start, end = ref.get("start"), ref.get("end")
    if type(start) is not int or type(end) is not int or not isinstance(ref.get("text"), str):
        return False
    try:
        return ir.resolve(ir.anchor(ref["source_id"], start, end)) == ref["text"]
    except (KeyError, ValueError, TypeError):
        return False


def _related_hints(state, assertion):
    objects = set(_endpoint_ids(assertion)[1:])
    sources = {ref.get("source_id") for ref in assertion.get("evidence", [])}
    result = []
    for domain in ("hints", "reference_cues"):
        for row in state.get(domain, {}).values():
            hint_objects = {row.get("object_id"), *row.get("object_ids", [])} - {None}
            same_relation = (row.get("subject_id") == assertion.get("subject_id")
                             and bool(objects & hint_objects))
            if domain == "reference_cues":
                targets = set(row.get("target_ids", []))
                if row.get("direction") == "outgoing":
                    same_relation |= (row.get("subject_id") == assertion.get("subject_id")
                                      and bool(objects & targets))
                elif row.get("direction") == "incoming":
                    same_relation |= (assertion.get("subject_id") in targets
                                      and row.get("subject_id") in objects)
            if (same_relation or sources & {ref.get("source_id")
                                            for ref in row.get("evidence", [])}):
                result.append({key: value for key, value in row.items()
                               if key not in {
                                   "id", "window_id", "state", "reason", "target_ids",
                                   "reference_targets_truncated",
                               }})
    return sorted(result, key=lambda row: _hash(row))


def assertion_dependency_hash(ir, catalog, state, assertion) -> str:
    """Fingerprint semantic inputs, excluding endpoint acceptance gate changes.

The caller stores this value when completing a semantic judgment. A later
candidate/accepted/unresolved transition alone may release or revoke that same
judgment; changing a class, anchor, identity interpretation or source cannot.
"""
    catalog = SchemaCatalog.model_validate(catalog)
    endpoint_keys = (
        "id", "class_iri", "label", "role", "name", "referent", "evidence",
        "type_evidence", "identity_binding",
    )
    claim_keys = (
        "subject_id", "object_id", "object_ids", "predicate_iri", "alignment_class_iri",
        "polarity", "conditions", "participation", "selection", "timing", "field_id",
        "value", "source_value", "source_unit", "source_unit_evidence",
        "ordered_object_ids", "order_evidence", "value_component", "value_evidence", "evidence",
    )
    endpoints = [state.get("entities", {}).get(key, {}) for key in _endpoint_ids(assertion)]
    field = state.get("fields", {}).get(assertion.get("field_id"), {})
    return _hash({
        "version": "assertion-dependency/1", "ir": ir.structure_hash,
        "ontology": catalog.ontology_hash, "root": catalog.root_class_iri,
        "assertion": {key: assertion[key] for key in claim_keys if key in assertion},
        "endpoints": [{key: row[key] for key in endpoint_keys if key in row} for row in endpoints],
        "field": {key: field[key] for key in (
            "id", "label", "value", "missing", "evidence", "value_evidence",
        ) if key in field},
        "hints": _related_hints(state, assertion), "policy": _policy(state),
    })


def _gate(action, reason, refs=(), *, proof=None, reused=None):
    return {
        "action": action, "reason_code": reason, "evidence_refs": _ref_keys(refs),
        "proof": proof, "reused_verification": deepcopy(reused),
    }


def _group_valid(assertion):
    if "object_ids" not in assertion:
        return True
    members = assertion["object_ids"]
    participation = assertion.get("participation", "unknown")
    selection = assertion.get("selection", "unspecified")
    timing = assertion.get("timing", "unspecified")
    return (
        len(members) >= 2 and len(set(members)) == len(members)
        and participation in {"options", "all", "unknown"}
        and selection in {"exactly_one", "unspecified"}
        and timing in {"parallel", "sequential", "unspecified"}
        and (selection != "exactly_one" or participation == "options")
        and (timing == "unspecified" or participation == "all")
        and (timing == "sequential" or (
            assertion.get("ordered_object_ids") is None and not assertion.get("order_evidence")))
    )


def _complete_referent_group(state, assertion):
    objects = set(_endpoint_ids(assertion)[1:])
    for key in objects:
        binding = state["entities"][key].get("identity_binding") or {}
        group = binding.get("group_id")
        if group:
            members = {row["id"] for row in state["entities"].values()
                       if (row.get("identity_binding") or {}).get("group_id") == group}
            if len(members) > 1 and ("object_ids" not in assertion or not members <= objects):
                return False
    return True


def _ontology_gate(catalog, endpoints, assertion):
    classes = [row.get("class_iri") for row in endpoints]
    if any(not iri for iri in classes):
        return "waiting", "missing_type"
    if any(iri not in catalog.reachable_class_iris for iri in classes):
        return ("invalid" if all(row.get("state") == "accepted" for row in endpoints)
                else "waiting"), "ontology_incompatible"
    prop = "field_id" in assertion
    menu = (catalog.classes[classes[0]].properties if prop
            else catalog.classes[classes[0]].relations)
    card = next((item for item in menu if item.iri == assertion.get("predicate_iri")), None)
    if card and card.constraint_status != "resolved":
        return "waiting", "ontology_unresolved"
    legal = (legal_property(catalog, classes[0], assertion.get("predicate_iri")) if prop
             else len(classes) >= 2 and all(
                 legal_relation(catalog, classes[0], assertion.get("predicate_iri"), iri)
                 for iri in classes[1:]
             ))
    if not legal:
        return ("invalid" if all(row.get("state") == "accepted" for row in endpoints)
                else "waiting"), "ontology_incompatible"
    return None


def _property_error(subject, field, assertion):
    if not field or field.get("missing") or assertion.get("field_id") not in subject.get(
        "field_ids", [],
    ):
        return "value_outside_field"
    if assertion.get("source_value", field["value"]) != field["value"]:
        return "bound_value_changed"
    selected = assertion.get("value_evidence", [])
    if not selected or any(not references_cover(ref, field.get("value_evidence", []))
                           for ref in selected):
        return "value_outside_field"
    component = assertion.get("value_component", "whole")
    if component == "span":
        if len(selected) != 1 or assertion.get("value") != selected[0]["text"]:
            return "value_outside_field"
        if missing(assertion["value"]):
            return "value_outside_field"
    else:
        # Component existence is a syntactic check; confirmation remains a gate.
        expected = value_components(field).get(component)
        if (expected is None or assertion.get("value") != expected["value"]
):
            return "bound_value_changed"
    return None


def check_candidate_structure(ir, catalog, state, assertion):
    """Check source locations and closed identifiers without deciding semantics."""
    endpoints = [state.get("entities", {}).get(key) for key in _endpoint_ids(assertion)]
    if not endpoints or any(row is None for row in endpoints):
        return _gate("invalid", "missing_endpoint")
    field = state.get("fields", {}).get(assertion.get("field_id"), {})
    refs = _refs(assertion) + _refs(field)
    for entity in endpoints:
        if entity.get("role") != "document_root" and not entity.get("referent"):
            return _gate("invalid", "missing_endpoint")
        refs.extend(_refs(entity))
        for binding in (entity.get("identity_binding") or {}).get("identifiers", []):
            refs.extend(_refs(binding))
    if any(not _valid_reference(ir, ref) for ref in refs):
        return _gate("invalid", "invalid_reference")
    if not _group_valid(assertion):
        return _gate("invalid", "invalid_group_contract", refs)
    if assertion.get("timing") == "sequential":
        ordered = assertion.get("ordered_object_ids") or []
        if (len(ordered) != len(set(ordered)) or set(ordered) != set(assertion["object_ids"])
                or not assertion.get("order_evidence")
                or any(not _valid_reference(ir, ref) for ref in assertion["order_evidence"])):
            return _gate("invalid", "invalid_group_order", refs)
    iri = assertion.get("predicate_iri")
    menus = [prop.iri for card in catalog.classes.values()
             for prop in (card.properties if "field_id" in assertion else card.relations)]
    if iri and iri not in menus:
        return _gate("invalid", "predicate_outside_catalog", refs)
    if "field_id" in assertion:
        error = _property_error(endpoints[0], field, assertion)
        if error:
            return _gate("invalid", error, refs)
    return None


def route_semantic_review(ir, catalog, state, assertion, policy=None) -> dict:
    """Route a proposal without ever converting lack of proof to a negative fact."""
    catalog = SchemaCatalog.model_validate(catalog)
    if not assertion.get("predicate_iri"):
        return _gate("waiting", "predicate_unmapped")
    failure = check_candidate_structure(ir, catalog, state, assertion)
    if failure:
        return failure
    if not _complete_referent_group(state, assertion):
        return _gate("invalid", "invalid_group_contract", assertion.get("evidence", []))
    endpoints = [state["entities"][key] for key in _endpoint_ids(assertion)]
    decision = _ontology_gate(catalog, endpoints, assertion)
    if decision:
        return _gate(*decision, assertion.get("evidence", []))
    if "field_id" in assertion:
        bound = [item for item in (endpoints[0].get("identity_binding") or {}).get(
            "identifiers", []) if item.get("field_id") == assertion["field_id"]]
        if bound and not any(item.get("property_iri") == assertion["predicate_iri"]
                             and item.get("value") == assertion.get("value") for item in bound):
            return _gate("invalid", "bound_value_changed", assertion.get("evidence", []))
    refs = assertion.get("evidence", [])
    verification = assertion.get("verification") or {}
    if (verification.get("method") in {"llm", "rule"}
            and verification.get("semantic_verdict") in {"accepted", "rejected", "unresolved"}
            and verification.get("dependency_hash") == assertion_dependency_hash(
                ir, catalog, state, assertion,
            )):
        return _gate("reuse", "exact_dependency_reuse", refs, reused=verification)
    proof = prove_table_relation(
        ir, catalog, state, assertion, (policy or _policy(state)).get("table_relation_rules", []),
    )
    if proof:
        return _gate("proven", "exact_table_relation", refs, proof=proof)
    if (not refs or ("object_ids" in assertion
                     and assertion.get("participation", "unknown") == "unknown")
            or assertion.get("missing_context")):
        return _gate("waiting", "insufficient_context", refs)
    return _gate("semantic", "semantic_proof_required", refs)


def semantic_acceptance(state, row, verdict):
    if verdict != "accepted":
        return verdict
    if not row.get("predicate_iri"):
        return "unresolved"
    if any(state.get("entities", {}).get(key, {}).get("state") != "accepted"
           for key in _endpoint_ids(row)):
        return "unresolved"
    if "object_ids" in row and row.get("participation") == "unknown":
        return "unresolved"
    return "accepted"


def table_scope_hash(ir, rule) -> str:
    """Hash all static source, masking only the two mapped data cell values."""
    if isinstance(rule, FrozenExactTableRule):
        rule = rule.model_dump(mode="json")
    rows = []
    for ordinal, unit in enumerate(ir.evidence_units):
        text = unit.text
        if (unit.kind == "cell_paragraph" and unit.table_path == rule["table_path"]
                and unit.row_index is not None and unit.row_index >= rule["header_rows"]):
            if unit.column_index == rule["subject_column"]:
                text = "<subject>"
            elif unit.column_index == rule["object_column"]:
                text = "<object>"
        rows.append([ordinal, unit.kind, unit.table_path, unit.row_index, unit.column_index, text])
    return _hash(rows)


def _table_cells(ir, rule):
    """Require a rectangular, uniquely mapped physical grid with one paragraph/cell."""
    tables = [table for table in ir.tables if table.get("table_path") == rule["table_path"]]
    if len(tables) != 1 or len(rule["table_path"]) != 1:
        return None
    table = tables[0]
    grid = table.get("grid", [])
    if (len(grid) <= rule["header_rows"] or not grid[0]
            or any(len(row) != len(grid[0]) for row in grid)):
        return None
    if max(rule["subject_column"], rule["object_column"]) >= len(grid[0]):
        return None
    ids = [cell_id for row in grid for cell_id in row]
    if None in ids or len(ids) != len(set(ids)):
        return None
    cells = table.get("source_cells", [])
    if len(cells) != len(ids) or {cell["cell_id"] for cell in cells} != set(ids):
        return None
    units = [unit for unit in ir.evidence_units if unit.table_path == rule["table_path"]]
    by_cell = {}
    for cell in cells:
        row, col = cell["row_index"], cell["column_index"]
        paragraphs = cell.get("blocks", [])
        matches = [unit for unit in units if unit.source_cell_id == cell["cell_id"]]
        if (cell.get("row_span") != 1 or cell.get("column_span") != 1
                or len(paragraphs) != 1 or paragraphs[0].get("kind") != "paragraph"
                or len(matches) != 1 or not 0 <= row < len(grid)
                or not 0 <= col < len(grid[0]) or grid[row][col] != cell["cell_id"]
                or (matches[0].row_index, matches[0].column_index) != (row, col)
                or matches[0].text != paragraphs[0]["text"]):
            return None
        by_cell[row, col] = matches[0]
    # All headers are named: an unmodelled condition column cannot sneak through.
    expected = {(row, col) for row in range(rule["header_rows"]) for col in range(len(grid[0]))}
    if {(cell["row"], cell["column"]) for cell in rule["header_cells"]} != expected:
        return None
    if any(by_cell[cell["row"], cell["column"]].text != cell["text"]
           for cell in rule["header_cells"]):
        return None
    return by_cell


def _whole_cell_entity(entity, unit):
    ref = entity.get("referent")
    if not ref or not unit.text.strip() or entity.get("referent_unresolved"):
        return False
    start = len(unit.text) - len(unit.text.lstrip())
    end = len(unit.text.rstrip())
    return (ref.get("source_id") == unit.evidence_id
            and (ref.get("start"), ref.get("end")) == (start, end)
            and ref.get("text") == unit.text.strip()
            and entity.get("label") == unit.text.strip())


def _conflicting_cue(ir, state, assertion, row_index, rule):
    for hint in _related_hints(state, assertion):
        if (hint.get("polarity", "positive") != "positive" or hint.get("conditions")
                or hint.get("competing_ownership") or hint.get("object_ids")):
            return True
    for hint in state.get("hints", {}).values():
        for ref in hint.get("evidence", []):
            try:
                unit = ir.unit(ref["source_id"])
            except (KeyError, ValueError):
                continue
            if (unit.table_path == rule["table_path"] and unit.row_index == row_index
                    and (hint.get("polarity", "positive") != "positive"
                         or hint.get("conditions") or hint.get("competing_ownership"))):
                return True
    return False


def prove_table_relation(ir, catalog, state, assertion, frozen_rules) -> dict | None:
    """Prove only a positive single relation covered by a complete frozen mapping."""
    catalog = SchemaCatalog.model_validate(catalog)
    if (not frozen_rules or "field_id" in assertion or "object_ids" in assertion
            or assertion.get("polarity") != "positive" or assertion.get("conditions") != []):
        return None
    if check_candidate_structure(ir, catalog, state, assertion):
        return None
    if _ontology_gate(catalog, [state["entities"][key] for key in _endpoint_ids(assertion)],
                      assertion):
        return None
    subject = state["entities"][assertion["subject_id"]]
    obj = state["entities"][assertion["object_id"]]
    if any(entity.get("state") != "accepted" for entity in (subject, obj)):
        return None
    for rule in normalize_table_rules(frozen_rules):
        if (rule["document_root_class_iri"] != catalog.root_class_iri
                or (rule["subject_class_iri"], rule["predicate_iri"], rule["object_class_iri"])
                != (subject.get("class_iri"), assertion["predicate_iri"], obj.get("class_iri"))
                or table_scope_hash(ir, rule) != rule["static_scope_hash"]):
            continue
        cells = _table_cells(ir, rule)
        if cells is None:
            continue
        subject_unit = ir.unit(subject["referent"]["source_id"])
        row = subject_unit.row_index
        if row is None or row < rule["header_rows"]:
            continue
        mapped_subject = cells.get((row, rule["subject_column"]))
        mapped_object = cells.get((row, rule["object_column"]))
        if mapped_subject is None or mapped_object is None:
            continue
        if not all(_whole_cell_entity(entity, unit) for entity, unit in (
            (subject, mapped_subject), (obj, mapped_object),
        )):
            continue
        # Any competing interpretation in either value cell defeats deterministic proof.
        if any(sum(1 for entity in state["entities"].values()
                   if (entity.get("referent") or {}).get("source_id") == unit.evidence_id) != 1
               for unit in (mapped_subject, mapped_object)):
            continue
        if _conflicting_cue(ir, state, assertion, row, rule):
            continue
        # Static scope is part of the proof, including footnotes and conditions outside the table.
        evidence = [reference(ir, unit.evidence_id, 0, len(unit.text))
                    for unit in ir.evidence_units if unit.text]
        return {
            "rule_id": rule["rule_id"], "rule_version": rule["version"],
            "rule_hash": _hash(rule),
            "dependency_hash": assertion_dependency_hash(ir, catalog, state, assertion),
            "evidence_refs": _ref_keys(evidence),
        }
    return None


def try_rule_seed(ir, catalog, state, seed, frozen_rules) -> tuple[dict, dict] | None:
    """Try before model alignment; default configuration deliberately proves nothing."""
    object_ids = seed.get("object_ids", [seed["object_id"]] if seed.get("object_id") else [])
    if not frozen_rules or len(object_ids) != 1:
        return None
    if seed.get("polarity", seed.get("polarity_hint", "positive")) != "positive":
        return None
    if seed.get("conditions", seed.get("condition_hints", [])):
        return None
    refs = seed.get("evidence", seed.get("evidence_refs", seed.get("clue_refs", [])))
    refs = [*refs, *seed.get("required_context_refs", [])]
    try:
        refs = [reference(ir, *ref) if isinstance(ref, (tuple, list)) else ref for ref in refs]
    except (TypeError, ValueError):
        return None
    catalog = SchemaCatalog.model_validate(catalog)
    subject = state.get("entities", {}).get(seed.get("subject_id"), {})
    predicate = seed.get("predicate_iri")
    assertion = {
        "id": identity(
            "relation", seed.get("subject_id"), predicate, object_ids[0], "positive", [],
        ),
        "subject_id": seed.get("subject_id"), "object_id": object_ids[0],
        "alignment_class_iri": subject.get("class_iri"), "predicate_iri": predicate,
        "label": predicate, "state": "candidate", "reason": "冻结表格契约精确匹配",
        "evidence": refs, "polarity": "positive", "conditions": [],
        "window_id": seed.get("window_id"),
    }
    proof = prove_table_relation(ir, catalog, state, assertion, frozen_rules)
    if proof is None:
        return None
    assertion["evidence"] = [reference(ir, *ref) for ref in proof["evidence_refs"]]
    proof["dependency_hash"] = assertion_dependency_hash(ir, catalog, state, assertion)
    return assertion, proof
