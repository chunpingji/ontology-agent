"""Feedback must never advertise binding-only evidence as a fact target."""

import json

import pytest

from app.services.extraction.citation_repair import repair_feedback
from app.services.extraction.ontology_guided.source_citations import SpanProposal
from tests.test_extraction.test_extraction_tasks import accept_fixture_types
from tests.test_extraction.test_template_relation_recovery import parts, quote, source, worker


@pytest.mark.parametrize(('kind', 'stage', 'field', 'domain'), [
    ('relationship', 'relationship_recall', 'assertions[0].assertion_spans', 'fact'),
    ('relationship', 'relationship_recall', 'assertions[0].conditions[0]', 'binding'),
    ('property', 'property_recall', 'assertions[0].assertion_spans', 'binding'),
    ('property', 'property_recall', 'assertions[0].value', 'fact'),
    ('entity', 'entity_recall', 'entities[0].mention', 'fact'),
    ('entity', 'entity_recall', 'entities[0].identifier.value', 'binding'),
    ('entity', 'entity_recall', 'entities[0].assertion_spans', 'binding'),
    ('relationship', 'relationship_binding', 'decision.assertion_spans', 'binding'),
    ('relationship', 'reference_verification', 'reference.assertion_spans', 'binding'),
    ('property', 'normalization', 'decision.source_unit', 'binding'),
    ('relationship', 'relationship_recall', '', 'fact'),
])
def test_feedback_search_uses_the_failed_field_permission(tmp_path, kind, stage, field, domain):
    ir = source(tmp_path, '当前事实目标', '其他记录中的设备乙')
    fact, binding = [ir.anchor(unit.evidence_id) for unit in ir.evidence_units if unit.text]
    runner = worker(None)
    runner._stage = stage
    # The model put the other record's exact quote under the current target ID.
    with pytest.raises(ValueError) as error:
        runner._anchors([SpanProposal(evidence_id=fact.evidence_id, text='设备乙')], ir,
                        [fact] if domain == 'fact' else [fact, binding], field_path=field)
    runner._record_issue(error.value)
    event = runner._task_events[0]
    assert event.get('field_path', '') == field
    feedback = repair_feedback([event], ir, [fact], [fact, binding], task_kind=kind)
    detail = feedback['errors'][0]
    assert detail['citation_domain'] == domain
    assert detail['exact_matches'] == (
        [{'evidence_id': binding.evidence_id, 'text': '设备乙'}] if domain == 'binding' else []
    )


def test_relationship_outside_scope_has_no_binding_only_repair_suggestion(tmp_path):
    ir = source(tmp_path, '当前目标', '设备乙')
    fact, binding = [ir.anchor(unit.evidence_id) for unit in ir.evidence_units if unit.text]
    runner = worker(None)
    runner._stage = 'relationship_recall'
    with pytest.raises(ValueError, match='source_quote_outside_scope') as error:
        runner._anchors([SpanProposal(evidence_id=binding.evidence_id, text='设备乙')], ir,
                        [fact], field_path='assertions[0].assertion_spans')
    runner._record_issue(error.value)
    feedback = repair_feedback(runner._task_events, ir, [fact], [fact, binding],
                               task_kind='relationship')
    assert feedback['errors'][0]['exact_matches'] == []


def test_compact_transport_delivers_repair_feedback_with_request_local_source_ids(tmp_path):
    ir = source(tmp_path, '产品：试验品甲。', '另一条记录')
    requests = []

    def model(system, user, schema, budget):
        request, task, targets = parts(user)
        requests.append(request)
        if request['stage'] == 'verify_entity_types':
            return accept_fixture_types(request)
        context = json.loads(request['context'])
        class_iri = next(iter(context['task']['predicate_definition']['classes']))
        mention = quote(targets[1], '试验品甲')
        if 'citation_feedback' in request:
            detail = request['citation_feedback']['errors'][0]
            assert detail['citation_domain'] == 'fact'
            assert detail['evidence_id'] == targets[1]['anchor']['evidence_id']
            assert detail['exact_matches'] == [quote(targets[0], '试验品甲')]
            mention = detail['exact_matches'][0]
        assert 'source_quote_rules' in request
        return {'entities': [{'class_iri': class_iri, 'mention': mention,
                              'supported': True, 'reason': '原文命名产品。'}]}

    runner = worker(model)
    runner.compact_identifiers = True
    run = runner.run(ir)
    assert run.completion == 'complete', run.diagnostics
    assert [request['stage'] for request in requests] == [
        'recall', 'recall', 'verify_entity_types',
    ]
    assert len(run.candidates) == 1 and run.candidates[0].positive_eligible
    assert ir.resolve(run.candidates[0].provenance[0].anchors[0]) == '试验品甲'
