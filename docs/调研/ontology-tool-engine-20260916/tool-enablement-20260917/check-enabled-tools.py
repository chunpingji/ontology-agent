import json
from pathlib import Path
from dataclasses import replace
from app.config import settings
from app.services.document_analysis.execution import freeze_tool_engine_policy, _configured_recognition_adapter
from app.services.extraction.document_ir import DocumentIR
from app.services.extraction.evidence_identity import evidence_hash
from app.services.extraction.ontology_guided.claim_protocol import EntityDependencyView, SchemaCard
from app.services.extraction.ontology_guided.context import TaskContext
from app.services.extraction.ontology_guided.contracts import OntologySnapshot, MetadataSnapshot, TraversalScope, VersionedRef
from app.services.extraction.ontology_guided.ontology_plan import compile_local_menu
from app.services.extraction.ontology_guided.scheduler import RecognitionTask
from app.services.extraction.ontology_guided.tool_contracts import ToolCall
from app.services.extraction.ontology_guided.tool_runtime import ToolContext, build_tool_definitions, dispatch_tool
from app.services.extraction.tool_validation.vocabulary import build_extraction_vocabulary

base=Path('/verification')
ir=DocumentIR.model_validate_json((base/'runtime/inputs/ir.json').read_text(),strict=True)
ontology=OntologySnapshot.model_validate_json((base/'runtime/inputs/ontology.json').read_text(),strict=True)
metadata=MetadataSnapshot.model_validate_json((base/'runtime/inputs/metadata.json').read_text(),strict=True)
policy=freeze_tool_engine_policy()
assert {'gliner2','external_sources','vocabulary_overlay'} <= set(policy['extraction_options'])
adapter=_configured_recognition_adapter(policy,ir=ir,ontology=ontology,metadata=metadata)
assert adapter.mention_extractor is not None and adapter.instance_reader is not None
limits=adapter.mention_extractor.prepare_strict()
assert limits['backend']=='gliner2.5' and limits['max_width'] is None
context=TaskContext.model_validate_json((base/'tool-chain-probe01/context.json').read_text(),strict=True)
card=SchemaCard.model_validate_json((base/'tool-chain-probe01/schema-card.json').read_text(),strict=True)
subject=context.target.subject_ref
menu=compile_local_menu(ontology,subject)
predicate=next(p for p in menu.relationships if p.iri==card.predicates[0].iri)
task=RecognitionTask.create(subject=subject,predicate_iri=predicate.iri,predicate_kind='relationship',record_id=context.record_id,phase=1,hop=0,dependency_hash=evidence_hash(['ontology-tool-chain-integration-01',predicate]))
assert task.task_id==context.target.task_id
root=VersionedRef(id=subject.entity_id,revision=subject.revision)
values=dict(entity_ref=root,class_iri=subject.class_iri,grounding_kind='document_root',root_origin='user_specified',proposal=None,source_refs=[],dependency_refs=[])
dependency=EntityDependencyView(**values,content_hash=evidence_hash(values))
ctx=ToolContext(task=task,context=context,index=adapter.index,menu=menu,profile=adapter.profile,scope=TraversalScope.create(),stage='discovery',recovery_kind='none',frozen_claims={},local_ref_map={},entity_dependencies={root.id:dependency},limits=adapter.tool_limits,measure_result_tokens=adapter.token_counter,cards={card.schema_card_id:card},ontology_snapshot=ontology,vocabulary_overlay=adapter.vocabulary_overlay,mention_extractor=adapter.mention_extractor,instance_reader=adapter.instance_reader,external_source_ids=adapter.external_source_ids)
names={tool['name'] for tool in build_tool_definitions(ctx,'discovery')}
assert {'propose_mentions','query_instances'} <= names
vocabulary=build_extraction_vocabulary(ontology,card.class_iris,overlay=adapter.vocabulary_overlay)
missing=[entry for entry in vocabulary['missing'] if entry['role']=='entity']
assert not missing
reactor=next(entry for entry in vocabulary['entries'].values() if entry['iri'].endswith('/Reactor') and entry['role']=='entity')
assert '反应釜' in reactor['alt_labels']
name_fragment=next(f for f in context.fragments if '反应釜' in f.text and f.fact_eligible)
call=ToolCall(call_id='local-ner-check',name='propose_mentions',arguments_json=json.dumps({'evidence_ids':[name_fragment.anchor.evidence_id],'schema_card_id':card.schema_card_id}))
ner=dispatch_tool(call,ctx)
assert ner.status in ('ok','no_match'),ner
assert not ner.issues and not ner.data.omissions,ner
assert not ner.data.coverage.unprocessed_units
source_id='16438631527487298ec0944d6b00559c2dbb71c7e9387096dd808d8c26ccaeda'
anchor_call=ToolCall(call_id='local-anchor-check',name='resolve_source_anchor',arguments_json=json.dumps({'evidence_id':source_id,'quote':'RE64611','context_text':None}))
anchor=dispatch_tool(anchor_call,ctx)
assert anchor.status=='ok',anchor
protocol={'materialized_refs':{},'evidence_revision':1,'context_hash':context.context_hash}
ctx,protocol=adapter._materialize(ctx,protocol,anchor_call,anchor,'local-check-result')
query_call=ToolCall(call_id='local-mock-check',name='query_instances',arguments_json=json.dumps({'mention_ref':anchor.data.mention_ref,'class_iri':reactor['iri'],'source_ids':['system-mock-equipment']}))
query=dispatch_tool(query_call,ctx)
assert query.status=='ok' and query.data.candidates,query
assert query.data.identity_status=='not_checked'
summary={'mode':'deployed_options_integration_check','config_content_hash':evidence_hash(settings.ontology_extraction_options),'gliner':{'backend':limits['backend'],'device':adapter.mention_extractor.device,'checkpoint_revision':settings.ontology_extraction_options['gliner2']['manifest']['revision'],'status':ner.status,'mention_count':len(ner.data.mentions),'processed_units':len(ner.data.coverage.processed_units),'unprocessed_units':len(ner.data.coverage.unprocessed_units),'omissions':len(ner.data.omissions),'missing_card_entity_definitions':len(missing)},'mock':{'source_counts':{k:len(v) for k,v in settings.ontology_extraction_options['external_sources']['records'].items()},'query_status':query.status,'candidates':len(query.data.candidates),'identity_status':query.data.identity_status},'vocabulary':{'overlay_entries':len(adapter.vocabulary_overlay.entries),'reactor_alt_labels':reactor['alt_labels']},'advertised_tools':sorted(names),'qwen_requests':0,'graph_writes':0,'fixture_scope':'explicit original name and code from the frozen report; local tool integration, not autonomous extraction or F1'}
print(json.dumps(summary,ensure_ascii=False),flush=True)
