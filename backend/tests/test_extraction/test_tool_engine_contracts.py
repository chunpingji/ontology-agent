"""Verification binds complete claims; model state cannot replace frozen content."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.services.extraction.evidence_identity import evidence_hash
from app.services.extraction.ontology_guided.claim_protocol import (
    DiscoveryEnvelope,
    ExtractionProfile,
    FrozenClaimSet,
    PropertyProposal,
    SchemaCard,
    VerificationEnvelope,
    VerificationInput,
    build_verification_input,
    claim_content_hash,
    compile_schema_card,
    compile_stage_schema,
)
from app.services.extraction.ontology_guided.context import ContextFragment
from app.services.extraction.ontology_guided.contracts import (
    EdgeSpec,
    LocalMenu,
    SlotSpec,
    SubjectRef,
    TraversalScope,
)

SPEC = Path(__file__).resolve().parents[3] / 'specs/027-ontology-extraction-engine-v2'


@pytest.fixture
def examples():
    return json.loads((SPEC / 'contracts/examples.json').read_text())


def test_discovery_and_full_verification_examples(examples):
    DiscoveryEnvelope.model_validate(examples['discovery'], strict=True)
    value = VerificationInput.model_validate(
        examples['verification_exchange']['verification_input'], strict=True
    )
    assert len(value.targets) == 3
    assert len(value.entity_dependencies) == 1


@pytest.mark.parametrize('mutation', ['missing', 'extra', 'type', 'duplicate', 'object_collision'])
def test_discovery_rejects_invalid_model_payload(examples, mutation):
    value = copy.deepcopy(examples['discovery'])
    if mutation == 'missing':
        value['entities'][0]['mentions'][0].pop('context_text')
    elif mutation == 'extra':
        value['entities'][0]['accepted'] = True
    elif mutation == 'type':
        value['entities'][0]['local_id'] = 1
    elif mutation == 'duplicate':
        value['entities'][1]['local_id'] = value['entities'][0]['local_id']
    else:
        value['relations'][0]['object_ids'] *= 2
    with pytest.raises(ValidationError):
        DiscoveryEnvelope.model_validate(value, strict=True)


@pytest.mark.parametrize('mutation', ['payload', 'hash', 'entity_ref', 'duplicate_dependency'])
def test_verification_input_rejects_unbound_content(examples, mutation):
    value = copy.deepcopy(examples['verification_exchange']['verification_input'])
    if mutation == 'payload':
        value['targets'][-1].pop('payload')
    elif mutation == 'hash':
        value['targets'][-1]['payload']['selection'] = 'all'
    elif mutation == 'entity_ref':
        value['local_ref_map']['s']['revision'] += 1
    else:
        value['entity_dependencies'].append(copy.deepcopy(value['entity_dependencies'][0]))
    with pytest.raises(ValidationError):
        VerificationInput.model_validate(value, strict=True)


def test_separate_unit_is_semantic_but_extra_same_unit_proof_is_not(examples):
    quote = {'evidence_id': 'ev-1', 'text': '5', 'context_text': None}
    payload = PropertyProposal(
        local_id='p1', subject_id='s', predicate_iri='urn:example:quantity',
        value_quote=quote, field_support=[], unit_support=[{**quote, 'text': 'mg'}],
        qualifiers={'polarity': 'affirmed', 'modality': 'asserted',
                    'condition_support': [], 'scope_qualifiers': []},
        bridge_kind='owned_field_group', bridge_ref_ids=[],
    )
    target = VerificationInput.model_validate(
        examples['verification_exchange']['verification_input']
    ).targets[-1]

    def digest(item):
        return claim_content_hash('property', item, target.scope, target.dependency_refs)

    original = digest(payload)
    changed = payload.model_copy(deep=True)
    changed.unit_support[0].text = 'g'
    assert digest(changed) != original
    extra = payload.model_copy(deep=True)
    extra.unit_support.append(extra.unit_support[0].model_copy(update={'evidence_id': 'ev-2'}))
    assert digest(extra) == original


def expanded_schema(value, root):
    if isinstance(value, list):
        return [expanded_schema(item, root) for item in value]
    if not isinstance(value, dict):
        return value
    if '$ref' in value:
        return expanded_schema(root['$defs'][value['$ref'].rsplit('/', 1)[-1]], root)
    return {
        key: sorted(item) if key in {'enum', 'required'} else expanded_schema(item, root)
        for key, item in value.items() if key not in {'title', 'description', '$defs'}
    }


@pytest.mark.parametrize(('stage', 'model'), [
    ('discovery', DiscoveryEnvelope), ('verification', VerificationEnvelope),
])
def test_stage_schema_generated_from_types_matches_spec(stage, model):
    document = json.loads((SPEC / 'contracts/stage-schemas.json').read_text())[stage]
    generated = model.model_json_schema()
    assert expanded_schema(generated, generated) == expanded_schema(document, document)


def make_menu(prefix):
    return LocalMenu(
        menu_id='menu', ontology_snapshot_id='snapshot',
        subject=SubjectRef(entity_id='s', revision=1, class_iri=prefix + 'Source'),
        properties=[SlotSpec(iri=prefix + 'amount', label='数量',
                             datatype_iris=['http://www.w3.org/2001/XMLSchema#decimal'])],
        relationships=[EdgeSpec(iri=prefix + 'links', label='关系',
                                range_class_iris=[prefix + 'Target'])],
    )


def test_card_narrowing_and_iri_renaming_do_not_choose_domain_algorithms():
    for prefix in ['urn:alpha:', 'urn:renamed:']:
        menu = make_menu(prefix)
        card = compile_schema_card(menu, predicate_iri=prefix + 'amount',
                                   profile=ExtractionProfile(), scope=TraversalScope.create())
        assert [p.iri for p in card.predicates] == [prefix + 'amount']
        assert card.quantity_policies[0].allowed_forms == ['scalar']
        assert card.quantity_policies[0].unit_requirement == 'not_declared'
        assert not card.quantity_policies[0].allowed_target_units
        with pytest.raises(ValueError, match='predicate_outside_menu'):
            compile_schema_card(menu, predicate_iri=prefix + 'invented',
                                profile=ExtractionProfile(), scope=TraversalScope.create())


def test_card_preserves_unresolved_multiple_ranges_and_tightens_stage_menu():
    menu = make_menu('urn:alpha:')
    menu.relationships[0].range_class_iris.append('urn:alpha:Other')
    menu.relationships[0].constraint_status = 'constraint_unresolved'
    card = compile_schema_card(menu, predicate_iri=None, profile=ExtractionProfile(),
                               scope=TraversalScope.create())
    assert len(card.predicates[-1].range_class_iris) == 2
    assert card.unsupported_constraints[0].reason_code == 'constraint_unresolved'
    schema = compile_stage_schema('discovery', card=card, evidence_ids=['only-source'], targets=[])
    assert schema['$defs']['Quote']['properties']['evidence_id']['enum'] == ['only-source']
    assert schema['$defs']['RelationProposal']['properties']['predicate_iri']['enum'] == [
        'urn:alpha:links'
    ]


def build_frozen_example(examples, mutation=None):
    view = VerificationInput.model_validate(examples['verification_exchange']['verification_input'])
    request = json.loads(
        examples['verification_exchange']['request']['input'][0]['content'][0]['text']
    )
    envelope = copy.deepcopy(examples['discovery'])
    if mutation:
        mutation(envelope)
    frozen_values = {
        **envelope, 'assertion_generation': 1, 'evidence_revision': 1,
        'local_ref_map': view.local_ref_map, 'claim_issues': {},
    }
    frozen = FrozenClaimSet(content_hash=evidence_hash(frozen_values), **frozen_values)
    anchor = view.entity_dependencies[0].source_refs[0]
    context = SimpleNamespace(
        protocol_state={'evidence_revision': 1},
        fragments=[ContextFragment(anchor=anchor, text=request['evidence_units'][0]['text'],
                                   purpose='target', fact_eligible=True)],
    )
    return build_verification_input(
        frozen, discovery_ref=view.discovery_ref, context=context,
        card=SchemaCard.model_validate(request['schema_card']), scope=view.targets[0].scope,
        entity_dependencies=view.entity_dependencies, external_candidates=[],
        bridge_dependencies=[], scope_resolutions=[],
    )


def test_verifier_targets_are_derived_once_from_frozen_claims(examples):
    result = build_frozen_example(examples)
    assert len(result.targets) == 3
    assert [t.payload.local_id for t in result.targets] == ['e1', 'e2', 'r1']
    assert len(result.entity_dependencies) == 1


@pytest.mark.parametrize('field', ['selection', 'object_ids', 'modality'])
def test_changed_claim_reaches_verifier_as_changed_content(examples, field):
    original = build_frozen_example(examples).targets[-1]

    def change(envelope):
        relation = envelope['relations'][0]
        if field == 'selection':
            relation['selection'] = 'all'
        elif field == 'object_ids':
            relation['object_ids'] = ['e1']
            relation['selection'] = 'all'
        else:
            relation['qualifiers']['modality'] = 'possible'

    changed = build_frozen_example(examples, change).targets[-1]
    assert changed.content_hash != original.content_hash
    assert changed.target_id != original.target_id
    assert changed.payload != original.payload


def test_verifier_does_not_accept_unauthorized_quote_or_class(examples):
    def wrong_source(envelope):
        envelope['entities'][0]['mentions'][0]['evidence_id'] = 'outside'

    with pytest.raises(ValueError, match='source_quote_outside_scope'):
        build_frozen_example(examples, wrong_source)

    def wrong_class(envelope):
        envelope['entities'][0]['class_iri'] = 'urn:invented'

    with pytest.raises(ValueError, match='class_outside_menu'):
        build_frozen_example(examples, wrong_class)
