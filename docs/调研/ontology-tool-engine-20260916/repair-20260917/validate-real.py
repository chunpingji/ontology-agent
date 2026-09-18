"""Bounded real-input regression; no writes to document runs or business graphs."""
import argparse
import json
from copy import deepcopy
from pathlib import Path
from time import monotonic
from uuid import uuid4
from sqlalchemy import text
from app.db import engine
from app.services.document_analysis.execution import freeze_tool_engine_policy, _configured_tool_adapter
from app.services.extraction.document_ir import DocumentIR
from app.services.extraction.evidence_identity import evidence_hash
from app.services.extraction.ontology_guided.context import assemble_context
from app.services.extraction.ontology_guided.contracts import (
    OntologySnapshot, MetadataSnapshot, GraphNode, SubjectRef, VersionedRef,
    DocumentContext, VerificationTarget,
)
from app.services.extraction.ontology_guided.claim_protocol import EntityDependencyView
from app.services.extraction.ontology_guided.current_work import validate_tool_protocol
from app.services.extraction.ontology_guided.ontology_plan import compile_local_menu
from app.services.extraction.ontology_guided.scheduler import RecognitionTask
from app.services.extraction.ontology_guided.heuristic_search import HeuristicSearchIndex, HeuristicSearchPolicy

parser=argparse.ArgumentParser()
parser.add_argument('--source-run',required=True)
parser.add_argument('--output',type=Path,required=True)
parser.add_argument('--real',action='store_true')
parser.add_argument('--case', choices=['previous_relation','external_name'])
args=parser.parse_args()
args.output.mkdir(parents=True,exist_ok=False)
with engine.connect() as c:
    c.execute(text('SET TRANSACTION READ ONLY'))
    def query(sql): return [dict(r) for r in c.execute(text(sql),{'id':args.source_run}).mappings()]
    artifacts={r['artifact_kind']:r['payload'] for r in query('SELECT h.artifact_kind,a.payload FROM document_analysis_artifact_heads h JOIN document_analysis_artifacts a USING(artifact_id) WHERE h.recognition_run_id=:id')}
    events=[r['payload'] for r in query('SELECT payload FROM document_analysis_events WHERE recognition_run_id=:id ORDER BY sequence')]
    root=next(GraphNode.model_validate(r['payload']) for r in query("SELECT payload FROM document_analysis_run_candidates WHERE recognition_run_id=:id AND kind='entity'") if r['payload'].get('root'))
ir=DocumentIR.model_validate(artifacts['structure']['analysis'])
ontology=OntologySnapshot.model_validate(artifacts['ontology_snapshot'])
metadata=MetadataSnapshot.model_validate(artifacts['metadata']['metadata_snapshot'])
policy=freeze_tool_engine_policy()
policy['request_budget']['max_context_tokens']=102400 # read-only /props confirmed before this run
adapter=_configured_tool_adapter(policy,ir=ir,ontology=ontology,metadata=metadata)
index=adapter.index
success=next(e for e in events if e.get('outcome',{}).get('reason_code')=='record_supported')
mock_record=next(r for r in index.records if 'PF64603' in r.text)
scenarios=[('previous_relation',success['task']['record_id'],success['task']['predicate_iri']),
           ('external_name',mock_record.record_id,'https://ontology.pharma-gmp.cn/slpra/drug-development/usesEquipment')]
report={'run_id':str(uuid4()),'source_run':args.source_run,'document_hash':ir.document_hash,
        'ontology_hash':ontology.ontology_hash,'metadata_hash':metadata.dependency_hash,
        'records':len(index.records),'units':len(ir.evidence_units),'model':policy['model'],
        'model_revision':policy['model_revision'],'budget':policy['request_budget'],
        'candidate_planning':policy['candidate_planning'],'heuristic_policy':policy['heuristic_policy'],
        'gliner_enabled':adapter.mention_extractor is not None,'mock_enabled':adapter.instance_reader is not None,
        'overlay_enabled':adapter.vocabulary_overlay is not None,'max_model_calls':8,
        'selection':'two engineer-selected regression cases; full IR and frozen ontology/metadata; no reference answers supplied',
        'scoring_status':'not_scored','cases':[]}
