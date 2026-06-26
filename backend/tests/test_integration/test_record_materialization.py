"""记录层物化不变量（007 US1，T007）。

[record-materialization C1.1/C1.3/C1.4](../../../specs/007-rnd-document-fact-source/contracts/record-materialization-invariants.md)：
物化后 `iri` 落 `facts#`、`class_iri` 落托管 `/slpra/document/`、100% 携 `hasDevelopmentPhase`；
非文档变更仍走原 `facts#<entity_type>` 分支（既有运营事实零回归）。
"""

from __future__ import annotations

import asyncio

from app.models.entity_shadow import EntityShadow
from app.models.integration import IntegrationConnector
from app.services.integration.materializer import FactMaterializer
from tests.test_integration.fixtures.doc_repo_changes import (
    DOC_REPO_CHANGE_STAB_PRECLIN,
    DOCUMENT_NS,
    inline_config,
)

FACTS = "http://slpra.org/facts#"


def _doc_connector(db, changes):
    c = IntegrationConnector(
        system_type="doc_repo",
        name="EDMS",
        connection_config=inline_config(changes=changes),
        field_mapping={},
        poll_interval_seconds=2,
        is_active=True,
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def _shadow(db, eid):
    return db.query(EntityShadow).filter(EntityShadow.iri == f"{FACTS}{eid}").one()


def test_document_record_iri_class_module_phase(db, fake_engine):
    """C1.1/C1.2/C1.3：A-Box iri + 托管 T-Box class + module=document + 100% 阶段。"""
    c = _doc_connector(db, [DOC_REPO_CHANGE_STAB_PRECLIN])
    asyncio.run(FactMaterializer(db, fake_engine).run_sync(c))

    s = _shadow(db, "doc-STAB-009")
    assert s.iri.startswith(FACTS)  # C1.1 A-Box 个体
    assert s.class_iri == f"{DOCUMENT_NS}StabilityReport"  # C1.1 托管 T-Box 子类（非 facts#）
    assert "/slpra/document/" in s.class_iri
    assert s.module == "document"  # C1.2 _detect_module 归类
    assert (
        s.properties_json["hasDevelopmentPhase"] == f"{DOCUMENT_NS}Phase_Preclinical"
    )  # C1.3 携阶段
    assert s.properties_json["approvalStatus"] == "approved"
    assert s.properties_json["contentHash"] == "sha256:beefbeef01"
    assert s.properties_json["_version"] == 1


def test_non_document_change_keeps_legacy_facts_class(db, fake_engine):
    """C1.4：非文档连接器（APS）变更仍走原 facts#<entity_type> 类 IRI 分支（零回归）。"""
    c = IntegrationConnector(
        system_type="aps",
        name="APS",
        connection_config={
            "source_mode": "inline",
            "inline_changes": [
                {
                    "entity_id": "EQ-1",
                    "entity_type": "equipment",
                    "version": 1,
                    "fields": {"status": "running"},
                }
            ],
        },
        field_mapping={},
        poll_interval_seconds=2,
        is_active=True,
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    asyncio.run(FactMaterializer(db, fake_engine).run_sync(c))

    s = _shadow(db, "EQ-1")
    assert s.class_iri == f"{FACTS}equipment"
    assert "/slpra/document/" not in s.class_iri
