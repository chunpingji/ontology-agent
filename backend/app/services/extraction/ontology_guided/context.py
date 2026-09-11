"""Role-preserving task context assembly with no silent truncation."""

from __future__ import annotations

import json
from collections.abc import Callable

from pydantic import Field, PrivateAttr

from app.schemas.evidence import EvidenceAnchor, EvidenceModel
from app.services.extraction.document_ir import DocumentIR
from app.services.extraction.evidence_identity import evidence_hash, stable_id
from app.services.extraction.ontology_guided.context_records import (
    CONTEXT_RECORDS_VERSION,
    field_group_context,
    named_object_context,
)
from app.services.extraction.ontology_guided.contracts import VerificationTarget, VersionedRef
from app.services.extraction.ontology_guided.field_bindings import FieldBinding, field_bindings
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
    subject_evidence_refs: list[EvidenceAnchor] = Field(default_factory=list)
    subject_label: str = ""
    owner_field_refs: list[EvidenceAnchor] = Field(default_factory=list)
    required_context_refs: list[EvidenceAnchor] = Field(default_factory=list)
    counterevidence_refs: list[EvidenceAnchor] = Field(default_factory=list)
    omitted_refs: list[EvidenceAnchor] = Field(default_factory=list)
    token_count: int | None = Field(default=None, ge=0)
    budget_status: str = "within_budget"
    remaining_model_calls: int | None = Field(default=None, ge=0, exclude=True)
    field_bindings: list[FieldBinding] = Field(default_factory=list)
    proof_menu: dict = Field(default_factory=dict)
    protocol_state: dict = Field(default_factory=dict, exclude=True)
    repair_enabled: bool = Field(default=False, exclude=True)
    incremental_performance: bool = Field(default=False, exclude=True)
    _before_model_call: Callable[[str, int], None] | None = PrivateAttr(default=None)
    _protocol_hook: Callable[[dict], None] | None = PrivateAttr(default=None)
    _actual_calls: int = PrivateAttr(default=0)

    def bind_protocol_hook(self, callback: Callable[[dict], None]) -> None:
        self._protocol_hook = callback

    def save_protocol(self, state: dict) -> None:
        if self._protocol_hook is not None:
            self._protocol_hook(state)
        self.protocol_state = state

    @property
    def before_model_call(self) -> Callable[[str, int], None] | None:
        return self._before_model_call

    def bind_model_call_hook(self, callback: Callable[[str, int], None]) -> None:
        self._before_model_call = callback


def covers_required_sources(
    provided: list[EvidenceAnchor],
    required: list[EvidenceAnchor],
) -> bool:
    """An evidence ID alone cannot attest that its negation span was read."""
    for needed in required:
        if not any(
            quote.model_copy(
                update={
                    "span_start": needed.span_start,
                    "span_end": needed.span_end,
                }
            )
            == needed
            and (quote.span_start or 0) <= (needed.span_start or 0)
            and (
                quote.span_end is None
                or needed.span_end is not None
                and quote.span_end >= needed.span_end
            )
            for quote in provided
        ):
            return False
    return True


