"""Role-preserving task context assembly with no silent truncation."""

from __future__ import annotations

from pydantic import Field

from app.schemas.evidence import EvidenceAnchor, EvidenceModel
from app.services.extraction.document_ir import DocumentIR
from app.services.extraction.evidence_identity import evidence_hash, stable_id
from app.services.extraction.ontology_guided.contracts import VerificationTarget, VersionedRef
from app.services.extraction.ontology_guided.records import RecordIndex


class ContextFragment(EvidenceModel):
    anchor: EvidenceAnchor
    text: str
    purpose: str
    fact_eligible: bool = False


class TaskContext(EvidenceModel):
    context_id: str = Field(min_length=1)
    context_hash: str = Field(min_length=64, max_length=64)
    target: VerificationTarget
    record_id: str = Field(min_length=1)
    fragments: list[ContextFragment] = Field(min_length=1)
    endpoint_evidence: list[VersionedRef] = Field(default_factory=list)
    proof_dependencies: list[VersionedRef] = Field(default_factory=list)
    competing_subject_refs: list[VersionedRef] = Field(default_factory=list)
    token_count: int | None = Field(default=None, ge=0)
    budget_status: str = "within_budget"


def assemble_context(
    target: VerificationTarget,
    record_ref: str,
    index: RecordIndex,
    *,
    endpoint_evidence: list[VersionedRef] | None = None,
    proof_dependencies: list[VersionedRef] | None = None,
    competing_subject_refs: list[VersionedRef] | None = None,
    token_counter=None,
    max_input_tokens: int | None = None,
) -> TaskContext:
    record = index.by_id.get(record_ref)
    if record is None:
        raise ValueError("record does not belong to the frozen index")
    fragments: list[ContextFragment] = []
    seen: set[str] = set()
    for purpose, eligible, units in (
        ("record_heading_or_header", False, record.header_units),
        ("parent_table_context", False, record.parent_units),
        ("target", True, record.source_units),
        ("table_note_metadata", False, record.note_units),
    ):
        for unit in units:
            if unit.evidence_id in seen or not unit.text:
                continue
            seen.add(unit.evidence_id)
            anchor = index.ir.anchor(unit.evidence_id, 0, len(unit.text))
            fragments.append(
                ContextFragment(
                    anchor=anchor,
                    text=index.ir.resolve(anchor),
                    purpose=purpose,
                    fact_eligible=eligible,
                )
            )
    payload = {
        "target": target.model_dump(mode="json"),
        "record_id": record_ref,
        "fragments": [item.model_dump(mode="json") for item in fragments],
        "endpoint_evidence": endpoint_evidence or [],
        "proof_dependencies": proof_dependencies or [],
        "competing_subject_refs": competing_subject_refs or [],
    }
    context_hash = evidence_hash(payload)
    serialized = str(payload)
    token_count = token_counter.count(serialized) if token_counter else None
    budget_status = (
        "context_budget_exceeded"
        if token_count is not None
        and max_input_tokens is not None
        and token_count > max_input_tokens
        else "within_budget"
    )
    return TaskContext(
        context_id=stable_id("task-context", payload),
        context_hash=context_hash,
        target=target,
        record_id=record_ref,
        fragments=fragments,
        endpoint_evidence=endpoint_evidence or [],
        proof_dependencies=proof_dependencies or [],
        competing_subject_refs=competing_subject_refs or [],
        token_count=token_count,
        budget_status=budget_status,
    )


def replay_fragment(ir: DocumentIR, fragment: ContextFragment) -> str:
    value = ir.resolve(fragment.anchor)
    if value != fragment.text:
        raise ValueError("context fragment no longer matches source")
    return value
