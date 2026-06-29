"""本体语义归类：把 GLiNER 抽取的实体 span 文本，经本地嵌入余弦相似度匹配到
**最具体**的本体类（offline、air-gap 友好）。

两阶段标注的第二阶段（设计见记忆 ner-semantic-typing-design）：
- 第一阶段（``document_annotator``）：GLiNER 以「拥有数据属性的类」标签集为种子召回 span；
- 本模块第二阶段：把每个 span 的表层文本嵌入，与全部本体类标签做余弦匹配，取最具体的
  命中（深度优先于近似并列），阈值默认 0.50，低于阈值丢弃。**纯嵌入，不用 SPARQL。**

降级：嵌入器不可用（未装 semantic extra / 加载失败）→ ``type_spans`` 全返回 ``None``，
调用方丢弃 span，结构化主路径零回归（对齐 ``semantic.py`` 的优雅降级）。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from app.services.extraction.semantic import cosine_similarity, get_embedder

logger = logging.getLogger(__name__)

# 语义命中接受阈值。低于此值视为「无可信本体类」→ 丢弃该 span。
# 0.50 平衡召回与精确：允许药品代号（HRS-1234）、属性名（性状/溶解性）通过。
DEFAULT_TYPE_THRESHOLD = 0.50

# 近似并列窗口：与最高分相差在此范围内的命中视为「同样好」，于其中取**最深**（最具体）类，
# 以兑现「span 映射到其最相似的最具体类」（Q2）。窗口外不因深度牺牲相似度。
_SPECIFICITY_MARGIN = 0.05

# 区分度门槛：best_score 需高出所有类平均余弦至少此值，才认为 span 有明确类归属。
# 无语义的编号/代码（DP-CP-PLS-X2534）经上下文窗口后仍与各类余弦接近 → 丢弃。
# 真正匹配的实体（溶剂 → 有机溶剂溶解性）gap 通常 0.30+，0.20 是保守下限。
_DISCRIMINABILITY_MARGIN = 0.20

# 记录待后续 feature 修复的根因 bug（用户：在抽取任务中以告警记录，不在本特性修复）。
GET_CLASS_PROPERTIES_DOMAIN_BUG = (
    "owlready2 get_class_properties() 不返回经 rdfs:domain 声明的数据属性"
    "（仅注解/限制属性），导致类详情属性面板对这些类为空。NER 种子改以属性 domain "
    "反查（OntologyEngine.data_property_domain_classes）规避；根因修复留待后续 feature。"
)


@dataclass(frozen=True)
class ClassEntry:
    iri: str
    label: str
    depth: int        # 在模块类层级中的深度（root=0），越大越具体
    module: str


# 进程级轻缓存：本体单例加载一次，避免每作业重复走层级树。键为 id(engine)。
_index_cache: dict[int, list[ClassEntry]] = {}
# seed 缓存：键为 (id(engine), doc_class_iri or "")，支持按文档类型缩窄种子。
_seed_cache: dict[tuple[int, str], list[str]] = {}
# 相关类缓存：键为 (id(engine), doc_class_iri)。
_relevant_cache: dict[tuple[int, str], set[str]] = {}


_SEED_LABEL_CAP = 40

# 否定名单：明显不是实体的 span 文本模式（度量值、纯数字等）。
_NON_ENTITY_RE = re.compile(
    r"^\d+(\.\d+)?\s*[a-zA-Zμ°℃%‰]+$"   # 数字+单位: 50L, 15℃, 99.5%
    r"|^\d+(\.\d+)?$"                       # 纯数字: 123, 1.5
    r"|^\d+\s*分钟$|^\d+\s*小时$|^\d+\s*天$"  # 中文时间: 15分钟
    r"|^[±<>≤≥]\s*\d"                       # 比较: ±0.5, <3
    r"|^\d+(\.\d+)?\s*[-–~]\s*\d"           # 范围: 3.2-5.8
)


def _is_non_entity_span(text: str) -> bool:
    """检测明显不是实体的 span 文本（度量值、纯数字、范围表达式）。"""
    t = text.strip()
    return len(t) <= 1 or bool(_NON_ENTITY_RE.match(t))


_RELEVANT_HOPS = 4


def relevant_classes_for_doc_type(
    engine, doc_class_iri: str, max_hops: int = _RELEVANT_HOPS,
) -> set[str]:
    """从关系图谱 schema（T-Box 多跳 BFS）提取相关类 IRI 集合。

    委托 ``engine.get_relation_schema()`` 做 BFS，从返回的边中收集所有
    range 类 + 子类 IRI，作为实体归类的候选类子集。

    CMCReport 4 跳约 114 个类（vs 全量 320）：
    hop 1 → DrugProduct, CleaningProcess, SynthesisRoute …
    hop 2 → SynthesisStep, ActivePharmaceuticalIngredient …
    hop 3 → Equipment, Reactor, Centrifuge, AnalyticalMethod …
    hop 4 → ProductionRoom, ConstructionMaterial, CleanabilityRating …
    """
    cache_key = (id(engine), doc_class_iri)
    cached = _relevant_cache.get(cache_key)
    if cached is not None:
        return cached

    edges = engine.get_relation_schema(doc_class_iri, max_hops=max_hops)
    relevant: set[str] = set()
    for edge in edges:
        relevant.add(edge["range_class_iri"])
        for sub in edge["range_subclasses"]:
            relevant.add(sub["iri"])

    logger.debug("文档类 %s 相关类子图 (%d 跳, %d 条边): %d 个类",
                 doc_class_iri, max_hops, len(edges), len(relevant))
    _relevant_cache[cache_key] = relevant
    return relevant


def seed_labels(engine, doc_class_iri: str | None = None) -> list[str]:
    """GLiNER 种子标签，上限 40。

    有 ``doc_class_iri`` 时，种子取自文档类的相关类子图标签（定向召回）；
    无 ``doc_class_iri`` 时走通用策略：数据属性 domain 类 + 模块根类（depth == 0）。
    """
    cache_key = (id(engine), doc_class_iri or "")
    cached = _seed_cache.get(cache_key)
    if cached is not None:
        return cached

    labels: list[str] = []
    seen: set[str] = set()

    if doc_class_iri:
        rel_iris = relevant_classes_for_doc_type(engine, doc_class_iri)
        for entry in build_class_index(engine):
            if len(labels) >= _SEED_LABEL_CAP:
                break
            if entry.iri in rel_iris and entry.label and entry.label not in seen:
                seen.add(entry.label)
                labels.append(entry.label)
    else:
        for _iri, label in engine.data_property_domain_classes():
            if label and label not in seen:
                seen.add(label)
                labels.append(label)
        for entry in build_class_index(engine):
            if len(labels) >= _SEED_LABEL_CAP:
                break
            if entry.depth == 0 and entry.label and entry.label not in seen:
                seen.add(entry.label)
                labels.append(entry.label)

    _seed_cache[cache_key] = labels
    return labels


def build_class_index(engine) -> list[ClassEntry]:
    """全本体类的扁平索引（含层级深度），作为语义匹配的目标语料。"""
    key = id(engine)
    cached = _index_cache.get(key)
    if cached is not None:
        return cached
    entries: list[ClassEntry] = []
    for mod in engine.get_modules():
        for root in engine.get_class_hierarchy(mod.key):
            _flatten(root, 0, mod.key, entries)
    _index_cache[key] = entries
    return entries


def _flatten(node, depth: int, module: str, out: list[ClassEntry]) -> None:
    label = (node.label or node.name or "").strip()
    if label:
        out.append(ClassEntry(iri=node.iri, label=label, depth=depth, module=module))
    for child in node.children:
        _flatten(child, depth + 1, module, out)


_SEGMENT_TOP_K = 15
_SEGMENT_TEXT_CAP = 256


def predict_segment_classes(
    segment_texts: list[str],
    engine,
    class_iris: set[str] | None = None,
    top_k: int = _SEGMENT_TOP_K,
) -> list[set[str] | None]:
    """段级类预测：把每段文本嵌入，与候选类标签做 cosine 排名，取 top-K 类 IRI。

    对叙述型长段落（``is_narrative_segment``），先做句组语义分割，每组独立预测
    top-K 类后取并集——覆盖跨主题段落中各子主题的本体类。短段、键值段、枚举段保持
    原有逻辑不变。

    返回与 ``segment_texts`` 等长的列表。``None`` 表示该段无法排名（空段、无嵌入器、
    候选类已 ≤ top_k），调用方应回退到文档级 ``class_iris``。
    """
    from app.services.extraction.sentence_grouping import (
        is_narrative_segment,
        segment_sentences,
        split_by_topic_shift,
    )

    embedder = get_embedder()
    if embedder is None or not embedder.is_available():
        return [None] * len(segment_texts)

    full_index = build_class_index(engine)
    if not full_index:
        return [None] * len(segment_texts)
    index = (
        [e for e in full_index if e.iri in class_iris] if class_iris else full_index
    )
    if not index or len(index) <= top_k:
        return [None] * len(segment_texts)

    # 预热缓存：类标签 + 原始段文本 + 叙述段句组文本。
    embedder.embed_many([e.label for e in index])
    all_embed_texts = [t[:_SEGMENT_TEXT_CAP] for t in segment_texts if t.strip()]
    for text in segment_texts:
        if is_narrative_segment(text):
            sents = segment_sentences(text)
            groups = split_by_topic_shift(sents, embedder)
            for group in groups:
                gt = " ".join(group)[:_SEGMENT_TEXT_CAP]
                if gt.strip():
                    all_embed_texts.append(gt)
    embedder.embed_many(all_embed_texts)

    class_vecs: list[list[float]] = []
    class_entries: list[ClassEntry] = []
    for entry in index:
        vec = embedder.embed(entry.label)
        if vec:
            class_vecs.append(vec)
            class_entries.append(entry)
    if not class_vecs:
        return [None] * len(segment_texts)

    matcher = _build_matcher(class_vecs)

    def _top_k_iris(query_text: str) -> set[str] | None:
        qvec = embedder.embed(query_text[:_SEGMENT_TEXT_CAP])
        if not qvec:
            return None
        sims = matcher(qvec)
        ranked = sorted(range(len(sims)), key=lambda k: sims[k], reverse=True)
        return {class_entries[k].iri for k in ranked[:top_k]}

    result: list[set[str] | None] = []
    for text in segment_texts:
        text_stripped = text.strip()
        if not text_stripped:
            result.append(None)
            continue

        if is_narrative_segment(text):
            sents = segment_sentences(text)
            groups = split_by_topic_shift(sents, embedder)
            union_iris: set[str] = set()
            for group in groups:
                group_text = " ".join(group)
                iris = _top_k_iris(group_text)
                if iris:
                    union_iris.update(iris)
            result.append(union_iris if union_iris else None)
        else:
            result.append(_top_k_iris(text_stripped))

    return result


def type_spans(
    texts: list[str],
    engine,
    threshold: float = DEFAULT_TYPE_THRESHOLD,
    class_iris: set[str] | None = None,
) -> list[dict | None]:
    """把每个 span 表层文本归类到最具体的本体类。

    ``class_iris`` 非空时仅在该子集内匹配（文档类型感知缩窄）。
    返回与 ``texts`` 等长的列表，第 i 项为 ``{iri, label, score}`` 或 ``None``
    （无 ≥threshold 命中 / 空文本 / 嵌入器不可用）。``score`` 为余弦相似度。
    """
    out: list[dict | None] = [None] * len(texts)
    embedder = get_embedder()
    if embedder is None or not embedder.is_available():
        return out

    full_index = build_class_index(engine)
    if not full_index:
        return out
    index = (
        [e for e in full_index if e.iri in class_iris]
        if class_iris
        else full_index
    )
    if not index:
        return out

    # 预热缓存：类标签 + 非空 span 文本一次性批量编码（远快于逐条）。
    class_labels = [e.label for e in index]
    embedder.embed_many(class_labels)
    embedder.embed_many([t for t in texts if t])

    # 仅保留成功编码的类向量。
    class_vecs: list[list[float]] = []
    class_entries: list[ClassEntry] = []
    for entry in index:
        vec = embedder.embed(entry.label)
        if vec:
            class_vecs.append(vec)
            class_entries.append(entry)
    if not class_vecs:
        return out

    matcher = _build_matcher(class_vecs)

    for i, text in enumerate(texts):
        if not text:
            continue
        if _is_non_entity_span(text):
            continue
        qvec = embedder.embed(text)
        if not qvec:
            continue
        sims = matcher(qvec)
        best_idx = max(range(len(sims)), key=sims.__getitem__)
        best_score = sims[best_idx]
        if best_score < threshold:
            continue
        # 区分度门槛：仅在全量匹配（class_iris 未指定）时生效。
        # 缩窄集的类已经过文档类型预筛选，语义距离更近导致 mean 偏高，
        # 使用全量门槛会误杀大量正确匹配。
        if class_iris is None and len(sims) > 10:
            mean_score = sum(sims) / len(sims)
            if best_score - mean_score < _DISCRIMINABILITY_MARGIN:
                continue
        # 近似并列中取最深（最具体）类；并列再以相似度兜底。
        choice = max(
            (k for k in range(len(sims)) if sims[k] >= best_score - _SPECIFICITY_MARGIN),
            key=lambda k: (class_entries[k].depth, sims[k]),
        )
        entry = class_entries[choice]
        out[i] = {
            "iri": entry.iri,
            "label": entry.label,
            "score": round(float(sims[choice]), 4),
        }
    return out


def _build_matcher(class_vecs: list[list[float]]):
    """返回 ``query_vec -> [cosine,...]`` 的匹配函数；有 numpy 走矩阵乘，否则纯 Python。

    bge 向量已归一化，故余弦等价于点积。numpy 在 semantic extra 装齐时必然存在，
    缺失则回退（数百 span × 数百类的纯 Python 点积在预计算场景可接受）。
    """
    try:
        import numpy as np

        mat = np.asarray(class_vecs, dtype="float32")  # (K, D)，已归一化

        def _match(qvec: list[float]):
            q = np.asarray(qvec, dtype="float32")
            return (mat @ q).tolist()

        return _match
    except Exception:  # pragma: no cover - numpy 缺失回退
        def _match(qvec: list[float]):
            return [cosine_similarity(cv, qvec) for cv in class_vecs]

        return _match
