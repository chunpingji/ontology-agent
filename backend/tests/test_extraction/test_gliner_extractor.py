"""契约测试：GlinerExtractor（本地零样本 NER 提取器）。

覆盖 contracts/gliner-extractor.md C1–C6：绝不抛出 / 进程级单例 / 功能开关 /
强制离线 / 多值聚合 / 标签驱动。以确定性桩替换真实权重（注入假 ``gliner``
模块），不下载模型——风格对齐 test_aligner_semantic.py 的注入桩做法。
"""

from __future__ import annotations

import sys
import types

import pytest

from app.config import settings
from app.services.extraction.gliner_extractor import (
    GlinerExtractor,
    _CJKAwareWordsSplitter,
    _entities_to_spans,
    _install_cjk_words_splitter,
    get_gliner_extractor,
)


class _FakeWordsSplitter:
    """替身 GLiNER ``WordsSplitter`` 工厂：内层 ``.splitter`` 可被注入替换。"""

    def __init__(self):
        self.splitter = object()  # 占位的默认分词器


class _FakeDataProcessor:
    def __init__(self):
        self.words_splitter = _FakeWordsSplitter()


class _FakeGLiNER:
    """替身 ``gliner.GLiNER``：记录加载入参/计数，预置 predict_entities 输出。"""

    load_count = 0
    last_path: str | None = None
    last_kwargs: dict | None = None
    entities: list[dict] = []
    raise_on_load = False

    def __init__(self):
        # 镜像真实 GLiNER 的 ``data_processor.words_splitter.splitter`` 嵌套结构，
        # 使 _install_cjk_words_splitter 的注入路径可被测试覆盖。
        self.data_processor = _FakeDataProcessor()

    @classmethod
    def reset(cls) -> None:
        cls.load_count = 0
        cls.last_path = None
        cls.last_kwargs = None
        cls.entities = []
        cls.raise_on_load = False

    @classmethod
    def from_pretrained(cls, path, **kwargs):
        cls.load_count += 1
        cls.last_path = path
        cls.last_kwargs = kwargs
        if cls.raise_on_load:
            raise RuntimeError("simulated weight load failure")
        return cls()

    def predict_entities(self, text, labels, threshold=None):
        # 真实 GLiNER 仅抽取请求 labels 中声明的标签——桩据此过滤。
        return [e for e in type(self).entities if e.get("label") in labels]


@pytest.fixture()
def fake_gliner(monkeypatch):
    """注入假 gliner 模块 + 默认开启功能开关 + 清 lru_cache 单例。"""
    _FakeGLiNER.reset()
    mod = types.ModuleType("gliner")
    mod.GLiNER = _FakeGLiNER
    monkeypatch.setitem(sys.modules, "gliner", mod)
    monkeypatch.setattr(settings, "gliner_extraction_enabled", True)
    monkeypatch.setattr(settings, "gliner_model_path", "models/gliner_multi-v2.1")
    monkeypatch.setattr(settings, "gliner_threshold", 0.5)
    get_gliner_extractor.cache_clear()
    yield _FakeGLiNER
    get_gliner_extractor.cache_clear()


# --- C4 强制离线 -------------------------------------------------------------
def test_loads_local_files_only(fake_gliner):
    """C4：加载恒用 local_files_only=True 且为本地 gliner_model_path。"""
    ex = GlinerExtractor()
    assert ex.is_available() is True
    assert fake_gliner.last_path == "models/gliner_multi-v2.1"
    assert fake_gliner.last_kwargs.get("local_files_only") is True


# --- C2 进程级单例 -----------------------------------------------------------
def test_singleton_loads_once(fake_gliner):
    """C2：多次 get_gliner_extractor() 返回同一实例，模型仅加载一次。"""
    a = get_gliner_extractor()
    b = get_gliner_extractor()
    assert a is b
    assert a.is_available() is True
    assert a.is_available() is True  # 二次触发不重复加载
    assert fake_gliner.load_count == 1


# --- C3 功能开关 -------------------------------------------------------------
def test_disabled_returns_none(fake_gliner, monkeypatch):
    """C3：gliner_extraction_enabled=False → get_gliner_extractor() 返回 None。"""
    monkeypatch.setattr(settings, "gliner_extraction_enabled", False)
    get_gliner_extractor.cache_clear()
    assert get_gliner_extractor() is None


# --- C1 绝不抛出 -------------------------------------------------------------
def test_never_raises_on_load_failure(fake_gliner):
    """C1：缺权重/加载失败 → is_available()=False、extract_text()={}，不抛出。"""
    fake_gliner.raise_on_load = True
    ex = GlinerExtractor()
    assert ex.is_available() is False
    assert ex.extract_text("某设备 CT64", ["设备"]) == {}
    # 已 _failed → 不重试加载。
    assert ex.is_available() is False
    assert fake_gliner.load_count == 1


def test_never_raises_when_package_missing(monkeypatch):
    """C1：gliner 包缺失（import 失败）→ 不抛出、降级。"""
    monkeypatch.setitem(sys.modules, "gliner", None)  # `from gliner import GLiNER` → ImportError
    monkeypatch.setattr(settings, "gliner_extraction_enabled", True)
    ex = GlinerExtractor()
    assert ex.is_available() is False
    assert ex.extract_text("文本", ["标签"]) == {}


# --- C5 多值聚合 -------------------------------------------------------------
def test_multi_value_aggregates_to_list(fake_gliner):
    """C5：同 label 多命中聚合为 list，单命中为标量。"""
    fake_gliner.entities = [
        {"text": "化合物A", "label": "活性成分"},
        {"text": "化合物B", "label": "活性成分"},
        {"text": "片剂", "label": "剂型"},
    ]
    ex = GlinerExtractor()
    res = ex.extract_text("正文……", ["活性成分", "剂型"])
    assert res["活性成分"] == ["化合物A", "化合物B"]
    assert res["剂型"] == "片剂"


