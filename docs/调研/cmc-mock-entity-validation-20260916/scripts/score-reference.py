"""Post-run lexical/reference scoring; never supplies recognition inputs.

Usage: python score-reference.py RUN_ROOT REFERENCE [--output FILE]
Reference is fixed before predictions. Only original candidate proposals and final
destinations are scored; verifier decisions are not used to define the reference.
"""

import argparse
from collections import Counter, defaultdict
from hashlib import sha256
import json
from pathlib import Path
import re

ID_PATTERN = re.compile(r'(?<![A-Za-z0-9_-])[A-Z]{1,8}\d{3,}(?![A-Za-z0-9_-])')
ARMS = ('E0', 'E1', 'E2')


def read(path):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate_json_key:' + key)
            result[key] = value
        return result

    def nonfinite(value):
        raise ValueError('nonfinite_json_number:' + value)

    return json.loads(path.read_text(), object_pairs_hook=pairs, parse_constant=nonfinite)


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def cite_valid(cite, sources):
    if not isinstance(cite, dict) or cite.get('ref') not in sources:
        return False
    text, raw = sources[cite['ref']]['text'], cite.get('quote')
    if not isinstance(raw, str) or not raw:
        return False
    start, end = cite.get('start'), cite.get('end')
    if start is not None or end is not None:
        return (type(start) is int and type(end) is int and 0 <= start < end <= len(text)
                and text[start:end] == raw)
    return text.count(raw) == 1


def selected_mentions(reference, sources, refs):
    by_eid = {sources[ref]['evidence_id']: sources[ref] for ref in refs}
    selected = []
    for mention in reference['equipment_id_mentions']:
        cited = mention['source']
        anchor = cited['anchor']
        source = by_eid.get(anchor['evidence_id'])
        if source is not None:
            if source['text'][anchor['span_start']:anchor['span_end']] != cited['quote']:
                raise ValueError('selected_reference_quote_does_not_replay')
            selected.append(mention)
    return selected


def coverage(reference, local, sources):
    primary = selected_mentions(reference, sources, local['roles']['primary_refs'])
    context = selected_mentions(reference, sources, local['roles']['context_refs'])
    ids = lambda rows: sorted({row['equipment_id'] for row in rows})
    return {'primary': {'mention_ids': [r['mention_id'] for r in primary], 'equipment_ids': ids(primary)},
            'context': {'mention_ids': [r['mention_id'] for r in context], 'equipment_ids': ids(context)},
            'context_only_equipment_ids': sorted(set(ids(context)) - set(ids(primary))),
            'selected_equipment_ids': sorted(set(ids(primary)) | set(ids(context)))}


def id_rows(entities, sources):
    output = []
    for entity in entities:
        citation = entity.get('equipment_id', {})
        raw = citation.get('quote', '')
        single = raw if isinstance(raw, str) and ID_PATTERN.fullmatch(raw) else None
        valid_quote = cite_valid(citation, sources)
        if single and valid_quote:
            text = sources[citation['ref']]['text']
            start = citation.get('start')
            start = text.index(raw) if start is None else start
            valid_quote = any(match.start() == start and match.group() == single
                              for match in ID_PATTERN.finditer(text))
        anchor_ids = sorted(set(ID_PATTERN.findall(entity.get('anchor', {}).get('quote', ''))))
        raw_ids = sorted(set(ID_PATTERN.findall(raw))) if isinstance(raw, str) else []
        output.append({'local_entity_id': entity['id'], 'raw_equipment_id': raw,
                       'single_identifier': single, 'identifier_quote_valid': valid_quote,
                       'anchor_quote_valid': cite_valid(entity.get('anchor'), sources),
                       'anchor_identifier_strings': anchor_ids, 'raw_identifier_strings': raw_ids,
                       'multiple_identifiers_merged': len(anchor_ids) > 1 or len(raw_ids) > 1,
                       'identifier_field_merges_multiple_ids': len(raw_ids) > 1,
                       'anchor_contains_multiple_ids': len(anchor_ids) > 1,
                       'anchor_identifier_disagrees': bool(single and anchor_ids and anchor_ids != [single]),
                       'external_key': entity.get('external_key', '')})
    return output


