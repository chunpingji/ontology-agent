"""Role-preserving task context assembly with no silent truncation."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from pydantic import ConfigDict, Field, PrivateAttr, model_validator

from app.schemas.evidence import EvidenceAnchor, EvidenceModel
from app.services.extraction.document_ir import DocumentIR
from app.services.extraction.evidence_identity import evidence_hash, stable_id
from app.services.extraction.ontology_guided.context_records import (
    CONTEXT_RECORDS_VERSION,
    field_group_context,
    named_object_context,
)
from app.services.extraction.ontology_guided.contracts import VerificationTarget, VersionedRef
from app.services.extraction.ontology_guided.field_bindings import (
    FIELD_BINDING_VERSION,
    FieldBinding,
    field_bindings,
)
from app.services.extraction.ontology_guided.records import RecordIndex

if TYPE_CHECKING:
    from app.services.extraction.ontology_guided.claim_protocol import (
        EntityDependencyView,
        ExtractionProfile,
        SchemaCard,
        VerificationInput,
    )
    from app.services.extraction.ontology_guided.contracts import GraphNode, TraversalScope
    from app.services.extraction.ontology_guided.scheduler import RecognitionTask
    from app.services.extraction.ontology_guided.tool_contracts import EvidenceUnit, ToolObservation
    from app.services.extraction.ontology_guided.tool_model_adapter import TurnPlan


class IRIdentity(EvidenceModel):
    model_config = ConfigDict(strict=True)
    document_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    parser_version: str
    structure_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class AuthorizedFragment(EvidenceModel):
    model_config = ConfigDict(strict=True)
    record_id: str | None
    evidence_id: str
    span_start: int = Field(ge=0)
    span_end: int = Field(gt=0)
    role: str
    fact_eligible: bool

    @model_validator(mode="after")
    def valid_interval(self):
        if self.span_start >= self.span_end:
            raise ValueError("authorized fragment must have a nonempty half-open span")
        if self.record_id is None and self.fact_eligible:
            raise ValueError("unassigned auxiliary context cannot grant fact eligibility")
        return self


class ContextBindingRefs(EvidenceModel):
    model_config = ConfigDict(strict=True)
    endpoint_evidence: list[VersionedRef]
    proof_dependencies: list[VersionedRef]
    competing_subject_refs: list[VersionedRef]
    subject_evidence_refs: list[EvidenceAnchor]
    owner_field_refs: list[EvidenceAnchor]
    required_context_refs: list[EvidenceAnchor]
    counterevidence_refs: list[EvidenceAnchor]
    omitted_refs: list[EvidenceAnchor]


class ContextAuthorization(EvidenceModel):
    model_config = ConfigDict(strict=True)
    task_id: str
    ir_identity: IRIdentity
    context_policy_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    record_ids: list[str]
    fragments: list[AuthorizedFragment]
    bindings: ContextBindingRefs

    @model_validator(mode="after")
    def unique_locations(self):
        locations = [
            (fragment.record_id, fragment.evidence_id, fragment.span_start,
             fragment.span_end, fragment.role)
            for fragment in self.fragments
        ]
        if (len(self.record_ids) != len(set(self.record_ids))
                or len(locations) != len(set(locations))):
            raise ValueError("authorization must not duplicate records or fragment locations")
        return self


@dataclass(frozen=True)
class SourceCatalogEntry:
    record_id: str
    title: str | None
    summary: str | None
    summary_source: str | None
    authorized_evidence_ids: list[str]


@dataclass(frozen=True)
class ContextFeedback:
    target_id: str | None
    facet: str | None
    code: str
    field_path: str | None
    message: str
    evidence_ids: list[str]


@dataclass(frozen=True)
class ModelContextView:
    task_id: str
    subject_ref: VersionedRef
    predicate_iri: str
    scope: TraversalScope
    stage: Literal["discovery", "verification"]
    context_hash: str
    evidence_hash: str
    schema_card: SchemaCard
    source_catalog: list[SourceCatalogEntry]
    evidence_units: list[EvidenceUnit]
    registered_refs: list[VersionedRef]
    verification_input: VerificationInput | None
    tool_observations: list[ToolObservation]
    feedback: list[ContextFeedback]
    turn: TurnPlan


AUTHORIZED_CONTEXT_VERSION = "ontology-authorized-context-v1"


class ContextFragment(EvidenceModel):
    anchor: EvidenceAnchor
    text: str
    purpose: str
    fact_eligible: bool = False

    def bounded_anchor(self) -> EvidenceAnchor:
        """Bound a whole-source reference by its replayed text, without changing hashes.

        Explicit slices retain their original limits. Whole-source anchors also
        occur in persisted task contexts, so normalize only when checking scope.
        """
        if self.anchor.span_start is not None:
            return self.anchor
        return self.anchor.model_copy(update={"span_start": 0, "span_end": len(self.text)})


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
    protocol_results: dict = Field(default_factory=dict, exclude=True)
    tool_inputs: dict = Field(default_factory=dict, exclude=True)
    repair_enabled: bool = Field(default=False, exclude=True)
    incremental_performance: bool = Field(default=False, exclude=True)
    compact_recognition: bool = Field(default=False, exclude=True)
    cmc_describes_type_scope: bool = Field(default=False, exclude=True)
    expert_feedback: dict = Field(default_factory=dict, exclude=True)
    _before_model_call: Callable[[str, int], None] | None = PrivateAttr(default=None)
    _protocol_hook: Callable[[dict], None] | None = PrivateAttr(default=None)
    _actual_calls: int = PrivateAttr(default=0)

    def bind_protocol_hook(self, callback: Callable[[dict], None]) -> None:
        self._protocol_hook = callback

    def save_protocol(self, state: dict, *, result_changes: dict | None = None) -> None:
        if self._protocol_hook is not None:
            self._protocol_hook({**state, "result_changes": result_changes}
                                if result_changes else state)
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


def _context_payload(
    target: VerificationTarget,
    record_id: str,
    fragments: list[ContextFragment],
    references: ContextBindingRefs,
    *,
    subject_label: str,
    bindings: list[FieldBinding],
    repair_enabled: bool,
) -> dict:
    payload = {
        "target": target.model_dump(mode="json"),
        "record_id": record_id,
        "fragments": [item.model_dump(mode="json") for item in fragments],
        **references.model_dump(mode="json"),
        "subject_label": subject_label,
        "context_records_version": CONTEXT_RECORDS_VERSION,
    }
    if repair_enabled:
        payload["field_bindings"] = [item.model_dump(mode="json") for item in bindings]
    return payload


def _bind_context_target(
    target: VerificationTarget, fragments: list[ContextFragment], context_hash: str
) -> VerificationTarget:
    target_values = target.model_dump(mode="python", exclude={"target_id"})
    target_values.update(
        context_hash=context_hash,
        source_scope_hash=evidence_hash(
            [[fragment.anchor, fragment.fact_eligible, fragment.purpose] for fragment in fragments]
        ),
    )
    return VerificationTarget.create(run_fingerprint=target.target_id, **target_values)


def fragment_record_id(fragment, index: RecordIndex, preferred_record_id: str) -> str | None:
    """A source location may be auxiliary to several records, or to none."""
    owners = [record.record_id for record in index.records if any(
        unit.evidence_id == fragment.anchor.evidence_id for unit in (
            *record.source_units, *record.header_units, *record.note_units, *record.parent_units,
        )
    )]
    if preferred_record_id in owners:
        return preferred_record_id
    if len(owners) == 1:
        return owners[0]
    if not fragment.fact_eligible:
        return None
    raise ValueError("evidence_record_ambiguous")


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
    retrieval_context_refs: list[EvidenceAnchor] | None = None,
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
        ("retrieval_group_binding", retrieval_context_refs or []),
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
    bindings = field_bindings(index, record_ref) if repair_enabled else []
    references = ContextBindingRefs(
        endpoint_evidence=endpoint_evidence or [],
        proof_dependencies=proof_dependencies or [],
        competing_subject_refs=competing_subject_refs or [],
        subject_evidence_refs=subject_evidence_refs or [],
        owner_field_refs=owners,
        required_context_refs=required_context_refs or [],
        counterevidence_refs=counterevidence_refs or [],
        omitted_refs=omitted,
    )
    payload = _context_payload(
        target, record_ref, fragments, references, subject_label=subject_label,
        bindings=bindings, repair_enabled=repair_enabled,
    )
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
    bound_target = _bind_context_target(target, fragments, context_hash)
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


def authorization_policy_hash(
    task: RecognitionTask, base: TaskContext, *,
    target_seed: VerificationTarget, card: SchemaCard, profile: ExtractionProfile,
    entity_dependencies: list[EntityDependencyView], scope: TraversalScope,
    subject_node: GraphNode,
) -> str:
    """Identify the frozen server policy used for both initial assembly and restore."""
    return evidence_hash({
        "task_id": task.task_id,
        "base_target": target_seed,
        "scope": scope,
        "schema_card": card,
        "profile": profile,
        "context_assembler_version": AUTHORIZED_CONTEXT_VERSION,
        "context_records_version": CONTEXT_RECORDS_VERSION,
        "field_binding_version": FIELD_BINDING_VERSION,
        "repair_enabled": base.repair_enabled,
        "subject_label": subject_node.label,
        "entity_dependencies": entity_dependencies,
    })


def _require_authorization(condition: bool, reason: str) -> None:
    if not condition:
        raise ValueError(f"context_authorization_mismatch: {reason}")


def build_authorized_context(
    task: RecognitionTask, base: TaskContext, *,
    authorization: ContextAuthorization, index: RecordIndex,
    evidence_revision: int, target_seed: VerificationTarget, run_fingerprint: str,
    card: SchemaCard, profile: ExtractionProfile,
    entity_dependencies: list[EntityDependencyView], scope: TraversalScope,
    subject_node: GraphNode,
) -> tuple[TaskContext, str]:
    """Derive a proposed context and evidence hash; this grants no persisted permission.

    The coordinator supplies its frozen base, exact graph node and dependencies.
    It confirms the authorization and hashes together before exposing this view.
    No text, field bindings or permission is read from model/worker protocol items.
    """
    _require_authorization(type(evidence_revision) is int and evidence_revision > 0, "revision")
    authorization = ContextAuthorization.model_validate(
        authorization.model_dump(mode="json"), strict=True
    )
    index.ir.verify_identity()
    subject_ref = VersionedRef(id=task.subject.entity_id, revision=task.subject.revision)
    dependencies = {(value.entity_ref.id, value.entity_ref.revision): value
                    for value in entity_dependencies}
    subject_dependency = dependencies.get((subject_ref.id, subject_ref.revision))
    _require_authorization(
        len(dependencies) == len(entity_dependencies)
        and subject_dependency is not None
        and subject_dependency.class_iri == task.subject.class_iri
        and subject_node.entity_id == subject_ref.id
        and subject_node.revision == subject_ref.revision
        and subject_node.class_iri == task.subject.class_iri
        and subject_node.root == task.subject.is_document_root,
        "subject identity",
    )
    for dependency in entity_dependencies:
        _require_authorization(
            dependency.content_hash == evidence_hash(
                dependency.model_dump(mode="json", exclude={"content_hash"})
            ),
            "entity dependency hash",
        )
        for anchor in dependency.source_refs:
            index.ir.resolve(anchor)
    _require_authorization(
        authorization.task_id == task.task_id
        and base.record_id == task.record_id
        and task.record_id in index.by_id
        and task.record_id in authorization.record_ids
        and card.subject_ref == subject_ref
        and task.subject.class_iri in card.class_iris
        and any(predicate.iri == task.predicate_iri for predicate in card.predicates)
        and target_seed.task_id == task.task_id
        and target_seed.subject_ref == task.subject
        and target_seed.predicate_iri == task.predicate_iri
        and target_seed.document_context.document_hash == index.ir.document_hash,
        "task, card or target identity",
    )
    recreated_seed = VerificationTarget.create(
        run_fingerprint=run_fingerprint,
        **target_seed.model_dump(mode="python", exclude={"target_id"}),
    )
    _require_authorization(
        target_seed == recreated_seed
        and base.target == _bind_context_target(target_seed, base.fragments, base.context_hash),
        "unbound target seed",
    )
    ir_identity = IRIdentity(
        document_hash=index.ir.document_hash,
        parser_version=index.ir.parser_version,
        structure_hash=index.ir.structure_hash,
    )
    _require_authorization(authorization.ir_identity == ir_identity, "frozen IR identity")
    policy_hash = authorization_policy_hash(
        task, base, target_seed=target_seed, card=card, profile=profile,
        entity_dependencies=entity_dependencies, scope=scope, subject_node=subject_node,
    )
    _require_authorization(authorization.context_policy_hash == policy_hash, "context policy")
    _require_authorization(
        all(record_id in index.by_id for record_id in authorization.record_ids),
        "authorized record identity",
    )
    fragments = []
    for source in authorization.fragments:
        anchor = index.ir.anchor(source.evidence_id, source.span_start, source.span_end)
        if source.record_id is None:
            _require_authorization(not source.fact_eligible and any(
                not old.fact_eligible and old.purpose == source.role
                and covers_required_sources([old.anchor], [anchor]) for old in base.fragments
            ), "unassigned source must be original auxiliary context")
        else:
            _require_authorization(source.record_id in authorization.record_ids, "fragment record")
            record = index.by_id[source.record_id]
            member_ids = {unit.evidence_id for unit in (
                *record.source_units, *record.header_units, *record.parent_units, *record.note_units
            )}
            _require_authorization(source.evidence_id in member_ids, "record/evidence membership")
        _require_authorization(
            not source.fact_eligible or any(
                old.fact_eligible and old.purpose == source.role
                and covers_required_sources([old.anchor], [anchor]) for old in base.fragments
            ),
            "supplemental context cannot grant fact eligibility",
        )
        fragments.append(ContextFragment(
            anchor=anchor, text=index.ir.resolve(anchor),
            purpose=source.role, fact_eligible=source.fact_eligible,
        ))
    _require_authorization(bool(fragments), "empty authorized context")
    for original in base.fragments:
        _require_authorization(
            covers_required_sources(
                [value.anchor for value in fragments
                 if value.purpose == original.purpose
                 and value.fact_eligible == original.fact_eligible],
                [original.anchor],
            ),
            "required base context omitted or role changed",
        )
    references = authorization.bindings
    for name in ContextBindingRefs.model_fields:
        _require_authorization(
            all(value in getattr(references, name) for value in getattr(base, name)),
            f"known {name} omitted",
        )
    for name in ("subject_evidence_refs", "owner_field_refs", "required_context_refs",
                 "counterevidence_refs"):
        for anchor in getattr(references, name):
            if anchor in references.omitted_refs:
                continue
            index.ir.resolve(anchor)
            _require_authorization(
                covers_required_sources([fragment.anchor for fragment in fragments], [anchor]),
                f"unread {name}",
            )
    _, owners = field_group_context(index, task.record_id, subject_node.label)
    _require_authorization(references.owner_field_refs == owners, "field owner bindings")
    bindings = field_bindings(index, task.record_id) if base.repair_enabled else []
    payload = _context_payload(
        target_seed, task.record_id, fragments, references, subject_label=subject_node.label,
        bindings=bindings, repair_enabled=base.repair_enabled,
    )
    payload.update(
        task_id=task.task_id, context_policy_hash=policy_hash,
        record_ids=authorization.record_ids, evidence_revision=evidence_revision,
    )
    context_hash = evidence_hash(payload)
    result = TaskContext(
        context_id=stable_id("task-context", payload),
        context_hash=context_hash,
        target=_bind_context_target(target_seed, fragments, context_hash),
        record_id=task.record_id,
        fragments=fragments,
        **references.model_dump(mode="python"),
        subject_label=subject_node.label,
        field_bindings=bindings,
        repair_enabled=base.repair_enabled,
        budget_status=("required_context_missing" if references.omitted_refs
                       else base.budget_status),
    )
    if base.before_model_call is not None:
        result.bind_model_call_hook(base.before_model_call)
    return result, evidence_hash({
        "ir_identity": authorization.ir_identity,
        "fragments": authorization.fragments,
        "bindings": authorization.bindings,
    })


def build_retrieval_authorization(
    task: RecognitionTask, base: TaskContext, record_ids: list[str], *,
    index: RecordIndex, target_seed: VerificationTarget, card: SchemaCard,
    profile: ExtractionProfile, entity_dependencies: list[EntityDependencyView],
    scope: TraversalScope, subject_node: GraphNode,
    current: ContextAuthorization | None = None,
) -> ContextAuthorization:
    """Reconstruct one current permission description from complete frozen records.

    This pure function grants nothing. The coordinator confirms its result with
    the tool result before exposing new source IDs; every added span is context.
    """
    policy = authorization_policy_hash(
        task, base, target_seed=target_seed, card=card, profile=profile,
        entity_dependencies=entity_dependencies, scope=scope, subject_node=subject_node,
    )
    identity = IRIdentity(
        document_hash=index.ir.document_hash, parser_version=index.ir.parser_version,
        structure_hash=index.ir.structure_hash,
    )
    if current is not None:
        _require_authorization(
            current.task_id == task.task_id and current.ir_identity == identity
            and current.context_policy_hash == policy, "retrieval base policy",
        )
        fragments = list(current.fragments)
        records = list(current.record_ids)
        bindings = current.bindings.model_copy(deep=True)
    else:
        fragments, records = [], [task.record_id]
        bindings = ContextBindingRefs(**{
            name: list(getattr(base, name)) for name in ContextBindingRefs.model_fields
        })
        for fragment in base.fragments:
            owner = fragment_record_id(fragment, index, task.record_id)
            if owner is not None:
                records.append(owner)
            start = fragment.anchor.span_start or 0
            fragments.append(AuthorizedFragment(
                record_id=owner, evidence_id=fragment.anchor.evidence_id,
                span_start=start, span_end=start + len(fragment.text),
                role=fragment.purpose, fact_eligible=fragment.fact_eligible,
            ))
    # One physical span/role has one owner in the permission description even if
    # a merged cell participates in several selected logical record views.
    seen = {(f.evidence_id, f.span_start, f.span_end, f.role) for f in fragments}
    # The result carries cumulative record IDs. Close over base-context owners
    # now as well, so rebuilding from that result cannot silently add fragments
    # that the retrieval handler did not include in its confirmed context hash.
    for record_id in dict.fromkeys([*records, *record_ids]):
        _require_authorization(record_id in index.by_id, "retrieval record")
        records.append(record_id)
        record = index.by_id[record_id]
        for role, units in (
            ("binding", record.source_units), ("header", record.header_units),
            ("parent", record.parent_units), ("note", record.note_units),
        ):
            for unit in units:
                if not unit.text:
                    continue
                key = (unit.evidence_id, 0, len(unit.text), role)
                if key in seen:
                    continue
                seen.add(key)
                fragments.append(AuthorizedFragment(
                    record_id=record_id, evidence_id=unit.evidence_id, span_start=0,
                    span_end=len(unit.text), role=role, fact_eligible=False,
                ))
    return ContextAuthorization(
        task_id=task.task_id, ir_identity=identity, context_policy_hash=policy,
        record_ids=list(dict.fromkeys(records)), fragments=fragments, bindings=bindings,
    )


def restore_authorized_context(
    task: RecognitionTask, base: TaskContext, *,
    authorization: ContextAuthorization, index: RecordIndex,
    expected_evidence_revision: int, expected_evidence_hash: str,
    expected_context_hash: str,
    target_seed: VerificationTarget, run_fingerprint: str,
    card: SchemaCard, profile: ExtractionProfile,
    entity_dependencies: list[EntityDependencyView], scope: TraversalScope,
    subject_node: GraphNode,
) -> TaskContext:
    """Rebuild from frozen sources and compare with the one confirmed protocol state."""
    try:
        context, computed_evidence_hash = build_authorized_context(
            task, base, authorization=authorization, index=index,
            evidence_revision=expected_evidence_revision,
            target_seed=target_seed, run_fingerprint=run_fingerprint,
            card=card, profile=profile, entity_dependencies=entity_dependencies,
            scope=scope, subject_node=subject_node,
        )
    except ValueError as error:
        raise ValueError("context_authorization_mismatch") from error
    _require_authorization(
        computed_evidence_hash == expected_evidence_hash
        and context.context_hash == expected_context_hash,
        "confirmed evidence/context hash",
    )
    return context


def replay_fragment(ir: DocumentIR, fragment: ContextFragment) -> str:
    value = ir.resolve(fragment.anchor)
    if value != fragment.text:
        raise ValueError("context fragment no longer matches source")
    return value


def build_model_context(
    task: RecognitionTask, context: TaskContext, card: SchemaCard, protocol: dict, *,
    verification_input: VerificationInput | None, confirmed_results: list[ToolObservation],
    turn: TurnPlan, scope: TraversalScope, source_catalog: list[SourceCatalogEntry],
    evidence_units: list[EvidenceUnit], feedback: list[ContextFeedback] | None = None,
) -> ModelContextView:
    """Derive a model view from already rendered, authorized sources; never grant access."""
    if (protocol.get("stage") != turn.stage or protocol.get("context_hash") != context.context_hash
            or protocol.get("scope_id") != scope.scope_id or task.task_id != context.target.task_id
            or card.subject_ref != VersionedRef(id=task.subject.entity_id,
                                               revision=task.subject.revision)):
        raise ValueError("model_context_identity_mismatch")
    if (turn.stage == "verification") != (verification_input is not None):
        raise ValueError("model_context_verification_input_required")
    if verification_input is not None and (
        verification_input.discovery_ref != protocol.get("discovery_ref")
        or any(target.scope != scope for target in verification_input.targets)
    ):
        raise ValueError("model_context_claim_scope_mismatch")
    # The renderer supplies table coordinates; source text/roles/permissions still
    # have to match every frozen fragment exactly, including required context.
    expected = {(fragment.anchor.evidence_id, fragment.anchor.span_start or 0,
                 (fragment.anchor.span_start or 0) + len(fragment.text),
                 fragment.text, fragment.fact_eligible) for fragment in context.fragments}
    actual = {(unit.evidence_id, unit.span_start, unit.span_end, unit.text, unit.fact_eligible)
              for unit in evidence_units}
    if expected != actual or context.omitted_refs or context.budget_status != "within_budget":
        raise ValueError("model_context_required_sources_missing")
    allowed_ids = {unit.evidence_id for unit in evidence_units}
    if any(not set(entry.authorized_evidence_ids) <= allowed_ids for entry in source_catalog):
        raise ValueError("model_context_catalog_permission_mismatch")
    if len({entry.record_id for entry in source_catalog}) != len(source_catalog):
        raise ValueError("model_context_duplicate_record")
    references = [card.subject_ref, *context.endpoint_evidence]
    if verification_input is not None:
        references.extend(verification_input.local_ref_map.values())
    unique_refs = {(ref.id, ref.revision): ref for ref in references}
    return ModelContextView(
        task_id=task.task_id, subject_ref=card.subject_ref, predicate_iri=task.predicate_iri,
        scope=scope, stage=turn.stage, context_hash=context.context_hash,
        evidence_hash=protocol["evidence_hash"], schema_card=card,
        source_catalog=source_catalog, evidence_units=evidence_units,
        registered_refs=[unique_refs[key] for key in sorted(unique_refs)],
        verification_input=verification_input, tool_observations=confirmed_results,
        feedback=list(feedback or []), turn=turn,
    )
