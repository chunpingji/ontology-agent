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

import logging
import re
from functools import lru_cache

from app.config import settings

logger = logging.getLogger(__name__)


class _CJKAwareWordsSplitter:
    """字符级中文友好分词器：每个汉字单独成词，ASCII 连写串整体成词。

    根因修复（research 长文本 span NER）：GLiNER 默认 ``WhitespaceTokenSplitter``
    （正则 ``\\w+(?:[-_]\\w+)*|\\S``）把无空格的中文整句坍缩为单一 word-token，
    而 GLiNER 仅在 word-token 边界枚举候选 span——导致整句被整体吞入一个 span、
    召回崩塌（阈值无法补救，因为未被切出的边界根本不会被打分）。

    本分词器在每个汉字边界切词，使 GLiNER 能在字级粒度枚举 span；ASCII 连写串
    （批准文号尾部 ``H13020999``、规格 ``0.25g``、标识符 ``ID_3``）保持整体，
    避免被无意义地拆碎。非空白且非上述两类的单字符（标点、全角符号等）各自成词，
    保证每个非空白字符都被产出——偏移精确、覆盖完整、下游 span 可精确回溯。

    与 GLiNER ``TokenSplitterBase`` 同形：``__call__`` 产出 ``(token, start, end)``，
    偏移为相对 ``text`` 的字符下标。
    """

    _TOKEN_PATTERN = re.compile(
        r"[一-鿿㐀-䶿]"  # 每个 CJK 表意文字单独成词
        r"|[A-Za-z0-9]+(?:[._\-/%][A-Za-z0-9]+)*"  # ASCII 连写串（含 . _ - / % 连接符）
        r"|[^\s]"  # 其余单个非空白字符（标点、全角符号等）
    )

    def __call__(self, text):
        for m in self._TOKEN_PATTERN.finditer(text):
            yield m.group(), m.start(), m.end()


def _install_cjk_words_splitter(model) -> None:
    """将字符级中文分词器注入 GLiNER 的 word splitter（根因修复）。

    GLiNER 把 ``data_processor.words_splitter`` 建为 ``WordsSplitter`` 工厂，其内层
    ``.splitter`` 才是实际分词实现（``model.predict_entities`` 经
    ``words_splitter(text)`` 委派到 ``.splitter``）。替换内层即改变 span 候选枚举的
    边界，无需改写 gliner_config、无新增运行期依赖。GLiNER 内部结构变更（API 漂移）
    时静默跳过，沿用默认空白分词，保持优雅降级。
    """
    try:
        model.data_processor.words_splitter.splitter = _CJKAwareWordsSplitter()
        logger.info("已注入字符级中文分词器（GLiNER 中文长文本 span 召回根因修复）")
    except AttributeError:  # pragma: no cover - GLiNER 内部结构变更的防御路径
        logger.warning(
            "无法注入中文分词器（GLiNER 内部结构可能已变更）；沿用默认空白分词",
            exc_info=True,
        )


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
            self._model = GLiNER.from_pretrained(
                settings.gliner_model_path, local_files_only=True
            )
            _install_cjk_words_splitter(self._model)
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
    """GLiNER predict_entities 输出 → 规整 span 列表。

    过滤越界 label 后，按分数贪心消除重叠/重复：字符级分词放开 span 枚举边界后，
    同一文本区间可能在多个 seed label 上重复命中（GLiNER ``flat_ner`` 通常已在单次
    推理内消重，此处为防御性兜底 + 跨调用去重）。保留扁平、无字符交集的 span 集，
    契合下游 tiptap 高亮（不允许重叠高亮）；GLiNER label 仅为临时值，阶段二按本体
    嵌入重归类，故重叠时保留高分者即可。返回按 ``start`` 升序。
    """
    cleaned: list[dict] = []
    for ent in entities:
        label = ent.get("label")
        value = ent.get("text")
        if label not in allowed or not value:
            continue
        cleaned.append({
            "start": ent.get("start", 0),
            "end": ent.get("end", 0),
            "text": value,
            "label": label,
            "score": round(ent.get("score", 0.0), 4),
        })

    # 分数降序贪心：仅保留与已选 span 无字符交集者（相邻 end==start 不算交集）。
    cleaned.sort(key=lambda s: (-s["score"], s["start"], s["end"]))
    kept: list[dict] = []
    for span in cleaned:
        if any(span["start"] < k["end"] and span["end"] > k["start"] for k in kept):
            continue
        kept.append(span)

    kept.sort(key=lambda s: s["start"])
    return kept


@lru_cache(maxsize=1)
def get_gliner_extractor() -> GlinerExtractor | None:
    """进程级单例提取器；本地 NER 关闭时返回 ``None``。

    不在此处触发模型加载（惰性），故关闭/缺包场景零开销。
    """
    if not settings.gliner_extraction_enabled:
        return None
    return GlinerExtractor()