def assemble_context(
    target: VerificationTarget,
    record_ref: str,
    index: RecordIndex,
    *,
    endpoint_evidence: list[VersionedRef] | None = None,
    proof_dependencies: list[VersionedRef] | None = None,
    competing_subject_refs: list[VersionedRef] | None = None,
    subject_evidence_refs: list[EvidenceAnchor] | None = None,
    required_context_refs: list[EvidenceAnchor] | None = None,
    counterevidence_refs: list[EvidenceAnchor] | None = None,
    token_counter=None,
    max_input_tokens: int | None = None,
    subject_label: str = "",
    predicate=None,
    ontology=None,
    repair_enabled: bool = False,
) -> TaskContext:
    record = index.by_id.get(record_ref)
    if record is None:
        raise ValueError("record does not belong to the frozen index")
    if repair_enabled:
        # A whole-source owner reference and its explicit complete span mean
        # the same thing. Resolve that boundary before the citation gate so a
        # full verbatim quote can prove it without weakening span containment.
        subject_evidence_refs = [
            ref.model_copy(update={
                "span_start": ref.span_start or 0,
                "span_end": (ref.span_end if ref.span_end is not None
                             else len(index.ir.unit(ref.evidence_id).text)),
            }) for ref in subject_evidence_refs or []
        ]
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
    omitted: list[EvidenceAnchor] = []
    fields, owners = field_group_context(index, record_ref, subject_label)
    for purpose, units in (
        ("field_group_binding", fields),
        ("named_object_binding", named_object_context(index, record_ref, predicate, ontology)),
    ):
        for unit in units:
            if unit.evidence_id in seen or not unit.text:
                continue
            seen.add(unit.evidence_id)
            anchor = index.ir.anchor(unit.evidence_id)
            fragments.append(ContextFragment(anchor=anchor, text=index.ir.resolve(anchor),
                                             purpose=purpose, fact_eligible=False))
    # Resolve every known dependency from this frozen document.  A source is
    # binding context here, never permission to propose a new object/value.
    # Keep complete physical units (and their table closure), not a quoted
    # substring that could omit a negation elsewhere in the same paragraph.
    for purpose, references in (
        ("subject_binding", subject_evidence_refs or []),
        ("required_context", required_context_refs or []),
        ("counterevidence", counterevidence_refs or []),
    ):
        for reference in references:
            try:
                index.ir.resolve(reference)
                unit = index.ir.unit(reference.evidence_id)
            except ValueError:
                if reference not in omitted:
                    omitted.append(reference)
                continue
            units = [unit]
            for linked in index.records_by_evidence.get(unit.evidence_id, []):
                units.extend([*linked.header_units, *linked.parent_units, *linked.note_units])
            for source in units:
                if source.evidence_id in seen or not source.text:
                    continue
                seen.add(source.evidence_id)
                anchor = index.ir.anchor(source.evidence_id, 0, len(source.text))
                fragments.append(
                    ContextFragment(anchor=anchor, text=index.ir.resolve(anchor), purpose=purpose)
                )
    payload = {
        "target": target.model_dump(mode="json"),
        "record_id": record_ref,
        "fragments": [item.model_dump(mode="json") for item in fragments],
        "endpoint_evidence": endpoint_evidence or [],
        "proof_dependencies": proof_dependencies or [],
        "competing_subject_refs": competing_subject_refs or [],
        "subject_evidence_refs": subject_evidence_refs or [],
        "subject_label": subject_label,
        "owner_field_refs": owners,
        "context_records_version": CONTEXT_RECORDS_VERSION,
        "required_context_refs": required_context_refs or [],
        "counterevidence_refs": counterevidence_refs or [],
        "omitted_refs": omitted,
    }
    bindings = field_bindings(index, record_ref) if repair_enabled else []
    if repair_enabled:
        payload["field_bindings"] = [item.model_dump(mode="json") for item in bindings]
    context_hash = evidence_hash(payload)
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        default=lambda value: value.model_dump(mode="json"),
    )
    # In v2, private anchors are not transmitted. This is a source-text lower
    # bound; the adapter measures the complete compact request plus schema.
    if repair_enabled:
        serialized = "\n".join(fragment.text for fragment in fragments)
    token_count = token_counter.count(serialized) if token_counter else None
    budget_status = (
        "required_context_missing"
        if omitted
        else "context_budget_exceeded"
        if token_count is not None
        and max_input_tokens is not None
        and token_count > max_input_tokens
        else "within_budget"
    )
    # The proof target binds the *assembled* source/permission/dependency
    # closure. Merely reordering retrieval does not change this hash; a newly
    # read counterexample does, and therefore cannot overwrite the old proof.
    target_values = target.model_dump(mode="python", exclude={"target_id"})
    target_values.update(
        context_hash=context_hash,
        source_scope_hash=evidence_hash(
            [[fragment.anchor, fragment.fact_eligible, fragment.purpose] for fragment in fragments]
        ),
    )
    bound_target = VerificationTarget.create(run_fingerprint=target.target_id, **target_values)
    return TaskContext(
        context_id=stable_id("task-context", payload),
        context_hash=context_hash,
        target=bound_target,
        record_id=record_ref,
        fragments=fragments,
        endpoint_evidence=endpoint_evidence or [],
        proof_dependencies=proof_dependencies or [],
        competing_subject_refs=competing_subject_refs or [],
        subject_evidence_refs=subject_evidence_refs or [],
        subject_label=subject_label,
        owner_field_refs=owners,
        required_context_refs=required_context_refs or [],
        counterevidence_refs=counterevidence_refs or [],
        omitted_refs=omitted,
        token_count=token_count,
        budget_status=budget_status,
        field_bindings=bindings,
        repair_enabled=repair_enabled,
    )


def replay_fragment(ir: DocumentIR, fragment: ContextFragment) -> str:
    value = ir.resolve(fragment.anchor)
    if value != fragment.text:
        raise ValueError("context fragment no longer matches source")
    return value