# --- C6 标签驱动 -------------------------------------------------------------
def test_only_returns_requested_labels(fake_gliner):
    """C6：仅返回 labels 中声明的标签键，无额外键。"""
    fake_gliner.entities = [
        {"text": "化合物A", "label": "活性成分"},
        {"text": "噪声", "label": "未声明标签"},
    ]
    ex = GlinerExtractor()
    res = ex.extract_text("正文……", ["活性成分", "剂型"])
    assert set(res.keys()) <= {"活性成分", "剂型"}
    assert "未声明标签" not in res


def test_empty_inputs_return_empty(fake_gliner):
    """空 text / 空 labels → {}（绝不抛出，不触发推理）。"""
    fake_gliner.entities = [{"text": "x", "label": "活性成分"}]
    ex = GlinerExtractor()
    assert ex.extract_text("", ["活性成分"]) == {}
    assert ex.extract_text("文本", []) == {}


# --- A1 字符级中文分词器（长文本 span 召回根因修复）--------------------------
def test_cjk_splitter_splits_han_per_char_keeps_ascii_runs():
    """每个汉字单独成词；ASCII 连写串（批准文号尾号、规格）整体保留；偏移精确回溯。"""
    sp = _CJKAwareWordsSplitter()
    text = "阿莫西林胶囊批准文号国药准字H13020999规格0.25g"
    toks = list(sp(text))
    vals = [t[0] for t in toks]
    assert "阿" in vals and "莫" in vals and "囊" in vals  # 汉字逐字切
    assert "H13020999" in vals  # ASCII 连写整体保留
    assert "0.25g" in vals  # 含小数点的规格整体保留
    assert all(text[s:e] == tok for tok, s, e in toks)  # 偏移精确回溯


def test_cjk_splitter_full_coverage_and_skips_whitespace():
    """连字符/下划线连写整体；汉字逐字；全部非空白字符被覆盖；不产出空白 token。"""
    sp = _CJKAwareWordsSplitter()
    text = "state-of-the-art ID_3 阿莫 A/B"
    toks = list(sp(text))
    vals = [t[0] for t in toks]
    assert "state-of-the-art" in vals  # 连字符连写
    assert "ID_3" in vals  # 下划线连写
    assert "阿" in vals and "莫" in vals  # 汉字逐字
    assert "A/B" in vals  # 斜杠连写
    covered: set[int] = set()
    for _, s, e in toks:
        covered.update(range(s, e))
    assert all(i in covered for i, c in enumerate(text) if not c.isspace())
    assert all(not tok.isspace() for tok, _, _ in toks)


def test_install_cjk_splitter_replaces_inner_splitter():
    """注入：替换 ``data_processor.words_splitter.splitter`` 为字符级中文分词器。"""
    model = _FakeGLiNER()
    assert not isinstance(model.data_processor.words_splitter.splitter, _CJKAwareWordsSplitter)
    _install_cjk_words_splitter(model)
    assert isinstance(model.data_processor.words_splitter.splitter, _CJKAwareWordsSplitter)


def test_install_cjk_splitter_degrades_gracefully_on_unexpected_structure():
    """GLiNER 内部结构变更（无 data_processor）→ 捕获 AttributeError、不抛出。"""
    _install_cjk_words_splitter(object())  # 不抛出即通过


def test_ensure_model_injects_cjk_splitter(fake_gliner):
    """加载真实路径会自动注入字符级中文分词器（根因修复随模型加载生效）。"""
    ex = GlinerExtractor()
    assert ex.is_available() is True
    assert isinstance(
        ex._model.data_processor.words_splitter.splitter, _CJKAwareWordsSplitter
    )


# --- A1 _entities_to_spans 去重/消重叠 --------------------------------------
def test_entities_to_spans_filters_disallowed_and_sorts_by_start():
    """过滤越界 label，按 start 升序。"""
    ents = [
        {"text": "乙", "label": "L", "start": 5, "end": 6, "score": 0.9},
        {"text": "甲", "label": "L", "start": 0, "end": 1, "score": 0.8},
        {"text": "丙", "label": "X", "start": 2, "end": 3, "score": 0.99},
    ]
    spans = _entities_to_spans(ents, {"L"})
    assert [s["text"] for s in spans] == ["甲", "乙"]


def test_entities_to_spans_resolves_overlap_by_score():
    """同区间多 label / 嵌套重叠 → 保留高分者，得到扁平无重叠 span 集。"""
    ents = [
        {"text": "阿莫西林", "label": "A", "start": 0, "end": 4, "score": 0.6},
        {"text": "阿莫西林胶囊", "label": "B", "start": 0, "end": 6, "score": 0.9},
        {"text": "胶囊", "label": "C", "start": 4, "end": 6, "score": 0.5},
        {"text": "规格", "label": "A", "start": 8, "end": 10, "score": 0.7},
    ]
    spans = _entities_to_spans(ents, {"A", "B", "C"})
    assert [(s["start"], s["end"], s["label"]) for s in spans] == [(0, 6, "B"), (8, 10, "A")]
    assert all(spans[i]["end"] <= spans[i + 1]["start"] for i in range(len(spans) - 1))


def test_entities_to_spans_keeps_adjacent_non_overlapping():
    """相邻（end==下一 start）不算交集，两 span 均保留。"""
    ents = [
        {"text": "甲", "label": "L", "start": 0, "end": 2, "score": 0.5},
        {"text": "乙", "label": "L", "start": 2, "end": 4, "score": 0.5},
    ]
    spans = _entities_to_spans(ents, {"L"})
    assert len(spans) == 2
