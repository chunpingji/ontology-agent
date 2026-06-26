"""文档生命周期记录更新——supersede / withdraw 绝不删行（007 US1，T012；FR-013）。

新版本携 `fields.supersedes=<old_eid>` → 旧影子行 approvalStatus 置 "superseded"，新旧两行均保留；
撤回（二次同步带 approvalStatus="withdrawn"）→ 同 iri 行状态更新、行数不变（绝不 DELETE 影子行）。
"""

from __future__ import annotations

import asyncio
import copy

from app.models.entity_shadow import EntityShadow
from app.models.integration import IntegrationConnector
from app.services.integration.materializer import FactMaterializer
from tests.test_integration.fixtures.doc_repo_changes import DOCUMENT_NS, inline_config

FACTS = "http://slpra.org/facts#"

OLD = {
    "entity_id": "doc-OLD-001",
    "entity_type": "TechTransferReport",
    "version": 1,
    "label": "旧版技术转移报告",
    "fields": {
        "hasDevelopmentPhase": f"{DOCUMENT_NS}Phase_ClinicalI",
        "approvalStatus": "approved",
        "contentHash": "sha256:old",
    },
}
NEW_SUPERSEDING = {
    "entity_id": "doc-NEW-002",
    "entity_type": "TechTransferReport",
    "version": 1,
    "label": "新版技术转移报告",
    "fields": {
        "hasDevelopmentPhase": f"{DOCUMENT_NS}Phase_ClinicalI",
        "approvalStatus": "approved",
        "contentHash": "sha256:new",
        "supersedes": "doc-OLD-001",
    },
}


def _conn(db, changes):
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


def _count(db):
    return db.query(EntityShadow).count()


def test_supersede_marks_old_and_keeps_both_rows(db, fake_engine):
    """旧版先于新版出现在同批拉取；新版携 supersedes → 旧版改 superseded，两行皆留。"""
    c = _conn(db, [copy.deepcopy(OLD), copy.deepcopy(NEW_SUPERSEDING)])
    asyncio.run(FactMaterializer(db, fake_engine).run_sync(c))

    assert _count(db) == 2  # 不删任何行
    assert _shadow(db, "doc-OLD-001").properties_json["approvalStatus"] == "superseded"
    assert _shadow(db, "doc-NEW-002").properties_json["approvalStatus"] == "approved"


def test_withdraw_updates_status_without_deleting(db, fake_engine):
    """撤回 = 同 iri 新版本将 approvalStatus 置 withdrawn；行数不变、无 DELETE。"""
    c = _conn(db, [copy.deepcopy(OLD)])
    asyncio.run(FactMaterializer(db, fake_engine).run_sync(c))
    assert _count(db) == 1

    withdrawn = copy.deepcopy(OLD)
    withdrawn["version"] = 2
    withdrawn["fields"]["approvalStatus"] = "withdrawn"
    c.connection_config = inline_config(changes=[withdrawn])
    db.commit()
    db.refresh(c)
    asyncio.run(FactMaterializer(db, fake_engine).run_sync(c))

    assert _count(db) == 1  # 行数不变（无 DELETE）
    s = _shadow(db, "doc-OLD-001")
    assert s.properties_json["approvalStatus"] == "withdrawn"
    assert s.properties_json["_version"] == 2
