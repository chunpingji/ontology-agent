"""Earlier identity proof may bind a current role, never prove its relationship."""

from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from app.schemas.evidence import EvidenceAnchor
from app.services.extraction.evidence_identity import evidence_hash, stable_id
from app.services.extraction.ontology_guided.claim_protocol import (
    ClaimCheckResult,
    EntityDependencyView,
    EntityProposal,
    Quote,
    ReferenceBindingProposal,
    VerificationInput,
    VerificationScopeResolution,
    VerificationScopeStep,
    VerificationTargetSpec,
    claim_content_hash,
)
from app.services.extraction.ontology_guided.context import ContextFragment, TaskContext
from app.services.extraction.ontology_guided.contracts import (
    DocumentContext,
    GraphNode,
    ScopeMember,
    SemanticDecision,
    SubjectRef,
    TraversalScope,
    VerificationTarget,
    VersionedRef,
)
from app.services.extraction.ontology_guided.record_discovery import RecordDiscoveryTarget
from app.services.extraction.ontology_guided.reference_dependencies import (
    ReferenceBindingDependencyView,
    ReferenceBindingEvidence,
    build_reference_binding_dependency,
    validate_reference_dependency,
)
from app.services.extraction.ontology_guided.source_assertions import validate_source_assertion
from app.services.extraction.ontology_guided.source_citations import resolve_fragment_quote
from app.services.extraction.ontology_guided.verification import (
    ProofGate,
    build_generic_proof_menu,
    resolve_claim_subject,
)


def _ref(identity):
    return VersionedRef(id=identity, revision=1)


def _scope():
    return TraversalScope.create([
        ScopeMember(relation_ref=_ref("choice-group"), member_ref=_ref("device")),
    ])


