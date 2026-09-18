import json
from sqlalchemy import text
from app.db import engine
from app.evaluation.quality_guided_variant import build_quality_guided_variant
from app.services.extraction.document_ir import DocumentIR
from app.services.extraction.ontology_guided.contracts import OntologySnapshot, MetadataSnapshot
from app.services.extraction.ontology_guided.executor import OntologyGuidedExecutor
rid='d231338c-f96f-436a-b759-85fc5610578c'
with engine.connect() as c:
 c.execute(text('SET TRANSACTION READ ONLY'))
 artifacts={r['artifact_kind']:r['payload'] for r in c.execute(text('SELECT h.artifact_kind,a.payload FROM document_analysis_artifact_heads h JOIN document_analysis_artifacts a USING(artifact_id) WHERE h.recognition_run_id=:id'),{'id':rid}).mappings()}
ir=DocumentIR.model_validate(artifacts['structure']['analysis'])
ontology=OntologySnapshot.model_validate(artifacts['ontology_snapshot'])
metadata=MetadataSnapshot.model_validate(artifacts['metadata']['metadata_snapshot'])
root='https://ontology.pharma-gmp.cn/slpra/drug-development/CMCReport'
class ForbiddenAdapter:
 model_identity='no-model-planning-diagnostic'
 protocol_version='ontology-tool-extraction-v1'
 def inspect(self,*args):raise AssertionError('no model calls allowed')
output={'source_run':rid,'real_model_calls':0,'max_hops':1,'scope':'initial admission before first task; not full run coverage or F1','variants':{}}
for variant in ('previous_dense','generic_sparse'):
 runner=build_quality_guided_variant(ontology=ontology,adapter=ForbiddenAdapter(),progress_hook=lambda stage:stage!='before_task',max_hops=1)
 if variant=='previous_dense':
  runner.executor=OntologyGuidedExecutor(ontology=ontology,engine=None,adapter=runner.observed_adapter,current_state=True,max_hops=1,progress_hook=lambda stage:stage!='before_task')
 result=runner.run(recognition_run_id='planning-repair-'+variant,ir=ir,metadata=metadata,root_class_iri=root,root_class_label='CMC报告',filename=artifacts['source']['filename'])
 output['variants'][variant]={'executor_version':runner.executor.version,'progress':result.graph.progress.model_dump(mode='json')}
print(json.dumps(output,ensure_ascii=False))
