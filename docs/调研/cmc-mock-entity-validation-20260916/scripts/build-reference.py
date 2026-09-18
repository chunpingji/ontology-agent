"""Freeze assistant-reviewed equipment references from source IR and static Mock only.

No model predictions, retrieval selections, or experiment conclusions are read.
The hand-reviewed row/mention annotations below are evaluation references only.
"""

from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import re
import sys

ROOT = Path('/opt/dev/chen/ontology-agent')
sys.path.insert(0, str(ROOT / 'backend'))
from app.services.extraction.document_ir import DocumentIR

IR_PATH = ROOT / 'output/schema-card-qwen-cmc-summary-retrieval-20260916/completed/ir.json'
ARCHIVE_PATH = ROOT / 'backend/app/resources/equipment_archive.json'
OUT = Path('/tmp/cmc-mock-validation-20260916/reference.json')
ir = DocumentIR.model_validate_json(IR_PATH.read_text())
units = ir.evidence_units
archive = json.loads(ARCHIVE_PATH.read_text())
assert len(units) == 447
archive_by_id = {}
for index, row in enumerate(archive):
    assert row['equipment_id'] not in archive_by_id
    archive_by_id[row['equipment_id']] = (index, row)


def evidence(index, quote=None):
    unit = units[index]
    quote = unit.text if quote is None else quote
    assert quote and unit.text.count(quote) == 1, (index, quote)
    start = unit.text.index(quote)
    anchor = ir.anchor(unit.evidence_id, start, start + len(quote)).model_dump(mode='json')
    assert ir.resolve(anchor) == quote
    return {'unit_index': index, 'quote': quote, 'anchor': anchor}


def mock(identifier, key=None):
    index, row = archive_by_id[identifier]
    return {'source': 'static_mock_archive', 'json_pointer': f'/{index}' + (f'/{key}' if key else ''),
            'equipment_id': identifier, 'value': row[key] if key else row.copy()}


# Table 6 binds each equipment field by its physical row and original header.
row_annotations = [
    ('req-row-1', [181, 182, 183, 184], ['RE64611'], 'planned_required_equipment'),
    ('req-row-2', [194, 195, 196, 197], ['PF64216', 'PF64616'], 'alternative'),
    ('req-row-3', [207, 208, 209, 210], ['RE64613'], 'planned_required_equipment'),
    ('req-row-4', [222, 223, 224, 225], ['CT64610'], 'planned_required_equipment'),
    ('req-row-5', [235, 236, 237, 238], ['DE64603'], 'planned_required_equipment'),
]
headers = {'equipmentName': 167, 'equipmentID': 168, 'modelSpecification': 169, 'material': 170}
row_fields = ['equipmentName', 'equipmentID', 'modelSpecification', 'material']
rows = []
for rid, indices, identifiers, modality in row_annotations:
    row_index = units[indices[0]].row_index
    assert all(units[i].table_path == ['table:6'] and units[i].row_index == row_index for i in indices)
    rows.append({
        'reference_id': rid, 'table_path': ['table:6'], 'row_index': row_index,
        'identifier_members': identifiers, 'modality': modality,
        'alternative_group_id': 'filter-choice' if modality == 'alternative' else None,
        'document_fields': {field: {'raw_value': units[i].text, 'source': evidence(i),
                                   'field_header': evidence(headers[field])}
                            for field, i in zip(row_fields, indices, strict=True)},
        'scope_note': ('Both IDs are mentioned as choices; the document does not select a winner '
                       'or assert that both filters were actually used.' if modality == 'alternative'
                       else 'Equipment requirement/process plan, not proof of completed physical use.'),
    })

identifier_pattern = re.compile(r'(?<![A-Z0-9])(?:RE|PF|CT|DE)[0-9]{5}(?![A-Z0-9])')
mentions = []
for index, unit in enumerate(units):
    for match in identifier_pattern.finditer(unit.text):
        mentions.append({
            'mention_id': f'equipment-id-{index}-{match.start()}', 'equipment_id': match.group(),
            'source': evidence(index, match.group()), 'context': evidence(index),
            'reference_scope': ('process' if unit.table_path == ['table:0'] else
                                'cleaning' if unit.table_path == ['table:5'] else 'requirements'),
            'alternative_group_id': 'filter-choice' if match.group().startswith('PF') else None,
        })
identifiers = sorted({row['equipment_id'] for row in mentions})
assert identifiers == ['CT64610', 'DE64603', 'PF64216', 'PF64616', 'RE64611', 'RE64613']
assert len(mentions) == 18