def _case(scope=None, *, record_origin=False):
    scope = scope or TraversalScope.create()
    texts = {"first": "装置甲已登记。", "next": "该装置包含零件乙。"}
    fragments = [ContextFragment(
        anchor=EvidenceAnchor(
            document_hash="a" * 64, parser_version="parser", structure_hash="b" * 64,
            evidence_id=identity, section_node_id="section", block_id=identity,
            span_start=0, span_end=len(text),
        ), text=text, purpose="target", fact_eligible=True,
    ) for identity, text in texts.items()]

    def quote(identity, text):
        return Quote(evidence_id=identity, text=text, context_text=None)

    def anchor(identity, text):
        return resolve_fragment_quote(identity, text, fragments)[0]

    def entity(identity, where, text):
        return EntityProposal(
            local_id=identity, class_iri="urn:Object", representation="mention",
            mentions=[quote(where, text)], record_components=[], identifier_claims=[],
        )

    def dependency(identity, where, text):
        payload = dict(
            entity_ref=_ref(identity), class_iri="urn:Object", grounding_kind="mention",
            root_origin=None, proposal=entity(identity, where, text),
            source_refs=[anchor(where, text)], dependency_refs=[_ref(identity + "-type")],
        )
        return EntityDependencyView(**payload, content_hash=evidence_hash(payload))

    def frozen(kind, payload, dependencies, facets):
        content = claim_content_hash(kind, payload, scope, dependencies)
        return VerificationTargetSpec(
            target_id=evidence_hash([kind, content]), target_kind=kind,
            claim_ref=_ref(payload.local_id), content_hash=content,
            required_facets=facets, payload=payload, scope=scope, dependency_refs=dependencies,
        )

    device = dependency("device", "first", "装置甲")
    part = dependency("part", "next", "零件乙")
    scope_refs = [ref for member in scope.members
                  for ref in (member.relation_ref, member.member_ref)]
    source = frozen("entity", entity("pronoun", "next", "该装置"), scope_refs,
                    ["type", "referent", "subject_role"])
    binding = frozen("reference_binding", ReferenceBindingProposal(
        local_id="binding", source_id="pronoun", target_id="device", binding_kind="anaphora",
        support=[quote("first", "装置甲"), quote("next", "该装置")],
    ), [source.claim_ref, device.entity_ref, *scope_refs],
        ["reference_identity", "reference_scope", "counterevidence"])
    origin_target = VerificationTarget.create(
        run_fingerprint="run", claim_ref=_ref("original-task"), task_id="original-task",
        check_kind="predicate_entailment",
        document_context=DocumentContext(
            document_hash="a" * 64, document_class_iri="urn:Document", root_ref=_ref("root"),
        ), subject_ref=SubjectRef(entity_id="device", revision=1, class_iri="urn:Object"),
        ontology_hash="ontology", source_scope_hash="original-permission",
        context_hash="c" * 64,
    )
    original_target = origin_target
    if record_origin:
        original_target = RecordDiscoveryTarget(
            target_id=origin_target.target_id, task_id=origin_target.task_id,
            document_context=origin_target.document_context, schema_card_id="card",
            analysis_scope_ref="analysis-scope", ontology_hash=origin_target.ontology_hash,
            source_scope_hash=origin_target.source_scope_hash,
            context_hash=origin_target.context_hash,
        )
    original = TaskContext(context_id="original", context_hash="c" * 64, target=original_target,
                           record_id="next", fragments=fragments)
    verification = VerificationInput(
        discovery_ref="saved-discovery", targets=[source, binding],
        local_ref_map={"device": device.entity_ref, "pronoun": source.claim_ref,
                       "binding": binding.claim_ref},
        entity_dependencies=[device], external_candidates=[], bridge_dependencies=[],
        scope_resolutions=[VerificationScopeResolution(scope_id=scope.scope_id, steps=[
            VerificationScopeStep(
                relation_ref=member.relation_ref, member_ref=member.member_ref,
                selection="one_of", polarity="affirmed", modality="asserted",
                conditions=[], applicability=[], evidence_refs=[fragments[0].anchor],
            ) for member in scope.members
        ])] if scope.members else [],
    )

    def decisions(target):
        return [SemanticDecision(
            decision_id=stable_id("decision", [target.target_id, facet]),
            target_id=target.target_id, check_kind=facet, verdict="supported",
            reason_code="verified", reason="独立核验原文支持此维度。",
            support_refs=[fragment.anchor for fragment in fragments],
            verifier_version="test", attempt_id="attempt",
        ) for facet in target.required_facets]

    card = NS(schema_card_id="card", quantity_policies=[], identity_keys=[],
              unsupported_constraints=[])
    source_proof = ProofGate().evaluate_frozen_claim(
        source, decisions(source), entity_proofs={},
        proof_menu=build_generic_proof_menu(
            card, original, source.required_facets,
            subject=resolve_claim_subject(source, verification, original),
        ),
        checks=ClaimCheckResult(), context=original, verification_input=verification,
    )
    prior_node = GraphNode(
        entity_id="device", revision=1, class_iri="urn:Object", class_label="对象", label="装置甲",
        referent_ref=_ref("referent-device"), type_decision_ref=_ref("device-type"),
        referent_decision_ref=_ref("device-referent"),
        dependency_refs=[_ref("device-type"), _ref("device-referent")],
    )
    proof = ProofGate().evaluate_frozen_claim(
        binding, decisions(binding), entity_proofs={
            (source.claim_ref.id, 1): source_proof, (device.entity_ref.id, 1): prior_node,
        }, proof_menu=build_generic_proof_menu(
            card, original, binding.required_facets,
            subject=resolve_claim_subject(binding, verification, original),
        ),
        checks=ClaimCheckResult(), context=original, verification_input=verification,
    )
    assert proof.policy_eligible, proof.validation_issues
    evidence = ReferenceBindingEvidence(original, verification, proof)
    view = build_reference_binding_dependency(evidence)
    current = original.model_copy(update={
        "context_id": "current", "context_hash": "d" * 64,
        "target": origin_target.model_copy(update={
            "task_id": "relationship-task", "context_hash": "d" * 64,
            "source_scope_hash": "current-permission",
        }),
    })
    assertion = NS(
        subject_support=[quote("next", "该装置")],
        object_support=[NS(object_id="part", support=[quote("next", "零件乙")])],
        predicate_support=[quote("next", texts["next"])],
        binding_ids=[], binding_dependency_refs=[view.binding_ref],
    )
    relation = NS(
        payload=NS(subject_id="device", object_ids=["part"],
                   bridge_kind="resolved_reference_chain", predicate_iri="urn:contains",
                   source_assertion=assertion),
        scope=scope, dependency_refs=[*view.dependency_refs, part.entity_ref],
    )
    current_input = NS(
        targets=[], local_ref_map={"device": device.entity_ref, "part": part.entity_ref},
        entity_dependencies=[device, part],
    )
    current_decisions = [NS(
        decision_id="current-" + name, check_kind=name, verdict="supported",
        support_refs=[fragments[1].anchor],
    ) for name in ("subject_binding", "object_binding", "predicate")]
    return NS(evidence=evidence, view=view, current=current, current_input=current_input,
              relation=relation, decisions=current_decisions, anchor=anchor, quote=quote)


