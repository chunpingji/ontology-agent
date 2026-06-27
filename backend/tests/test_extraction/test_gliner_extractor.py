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
from app.services.extraction.gliner_extractor import GlinerExtractor, get_gliner_extractor


class _FakeGLiNER:
    """替身 ``gliner.GLiNER``：记录加载入参/计数，预置 predict_entities 输出。"""

    load_count = 0
    last_path: str | None = None
    last_kwargs: dict | None = None
    entities: list[dict] = []
    raise_on_load = False

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