def set_score(ids, scope, available):
    primary, selected = set(scope['primary']['equipment_ids']), set(scope['selected_equipment_ids'])
    matched = ids & primary
    return {
        'status': 'evaluated_lexical_only' if available else 'not_evaluated_no_stage_output',
        'predicted_ids': sorted(ids), 'primary_reference_denominator': len(primary),
        'selected_reference_denominator': len(selected),
        'primary_exact_matches': sorted(matched),
        'context_only_predictions': sorted(ids & selected - primary),
        'not_in_selected_reference_ids': sorted(ids - selected),
        'missing_primary_ids': sorted(primary - ids) if available else [],
        'not_evaluated_primary_ids': sorted(primary) if not available else [],
        'primary_exact_set_precision': ratio(len(matched), len(ids)) if available else None,
        'primary_exact_set_recall': ratio(len(matched), len(primary)) if available else None,
        'selected_exact_set_precision': ratio(len(ids & selected), len(ids)) if available else None,
        'selected_exact_set_recall': ratio(len(ids & selected), len(selected)) if available else None,
    }


def alternative_scores(reference, relations, entity_rows, sources, local, available):
    entity_ids = {row['local_entity_id']: row['single_identifier'] for row in entity_rows
                  if row['identifier_quote_valid']}
    raw_entity_ids = {row['local_entity_id']: set(row['raw_identifier_strings']) for row in entity_rows}
    primary_eids = {sources[ref]['evidence_id'] for ref in local['roles']['primary_refs']}
    selected_eids = {s['evidence_id'] for s in sources.values()}
    output = []
    for group in reference['alternative_groups']:
        primary_occurrences = [s for s in group['sources'] if s['anchor']['evidence_id'] in primary_eids]
        selected_occurrences = [s for s in group['sources'] if s['anchor']['evidence_id'] in selected_eids]
        if not selected_occurrences:
            continue
        pair = set(group['members'])
        preserved, malformed = [], []
        for index, relation in enumerate(relations, 1):
            ids = {entity_ids.get(eid) for eid in relation.get('object_ids', [])} - {None}
            raw_ids = {key for eid in relation.get('object_ids', []) for key in raw_entity_ids.get(eid, set())}
            if not (ids | raw_ids) & pair:
                continue
            evidence = relation.get('evidence', {})
            valid = (ids == pair and len(relation.get('object_ids', [])) == len(pair)
                     and relation.get('selection') == 'one_of'
                     and cite_valid(evidence, sources)
                     and '或' in evidence['quote'] and all(key in evidence['quote'] for key in pair))
            detail = {'relation_index': index, 'selection': relation.get('selection'),
                      'object_identifier_strings': sorted(ids),
                      'raw_object_identifier_strings': sorted(raw_ids),
                      'object_ids_are_atomic_and_source_bound': len(ids) == len(relation.get('object_ids', [])),
                      'polarity': relation.get('polarity')}
            (preserved if valid else malformed).append(detail)
        status = ('not_evaluated_no_stage_output' if not available else
                  'split_or_unconditional_or_incomplete' if malformed else
                  'one_of_preserved' if preserved else 'not_output')
        output.append({'group_id': group['group_id'], 'members': group['members'],
                       'primary_reference_occurrences': len(primary_occurrences),
                       'selected_reference_occurrences': len(selected_occurrences),
                       'scope': 'primary' if primary_occurrences else 'context_only',
                       'status': status, 'preserved_relations': preserved, 'malformed_relations': malformed,
                       'semantic_limit': 'Object ID set and 或 are checked; process role, polarity, and full relation semantics still need independent source review.'})
    return output


