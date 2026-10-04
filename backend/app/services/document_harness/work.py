"""Current, bounded work. Identity and dependency checks never perform I/O."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from copy import deepcopy
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .protocols import PROTOCOL
from .source import identity

DEFAULT_POLICY = {
    "schema": "harness-pruning/1",
    "flow": "four_stage",
    "reading_concurrency": 2,
    "weak_candidates_per_subject_predicate_region": 4,
    "weak_candidates_per_region": 64,
    "reference_targets_per_cue": 8,
    "relation_items_per_call": 6,
    "review_judgments_per_call": 12,
    "context_expansions_per_dependency": 1,
    "reading_split_depth": 4,
    "wire_bytes_per_call": 96000,
    "table_relation_rules": [],
}
WorkKind = Literal[
    "property_alignment",
    "relation_alignment",
    "evidence_review",
    "group_interpretation",
    "coreference_review",
    "entity_review",
    "referent_alignment",
    "entity_calibration",
    "assertion_calibration",
]
WorkStatus = Literal["ready", "waiting", "pruned", "done", "failed"]


class WorkDependencies(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    entity_ids: list[str]
    field_ids: list[str]
    source_refs: list[dict]


class WorkItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    kind: WorkKind
    phase: Literal["skeleton", "semantic", "deterministic"]
    retryable: bool = True
    status: WorkStatus
    input: dict
    dependencies: dict
    dependency_hash: str
    applied_dependency_hash: str | None = None
    reason_code: str | None = None
    output_ids: list[str] = Field(default_factory=list)
    expansion_basis_hash: str
    expansions_used: int = Field(default=0, ge=0, le=1)
    last_call_key: str | None = None
    processed_predicate_iris: list[str] = Field(default_factory=list)
    selection_hash: str | None = None

    @model_validator(mode="after")
    def closed_input(self):
        allowed_phases = (
            {"skeleton", "semantic"} if self.kind in {"property_alignment", "relation_alignment"}
            else {"deterministic"} if self.kind.endswith("calibration") else {"semantic"}
        )
        if self.phase not in allowed_phases:
            raise ValueError("harness_work_phase_invalid")
        fields = {
            "property_alignment": {"subject_id", "field_id"},
            "relation_alignment": {
                "subject_id", "object_ids", "predicate_iri", "region_id", "clue_refs",
                "required_context_refs", "origin_ids", "priority", "polarity_hint",
                "condition_hints", "endpoint_hypothesis",
            },
            "evidence_review": {"domain", "assertion_id"},
            "group_interpretation": {"group_id"},
            "coreference_review": {"left_mention_id", "right_mention_id", "clue_refs"},
            "entity_review": {"entity_id"},
            "referent_alignment": {"group_id", "mention_ids"},
            "entity_calibration": {"entity_id"},
            "assertion_calibration": {"domain", "assertion_id"},
        }[self.kind]
        if (not fields <= self.input.keys()
                or self.input.keys() - fields - {"required_context_refs"}):
            raise ValueError("harness_work_input_shape_invalid")
        if (self.kind in {"evidence_review", "assertion_calibration"}
                and self.input["domain"] not in {"properties", "relations", "relation_groups"}):
            raise ValueError("harness_work_assertion_domain_invalid")
        WorkDependencies.model_validate(self.dependencies)
        return self


def digest(value):
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def ref_key(ref):
    if isinstance(ref, (list, tuple)):
        return tuple(ref)
    return ref["source_id"], ref["start"], ref["end"]


def unique_refs(refs):
    return [value for _, value in sorted({ref_key(r): r for r in refs}.items())]


def endpoints(kind, data, state):
    if kind in {"evidence_review", "group_interpretation", "assertion_calibration"}:
        domain = data.get("domain", "relation_groups")
        data = state.get(domain, {}).get(data.get("assertion_id", data.get("group_id")), {})
    return list(
        dict.fromkeys(
            key
            for key in [
                data.get("entity_id"),
                *data.get("mention_ids", []),
                data.get("subject_id"),
                data.get("object_id"),
                *data.get("object_ids", []),
                data.get("left_mention_id"),
                data.get("right_mention_id"),
            ]
            if key
        )
    )


def dependencies(kind, data, state):
    from .evidence_gate import _related_hints

    row = data
    if kind in {"evidence_review", "group_interpretation", "assertion_calibration"}:
        row = state.get(data.get("domain", "relation_groups"), {}).get(
            data.get("assertion_id", data.get("group_id")),
            {},
        )
    refs = [
        *row.get("evidence", []),
        *data.get("clue_refs", []),
        *data.get("required_context_refs", []),
    ]
    if kind in {"relation_alignment", "evidence_review", "group_interpretation"}:
        for hint in _related_hints(state, {**row, "evidence": refs}):
            refs.extend(hint.get("evidence", []))
    return {
        "entity_ids": sorted(endpoints(kind, data, state)),
        "field_ids": [row["field_id"]] if row.get("field_id") else [],
        "source_refs": unique_refs(refs),
    }


def dependency_hash(kind, data, state, catalog, policy):
    from .evidence_gate import _related_hints

    if kind.endswith("calibration"):
        from .deterministic import calibration_input_hash

        domain = data.get("domain", "entities")
        key = data.get("entity_id", data.get("assertion_id"))
        return calibration_input_hash(domain, state.get(domain, {}).get(key, {}), catalog)
    deps = dependencies(kind, data, state)
    endpoint_fields = (
        "referent",
        "class_iri",
        "type_evidence",
        "identity_binding",
        "name",
        "name_candidates",
        "field_ids",
        "role",
    )
    inputs = {
        k: v
        for k, v in data.items()
        if k
        not in {
            "endpoint_hypothesis",
            "priority",
            "origin_ids",
            "window_id",
            "score",
        }
    }
    assertion = None
    if kind in {"evidence_review", "group_interpretation", "assertion_calibration"}:
        row = state.get(data.get("domain", "relation_groups"), {}).get(
            data.get("assertion_id", data.get("group_id")),
            {},
        )
        assertion = {
            k: row.get(k)
            for k in (
                "predicate_iri",
                "value",
                "value_component",
                "value_evidence",
                "evidence",
                "polarity",
                "conditions",
                "participation",
                "selection",
                "timing",
                "ordered_object_ids",
                "order_evidence",
                "source_unit",
                "source_unit_evidence",
            )
        }
    return digest(
        {
            "input": inputs,
            "assertion": assertion,
            "entities": {
                key: {f: state.get("entities", {}).get(key, {}).get(f) for f in endpoint_fields}
                for key in deps["entity_ids"]
            },
            "fields": {
                key: {
                    f: state.get("fields", {}).get(key, {}).get(f)
                    for f in ("label", "value", "missing", "value_evidence", "evidence")
                }
                for key in deps["field_ids"]
            },
            "sources": deps["source_refs"],
            "hints": _related_hints(
                state,
                {
                    **data,
                    **(row if kind in {"evidence_review", "group_interpretation"} else {}),
                    "evidence": deps["source_refs"],
                },
            )
            if kind in {"relation_alignment", "evidence_review", "group_interpretation"}
            else [],
            "ontology": catalog.ontology_hash,
            "policy": policy,
            "protocol": PROTOCOL,
        }
    )


def work_id(kind, data, phase=None):
    phase = phase or ("skeleton" if kind.endswith("alignment") and kind != "referent_alignment"
                      else "deterministic" if kind.endswith("calibration") else "semantic")
    if kind == "relation_alignment":
        parts = [
            data["subject_id"],
            sorted(data["object_ids"]),
            data["predicate_iri"],
            data["region_id"],
            data.get("polarity_hint"),
            data.get("condition_hints", []),
        ]
    elif kind == "property_alignment":
        parts = [data["subject_id"], data["field_id"]]
    elif kind == "coreference_review":
        parts = sorted([data["left_mention_id"], data["right_mention_id"]])
    elif kind in {"entity_review", "entity_calibration"}:
        parts = [data["entity_id"]]
    elif kind == "referent_alignment":
        parts = [data["group_id"], sorted(data["mention_ids"])]
    else:
        parts = [
            data.get("domain", "relation_groups"),
            data.get("assertion_id", data.get("group_id")),
        ]
    return identity("work", phase, kind, parts)


def make_work(kind, data, state, catalog, policy, *, status="ready", reason=None, phase=None):
    data = {key: value for key, value in data.items() if key not in {"window_id", "score"}}
    alignment_phase = state.get("cursor", {}).get("main", {}).get("phase", "skeleton")
    phase = phase or (alignment_phase if kind in {"property_alignment", "relation_alignment"}
                      and alignment_phase in {"skeleton", "semantic"}
                      else "deterministic" if kind.endswith("calibration") else "semantic")
    key = work_id(kind, data, phase)
    fingerprint = dependency_hash(kind, data, state, catalog, policy)
    previous = state.get("work", {}).get(key)
    if previous and (
        previous["dependency_hash"] == fingerprint
        or (previous.get("expansions_used") and previous.get("expansion_basis_hash") == fingerprint)
    ):
        return deepcopy(previous)
    return WorkItem(
        id=key,
        kind=kind,
        phase=phase,
        input=deepcopy(data),
        status=status,
        reason_code=reason,
        dependencies=dependencies(kind, data, state),
        dependency_hash=fingerprint,
        expansion_basis_hash=fingerprint,
    ).model_dump()


class WorkIndex:
    def __init__(self, state):
        self.by_entity = defaultdict(set)
        self.by_field = defaultdict(set)
        self.by_source = defaultdict(set)
        self.rows = {}
        self.update(state.get("work", {}))

    def update(self, changes):
        for key, row in changes.items():
            previous = self.rows.pop(key, None)
            for current, add in ((previous, False), (row, True)):
                if not current:
                    continue
                deps = current["dependencies"]
                for index, values in (
                    (self.by_entity, deps["entity_ids"]),
                    (self.by_field, deps["field_ids"]),
                    (self.by_source, [ref_key(ref)[0] for ref in deps["source_refs"]]),
                ):
                    for value in values:
                        if add:
                            index[value].add(key)
                        else:
                            index[value].discard(key)
            if row:
                self.rows[key] = row

    def affected(self, changes):
        result = set()
        for domain, index in (("entities", self.by_entity), ("fields", self.by_field)):
            for key in changes.get(domain, {}):
                result.update(index.get(key, ()))
        for domain in ("hints", "reference_cues"):
            for row in changes.get(domain, {}).values():
                if not row:
                    continue
                for key in endpoints("relation_alignment", row, {}):
                    result.update(self.by_entity.get(key, ()))
                for ref in row.get("evidence", []):
                    result.update(self.by_source.get(ref_key(ref)[0], ()))
        return result
