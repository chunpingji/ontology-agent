"""语义相似度后端：本地 sentence-transformers 向量嵌入 + 余弦相似度。

服务于实体对齐的「语义模糊匹配」（``aligner.align_entity``）：前置条件为实体
类别相等，候选与既有个体的标签向量余弦相似度超过阈值时判定为同一实体。

设计要点：
- 可插拔 ``Embedder`` 协议，便于测试注入确定性桩实现，无需下载真实模型。
- 默认 ``SentenceTransformerEmbedder`` 惰性加载模型；包缺失/加载失败时
  ``is_available()`` 返回 ``False``，调用方据此回退到纯字面匹配（沿用
  ``extract_with_fallback`` 的优雅降级思路，FR-007 / R3）。
- 文本→向量按文本缓存，避免同一作业内重复编码既有个体标签；编码默认归一化，
  故余弦相似度等价于点积。
"""

from __future__ import annotations

import logging
import math
from functools import lru_cache
from typing import Protocol, runtime_checkable

from app.config import settings

logger = logging.getLogger(__name__)


@runtime_checkable
class Embedder(Protocol):
    """对齐器只依赖此协议；真实实现与测试桩均可注入。"""

    def is_available(self) -> bool: ...

    def embed(self, text: str) -> list[float] | None: ...

    def embed_many(self, texts: list[str]) -> None: ...


def cosine_similarity(a: list[float] | None, b: list[float] | None) -> float:
    """余弦相似度；任一为空或零向量返回 0.0。向量已归一化时等价于点积。"""
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


class SentenceTransformerEmbedder:
    """基于 sentence-transformers 的本地嵌入器（惰性加载 + 文本缓存）。

    模型在首次编码时加载；若依赖未安装或加载失败，``is_available()`` 返回
    ``False`` 且后续调用为零开销 no-op，对齐器据此回退字面匹配。
    """

    def __init__(self, model_name: str) -> None:
        self._model_name = model_name
        self._model = None
        self._failed = False
        self._cache: dict[str, list[float]] = {}

    def _ensure_model(self):
        if self._model is not None or self._failed:
            return self._model
        try:
            from sentence_transformers import SentenceTransformer

            logger.info("加载 sentence-transformers 模型：%s", self._model_name)
            self._model = SentenceTransformer(self._model_name)
        except Exception:  # pragma: no cover - 依赖缺失/下载失败路径
            logger.warning(
                "sentence-transformers 不可用（未安装或模型加载失败）；语义对齐回退"
                "字面匹配。启用语义对齐请安装：uv sync --extra semantic",
                exc_info=True,
            )
            self._failed = True
        return self._model

    def is_available(self) -> bool:
        return self._ensure_model() is not None

    def embed_many(self, texts: list[str]) -> None:
        """批量编码未缓存文本并写入缓存（单次 encode，显著快于逐条）。"""
        model = self._ensure_model()
        if model is None:
            return
        pending = sorted({t for t in texts if t and t not in self._cache})
        if not pending:
            return
        try:
            vectors = model.encode(pending, normalize_embeddings=True)
        except Exception:  # pragma: no cover - 编码异常路径
            logger.warning("嵌入编码失败，本批回退字面匹配", exc_info=True)
            return
        for text, vec in zip(pending, vectors):
            self._cache[text] = [float(x) for x in vec]

    def embed(self, text: str) -> list[float] | None:
        if not text:
            return None
        if text not in self._cache:
            self.embed_many([text])
        return self._cache.get(text)


@lru_cache(maxsize=1)
def get_embedder() -> Embedder | None:
    """进程级单例嵌入器；语义对齐关闭时返回 ``None``。

    不在此处触发模型加载（惰性），故关闭/缺包场景零开销。返回的实例自带文本
    缓存，跨候选、跨作业复用既有个体标签的向量。
    """
    if not settings.semantic_alignment_enabled:
        return None
    return SentenceTransformerEmbedder(settings.semantic_embedding_model)
