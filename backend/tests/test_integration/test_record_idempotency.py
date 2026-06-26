"""记录层物化幂等与水位语义（007 US1，T010；FR-018/019）。

[record-materialization](../../../specs/007-rnd-document-fact-source/contracts/record-materialization-invariants.md)：
同批 v1+v2 → 单影子行 @v2；乱序/重复旧版本不重复物化；成功推进
`cursor={"version":max,"versions":{eid:max}}` 并回填 event_ids；水位已达再拉 change_count=0；
fetch 超时 → status="timeout"、cursor_to=None、connector.sync_cursor 不回退、无新增影子行。
"""

from __future__ import annotations

import asyncio

from app.models.entity_shadow import EntityShadow
from app.models.integration import IntegrationConnector
from app.services.integration import materializer as materializer_module
from app.services.integration.materializer import FactMaterializer
from tests.test_integration.fixtures.doc_repo_changes import (
    DOC_REPO_CHANGE_TTR_V1,
    DOC_REPO_CHANGE_TTR_V2,
    DOCUMENT_NS,
    inline_config,
    upload_config,
)

FACTS = "http://slpra.org/facts#"


def _conn(db, cfg):
    c = IntegrationConnector(
        system_type="doc_repo",
        name="EDMS",
        connection_config=cfg,
        field_mapping={},
        poll_interval_seconds=2,
        is_active=True,
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def _rows(db, eid):
    return db.query(EntityShadow).filter(EntityShadow.iri == f"{FACTS}{eid}").all()


def test_same_pull_v1_v2_collapses_to_single_row_at_v2(db, fake_engine):
    """同批 v1+v2：两条都计入 change_count，但同 iri 折叠为单行 @v2，水位带 versions。"""
    c = _conn(db, inline_config(changes=[DOC_REPO_CHANGE_TTR_V1, DOC_REPO_CHANGE_TTR_V2]))
    run = asyncio.run(FactMaterializer(db, fake_engine).run_sync(c))

    assert run.change_count == 2
    rows = _rows(db, "doc-TTR-001")
    assert len(rows) == 1
    assert rows[0].properties_json["_version"] == 2
    assert run.cursor_to == {"version": 2, "versions": {"doc-TTR-001": 2}}
    assert len(run.event_ids) == 2  # 每条 applied 一个事件（C-5 提交后发布）


def test_out_of_order_old_version_skipped(db, fake_engine):
    """乱序到达（v2 先、v1 后）：v1<=已物化 v2 → 幂等跳过。"""
    c = _conn(db, inline_config(changes=[DOC_REPO_CHANGE_TTR_V2, DOC_REPO_CHANGE_TTR_V1]))
    run = asyncio.run(FactMaterializer(db, fake_engine).run_sync(c))

    assert run.change_count == 1
    assert _rows(db, "doc-TTR-001")[0].properties_json["_version"] == 2


def test_second_sync_with_advanced_cursor_yields_zero(db, fake_engine):
    """水位已达 v2 → 二次同步无新增、行数不变。"""
    c = _conn(db, inline_config(changes=[DOC_REPO_CHANGE_TTR_V2]))
    asyncio.run(FactMaterializer(db, fake_engine).run_sync(c))
    run2 = asyncio.run(FactMaterializer(db, fake_engine).run_sync(c))

    assert run2.change_count == 0
    assert run2.cursor_to["version"] == 2
    assert len(_rows(db, "doc-TTR-001")) == 1


class _Boom:
    async def fetch_incremental(self, cursor):
        raise asyncio.TimeoutError("simulated")


def test_timeout_does_not_advance_watermark_or_write_rows(db, fake_engine, monkeypatch):
    """fetch 超时 → status=timeout、cursor_to=None、connector.sync_cursor 不回退、零新增行。"""
    c = _conn(db, inline_config(changes=[DOC_REPO_CHANGE_TTR_V2]))
    c.sync_cursor = {"version": 1, "versions": {"doc-TTR-001": 1}}
    db.commit()
    db.refresh(c)
    monkeypatch.setattr(materializer_module, "connector_for", lambda conn: _Boom())

    run = asyncio.run(FactMaterializer(db, fake_engine).run_sync(c))

    assert run.status == "timeout"
    assert run.cursor_to is None
    db.refresh(c)
    assert c.sync_cursor == {"version": 1, "versions": {"doc-TTR-001": 1}}  # 不回退
    assert _rows(db, "doc-TTR-001") == []  # 无新增影子行


def test_upload_mode_materializes_to_canonical_shadow(db, fake_engine):
    """C3.4：upload 接入模式经同一物化出口产出与 inline 规范一致的影子行（class+properties）。"""
    c = _conn(db, upload_config())
    asyncio.run(FactMaterializer(db, fake_engine).run_sync(c))

    s = _rows(db, "doc-TTR-001")[0]
    assert s.class_iri == f"{DOCUMENT_NS}TechTransferReport"
    assert s.module == "document"
    assert s.properties_json["hasDevelopmentPhase"] == f"{DOCUMENT_NS}Phase_ClinicalI"
    assert s.properties_json["approvalStatus"] == "approved"
    assert s.properties_json["contentHash"] == "sha256:1f3b9cda2e"
    assert s.properties_json["_version"] == 2
