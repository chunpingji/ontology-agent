import hashlib
import json
import os
import sys
from pathlib import Path

BACKEND = Path('/opt/dev/chen/ontology-agent/backend')
DIRECTORY = Path(__file__).parent
RESULTS = DIRECTORY / 'results'
os.environ['DATABASE_URL'] = f'sqlite:///{RESULTS / "scheduler.sqlite3"}'
sys.path.insert(0, str(BACKEND))

from app.services.extraction.document_ir import DocumentIR
from app.services.extraction.ontology_guided.contracts import (
    EdgeSpec, GraphNode, OntologySnapshot, RangeClass, SubjectRef, VersionedRef,
)
from app.services.extraction.ontology_guided.records import RecordIndex
from app.services.extraction.ontology_guided.retrieval_query import build_subject_queries


def read(name):
    return json.loads((RESULTS / name).read_text())


baseline = read('E0-queries.json')[0]
payload = json.loads(baseline['model_text'])
subject = SubjectRef.model_validate(baseline['subject_ref'])
node = GraphNode(entity_id=subject.entity_id, revision=subject.revision,
                 class_iri=subject.class_iri, class_label=payload['subject_class_label'],
                 label='synthetic.docx', root=True, root_origin='user_specified')
predicate = EdgeSpec(
    iri=payload['predicate']['iri'], label=payload['predicate']['label'],
    description=payload['predicate']['definition'], declared_by=[subject.class_iri],
    range_class_iris=['https://ontology.pharma-gmp.cn/slpra/equipment/Equipment'],
    range_classes=[RangeClass(iri=item['iri'],label=item['label'],
                              description=item['definition'])
                   for item in payload['allowed_object_types']])
index = RecordIndex(DocumentIR.model_validate(read('document_ir.json')))
comparisons = []
for variant in ('E0', 'E1', 'E2'):
    ontology = None if variant == 'E0' else OntologySnapshot.model_validate(
        read(f'{variant}-ontology_snapshot.json'))
    current = build_subject_queries(
        subject=subject, subject_node=node, predicate=predicate, index=index,
        run_fingerprint=baseline['run_fingerprint'],
        root_ref=VersionedRef(id=subject.entity_id, revision=subject.revision),
        root_class_iri=subject.class_iri, ontology=ontology)
    for old, new in zip(read(f'{variant}-queries.json'), current, strict=True):
        comparisons.append({
            'variant':variant, 'intent':new.retrieval_intent,
            'model_text_identical':old['model_text']==new.model_text,
            'query_dependency_hash_identical':old['query_dependency_hash']==new.query_dependency_hash,
            'query_id_identical':old['query_id']==new.query_id,
            'full_query_identical':old==new.model_dump(mode='json'),
        })
report = read('report.json')
result = {
    'comparisons':comparisons,
    'measured_code_sha256':report['code_sha256'],
    'current_code_sha256':{
        relative:hashlib.sha256((BACKEND/relative).read_bytes()).hexdigest()
        for relative in report['code_sha256']},
    'scope':'No model calls; regenerate frozen sample queries with current builder.',
}
(DIRECTORY/'current_query_compatibility.json').write_text(
    json.dumps(result, ensure_ascii=False, indent=2)+'\n')
print(json.dumps(comparisons, ensure_ascii=False))
raise SystemExit(0 if all(item['full_query_identical'] for item in comparisons) else 1)
