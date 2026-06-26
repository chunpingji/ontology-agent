"""US2 extractedFrom 溯源回链注入（T024；FR-004，SC-002）。

[content-extraction C4](../../../specs/007-rnd-document-fact-source/contracts/content-extraction-orchestration.md)：
`_commit_candidate` 确认入库时，doc_repo 来源候选注入 `extracted_properties["extractedFrom"]
== candidate.source_ref`（文档个体 IRI），并 `setdefault("hasDevelopmentPhase", <文档阶段 IRI>)`；
100% 携带、可一键溯源回文档+版本；注入**仅**作用于 doc_repo 来源候选（`source_ref` 为 facts#
文档 IRI），非文档候选 `_commit_candidate` 行为不变。
"""

from __future__ import annotations

import asyncio

from app.models.entity_shadow import EntityShadow
from app.models.extraction import ExtractionCandidate, ExtractionJob
from app.models.integration import IntegrationConnector
from app.services.integration.materializer import FactMaterializer
from tests.test_integration.fixtures.doc_repo_changes import (
    DOC_REPO_CHANGE_TTR_V2,
    DOCUMENT_NS,
    inline_config,
)

FACTS = "http://slpra.org/facts#"
DOC_IRI = f"{FACTS}doc-TTR-001"
PHASE_CLINICAL_I = f"{DOCUMENT_NS}Phase_ClinicalI"
DRUG_CLASS = "https://ontology.pharma-gmp.cn/slpra/drug/DrugProduct"


def _materialize_doc(db, fake_engine):
    """能力三物化文档记录 → facts#doc-TTR-001 影子行（携 hasDevelopmentPhase=Phase_ClinicalI）。"""
    c = IntegrationConnector(
        system_type="doc_repo", name="EDMS",
        connection_config=inline_config(changes=[DOC_REPO_CHANGE_TTR_V2]),
        field_mapping={}, poll_interval_seconds=2, is_active=True,
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    asyncio.run(FactMaterializer(db, fake_engine).run_sync(c))
    shadow = db.query(EntityShadow).filter(EntityShadow.iri == DOC_IRI).one()
    assert shadow.properties_json["hasDevelopmentPhase"] == PHASE_CLINICAL_I  # 前置坐实


def _candidate(db, props, *, source_ref=DOC_IRI, source_type="doc_repo"):
    job = ExtractionJob(source_type=source_type, source_filename=DOC_IRI,
                        source_config={"doc_ref": DOC_IRI}, status="reviewing")
    db.add(job)
    db.commit()
    db.refresh(job)
    cand = ExtractionCandidate(job_id=job.id, target_class_iri=DRUG_CLASS,
                               extracted_properties=props, source_ref=source_ref,
                               review_status="pending", alignment_result="new")
    db.add(cand)
    db.commit()
    db.refresh(cand)
    return cand


def _confirm(client, headers, cand_id):
    r = client.put(f"/api/extraction/candidates/{cand_id}/review",
                   json={"status": "confirmed"}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def test_committed_doc_entity_carries_extracted_from(client, analyst_headers, db, fake_engine):
    """C4.1：提交个体 extracted_properties['extractedFrom'] == source_ref（文档个体 IRI）。"""
    _materialize_doc(db, fake_engine)
    cand = _candidate(db, {"activeIngredient": "化合物 X"})
    body = _confirm(client, analyst_headers, cand.id)

    assert body["review_status"] == "committed"
    assert body["extracted_properties"]["extractedFrom"] == DOC_IRI


def test_inherits_document_phase_by_default(client, analyst_headers, db, fake_engine):
    """C4.3：实体缺省继承文档阶段（setdefault hasDevelopmentPhase = 文档阶段 IRI）。"""
    _materialize_doc(db, fake_engine)
    cand = _candidate(db, {"activeIngredient": "化合物 X"})
    body = _confirm(client, analyst_headers, cand.id)

    assert body["extracted_properties"]["hasDevelopmentPhase"] == PHASE_CLINICAL_I


def test_own_phase_not_overwritten(client, analyst_headers, db, fake_engine):
    """C4.3 冲突消解：实体已声明阶段则保留自身（setdefault 不覆盖）。"""
    _materialize_doc(db, fake_engine)
    own = f"{DOCUMENT_NS}Phase_Preclinical"
    cand = _candidate(db, {"activeIngredient": "化合物 Z", "hasDevelopmentPhase": own})
    body = _confirm(client, analyst_headers, cand.id)

    assert body["extracted_properties"]["hasDevelopmentPhase"] == own
    assert body["extracted_properties"]["extractedFrom"] == DOC_IRI


def test_all_committed_doc_entities_carry_extracted_from(client, analyst_headers, db, fake_engine):
    """C4.2：100% 经 doc_repo 抽取确认入库的实体均携 extractedFrom。"""
    _materialize_doc(db, fake_engine)
    cands = [_candidate(db, {"activeIngredient": f"化合物 {n}"}) for n in ("A", "B", "C")]
    bodies = [_confirm(client, analyst_headers, c.id) for c in cands]

    assert all(b["extracted_properties"]["extractedFrom"] == DOC_IRI for b in bodies)


def test_non_doc_candidate_commit_unchanged(client, analyst_headers, db, fake_engine):
    """C4.4：非 doc_repo 候选（source_ref 非 facts#）入库不注入——既有抽取零回归。"""
    cand = _candidate(db, {"equipmentID": "CT64201"},
                      source_ref="设备台账.xlsx", source_type="excel")
    body = _confirm(client, analyst_headers, cand.id)

    assert body["review_status"] == "committed"
    assert "extractedFrom" not in body["extracted_properties"]
    assert "hasDevelopmentPhase" not in body["extracted_properties"]
