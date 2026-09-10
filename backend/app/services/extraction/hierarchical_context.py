"""Subject-conditioned context with separate fact/binding regions and cache identity."""

import json
from typing import Any, Protocol

from pydantic import Field

from app.schemas.evidence import Candidate, EvidenceAnchor, EvidenceModel, ExtractionTask
from app.services.extraction.document_ir import DocumentIR
from app.services.extraction.evidence_identity import canonical_json, evidence_hash, stable_id
from app.services.extraction.evidence_scope import document_anchors, scope_contains, scope_intervals
from app.services.extraction.model_protocol import PROTOCOL_VERSION, ModelProtocol, planning_schema
from app.services.extraction.performance import timed
from app.services.extraction.table_records import table_records

MODEL_CONTEXT_VERSION = "model-context-v10-structural-records"


def model_anchor(anchor: dict) -> dict:
    """Keep replay coordinates and record structure, not repeated source digests."""
    return {
        key: value for key, value in anchor.items()
        if key not in {"document_hash", "structure_hash", "parser_version"}
        and (value is not None or key in {"span_start", "span_end"})
    }


def model_candidate(candidate: dict | None, *, proposed_identity: bool = False) -> dict | None:
    if candidate is None:
        return None
    if "proposed_entities" in candidate:
        return {"proposed_entities": [
            model_candidate(c, proposed_identity=True) for c in candidate["proposed_entities"]
        ], "competing_class_iris": candidate.get("competing_class_iris", [])}
    if "shared_candidates" in candidate:
        return {
            "shared_candidates": [model_candidate(c) for c in candidate["shared_candidates"]],
            "instruction": candidate.get("instruction", ""),
        }
    result = {
        key: value for key, value in candidate.items()
        if key in {
            "candidate_id", "revision", "kind", "class_iri", "text", "identity",
            "subject", "object", "predicate_iri", "literal", "assertion_status",
            "applicable_at", "path_root", "relationship_path", "dependency_refs",
            "condition_provenance_indexes", "type_verification",
        }
    }
    if candidate.get("kind") == "entity" and not proposed_identity:
        verification = candidate.get("type_verification") or {}
        if verification.get("identity_supported") is not True:
            # Retain the original proposal in the audit candidate, but do not
            # present an unverified project/report number as an entity key in
            # downstream binding/reference requests. Initial type verification
            # still receives that proposal through proposed_entities above.
            result["identity"] = {
                key: value for key, value in candidate.get("identity", {}).items()
                if key not in {"key_predicate", "key_value"}
            }
    result["condition_anchors"] = [model_anchor(a) for a in candidate.get("condition_anchors", [])]
    result["provenance"] = [
        {**p, "anchors": [model_anchor(a) for a in p["anchors"]]}
        if p.get("kind") == "document" else p
        for p in candidate.get("provenance", [])
    ]
    result["bindings"] = [
        {**binding, "anchors": [model_anchor(a) for a in binding.get("anchors", [])]}
        for binding in candidate.get("bindings", [])
    ]
    return result


def model_task(task: dict) -> dict:
    result = {
        key: value for key, value in task.items()
        if key in {
            "task_kind", "subject", "predicate_iri", "predicate_definition",
            "target_class_iris", "object_candidates", "competing_subjects",
            "relationship_path", "path_root", "dependency_refs",
        }
    }
    definition = result.get("predicate_definition", {})
    if "classes" in definition:
        # The class-map keys are the exact allowed IRIs; do not repeat them as
        # a second long array. Keep the authoritative list on the server task.
        result.pop("target_class_iris", None)
        result["predicate_definition"] = {
            **definition,
            "classes": {
                iri: {key: value for key, value in details.items() if key != "iri"}
                for iri, details in definition["classes"].items()
            },
        }
    return result


