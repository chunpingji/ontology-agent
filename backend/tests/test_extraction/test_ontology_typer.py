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
    _is_non_entity_span,
    build_class_index,
    predict_segment_classes,
    relevant_classes_for_doc_type,
    seed_labels,
    type_spans,
)


@pytest.fixture(autouse=True)
def _clear_caches():
    """清空进程级缓存（键为 id(engine)，跨测试可能复用 id）。"""
    ontology_typer._index_cache.clear()
    ontology_typer._seed_cache.clear()
    ontology_typer._relevant_cache.clear()
    yield
    ontology_typer._index_cache.clear()
    ontology_typer._seed_cache.clear()
    ontology_typer._relevant_cache.clear()


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
    """最小本体引擎桩：暴露 typer 依赖的只读访问器。"""

    def __init__(self, roots, dp_classes, obj_props=None, subclasses=None):
        self._roots = roots
        self._dp = dp_classes
        self._obj_props = obj_props or {}
        self._subclasses = subclasses or {}

    def get_modules(self):
        return [_Module("m")]

    def get_class_hierarchy(self, key):
        return list(self._roots)

    def data_property_domain_classes(self):
        return list(self._dp)

    def data_property_labels(self):
        return []

    def get_object_properties_by_domain(self, class_iri):
        return self._obj_props.get(class_iri, [])

    def get_subclasses(self, class_iri, recursive=True):
        return self._subclasses.get(class_iri, [])

    def get_relation_schema(self, class_iri, max_hops=4):
        edges = []
        visited = set()
        frontier = {class_iri}
        for hop in range(1, max_hops + 1):
            nxt = set()
            for d_iri in frontier:
                for prop in self._obj_props.get(d_iri, []):
                    for r_iri in prop.get("range", []):
                        if r_iri in visited:
                            continue
                        visited.add(r_iri)
                        subs = []
                        for s in self._subclasses.get(r_iri, []):
                            subs.append({"iri": s["iri"], "label": s.get("label", "")})
                            visited.add(s["iri"])
                        edges.append({
                            "hop": hop,
                            "predicate_iri": prop["iri"],
                            "predicate_label": prop["iri"],
                            "domain_class_iri": d_iri,
                            "domain_class_label": d_iri,
                            "range_class_iri": r_iri,
                            "range_class_label": r_iri,
                            "range_subclasses": subs,
                            "range_data_properties": [],
                        })
                        nxt.add(r_iri)
                        for s in subs:
                            nxt.add(s["iri"])
            if not nxt:
                break
            frontier = nxt
        return edges


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


# --- _is_non_entity_span 否定名单 -------------------------------------------

@pytest.mark.parametrize("text", [
    "50L", "15℃", "99.5%", "2小时", "15分钟", "3天",
    "123", "1.5", "0",
    "±0.5", "<3", "≤10",
    "3.2-5.8", "10–20", "5~8",
    "",     # 空串
    "A",    # 单字符
])
def test_non_entity_span_filtered(text):
    assert _is_non_entity_span(text) is True


@pytest.mark.parametrize("text", [
    "HRS-1234", "原料药", "离心机", "TLC/HPLC", "乙酸乙酯",
    "RE64202", "642/646车间", "Imp-A", "CMC报告",
    "阿莫西林胶囊", "NOAEL",
])
def test_entity_span_not_filtered(text):
    assert _is_non_entity_span(text) is False


# --- relevant_classes_for_doc_type -------------------------------------------

_DOC_IRI = "iri#doc"
_RANGE_A = "iri#rangeA"
_RANGE_B = "iri#rangeB"
_RANGE_B_SUB = "iri#rangeB_sub"
_DEEP_RANGE = "iri#deepRange"


