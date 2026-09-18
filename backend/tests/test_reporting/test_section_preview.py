"""Transient preview must use owned sources without running a whole report."""

from copy import deepcopy

import pytest
from sqlalchemy import func, select

from app.config import settings
from app.models.reporting import ReportRun
from app.services.reporting.demo_sources import PROMPT_REF, builtin_contract, contract_ref
from app.services.reporting.input_resolver import ResolvedValue
from app.services.reporting.narrative_renderer import assisted_nodes
from app.services.reporting.section_narrative import (
    CUSTOM_PROMPT_REF,
    draft_value,
    substitute_placeholders,
    write_preview,
)
from app.services.reporting.section_preview import resolved_fields, variables_from_fields
from app.services.reporting.template_v2 import Budget, NarrativeRender, ReportingError, TemplateV2
from tests.test_api.test_template_finder import finder_setup, route  # noqa: F401
from tests.test_reporting.test_finder_report_compatibility import finder_source, resolve_finder
from tests.test_reporting.test_output_contracts import contracts, ontology, template


def test_guided_graph_keeps_selected_paths_and_conditions():
    from app.services.reporting.section_preview import scoped_guided_graph, variables_from_graph

    parsed = TemplateV2.model_validate(template())
    parsed.definitions.bindings['equipment'].scope.object_ids = ['chosen']
    graph = {
        'entities': [{'entity_id': key, 'seed_origin': 'user_selected' if key == 'root'
                      else 'recognized'} for key in ['root', 'chosen', 'other']],
        'relationships': [{'subject_ref': {'entity_id': 'root'}, 'object_ref': {'entity_id': key},
                           'direction': 'subject_to_object', 'predicate_iri': 'urn:uses'}
                          for key in ['chosen', 'other']],
        'properties': [{'subject_ref': {'entity_id': key}, 'predicate_label': '条件用量',
                        'raw_value': '10 kg', 'conditions': [{'text': '仅试制时'}]}
                       for key in ['chosen', 'other']],
        'coverage': {'records_incomplete': 1},
    }
    result = scoped_guided_graph(graph, parsed, parsed.sections[0])
    assert {e['entity_id'] for e in result['entities']} == {'root', 'chosen'}
    assert len(result['relationships']) == 1
    assert len(result['properties']) == 1
    assert result['properties'][0]['conditions'] == [{'text': '仅试制时'}]
    assert '条件用量' not in variables_from_graph(result)
    assert len(graph['properties']) == 2
    scope = parsed.definitions.bindings['equipment'].scope
    scope.root.kind = 'entity_ref'
    scope.root.entity_id = 'chosen'
    scope.predicate_path[0].direction = 'inverse'
    scope.object_ids = ['root']
    result = scoped_guided_graph(graph, parsed, parsed.sections[0])
    assert {e['entity_id'] for e in result['entities']} == {'root', 'chosen'}
    assert len(result['relationships']) == 1


def test_partial_fields_allow_custom_prose_but_do_not_relax_strict_prose():
    schema = template()
    schema['definitions']['bindings']['equipment']['scope']['require_complete_set'] = True
    source = finder_source()
    source['relationships'][0]['object_data_properties'] = [
        source['relationships'][0]['object_data_properties'][0],
    ]
    source['relationships'][1]['object_data_properties'].append({'iri': 'urn:code', 'value': 'bad'})
    snapshot = resolve_finder(schema, source)
    value = ResolvedValue.model_validate(snapshot['inputs']['rows'])
    data = draft_value(value)
    assert data['coverage'] == 'open'
    assert data['items'][0]['fields']['code']['value'] == 'EQ-A'
    assert data['items'][0]['fields']['spec']['value'] == '（待补充）'
    assert data['items'][1]['fields']['code']['value'] == '（待补充）'
    assert 'bad' not in str(data)
    render = NarrativeRender.model_validate({'kind': 'narrative', 'mode': 'assisted', 'prompt': {
        'instructions': '描述设备', 'policy_ref': CUSTOM_PROMPT_REF,
        'input_refs': [{'input_id': 'rows'}], 'required_refs': [{'input_id': 'rows'}],
    }})
    calls = []

    def provider(system, payload, policy, budget):
        calls.append(payload)
        return {'nodes': [{'kind': 'input_ref', 'input_id': 'rows'}]}

    nodes, _ = assisted_nodes(render, {'rows': value}, {},
                              builtin_contract(CUSTOM_PROMPT_REF)['definition'], Budget(), provider)
    assert nodes[0].input_id == 'rows'
    assert calls[0]['inputs'][0]['value'] == data
    with pytest.raises(ReportingError, match='INPUT_CONSUMPTION_BLOCKED'):
        assisted_nodes(render, {'rows': value}, {},
                       builtin_contract(PROMPT_REF)['definition'], Budget(), provider)
    assert len(calls) == 1
    assert snapshot['material_status'] != 'ready'


