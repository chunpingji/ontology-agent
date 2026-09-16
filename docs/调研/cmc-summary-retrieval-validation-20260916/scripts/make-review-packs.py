"""Build source-only review packs; excludes Qwen verification and scoring references."""
import hashlib
import json
import sys
from pathlib import Path

root, output = map(Path, sys.argv[1:])
output.mkdir(parents=True, exist_ok=True)
for scope in sorted((root/'D').iterdir()):
    files = {'source': 'sources.json', 'cards':'subject-cards.json',
             'candidate_proposal':'candidates/proposal.json'}
    if not all((scope/name).exists() for name in files.values()):
        continue
    data = {key: json.loads((scope/name).read_text()) for key,name in files.items()}
    index=[]
    for sid, subject in data['candidate_proposal']['subjects'].items():
        for kind in ('attributes','relations'):
            for field, values in subject[kind].items():
                for n, proposal in enumerate(values):
                    index.append({'id':f'c{len(index)+1}', 'path':f'subjects.{sid}.{kind}.{field}[{n}]',
                                  'subject_id':sid,'field':field,
                                  'kind':'property' if kind=='attributes' else 'relation',
                                  'proposal':proposal})
    data.update(arm='D',scope_id=scope.name,claim_index=index,
                observations=[{'id':f'o{i+1}','proposal':p} for i,p in enumerate(data['candidate_proposal']['observations'])],
                review_boundary='Source, cards and original generation only; no verification, checker verdicts or references.',
                source_artifacts={name:hashlib.sha256((scope/name).read_bytes()).hexdigest() for name in files.values()})
    path=output/(scope.name+'.json')
    encoded=json.dumps(data,ensure_ascii=False,indent=2)+'\n'
    if path.exists():
        assert path.read_text()==encoded, f'Frozen review input changed: {scope.name}'
    else:
        path.write_text(encoded)
        print(scope.name,len(index),len(data['observations']),flush=True)
