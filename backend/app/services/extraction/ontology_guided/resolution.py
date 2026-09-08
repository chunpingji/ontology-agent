"""Auditable local merge/split events without name-based global identity."""

from __future__ import annotations

from pydantic import Field

from app.schemas.evidence import EvidenceModel
from app.services.extraction.evidence_identity import stable_id
from app.services.extraction.ontology_guided.contracts import VersionedRef


class ResolutionEvent(EvidenceModel):
    event_id: str = Field(min_length=1)
    action: str
    input_refs: list[VersionedRef] = Field(min_length=1)
    proof_refs: list[VersionedRef] = Field(min_length=1)
    canonical_ref: VersionedRef | None = None
    output_refs: list[VersionedRef] = Field(default_factory=list)
    alias_map: dict[str, str] = Field(default_factory=dict)
    affected_assertion_refs: list[VersionedRef] = Field(default_factory=list)
    invalidated_assertion_refs: list[VersionedRef] = Field(default_factory=list)
    reason: str = Field(min_length=1)


def merge_entities(
    inputs: list[VersionedRef],
    *,
    proof_refs: list[VersionedRef],
    canonical_id: str,
    reason: str,
) -> ResolutionEvent:
    if len({item.id for item in inputs}) < 2:
        raise ValueError("entity merge requires distinct versioned inputs")
    if not proof_refs:
        raise ValueError("entity merge requires explicit proof")
    canonical = next((item for item in inputs if item.id == canonical_id), None)
    if canonical is None:
        raise ValueError("canonical entity must be one of the inputs")
    output = VersionedRef(id=canonical.id, revision=canonical.revision + 1)
    identity = [inputs, proof_refs, output, reason]
    return ResolutionEvent(
        event_id=stable_id("resolution-event", identity),
        action="merge",
        input_refs=inputs,
        proof_refs=proof_refs,
        canonical_ref=output,
        output_refs=[output],
        alias_map={item.id: output.id for item in inputs if item.id != output.id},
        reason=reason,
    )


def split_entity(
    source: VersionedRef,
    *,
    output_ids: list[str],
    proof_refs: list[VersionedRef],
    assertion_refs: list[VersionedRef],
    reassigned: dict[str, str],
    reason: str,
) -> ResolutionEvent:
    unique = list(dict.fromkeys(output_ids))
    if len(unique) < 2:
        raise ValueError("entity split requires at least two outputs")
    if any(value not in unique for value in reassigned.values()):
        raise ValueError("assertions can only be reassigned to split outputs")
    outputs = [VersionedRef(id=item, revision=1) for item in unique]
    invalidated = [item for item in assertion_refs if item.id not in reassigned]
    identity = [source, outputs, proof_refs, assertion_refs, reassigned, reason]
    return ResolutionEvent(
        event_id=stable_id("resolution-event", identity),
        action="split",
        input_refs=[source],
        proof_refs=proof_refs,
        output_refs=outputs,
        alias_map=reassigned,
        affected_assertion_refs=assertion_refs,
        invalidated_assertion_refs=invalidated,
        reason=reason,
    )
