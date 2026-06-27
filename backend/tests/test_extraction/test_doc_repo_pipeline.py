"""US2 doc_repo 抽取分支（T022；FR-003，宪章 V 复用）。

[content-extraction C2](../../../specs/007-rnd-document-fact-source/contracts/content-extraction-orchestration.md)：
`run_extraction_pipeline` 增 `source_type=='doc_repo'` 分支——`source_ref=job.source_config['doc_ref']`
（文档个体 IRI）；每候选 `source_ref==<文档个体 IRI>`；一律 `review_status='pending'` 不自动断言；
复用既有 `align_entity`/`group_key`/`degraded_reason`；正文经 `content_ref` 按需取、不持久化全文。
"""

from __future__ import annotations

import asyncio

from app.models.extraction import ExtractionCandidate, ExtractionConfig, ExtractionJob
from app.services.extraction import pipeline as pipeline_module
from app.services.extraction.pipeline import run_extraction_pipeline

FACTS = "http://slpra.org/facts#"
DOC_IRI = f"{FACTS}doc-TTR-001"
CONTENT_REF = "edms://doc/TTR-001/v2"
DRUG_CLASS = "https://ontology.pharma-gmp.cn/slpra/drug/DrugProduct"

# 文档正文按需取出的结构化字段行（list[dict]）——降级模式下经 extract_with_fallback 直通。
DOC_CONTENT_ROWS = [
    {"活性成分": "化合物 X", "剂型": "片剂", "规格": "10mg"},
    {"活性成分": "化合物 Y", "剂型": "注射剂", "规格": "5mg/mL"},
]


def _doc_config(db) -> ExtractionConfig:
    cfg = ExtractionConfig(
        name="TTR 内部药物实体",
        target_class_iri=DRUG_CLASS,
        source_type="doc_repo",
        column_mapping={"活性成分": "activeIngredient", "剂型": "dosageForm",
                        "规格": "specification"},
    )
    db.add(cfg)
    db.commit()
    db.refresh(cfg)
    return cfg


def _doc_job(db, cfg, *, content_ref=CONTENT_REF) -> ExtractionJob:
    job = ExtractionJob(
        source_type="doc_repo",
        source_filename=DOC_IRI,
        source_config={"doc_ref": DOC_IRI, "content_ref": content_ref,
                       "config_id": str(cfg.id)},
        status="pending",
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def _run(db, fake_engine, cfg, job, monkeypatch):
    """按需取正文打桩（记录 content_ref，返回确定性正文行）；运行 doc_repo 分支。"""
    calls: list = []

    def _fake_fetch(content_ref, source_config=None):
        calls.append(content_ref)
        return [dict(r) for r in DOC_CONTENT_ROWS]

    monkeypatch.setattr(pipeline_module, "fetch_document_content", _fake_fetch)
    asyncio.run(run_extraction_pipeline(job, cfg, None, fake_engine, db))
    return calls


def _candidates(db, job):
    return db.query(ExtractionCandidate).filter(ExtractionCandidate.job_id == job.id).all()


def test_doc_repo_branch_produces_pending_candidates_traced_to_document(
        db, fake_engine, monkeypatch):
    """C2.1/C2.2：每候选 source_ref==文档个体 IRI；一律 pending 不自动断言。"""
    cfg = _doc_config(db)
    job = _doc_job(db, cfg)
    _run(db, fake_engine, cfg, job, monkeypatch)

    db.refresh(job)
    assert job.status == "reviewing"
    cands = _candidates(db, job)
    assert len(cands) == len(DOC_CONTENT_ROWS)
    assert all(c.source_ref == DOC_IRI for c in cands)          # C2.1 溯源来源
    assert all(c.review_status == "pending" for c in cands)     # C2.2 零自动断言
    assert all(c.committed_iri is None for c in cands)


def test_doc_repo_branch_reuses_alignment_grouping_degraded(db, fake_engine, monkeypatch):
    """C2.3：复用 align_entity/group_key/degraded_reason——doc_repo 不另起对齐栈。"""
    cfg = _doc_config(db)
    job = _doc_job(db, cfg)
    _run(db, fake_engine, cfg, job, monkeypatch)

    cands = _candidates(db, job)
    assert all(c.alignment_result for c in cands)               # align_entity 已跑
    # 药品归组键 = 活性成分|剂型|规格（_compute_group_key 复用）。
    assert all(c.group_key for c in cands)
    assert any("化合物 X" in c.group_key for c in cands)
    # 008 门控翻转：air-gap 默认（云端关）为离线正常态，复用同一回退路径但**非降级**。
    assert all(c.degraded_reason is None for c in cands)
    assert all(c.candidate_kind == "instance" for c in cands)


def test_content_fetched_on_demand_not_persisted(db, fake_engine, monkeypatch):
    """C2.4：正文经 content_ref 按需取；平台不持久化全文（不回写 source_config/作业列）。"""
    cfg = _doc_config(db)
    job = _doc_job(db, cfg)
    calls = _run(db, fake_engine, cfg, job, monkeypatch)

    assert calls == [CONTENT_REF]  # 按需取一次，按 content_ref 外部引用
    db.refresh(job)
    # 不持久化全文：source_config 仍只含编排键，未回写正文/全文。
    assert set(job.source_config) == {"doc_ref", "content_ref", "config_id"}
    blob = str(job.source_config)
    assert "化合物 X" not in blob and "片剂" not in blob