def model_request(
    payload: dict, stage: str, candidate: dict | None = None, *, planning=False,
) -> str:
    """A compact model projection; the full envelope remains the audit identity.

    Only target fragments can propose facts. Scope validation and full anchor
    replay happen against the original task/IR, never against this projection.
    """
    context = {
        "task": model_task(payload["task"]),
        "subjects": {key: model_candidate(value) for key, value in payload["subjects"].items()},
        "fragments": [
            {**fragment, "anchor": model_anchor(fragment["anchor"])}
            for fragment in payload["fragments"]
        ],
        "effective_class": payload["effective_class"],
    }
    # A stable ontology/task prefix can be reused by the local model across
    # evidence windows. Canonicalize each value without alphabetically moving
    # changing source fragments ahead of that prefix.
    serialized = "{" + ",".join(
        canonical_json(key) + ":" + canonical_json(value) for key, value in context.items()
    ) + "}"
    if not planning:
        task = context["task"]
        # Ontology definitions precede dynamic subject IDs, record scopes and evidence.
        context["task"] = {
            key: task[key] for key in (
                "task_kind", "predicate_definition", "predicate_iri", "target_class_iris",
                *sorted(set(task) - {"task_kind", "predicate_definition", "predicate_iri",
                                    "target_class_iris"}),
            ) if key in task
        }
        serialized = json.dumps(context, ensure_ascii=False, separators=(",", ":"))
    request = {
        "stage": stage, "context": serialized,
        "candidate": model_candidate(candidate),
    }
    task = context["task"]
    if task.get("predicate_iri"):
        # Keep the precise target salient after the long source/subject context.
        # This is ontology data, not predicate-name dispatch or value inference.
        request["focus"] = {
            "predicate": task.get("predicate_definition") or {"iri": task["predicate_iri"]},
            "instruction": (
                "独立核对候选值/对象是否满足本次谓词的精确含义和主体归属；"
                "引用完整支持断言。候选本身不是事实，不满足则 supported=false。"
                if stage == "verify_binding" else
                "仅回答此谓词，按其精确含义从 target 原文逐字选取值/断言；"
                "区分上限与下限、计划与实际、不同主体，无法支持则返回空 assertions。"
            ),
        }
    return (canonical_json(request) if planning else
            json.dumps(request, ensure_ascii=False, separators=(",", ":")))


class TokenCounter(Protocol):
    identity: str

    def count(self, text: str) -> int: ...


class TokenizationUnavailable(ValueError):
    """Exact token counting failed; estimated-token fallback is forbidden."""


class ContextEnvelope(EvidenceModel):
    lookup_key: str
    context_hash: str
    completion: str
    reason: str | None = None
    serialized_input: str | None = None
    allowed_fact_regions: list[EvidenceAnchor] = Field(default_factory=list)
    allowed_binding_regions: list[EvidenceAnchor] = Field(default_factory=list)
    fragments: list[dict[str, Any]] = Field(default_factory=list)
    ancestor_chain: list[str] = Field(default_factory=list)
    retained_metadata: list[str] = Field(default_factory=list)
    omitted_metadata: list[str] = Field(default_factory=list)
    input_tokens: int = 0


