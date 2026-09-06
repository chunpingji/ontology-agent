"""Subject-conditioned context with separate fact/binding regions and cache identity."""

from typing import Any, Protocol

from pydantic import Field

from app.schemas.evidence import Candidate, EvidenceAnchor, EvidenceModel, ExtractionTask
from app.services.extraction.document_ir import DocumentIR
from app.services.extraction.evidence_identity import canonical_json, evidence_hash, stable_id
from app.services.extraction.evidence_scope import document_anchors, scope_contains
from app.services.extraction.model_protocol import PROTOCOL_VERSION, ModelProtocol

MODEL_CONTEXT_VERSION = "model-context-v2"


def model_anchor(anchor: dict) -> dict:
    """Keep replay coordinates and record structure, not repeated source digests."""
    return {
        key: value for key, value in anchor.items()
        if key not in {"document_hash", "structure_hash", "parser_version"}
        and (value is not None or key in {"span_start", "span_end"})
    }


def model_candidate(candidate: dict | None) -> dict | None:
    if candidate is None:
        return None
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
            "condition_provenance_indexes",
        }
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


def model_request(payload: dict, stage: str, candidate: dict | None = None) -> str:
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
    return canonical_json({
        "stage": stage, "context": serialized,
        "candidate": model_candidate(candidate),
    })


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


def split_windows(
    text: str, tokenizer: TokenCounter, max_tokens: int, overlap: int = 32
) -> list[tuple[int, int]]:
    if max_tokens < 1:
        raise ValueError("window budget must be positive")
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
        if any(not scope_contains(task.scope, anchor, ir) for anchor in facts):
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
    payload = {
        "task": task.model_dump(mode="json"),
        "subjects": {
            key: {
                "candidate_id": value.candidate_id,
                "revision": value.revision,
                "class_iri": value.class_iri,
                "text": value.text,
                "identity": value.identity,
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
    }
    # Includes instructions/schema in token accounting; no subject is sacrificed
    # for a long optional ancestor. Provider chat framing is reserved explicitly.
    def count():
        user = model_request(payload, "recall")
        if compact_identifiers:
            wire = ModelProtocol(system_prompt, user, response_schema or {})
            return tokenizer.count(wire.user + wire.system) + 128
        return tokenizer.count(user + system_prompt + canonical_json(response_schema or {})) + 128

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