def write():
    (args.output/'summary.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
write()
for name,record_id,predicate_iri in scenarios:
    if args.case and args.case != name: continue
    run_id=report['run_id']+':'+name
    subject=SubjectRef(entity_id=run_id,revision=1,class_iri=root.class_iri,is_document_root=True)
    node=root.model_copy(update={'entity_id':run_id,'evidence_refs':root.evidence_refs})
    ref=VersionedRef(id=run_id,revision=1)
    menu=compile_local_menu(ontology,subject)
    predicate=next(p for p in [*menu.properties,*menu.relationships] if p.iri==predicate_iri)
    task=RecognitionTask.create(subject=subject,predicate_iri=predicate_iri,
        predicate_kind=predicate.kind,record_id=record_id,phase=1,hop=0,dependency_hash=evidence_hash(run_id))
    seed=VerificationTarget.create(run_fingerprint=run_id,claim_ref=VersionedRef(id=task.claim_lineage_id,revision=1),
        task_id=task.task_id,check_kind='predicate_entailment',document_context=DocumentContext(
            document_hash=ir.document_hash,document_class_iri=root.class_iri,root_ref=ref),
        subject_ref=subject,predicate_iri=predicate_iri,ontology_hash=ontology.ontology_hash,
        source_scope_hash=evidence_hash(record_id),context_hash=evidence_hash([run_id,record_id]))
    context=assemble_context(seed,record_id,index,subject_evidence_refs=node.evidence_refs,
        subject_label=node.label,predicate=predicate,ontology=ontology,repair_enabled=False)
    dependency=dict(entity_ref=ref,class_iri=subject.class_iri,grounding_kind='document_root',
        root_origin='user_specified',proposal=None,source_refs=node.evidence_refs,dependency_refs=[])
    dependency=EntityDependencyView(**dependency,content_hash=evidence_hash(dependency))
    context.tool_inputs={'target_seed':seed.model_dump(mode='json'),'run_fingerprint':run_id,
        'subject_node':node.model_dump(mode='json'),'entity_dependencies':[dependency.model_dump(mode='json')]}
    context.remaining_model_calls=4
    stored,paid={},[]
    def checkpoint(state):
        protocol = {key: value for key, value in state.items() if key != 'result_changes'}
        validate_tool_protocol(protocol)
        stored.update(deepcopy(state.get('result_changes',{})))
        (args.output/(name+'-results.json')).write_text(json.dumps(stored,ensure_ascii=False))
        (args.output/(name+'-protocol.json')).write_text(json.dumps(state,ensure_ascii=False))
    def reserve(stage,ordinal):
        if len(paid)>=4: raise RuntimeError('regression_budget_exhausted')
        paid.append({'stage':stage,'ordinal':ordinal})
        print(json.dumps({'case':name,'request':len(paid),'stage':stage}),flush=True)
    context.bind_protocol_hook(checkpoint)
    context.bind_model_call_hook(reserve)
    case={'case':name,'record_id':record_id,'predicate_iri':predicate_iri,'requests':paid}
    report['cases'].append(case)
    if args.real:
        start=monotonic()
        try:
            outcome=adapter.inspect(task,context,predicate,menu)
            case.update(complete=outcome.complete,reason_code=outcome.reason_code,
                semantic_outcome=outcome.semantic_outcome,nodes=len(outcome.nodes),
                groups=len(outcome.relationship_groups),properties=len(outcome.properties),
                controller_checks=outcome.controller_checks)
            (args.output/(name+'-outcome.json')).write_text(outcome.model_dump_json(indent=2))
        except Exception as exc:
            case.update(error_type=type(exc).__name__,reason_code=getattr(exc,'reason_code',None),error_detail=str(exc)[:150] if isinstance(exc,ValueError) else None)
        case['seconds']=round(monotonic()-start,3)
        tools=[]; usage=[]
        for row in stored.values():
            if row['field']=='tool_result':
                value=row['value']; result=value['result']
                tools.append({'name':next((item['name'] for row in stored.values() if row['field']=='model_turn' for item in row['value']['output_items'] if item.get('call_id')==value['call_id']), 'unknown'),'status':result['status'],
                    'issues':[i['code'] for i in result['issues']]})
            if row['field']=='model_turn':usage.append(row['value'].get('usage'))
        case['tools']=tools;case['usage']=usage
        (args.output/(name+'-results.json')).write_text(json.dumps(stored,ensure_ascii=False))
    write()
    print(json.dumps(case,ensure_ascii=False),flush=True)