# Name-to-ID evidence from the same phrase, same physical cell, or explicit table columns.
name_annotations = {
    'RE64611': [(67, '200L反应釜', 67), (134, '200L反应釜', 135), (181, '200L反应釜', 182)],
    'RE64613': [(69, '500L结晶釜', 69), (141, '500L反应釜', 142), (207, '500L反应釜', 208)],
    'CT64610': [(77, '离心机', 77), (148, '离心机', 149), (222, '离心机', 223)],
    'DE64603': [(83, '真空干燥箱', 83), (155, '真空干燥箱', 156), (235, '真空干燥箱', 236)],
    'PF64216': [(135, '压滤器', 135), (194, '钛棒过滤器', 195)],
    'PF64616': [(135, '压滤器', 135), (194, '钛棒过滤器', 195)],
}
identity_refs = []
for identifier in identifiers:
    req = next(row for row in rows if identifier in row['identifier_members'])
    bindings = []
    for name_index, name, id_index in name_annotations[identifier]:
        source, identity = units[name_index], units[id_index]
        assert source.table_path == identity.table_path and source.row_index == identity.row_index
        bindings.append({
            'raw_name': name, 'name_source': evidence(name_index, name),
            'identifier_source': evidence(id_index, identifier),
            'binding_basis': ('same_source_phrase' if name_index == id_index else
                              'same_physical_cell_fragments' if source.source_cell_id == identity.source_cell_id
                              else 'same_table_row_explicit_name_and_id_headers'),
            'qualifier': 'alternative' if identifier.startswith('PF') else 'planned_required_equipment',
        })
    identity_refs.append({
        'equipment_id': identifier, 'identity_scope': 'same_report_equipment_identifier',
        'mention_ids': [m['mention_id'] for m in mentions if m['equipment_id'] == identifier],
        'name_identifier_bindings': bindings,
        'requirement_row_ref': req['reference_id'],
        'archive_lookup': {'status': 'exact_match' if identifier in archive_by_id else 'not_found',
                           'match_count': 1 if identifier in archive_by_id else 0,
                           'entry': mock(identifier) if identifier in archive_by_id else None},
        'identity_limit': 'Exact archive lookup does not prove actual use, erase alternative choice, or grant document evidence to Mock-only attributes.',
    })

alternative = {
    'group_id': 'filter-choice', 'operator': 'alternative', 'members': ['PF64216', 'PF64616'],
    'selected_member': None, 'observed_actual_use': 'not_established',
    'sources': [evidence(i, 'PF64216或PF64616') for i in [69, 135, 195]],
    'required_behavior': 'Preserve the complete 或 condition in all three contexts; neither join IDs into a new identity nor turn the two possibilities into two unconditional used-device facts.',
}

comparisons = []
for identity in identity_refs:
    identifier = identity['equipment_id']
    if identifier not in archive_by_id:
        continue
    row = next(row for row in rows if identifier in row['identifier_members'])
    for field, key in [('equipmentName', 'name'), ('modelSpecification', 'specification'), ('material', 'material')]:
        doc_field = row['document_fields'][field]
        archive_value = archive_by_id[identifier][1][key]
        if doc_field['raw_value'] == archive_value:
            classification = 'exact_value_agreement'
        elif field == 'equipmentName' and identifier.startswith('RE'):
            classification = 'different_compatible_descriptions_not_a_conflict'
        elif field == 'material' and identifier.startswith('PF'):
            classification = 'mock_more_specific_grade_not_a_conflict'
        else:
            assert identifier == 'CT64610' and field == 'material'
            classification = 'source_value_discrepancy_requires_review'
        comparisons.append({
            'equipment_id': identifier, 'field': field, 'classification': classification,
            'document': doc_field, 'mock': mock(identifier, key),
            'qualifier': 'alternative' if identifier.startswith('PF') else 'planned_required_equipment',
        })

conflicts = [row for row in comparisons if row['classification'] == 'source_value_discrepancy_requires_review']
assert len(conflicts) == 1
conflicts[0]['interpretation'] = ('Document states 不锈钢衬HALAR while Mock records 316L不锈钢. '
    'This is an attribute value discrepancy; the Mock does not explicitly negate a lining, so strict physical incompatibility is not proven. '
    'Keep both sourced values and flag the difference; do not overwrite either or call either the verified real-world material.')