def _engine_with_obj_props():
    """文档类 → 对象属性 → range 类 + 子类 + 二跳。"""
    root_a = _Node(_RANGE_A, "RangeA", "端点A类")
    root_b = _Node(_RANGE_B, "RangeB", "端点B类", [
        _Node(_RANGE_B_SUB, "RangeBSub", "端点B子类"),
    ])
    deep = _Node(_DEEP_RANGE, "DeepRange", "二跳端点类")
    doc = _Node(_DOC_IRI, "Doc", "文档类")
    return _FakeEngine(
        roots=[doc, root_a, root_b, deep],
        dp_classes=[(_RANGE_A, "端点A类")],
        obj_props={
            _DOC_IRI: [
                {"iri": "p1", "range": [_RANGE_A]},
                {"iri": "p2", "range": [_RANGE_B]},
            ],
            _RANGE_A: [
                {"iri": "p3", "range": [_DEEP_RANGE]},
            ],
        },
        subclasses={
            _RANGE_B: [{"iri": _RANGE_B_SUB, "label": "端点B子类"}],
        },
    )


def test_relevant_classes_one_hop():
    eng = _engine_with_obj_props()
    rel = relevant_classes_for_doc_type(eng, _DOC_IRI)
    assert _RANGE_A in rel
    assert _RANGE_B in rel
    assert _RANGE_B_SUB in rel  # 子类


def test_relevant_classes_two_hops():
    eng = _engine_with_obj_props()
    rel = relevant_classes_for_doc_type(eng, _DOC_IRI)
    assert _DEEP_RANGE in rel  # 二跳


def test_relevant_classes_excludes_doc_class():
    eng = _engine_with_obj_props()
    rel = relevant_classes_for_doc_type(eng, _DOC_IRI)
    assert _DOC_IRI not in rel


def test_relevant_classes_cached():
    eng = _engine_with_obj_props()
    r1 = relevant_classes_for_doc_type(eng, _DOC_IRI)
    r2 = relevant_classes_for_doc_type(eng, _DOC_IRI)
    assert r1 is r2


def test_relevant_classes_empty_for_unknown_class():
    eng = _engine_with_obj_props()
    rel = relevant_classes_for_doc_type(eng, "iri#nonexistent")
    assert len(rel) == 0


# --- seed_labels（文档类型缩窄） -----------------------------------------------

def test_seed_labels_narrowed_by_doc_class():
    eng = _engine_with_obj_props()
    labels = seed_labels(eng, doc_class_iri=_DOC_IRI)
    assert "端点A类" in labels
    assert "端点B类" in labels
    assert "端点B子类" in labels
    assert "二跳端点类" in labels
    assert "文档类" not in labels


def test_seed_labels_generic_and_narrowed_cached_separately():
    eng = _engine_with_obj_props()
    generic = seed_labels(eng)
    narrowed = seed_labels(eng, doc_class_iri=_DOC_IRI)
    assert generic is not narrowed
    assert seed_labels(eng) is generic
    assert seed_labels(eng, doc_class_iri=_DOC_IRI) is narrowed


# --- type_spans with class_iris (桩嵌入器) ------------------------------------

def test_type_spans_with_class_iris_narrows_matching(monkeypatch):
    """class_iris 过滤后，匹配仅在子集中进行。"""
    vectors = {
        "药物制剂": [0.99, 0.1410674],
        "无菌粉针剂": [0.97, 0.2431049],
        "无菌粉针": [1.0, 0.0],
    }
    monkeypatch.setattr(ontology_typer, "get_embedder", lambda: _FakeEmbedder(vectors))
    eng = _engine_with_hierarchy()

    # 全量匹配：应该选到 "无菌粉针剂"（更具体）
    full = type_spans(["无菌粉针"], eng)
    assert full[0]["label"] == "无菌粉针剂"

    # 缩窄到只含父类 → 只能匹配 "药物制剂"
    narrowed = type_spans(["无菌粉针"], eng, class_iris={"iri#drug"})
    assert narrowed[0]["label"] == "药物制剂"


