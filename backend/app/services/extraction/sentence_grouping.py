"""叙述型长段落的句组语义分割 —— 仅对跨主题长叙述段启用。

验证脚本 (``scripts/verify_sentence_topic_shift.py``) 证实 bge-small-zh 在跨主题
长叙述段（如工艺描述）中，相邻句 cosine 在主题切换处有可辨识的谷值。但对枚举型段落
（键值属性列表、清洗步骤列表、降解条件列表）会过度切分。因此本模块提供三个纯函数，
由调用方 (``ontology_typer.predict_segment_classes``) 仅对叙述型长段落启用。
"""

from __future__ import annotations

import re

# ── CJK 分句 ─────────────────────────────────────────────────────────

_SENT_SPLIT_RE = re.compile(
    r"(?<=[。！？；\n])"
    r"|(?<=[.!?;])\s+"
)

_MIN_SENT_CHARS = 8


def segment_sentences(text: str) -> list[str]:
    """CJK 友好分句：按句末标点切分，过短片段合并到前一句。"""
    raw = _SENT_SPLIT_RE.split(text.strip())
    sents: list[str] = []
    buf = ""
    for frag in raw:
        frag = frag.strip()
        if not frag:
            continue
        buf = f"{buf}{frag}" if buf else frag
        if len(buf) >= _MIN_SENT_CHARS:
            sents.append(buf)
            buf = ""
    if buf:
        if sents:
            sents[-1] = f"{sents[-1]}{buf}"
        else:
            sents.append(buf)
    return sents


# ── 段落类型判定 ──────────────────────────────────────────────────────

_KV_RE = re.compile(r"^\s*[^：:]{1,40}[：:]\s*")
_ENUM_RE = re.compile(r"^\s*(?:\d+[.、）)]\s*|[a-zA-Z][.、）)]\s*|[①②③④⑤⑥⑦⑧⑨⑩])")

_MIN_NARRATIVE_CHARS = 120
_MIN_NARRATIVE_SENTS = 4
_KV_RATIO_THRESHOLD = 0.5
_ENUM_RATIO_THRESHOLD = 0.8


def is_narrative_segment(text: str) -> bool:
    """判断段落是否为叙述型长段落（适合句组切分）。

    排除键值型（50%+ 句子匹配 ``key：value`` 模式）和枚举型（80%+ 以序号开头）
    段落，避免过度切分。
    """
    if len(text) < _MIN_NARRATIVE_CHARS:
        return False
    sents = segment_sentences(text)
    if len(sents) < _MIN_NARRATIVE_SENTS:
        return False
    kv_count = sum(1 for s in sents if _KV_RE.match(s))
    if kv_count / len(sents) >= _KV_RATIO_THRESHOLD:
        return False
    enum_count = sum(1 for s in sents if _ENUM_RE.match(s))
    if enum_count / len(sents) >= _ENUM_RATIO_THRESHOLD:
        return False
    return True


# ── 语义骤降切分 ─────────────────────────────────────────────────────

_DROP_THRESHOLD = 0.08
_ABS_LOW_THRESHOLD = 0.75


def split_by_topic_shift(
    sentences: list[str],
    embedder,
    drop_threshold: float = _DROP_THRESHOLD,
    abs_low_threshold: float = _ABS_LOW_THRESHOLD,
) -> list[list[str]]:
    """按相邻句 cosine 骤降检测语义转移，返回句组列表。

    嵌入器不可用 / 句子过少 → 返回 ``[sentences]``（不切分，优雅降级）。
    """
    if len(sentences) < 2:
        return [sentences] if sentences else []
    if embedder is None or not embedder.is_available():
        return [sentences]

    embedder.embed_many(sentences)
    vecs = [embedder.embed(s) for s in sentences]
    if any(v is None for v in vecs):
        return [sentences]

    boundaries: list[int] = []
    prev_sim = 1.0
    for i in range(len(vecs) - 1):
        sim = sum(a * b for a, b in zip(vecs[i], vecs[i + 1]))
        drop = prev_sim - sim
        if drop >= drop_threshold or sim < abs_low_threshold:
            boundaries.append(i + 1)
        prev_sim = sim

    if not boundaries:
        return [sentences]

    groups: list[list[str]] = []
    prev = 0
    for b in boundaries:
        if prev < b:
            groups.append(sentences[prev:b])
        prev = b
    if prev < len(sentences):
        groups.append(sentences[prev:])
    return groups
