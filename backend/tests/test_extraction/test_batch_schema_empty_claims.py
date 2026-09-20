"""Batch discovery must not send impossible empty-enum claim branches to the model."""

import json

import pytest

from app.services.extraction.ontology_guided.contracts import EdgeSpec
from app.services.extraction.ontology_guided.recognition_batch import compile_batch_stage_schema
from tests.test_extraction.test_recognition_batch import batch_members  # noqa: F401
from tests.test_extraction.test_tool_engine_context import authorized_context  # noqa: F401


@pytest.mark.parametrize('kind', ['property', 'relationship'])
def test_batch_discovery_empty_claim_categories_preserve_member_predicate_authority(
    batch_members, kind,  # noqa: F811
):
    tasks, members = batch_members
    if kind == 'relationship':
        for task, member in zip(tasks, members):
            member.card.predicates = [EdgeSpec(
                iri=task.predicate_iri, label='related', range_class_iris=['urn:Target'],
            )]
    schema = compile_batch_stage_schema('discovery', members, reference_resolution=True)
    definitions = schema['$defs']
    forbidden = 'properties' if kind == 'relationship' else 'relations'
    allowed = 'relations' if kind == 'relationship' else 'properties'
    proposal = 'RelationProposal' if kind == 'relationship' else 'PropertyProposal'
    assert definitions['BatchMemberResult']['properties'][forbidden]['maxItems'] == 0
    assert 'maxItems' not in definitions['BatchMemberResult']['properties'][allowed]
    assert definitions[proposal]['properties']['predicate_iri']['enum'] == [
        task.predicate_iri for task in tasks
    ]
    assert definitions['EntityProposal']['properties']['identifier_claims']['maxItems'] == 0
    assert '"enum": []' not in json.dumps(schema)
