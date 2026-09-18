"""Match source-only independent reviews to exact frozen targets and destinations."""
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

root, packets, output = map(Path, sys.argv[1:4])
review_paths = list(map(Path, sys.argv[4:]))

def read(path):
    return json.loads(path.read_text())

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

manifest = read(root/'result.json')
manifest_cases = {(c['arm'], c['scope_id']): c for c in manifest['results']}
assert len(manifest_cases) == len(manifest['results'])

rows, coverage, seen = [], [], set()
for review_path in review_paths:
    for review in read(review_path)['reviews']:
        arm, scope = review['arm'], review['scope_id']
        assert (arm, scope) not in seen
        seen.add((arm, scope))
        path = packets / (arm + '-' + scope + '.json')
        assert review['packet_sha256'] == sha(path)
        packet = read(path)
        assert (packet['arm'], packet['scope_id']) == (arm, scope)
        directory = root / arm / scope
        frozen = read(directory / 'frozen-candidates.json')
        result = read(directory / 'result.json')
        assert result == manifest_cases[(arm, scope)]
        originals = {t['id']: t for t in packet['targets']}
        targets = {t['id']: t for t in frozen['targets']}
        reviews = {t['id']: t for t in review['targets']}
        assert len(originals) == len(packet['targets'])
        assert len(targets) == len(frozen['targets'])
        assert len(reviews) == len(review['targets'])
        assert originals == targets and set(reviews) == set(originals)
        actual_paths = {'proposal': directory/'candidates/proposal.json',
                        'sources': directory/'sources.json', 'case': directory/'case.json',
                        'mock_search': directory/'mock-search.json', 'cards': root/'scope-cards.json'}
        assert all(sha(actual_paths[k]) == v for k, v in packet['artifact_hashes'].items())
        destinations = {}
        for dest in ('accepted', 'unresolved', 'rejected'):
            for row in result.get(dest, []):
                if row['kind'] == 'external_link':
                    continue
                assert row['id'] not in destinations
                destinations[row['id']] = (dest, row)
        assert set(destinations) <= set(originals)
        if result['status'] == 'complete':
            assert set(destinations) == set(originals)
        for cid, item in reviews.items():
            assert item['kind'] == originals[cid]['kind']
            assert item['proposal'] == originals[cid]['proposal']
            dest, final = destinations.get(cid, ('not_finalized', {}))
            if dest == 'not_finalized':
                assert result['status'] == 'failed' and result['failure_stage'] == 'verification'
            else:
                assert final['proposal'] == item['proposal']
                assert final['kind'] == item['kind']
            overall = item['overall_verdict']
            assert overall in {'supported', 'unsupported', 'undetermined'}
            content = item.get('content_verdict', overall)
            assert content in {'supported', 'unsupported', 'undetermined'}
            rows.append({'arm': arm, 'scope_id': scope, 'id': cid, 'kind': item['kind'],
                         'program_destination': dest, 'overall_verdict': overall,
                         'content_verdict': content,
                         'program_model_verdict': final.get('verdict', {}).get('verdict'),
                         'reason': item['reason'], 'program_issues': final.get('issues', []),
                         'entity_dimensions': {k: item[k] for k in (
                             'role_verdict', 'identifier_owner_verdict', 'external_link_verdict')
                             if k in item}})
        coverage.append({'arm': arm, 'scope_id': scope, 'targets': len(reviews),
                         'packet_sha256': sha(path)})

def counts(data):
    return {'count': len(data), 'overall': dict(Counter(x['overall_verdict'] for x in data)),
            'content': dict(Counter(x['content_verdict'] for x in data)),
            'overall_supported_count': sum(x['overall_verdict'] == 'supported' for x in data),
            'content_supported_count': sum(x['content_verdict'] == 'supported' for x in data)}

data = {'review_type': 'Independent assistant source review; not expert gold',
        'exact_candidate_matching_passed': True, 'artifact_sha_matching_passed': True,
        'manifest_scope_results_matching_passed': True, 'coverage': coverage,
        'review_file_hashes': {str(p): sha(p) for p in review_paths},
        'counting_policy': 'All reviewed targets stay in their destination denominator. '
            'Independent unsupported/undetermined are never dropped or counted as supported. '
            'Overall judges the exact proposal including citations, identity and binding; '
            'content judges underlying content separately. Not expert gold or recall.',
        'unreviewed': [{'arm': c['arm'], 'scope_id': c['scope_id'],
                       'status': c['status'], 'reason': 'no independent review; never quality pass'}
                      for c in manifest['results'] if (c['arm'], c['scope_id']) not in seen],
        'by_arm': {arm: {'all': counts([r for r in rows if r['arm']==arm]),
                        'accepted': counts([r for r in rows if r['arm']==arm
                                            and r['program_destination']=='accepted']),
                        'unresolved': counts([r for r in rows if r['arm']==arm
                                              and r['program_destination']=='unresolved']),
                        'rejected': counts([r for r in rows if r['arm']==arm
                                            and r['program_destination']=='rejected']),
                        'not_finalized': counts([r for r in rows if r['arm']==arm
                                            and r['program_destination']=='not_finalized']),
                        'accepted_by_kind': {kind: counts([r for r in rows if r['arm']==arm
                            and r['program_destination']=='accepted' and r['kind']==kind])
                            for kind in ('entity','property','relation','observation')}}
                   for arm in ('E0','E1','E2')},
        'by_scope': [{'arm': arm, 'scope_id': scope,
                      'status': manifest_cases[(arm, scope)]['status'],
                      **{dest: counts([r for r in rows if r['arm'] == arm
                          and r['scope_id'] == scope and r['program_destination'] == dest])
                         for dest in ('accepted', 'unresolved', 'rejected', 'not_finalized')}}
                     for arm, scope in sorted(seen)],
        'accepted_not_independently_supported': [r for r in rows
            if r['program_destination'] == 'accepted' and r['overall_verdict'] != 'supported'],
        'records': rows}
output.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({k:v for k,v in data.items() if k!='records'},ensure_ascii=False,indent=2))