def test_type_spans_non_entity_filtered(monkeypatch):
    """否定名单过滤的 span（如度量值）返回 None。"""
    monkeypatch.setattr(ontology_typer, "get_embedder", lambda: _FakeEmbedder(_VECTORS))
    out = type_spans(["50L", "15分钟", "无菌粉针"], _engine_with_hierarchy())
    assert out[0] is None  # 50L → 度量值
    assert out[1] is None  # 15分钟 → 时间
    assert out[2] is not None  # 正常实体


# --- predict_segment_classes --------------------------------------------------

def _engine_for_segment_pred():
    """4 个类：反应/设备/清洗/产品，向量手工控制余弦排名。"""
    nodes = [
        _Node("iri#react", "Reaction", "化学反应"),
        _Node("iri#equip", "Equipment", "设备"),
        _Node("iri#clean", "Cleaning", "清洗方法"),
        _Node("iri#prod", "Product", "药物产品"),
    ]
    return _FakeEngine(roots=nodes, dp_classes=[])


def test_predict_segment_classes_returns_top_k(monkeypatch):
    """给定 4 个候选类、top_k=2，每段只返回与段文本余弦最高的 2 个类 IRI。"""
    vectors = {
        "化学反应": [1.0, 0.0, 0.0, 0.0],
        "设备": [0.0, 1.0, 0.0, 0.0],
        "清洗方法": [0.0, 0.0, 1.0, 0.0],
        "药物产品": [0.0, 0.0, 0.0, 1.0],
        # 段文本——偏向反应+设备
        "本步骤使用反应釜进行偶联反应"[:256]: [0.8, 0.6, 0.1, 0.05],
        # 段文本——偏向清洗+设备
        "设备清洗方法"[:256]: [0.05, 0.6, 0.9, 0.1],
    }
    monkeypatch.setattr(ontology_typer, "get_embedder", lambda: _FakeEmbedder(vectors))
    eng = _engine_for_segment_pred()

    result = predict_segment_classes(
        ["本步骤使用反应釜进行偶联反应", "设备清洗方法"],
        eng,
        top_k=2,
    )
    # 段1：化学反应(0.8) + 设备(0.6) 是 top-2
    assert result[0] is not None
    assert "iri#react" in result[0]
    assert "iri#equip" in result[0]
    assert len(result[0]) == 2

    # 段2：清洗方法(0.9) + 设备(0.6) 是 top-2
    assert result[1] is not None
    assert "iri#clean" in result[1]
    assert "iri#equip" in result[1]
    assert len(result[1]) == 2


def test_predict_segment_classes_none_when_small_candidate_set(monkeypatch):
    """候选类数量 ≤ top_k → 返回 None（无需缩窄）。"""
    monkeypatch.setattr(ontology_typer, "get_embedder", lambda: _FakeEmbedder({}))
    eng = _engine_for_segment_pred()
    result = predict_segment_classes(["设备清洗"], eng, top_k=10)
    assert result == [None]


def test_predict_segment_classes_none_for_empty_segment(monkeypatch):
    """空段文本 → None。"""
    vectors = {"化学反应": [1.0, 0.0], "设备": [0.0, 1.0], "清洗方法": [0.5, 0.5],
               "药物产品": [0.3, 0.7]}
    monkeypatch.setattr(ontology_typer, "get_embedder", lambda: _FakeEmbedder(vectors))
    eng = _engine_for_segment_pred()
    result = predict_segment_classes(["", "   "], eng, top_k=2)
    assert result == [None, None]


def test_predict_segment_classes_degrades_without_embedder(monkeypatch):
    """无嵌入器 → 全 None。"""
    monkeypatch.setattr(ontology_typer, "get_embedder", lambda: None)
    result = predict_segment_classes(["任何文本"], _engine_for_segment_pred())
    assert result == [None]