def _consume(case, **overrides):
    args = dict(evidence=case.evidence, current_context=case.current,
                current_scope=case.relation.scope,
                current_entities=case.current_input.entity_dependencies,
                is_valid_reference=lambda ref: True)
    args.update(overrides)
    return validate_reference_dependency(case.view, **args)


def _relation(case, **overrides):
    args = dict(
        context=case.current, verification_input=case.current_input, decisions=case.decisions,
        reference_dependencies=[case.view],
        reference_evidence={
            (case.view.binding_ref.id, case.view.binding_ref.revision): case.evidence,
        },
        reference_is_valid=lambda ref: True,
    )
    args.update(overrides)
    return validate_source_assertion(case.relation, **args)


def test_prior_binding_reauthorizes_source_under_different_context_and_permission():
    case = _case()
    resolved = _consume(case)
    assert resolved.source_signatures == ((case.anchor("next", "该装置"),),)
    assert resolved.entity_ref == _ref("device")
    assert resolved.step.input_refs == [_ref("pronoun"), _ref("device")]
    result = _relation(case)
    assert result.issues == []
    assert [step.step_kind for step in result.steps] == ["same_referent", "predicate_assertion"]


def test_record_origin_binding_is_consumed_by_a_separate_predicate_task():
    case = _case(record_origin=True)
    assert isinstance(case.evidence.original_context.target, RecordDiscoveryTarget)
    assert isinstance(case.current.target, VerificationTarget)
    assert _consume(case).entity_ref == _ref("device")
    assert _relation(case).issues == []


def test_serialized_matching_hash_alone_never_authorizes_a_reference():
    case = _case()
    result = _relation(case, reference_evidence={})
    assert "reference_dependency_not_authorized" in result.issues
    assert "source_assertion_subject_endpoint_unbound" in result.issues
    assert not result.steps


@pytest.mark.parametrize("facet", ["subject_binding", "object_binding", "predicate"])
def test_old_binding_does_not_replace_current_independent_relation_facets(facet):
    case = _case()
    decision = next(decision for decision in case.decisions if decision.check_kind == facet)
    decision.verdict = "unsupported"
    result = _relation(case)
    assert result.issues
    assert not result.steps


def test_same_name_at_unbound_source_does_not_become_a_relation_role():
    case = _case()
    case.relation.payload.source_assertion.subject_support = [case.quote("next", "零件乙")]
    assert "source_assertion_subject_endpoint_unbound" in _relation(case).issues


@pytest.mark.parametrize("change", ["rejected", "missing_facet", "wrong_source_scope", "wrong_hash",
                                    "wrong_target", "empty_support", "stale_proof_ref"])
def test_real_original_proof_is_required_even_if_view_content_is_unchanged(change):
    case = _case()
    proof = case.evidence.proof.model_copy(deep=True)
    if change == "rejected":
        proof.independent_review = "rejected"
    elif change == "missing_facet":
        proof.decisions.pop()
    elif change == "wrong_source_scope":
        proof.target.source_scope_hash = "foreign-source-scope"
    elif change == "wrong_hash":
        proof.target.literal_hash = "different-claim"
    elif change == "wrong_target":
        proof.target.claim_ref = _ref("another-binding")
    elif change == "empty_support":
        proof.decisions[0] = proof.decisions[0].model_copy(update={"support_refs": []})
    else:
        proof.bundle_id = "another-proof"
    with pytest.raises(ValueError):
        _consume(case, evidence=replace(case.evidence, proof=proof))


