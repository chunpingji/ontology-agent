"""Audit one frozen completed Mock experiment using saved artefacts only.

Usage: python audit-results.py RUN_ROOT [--output FILE]
No application imports, model calls, live archive reads, or quality-reference reads.
"""

import argparse
from collections import Counter
from hashlib import sha256
from importlib.metadata import version
import json
import math
from pathlib import Path
import re
from urllib.parse import quote

from jsonschema import Draft202012Validator

EQ = 'https://ontology.pharma-gmp.cn/slpra/equipment/'
DEV = 'https://ontology.pharma-gmp.cn/slpra/drug-development/'
FIELD_PREDICATES = {'equipment_id': EQ + 'equipmentID', 'name': EQ + 'equipmentName',
                    'specification': EQ + 'modelSpecification'}
FIXED_SOURCE_HASH = 'e94822808601e73f4ba4c715c664812d4a01e42f0e4e84a6b08a3653b915daf7'
STAGES = ('candidates', 'verification')
ARMS = ('E0', 'E1', 'E2')
checks, responses, metric_rows, external_rows = [], [], [], []


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                      allow_nan=False)


def value_hash(value):
    return sha256(canonical(value).encode()).hexdigest()


def file_hash(path):
    return sha256(path.read_bytes()).hexdigest()


def strict_json(text):
    def object_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate_json_key:' + key)
            result[key] = value
        return result

    def nonfinite(value):
        raise ValueError('nonfinite_json_number:' + value)

    def finite_float(value):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError('nonfinite_json_float:' + value)
        return number

    return json.loads(text, object_pairs_hook=object_pairs, parse_constant=nonfinite,
                      parse_float=finite_float)


def read(path):
    return strict_json(path.read_text())


def check(code, condition, *, where='', detail=None, category='integrity'):
    checks.append({'code': code, 'passed': bool(condition), 'where': where,
                   'category': category, 'detail': detail})
    return bool(condition)


def schema_errors(instance, schema):
    Draft202012Validator.check_schema(schema)
    return [{'instance_path': list(error.absolute_path), 'schema_path': list(error.absolute_schema_path),
             'message': error.message} for error in Draft202012Validator(schema).iter_errors(instance)]


def cite_valid(cite, sources):
    if not isinstance(cite, dict) or cite.get('ref') not in sources:
        return False
    text, raw = sources[cite['ref']]['text'], cite.get('quote')
    if not isinstance(raw, str) or not raw.strip():
        return False
    start, end = cite.get('start'), cite.get('end')
    if start is not None or end is not None:
        return (type(start) is int and type(end) is int and 0 <= start < end <= len(text)
                and text[start:end] == raw)
    return text.count(raw) == 1


def strict_shacl(metric):
    shacl = metric.get('shacl', {})
    coverage = shacl.get('coverage', {})
    cid = metric.get('candidate_id')
    expected = {'urn:ontology-agent:claim:' + quote(cid, safe='')} if isinstance(cid, str) else set()
    return (bool(expected) and metric.get('tool') == 'validate_metric'
            and shacl.get('execution_status') == 'completed' and shacl.get('evaluated') is True
            and shacl.get('conforms') is True and shacl.get('validation_status') == 'passed'
            and coverage.get('complete') is True
            and set(coverage.get('expected_focus_nodes', [])) == expected
            and set(coverage.get('actual_focus_nodes', [])) == expected
            and bool(coverage.get('executed_shapes'))
            and not coverage.get('missing_focus_nodes') and not coverage.get('unexpected_focus_nodes'))