def test_predict_segment_classes_with_class_iris_filter(monkeypatch):
    """class_iris 过滤后在子集内排名。"""
    vectors = {
        "化学反应": [1.0, 0.0, 0.0, 0.0],
        "设备": [0.0, 1.0, 0.0, 0.0],
        "清洗方法": [0.0, 0.0, 1.0, 0.0],
        "药物产品": [0.0, 0.0, 0.0, 1.0],
        "设备清洗"[:256]: [0.05, 0.6, 0.9, 0.1],
    }
    monkeypatch.setattr(ontology_typer, "get_embedder", lambda: _FakeEmbedder(vectors))
    eng = _engine_for_segment_pred()
    # 只看 equip + clean（2 类），top_k=1 → 必选最近的 1 类
    result = predict_segment_classes(
        ["设备清洗"], eng,
        class_iris={"iri#equip", "iri#clean"},
        top_k=1,
    )
    assert result[0] is not None
    assert len(result[0]) == 1
    assert "iri#clean" in result[0]


# --- 叙述段句组分割 -----------------------------------------------------------


def test_predict_segment_classes_narrative_split(monkeypatch):
    """长叙述段被句组分割后，top-K 结果为各句组并集。"""
    import app.services.extraction.sentence_grouping as sg

    monkeypatch.setattr(sg, "is_narrative_segment", lambda text: True)
    monkeypatch.setattr(sg, "segment_sentences", lambda text: [
        "sent_a", "sent_b", "sent_c", "sent_d",
    ])
    monkeypatch.setattr(sg, "split_by_topic_shift", lambda sents, emb, **kw: [
        ["sent_a", "sent_b"],
        ["sent_c", "sent_d"],
    ])

    vectors = {
        "化学反应": [1.0, 0.0, 0.0, 0.0],
        "设备": [0.0, 1.0, 0.0, 0.0],
        "清洗方法": [0.0, 0.0, 1.0, 0.0],
        "药物产品": [0.0, 0.0, 0.0, 1.0],
        "sent_a sent_b": [0.9, 0.1, 0.0, 0.0],
        "sent_c sent_d": [0.0, 0.1, 0.9, 0.0],
    }
    monkeypatch.setattr(ontology_typer, "get_embedder", lambda: _FakeEmbedder(vectors))
    eng = _engine_for_segment_pred()

    result = predict_segment_classes(["叙述型长段落文本"], eng, top_k=1)
    assert result[0] is not None
    assert "iri#react" in result[0], "组1 top-1 应命中 react"
    assert "iri#clean" in result[0], "组2 top-1 应命中 clean"
    assert len(result[0]) == 2


def test_predict_segment_classes_short_segment_unchanged(monkeypatch):
    """短段 / 键值段不触发分割，走原有单段 top-K 路径。"""
    import app.services.extraction.sentence_grouping as sg

    split_called: list[int] = []
    monkeypatch.setattr(
        sg, "split_by_topic_shift",
        lambda *a, **kw: (split_called.append(1), [a[0]])[1],
    )

    vectors = {
        "化学反应": [1.0, 0.0, 0.0, 0.0],
        "设备": [0.0, 1.0, 0.0, 0.0],
        "清洗方法": [0.0, 0.0, 1.0, 0.0],
        "药物产品": [0.0, 0.0, 0.0, 1.0],
        "设备清洗方法"[:256]: [0.05, 0.6, 0.9, 0.1],
    }
    monkeypatch.setattr(ontology_typer, "get_embedder", lambda: _FakeEmbedder(vectors))
    eng = _engine_for_segment_pred()

    result = predict_segment_classes(["设备清洗方法"], eng, top_k=2)
    assert result[0] is not None
    assert "iri#clean" in result[0]
    assert "iri#equip" in result[0]
    assert len(result[0]) == 2
    assert not split_called, "短段不应触发 split_by_topic_shift"


# --- 告警常量 ----------------------------------------------------------------
def test_domain_bug_warning_constant_nonempty():
    assert isinstance(GET_CLASS_PROPERTIES_DOMAIN_BUG, str)
    assert "get_class_properties" in GET_CLASS_PROPERTIES_DOMAIN_BUG
