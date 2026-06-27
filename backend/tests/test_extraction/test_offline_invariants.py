"""契约测试：离线抽取不变量（云端 opt-in 双条件门控 + 离线非降级 + 零外发）。

覆盖 [contracts/offline-extraction-invariants.md](../../../specs/008-gliner-ner-extraction/contracts/offline-extraction-invariants.md)
O1–O4、O10：`extract_with_fallback` 仅在 `llm_cloud_enabled AND anthropic_api_key`
双条件下触发云端，否则结构化源原样返回且 `degraded_reason is None`；`degraded` 仅在
云端被显式开启却无法兑现（无 Key / 调用失败 / 返回空）时非空。进度一致性（O5）与
端到端零外发（O9）在 test_offline_pipeline.py（集成）验证。

以确定性桩替换 `extract_entities_with_llm`，不触真实 SDK——风格对齐 conftest 的注入桩做法。
"""

from __future__ import annotations

import asyncio

import pytest

from app.config import settings
from app.services.extraction import llm_extractor

ROWS = [{"设备编号": "CT64201", "设备名称": "压片机A"}]
CLS = "http://slpra.org/equipment#Equipment"


def _run(monkeypatch, *, cloud, key, returns=None, raises=None):
    """以给定 (开关×Key) 象限跑 extract_with_fallback；返回 (entities, degraded, calls)。

    桩记录云端调用次数（calls）；`returns` 预置云端返回值，`raises` 预置云端抛异常。
    """
    monkeypatch.setattr(settings, "llm_cloud_enabled", cloud)
    monkeypatch.setattr(settings, "anthropic_api_key", key)

    calls: list = []

    async def _spy(source_data, target_class_iri, property_schema,
                   few_shot_examples=None, controlled_vocab=None):
        calls.append({"target": target_class_iri})
        if raises is not None:
            raise raises
        return [] if returns is None else returns

    monkeypatch.setattr(llm_extractor, "extract_entities_with_llm", _spy)
    entities, degraded = asyncio.run(
        llm_extractor.extract_with_fallback(ROWS, CLS, property_schema=[])
    )
    return entities, degraded, calls


# --- O1/O3 默认关 + 离线非降级 ----------------------------------------------
def test_default_offline_no_cloud_no_degrade(monkeypatch):
    """O1/O3：默认（云端关、无 Key）→ 0 次云端调用、结构化原样返回、degraded_reason=None。"""
    entities, degraded, calls = _run(monkeypatch, cloud=False, key="")
    assert calls == []
    assert entities == ROWS
    assert degraded is None


def test_cloud_off_with_key_still_offline(monkeypatch):
    """O1/O2：云端关即便有 Key 也不触发——单 Key 不构成 opt-in。"""
    entities, degraded, calls = _run(monkeypatch, cloud=False, key="sk-present")
    assert calls == []
    assert entities == ROWS
    assert degraded is None


# --- O2 opt-in 需双条件 ------------------------------------------------------
def test_cloud_on_with_key_triggers(monkeypatch):
    """O2：仅「云端开 + Key 非空」象限触发云端，返回云端结果、不降级。"""
    cloud_out = [{"equipmentID": "CT64201"}]
    entities, degraded, calls = _run(
        monkeypatch, cloud=True, key="sk-present", returns=cloud_out)
    assert len(calls) == 1
    assert entities == cloud_out
    assert degraded is None


@pytest.mark.parametrize(
    ("cloud", "key", "should_call"),
    [
        (False, "", False),
        (False, "sk", False),
        (True, "", False),    # 开关开但无 Key → 不触发（配置缺失）
        (True, "sk", True),   # 唯一触发象限
    ],
)
def test_four_quadrant_gating(monkeypatch, cloud, key, should_call):
    """O2：开关×Key 四象限——仅双条件齐备象限触发云端调用。"""
    _, _, calls = _run(monkeypatch, cloud=cloud, key=key,
                       returns=[{"x": 1}])
    assert (len(calls) == 1) is should_call


# --- O4 降级语义矩阵（云端开启却无法兑现才降级） -----------------------------
def test_cloud_on_no_key_degrades_config_missing(monkeypatch):
    """O4：云端开但无 Key → 不调云端、回退结构化、degraded_reason 非空（配置缺失）。"""
    entities, degraded, calls = _run(monkeypatch, cloud=True, key="")
    assert calls == []
    assert entities == ROWS
    assert degraded is not None and degraded != ""


def test_cloud_on_call_fails_degrades(monkeypatch):
    """O4：云端开 + Key，但调用抛异常 → 回退结构化、degraded_reason 非空。"""
    entities, degraded, calls = _run(
        monkeypatch, cloud=True, key="sk", raises=RuntimeError("network down"))
    assert len(calls) == 1
    assert entities == ROWS
    assert degraded is not None and degraded != ""


def test_cloud_on_empty_result_degrades(monkeypatch):
    """O4：云端开 + Key，但返回空 → 回退结构化、degraded_reason 非空。"""
    entities, degraded, calls = _run(
        monkeypatch, cloud=True, key="sk", returns=[])
    assert len(calls) == 1
    assert entities == ROWS
    assert degraded is not None and degraded != ""


# --- O10 默认配置零外发尝试 --------------------------------------------------
def test_default_config_never_reaches_cloud(monkeypatch):
    """O10：默认配置下绝不触达云端——即便桩在被调用时抛异常也不会被触发。"""
    monkeypatch.setattr(settings, "llm_cloud_enabled", False)
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-should-not-be-used")

    async def _boom(*a, **k):
        raise AssertionError("默认配置下不得触达云端 extract_entities_with_llm")

    monkeypatch.setattr(llm_extractor, "extract_entities_with_llm", _boom)
    entities, degraded = asyncio.run(
        llm_extractor.extract_with_fallback(ROWS, CLS, property_schema=[]))
    assert entities == ROWS
    assert degraded is None