def usage_summary(calls):
    result = {'http_calls': len(calls), 'finish_reasons': dict(Counter(
        row.get('finish_reason', 'unreported') for row in calls)),
        'http_status_codes': dict(Counter(str(row.get('status_code', 'unreported')) for row in calls))}
    for field in ('prompt_tokens', 'completion_tokens', 'total_tokens'):
        values = [row.get('usage', {}).get(field) for row in calls
                  if isinstance(row.get('usage'), dict)]
        valid = [value for value in values if type(value) is int and value >= 0]
        result[field] = {'reported_sum': sum(valid), 'reported_calls': len(valid),
                         'unknown_calls': len(calls) - len(valid),
                         'all_calls_total': sum(valid) if len(valid) == len(calls) else None}
    times = [row.get('seconds') for row in calls]
    valid_times = [t for t in times if type(t) in (int, float) and t >= 0]
    result['http_seconds'] = {'reported_sum': round(sum(valid_times), 3),
                              'reported_calls': len(valid_times),
                              'unknown_calls': len(calls) - len(valid_times)}
    return result


def metric_summary(rows):
    return {'saved_invocations': len(rows),
            'validation_statuses': dict(Counter(row['metric'].get('validation_status') for row in rows)),
            'strict_focus_covered_conformant_text_claims': sum(strict_shacl(row['metric']) for row in rows),
            'shacl_evaluated': sum(row['metric'].get('shacl', {}).get('evaluated') is True for row in rows),
            'numeric_slots': 0, 'numeric_calibration': 'not_evaluated_text_slots_only',
            'numeric_pass_fraction': None}


def check_provenance(provenance, archive, *, where, expected_field=None):
    key = provenance.get('record_key')
    record = archive.get(key)
    if not check('external_record_key_exists', record is not None, where=where, detail=key):
        return
    field = provenance.get('field_path')
    check('external_record_version_matches_frozen_archive',
          provenance.get('record_version') == value_hash(record), where=where)
    check('external_source_namespace', provenance.get('system') == 'mock_equipment'
          and provenance.get('dataset') == 'equipment_archive', where=where)
    check('external_field_value_and_snapshot', field in record and provenance.get('value') == record[field]
          and provenance.get('record_snapshot') == record, where=where)
    if expected_field:
        check('external_expected_field', field == expected_field, where=where)


def check_mock_search(search, archive, sources, arm, where):
    if arm == 'E0':
        check('E0_mock_search_not_requested', search.get('execution_status') == 'not_requested'
              and search.get('candidates') == [], where=where)
        return
    check('mock_search_completed', search.get('execution_status') == 'completed', where=where)
    expected_snapshot = value_hash({
        'system': 'mock_equipment', 'dataset': 'equipment_archive',
        'records': [{'key': key, 'version': value_hash(row)} for key, row in sorted(archive.items())],
    })
    check('mock_catalog_snapshot_identity', search.get('catalog_snapshot_hash') == expected_snapshot, where=where)
    rows = search.get('candidates', [])
    check('mock_search_unique_bounded_keys', len(rows) <= 8 and len({r['key'] for r in rows}) == len(rows),
          where=where)
    check('mock_search_archive_coverage', search.get('coverage', {}).get('archive_records') == len(archive)
          and search.get('coverage', {}).get('inspected_records') == len(archive)
          and search.get('coverage', {}).get('returned_records') == len(rows), where=where)
    for row in rows:
        key = row['key']
        record = archive.get(key)
        if not check('mock_candidate_key_exists', record is not None, where=where, detail=key):
            continue
        fields = {k: record[k] for k in FIELD_PREDICATES if k in record and record[k] != ''}
        check('mock_candidate_version_and_fields', row['version'] == value_hash(record)
              and row['fields'] == fields and row['field_predicates'] == {
                  k: FIELD_PREDICATES[k] for k in fields}, where=where, detail=key)
        check('mock_metadata_not_mapped_as_predicates', row.get('unmapped_metadata_only') is True
              and row.get('metadata') == {k: record[k] for k in ('workshop', 'material', 'location', 'area_type')
                                         if record.get(k) not in (None, '')}, where=where, detail=key)
        check('mock_matches_are_source_quotes', all(cite_valid(m, sources) for m in row['matches']),
              where=where, detail=key)


