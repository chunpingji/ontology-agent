"""An independent verifier cannot change a frozen claim or bypass a proof facet."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.extraction.evidence_identity import evidence_hash
from app.services.extraction.ontology_guided.claim_protocol import (
    ClaimCheckResult,
    SchemaCard,
    VerificationEnvelope,
    VerificationInput,
    validate_verification,
)
from app.services.extraction.ontology_guided.context import ContextFragment
from app.services.extraction.ontology_guided.contracts import (
    DocumentContext,
    SubjectRef,
    VerificationTarget,
)
from app.services.extraction.ontology_guided.verification import ProofGate, build_generic_proof_menu


@pytest.fixture
def case():
    path = Path(__file__).resolve().parents[3] / 'specs/027-ontology-extraction-engine-v2'
    example = json.loads((path / 'contracts/examples.json').read_text())
    exchange = example['verification_exchange']
    frozen = VerificationInput.model_validate(exchange['verification_input'])
    request = json.loads(exchange['request']['input'][0]['content'][0]['text'])
    anchor = frozen.entity_dependencies[0].source_refs[0]
    root = frozen.entity_dependencies[0]
    seed = VerificationTarget.create(
        run_fingerprint='run', claim_ref=root.entity_ref, task_id='task', check_kind='type',
        document_context=DocumentContext(
            document_hash=anchor.document_hash, document_class_iri=root.class_iri,
            root_ref=root.entity_ref,
        ),
        subject_ref=SubjectRef(entity_id=root.entity_ref.id, revision=root.entity_ref.revision,
                               class_iri=root.class_iri, is_document_root=True),
        ontology_hash='ontology', source_scope_hash='scope', context_hash='context',
    )
    context = SimpleNamespace(
        target=seed, counterevidence_refs=[], field_bindings=[], proof_dependencies=[],
        card=SchemaCard.model_validate(request['schema_card']),
        fragments=[ContextFragment(anchor=anchor, text=request['evidence_units'][0]['text'],
                                   purpose='target', fact_eligible=True)],
    )
    answers = []
    for target in frozen.targets:
        quote = (target.payload.mentions[0] if target.target_kind == 'entity'
                 else target.payload.bridge_support[0])
        answers.append({
            'target_id': target.target_id, 'content_hash': target.content_hash,
            'facets': [{'name': name, 'verdict': 'supported', 'support': [quote.model_dump()],
                        'counterevidence_support': [], 'reason': '原文支持该维度'}
                       for name in target.required_facets],
        })
    return frozen, context, {'verifications': answers}


def test_verifier_binds_every_target_facet_and_source(case):
    frozen, context, payload = case
    result = validate_verification(VerificationEnvelope.model_validate(payload),
                                  targets=frozen.targets, context=context)
    assert len(result.targets) == len(frozen.targets)
    assert result.context_hash == context.target.context_hash
    for target in result.targets:
        assert not target.missing_facets
        assert not target.validation_issues
        assert all(d.support_refs and d.verdict == 'supported' for d in target.decisions)


@pytest.mark.parametrize('mutation', ['omit_target', 'hash', 'omit_facet', 'extra_facet'])
def test_invalid_verification_cannot_be_treated_as_partial_success(case, mutation):
    frozen, context, payload = case
    answer = payload['verifications'][0]
    if mutation == 'omit_target':
        payload['verifications'].pop()
    elif mutation == 'hash':
        answer['content_hash'] = 'wrong'
    elif mutation == 'omit_facet':
        answer['facets'].pop()
    else:
        answer['facets'].append({**answer['facets'][0], 'name': 'value'})
    with pytest.raises(ValueError, match='verification_'):
        validate_verification(VerificationEnvelope.model_validate(payload),
                              targets=frozen.targets, context=context)


@pytest.mark.parametrize('mutation', ['missing', 'outside', 'ambiguous'])
def test_invalid_source_blocks_only_affected_target(case, mutation):
    frozen, context, payload = case
    facet = payload['verifications'][0]['facets'][0]
    if mutation == 'missing':
        facet['support'] = []
    elif mutation == 'outside':
        facet['support'][0]['evidence_id'] = 'unauthorized'
    elif mutation == 'ambiguous':
        context.fragments[0].text += context.fragments[0].text
    result = validate_verification(VerificationEnvelope.model_validate(payload),
                                  targets=frozen.targets, context=context)
    assert 'type' in result.targets[0].missing_facets
    assert result.targets[0].validation_issues
    if mutation in {'missing', 'outside'}:
        assert not result.targets[1].missing_facets


def test_supplemental_original_context_can_verify_but_not_rewrite_frozen_claim(case):
    frozen, context, payload = case
    context.fragments[0].fact_eligible = False
    before = frozen.model_dump(mode='json')
    result = validate_verification(VerificationEnvelope.model_validate(payload),
                                  targets=frozen.targets, context=context)
    assert all(not target.missing_facets for target in result.targets)
    assert frozen.model_dump(mode='json') == before


def test_supported_negative_assertion_is_not_a_rejected_positive(case):
    frozen, context, payload = case
    payload['verifications'][-1]['facets'][-1]['verdict'] = 'unsupported'
    result = validate_verification(VerificationEnvelope.model_validate(payload),
                                  targets=frozen.targets, context=context)
    assert result.targets[-1].decisions[-1].verdict == 'unsupported'
    assert result.targets[-1].missing_facets == [frozen.targets[-1].required_facets[-1]]


def test_known_counterevidence_cannot_disappear(case):
    frozen, context, payload = case
    counter = context.fragments[0].model_copy(deep=True)
    counter.anchor = counter.anchor.model_copy(update={'evidence_id': 'counter'})
    context.fragments.append(counter)
    context.counterevidence_refs = [counter.anchor]
    result = validate_verification(VerificationEnvelope.model_validate(payload),
                                  targets=frozen.targets, context=context)
    assert 'counterevidence_not_reviewed' in result.targets[-1].validation_issues


def gate_case(case, *, failed_entity=False, checks=None):
    frozen, context, payload = case
    if failed_entity:
        payload['verifications'][0]['facets'][0]['verdict'] = 'unsupported'
    verified = validate_verification(VerificationEnvelope.model_validate(payload),
                                    targets=frozen.targets, context=context)
    proofs = {(e.entity_ref.id, e.entity_ref.revision): e for e in frozen.entity_dependencies}
    bundles = []
    for claim, result in zip(frozen.targets, verified.targets, strict=True):
        menu = build_generic_proof_menu(context.card, context, claim.required_facets)
        bundle = ProofGate().evaluate_frozen_claim(
            claim, result.decisions, entity_proofs=proofs, proof_menu=menu,
            checks=ClaimCheckResult(checks=checks if checks is not None else {
                'binding': True, 'relation_graph': True,
            }),
            context=context, verification_input=frozen,
        )
        bundles.append(bundle)
        if claim.target_kind == 'entity':
            proofs[claim.claim_ref.id, claim.claim_ref.revision] = bundle
    return bundles


def test_generic_gate_preserves_group_and_does_not_use_legacy_policy(case, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('generic path selected a legacy predicate policy')

    monkeypatch.setattr('app.services.extraction.ontology_guided.verification.'
                        'PredicatePolicyRegistry.policy_for', forbidden)
    bundles = gate_case(case)
    assert all(bundle.policy_eligible for bundle in bundles), [b.validation_issues for b in bundles]
    assert len(bundles[-1].target.object_refs) == 2
    assert bundles[-1].target.object_ref is None
    assert bundles[-1].predicate_evidence.selection_support_refs


@pytest.mark.parametrize('quote_field', ['support', 'counterevidence_support'])
def test_whole_auxiliary_source_can_verify_relation_without_changing_frozen_context(
    case, quote_field,
):
    frozen, context, payload = case
    auxiliary = context.fragments[0].model_copy(deep=True)
    auxiliary.anchor = auxiliary.anchor.model_copy(update={
        'evidence_id': 'auxiliary', 'block_id': 'auxiliary-block',
        'span_start': None, 'span_end': None,
    })
    auxiliary.purpose = 'field_group_binding'
    auxiliary.fact_eligible = False
    context.fragments.append(auxiliary)
    for answer in payload['verifications']:
        for facet in answer['facets']:
            facet[quote_field] = [dict(evidence_id='auxiliary', text=auxiliary.text,
                                       context_text=None)]
    before = [fragment.model_dump() for fragment in context.fragments]
    before_target = context.target.model_dump()
    bundles = gate_case((frozen, context, payload))
    assert all(bundle.policy_eligible for bundle in bundles), [b.validation_issues for b in bundles]
    assert [fragment.model_dump() for fragment in context.fragments] == before
    assert context.target.model_dump() == before_target


@pytest.mark.parametrize('fragment_start', [None, 5])
@pytest.mark.parametrize('invalid', ['past_end', 'foreign_document', 'whole_source'])
def test_fragment_does_not_authorize_unknown_or_outside_proof(case, invalid, fragment_start):
    frozen, context, payload = case
    fragment = context.fragments[0]
    end = fragment_start + len(fragment.text) if fragment_start is not None else None
    fragment.anchor = fragment.anchor.model_copy(update={'span_start': fragment_start,
                                                        'span_end': end})
    verified = validate_verification(VerificationEnvelope.model_validate(payload),
                                    targets=frozen.targets, context=context)
    result = verified.targets[0]
    anchor = result.decisions[0].support_refs[0]
    changes = {
        'past_end': {'span_start': end or len(fragment.text),
                     'span_end': (end or len(fragment.text)) + 1},
        'foreign_document': {'document_hash': 'f' * 64},
        'whole_source': {'span_start': None, 'span_end': None},
    }[invalid]
    result.decisions[0].support_refs = [anchor.model_copy(update=changes)]
    claim = frozen.targets[0]
    bundle = ProofGate().evaluate_frozen_claim(
        claim, result.decisions, entity_proofs={},
        proof_menu=build_generic_proof_menu(context.card, context, claim.required_facets),
        checks=ClaimCheckResult(), context=context, verification_input=frozen,
    )
    assert not bundle.policy_eligible
    assert 'proof_source_outside_context' in bundle.validation_issues


def test_one_failed_entity_blocks_group_without_dropping_other_independent_entity(case):
    bundles = gate_case(case, failed_entity=True)
    assert not bundles[0].policy_eligible
    assert bundles[1].policy_eligible
    assert not bundles[-1].policy_eligible
    assert 'entity_dependency_not_verified' in bundles[-1].validation_issues
    assert len(bundles[-1].target.object_refs) == 2


def test_controller_binding_check_cannot_be_skipped(case):
    bundles = gate_case(case, checks={})
    assert bundles[0].policy_eligible
    assert not bundles[-1].policy_eligible
    assert 'binding_not_passed' in bundles[-1].validation_issues


def test_entity_proof_from_another_exact_revision_is_not_reused(case):
    frozen, context, payload = case
    root = frozen.entity_dependencies[0]
    frozen.entity_dependencies[0] = root.model_copy(update={
        'entity_ref': root.entity_ref.model_copy(update={'revision': 2}),
    })
    bundles = gate_case(case)
    assert not bundles[-1].policy_eligible
    assert 'entity_dependency_not_verified' in bundles[-1].validation_issues


@pytest.mark.parametrize("foreign", [False, True])
def test_bridge_cannot_prove_a_relation_with_unrelated_or_reversed_endpoints(case, foreign):
    from app.services.extraction.ontology_guided.claim_protocol import (
        BridgeDependencyView,
        VerificationInput,
        claim_content_hash,
    )
    from app.services.extraction.ontology_guided.contracts import VersionedRef

    frozen, context, answers = case
    claim = frozen.targets[-1]
    subject = frozen.local_ref_map[claim.payload.subject_id]
    obj = frozen.local_ref_map[claim.payload.object_ids[0]]
    value = dict(
        bridge_id="chain", bridge_ref=VersionedRef(id="chain", revision=1),
        bridge_kind="resolved_reference_chain", dependency_refs=[],
        steps=[dict(kind="predicate_assertion",
                    subject_ref=VersionedRef(id="foreign", revision=1) if foreign else obj,
                    object_ref=subject, predicate_iri=claim.payload.predicate_iri,
                    source_refs=[context.fragments[0].anchor])],
    )
    bridge = BridgeDependencyView(**value, content_hash=evidence_hash(value))
    if foreign:
        with pytest.raises(ValueError, match="bridge_endpoint_outside_entity_dependencies"):
            frozen.bridge_dependencies = [bridge]
        return
    frozen.bridge_dependencies = [bridge]
    claim.payload.bridge_kind = "resolved_reference_chain"
    claim.payload.bridge_ref_ids = [bridge.bridge_id]
    claim.dependency_refs.append(bridge.bridge_ref)
    claim.content_hash = claim_content_hash(
        claim.target_kind, claim.payload, claim.scope, claim.dependency_refs,
    )
    answers["verifications"][-1]["content_hash"] = claim.content_hash
    context.proof_dependencies = [bridge.bridge_ref]
    VerificationInput.model_validate(frozen.model_dump(mode="json"), strict=True)
    bundle = gate_case(case)[-1]
    assert not bundle.policy_eligible
    assert "bridge_endpoint_chain_not_closed" in bundle.validation_issues