def test_full_report_custom_section_renders_available_rows_and_retains_material_gap():
    from app.services.reporting.output_renderer import render_snapshot

    schema = template()
    schema['definitions']['bindings']['equipment']['scope']['require_complete_set'] = True
    schema['sections'][0]['narrative'] = {
        'enabled': True, 'instructions': '介绍已有设备', 'policy_ref': CUSTOM_PROMPT_REF,
        'input_refs': [{'input_id': 'rows'}], 'required_refs': [{'input_id': 'rows'}],
    }
    source = finder_source()
    source['relationships'][0]['object_data_properties'] = (
        source['relationships'][0]['object_data_properties'][:1]
    )
    snapshot = resolve_finder(schema, source, {
        CUSTOM_PROMPT_REF: builtin_contract(CUSTOM_PROMPT_REF),
    })
    result = render_snapshot(snapshot, provider=lambda *_: {
        'nodes': [{'kind': 'input_ref', 'input_id': 'rows'}],
    })
    section = result['output_results'][0]
    assert result['execution_status'] == 'completed'
    assert 'EQ-A' in str(section) and 'EQ-B' in str(section)
    assert '待补充' in str(section) and '部分数据' in str(section)
    assert snapshot['material_status'] != 'ready'


def test_preview_resolves_partial_finder_and_filtered_mock_independent_of_bad_output(
    db, monkeypatch,
):
    from app.models.mock_data import MockTeamMember
    from app.services.reporting.report_run_service import ReportRunService

    schema = template()
    schema['definitions']['bindings']['equipment']['scope']['require_complete_set'] = True
    schema['definitions']['bindings']['team'] = {
        'binding_id': 'team', 'kind': 'context', 'contract_ref': contract_ref('assessment_team'),
        'scope': {'record_slot': 'team'},
    }
    schema['definitions']['inputs']['team'] = {
        'input_id': 'team', 'name': 'team', 'label': '评估小组', 'binding_ref': 'team',
        'projection': {'kind': 'identity'},
    }
    schema['record_sources'] = {'team': {'provider': 'assessment_team',
        'contract_ref': contract_ref('assessment_team'), 'filters': {'department': 'QA'}}}
    section = schema['sections'][0]
    unit = section['groups'][0]['units'][0]
    unit['inputs'].append({'input_ref': 'team', 'alias': 'team'})
    unit['render'] = {'kind': 'static', 'nodes': [], 'approval_ref': 'unresolved:approval'}
    db.add_all([
        MockTeamMember(team_type='assessment', name='已筛选评估人', role_code='a',
                       role_label='组员', role_class_iri='urn:Reviewer', department='QA'),
        MockTeamMember(team_type='assessment', name='不在筛选内', role_code='b',
                       role_label='组员', role_class_iri='urn:Reviewer', department='QC'),
        MockTeamMember(team_type='approver', name='审批名单不可混入', role_code='c',
                       role_label='组员', role_class_iri='urn:Reviewer', department='QA'),
    ])
    db.commit()
    monkeypatch.setattr(
        ReportRunService, 'prepare', lambda self, value: TemplateV2.model_validate(value),
    )
    cs = {**contracts(), CUSTOM_PROMPT_REF: builtin_contract(CUSTOM_PROMPT_REF),
          contract_ref('assessment_team'): builtin_contract(contract_ref('assessment_team'))}
    monkeypatch.setattr(ReportRunService, 'load_contracts', lambda *_: (ontology(), cs))
    parsed = TemplateV2.model_validate(schema)
    fields = resolved_fields(db, None, parsed, parsed.sections[0], finder_source())
    assert fields[0]['value']['coverage'] == 'open', fields
    assert 'EQ-A' in str(fields)
    assert '已筛选评估人' in str(fields)
    assert '不在筛选内' not in str(fields)
    assert '审批名单不可混入' not in str(fields)
    assert db.scalar(select(func.count()).select_from(ReportRun)) == 0


def test_placeholders_are_single_pass_and_ambiguity_stays_pending():
    assert substitute_placeholders('{{产品}} / {{无数据}} / {{}}', {'产品': 'HRS-5678'}) == (
        'HRS-5678 / （待补充） / （待补充）'
    )
    assert substitute_placeholders(
        '{{产品}}', {'产品': '{{机密}}', '机密': '禁止递归替换'},
    ) == '｛｛机密｝｝'
    fields = [{'label': '设备', 'value': {'status': 'ready', 'value': '同名'}}] * 2
    assert variables_from_fields(fields)['设备'] == '（待补充：字段名不唯一）'
    assert substitute_placeholders(
        '{{批量}}', {'批量': {'value': '1.50', 'unit': 'kg'}},
    ) == '1.50 kg'


