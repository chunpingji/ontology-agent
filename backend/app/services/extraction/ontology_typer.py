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
_seed_cache: dict[int, list[str]] = {}


_SEED_LABEL_CAP = 40


def seed_labels(engine) -> list[str]:
    """GLiNER 种子标签 = 数据属性 domain 类 + 模块根类（depth == 0），上限 40。

    数据属性 domain 类提供精确召回（PDE计算/MACO计算/洁净区…）；模块根类补充高层
    类别召回（药物产品/设备…——它们自身无数据属性但语义上是重要实体类别）。
    标签总量上限 40：GLiNER 标签过多（>50）会导致注意力稀释和推理超时。

    注：数据属性标签（"NOAEL"、"制剂剂型"…）不应进入种子标签——它们是字段名而非实体
    类型，会导致 GLiNER 把文档中的属性名误检为实体 span。属性标签仅用于阶段三（属性
    三元组抽取）的 per-class GLiNER 标签集。
    """
    key = id(engine)
    cached = _seed_cache.get(key)
    if cached is not None:
        return cached
    labels: list[str] = []
    seen: set[str] = set()
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
    _seed_cache[key] = labels
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


def type_spans(
    texts: list[str], engine, threshold: float = DEFAULT_TYPE_THRESHOLD
) -> list[dict | None]:
    """把每个 span 表层文本归类到最具体的本体类。

    返回与 ``texts`` 等长的列表，第 i 项为 ``{iri, label, score}`` 或 ``None``
    （无 ≥threshold 命中 / 空文本 / 嵌入器不可用）。``score`` 为余弦相似度。
    """
    out: list[dict | None] = [None] * len(texts)
    embedder = get_embedder()
    if embedder is None or not embedder.is_available():
        return out

    index = build_class_index(engine)
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
        qvec = embedder.embed(text)
        if not qvec:
            continue
        sims = matcher(qvec)
        best_idx = max(range(len(sims)), key=sims.__getitem__)
        best_score = sims[best_idx]
        if best_score < threshold:
            continue
        if len(sims) > 10:
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
