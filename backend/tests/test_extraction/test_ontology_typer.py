"""本体语义归类单测（ontology_typer）。

覆盖两阶段标注的第二阶段：
- ``seed_labels``：种子标签源自数据属性 domain 反查 + 根/近根类，去重保序、有界；
- ``type_spans``：span 文本嵌入余弦匹配到**最具体**的本体类（深度并列）、阈值 0.50
  以下丢弃、嵌入器不可用时优雅降级（全 None）。

注入确定性桩嵌入器（不下载真实模型），风格对齐 test_aligner_semantic 的 _FakeEmbedder。
"""

from __future__ import annotations

import pytest

from app.services.extraction import ontology_typer
from app.services.extraction.ontology_typer import (
    GET_CLASS_PROPERTIES_DOMAIN_BUG,
    build_class_index,
    seed_labels,
    type_spans,
)


@pytest.fixture(autouse=True)
def _clear_caches():
    """清空进程级缓存（键为 id(engine)，跨测试可能复用 id）。"""
    ontology_typer._index_cache.clear()
    ontology_typer._seed_cache.clear()
    yield
    ontology_typer._index_cache.clear()
    ontology_typer._seed_cache.clear()


class _Node:
    def __init__(self, iri, name, label, children=None):
        self.iri = iri
        self.name = name
        self.label = label
        self.children = children or []


class _Module:
    def __init__(self, key):
        self.key = key


class _FakeEngine:
    """最小本体引擎桩：暴露 typer 依赖的三个只读访问器。"""

    def __init__(self, roots, dp_classes):
        self._roots = roots
        self._dp = dp_classes

    def get_modules(self):
        return [_Module("m")]

    def get_class_hierarchy(self, key):
        return list(self._roots)

    def data_property_domain_classes(self):
        return list(self._dp)

    def data_property_labels(self):
        return []


class _FakeEmbedder:
    """确定性桩：按预设字典把文本映射到（已归一化的）向量。"""

    def __init__(self, vectors: dict[str, list[float]]):
        self._vectors = vectors

    def is_available(self) -> bool:
        return True

    def embed_many(self, texts):
        return None

    def embed(self, text: str):
        return self._vectors.get(text)


# 父类（depth0）与子类（depth1）：余弦与查询都很高且互相接近（<0.05），用于最具体并列。
_PARENT = _Node("iri#drug", "Drug", "药物制剂")
_CHILD = _Node("iri#sterile", "SterilePowder", "无菌粉针剂")
_PARENT.children = [_CHILD]

# 归一化向量：q·parent=0.99，q·child=0.97（均 ≥0.50 且相差 0.02<0.05）。
_VECTORS = {
    "药物制剂": [0.99, 0.1410674],
    "无菌粉针剂": [0.97, 0.2431049],
    "无菌粉针": [1.0, 0.0],       # span 文本，与 q 同向
    "随机噪声词": [0.0, 1.0],      # 与两类都正交 → 余弦 0
}


def _engine_with_hierarchy():
    return _FakeEngine([_PARENT], dp_classes=[])


# --- seed_labels ------------------------------------------------------------
def test_seed_labels_from_domain_dedup_ordered():
    """种子标签 = 数据属性 domain 类标签 + 根/近根类，去重、按首次出现保序、有界。"""
    eng = _FakeEngine(
        [],
        dp_classes=[
            ("iri#a", "药物产品"),
            ("iri#b", "设备"),
            ("iri#a2", "药物产品"),  # 重复 label
            ("iri#c", ""),            # 空 label 跳过
        ],
    )
    assert seed_labels(eng) == ["药物产品", "设备"]


def test_seed_labels_includes_root_classes():
    """无数据属性的模块根类（depth == 0）出现在种子标签中，深层子类排除。"""
    root = _Node("iri#drug", "Drug", "药物产品")
    child = _Node("iri#sterile", "SterilePowder", "无菌粉针剂")
    grandchild = _Node("iri#sub", "Sub", "深层子类")
    child.children = [grandchild]
    root.children = [child]
    eng = _FakeEngine([root], dp_classes=[])
    labels = seed_labels(eng)
    assert "药物产品" in labels       # depth 0
    assert "无菌粉针剂" not in labels  # depth 1 → 排除（只取根类）
    assert "深层子类" not in labels    # depth 2 → 排除


def test_seed_labels_cached_per_engine():
    """同一引擎重复调用走缓存（返回等值）。"""
    eng = _FakeEngine([], dp_classes=[("iri#a", "设备")])
    assert seed_labels(eng) == ["设备"]
    assert seed_labels(eng) == ["设备"]


# --- build_class_index ------------------------------------------------------
def test_build_class_index_carries_depth():
    """层级扁平化携带深度：root=0，子类=1。"""
    index = build_class_index(_engine_with_hierarchy())
    by_label = {e.label: e for e in index}
    assert by_label["药物制剂"].depth == 0
    assert by_label["无菌粉针剂"].depth == 1


# --- type_spans -------------------------------------------------------------
def test_type_spans_picks_most_specific_above_threshold(monkeypatch):
    """近似并列中取最深（最具体）类；命中 ≥0.50，score 为余弦。"""
    monkeypatch.setattr(ontology_typer, "get_embedder", lambda: _FakeEmbedder(_VECTORS))
    out = type_spans(["无菌粉针"], _engine_with_hierarchy())
    assert out[0] is not None
    assert out[0]["label"] == "无菌粉针剂"          # 子类胜出（更具体）
    assert out[0]["iri"] == "iri#sterile"
    assert out[0]["score"] == pytest.approx(0.97, abs=1e-3)


def test_type_spans_drops_below_threshold(monkeypatch):
    """最高余弦 <0.50 → 丢弃该 span（返回 None）。"""
    monkeypatch.setattr(ontology_typer, "get_embedder", lambda: _FakeEmbedder(_VECTORS))
    out = type_spans(["随机噪声词"], _engine_with_hierarchy())
    assert out == [None]


def test_type_spans_degrades_without_embedder(monkeypatch):
    """嵌入器不可用 → 全 None（结构化主路径零回归）。"""
    monkeypatch.setattr(ontology_typer, "get_embedder", lambda: None)
    out = type_spans(["无菌粉针", "随机噪声词"], _engine_with_hierarchy())
    assert out == [None, None]


def test_type_spans_empty_text_is_none(monkeypatch):
    """空文本 span → None（不浪费一次编码）。"""
    monkeypatch.setattr(ontology_typer, "get_embedder", lambda: _FakeEmbedder(_VECTORS))
    out = type_spans(["", "无菌粉针"], _engine_with_hierarchy())
    assert out[0] is None
    assert out[1] is not None


# --- 告警常量 ----------------------------------------------------------------
def test_domain_bug_warning_constant_nonempty():
    assert isinstance(GET_CLASS_PROPERTIES_DOMAIN_BUG, str)
    assert "get_class_properties" in GET_CLASS_PROPERTIES_DOMAIN_BUG
