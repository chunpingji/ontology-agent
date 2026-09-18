import json
from types import SimpleNamespace
from sqlalchemy import text
from app.db import engine
from app.services.extraction.document_ir import DocumentIR
from app.services.extraction.ontology_guided.records import RecordIndex
from app.services.extraction.ontology_guided.contracts import OntologySnapshot,LocalMenu,GraphNode,VerificationTarget
from app.services.extraction.ontology_guided.scheduler import RecognitionTask
from app.services.extraction.ontology_guided.ontology_plan import compile_local_menu
from app.services.extraction.ontology_guided.context import assemble_context
from app.services.extraction.ontology_guided.tool_runtime import _record_id,_inspect_evidence,ToolLimits
from app.services.extraction.ontology_guided.tool_contracts import InspectEvidenceArgs
rid='d231338c-f96f-436a-b759-85fc5610578c'
with engine.connect() as c:
 c.execute(text('SET TRANSACTION READ ONLY'))
 def q(sql):return [dict(r) for r in c.execute(text(sql),{'id':rid}).mappings()]
 artifacts={r['artifact_kind']:r['payload'] for r in q('SELECT h.artifact_kind,a.payload FROM document_analysis_artifact_heads h JOIN document_analysis_artifacts a USING(artifact_id) WHERE h.recognition_run_id=:id')}
 events=q('SELECT payload FROM document_analysis_events WHERE recognition_run_id=:id ORDER BY sequence')
 menu=LocalMenu.model_validate(q("SELECT payload FROM document_analysis_current_state WHERE recognition_run_id=:id AND domain='work:menus'")[0]['payload']['body']['value'])
 root=next(GraphNode.model_validate(r['payload']) for r in q("SELECT payload FROM document_analysis_run_candidates WHERE recognition_run_id=:id AND kind='entity'") if r['payload'].get('root'))
 proto=q("SELECT payload FROM document_analysis_requests WHERE recognition_run_id=:id AND domain='calls:protocols' LIMIT 1")[0]['payload']['body']['value']
ir=DocumentIR.model_validate(artifacts['structure']['analysis']);index=RecordIndex(ir);ontology=OntologySnapshot.model_validate(artifacts['ontology_snapshot'])
last={r['payload']['task']['task_id']:r['payload'] for r in events if 'outcome' in r['payload']}
report={'source_record_count':len(index.records),'failures':[]}
for event in last.values():
 if event['outcome']['reason_code']!='_ToolFailure':continue
 task=RecognitionTask.model_validate(event['task']);menu=compile_local_menu(ontology,task.subject);predicate=next(p for p in [*menu.properties,*menu.relationships] if p.iri==task.predicate_iri)
 target=VerificationTarget.model_validate(proto['base_target']).model_copy(update={'task_id':task.task_id,'subject_ref':task.subject,'predicate_iri':task.predicate_iri})
 context=assemble_context(target,task.record_id,index,subject_evidence_refs=root.evidence_refs,subject_label=root.label,predicate=predicate,ontology=ontology,repair_enabled=artifacts['source']['performance_policy'].get('evidence_repair')=='evidence-repair-v1')
 ctx=SimpleNamespace(context=context,index=index,authorization=None,limits=ToolLimits(max_result_tokens=4096))
 failures=[]
 for fragment in context.fragments:
  try:_record_id(fragment,ctx)
  except Exception as exc:
   matches=[r.record_id for r in index.records if any(u.evidence_id==fragment.anchor.evidence_id for u in [*r.source_units,*r.header_units,*r.note_units,*r.parent_units])]
   failures.append({'error':type(exc).__name__,'code':getattr(exc,'code',None),'purpose':fragment.purpose,'evidence_id':fragment.anchor.evidence_id,'record_matches':len(matches)})
 try:
  ids=list(dict.fromkeys(f.anchor.evidence_id for f in context.fragments))
  for start in range(0,len(ids),ctx.limits.max_evidence_units_per_call):_inspect_evidence(InspectEvidenceArgs(evidence_ids=ids[start:start+ctx.limits.max_evidence_units_per_call]),ctx)
  reproduced=False
 except Exception as exc:reproduced=type(exc).__name__
 report['failures'].append({'task_id':task.task_id,'lineage_id':task.claim_lineage_id,'predicate':task.predicate_iri,'reproduced':reproduced,'fragment_count':len(context.fragments),'bad_fragments':failures})
print(json.dumps(report,ensure_ascii=False))
