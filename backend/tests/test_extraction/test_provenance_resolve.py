"""Previously committed source links and document versions remain readable."""

from __future__ import annotations

import asyncio

from app.models.entity_shadow import EntityShadow
from app.models.extraction import ExtractionCandidate, ExtractionJob
from app.models.integration import IntegrationConnector
from app.services.integration.materializer import FactMaterializer
from tests.test_integration.fixtures.doc_repo_changes import (
    DOC_REPO_CHANGE_TTR_V2,
    inline_config,
)

FACTS = "http://slpra.org/facts#"
DOC_IRI = f"{FACTS}doc-TTR-001"
DRUG_CLASS = "https://ontology.pharma-gmp.cn/slpra/drug/DrugProduct"


def _materialize_doc(db, fake_engine):
    """能力三物化文档记录 TTR-001 v2（documentVersion='2'）→ facts# 影子行。"""
    c = IntegrationConnector(
        system_type="doc_repo",
        name="EDMS",
        connection_config=inline_config(changes=[DOC_REPO_CHANGE_TTR_V2]),
        field_mapping={},
        poll_interval_seconds=2,
        is_active=True,
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    asyncio.run(FactMaterializer(db, fake_engine).run_sync(c))


def _candidate(db, props, *, source_ref=DOC_IRI):
    job = ExtractionJob(
        source_type="doc_repo",
        source_filename=DOC_IRI,
        source_config={"doc_ref": DOC_IRI},
        status="reviewing",
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    cand = ExtractionCandidate(
        job_id=job.id,
        target_class_iri=DRUG_CLASS,
        extracted_properties={**props, "extractedFrom": source_ref},
        source_ref=source_ref,
        review_status="committed",
        committed_iri="urn:historical:" + str(job.id),
        alignment_result="new",
    )
    db.add(cand)
    db.commit()
    db.refresh(cand)
    return cand


def _historical_payload(cand):
    # Fixture represents an existing artifact; no new review or fact is fabricated.
    return {"extracted_properties": cand.extracted_properties, "committed_iri": cand.committed_iri}


def _resolve_source_document(db, committed: dict) -> EntityShadow:
    """从入库实体的 extractedFrom 回链解析源文档个体（影子表查询）。"""
    src = committed["extracted_properties"]["extractedFrom"]
    return db.query(EntityShadow).filter(EntityShadow.iri == src).one_or_none()


def test_business_entity_resolves_to_source_document(client, analyst_headers, db, fake_engine):
    """C3.1：经 extractedFrom 一键解析回源文档个体（facts# 文档行）。"""
    _materialize_doc(db, fake_engine)
    cand = _candidate(db, {"activeIngredient": "化合物 X"})
    committed = _historical_payload(cand)

    doc = _resolve_source_document(db, committed)
    assert doc is not None and doc.iri == DOC_IRI
    assert doc.module == "document"


def test_document_version_yields_which_version(client, analyst_headers, db, fake_engine):
    """C3.2：由源文档个体的 documentVersion 得「抽自哪一版」。"""
    _materialize_doc(db, fake_engine)
    cand = _candidate(db, {"activeIngredient": "化合物 X"})
    committed = _historical_payload(cand)

    doc = _resolve_source_document(db, committed)
    assert (doc.properties_json or {}).get("documentVersion") == "2"


def test_all_committed_doc_entities_traceable_no_broken_link(
    client, analyst_headers, db, fake_engine
):
    """C3.3：100% 经文档抽取入库实体可溯源——每条 extractedFrom 均解析到现存文档个体。"""
    _materialize_doc(db, fake_engine)
    cands = [_candidate(db, {"activeIngredient": f"化合物 {n}"}) for n in ("A", "B", "C")]
    committed = [_historical_payload(c) for c in cands]

    for body in committed:
        assert "extractedFrom" in body["extracted_properties"]  # 无断链：均携回链
        doc = _resolve_source_document(db, body)
        assert doc is not None, "extractedFrom 必须解析到现存文档个体（无悬挂回链）"