def test_recomputed_view_hash_cannot_change_the_proven_source_mention():
    case = _case()
    payload = case.view.model_dump(mode="json", exclude={"content_hash"})
    payload["source_refs"] = [case.anchor("next", "零件乙").model_dump(mode="json")]
    forged = ReferenceBindingDependencyView(**payload, content_hash=evidence_hash(payload))
    case.view = forged
    with pytest.raises(ValueError, match="reference_dependency_content_mismatch"):
        _consume(case)


@pytest.mark.parametrize("which", ["first", "next"])
def test_both_source_and_destination_original_text_need_current_authorization(which):
    case = _case()
    case.current.fragments = [fragment for fragment in case.current.fragments
                              if fragment.anchor.evidence_id != which]
    with pytest.raises(ValueError):
        _consume(case)


def test_current_text_cannot_reinterpret_the_same_physical_anchor():
    case = _case()
    case.current = case.current.model_copy(deep=True)
    case.current.fragments[0].text = "装置乙已登记。"
    with pytest.raises(ValueError):
        _consume(case)


@pytest.mark.parametrize("change", ["document", "run_root", "ontology", "revision", "type"])
def test_binding_cannot_cross_document_run_ontology_or_endpoint_identity(change):
    case = _case()
    case.current = case.current.model_copy(deep=True)
    if change == "document":
        case.current.target.document_context.document_hash = "e" * 64
    elif change == "run_root":
        case.current.target.document_context.root_ref = _ref("another-run-root")
    elif change == "ontology":
        case.current.target.ontology_hash = "another-ontology"
    else:
        entity = case.current_input.entity_dependencies[0]
        payload = entity.model_dump(mode="json", exclude={"content_hash"})
        if change == "revision":
            payload["entity_ref"]["revision"] = 2
        else:
            payload["class_iri"] = payload["proposal"]["class_iri"] = "urn:OtherType"
        case.current_input.entity_dependencies[0] = EntityDependencyView(
            **payload, content_hash=evidence_hash(payload),
        )
    with pytest.raises(ValueError):
        _consume(case)


def test_every_exact_dependency_remains_subject_to_invalidation():
    case = _case()
    for invalid in case.view.dependency_refs:
        with pytest.raises(ValueError, match="reference_dependency_invalidated"):
            _consume(case, is_valid_reference=lambda ref: ref != invalid)


def test_old_scope_can_be_narrowed_but_never_promoted_to_unconditional_scope():
    case = _case()
    assert _consume(case, current_scope=_scope()).entity_ref == _ref("device")
    scoped = _case(_scope())
    with pytest.raises(ValueError, match="reference_dependency_scope_mismatch"):
        _consume(scoped, current_scope=TraversalScope.create())


def test_binding_proof_must_be_in_current_frozen_claim_dependency_closure():
    case = _case()
    case.relation.dependency_refs.remove(case.view.proof_ref)
    result = _relation(case)
    assert "reference_dependency_reference_closure" in result.issues
    assert not result.steps


def test_original_binding_flags_do_not_override_missing_antecedent_support():
    case = _case()
    proof = case.evidence.proof.model_copy(deep=True)
    proof.decisions = [decision.model_copy(update={
        "support_refs": [case.anchor("next", "该装置")],
    }) for decision in proof.decisions]
    binding = case.evidence.verification_input.targets[-1]
    proof.bundle_id = stable_id("claim-bundle", [
        binding.target_id, binding.content_hash, case.evidence.original_context.target.context_hash,
        proof.decisions, ClaimCheckResult(),
    ])
    with pytest.raises(ValueError, match="reference_dependency_source_not_verified"):
        _consume(case, evidence=replace(case.evidence, proof=proof))
