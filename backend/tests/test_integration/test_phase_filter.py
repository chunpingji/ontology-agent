"""US3 按阶段检索（T031；SC-008/SC-001，US3 AS#1）。

[provenance-and-phase C2](../../../specs/007-rnd-document-fact-source/contracts/provenance-and-phase-query.md)：
按 `hasDevelopmentPhase` 过滤可返回该阶段下的**文档**与**派生实体**（备样/药物）集合；
文档事实个体 100% 携阶段标注（无「未标注」文档个体）。检索**复用**既有 `search_entities` /
`/api/entities` 端点，按 `properties_json.hasDevelopmentPhase` 过滤——不新增检索框架。
"""

from __future__ import annotations

import asyncio

from app.models.entity_shadow import EntityShadow
from app.models.integration import IntegrationConnector
from app.services.integration.materializer import FactMaterializer
from tests.test_integration.fixtures.doc_repo_changes import (
    DOC_REPO_CHANGE_STAB_PRECLIN,
    DOC_REPO_CHANGE_TTR_V2,
    DOCUMENT_NS,
    inline_config,
)

FACTS = "http://slpra.org/facts#"
DRUG_NS = "https://ontology.pharma-gmp.cn/slpra/drug/"
PHASE_CLINICAL_I = f"{DOCUMENT_NS}Phase_ClinicalI"
PHASE_PRECLINICAL = f"{DOCUMENT_NS}Phase_Preclinical"

DOC_TTR = f"{FACTS}doc-TTR-001"      # TechTransferReport，临床Ⅰ期
DOC_STAB = f"{FACTS}doc-STAB-009"    # StabilityReport，临床前
DERIVED_DRUG = f"{FACTS}drug-CMPD-X"  # 自 TTR 抽取入库的派生药物（继承临床Ⅰ期）


def _materialize_docs(db, fake_engine):
    """能力三物化两份分属不同阶段的文档记录 → facts# 影子行。"""
    c = IntegrationConnector(
        system_type="doc_repo", name="EDMS",
        connection_config=inline_config(
            changes=[DOC_REPO_CHANGE_TTR_V2, DOC_REPO_CHANGE_STAB_PRECLIN]),
        field_mapping={}, poll_interval_seconds=2, is_active=True,
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    asyncio.run(FactMaterializer(db, fake_engine).run_sync(c))


def _seed_derived_drug(db):
    """直接落一条派生业务实体影子行（模拟 doc_repo 抽取确认入库后投影，继承临床Ⅰ期）。"""
    db.add(EntityShadow(
        iri=DERIVED_DRUG, class_iri=f"{DRUG_NS}DrugProduct", module="drug",
        label_zh="化合物 X",
        properties_json={"hasDevelopmentPhase": PHASE_CLINICAL_I, "extractedFrom": DOC_TTR},
    ))
    db.commit()


def _filter(client, phase: str) -> set[str]:
    r = client.get(f"/api/entities?development_phase={phase}&page_size=100")
    assert r.status_code == 200, r.text
    return {it["iri"] for it in r.json()["items"]}


def test_filter_returns_documents_of_that_phase(client, db, fake_engine):
    """C2.1：按 hasDevelopmentPhase=临床Ⅰ期 返回该阶段文档、不返回其它阶段文档。"""
    _materialize_docs(db, fake_engine)
    iris = _filter(client, PHASE_CLINICAL_I)
    assert DOC_TTR in iris
    assert DOC_STAB not in iris  # 临床前文档不应出现在临床Ⅰ期结果中


def test_filter_returns_derived_entities_of_that_phase(client, db, fake_engine):
    """C2.2：派生实体（药物）随其阶段一并被检出（与文档同维过滤）。"""
    _materialize_docs(db, fake_engine)
    _seed_derived_drug(db)
    iris = _filter(client, PHASE_CLINICAL_I)
    assert {DOC_TTR, DERIVED_DRUG} <= iris  # 文档 + 派生实体同处临床Ⅰ期


def test_filter_partitions_by_phase(client, db, fake_engine):
    """C2.1/C2.2：不同阶段互不串扰——临床前仅返回稳定性报告。"""
    _materialize_docs(db, fake_engine)
    _seed_derived_drug(db)
    pre = _filter(client, PHASE_PRECLINICAL)
    assert DOC_STAB in pre
    assert DOC_TTR not in pre and DERIVED_DRUG not in pre


def test_every_document_individual_carries_phase(client, db, fake_engine):
    """C2.3：文档事实个体 100% 携阶段标注（无未标注文档个体, SC-008）。"""
    _materialize_docs(db, fake_engine)
    docs = db.query(EntityShadow).filter(EntityShadow.module == "document").all()
    assert docs, "应已物化至少一份文档个体"
    for d in docs:
        assert (d.properties_json or {}).get("hasDevelopmentPhase"), \
            f"文档个体 {d.iri} 缺阶段标注（违反 SC-008）"
