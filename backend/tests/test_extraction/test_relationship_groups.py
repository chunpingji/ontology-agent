"""Frozen Word statements preserve group selection and exact grounding."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from docx import Document

from app.services.extraction.evidence_identity import evidence_hash
from app.services.extraction.ontology_guided.claim_protocol import (
    ClaimCheckResult,
    EntityDependencyView,
    FrozenClaimSet,
    SchemaCard,
    VerificationEnvelope,
    build_verification_input,
    finalize_claims,
    validate_verification,
)
from app.services.extraction.ontology_guided.context import ContextFragment, TaskContext
from app.services.extraction.ontology_guided.contracts import (
    DocumentContext,
    SubjectRef,
    TraversalScope,
    VerificationTarget,
    VersionedRef,
)
from app.services.extraction.ontology_guided.mentions import MentionRegistry
from app.services.extraction.ontology_guided.records import RecordIndex
from app.services.extraction.word_analysis import analyze_word_core


@pytest.fixture
def graph_case(tmp_path):
    path = Path(__file__).resolve().parents[3] / 'specs/027-ontology-extraction-engine-v2'
    example = json.loads((path / 'contracts/examples.json').read_text())
    request = json.loads(
        example['verification_exchange']['request']['input'][0]['content'][0]['text']
    )
    doc = Document()
    doc.add_paragraph(request['evidence_units'][0]['text'])
    source = tmp_path / 'source.docx'
    doc.save(source)
    index = RecordIndex(analyze_word_core(source).ir)
    record = index.records[0]
    unit = record.source_units[0]
    anchor = index.ir.anchor(unit.evidence_id, 0, len(unit.text))
    card = SchemaCard.model_validate(request['schema_card'])
    root = card.subject_ref
    context_hash = evidence_hash('context')
    target = VerificationTarget.create(
        run_fingerprint='run', claim_ref=root, task_id='task', check_kind='predicate_entailment',
        document_context=DocumentContext(
            document_hash=index.ir.document_hash,
            document_class_iri=card.class_iris[0], root_ref=root,
        ),
        subject_ref=SubjectRef(entity_id=root.id, revision=root.revision,
                               class_iri=card.class_iris[0], is_document_root=True),
        ontology_hash='ontology', source_scope_hash='scope', context_hash=context_hash,
    )
    context = TaskContext(
        context_id='context', context_hash=context_hash, target=target, record_id=record.record_id,
        fragments=[ContextFragment(anchor=anchor, text=unit.text, purpose='target',
                                   fact_eligible=True)],
    )
    proposal = json.loads(json.dumps(example['discovery']).replace('ev-1', unit.evidence_id))
    mapping = {item['local_id']: VersionedRef(id=item['local_id'], revision=1)
               for key in ('entities', 'relations', 'properties', 'external_links')
               for item in proposal[key]}
    mapping[root.id] = root
    dependency = dict(entity_ref=root, class_iri=card.class_iris[0], grounding_kind='document_root',
                      root_origin='user_specified', proposal=None, source_refs=[anchor],
                      dependency_refs=[])
    dependency = EntityDependencyView(**dependency, content_hash=evidence_hash(dependency))
    return proposal, mapping, context, card, dependency, MentionRegistry(index.ir, index)


def run_case(case, *, fail_entity=False, external_candidates=()):
    proposal, mapping, context, card, dependency, registry = case
    values = dict(**proposal, assertion_generation=1, evidence_revision=1,
                  local_ref_map=mapping, claim_issues={})
    frozen = FrozenClaimSet(**values, content_hash=evidence_hash(values))
    scope = TraversalScope.create()
    verification = build_verification_input(
        frozen, discovery_ref='discovery', context=context, card=card, scope=scope,
        entity_dependencies=[dependency], external_candidates=list(external_candidates),
        bridge_dependencies=[],
        scope_resolutions=[],
    )
    answers = []
    for target in verification.targets:
        quote = (target.payload.mentions[0] if target.target_kind == 'entity' else
                 target.payload.identity_support[0] if target.target_kind == 'external_link'
                 else target.payload.bridge_support[0])
        answers.append(dict(
            target_id=target.target_id, content_hash=target.content_hash,
            facets=[dict(name=name, verdict=('unsupported' if fail_entity
                                             and target.payload.local_id == 'e1' else 'supported'),
                         support=[quote.model_dump()], counterevidence_support=[],
                         reason='原文核验')
                    for name in target.required_facets],
        ))
    checked = validate_verification(VerificationEnvelope.model_validate({'verifications': answers}),
                                    targets=verification.targets, context=context)
    result = finalize_claims(
        frozen, checked, scope=scope, context=context, card=card, verification_input=verification,
        registry=registry, deterministic_results={'r1': ClaimCheckResult(checks={
            'binding': True, 'relation_graph': True,
        })},
    )
    return result


def test_group_preserves_condition_exact_evidence_and_proof(graph_case):
    result = run_case(graph_case)
    assert result.complete
    assert len(result.nodes) == 2
    assert not result.edges
    assert len(result.relationship_groups) == 1
    group = result.relationship_groups[0]
    assert group.selection == 'one_of'
    assert len(group.object_refs) == 2
    assert group.conditions == ['在条件C下']
    assert group.condition_evidence_refs and group.selection_evidence_refs
    assert all(node.referent_ref and node.type_decision_ref for node in result.nodes)
    assert group.proof_ref.id == result.proof_payloads[-1]['proof_id']


@pytest.mark.parametrize('modality', ['required', 'possible', 'planned', 'unspecified'])
def test_assertion_modality_is_not_upgraded_to_asserted(graph_case, modality):
    graph_case[0]['relations'][0]['qualifiers']['modality'] = modality
    assert run_case(graph_case).relationship_groups[0].modality == modality


def test_negative_group_remains_a_supported_negative_statement(graph_case):
    graph_case[0]['relations'][0]['qualifiers']['polarity'] = 'negated'
    group = run_case(graph_case).relationship_groups[0]
    assert group.polarity == 'negated' and group.decision_status == 'supported'


def test_failed_member_never_turns_one_of_into_a_single_edge(graph_case):
    result = run_case(graph_case, fail_entity=True)
    assert not result.relationship_groups and not result.edges
    assert len(result.nodes) == 1
    assert not result.complete


def test_independent_verified_entities_survive_without_an_incoming_edge(graph_case):
    graph_case[0]['relations'] = []
    result = run_case(graph_case)
    assert len(result.nodes) == 2
    assert not result.edges and not result.relationship_groups


def test_single_object_is_an_edge_but_undetermined_group_is_not_accepted(graph_case):
    graph_case[0]['relations'][0]['object_ids'] = ['e1']
    graph_case[0]['relations'][0]['selection'] = 'all'
    result = run_case(graph_case)
    assert len(result.edges) == 1 and not result.relationship_groups
    graph_case[0]['relations'][0]['object_ids'] = ['e1', 'e2']
    graph_case[0]['relations'][0]['selection'] = 'undetermined'
    result = run_case(graph_case)
    assert not result.edges and not result.relationship_groups
    assert not result.complete
