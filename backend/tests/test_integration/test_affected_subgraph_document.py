"""US2 受影响子图 document 维（T025；FR-007 下半句，数据模型 §6）。

[content-extraction C5](../../../specs/007-rnd-document-fact-source/contracts/content-extraction-orchestration.md)：
文档/派生实体事实变更经 `resolve_affected_subgraph` 解出 `document`（及关联 `sample`/`product`）维，
供受影响子图推理重算；不改 `FactEventBus.publish` 事件信封结构、不改 `AssessmentResult` 对外形状。
"""

from __future__ import annotations

from app.services.integration.connector_factory import DEFAULT_DOC_TYPE_TO_CLASS
from app.services.integration.events import (
    FactEventBus,
    resolve_affected_subgraph,
)

# 发布前事件信封键集（C5.2：本期增 document 维不得增删信封字段）。
_ENVELOPE_KEYS = {
    "id", "connector_id", "entity_type", "entity_id", "version",
    "affected_subgraph", "created_at",
}


def test_document_change_resolves_document_dimension():
    """C5.1：文档变更（doc 子类 entity_type）解出 document 维含文档标识。"""
    change = {"entity_type": "TechTransferReport", "entity_id": "doc-TTR-001",
              "version": 2, "fields": {"approvalStatus": "approved"}}
    sub = resolve_affected_subgraph(change)
    assert sub["document"] == ["doc-TTR-001"]


def test_all_doc_subclasses_recognized_as_document():
    """文档全部托管子类（及通用 'document'）均归 document 维。"""
    for etype in (*DEFAULT_DOC_TYPE_TO_CLASS, "document"):
        sub = resolve_affected_subgraph({"entity_type": etype, "entity_id": "d-1"})
        assert sub.get("document") == ["d-1"], etype


def test_document_associated_sample_and_product_included():
    """C5.1：文档关联的样品/产品也纳入受影响范围。"""
    change = {"entity_type": "StabilityReport", "entity_id": "doc-STAB-009",
              "fields": {"sample": "SMP-77", "product": "PROD-9"}}
    sub = resolve_affected_subgraph(change)
    assert sub["document"] == ["doc-STAB-009"]
    assert sub["sample"] == ["SMP-77"]
    assert sub["product"] == ["PROD-9"]


def test_non_document_change_shape_unchanged():
    """零回归：设备变更不引入 document/sample 维，沿用既有 {equipment,product,area} 形状。"""
    sub = resolve_affected_subgraph(
        {"entity_type": "equipment", "entity_id": "CT64201", "fields": {"product": "P-1"}})
    assert sub["equipment"] == ["CT64201"]
    assert sub["product"] == ["P-1"]
    assert "document" not in sub  # 空维被剔除，对外形状不变
    assert "sample" not in sub


def test_publish_envelope_shape_unchanged_for_document():
    """C5.2：发布文档变更不改事件信封结构（仅 affected_subgraph 内含 document 维）。"""
    bus = FactEventBus()
    ev = bus.publish(connector_id="c-1", change={
        "entity_type": "TechTransferReport", "entity_id": "doc-TTR-001", "version": 2,
        "fields": {"sample": "SMP-1"},
    })
    assert set(ev) == _ENVELOPE_KEYS
    assert ev["affected_subgraph"]["document"] == ["doc-TTR-001"]
    assert ev["affected_subgraph"]["sample"] == ["SMP-1"]