def check_stage(directory, stage, sources, roles, cards, ner_view, search, arm, calls, manifest):
    stage_dir = directory / stage
    request_files = sorted(stage_dir.glob('http-*-request.json'))
    response_files = sorted(stage_dir.glob('http-*-response.json'))
    where = str(stage_dir)
    ledger = read(stage_dir / 'calls.json') if (stage_dir / 'calls.json').exists() else []
    check('stage_at_most_one_request', len(request_files) <= 1, where=where)
    check('stage_ledger_request_count', len(request_files) == len(ledger), where=where)
    check('stage_ledger_matches_case', ledger == [c for c in calls if c['stage'] == stage], where=where)
    check('response_has_saved_request', all(p.with_name(p.name.replace('-response', '-request')) in request_files
                                          for p in response_files), where=where)
    for index, request_path in enumerate(request_files, 1):
        request = read(request_path)
        schema = read(stage_dir / 'schema.json')
        Draft202012Validator.check_schema(schema)
        check('request_uses_saved_schema', request.get('response_format', {}).get('type') == 'json_schema'
              and request['response_format']['json_schema'].get('schema') == schema, where=where)
        check('request_model_budget_settings', request.get('model') == manifest['model']
              and request.get('max_tokens') == 8192 and request.get('temperature') == 0
              and request.get('extra_body', {}).get('chat_template_kwargs', {}).get('enable_thinking') is False,
              where=where)
        messages = request['messages']
        check('request_message_roles', [m['role'] for m in messages] == ['system', 'user'], where=where)
        chars = sum(len(m['content']) for m in messages)
        size = read(stage_dir / 'input-size.json')
        check('request_input_character_accounting', size.get('message_characters') == chars
              and size.get('maximum') == 60000 and chars <= 60000, where=where)
        user = strict_json(next(m['content'] for m in messages if m['role'] == 'user'))
        payload = user['input']
        check('request_repeats_identical_output_schema', user['output_schema'] == schema, where=where)
        check('request_source_values_not_mock_or_summary', len(payload['source']) == len(sources)
              and all(s.get('ref') in sources and s.get('text') == sources[s['ref']]['text']
                      for s in payload['source'])
              and len({s['ref'] for s in payload['source']}) == len(sources), where=where)
        check('request_cards_roles_and_external_payload', payload.get('cards') == cards
              and payload.get('record_roles') == roles and payload.get('external_candidates') == search,
              where=where)
        keys = {'source', 'record_roles', 'cards', 'external_candidates',
                'ner' if stage == 'candidates' else 'targets'}
        check('request_payload_has_no_reference_or_extra_channel', set(payload) == keys, where=where)
        if stage == 'candidates':
            check('request_uses_saved_compact_ner', payload['ner'] == ner_view, where=where)
        else:
            frozen = read(directory / 'frozen-candidates.json')
            check('verification_uses_exact_frozen_targets', payload['targets'] == frozen['targets'], where=where)
        if arm == 'E0':
            check('E0_request_has_no_mock_candidates', payload['external_candidates'].get('candidates') == []
                  and payload['external_candidates'].get('execution_status') == 'not_requested', where=where)
        response_path = request_path.with_name(request_path.name.replace('-request', '-response'))
        call = ledger[index - 1] if index <= len(ledger) else {}
        check('stage_call_number_identity', call.get('http_call') == index and call.get('stage') == stage,
              where=where)
        if not response_path.exists():
            check('missing_response_recorded_as_http_failure', bool(call.get('error_type')), where=where)
            responses.append({'arm': arm, 'case': directory.name, 'stage': stage,
                              'status': 'http_failed_no_response', 'usage': call.get('usage')})
            continue
        response = read(response_path)
        check('response_usage_matches_ledger', (response.get('usage') or {}) == (call.get('usage') or {}),
              where=where)
        first = response['choices'][0]
        check('response_finish_reason_matches_ledger', first['finish_reason'] == call.get('finish_reason'),
              where=where)
        parsed, errors = None, []
        try:
            # Raw content only: no fence removal, repair, or reasoning-content fallback.
            parsed = strict_json(first['message']['content'])
            errors = schema_errors(parsed, schema)
        except (ValueError, TypeError) as exc:
            errors = [{'raw_json_error': type(exc).__name__}]
        raw_ok = not errors
        check('raw_response_json_schema', raw_ok, where=where, detail=errors, category='response_contract')
        proposal_path = stage_dir / 'proposal.json'
        if proposal_path.exists():
            saved = read(proposal_path)
            check('saved_proposal_matches_valid_raw_response', raw_ok and saved == parsed
                  and not schema_errors(saved, schema) and first['finish_reason'] == 'stop', where=where)
        responses.append({'arm': arm, 'case': directory.name, 'stage': stage,
                          'status': 'schema_valid' if raw_ok else 'schema_invalid',
                          'finish_reason': first['finish_reason'], 'raw_schema_errors': errors,
                          'saved_proposal': proposal_path.exists(),
                          'request_json_bytes': request_path.stat().st_size,
                          'message_characters': chars, 'usage': response.get('usage')})
    return int((stage_dir / 'proposal.json').exists())


