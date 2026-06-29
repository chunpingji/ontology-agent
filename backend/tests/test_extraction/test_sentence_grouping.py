"""sentence_grouping 模块单测。"""

from __future__ import annotations

import pytest

from app.services.extraction.sentence_grouping import (
    is_narrative_segment,
    segment_sentences,
    split_by_topic_shift,
)


# ── segment_sentences ────────────────────────────────────────────────


def test_segment_sentences_cjk():
    text = "步骤一采用甲苯为溶剂。加入起始物料A。升温至80℃反应4小时。降温得到中间体B。"
    sents = segment_sentences(text)
    assert len(sents) == 4
    assert sents[0] == "步骤一采用甲苯为溶剂。"
    assert sents[-1] == "降温得到中间体B。"


def test_segment_sentences_merge_short():
    text = "概述。一。二。这是一段较长的叙述文本内容。"
    sents = segment_sentences(text)
    assert all(len(s) >= 8 for s in sents)
    assert any("概述" in s for s in sents)


def test_segment_sentences_english():
    text = "First sentence here. Second one here too. Third sentence follows."
    sents = segment_sentences(text)
    assert len(sents) == 3


def test_segment_sentences_mixed_punctuation():
    text = "反应完成！过滤得到产物。收率85%？确认无误；结束。"
    sents = segment_sentences(text)
    assert len(sents) >= 2
    assert all(len(s) >= 8 for s in sents)


def test_segment_sentences_empty():
    assert segment_sentences("") == []
    assert segment_sentences("   ") == []


def test_segment_sentences_single_long():
    text = "这是一个没有句末标点的很长文本段落"
    sents = segment_sentences(text)
    assert len(sents) == 1
    assert sents[0] == text


# ── is_narrative_segment ─────────────────────────────────────────────


def test_is_narrative_long_text():
    text = (
        "以1234-4和HRS-1234 SMC为起始物料，通过Suzuki偶联反应，得到中间体1234-3。"
        "中间体1234-3在酸催化条件下，脱除叔丁基亚磺酰胺保护基，成盐得到中间体1234-2 HCl。"
        "中间体1234-2 HCl与起始物料HRS-1234反应，经碱性条件缩合得到中间体1234-1。"
        "中间体1234-1在强碱条件下，脱除苄基保护基得到HRS-1234粗品。"
        "粗品经重结晶纯化，得到HRS-1234原料药。"
    )
    assert is_narrative_segment(text) is True


def test_is_narrative_rejects_short():
    assert is_narrative_segment("短文本") is False
    assert is_narrative_segment("A" * 50) is False


def test_is_narrative_rejects_kv():
    text = (
        "项目名称：HRS-1234。"
        "项目代码：DP-001。"
        "化学名：某化合物。"
        "CAS号：123-45-6。"
        "分子量：456.78。"
        "性状：白色粉末。"
    )
    assert is_narrative_segment(text) is False


def test_is_narrative_rejects_enum():
    text = (
        "1. 第一步操作说明内容。"
        "2. 第二步操作说明内容。"
        "3. 第三步操作说明内容。"
        "4. 第四步操作说明内容。"
        "5. 第五步操作说明内容。"
    )
    assert is_narrative_segment(text) is False


def test_is_narrative_rejects_few_sentences():
    text = "这是第一句话，内容较长可以超过最小字符限制。" * 10
    sents = segment_sentences(text)
    if len(sents) < 4:
        assert is_narrative_segment(text) is False


# ── split_by_topic_shift ─────────────────────────────────────────────


class _FakeEmbedder:
    """确定性桩嵌入器：每个文本映射到预设向量。"""

    def __init__(self, vectors: dict[str, list[float]]):
        self._vecs = vectors

    def is_available(self) -> bool:
        return True

    def embed_many(self, texts: list[str]) -> None:
        pass

    def embed(self, text: str) -> list[float] | None:
        return self._vecs.get(text)


def test_split_by_topic_shift_detects_drop():
    sents = ["sent_a", "sent_b", "sent_c", "sent_d"]
    vecs = {
        "sent_a": [1.0, 0.0],
        "sent_b": [0.98, 0.2],   # high sim with a
        "sent_c": [0.0, 1.0],    # sharp drop from b
        "sent_d": [0.1, 0.99],   # high sim with c
    }
    embedder = _FakeEmbedder(vecs)
    groups = split_by_topic_shift(sents, embedder, drop_threshold=0.08, abs_low_threshold=0.3)
    assert len(groups) == 2
    assert groups[0] == ["sent_a", "sent_b"]
    assert groups[1] == ["sent_c", "sent_d"]


def test_split_no_shift():
    sents = ["sent_a", "sent_b", "sent_c"]
    vecs = {
        "sent_a": [1.0, 0.0],
        "sent_b": [0.99, 0.14],
        "sent_c": [0.98, 0.2],
    }
    embedder = _FakeEmbedder(vecs)
    groups = split_by_topic_shift(sents, embedder)
    assert len(groups) == 1
    assert groups[0] == sents


def test_split_degrades_without_embedder():
    sents = ["sent_a", "sent_b", "sent_c"]
    groups = split_by_topic_shift(sents, None)
    assert groups == [sents]


def test_split_degrades_unavailable_embedder():

    class _Unavailable:
        def is_available(self):
            return False

        def embed_many(self, texts):
            pass

        def embed(self, text):
            return None

    sents = ["sent_a", "sent_b"]
    groups = split_by_topic_shift(sents, _Unavailable())
    assert groups == [sents]


def test_split_single_sentence():
    groups = split_by_topic_shift(["only_one"], _FakeEmbedder({}))
    assert groups == [["only_one"]]


def test_split_empty():
    groups = split_by_topic_shift([], _FakeEmbedder({}))
    assert groups == []


def test_split_abs_low_threshold():
    sents = ["sent_a", "sent_b", "sent_c"]
    vecs = {
        "sent_a": [1.0, 0.0],
        "sent_b": [0.6, 0.8],   # cosine ~0.6, below abs_low=0.75
        "sent_c": [0.5, 0.87],
    }
    embedder = _FakeEmbedder(vecs)
    groups = split_by_topic_shift(
        sents, embedder, drop_threshold=1.0, abs_low_threshold=0.75,
    )
    assert len(groups) >= 2
