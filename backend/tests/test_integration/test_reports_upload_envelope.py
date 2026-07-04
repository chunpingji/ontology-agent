"""报告中心文档上传信封契约（前端 `prepareUpload` → doc_repo upload → 物化）。

前端 `frontend/src/lib/api.ts::prepareUpload` 构造的上传信封须与后端
`DocumentRepositoryConnector._normalize_upload` 契约逐字段一致（doc_id/doc_type/
version/title/metadata），经一次 `run_sync` 即物化为**可被 listDocuments 检索的托管
文档个体**（module=document、class=document/<docType>、label_zh=文件名）。

回归护栏：历史 bug——前端曾发送 {filename, content_type, size}，与契约不符 →
`_normalize_upload` 产出 version=None → `int(None)` 崩溃 → 同步失败 → 文档永不入库。
本测试锁定修复后的信封形状，防止再次漂移。
"""

from __future__ import annotations

import asyncio

from app.models.entity_shadow import EntityShadow
from app.models.integration import IntegrationConnector
from app.services.integration.materializer import FactMaterializer

FACTS = "http://slpra.org/facts#"
DOCUMENT_NS = "https://ontology.pharma-gmp.cn/slpra/document/"
DRUG_DEV_NS = "https://ontology.pharma-gmp.cn/slpra/drug-development/"


def _prepared_envelope(
    doc_id: str, doc_type: str, title: str, development_phase: str | None = None
) -> dict:
    """镜像前端 `prepareUpload` 产出的上传信封（字段与形状逐字段对齐）。"""
    metadata = {
        "created_at": "2026-07-03T00:00:00.000Z",
        "ingested_at": "2026-07-03T00:00:00.000Z",
        "content_type": "application/pdf",
        "file_size": 20480,
        "approvalStatus": "approved",
        "sourceSystem": "web-upload",
    }
    if development_phase:  # 研发阶段来自左侧选中分类，随信封 metadata 落为文档属性
        # 规范谓词 hasDevelopmentPhase（与后端 search_entities 阶段过滤 + _document_phase 继承同键）。
        metadata["hasDevelopmentPhase"] = development_phase
    return {
        "doc_id": doc_id,
        "doc_type": doc_type,
        "version": 1,
        "title": title,
        "metadata": metadata,
    }


def _upload_connector(
    db, envelopes: list[dict], doc_type_to_class: dict | None = None
) -> IntegrationConnector:
    c = IntegrationConnector(
        system_type="doc_repo",
        name="文档上传：合同.pdf",
        connection_config={"access_mode": "upload", "upload_payload": envelopes},
        # 前端为所选文档类型下发 doc_type_to_class 覆盖：localName → 完整文档类 IRI，
        # 使非默认（drug-development 命名空间）的 RegulatoryDocument 子类也能正确物化。
        field_mapping={"doc_type_to_class": doc_type_to_class} if doc_type_to_class else {},
        poll_interval_seconds=2,
        is_active=True,
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def test_prepared_upload_envelope_materializes_listable_document(db, fake_engine):
    """前端信封 → 一次 run_sync → 托管文档个体（module=document，按类型归类，标签=文件名）。"""
    doc_id = "upload-abc123"
    env = _prepared_envelope(doc_id, "RegulatoryDocument", "受托生产质量协议.pdf")
    c = _upload_connector(db, [env])

    run = asyncio.run(FactMaterializer(db, fake_engine).run_sync(c))
    assert run.status == "success"  # 修复前此处为 "error"（int(None) 崩溃）
    assert run.change_count == 1

    s = db.query(EntityShadow).filter(EntityShadow.iri == f"{FACTS}{doc_id}").one()
    assert s.iri == f"{FACTS}{doc_id}"  # 预测 IRI（前端据此乐观占位 + 去重对账）
    assert s.class_iri == f"{DOCUMENT_NS}RegulatoryDocument"  # docType → 托管文档类
    assert s.module == "document"  # → listDocuments(module=document) 可检索
    assert s.label_zh == "受托生产质量协议.pdf"  # title → label_zh → 列表标题
    assert s.properties_json["approvalStatus"] == "approved"
    assert s.properties_json["_version"] == 1


def test_all_managed_doc_types_map_to_document_category(db, fake_engine):
    """7 类受控文档类型逐一物化为对应托管类（前端下拉选项与后端映射表一致）。"""
    doc_types = [
        "RegulatoryDocument",
        "INDDossier",
        "TechTransferReport",
        "ProcessValidationReport",
        "StabilityReport",
        "NDA_BLADossier",
        "PVReport",
    ]
    envelopes = [
        _prepared_envelope(f"upload-{dt}", dt, f"{dt}.docx") for dt in doc_types
    ]
    c = _upload_connector(db, envelopes)

    run = asyncio.run(FactMaterializer(db, fake_engine).run_sync(c))
    assert run.status == "success"
    assert run.change_count == len(doc_types)

    for dt in doc_types:
        s = db.query(EntityShadow).filter(EntityShadow.iri == f"{FACTS}upload-{dt}").one()
        assert s.class_iri == f"{DOCUMENT_NS}{dt}"
        assert s.module == "document"


def test_drug_development_subclass_via_override_is_listable_document(db, fake_engine):
    """非默认命名空间的 RegulatoryDocument 子类（drug-development）经 doc_type_to_class
    覆盖上传 → 强制归 module=document（绕过按 /slpra/document/ 前缀的启发式误判），
    研发阶段作为属性落库供按阶段归类。"""
    doc_id = "upload-csr-001"
    doc_type = "ClinicalStudyReport"  # slpra-dev 命名空间，不在默认 7 类映射中
    class_iri = f"{DRUG_DEV_NS}{doc_type}"
    phase_iri = f"{DOCUMENT_NS}Phase_ClinicalI"
    env = _prepared_envelope(doc_id, doc_type, "布洛芬 III 期 CSR.pdf", development_phase=phase_iri)
    c = _upload_connector(db, [env], doc_type_to_class={doc_type: class_iri})

    run = asyncio.run(FactMaterializer(db, fake_engine).run_sync(c))
    assert run.status == "success"
    assert run.change_count == 1

    s = db.query(EntityShadow).filter(EntityShadow.iri == f"{FACTS}{doc_id}").one()
    assert s.class_iri == class_iri  # 覆盖生效：完整 drug-development 类 IRI
    assert s.module == "document"  # 强制归档（前缀启发式本会误判为 integration）
    assert s.properties_json["hasDevelopmentPhase"] == phase_iri  # → 按研发阶段归类
    assert s.label_zh == "布洛芬 III 期 CSR.pdf"
