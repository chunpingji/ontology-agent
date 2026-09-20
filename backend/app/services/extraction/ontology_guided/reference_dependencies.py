"""Consume previously verified local bindings without treating their hashes as proof.

The coordinator loads ``ReferenceBindingEvidence`` from saved model results. It
is never model input or a second persisted copy of those results. The small view
only advertises which exact binding a new statement may use.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import Field, model_validator

from app.schemas.evidence import EvidenceAnchor, EvidenceModel
from app.services.extraction.evidence_identity import evidence_hash, stable_id
from app.services.extraction.ontology_guided.contracts import (
    BridgeStep,
    DocumentContext,
    TraversalScope,
    VerificationBundle,
    VersionedRef,
)

if TYPE_CHECKING:
    from app.services.extraction.ontology_guided.claim_protocol import (
        EntityDependencyView,
        VerificationInput,
    )
    from app.services.extraction.ontology_guided.context import TaskContext


class ReferenceBindingDependencyView(EvidenceModel):
    binding_ref: VersionedRef
    proof_ref: VersionedRef
    source_ref: VersionedRef
    entity_ref: VersionedRef
    source_refs: list[EvidenceAnchor] = Field(min_length=1)
    document_context: DocumentContext
    scope: TraversalScope
    dependency_refs: list[VersionedRef] = Field(min_length=1)
    content_hash: str

    @model_validator(mode="after")
    def exact_content(self):
        if self.source_ref == self.entity_ref:
            raise ValueError("reference_dependency_same_endpoint")
        keys = [(ref.id, ref.revision) for ref in self.dependency_refs]
        if len(keys) != len(set(keys)) or any(
            (ref.id, ref.revision) not in keys
            for ref in (self.binding_ref, self.proof_ref, self.source_ref, self.entity_ref)
        ):
            raise ValueError("reference_dependency_reference_closure")
        if self.content_hash != evidence_hash(
            self.model_dump(mode="json", exclude={"content_hash"})
        ):
            raise ValueError("reference_dependency_hash_mismatch")
        return self


@dataclass(frozen=True)
class ReferenceBindingEvidence:
    original_context: TaskContext
    verification_input: VerificationInput
    proof: VerificationBundle


@dataclass(frozen=True)
class ResolvedReferenceBinding:
    entity_ref: VersionedRef
    source_signatures: tuple[tuple[EvidenceAnchor, ...], ...]
    step: BridgeStep
    dependency_refs: tuple[VersionedRef, ...]


def _key(ref):
    return ref.id, ref.revision


def _quotes(proposal):
    return proposal.mentions if proposal.representation == "mention" else [
        component.quote for component in proposal.record_components
    ]


def _resolve(quote, context):
    from app.services.extraction.ontology_guided.source_citations import resolve_fragment_quote

    return resolve_fragment_quote(
        quote.evidence_id, quote.text, context.fragments, context_text=quote.context_text,
    )[0]


def _anchor_text(anchor, context):
    from app.services.extraction.ontology_guided.claim_protocol import _anchor_covers

    values = {
        fragment.text[
            anchor.span_start - fragment.bounded_anchor().span_start:
            anchor.span_end - fragment.bounded_anchor().span_start
        ]
        for fragment in context.fragments
        if _anchor_covers(fragment.bounded_anchor(), anchor)
    }
    if len(values) != 1 or not next(iter(values)):
        raise ValueError("reference_dependency_source_outside_context")
    return next(iter(values))


def _original(evidence):
    from app.services.extraction.ontology_guided.claim_protocol import (
        ClaimCheckResult,
        VerificationInput,
        _anchor_covers,
    )
    from app.services.extraction.ontology_guided.source_assertions import (
        validate_reference_binding,
    )

    if not isinstance(evidence, ReferenceBindingEvidence):
        raise ValueError("reference_dependency_evidence_missing")
    verification = VerificationInput.model_validate(
        evidence.verification_input.model_dump(mode="json"), strict=True,
    )
    proof = VerificationBundle.model_validate(evidence.proof.model_dump(mode="json"), strict=True)
    context = evidence.original_context
    binding = next((target for target in verification.targets
                    if target.claim_ref == proof.target.claim_ref), None)
    if binding is None or binding.target_kind != "reference_binding":
        raise ValueError("reference_dependency_binding_missing")
    expected_facets = {"reference_identity", "reference_scope", "counterevidence"}
    if (set(binding.required_facets) != expected_facets
            or len(proof.decisions) != len(expected_facets)
            or {decision.check_kind for decision in proof.decisions} != expected_facets
            or len({decision.decision_id for decision in proof.decisions}) != len(expected_facets)
            or not proof.policy_eligible or not proof.structural_valid or not proof.model_supported
            or proof.independent_review == "rejected" or proof.validation_issues):
        raise ValueError("reference_dependency_not_verified")
    origin = context.target
    if (proof.target.target_id != binding.target_id
            or proof.target.literal_hash != binding.content_hash
            or proof.target.context_hash != context.context_hash
            or proof.target.context_hash != origin.context_hash
            or proof.target.source_scope_hash != origin.source_scope_hash
            or proof.target.document_context != origin.document_context
            or proof.target.ontology_hash != origin.ontology_hash
            or proof.target.task_id != origin.task_id):
        raise ValueError("reference_dependency_original_target_mismatch")
    if proof.bundle_id != stable_id("claim-bundle", [
        binding.target_id, binding.content_hash, origin.context_hash,
        proof.decisions, ClaimCheckResult(),
    ]):
        raise ValueError("reference_dependency_proof_mismatch")
    for decision in proof.decisions:
        if (decision.target_id != binding.target_id or decision.verdict != "supported"
                or decision.model_refusal or not decision.support_refs):
            raise ValueError("reference_dependency_not_verified")
        for anchor in [*decision.support_refs, *decision.counterevidence_refs]:
            _anchor_text(anchor, context)
    counter = next(d for d in proof.decisions if d.check_kind == "counterevidence")
    if any(not any(_anchor_covers(anchor, expected)
                   for anchor in [*counter.support_refs, *counter.counterevidence_refs])
           for expected in context.counterevidence_refs):
        raise ValueError("reference_dependency_counterevidence_missing")
    source_ref = verification.local_ref_map[binding.payload.source_id]
    entity_ref = verification.local_ref_map[binding.payload.target_id]
    source = next((target for target in verification.targets
                   if target.target_kind == "entity" and target.claim_ref == source_ref), None)
    entity = next((item for item in verification.entity_dependencies
                   if item.entity_ref == entity_ref), None)
    if (source is None or entity is None or entity.grounding_kind == "document_root"
            or source_ref == entity_ref or entity.proposal is None
            or source.payload.class_iri != entity.class_iri
            or (proof.target.subject_ref.entity_id, proof.target.subject_ref.revision)
            != _key(source_ref)
            or proof.target.subject_ref.class_iri != source.payload.class_iri
            or proof.target.subject_ref.is_document_root):
        raise ValueError("reference_dependency_endpoint_mismatch")
    issues = validate_reference_binding(
        binding, context=context, verification_input=verification, decisions=proof.decisions,
    )
    if issues:
        raise ValueError("reference_dependency_source_not_verified")
    return binding, source, entity, proof


def build_reference_binding_dependency(
    evidence: ReferenceBindingEvidence,
) -> ReferenceBindingDependencyView:
    """Build an authorization view only from a saved, complete binding proof."""
    binding, source, entity, proof = _original(evidence)
    proof_ref = VersionedRef(id=proof.bundle_id, revision=1)
    refs = [binding.claim_ref, proof_ref, source.claim_ref, entity.entity_ref,
            *binding.dependency_refs, *source.dependency_refs, *entity.dependency_refs,
            *(VersionedRef(id=decision.decision_id, revision=1) for decision in proof.decisions)]
    payload = {
        "binding_ref": binding.claim_ref, "proof_ref": proof_ref,
        "source_ref": source.claim_ref, "entity_ref": entity.entity_ref,
        "source_refs": [_resolve(quote, evidence.original_context)
                        for quote in _quotes(source.payload)],
        "document_context": proof.target.document_context, "scope": binding.scope,
        "dependency_refs": list({_key(ref): ref for ref in refs}.values()),
    }
    return ReferenceBindingDependencyView(**payload, content_hash=evidence_hash(payload))


def validate_reference_dependency(
    view: ReferenceBindingDependencyView, *, evidence: ReferenceBindingEvidence,
    current_context: TaskContext, current_scope: TraversalScope,
    current_entities: Sequence[EntityDependencyView],
    is_valid_reference: Callable[[VersionedRef], bool],
) -> ResolvedReferenceBinding:
    """Reauthorize an old local binding; its predicate is never reused.

    ``evidence`` and ``is_valid_reference`` are coordinator inputs. A serialized
    view, even one with a matching content hash, is insufficient to authorize it.
    Original scope may be narrowed but cannot be promoted to unconditional scope.
    """
    from app.services.extraction.ontology_guided.claim_protocol import EntityDependencyView

    view = ReferenceBindingDependencyView.model_validate(view.model_dump(mode="json"), strict=True)
    expected = build_reference_binding_dependency(evidence)
    if view != expected:
        raise ValueError("reference_dependency_content_mismatch")
    binding, source, entity, proof = _original(evidence)
    current_scope = TraversalScope.model_validate(
        current_scope.model_dump(mode="json"), strict=True,
    )
    origin_members = {(_key(item.relation_ref), _key(item.member_ref))
                      for item in view.scope.members}
    current_members = {(_key(item.relation_ref), _key(item.member_ref))
                       for item in current_scope.members}
    if not origin_members <= current_members:
        raise ValueError("reference_dependency_scope_mismatch")
    if (view.document_context != current_context.target.document_context
            or proof.target.ontology_hash != current_context.target.ontology_hash):
        raise ValueError("reference_dependency_document_mismatch")
    current = [item for item in current_entities if item.entity_ref == view.entity_ref]
    if len(current) != 1:
        raise ValueError("reference_dependency_entity_unavailable")
    current_entity = EntityDependencyView.model_validate(current[0].model_dump(mode="json"),
                                                        strict=True)
    if (current_entity.grounding_kind == "document_root"
            or current_entity.proposal != entity.proposal
            or current_entity.class_iri != entity.class_iri):
        raise ValueError("reference_dependency_endpoint_mismatch")
    if not callable(is_valid_reference) or any(
        not is_valid_reference(ref)
        for ref in [*view.dependency_refs, *current_entity.dependency_refs]
    ):
        raise ValueError("reference_dependency_invalidated")
    quotes = [*binding.payload.support, *_quotes(source.payload), *_quotes(entity.proposal)]
    for quote in quotes:
        if _resolve(quote, evidence.original_context) != _resolve(quote, current_context):
            raise ValueError("reference_dependency_source_changed")
    sources = [_resolve(quote, evidence.original_context) for quote in quotes]
    sources.extend(anchor for decision in proof.decisions
                   for anchor in [*decision.support_refs, *decision.counterevidence_refs])
    for anchor in sources:
        if _anchor_text(anchor, evidence.original_context) != _anchor_text(anchor, current_context):
            raise ValueError("reference_dependency_source_changed")
    proposal = source.payload
    if proposal.representation == "mention":
        signatures = tuple((_resolve(quote, current_context),) for quote in proposal.mentions)
    else:
        role_quotes = [component.quote for component in proposal.record_components
                       if component.role == "subject"] or _quotes(proposal)
        signatures = (tuple(_resolve(quote, current_context) for quote in role_quotes),)
    identity = next(d for d in proof.decisions if d.check_kind == "reference_identity")
    return ResolvedReferenceBinding(
        entity_ref=view.entity_ref, source_signatures=signatures,
        dependency_refs=tuple(view.dependency_refs),
        step=BridgeStep(
            step_kind="same_referent", input_refs=[view.source_ref, view.entity_ref],
            output_role="referent_binding",
            source_refs=[_resolve(quote, current_context) for quote in binding.payload.support],
            decision_ref=VersionedRef(id=identity.decision_id, revision=1),
        ),
    )
