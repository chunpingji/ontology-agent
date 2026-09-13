"""Isolated synthetic label/altLabel ranking comparison; no fact-quality claim."""
from __future__ import annotations

import hashlib
import json
import math
import os
import sys
import time
from pathlib import Path
from uuid import uuid4

BACKEND = Path('/opt/dev/chen/ontology-agent/backend')
OUTPUT = Path(__file__).parent / 'results'
sys.path.insert(0, str(BACKEND))
os.environ.update({
    'DATABASE_URL': f'sqlite:///{OUTPUT / "scheduler.sqlite3"}',
    'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1',
    'TOKENIZERS_PARALLELISM': 'false', 'OMP_NUM_THREADS': '4', 'MKL_NUM_THREADS': '4',
})


def write(name, value):
    (OUTPUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2,
                                       allow_nan=False) + '\n', encoding='utf-8')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def execute(report):
    from docx import Document
    from rdflib import Graph, Literal, URIRef
    from rdflib.namespace import RDFS, SKOS
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session
    from app.models.model_request import LocalModelPool, LocalModelRequest
    from app.services.extraction.evidence_identity import evidence_hash, stable_id
    from app.services.extraction.ontology_guided.contracts import (
        EdgeSpec, GraphNode, OntologyClassDefinition, OntologySnapshot,
        ONTOLOGY_LEXICAL_SNAPSHOT_VERSION, RangeClass, SubjectRef, VersionedRef,
    )
    from app.services.extraction.ontology_guided.metadata import prepare_metadata
    from app.services.extraction.ontology_guided.ontology_lexical import build_lexical_context
    from app.services.extraction.ontology_guided.records import RecordIndex
    from app.services.extraction.ontology_guided.retrieval_query import build_subject_queries
    from app.services.extraction.ontology_guided.retrieval_views import build_retrieval_views
    from app.services.extraction.ontology_guided.semantic_retrieval import cosine_scores
    from app.services.extraction.word_analysis import analyze_word_core
    from app.services.llm.model_runtime import model_scope
    from app.services.llm.semantic_ranking import LocalSemanticRanking

    root = 'https://ontology.pharma-gmp.cn/slpra/drug-development/CMCReport'
    predicate_iri = 'https://ontology.pharma-gmp.cn/slpra/drug-development/usesEquipment'
    equipment = 'https://ontology.pharma-gmp.cn/slpra/equipment/Equipment'
    reactor = 'https://ontology.pharma-gmp.cn/slpra/equipment/Reactor'
    graph = Graph()
    sources = [BACKEND.parent / 'ontology/slpra/slpra-drug-development.ttl',
               BACKEND.parent / 'ontology/slpra/slpra-equipment.ttl']
    for source in sources:
        graph.parse(source, format='turtle')
    report['ontology_sources'] = {str(path): digest(path) for path in sources}
    annotations = {
        iri: [{'text': str(value), 'language': value.language,
               'predicate_iri': str(annotation)}
              for annotation in (RDFS.label, SKOS.altLabel)
              for value in graph.objects(URIRef(iri), annotation)
              if isinstance(value, Literal)]
        for iri in (root, predicate_iri, equipment, reactor)
    }
    write('source_annotations.json', annotations)

    def label(iri):
        labels = [term for term in annotations[iri] if term['predicate_iri'] == str(RDFS.label)]
        return sorted(labels, key=lambda term: (term['language'] != 'zh', term['text']))[0]['text']

    def definition(iri):
        values = list(graph.objects(URIRef(iri), RDFS.comment))
        return str(sorted(values, key=lambda value: (value.language != 'zh', str(value)))[0]) if values else ''

    classes = {
        iri: OntologyClassDefinition(iri=iri, label=label(iri), description=definition(iri),
                                    source_hash=evidence_hash([iri, annotations[iri]]))
        for iri in (root, equipment, reactor)
    }
    target = RangeClass(iri=reactor, label=label(reactor), description=definition(reactor))
    predicate = EdgeSpec(iri=predicate_iri, label=label(predicate_iri),
                         description=definition(predicate_iri), declared_by=[root],
                         range_class_iris=[equipment], range_classes=[target])
    report['query_scope'] = {
        'root_class_iri': root, 'predicate_iri': predicate_iri,
        'declared_range_class_iris': [equipment], 'focused_target_class_iris': [reactor],
        'note': 'Fixed synthetic ranking-only target scope, not complete ontology execution.'}
    node = GraphNode(entity_id='synthetic-report', class_iri=root, class_label=label(root),
                     label='synthetic.docx', revision=1, root=True, root_origin='user_specified')
    subject = SubjectRef(entity_id=node.entity_id, revision=1, class_iri=root,
                         is_document_root=True)
    paragraphs = [
        ('alias_support', '本报告设备清单列出反应釜 R-101，物料在该釜内混合反应。', 3, 'support'),
        ('english_support', 'The CMC report lists Reactor R-202 as the vessel used for synthesis.', 3, 'support'),
        ('label_support', '本报告工艺使用反应器 R-103 完成缩合反应。', 3, 'support'),
        ('alias_counter', '本报告明确不使用反应釜 R-303，该设备的租用计划已取消。', 3, 'counterevidence'),
        ('alias_conditional', '仅在放大批次阶段，本报告工艺才使用反应釜 R-404。', 3, 'conditional'),
        ('generic_context', '培训资料介绍反应器的工作原理，不涉及本报告选用的设备。', 1, 'context'),
        ('unrelated_admin', '行政会议室的桌椅按照日程进行整理。', 0, 'irrelevant'),
        ('unrelated_product', '成品包装盒采用蓝色图案，产品名称印在外包装正面。', 0, 'irrelevant'),
    ]
    document = Document()
    for _, text, _, _ in paragraphs:
        document.add_paragraph(text)
    source = OUTPUT / 'synthetic.docx'
    document.save(source)
    analysis = analyze_word_core(source)
    index = RecordIndex(analysis.ir)
    if len(index.records) != len(paragraphs):
        raise ValueError('unexpected synthetic record decomposition')
    metadata = prepare_metadata(analysis.ir, section_tree=analysis.structure.section_tree.to_dict(),
                                summary_version='s0-synthetic-v1', generation_source='structure_only')
    write('document_ir.json', analysis.ir.model_dump(mode='json'))
    write('metadata.json', metadata.model_dump(mode='json'))
    records = [{
        'record_id': record.record_id, 'case_id': case_id, 'text': text,
        'synthetic_relevance_grade': grade, 'role': role,
    } for record, (case_id, text, grade, role) in zip(index.records, paragraphs, strict=True)]
    write('synthetic_relevance.json', {
        'origin': 'assistant-authored synthetic diagnostic; not expert gold', 'records': records})
    variants = []
    for variant in ('E0', 'E1', 'E2'):
        ontology = None
        if variant != 'E0':
            scoped = {iri: [term for term in terms if variant == 'E2'
                            or term['predicate_iri'] == str(RDFS.label)]
                      for iri, terms in annotations.items()}
            lexical = build_lexical_context(scoped)
            ont_hash = evidence_hash({'classes': classes, 'lexical_context': lexical})
            ontology = OntologySnapshot(
                snapshot_id=stable_id('s0-snapshot', [ont_hash]), ontology_hash=ont_hash,
                version=ONTOLOGY_LEXICAL_SNAPSHOT_VERSION, classes=classes,
                created_from='frozen_fixture', lexical_context=lexical)
            write(f'{variant}-ontology_snapshot.json', ontology.model_dump(mode='json'))
        queries = build_subject_queries(
            subject=subject, subject_node=node, predicate=predicate, index=index,
            run_fingerprint=evidence_hash([report['run_id'], analysis.ir.document_hash]),
            root_ref=VersionedRef(id=node.entity_id, revision=1), root_class_iri=root,
            ontology=ontology)
        variants.append((variant, queries))
        write(f'{variant}-queries.json', [query.model_dump(mode='json') for query in queries])
    assert variants[0][1][0].query_version == 'subject-slot-query-v1'
    assert '反应釜' not in variants[1][1][0].model_text
    assert '反应釜' in variants[2][1][0].model_text

    models = BACKEND / 'models/semantic-ranking'
    embedding = models / 'bge-m3/5617a9f61b028005a4858fdac845db406aefb181'
    reranker = models / 'bge-reranker-v2-m3/953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e'
    config = {'mode': 'rerank', 'device': 'cpu', 'dtype': 'float32', 'cuda_version': '12.6',
              'batch_size': 4, 'max_tokens_per_pair': 1024, 'timeout_seconds': 600,
              'embedding_path': str(embedding), 'embedding_manifest_path': str(embedding)+'.sha256',
              'reranker_path': str(reranker), 'reranker_manifest_path': str(reranker)+'.sha256'}
    report['configuration'] = config
    bind = create_engine(f'sqlite:///{OUTPUT / "scheduler.sqlite3"}')
    LocalModelPool.__table__.create(bind)
    LocalModelRequest.__table__.create(bind)
    model = None
    try:
        model = LocalSemanticRanking(config)
        report['model_identity'] = model.identity
        print('artifact verification complete; entering isolated real model calls', flush=True)
        with model_scope(bind=bind, run_id=report['run_id'], task_id='s0-lexical-comparison'):
            views = build_retrieval_views(index, metadata, count_tokens=model.count_tokens,
                                         count_tokens_batch=model.count_tokens_batch,
                                         max_record_tokens=1024)
            if any(view.status != 'complete' for view in views.values()):
                raise ValueError('synthetic view exceeds model input limit')
            write('retrieval_views.json', {rid: view.model_dump(mode='json') for rid, view in views.items()})
            ordered_ids = list(views)
            record_vectors = model.embed([views[rid].model_text for rid in ordered_ids])
            vectors_by_id = dict(zip(ordered_ids, record_vectors, strict=True))
            write('record_embeddings.json', vectors_by_id)
            grades = {record['record_id']: record['synthetic_relevance_grade'] for record in records}
            case_ids = {record['record_id']: record['case_id'] for record in records}

            def metrics(ordered):
                relevant = {rid for rid, grade in grades.items() if grade == 3}
                gains = [2**grades[rid]-1 for rid in ordered[:5]]
                dcg = sum(value/math.log2(position+2) for position, value in enumerate(gains))
                ideal = sum((2**grade-1)/math.log2(position+2)
                            for position, grade in enumerate(sorted(grades.values(), reverse=True)[:5]))
                return {'synthetic_recall_at_3':len(set(ordered[:3]) & relevant)/len(relevant),
                        'synthetic_recall_at_5':len(set(ordered[:5]) & relevant)/len(relevant),
                        'synthetic_ndcg_at_5':dcg/ideal,
                        'alias_support_rank':next(i+1 for i, rid in enumerate(ordered) if case_ids[rid]=='alias_support'),
                        'alias_counter_rank':next(i+1 for i, rid in enumerate(ordered) if case_ids[rid]=='alias_counter'),
                        'alias_conditional_rank':next(i+1 for i, rid in enumerate(ordered) if case_ids[rid]=='alias_conditional'),
                        'ordered_case_ids':[case_ids[rid] for rid in ordered]}

            results = []
            for variant, queries in variants:
                before = len(model.observations)
                phase_started = time.monotonic()
                query_vectors = model.embed([query.model_text for query in queries])
                for query, query_vector in zip(queries, query_vectors, strict=True):
                    pairs = [(query.model_text, views[rid].model_text) for rid in ordered_ids]
                    write(f'{variant}-{query.retrieval_intent}-model_inputs.json', {
                        'embedding_query':query.model_text, 'reranker_pairs':pairs})
                    scores = model.score_pairs(pairs)
                    dense = cosine_scores(query_vector, vectors_by_id)
                    rerank = dict(zip(ordered_ids, scores, strict=True))
                    if not all(math.isfinite(value) for value in scores):
                        raise ValueError('nonfinite reranker output')
                    dense_order = sorted(ordered_ids, key=lambda rid:(-dense[rid], rid))
                    rerank_order = sorted(ordered_ids, key=lambda rid:(-rerank[rid], rid))
                    result = {'variant':variant, 'intent':query.retrieval_intent,
                              'query_version':query.query_version, 'query_vector':query_vector,
                              'dense_scores':dense, 'raw_rerank_scores':rerank,
                              'dense':metrics(dense_order), 'rerank':metrics(rerank_order)}
                    results.append(result)
                    print(json.dumps({key:result[key] for key in ('variant','intent','dense','rerank')}, ensure_ascii=False), flush=True)
                write(f'{variant}-model_observations.json', model.observations[before:])
                report.setdefault('variant_elapsed_seconds', {})[variant] = time.monotonic()-phase_started
            write('results.json', results)
            report['ranking_summary'] = [{key:value for key,value in item.items()
                                         if key not in ('query_vector','dense_scores','raw_rerank_scores')}
                                        for item in results]
    finally:
        if model is not None:
            model.close()
            write('model_observations.json', model.observations)
            report['measured_model_calls'] = len(model.observations)
            report['measured_input_tokens'] = sum(item.get('input_tokens') or 0 for item in model.observations)
        with Session(bind) as db:
            requests = [{'request_id':row.request_id,'status':row.status,'stage':row.stage,
                         'metrics':row.metrics} for row in db.scalars(select(LocalModelRequest))]
        write('scheduler_requests.json', requests)
        report['scheduler_request_count'] = len(requests)
        report['scheduler_statuses'] = sorted(set(row['status'] for row in requests))
        bind.dispose()


