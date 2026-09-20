"""Rebuild a binding proof from exact saved stage results and frozen source inputs."""
from .claim_protocol import (
    ClaimCheckResult,
    FrozenClaimSet,
    VerifiedClaimSet,
    build_verification_input,
    ref_key,
)
from .record_discovery import resolve_claim_schema
from .reference_dependencies import ReferenceBindingEvidence, build_reference_binding_dependency
from .verification import ProofGate, build_generic_proof_menu, resolve_claim_subject


def restore_record_binding(binding_ref, *, origin, context, card, dependencies, nodes, load_result):
    frozen = FrozenClaimSet.model_validate(
        load_result(origin["lineage_id"], origin["discovery_ref"], "discovery"), strict=True,
    )
    verified = VerifiedClaimSet.model_validate(
        load_result(origin["lineage_id"], origin["verification_ref"], "verification"), strict=True,
    )
    if (context.target.model_dump(mode="json") != origin["target"]
            or verified.context_hash != context.context_hash):
        raise ValueError("reference_dependency_original_context_changed")
    # Reconstruct the confirmed generation's source version, not today's live
    # protocol head. A bounded feedback pass may have advanced this revision.
    context.protocol_state = {"evidence_revision": frozen.evidence_revision}
    verification = build_verification_input(
        frozen, discovery_ref=origin["discovery_ref"], context=context, card=card,
        scope=origin["task_scope"], entity_dependencies=dependencies,
        external_candidates=[], bridge_dependencies=[], scope_resolutions=[],
    )
    results = {result.target_id: result for result in verified.targets}
    if set(results) != {target.target_id for target in verification.targets}:
        raise ValueError("reference_dependency_original_targets_changed")
    proofs = {(node.entity_id, node.revision): node for node in nodes}
    binding = next((target for target in verification.targets
                    if target.target_kind == "reference_binding"
                    and target.claim_ref == binding_ref), None)
    if binding is None:
        raise ValueError("reference_dependency_binding_missing")
    source_ref = verification.local_ref_map[binding.payload.source_id]
    for dependency in dependencies:
        if dependency.grounding_kind == "document_root":
            proofs[ref_key(dependency.entity_ref)] = dependency
    for target in sorted(verification.targets, key=lambda t: t.target_kind != "entity"):
        if target.claim_ref not in (source_ref, binding_ref):
            continue
        result = results[target.target_id]
        if result.missing_facets or result.validation_issues:
            raise ValueError("reference_dependency_not_verified")
        owner_card = resolve_claim_schema(card, target.payload, frozen.local_ref_map,
                                         frozen.entities, dependencies)
        proof = ProofGate().evaluate_frozen_claim(
            target, result.decisions, entity_proofs=proofs,
            proof_menu=build_generic_proof_menu(
                owner_card, context, target.required_facets, reference_bindings=True,
                subject=resolve_claim_subject(target, verification, context),
            ),
            checks=ClaimCheckResult(), context=context, verification_input=verification,
        )
        if not proof.policy_eligible:
            raise ValueError("reference_dependency_not_verified")
        if target.target_kind == "entity":
            proofs[ref_key(target.claim_ref)] = proof
        else:
            evidence = ReferenceBindingEvidence(context, verification, proof)
            return build_reference_binding_dependency(evidence), evidence
    raise ValueError("reference_dependency_binding_missing")