def audit(root, prepared_root=None):
    manifest = read(root / 'result.json')
    check('completed_manifest', manifest.get('status') == 'completed')
    check('experiment_identity', manifest.get('version') == 'cmc-mock-equipment-three-arm-v1')
    check('declared_budgets', all(manifest.get(k) == v for k, v in {
        'max_calls': 18, 'max_input_characters': 60000, 'max_ner_spans': 48, 'max_tokens': 8192}.items()))
    check('reference_summary_mock_authority_flags', manifest.get('reference_is_input') is False
          and manifest.get('summary_is_evidence') is False
          and manifest.get('static_mock_not_database_mock') is True and manifest.get('new_summary_calls') == 0)
    for label, prefix in [('input_hashes', root), ('metadata_hashes', root / 'metadata'),
                          ('code_hashes', root / 'code')]:
        for name, expected in manifest[label].items():
            path = prefix / name
            check('frozen_' + label, path.exists() and file_hash(path) == expected, where=str(path))
    check('frozen_code_inventory_complete', {p.name for p in (root / 'code').iterdir() if p.is_file()}
          == set(manifest['code_hashes']))
    ir, cards = read(root / 'ir.json'), read(root / 'scope-cards.json')
    archive_list = read(root / 'mock-equipment.json')
    archive = {row['equipment_id']: row for row in archive_list}
    check('unique_mock_record_keys', len(archive) == len(archive_list))
    check('source_document_identity', file_hash(root / 'source.docx') == manifest['source_sha256']
          == ir['document_hash'] == FIXED_SOURCE_HASH and len(ir['evidence_units']) == 447)
    structural = {key: ir[key] for key in ('parser_version', 'ir_version', 'structure_policy_version',
                                          'nodes', 'blocks', 'tables', 'evidence_units', 'pagination')}
    check('IR_structure_identity', value_hash(structural) == ir['structure_hash'])
    analysis = {'document_hash': ir['document_hash'], 'structure_hash': ir['structure_hash'],
                'role': ir['document_role'], 'original_document_hash': ir['original_document_hash']}
    check('IR_analysis_identity', value_hash({'namespace': 'analysis', 'value': analysis}) == ir['analysis_id'])
    ir_units = {u['evidence_id']: u for u in ir['evidence_units']}
    check('text_slot_scope_only', {p['iri'] for p in cards['equipment']['properties']} == set(FIELD_PREDICATES.values())
          and all(p['datatype_iris'] == ['http://www.w3.org/2001/XMLSchema#string']
                  and not p.get('canonical_unit') for p in cards['equipment']['properties']))
    declared = manifest['case_hashes']
    check('nine_declared_cases_three_per_arm', len(declared) == 9
          and Counter(c['arm'] for c in declared) == dict.fromkeys(ARMS, 3))
    check('unique_case_identities', len({(c['arm'], c['scope_id']) for c in declared}) == len(declared))
    results = {(row['arm'], row['scope_id']): row for row in manifest['results']}
    check('top_results_cover_exact_declared_cases', len(results) == len(manifest['results']) == len(declared)
          and set(results) == {(c['arm'], c['scope_id']) for c in declared})
    if prepared_root is not None:
        prepared = read(prepared_root / 'result.json')
        identity_keys = ('version', 'source_sha256', 'input_hashes', 'metadata_hashes', 'code_hashes',
                         'case_hashes', 'model', 'declared_model_revision', 'gliner2_model', 'packages',
                         'device', 'max_calls', 'max_input_characters', 'max_ner_spans', 'max_tokens')
        check('prepared_NER_identity', prepared.get('status') == 'ner_completed'
              and manifest.get('ner_reused_from') == prepared['run_id']
              and all(prepared.get(k) == manifest.get(k) for k in identity_keys))
        check('prepared_NER_manifest_hashes_match_run', prepared['ner_hashes'] == manifest['ner_hashes'])
        for name, expected in prepared['code_hashes'].items():
            path = prepared_root / 'code' / name
            check('prepared_code_hash', path.exists() and file_hash(path) == expected, where=str(path))
    all_calls, case_rows, pairs, omitted_ner = [], [], {}, 0
    for declared_case in declared:
        arm, scope = declared_case['arm'], declared_case['scope_id']
        directory = root / declared_case['directory']
        where = f'{arm}/{scope}'
        check('case_path_identity', Path(declared_case['directory']).parts == (arm, scope), where=where)
        for name, hash_key in [('case.json', 'case_sha256'), ('sources.json', 'source_sha256'),
                               ('mock-search.json', 'mock_search_sha256')]:
            check('frozen_case_file_hash', file_hash(directory / name) == declared_case[hash_key], where=where + '/' + name)
        local, sources = read(directory / 'case.json'), read(directory / 'sources.json')
        search, ner = read(directory / 'mock-search.json'), read(directory / 'ner.json')
        ner_view, result = read(directory / 'ner-model-view.json'), read(directory / 'result.json')
        check('case_identity', all(local[k] == declared_case[k] == result[k]
                                  for k in ('arm', 'scope_id', 'record_id')), where=where)
        check('root_case_result_ledger_identical', result == results.get((arm, scope)), where=where)
        check('case_source_catalog_consistency', local['sources'] == sources, where=where)
        check('case_units_exact_frozen_IR', all(u == ir_units.get(u['evidence_id']) for u in local['source_units']), where=where)
        check('case_source_strings_exact_IR', all(s.get('evidence_id') in ir_units
              and s['text'] == ir_units[s['evidence_id']]['text'] for s in sources.values()), where=where)
        check('case_full_text_and_budget', local['content']['text'] == '\n'.join(u['text'] for u in local['source_units'])
              and local['source_characters'] == sum(len(u['text']) for u in local['source_units']) <= 3000,
              where=where)
        roles = {k: local['roles'][k] for k in ('primary_refs', 'context_refs')}
        check('case_primary_context_partition', set(roles['primary_refs']).isdisjoint(roles['context_refs'])
              and set(roles['primary_refs']) | set(roles['context_refs']) == set(sources), where=where)
        check('frozen_ner_hash', file_hash(directory / 'ner.json') == manifest['ner_hashes'][declared_case['directory']], where=where)
        if prepared_root is not None:
            prior_directory = prepared_root / declared_case['directory']
            for name in ('case.json', 'sources.json', 'mock-search.json', 'ner.json', 'ner-model-view.json'):
                check('prepared_case_artifact_identical', file_hash(prior_directory / name) == file_hash(directory / name),
                      where=where + '/' + name)
        vocabulary = read(root / 'vocabulary.json')['entries']
        check('ner_complete_and_source_bound', ner.get('execution_status') == 'completed' and all(
            s['ref'] in sources and type(s['start']) is int and type(s['end']) is int
            and 0 <= s['start'] < s['end'] <= len(sources[s['ref']]['text'])
            and sources[s['ref']]['text'][s['start']:s['end']] == s['text']
            and s['label'] in vocabulary for s in ner['spans']), where=where)
        unique, compact = set(), []
        for span in sorted(ner['spans'], key=lambda s: (-s['score'], s['ref'], s['start'], s['end'], s['label'])):
            key = (span['ref'], span['start'], span['end'], span['label'])
            if key not in unique:
                unique.add(key)
                compact.append({k: span[k] for k in ('ref', 'start', 'end', 'text', 'label', 'score')})
        check('compact_ner_selection_and_budget', ner_view['spans'] == compact[:48]
              and ner_view['available_unique_spans'] == len(compact)
              and ner_view['omitted_spans'] == len(compact[48:]), where=where)
        omitted_ner += ner_view['omitted_spans']
        check_mock_search(search, archive, sources, arm, where)
        calls = result.get('calls', [])
        check('case_call_budget_and_stages', len(calls) <= 2
              and [c['stage'] for c in calls] in [[], ['candidates'], ['candidates', 'verification']], where=where)
        contracts = sum(check_stage(directory, stage, sources, roles, cards, ner_view, search, arm, calls, manifest)
                        for stage in STAGES)
        check('case_contract_counter', result.get('contracts_passed') == contracts, where=where)
        check('case_status_contract_consistency',
              (result['status'] == 'complete' and contracts == 2 and len(calls) == 2
               and all(c.get('finish_reason') == 'stop' for c in calls))
              or (result['status'] == 'failed' and bool(result.get('error_type'))
                  and result.get('failure_stage') in ('input', *STAGES)), where=where)
        if (directory / 'frozen-candidates.json').exists():
            frozen = read(directory / 'frozen-candidates.json')
            check('frozen_proposal_equals_candidate_response', frozen['proposal'] == read(directory / 'candidates/proposal.json'), where=where)
            for cid, item in frozen['checks'].items():
                if 'metric_precheck' in item:
                    metric_rows.append({'arm': arm, 'case': scope, 'stage': 'precheck', 'candidate_id': cid,
                                        'metric': item['metric_precheck']})
        else:
            frozen = {'targets': [], 'checks': {}}
        target_map = {target['id']: target for target in frozen['targets']}
        for metric in result.get('metrics', []):
            metric_rows.append({'arm': arm, 'case': scope, 'stage': 'final', 'candidate_id': metric['candidate_id'],
                                'metric': metric})
            target = target_map.get(metric.get('candidate_id'), {})
            check('metric_uses_frozen_document_literal', target.get('kind') == 'property'
                  and metric.get('raw_value') == target.get('proposal', {}).get('raw'), where=where)
        if result['status'] == 'complete':
            check('complete_case_is_not_fact_or_numeric_claim', result.get('fact_eligible') is False
                  and result.get('numeric_calibration') == 'not_evaluated_text_slots_only', where=where)
        accepted = {row['id']: row for row in result.get('accepted', [])}
        check('accepted_ids_unique_and_frozen', len(accepted) == len(result.get('accepted', []))
              and all(row['id'] in target_map and row['kind'] == target_map[row['id']]['kind']
                      and row['proposal'] == target_map[row['id']]['proposal']
                      and row['verdict']['verdict'] == 'supported' and not row['issues']
                      for row in accepted.values()), where=where)
        metrics_by_id = {m['candidate_id']: m for m in result.get('metrics', [])}
        for row in accepted.values():
            if row['kind'] == 'property':
                metric = metrics_by_id.get(row['id'], {})
                check('accepted_text_attribute_has_valid_metric', metric.get('validation_status') == 'passed'
                      and strict_shacl(metric) and metric.get('normalized_value') == row['proposal']['raw'].strip(), where=where)
        if arm == 'E0':
            check('E0_has_no_external_records', not result.get('external_records'), where=where)
        for external in result.get('external_records', []):
            eid, link, fields = external['entity_id'], external['link'], external['fields']
            external_rows.append({'arm': arm, 'case': scope, 'entity_id': eid,
                                  'key': fields.get('key'), 'version': fields.get('version'),
                                  'mapped_field_count': len(fields.get('fields', []))})
            check('external_entity_accepted', eid in accepted and accepted[eid]['kind'] == 'entity'
                  and accepted[eid]['verdict']['verdict'] == 'supported', where=where)
            check('external_link_supported_with_source', link.get('identity_status') == 'supported'
                  and link.get('validation_status') == 'passed' and cite_valid(link.get('citation'), sources)
                  and link.get('fact_eligible') is False, where=where)
            check_provenance(link['external_record'], archive, where=where, expected_field='equipment_id')
            key = fields.get('key')
            record = archive.get(key)
            check('external_entity_link_fields_share_one_identity', eid in accepted
                  and accepted[eid]['proposal'].get('external_key') == key
                  and link['external_record'].get('record_key') == key, where=where)
            citation = link.get('citation') or {}
            source_text = sources.get(citation.get('ref'), {}).get('text', '')
            exact_matches = list(re.finditer(r'(?<![A-Za-z0-9_])' + re.escape(key or '')
                                            + r'(?![A-Za-z0-9_])', source_text))
            check('external_identity_has_full_source_identifier', bool(key) and any(
                citation.get('start', -1) <= match.start() and match.end() <= citation.get('end', -1)
                for match in exact_matches), where=where)
            check('external_fields_record_identity', record is not None and fields.get('version') == value_hash(record)
                  and fields.get('class_iri') == EQ + 'ProcessEquipment'
                  and fields.get('validation_status') == 'passed' and fields.get('fact_eligible') is False,
                  where=where)
            if record is not None:
                expected = {k for k in FIELD_PREDICATES if record.get(k) not in (None, '')}
                check('external_fields_only_mapped_present_attributes',
                      {f['field_path'] for f in fields['fields']} == expected and len(fields['fields']) == len(expected), where=where)
                for field in fields['fields']:
                    name = field['field_path']
                    check('external_field_predicate_and_value', name in FIELD_PREDICATES
                          and field['predicate_iri'] == FIELD_PREDICATES.get(name)
                          and field['value'] == record.get(name), where=where)
                    check_provenance(field['external_record'], archive, where=where, expected_field=name)
        case_rows.append({'arm': arm, 'scope_id': scope, 'record_id': local['record_id'],
                          'status': result['status'], 'contracts_passed': contracts,
                          'calls': usage_summary(calls), 'accepted_by_kind': dict(Counter(r['kind'] for r in accepted.values())),
                          'unresolved': len(result.get('unresolved', [])), 'rejected': len(result.get('rejected', [])),
                          'error_type': result.get('error_type'), 'error_code': result.get('error_code')})
        pairs[(arm, local['record_id'])] = (local, sources, ner, ner_view)
        all_calls.extend({'arm': arm, 'case': scope, **row} for row in calls)
    for (arm, rid), (local, sources, ner, ner_view) in pairs.items():
        if arm == 'E0':
            peer = pairs.get(('E1', rid))
            check('E0_E1_same_record_source_ner', peer is not None and peer[1:] == (sources, ner, ner_view)
                  and {k: v for k, v in local.items() if k != 'arm'} == {k: v for k, v in peer[0].items() if k != 'arm'},
                  where=rid)
        if arm == 'E2' and ('E1', rid) in pairs:
            peer = pairs[('E1', rid)]
            check('shared_E1_E2_record_source_ner_identical', peer[1:] == (sources, ner, ner_view), where=rid)
            first = root / 'E1' / peer[0]['scope_id'] / 'candidates/http-01-request.json'
            second = root / 'E2' / local['scope_id'] / 'candidates/http-01-request.json'
            if first.exists() and second.exists():
                check('shared_E1_E2_candidate_request_identical', read(first) == read(second), where=rid)
    unique_ner_inputs = {value_hash(sources) for _, sources, _, _ in pairs.values()}
    check('ner_unique_input_accounting', manifest.get('ner_unique_inputs') == len(unique_ner_inputs),
          detail={'unique_inputs': len(unique_ner_inputs)})
    check('global_call_budget', len(all_calls) <= 18)
    for row in metric_rows:
        metric = row['metric']
        check('text_metric_not_numeric_calibration', metric.get('quantity') is None
              and metric.get('unit_checks', {}).get('status') == 'not_required'
              and metric.get('conversion_record') in (None, {}), where=f"{row['arm']}/{row['case']}/{row['candidate_id']}/{row['stage']}")
        if metric.get('validation_status') == 'passed':
            check('passed_metric_has_real_focus_and_conformance', strict_shacl(metric),
                  where=f"{row['arm']}/{row['case']}/{row['candidate_id']}/{row['stage']}")
    integrity_failures = [c for c in checks if c['category'] == 'integrity' and not c['passed']]
    arm_summary = {}
    for arm in ARMS:
        selected = [row for row in case_rows if row['arm'] == arm]
        arm_summary[arm] = {
            'cases': len(selected), 'case_statuses': dict(Counter(row['status'] for row in selected)),
            'contracts_passed': sum(row['contracts_passed'] for row in selected),
            'http': usage_summary([row for row in all_calls if row['arm'] == arm]),
            'metrics': {stage: metric_summary([row for row in metric_rows if row['arm'] == arm and row['stage'] == stage])
                        for stage in ('precheck', 'final')},
            'external_link_count': sum(row['arm'] == arm for row in external_rows),
        }
    return {'run_root': str(root), 'run_id': manifest['run_id'],
            'prepared_ner_root': str(prepared_root) if prepared_root else None,
            'audit_script_sha256': file_hash(Path(__file__)), 'jsonschema_version': version('jsonschema'),
            'summary': {'integrity_passed': not integrity_failures, 'integrity_failures': len(integrity_failures),
                        'unique_ner_inputs': len(unique_ner_inputs),
                        'model_view_omitted_spans_across_cases': omitted_ner,
                        'model_view_truncated_any_case': omitted_ner > 0,
                        'raw_responses': len([r for r in responses if r['status'] != 'http_failed_no_response']),
                        'raw_schema_passes': sum(r['status'] == 'schema_valid' for r in responses),
                        'complete_cases': sum(row['status'] == 'complete' for row in case_rows),
                        'http': usage_summary(all_calls), 'numeric_calibration': 'not_evaluated_text_slots_only'},
            'arms': arm_summary, 'cases': case_rows, 'responses': responses, 'metrics': metric_rows,
            'external_records': external_rows, 'checks': checks,
            'limitations': [
                'No model calls or main runner imports; only saved completed-run artifacts were read.',
                'Quality reference was not read. Program acceptance and SHACL conformance are not extraction precision/recall.',
                'E0/E1 share source and NER; E2 changes retrieval and can change examined records. No delta is attributed to historical D.',
                'Shared E1/E2 inputs are checked; temperature zero does not require independent model outputs to be identical.',
                'Missing usage remains unknown, not zero; HTTP stage seconds differ from complete experiment wall time.',
                'Text literal SHACL does not demonstrate numeric unit calibration, archive truth, or relationship correctness.',
            ]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run_root', type=Path)
    parser.add_argument('--prepared-ner', type=Path)
    parser.add_argument('--output', type=Path, default=Path('/tmp/cmc-mock-validation-20260916/audit-results.json'))
    args = parser.parse_args()
    output = audit(args.run_root.resolve(), args.prepared_ner.resolve() if args.prepared_ner else None)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(output['summary'], ensure_ascii=False))
    print(args.output)


if __name__ == '__main__':
    main()