def test_empty_custom_response_is_not_success_when_required_refs_are_empty(monkeypatch):
    from app.services.reporting.narrative_renderer import local_provider
    from app.services.reporting.output_renderer import render_snapshot
    from tests.test_reporting.test_section_narrative import configured

    schema = configured()
    schema['sections'][0]['narrative']['required_refs'] = []
    source = finder_source()
    source['relationships'] = source['relationships'][:1]
    snapshot = resolve_finder(schema, source, {
        CUSTOM_PROMPT_REF: builtin_contract(CUSTOM_PROMPT_REF),
    })
    monkeypatch.setattr('app.services.llm.local_client.get_local_llm', lambda: object())
    monkeypatch.setattr(
        'app.services.llm.local_client.chat_with_schema', lambda *a, **kw: {'text': '  '},
    )
    result = render_snapshot(snapshot, provider=local_provider)
    assert result['execution_status'] == 'failed'
    assert 'OUTPUT_REFERENCE_INVALID' in str(result)


def test_writer_keeps_partial_context_and_supports_markdown(monkeypatch):
    monkeypatch.setattr(settings, 'llm_suggest_slots_enabled', True)
    monkeypatch.setattr('app.services.llm.local_client.get_local_llm', lambda: object())
    calls = []

    def model(client, **kwargs):
        calls.append(kwargs)
        return {'content': '产品为 {{产品}}。\n\n| 项目 | 值 |\n|---|---|\n|用量|{{用量}}|'}

    monkeypatch.setattr('app.services.llm.local_client.chat_with_schema', model)
    result = write_preview(
        '介绍 {{产品}} 和 {{用量}}', {'fields': [{'coverage': 'open'}]}, {'产品': 'HRS'},
    )
    assert '产品为 HRS' in result and '|用量|（待补充）|' in result
    assert '{{' not in calls[0]['user']
    assert 'open' in calls[0]['user']
    monkeypatch.setattr(
        'app.services.llm.local_client.chat_with_schema', lambda *a, **kw: {'content': ''},
    )
    with pytest.raises(ReportingError) as failure:
        write_preview('prompt', {}, {})
    assert failure.value.code == 'MODEL_RESPONSE_INVALID'


def test_preview_api_uses_owned_finder_without_template_or_report_side_effects(
    client, db, analyst_headers, operator_headers, finder_setup, monkeypatch,  # noqa: F811
):
    from app.models.extraction import AnnotationExecution, AstTemplate

    row, job, path, _ = finder_setup
    row.schema_json = {
        **row.schema_json, 'sections': [{'section_id': 's', 'title': '正文', 'groups': []}],
    }
    db.commit()
    original = deepcopy(row.schema_json)
    url = '/api/ast-templates/preview-section-narrative'
    request = {'template_id': str(row.id), 'job_id': str(job.id), 'section_id': 's',
               'prompt': '以两段描述本节原文，未知部分保留待补充'}
    calls = []
    monkeypatch.setattr(settings, 'llm_suggest_slots_enabled', True)
    monkeypatch.setattr('app.services.llm.local_client.get_local_llm', lambda: object())

    def model(client, **kwargs):
        calls.append(kwargs)
        assert '重复值' in kwargs['user']
        assert 'authoring-only-sample' not in kwargs['user']
        return {'content': '本节正文。\n\n缺少的字段：（待补充）'}

    monkeypatch.setattr('app.services.llm.local_client.chat_with_schema', model)
    assert client.post(url, json=request, headers=operator_headers).status_code == 403
    missing = client.post(url, json=request, headers=analyst_headers)
    assert missing.status_code == 409 and 'FINDER_RESULT_UNAVAILABLE' in missing.text
    started = client.post(
        route(row, job), json={'request_key': 'preview-source'}, headers=analyst_headers,
    )
    assert started.status_code == 202, started.text
    counts = [db.scalar(select(func.count()).select_from(model))
              for model in (ReportRun, AstTemplate, AnnotationExecution)]
    response = client.post(url, json=request, headers=analyst_headers)
    assert response.status_code == 200, response.text
    assert '本节正文' in response.json()['narrative']
    assert response.json()['source']['kind'] == 'finder_demo'
    assert counts == [db.scalar(select(func.count()).select_from(model))
                      for model in (ReportRun, AstTemplate, AnnotationExecution)]
    db.refresh(row)
    assert row.schema_json == original
    assert len(calls) == 1
    other = client.post(url, json=request, headers={**analyst_headers, 'X-User': 'other-analyst'})
    assert other.status_code == 409 and 'FINDER_RESULT_UNAVAILABLE' in other.text
    assert len(calls) == 1
    row.recognition_mode = 'ontology_guided'
    db.commit()
    normal = client.post(url, json=request, headers=analyst_headers)
    assert normal.status_code == 409 and 'GRAPH_RESULT_UNAVAILABLE' in normal.text
    assert len(calls) == 1
    row.recognition_mode = 'finder_legacy'
    row.finder_profile_id = 'cmc_baseline_v1'
    db.commit()
    path.write_bytes(b'changed source')
    stale = client.post(url, json=request, headers=analyst_headers)
    assert stale.status_code == 409 and 'FINDER_RESULT_STALE' in stale.text
    assert len(calls) == 1