def stage_score(reference, entities, relations, sources, local, selected, available):
    rows = id_rows(entities, sources)
    lexical = {row['single_identifier'] for row in rows if row['single_identifier']}
    source_bound = {row['single_identifier'] for row in rows
                    if row['single_identifier'] and row['identifier_quote_valid']}
    duplicates = {key: count for key, count in Counter(
        row['single_identifier'] for row in rows if row['single_identifier']).items() if count > 1}
    name_groups = defaultdict(set)
    for identity in reference['equipment_identities']:
        for binding in identity['name_identifier_bindings']:
            name_groups[binding['raw_name']].add(identity['equipment_id'])
    same_name_merges = []
    for row in rows:
        represented = set(row['anchor_identifier_strings']) | set(row['raw_identifier_strings'])
        for name, ids in name_groups.items():
            if len(represented & ids) > 1:
                same_name_merges.append({'entity_id': row['local_entity_id'], 'reference_name': name,
                                         'represented_identifiers': sorted(represented & ids)})
    return {
        'stage_output_available': available, 'entities': rows,
        'entity_count': len(rows), 'unnumbered_entities': sum(row['raw_equipment_id'] == '' for row in rows),
        'malformed_numbered_entities': sum(bool(row['raw_equipment_id']) and not row['single_identifier'] for row in rows),
        'invalid_identifier_quotes': sum(bool(row['raw_equipment_id']) and not row['identifier_quote_valid'] for row in rows),
        'lexical_identifier_sets': set_score(lexical, selected, available),
        'source_bound_identifier_sets': set_score(source_bound, selected, available),
        'multi_identifier_merges': [row['local_entity_id'] for row in rows if row['multiple_identifiers_merged']],
        'anchor_identifier_conflicts': [row['local_entity_id'] for row in rows if row['anchor_identifier_disagrees']],
        'same_name_multi_identifier_merges_proven_by_explicit_ids': same_name_merges,
        'duplicate_local_entities_for_one_identifier': duplicates,
        'merge_limit': 'An omitted same-name ID alone is not evidence of an entity merge; it is reported as missing/unexamined, not a proven merge.',
        'alternatives': alternative_scores(reference, relations, rows, sources, local, available),
    }


def external_score(result, proposed_entities, final_entities, sources, search):
    returned = {row['key'] for row in search.get('candidates', [])}
    final_by_id = {entity['id']: entity for entity in final_entities}
    proposed = []
    for row in id_rows(proposed_entities, sources):
        if row['external_key']:
            proposed.append({'entity_id': row['local_entity_id'], 'key': row['external_key'],
                             'identifier': row['single_identifier'],
                             'key_returned_by_tool': row['external_key'] in returned,
                             'key_exactly_matches_source_identifier': row['identifier_quote_valid']
                              and row['external_key'] == row['single_identifier']})
    final = []
    for external in result.get('external_records', []):
        eid = external['entity_id']
        key = external.get('fields', {}).get('key')
        entity = final_by_id.get(eid, {})
        citation = entity.get('equipment_id', {})
        valid = cite_valid(citation, sources)
        final.append({'entity_id': eid, 'key': key, 'source_identifier': citation.get('quote'),
                      'accepted_entity_present': eid in final_by_id,
                      'key_returned_by_tool': key in returned,
                      'key_exactly_matches_source_identifier': valid and key == citation.get('quote'),
                      'entity_external_key_matches': key == entity.get('external_key'),
                      'link_record_key_matches': key == external.get('link', {}).get('external_record', {}).get('record_key')})
    return {'proposed': proposed, 'final': final,
            'invalid_final_links': [row for row in final if not all(row[k] for k in (
                'accepted_entity_present', 'key_returned_by_tool', 'key_exactly_matches_source_identifier',
                'entity_external_key_matches', 'link_record_key_matches'))]}


