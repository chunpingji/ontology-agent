"""Read only saved request/input artefacts; do not infer model failure causes."""

import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path('/opt/dev/chen/ontology-agent')
RUNS = {
    'C': ROOT / 'output/schema-card-qwen-cmc-gliner25-skos-20260916/completed',
    'D': ROOT / 'output/schema-card-qwen-cmc-summary-retrieval-20260916/completed',
}
OUT = Path('/tmp/cmc-summary-retrieval-20260916/request-footprint.json')


def read(path):
    return json.loads(path.read_text())


def stat_request(path):
    request = read(path)
    messages = []
    inputs = []
    for message in request['messages']:
        content = message['content']
        assert isinstance(content, str), 'Character measure requires string message contents'
        messages.append({'role': message['role'], 'content_characters': len(content)})
        try:
            parsed = json.loads(content)
        except ValueError:
            continue
        if isinstance(parsed, dict) and 'input' in parsed:
            inputs.append(parsed['input'])
    assert len(inputs) == 1
    supplied = inputs[0]
    ner = supplied['tool_results']['propose_mentions']
    return {
        'path': str(path.relative_to(ROOT)),
        'request_json_file_bytes': path.stat().st_size,
        'messages': messages,
        'messages_content_characters': sum(row['content_characters'] for row in messages),
        'sources_in_request': len(supplied['source']),
        'source_text_characters': sum(len(row['text']) for row in supplied['source']),
        'ner_spans_in_request': len(ner['spans']),
        'ner_groups_in_request': len(ner['groups']) if 'groups' in ner else None,
    }


def stat_scope(path):
    requests = [stat_request(p) for p in sorted((path / 'candidates').glob('*-request.json'))]
    tools = read(path / 'tools.json')
    sources = read(path / 'sources.json')
    return {
        'saved_candidate_request_count': len(requests),
        'requests': requests,
        'saved_source_count': len(sources),
        'saved_source_text_characters': sum(len(row['text']) for row in sources.values()),
        'saved_ner_span_count': len(tools['propose_mentions']['spans']),
        'saved_ner_group_count': (len(tools['propose_mentions']['groups'])
                                  if 'groups' in tools['propose_mentions'] else None),
    }


arms = {arm: {p.name: stat_scope(p) for p in sorted((root / arm).iterdir()) if p.is_dir()}
        for arm, root in RUNS.items()}
paired = {}
keys = ('request_json_file_bytes', 'messages_content_characters', 'sources_in_request',
        'source_text_characters', 'ner_spans_in_request')
for scope, d in arms['D'].items():
    c = arms['C'][scope]
    if not d['requests'] or not c['requests']:
        paired[scope] = {'comparison_status': 'D_request_not_saved' if not d['requests']
                         else 'C_request_not_saved'}
        continue
    assert len(d['requests']) == len(c['requests']) == 1
    dr, cr = d['requests'][0], c['requests'][0]
    paired[scope] = {'comparison_status': 'paired_saved_requests', 'metrics': {
        key: {'C': cr[key], 'D': dr[key], 'delta': dr[key] - cr[key],
              'D_over_C': dr[key] / cr[key] if cr[key] else None}
        for key in keys
    }}

output = {
    'generated_at_utc': datetime.now(timezone.utc).isoformat(),
    'scope': 'Saved candidate HTTP requests only; plan/repair/usage/result conclusions not read.',
    'definitions': {
        'request_json_file_bytes': 'Exact stored JSON file byte count, including serialization.',
        'messages_content_characters': 'Unicode character count of decoded message content strings.',
        'ner_spans_in_request': 'Length of input.tool_results.propose_mentions.spans.',
        'sources_in_request': 'Length of input.source; actual supplied evidence units.',
        'limitations': [
            'Character and byte counts are not tokenizer token counts.',
            'HTTP400 cause is not inferred; error response bodies are unavailable.',
            'D completed is the completed artifact snapshot; unsaved usage is not zero-cost usage.',
        ],
    },
    'roots': {arm: str(root.relative_to(ROOT)) for arm, root in RUNS.items()},
    'arms': arms,
    'C_D_comparison': paired,
}
OUT.write_text(json.dumps(output, ensure_ascii=False, indent=2) + '\n')
print(OUT)
for scope, row in paired.items():
    if row['comparison_status'] != 'paired_saved_requests':
        print(scope, row['comparison_status'])
        continue
    values = row['metrics']
    print(scope, 'C/D bytes', values['request_json_file_bytes']['C'],
          values['request_json_file_bytes']['D'], 'C/D chars',
          values['messages_content_characters']['C'], values['messages_content_characters']['D'],
          'C/D spans', values['ner_spans_in_request']['C'], values['ner_spans_in_request']['D'],
          'C/D sources', values['sources_in_request']['C'], values['sources_in_request']['D'])
