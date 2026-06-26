"""US2 复核门禁零削弱（T023；FR-003，SC-003 = 0% 自动入库）。

[content-extraction C3](../../../specs/007-rnd-document-fact-source/contracts/content-extraction-orchestration.md)：
唯一入库路径 = `confirmed`/`edited` → `_commit_candidate`；`rejected` 不入库；入库经
`require_role(senior_analyst)`；拒绝决定可追溯（`extraction.candidate.*` 审计）；
来源文档可信**不**绕过门禁（无「doc_repo 直接入库」捷径）。
"""

from __future__ import annotations

import asyncio

from app.models.extraction import ExtractionCandidate, ExtractionConfig, ExtractionJob
from app.models.reasoning import AuditLog
from app.services.extraction import pipeline as pipeline_module
from app.services.extraction.pipeline import run_extraction_pipeline

FACTS = "http://slpra.org/facts#"
DOC_IRI = f"{FACTS}doc-TTR-001"
DRUG_CLASS = "https://ontology.pharma-gmp.cn/slpra/drug/DrugProduct"
DOC_CONTENT_ROWS = [
    {"活性成分": "化合物 X", "剂型": "片剂", "规格": "10mg"},
    {"活性成分": "化合物 Y", "剂型": "注射剂", "规格": "5mg/mL"},
]


def _seed_candidates(db, fake_engine, monkeypatch):
    """经 doc_repo 分支产出待复核候选（来源文档可信，但仍须过门禁）。"""
    cfg = ExtractionConfig(name="TTR", target_class_iri=DRUG_CLASS, source_type="doc_repo",
                           column_mapping={"活性成分": "activeIngredient"})
    db.add(cfg)
    db.commit()
    db.refresh(cfg)
    job = ExtractionJob(source_type="doc_repo", source_filename=DOC_IRI,
                        source_config={"doc_ref": DOC_IRI, "content_ref": "edms://x",
                                       "config_id": str(cfg.id)}, status="pending")
    db.add(job)
    db.commit()
    db.refresh(job)

    monkeypatch.setattr(pipeline_module, "fetch_document_content",
                        lambda content_ref, source_config=None: [dict(r) for r in DOC_CONTENT_ROWS])
    asyncio.run(run_extraction_pipeline(job, cfg, None, fake_engine, db))
    return db.query(ExtractionCandidate).filter(ExtractionCandidate.job_id == job.id).all()


def test_trusted_source_does_not_bypass_gate(client, db, fake_engine, monkeypatch):
    """C3.4：来源文档可信不绕过——产出候选一律 pending、未入库（无直入捷径）。"""
    cands = _seed_candidates(db, fake_engine, monkeypatch)
    assert cands, "doc_repo 分支应产出候选"
    assert all(c.review_status == "pending" for c in cands)
    assert all(c.committed_iri is None for c in cands)


def test_only_confirmed_commits_rejected_does_not(client, analyst_headers, db, fake_engine,
                                                  monkeypatch):
    """C3.1：confirmed → committed（_commit_candidate）；rejected 不入库。"""
    cands = _seed_candidates(db, fake_engine, monkeypatch)
    c_ok, c_no = cands[0], cands[1]

    r = client.put(f"/api/extraction/candidates/{c_ok.id}/review",
                   json={"status": "confirmed"}, headers=analyst_headers)
    assert r.status_code == 200, r.text
    assert r.json()["review_status"] == "committed"
    assert r.json()["committed_iri"]

    r = client.put(f"/api/extraction/candidates/{c_no.id}/review",
                   json={"status": "rejected"}, headers=analyst_headers)
    assert r.status_code == 200, r.text
    assert r.json()["review_status"] == "rejected"
    assert not r.json()["committed_iri"]  # 拒绝绝不入库


def test_commit_requires_senior_analyst(client, operator_headers, db, fake_engine, monkeypatch):
    """C3.2：入库经 require_role(senior_analyst)——operator 确认被拒（403），不入库。"""
    cands = _seed_candidates(db, fake_engine, monkeypatch)
    c = cands[0]
    r = client.put(f"/api/extraction/candidates/{c.id}/review",
                   json={"status": "confirmed"}, headers=operator_headers)
    assert r.status_code == 403, r.text
    db.refresh(c)
    assert c.review_status == "pending"  # 未授权 → 仍未入库
    assert c.committed_iri is None


def test_rejection_is_traceable(client, analyst_headers, db, fake_engine, monkeypatch):
    """C3.3：拒绝决定可追溯（extraction.candidate.review 审计）。"""
    cands = _seed_candidates(db, fake_engine, monkeypatch)
    c = cands[0]
    client.put(f"/api/extraction/candidates/{c.id}/review",
               json={"status": "rejected"}, headers=analyst_headers)

    entry = (
        db.query(AuditLog)
        .filter(AuditLog.action == "extraction.candidate.review",
                AuditLog.entity_iri == str(c.id))
        .order_by(AuditLog.seq.desc())
        .first()
    )
    assert entry is not None
    assert entry.actor == "analyst"
    assert entry.details["status"] == "rejected"