output = {
    'reference_version': 'cmc-equipment-assistant-reference-v1',
    'authority': 'Hand-reviewed assistant reference, not expert-adjudicated gold and not a full graph gold set.',
    'blindness': 'Prepared from the full IR and static Mock archive before this round of predictions. No future prediction or retrieval-selection artifact was read.',
    'recognition_input_policy': 'Evaluation only. This reference must not enter prompts, retrieval selection, tools, or recognition inputs.',
    'source': {
        'document_ref': 'upload-23c872fb-3ab1-41de-a705-dd4b162dfa09',
        'ir_path': str(IR_PATH), 'ir_file_sha256': sha256(IR_PATH.read_bytes()).hexdigest(),
        **{key: getattr(ir, key) for key in ['analysis_id', 'document_hash', 'structure_hash', 'title']},
        'all_ir_units_reviewed': 447, 'raw_text_characters': sum(len(u.text) for u in units),
        'mock_path': str(ARCHIVE_PATH), 'mock_file_sha256': sha256(ARCHIVE_PATH.read_bytes()).hexdigest(),
        'mock_entry_count': len(archive), 'mock_unique_identifier_count': len(archive_by_id),
    },
    'summary': {
        'distinct_explicit_equipment_ids': len(identifiers),
        'identifier_mention_occurrences': len(mentions),
        'mention_occurrences_by_context': dict(Counter(row['reference_scope'] for row in mentions)),
        'equipment_requirement_rows': len(rows), 'alternative_groups': 1,
        'archive_exact_matches': 5, 'archive_not_found': ['DE64603'],
        'actual_attribute_value_discrepancies': 1,
        'strict_semantic_attribute_contradictions_proven': 0,
    },
    'equipment_id_mentions': mentions,
    'equipment_identities': identity_refs,
    'requirement_rows': rows,
    'alternative_groups': [alternative],
    'document_mock_comparisons': comparisons,
    'attribute_value_discrepancies': conflicts,
    'unknown_archive_devices': [{
        'equipment_id': 'DE64603', 'document_source': evidence(236),
        'expected_lookup_status': 'not_found', 'lookup_scope': 'This frozen 211-entry static archive only.',
        'not_proven': 'Non-existence in the real world; invalidity of the source document identifier.',
        'forbidden_name_only_substitution': mock('DE64203'),
        'reason': 'DE64203 shares the vacuum-dryer name and 24盘 specification but has a different explicit identifier.',
    }],
    'non_identifier_boundaries': [
        {'kind': 'consumable_not_equipment_id', 'sources': [evidence(273), evidence(274), evidence(278)],
         'note': '囊式滤芯 / WSF-10-SLHPF0022P is in the materials table as 反应耗材; do not equate its model to a PF equipment ID.'},
        {'kind': 'consumable_not_equipment_id', 'sources': [evidence(280), evidence(281), evidence(285)],
         'note': '钛棒滤芯 / TIC-0045L10SP is a consumable, not the 钛棒过滤器 equipment identity.'},
        {'kind': 'unnumbered_auxiliary', 'sources': [evidence(142, '穿墙管道')],
         'note': 'A pipe is mentioned but has no explicit equipment ID; reference does not invent an archive match.'},
        {'kind': 'analytical_method_not_identified_instrument', 'sources': [evidence(80), evidence(81)],
         'note': 'HPLC is a testing label in this context and does not identify a particular archive instrument.'},
    ],
    'review_plan': [
        'Freeze this reference and archive hash before predictions; keep reference inaccessible to retrieval/prompt construction.',
        'Replay each predicted quote against the same IR, then classify mention identity, field role, table-row ownership, and alternative qualifiers independently.',
        'Score mention-level identifier recall separately from distinct identifier recall; report selected-source coverage separately from model extraction success.',
        'Evaluate the five requirement rows and three repeated alternative contexts; do not treat six identifier strings as six unconditional usage relations.',
        'For identity lookup, compare exact ID status with the five matches and DE64603 not_found. Name/spec similarity must not repair DE64603 into DE64203.',
        'Compare document and Mock attributes with source authority separated. Flag CT64610 material discrepancy; treat compatible names and more-specific steel grades separately.',
        'Evaluate text-property datatype/SHACL representation only after evidence and semantic checks. No numeric calibration indicator exists for these xsd:string slots.',
        'Keep technical failures and unattempted candidates in coverage reporting; do not calculate end-to-end quality only on successful outputs.',
    ],
}

def check_anchors(obj):
    if isinstance(obj, dict):
        if 'anchor' in obj and 'quote' in obj:
            assert ir.resolve(obj['anchor']) == obj['quote']
        for value in obj.values():
            check_anchors(value)
    elif isinstance(obj, list):
        for value in obj:
            check_anchors(value)

check_anchors(output)
OUT.write_text(json.dumps(output, ensure_ascii=False, indent=2) + '\n')
print(OUT)
print(json.dumps(output['summary'], ensure_ascii=False))
