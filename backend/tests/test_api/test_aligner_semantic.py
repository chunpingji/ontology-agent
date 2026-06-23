"""语义实体对齐单测（aligner.align_entity）。

覆盖：字面差异大但语义同义→语义命中；前置条件「实体类别相等」门控；无嵌入器
时退化为字面匹配；语义低于阈值→新建；精确 ID 短路优先。

注入确定性桩嵌入器，不下载真实模型；风格对齐 conftest 的 FakeOntologyEngine。
"""

from __future__ import annotations

from app.services.extraction.aligner import align_entity
from app.services.ontology_engine import IndividualInfo

CLS = "http://slpra.org/drug#Drug"
OTHER = "http://slpra.org/equipment#Equipment"


class _FakeEngine:
    """返回预置个体；模拟 owlready2 cls.instances() 可能混入非目标类个体。"""

    def __init__(self, individuals: list[IndividualInfo]):
        self._individuals = individuals

    def get_individuals(self, class_iri: str):
        return list(self._individuals)


class _FakeEmbedder:
    """确定性桩：按预设字典把文本映射到向量，从而精确制造语义相似度。"""

    def __init__(self, vectors: dict[str, list[float]]):
        self._vectors = vectors

    def is_available(self) -> bool:
        return True

    def embed_many(self, texts):  # 桩无需预热缓存
        return None

    def embed(self, text: str):
        return self._vectors.get(text)


def _ind(iri: str, label: str, cls: str = CLS) -> IndividualInfo:
    return IndividualInfo(iri=iri, name=label, class_iris=[cls], label_zh=label)


def test_semantic_match_when_lexical_low():
    """字面差异大但语义同义（同药异名）→ 语义命中 merge。"""
    existing = [_ind("d#1", "对乙酰氨基酚")]
    cand = {"drugName": "扑热息痛"}
    vectors = {"扑热息痛": [1.0, 0.0], "对乙酰氨基酚": [0.96, 0.28]}  # cos≈0.96
    res = align_entity(
        cand, CLS, _FakeEngine(existing), label_property="drugName",
        embedder=_FakeEmbedder(vectors), semantic_threshold=0.82,
    )
    assert res.action == "merge"
    assert res.match_iri == "d#1"
    assert res.method == "semantic"
    assert res.match_score >= 0.82


def test_class_equality_gate_blocks_other_class():
    """前置条件：类别不等的个体即使语义相同也不参与匹配。"""
    existing = [_ind("e#1", "扑热息痛", cls=OTHER)]  # 错误类别
    cand = {"drugName": "扑热息痛"}
    vectors = {"扑热息痛": [1.0, 0.0]}
    res = align_entity(
        cand, CLS, _FakeEngine(existing), label_property="drugName",
        embedder=_FakeEmbedder(vectors), semantic_threshold=0.82,
    )
    assert res.action == "new"
    assert res.method == "none"


def test_lexical_match_without_embedder():
    """未提供嵌入器 → 退回字面匹配，历史行为不变。"""
    existing = [_ind("d#1", "压片机A")]
    cand = {"equipmentName": "压片机A"}
    res = align_entity(
        cand, CLS, _FakeEngine(existing), label_property="equipmentName",
        embedder=None,
    )
    assert res.action == "merge"
    assert res.method == "lexical"
    assert res.match_score >= 0.85


def test_semantic_below_threshold_is_new():
    """语义相似度低于阈值且字面不达标 → 新建实体。"""
    existing = [_ind("d#1", "阿司匹林")]
    cand = {"drugName": "扑热息痛"}
    vectors = {"扑热息痛": [1.0, 0.0], "阿司匹林": [0.2, 0.98]}  # cos≈0.2
    res = align_entity(
        cand, CLS, _FakeEngine(existing), label_property="drugName",
        embedder=_FakeEmbedder(vectors), semantic_threshold=0.82,
    )
    assert res.action == "new"


def test_exact_id_short_circuits_before_fuzzy():
    """精确 ID 命中优先于字面/语义模糊匹配。"""
    ind = _ind("d#1", "随便起的名")
    ind.properties = {"http://slpra.org/drug#drugID": "D-001"}
    cand = {"drugID": "D-001", "drugName": "完全不同的名字"}
    res = align_entity(
        cand, CLS, _FakeEngine([ind]), id_property="drugID", label_property="drugName",
    )
    assert res.action == "merge"
    assert res.method == "id"
    assert res.match_score == 1.0
