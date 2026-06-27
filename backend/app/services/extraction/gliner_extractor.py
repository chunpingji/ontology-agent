"""本地零样本 NER 后端：GLiNER 标签驱动实体抽取（离线、air-gap 友好）。

服务于自由文本的实体召回——Word 正文段落（US2）与 Excel 自由文本列（US3）——
以本体类的数据属性 label 作为 GLiNER 抽取标签，零样本抽取实体值。

设计要点（逐字镜像 ``semantic.py:SentenceTransformerEmbedder`` + ``get_embedder``，
research R7）：
- 可插拔 ``GlinerExtractor`` 协议，便于测试注入确定性桩实现，无需下载真实权重。
- 默认实现惰性加载模型：``GLiNER.from_pretrained(path, local_files_only=True)``
  **强制本地**，绝不远程解析 repo id（air-gap 零外发，FR-011）。包缺失/缺权重/
  加载失败时 ``is_available()`` 返回 ``False``、``extract_text()`` 返回 ``{}``，
  调用方据此静默跳过 NER 分支、结构化主路径零回归（优雅降级，FR-012）。
- GLiNER 推理为 CPU 同步阻塞；本模块只暴露同步 ``extract_text``，pipeline 侧经
  ``asyncio.to_thread`` 包装调用以不阻塞事件循环（异步包装是调用方职责，FR-015）。
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)

_MDEBERTA_V3_BASE_CONFIG = {
    "model_type": "deberta-v2",
    "hidden_size": 768,
    "num_hidden_layers": 12,
    "num_attention_heads": 12,
    "intermediate_size": 3072,
    "hidden_act": "gelu",
    "hidden_dropout_prob": 0.1,
    "attention_probs_dropout_prob": 0.1,
    "max_position_embeddings": 512,
    "type_vocab_size": 0,
    "initializer_range": 0.02,
    "layer_norm_eps": 1e-7,
    "relative_attention": True,
    "max_relative_positions": 256,
    "position_biased_input": False,
    "pos_att_type": ["c2p", "p2c"],
    "share_att_key": True,
    "norm_rel_ebd": "layer_norm",
    "vocab_size": 250002,
    "pad_token_id": 0,
    "pooler_dropout": 0,
    "pooler_hidden_act": "gelu",
    "pooler_hidden_size": 768,
}


def _patch_encoder_config(model_path: str) -> None:
    """Inject encoder_config into gliner_config.json if missing.

    Without this, GLiNER calls AutoConfig.from_pretrained("microsoft/mdeberta-v3-base")
    which fails in offline/air-gap mode. The encoder_config only describes the backbone
    architecture (shapes, layer count); actual weights come from the GLiNER checkpoint.
    """
    config_path = Path(model_path) / "gliner_config.json"
    if not config_path.exists():
        return
    try:
        cfg = json.loads(config_path.read_text())
    except Exception:
        return
    if cfg.get("encoder_config") is not None:
        return
    cfg["encoder_config"] = _MDEBERTA_V3_BASE_CONFIG
    try:
        config_path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))
        logger.info("已向 %s 注入 encoder_config（离线模式所需）", config_path)
    except OSError:
        logger.debug("无法写入 %s，跳过 encoder_config 注入", config_path)


class GlinerExtractor:
    """基于 GLiNER 的本地零样本 NER 提取器（惰性加载 + 优雅降级）。

    既是默认实现，也定义调用方依赖的对外形态（``is_available()`` /
    ``extract_text()``）——测试以鸭子类型注入确定性桩，无需继承（对齐
    ``semantic.py`` 的可插拔范式）。

    模型在首次 ``is_available()``/``extract_text()`` 时加载；依赖未安装或加载失败
    时 ``is_available()`` 返回 ``False`` 且后续调用为零开销 no-op，pipeline 据此
    跳过 NER 分支。
    """

    def __init__(self) -> None:
        self._model = None
        self._failed = False

    def _ensure_model(self):
        if self._model is not None or self._failed:
            return self._model
        try:
            from gliner import GLiNER

            logger.info("加载 GLiNER 本地权重：%s", settings.gliner_model_path)
            _patch_encoder_config(settings.gliner_model_path)
            self._model = GLiNER.from_pretrained(
                settings.gliner_model_path, local_files_only=True
            )
        except Exception:  # pragma: no cover - 依赖缺失/缺权重/加载失败路径
            logger.warning(
                "GLiNER 不可用（未安装、缺本地权重或加载失败）；自由文本 NER 召回回退"
                "跳过，结构化主路径不受影响。启用本地 NER 请安装 `uv sync --extra "
                "gliner` 并经 scripts/fetch_models.sh 预置权重到 %s",
                settings.gliner_model_path,
                exc_info=True,
            )
            self._failed = True
        return self._model

    def is_available(self) -> bool:
        return self._ensure_model() is not None

    def extract_text(
        self, text: str, labels: list[str], threshold: float | None = None
    ) -> dict[str, str | list[str]]:
        """对 ``text`` 跑零样本 NER，返回 ``{label: value | [values]}``。

        同一 label 多命中聚合为 ``list``（research R9），单命中为标量；无命中的
        label 不出现在结果中（标签驱动，C6）。不可用 / 空 text / 空 labels →
        ``{}``（绝不抛出，C1）。
        """
        if not text or not labels:
            return {}
        model = self._ensure_model()
        if model is None:
            return {}
        thr = settings.gliner_threshold if threshold is None else threshold
        try:
            entities = model.predict_entities(text, labels, threshold=thr)
        except Exception:  # pragma: no cover - 推理异常路径
            logger.warning("GLiNER 推理失败，本次跳过 NER 召回", exc_info=True)
            return {}

        allowed = set(labels)
        result: dict[str, str | list[str]] = {}
        for ent in entities:
            label = ent.get("label")
            value = ent.get("text")
            if label not in allowed or not value:
                continue
            if label in result:
                existing = result[label]
                if isinstance(existing, list):
                    existing.append(value)
                else:
                    result[label] = [existing, value]
            else:
                result[label] = value
        return result

    def extract_text_with_spans(
        self, text: str, labels: list[str], threshold: float | None = None
    ) -> list[dict]:
        """对 ``text`` 跑零样本 NER，保留位置信息。

        返回 ``[{start, end, text, label, score}]``，按 ``start`` 升序排列。
        不可用 / 空 text / 空 labels → ``[]``（绝不抛出）。
        """
        if not text or not labels:
            return []
        model = self._ensure_model()
        if model is None:
            return []
        thr = settings.gliner_threshold if threshold is None else threshold
        try:
            entities = model.predict_entities(text, labels, threshold=thr)
        except Exception:
            logger.warning("GLiNER 推理失败，本次跳过 NER span 召回", exc_info=True)
            return []

        return _entities_to_spans(entities, set(labels))

    def extract_batch_with_spans(
        self, texts: list[str], labels: list[str], threshold: float | None = None
    ) -> list[list[dict]]:
        """对一批 ``texts`` 一次性跑零样本 NER，保留位置信息。

        返回与 ``texts`` 等长的 span 列表的列表；第 i 项对应 ``texts[i]`` 的
        ``[{start, end, text, label, score}]``（按 start 升序）。批量推理远快于
        逐条调用（整文档标注的关键优化）。不可用 / 空输入 → 等长的空列表列表。
        """
        if not texts or not labels:
            return [[] for _ in texts]
        model = self._ensure_model()
        if model is None:
            return [[] for _ in texts]
        thr = settings.gliner_threshold if threshold is None else threshold

        # 空串不送模型（GLiNER 对空输入行为不确定）；占位以保持索引对齐。
        idx_nonempty = [i for i, t in enumerate(texts) if t]
        payload = [texts[i] for i in idx_nonempty]
        out: list[list[dict]] = [[] for _ in texts]
        if not payload:
            return out
        try:
            batched = model.batch_predict_entities(payload, labels, threshold=thr)
        except Exception:
            logger.warning("GLiNER 批量推理失败，本次跳过 NER span 召回", exc_info=True)
            return out

        allowed = set(labels)
        for slot, entities in zip(idx_nonempty, batched):
            out[slot] = _entities_to_spans(entities, allowed)
        return out


def _entities_to_spans(entities: list[dict], allowed: set[str]) -> list[dict]:
    """GLiNER predict_entities 输出 → 规整 span 列表（过滤越界 label、按 start 排序）。"""
    spans = []
    for ent in entities:
        label = ent.get("label")
        value = ent.get("text")
        if label not in allowed or not value:
            continue
        spans.append({
            "start": ent.get("start", 0),
            "end": ent.get("end", 0),
            "text": value,
            "label": label,
            "score": round(ent.get("score", 0.0), 4),
        })
    spans.sort(key=lambda s: s["start"])
    return spans


@lru_cache(maxsize=1)
def get_gliner_extractor() -> GlinerExtractor | None:
    """进程级单例提取器；本地 NER 关闭时返回 ``None``。

    不在此处触发模型加载（惰性），故关闭/缺包场景零开销。
    """
    if not settings.gliner_extraction_enabled:
        return None
    return GlinerExtractor()