def main():
    OUTPUT.mkdir(exist_ok=False)
    started = time.monotonic()
    report = {'schema_version':'s0-ontology-lexical-real-model-diagnostic-v1',
              'run_id':uuid4().hex, 'status':'running',
              'scope':{'synthetic_input':True, 'main_llm_called':False,
                       'business_facts_written':False, 'gold_quality_evaluation':False,
                       'ranking_only':True, 'shared_database_used':False},
              'harness_sha256':digest(Path(__file__)), 'code_sha256':{}}
    for relative in (
        'app/services/extraction/ontology_guided/retrieval_query.py',
        'app/services/extraction/ontology_guided/lexical_query.py',
        'app/services/extraction/ontology_guided/ontology_lexical.py',
        'app/services/llm/semantic_ranking.py',
    ):
        report['code_sha256'][relative] = digest(BACKEND / relative)
    try:
        execute(report)
        report['status'] = 'passed'
    except Exception as exc:
        report['status'] = 'failed'
        report['error'] = {'type':type(exc).__name__, 'reason':str(exc)}
        import traceback
        traceback.print_exc()
    finally:
        report['elapsed_seconds'] = time.monotonic()-started
        report['artifact_sha256'] = {path.name:digest(path) for path in OUTPUT.iterdir()
                                     if path.is_file() and path.name != 'report.json'}
        write('report.json', report)
        print(json.dumps({'status':report['status'], 'output':str(OUTPUT),
                          'elapsed_seconds':report['elapsed_seconds'],
                          'error':report.get('error')}, ensure_ascii=False), flush=True)
    return int(report['status'] != 'passed')


if __name__ == '__main__':
    raise SystemExit(main())