def score(root, reference_path):
    reference, manifest = read(reference_path), read(root / 'result.json')
    if manifest.get('status') != 'completed' or manifest.get('reference_is_input') is not False:
        raise ValueError('completed_reference_isolated_run_required')
    if (digest(root / 'ir.json') != reference['source']['ir_file_sha256']
            or digest(root / 'mock-equipment.json') != reference['source']['mock_file_sha256']):
        raise ValueError('reference_frozen_source_or_mock_mismatch')
    if any('reference' in key.lower() for key in manifest['input_hashes']):
        raise ValueError('reference_must_not_be_recognition_input')
    full_ids = {row['equipment_id'] for row in reference['equipment_identities']}
    full_mentions = {row['mention_id'] for row in reference['equipment_id_mentions']}
    cases = []
    for frozen_case in manifest['case_hashes']:
        directory = root / frozen_case['directory']
        local, sources, result = [read(directory / name) for name in ('case.json', 'sources.json', 'result.json')]
        search = read(directory / 'mock-search.json')
        selected = coverage(reference, local, sources)
        candidate_path = directory / 'candidates/proposal.json'
        proposed = read(candidate_path) if candidate_path.exists() else {'entities': [], 'relations': []}
        final_entities = [row['proposal'] for row in result.get('accepted', []) if row['kind'] == 'entity']
        final_relations = [row['proposal'] for row in result.get('accepted', []) if row['kind'] == 'relation']
        candidate = stage_score(reference, proposed['entities'], proposed['relations'], sources, local,
                                 selected, candidate_path.exists())
        final = stage_score(reference, final_entities, final_relations, sources, local,
                             selected, result['status'] == 'complete')
        external = external_score(result, proposed['entities'], final_entities, sources, search)
        unknown = []
        for missing in reference['unknown_archive_devices']:
            identifier = missing['equipment_id']
            surrogate = missing['forbidden_name_only_substitution']['equipment_id']
            unknown.append({
                'equipment_id': identifier, 'primary_selected': identifier in selected['primary']['equipment_ids'],
                'context_selected': identifier in selected['context']['equipment_ids'],
                'candidate_stage_available': candidate_path.exists(), 'final_stage_complete': result['status'] == 'complete',
                'candidate_retained': identifier in candidate['lexical_identifier_sets']['predicted_ids'],
                'final_retained': identifier in final['lexical_identifier_sets']['predicted_ids'],
                'candidate_wrong_surrogate_present': surrogate in candidate['lexical_identifier_sets']['predicted_ids'],
                'final_wrong_surrogate_present': surrogate in final['lexical_identifier_sets']['predicted_ids'],
                'unexpected_final_external_links': [row for row in external['final'] if row['source_identifier'] == identifier],
                'limit': 'A surrogate ID appearing while the source unknown ID is omitted is a replacement warning; exact role/identity substitution requires source-only review.',
            })
        cases.append({'arm': frozen_case['arm'], 'scope_id': frozen_case['scope_id'],
                      'record_id': frozen_case['record_id'], 'run_status': result['status'],
                      'source_reference_coverage': selected, 'candidate': candidate, 'final': final,
                      'external_links': external, 'unknown_archive_devices': unknown})
    arms = {}
    for arm in ARMS:
        selected_cases = [row for row in cases if row['arm'] == arm]
        coverage_summary = {}
        for role in ('primary', 'context'):
            ids = {i for row in selected_cases for i in row['source_reference_coverage'][role]['equipment_ids']}
            mentions = {i for row in selected_cases for i in row['source_reference_coverage'][role]['mention_ids']}
            coverage_summary[role] = {'equipment_ids': sorted(ids), 'full_report_id_denominator': len(full_ids),
                                      'full_report_id_coverage': ratio(len(ids), len(full_ids)),
                                      'mention_occurrences': len(mentions), 'full_report_mention_denominator': len(full_mentions),
                                      'full_report_mention_coverage': ratio(len(mentions), len(full_mentions))}
        selected_ids = set(coverage_summary['primary']['equipment_ids']) | set(coverage_summary['context']['equipment_ids'])
        primary_ids = set(coverage_summary['primary']['equipment_ids'])
        stage_summary = {}
        for stage in ('candidate', 'final'):
            predictions = {i for row in selected_cases for i in row[stage]['source_bound_identifier_sets']['predicted_ids']}
            available = [row for row in selected_cases if row[stage]['stage_output_available']]
            evaluated_primary = {i for row in available for i in row['source_reference_coverage']['primary']['equipment_ids']}
            stage_summary[stage] = {
                'cases_with_output': len(available), 'total_selected_cases': len(selected_cases),
                'source_bound_predicted_ids': sorted(predictions),
                'selected_primary_reference_denominator': len(primary_ids),
                'selected_all_reference_denominator': len(selected_ids),
                'primary_output_id_coverage_fraction_over_all_selected': ratio(len(predictions & primary_ids), len(primary_ids)),
                'primary_ids_without_stage_evaluation': sorted(primary_ids - evaluated_primary),
                'outside_selected_reference_ids': sorted(predictions - selected_ids),
                'multi_identifier_merge_count': sum(len(row[stage]['multi_identifier_merges']) for row in selected_cases),
                'anchor_identifier_conflict_count': sum(len(row[stage]['anchor_identifier_conflicts']) for row in selected_cases),
                'primary_alternative_statuses': dict(Counter(group['status'] for row in selected_cases
                    for group in row[stage]['alternatives'] if group['scope'] == 'primary')),
                'context_alternative_statuses': dict(Counter(group['status'] for row in selected_cases
                    for group in row[stage]['alternatives'] if group['scope'] == 'context_only')),
                'meaning': 'Output coverage over all selected reference IDs, not entity semantic accuracy or recall of the full report. Failed cases remain in coverage denominators.',
            }
        arms[arm] = {'selected_source_coverage': coverage_summary,
                     'selected_ids_union': sorted(selected_ids), 'unselected_report_ids': sorted(full_ids - selected_ids),
                     'stages': stage_summary,
                     'invalid_final_external_links': sum(len(row['external_links']['invalid_final_links']) for row in selected_cases)}
    by_key = {(row['arm'], row['record_id']): row for row in cases}
    paired = []
    for (arm, rid), left in by_key.items():
        if arm == 'E0':
            right = by_key.get(('E1', rid))
            paired.append({'record_id': rid, 'same_reference_source_coverage': right is not None
                           and left['source_reference_coverage'] == right['source_reference_coverage'],
                           'E0': {stage: left[stage]['source_bound_identifier_sets'] for stage in ('candidate', 'final')},
                           'E1': {stage: right[stage]['source_bound_identifier_sets'] for stage in ('candidate', 'final')} if right else None})
    discrepancies = [{
        'equipment_id': row['equipment_id'], 'field': row['field'],
        'document_value': row['document']['raw_value'], 'mock_value': row['mock']['value'],
        'classification': 'source_value_discrepancy_not_proven_strict_semantic_contradiction',
        'scoring': 'Not scored as a mapped document property; this scope permits only ID/name/model. Observational conflict interpretation requires source-only review.',
    } for row in reference['attribute_value_discrepancies']]
    return {'run_root': str(root), 'reference_path': str(reference_path), 'reference_sha256': digest(reference_path),
            'script_sha256': digest(Path(__file__)), 'reference_authority': reference['authority'],
            'summary': {'full_report_reference_ids': sorted(full_ids), 'full_report_reference_id_count': len(full_ids),
                        'full_report_reference_mentions': len(full_mentions), 'cases': len(cases),
                        'source_only_semantic_review': 'pending_separate_assistant_review'},
            'arms': arms, 'cases': cases, 'paired_E0_E1': paired,
            'material_discrepancies': discrepancies,
            'limits': [
                'Reference created before predictions; no verification/review output defines gold labels.',
                'Exact identifier strings and source quote replay do not prove equipment class, identity scope, process role, or full relationship semantics.',
                'Per-case primary and context coverage are separate; an unselected or unproduced ID is never labeled negated.',
                'Empty-denominator precision/recall is null; stages without a parsed candidate or completed final result are not evaluated.',
                'E0/E1 same-source comparison is paired; E2 changes selected records and is reported independently. No result is treated as an improvement over D.',
                'Same-name omission alone cannot prove an erroneous identity merge.',
                'This assistant reference is not expert gold and is not a full graph gold set.',
            ]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run_root', type=Path)
    parser.add_argument('reference', type=Path)
    parser.add_argument('--output', type=Path, default=Path('/tmp/cmc-mock-validation-20260916/reference-score.json'))
    args = parser.parse_args()
    output = score(args.run_root.resolve(), args.reference.resolve())
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(output['summary'], ensure_ascii=False))
    print(args.output)


if __name__ == '__main__':
    main()
