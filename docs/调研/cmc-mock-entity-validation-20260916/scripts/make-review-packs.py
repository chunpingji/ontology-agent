"""Create immutable source-only packets; never read checker or Qwen verification."""
import hashlib
import json
import sys
from pathlib import Path

root, out = map(Path, sys.argv[1:])
out.mkdir(parents=True, exist_ok=True)

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

for arm in ('E0', 'E1', 'E2'):
    for directory in sorted((root / arm).iterdir()):
        proposal_path = directory / 'candidates/proposal.json'
        if not proposal_path.is_file():
            continue
        files = {'proposal': proposal_path, 'sources': directory / 'sources.json',
                 'case': directory / 'case.json', 'mock_search': directory / 'mock-search.json',
                 'cards': root / 'scope-cards.json'}
        packet = {key: json.loads(path.read_text()) for key, path in files.items()}
        targets = []
        for entity in packet['proposal']['entities']:
            targets.append({'id': entity['id'], 'kind': 'entity', 'proposal': entity})
            for i, attr in enumerate(entity['attributes'], 1):
                targets.append({'id': entity['id'] + f'.a{i}', 'kind': 'property',
                                'entity_id': entity['id'], 'proposal': attr})
        for group, prefix, kind in [('relations', 'r', 'relation'),
                                    ('observations', 'o', 'observation')]:
            for i, item in enumerate(packet['proposal'][group], 1):
                targets.append({'id': prefix + str(i), 'kind': kind, 'proposal': item})
        packet.update(arm=arm, scope_id=directory.name, targets=targets,
                      artifact_hashes={key: digest(path) for key, path in files.items()},
                      review_boundary='Only original source, active cards, candidate generation '
                                      'and actually offered Mock. No checker, verification or result.')
        encoded = json.dumps(packet, ensure_ascii=False, indent=2) + '\n'
        path = out / (arm + '-' + directory.name + '.json')
        if path.exists():
            assert path.read_text() == encoded, 'review packet changed'
        else:
            path.write_text(encoded)
            print(path.name, len(targets), flush=True)