@timed("split_windows")
def split_windows(
    text: str, tokenizer: TokenCounter, max_tokens: int, overlap: int = 32
) -> list[tuple[int, int]]:
    if max_tokens < 1:
        raise ValueError("window budget must be positive")
    if not text:
        return []
    if tokenizer.count(text) <= max_tokens:
        return [(0, len(text))]
    windows, start = [], 0
    while start < len(text):
        left, right, end = start + 1, len(text), start
        while left <= right:
            mid = (left + right) // 2
            if tokenizer.count(text[start:mid]) <= max_tokens:
                end, left = mid, mid + 1
            else:
                right = mid - 1
        if end == start:
            raise ValueError("single codepoint exceeds tokenizer budget")
        windows.append((start, end))
        if end == len(text):
            break
        start = max(start + 1, end - min(overlap, (end - start) // 4))
    return windows


@timed("context")
def build_context(
    task: ExtractionTask,
    ir: DocumentIR,
    candidates: dict[str, Candidate],
    tokenizer: TokenCounter,
    *,
    model: str,
    effective_class: str = "",
    system_prompt: str = "",
    response_schema: dict | None = None,
    compact_identifiers: bool = False,
    citation_protocol_version: str | None = None,
    record_index=None,
    required_context_refs: list[EvidenceAnchor] | None = None,
) -> ContextEnvelope:
    references = [
        ref
        for ref in [
            task.subject,
            task.path_root,
            *task.competing_subjects,
            *task.object_candidates,
            *task.dependency_refs,
        ]
        if ref is not None
    ]
    selected = {}
    for ref in references:
        candidate = candidates.get(ref.candidate_id)
        if candidate is None or candidate.revision != ref.revision:
            raise ValueError("missing or stale context subject")
        selected[ref.candidate_id] = candidate
    lookup = stable_id(
        "context-input",
        {
            "task": task,
            "document": ir.analysis_id,
            "subjects": selected,
            "effective_class": effective_class,
            "model": model,
            "tokenizer": tokenizer.identity,
            "system_prompt": system_prompt,
            "response_schema": response_schema,
            "model_context_version": MODEL_CONTEXT_VERSION,
            "reference_protocol": PROTOCOL_VERSION if compact_identifiers else "canonical",
            "citation_protocol": citation_protocol_version,
        },
    )
    facts = []
    if task.target_ranges:
        for region in task.target_ranges:
            if region.evidence_id not in task.target_evidence_ids:
                raise ValueError("target range is not registered in task")
            facts.append(ir.anchor(region.evidence_id, region.start, region.end))
    else:
        facts = [ir.anchor(identity) for identity in task.target_evidence_ids]
    if task.subject:
        if task.scope is None or task.scope.subject.candidate_id != task.subject.candidate_id:
            raise ValueError("task scope belongs to a different subject")
        if task.scope.subject.revision != task.subject.revision:
            raise ValueError("task scope uses a stale subject revision")
        intervals = scope_intervals(task.scope, ir)
        if any(
            not scope_contains(task.scope, anchor, ir, intervals=intervals) for anchor in facts
        ):
            raise ValueError("task target outside accepted scope")
    bindings = list(facts)
    fragments = [
        {
            "anchor": a.model_dump(mode="json"),
            "text": ir.resolve(a),
            "purpose": "target",
            "fact_eligible": True,
        }
        for a in facts
    ]
    for anchor in required_context_refs or []:
        text = ir.resolve(anchor)
        if anchor not in bindings:
            bindings.append(anchor)
            fragments.append({
                "anchor": anchor.model_dump(mode="json"), "text": text,
                "purpose": "related_section_reference_context", "fact_eligible": False,
            })
    # Row and header evidence explain what each cell denotes, even when a source
    # window splits the record. They aid typing/binding but cannot propose facts
    # outside the task's target ranges.
    record_units = [ir.unit(a.evidence_id) for a in facts if a.table_path]
    target_ids = {a.evidence_id for a in facts}
    records = table_records(ir)
    for unit in records.metadata(record_units):
        if unit.evidence_id in target_ids or not unit.text or not unit.table_path:
            continue
        anchor = ir.anchor(unit.evidence_id)
        bindings.append(anchor)
        fragments.append({
            "anchor": anchor.model_dump(mode="json"), "text": unit.text,
            "purpose": "table_record_metadata", "fact_eligible": False,
        })
    for unit in records.notes(record_units):
        if unit.evidence_id not in target_ids:
            anchor = ir.anchor(unit.evidence_id)
            bindings.append(anchor)
            fragments.append({
                "anchor": anchor.model_dump(mode="json"), "text": unit.text,
                "purpose": "table_note_metadata", "fact_eligible": False,
            })
    for identity, candidate in selected.items():
        for anchor in document_anchors(candidate):
            bindings.append(anchor)
            fragments.append(
                {
                    "anchor": anchor.model_dump(mode="json"),
                    "text": ir.resolve(anchor),
                    "purpose": "subject_evidence",
                    "subject_id": identity,
                    "fact_eligible": False,
                }
            )
    if task.scope:
        for anchor in [
            *task.scope.construction_evidence,
            *(anchor for expansion in task.scope.expansion_history
              for anchor in expansion.evidence),
        ]:
            if anchor not in bindings:
                bindings.append(anchor)
                fragments.append({
                    "anchor": anchor.model_dump(mode="json"), "text": ir.resolve(anchor),
                    "purpose": ("scope_construction_evidence"
                                if anchor in task.scope.construction_evidence
                                else "reference_owner_context"), "fact_eligible": False,
                })
        if any(expansion.reason == "field_group_reference_lookup"
               for expansion in task.scope.expansion_history):
            from app.services.extraction.ontology_guided.records import RecordIndex
            from app.services.extraction.record_targets import field_group_context

            for group_id, units in field_group_context(task, record_index or RecordIndex(ir)):
                for position, unit in enumerate(units):
                    anchor = ir.anchor(unit.evidence_id)
                    matching = [fragment for fragment in fragments
                                if fragment["anchor"] == anchor.model_dump(mode="json")]
                    if not matching:
                        fragment = {
                            "anchor": anchor.model_dump(mode="json"), "text": unit.text,
                            "purpose": "field_group_reference_context", "fact_eligible": False,
                        }
                        fragments.append(fragment)
                        bindings.append(anchor)
                        matching = [fragment]
                    for fragment in matching:
                        fragment.update(field_group_id=group_id, field_index=position)
    payload = {
        "task": task.model_dump(mode="json"),
        "subjects": {
            key: {
                "candidate_id": value.candidate_id,
                "revision": value.revision,
                "class_iri": value.class_iri,
                "text": value.text,
                "identity": value.identity,
                "type_verification": (
                    value.type_verification.model_dump(mode="json")
                    if value.type_verification else None
                ),
                "kind": value.kind,
                "predicate_iri": value.predicate_iri,
                "subject": value.subject.model_dump() if value.subject else None,
                "object": value.object.model_dump() if value.object else None,
                "assertion_status": value.assertion_status,
                "provenance": [p.model_dump(mode="json") for p in value.provenance],
            }
            for key, value in selected.items()
        },
        "fragments": fragments,
        "effective_class": effective_class,
        "citation_protocol": citation_protocol_version,
    }
    # Includes instructions/schema in token accounting; no subject is sacrificed
    # for a long optional ancestor. Provider chat framing is reserved explicitly.
    def count():
        user = model_request(payload, "recall", planning=True)
        if compact_identifiers:
            wire = ModelProtocol(system_prompt, user, response_schema or {}, planning=True)
            return tokenizer.count(wire.user + wire.system) + 128
        return tokenizer.count(
            user + system_prompt + canonical_json(planning_schema(response_schema or {}))
        ) + 128

    base_tokens = count()
    if base_tokens > task.budget.max_input_tokens:
        return ContextEnvelope(
            lookup_key=lookup,
            context_hash=evidence_hash(payload),
            completion="incomplete",
            reason="budget_exceeded",
            allowed_fact_regions=facts,
            allowed_binding_regions=bindings,
            input_tokens=base_tokens,
        )
    nodes = {node["node_id"]: node for node in ir.nodes}
    ancestors, retained, omitted = [], [], []
    node_id = ir.unit(task.target_evidence_ids[0]).section_node_id
    while node_id:
        node = nodes[node_id]
        ancestors.append(node_id)
        heading = next(
            (
                unit
                for unit in ir.evidence_units
                if unit.kind == "heading" and unit.section_node_id == node_id
            ),
            None,
        )
        if heading:
            anchor = ir.anchor(heading.evidence_id)
            fragment = {
                "anchor": anchor.model_dump(mode="json"),
                "text": heading.text,
                "purpose": "ancestor_metadata",
                "fact_eligible": False,
            }
            fragments.append(fragment)
            if count() > task.budget.max_input_tokens:
                fragments.pop()
                omitted.append(node_id)
            else:
                bindings.append(anchor)
                retained.append(node_id)
        node_id = node["parent_id"]
    serialized = canonical_json(payload)
    return ContextEnvelope(
        lookup_key=lookup,
        context_hash=evidence_hash(payload),
        completion="complete",
        serialized_input=serialized,
        allowed_fact_regions=facts,
        allowed_binding_regions=bindings,
        fragments=fragments,
        ancestor_chain=ancestors,
        retained_metadata=retained,
        omitted_metadata=omitted,
        input_tokens=count(),
    )
