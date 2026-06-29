"""文档级分类 —— 把一篇研发文档判定为某 ``RegulatoryDocument`` 子类（离线、确定性）。

关系抽取的第一步：先确定「这是什么文档」（如 CMCReport），再按该类的对象属性图抽边。
之前 ``relation_extractor.classify_document`` 是硬编码桩；本模块替换为**本体驱动的可解释
打分**：

1. 经 ``engine.get_subclasses(RegulatoryDocument)`` 枚举全部候选文档类型。
2. 每个候选的信号词来自两处：① 该类 rdfs:label 的分词（通用、零配置随本体演进）；
   ② 少量人工策动的强信号（``_CURATED_SIGNALS``，按 local-name 键入，提高已知关键类
   的判准）。
3. 对「标题 + 各级标题 + TOC + 前若干段」组成的 haystack 子串计分，取最高分且过阈值者。

全程离线、无模型调用、无外发。无命中 → ``None``（调用方据此不产关系，保持优雅降级）。
"""

from __future__ import annotations

import re

from app.services.extraction.docx_structure import DocStructure

REGULATORY_DOCUMENT_IRI = "https://ontology.pharma-gmp.cn/slpra/document/RegulatoryDocument"

# 人工策动的强信号：local-name → 关键词（权重 2）。覆盖关键已知类，余者退化到 label 分词。
# 关键词命中即为该类加分；CMCReport 信号刻意取自原料药 CMC/备样生产文档的高判别力术语。
_CURATED_SIGNALS: dict[str, tuple[str, ...]] = {
    "CMCReport": (
        "CMC", "原料药", "备样生产", "临床备样", "工艺描述", "合成路线",
        "设备需求", "设备清洗", "共线评估", "得量", "收率", "降解途径",
    ),
    "BatchProductionRecord": ("批生产记录", "批记录", "生产指令", "批号"),
    "ValidationProtocol": ("验证方案", "确认方案", "验证protocol"),
    "ValidationReport": ("验证报告", "确认报告"),
    "StabilityStudyReport": ("稳定性研究", "稳定性报告", "长期稳定性", "加速试验"),
    "ToxicologyStudyReport": ("毒理学研究", "毒理报告", "GLP", "致畸"),
    "QualitySpecification": ("质量标准", "质量规格", "放行标准"),
    "AnalyticalMethod": ("分析方法", "检验方法", "方法学验证"),
}

_CURATED_WEIGHT = 2
_LABEL_WEIGHT = 1
_MIN_SCORE = 3  # 过阈：弱于此视作未识别（避免一两个泛词误判）


def _local_name(iri: str) -> str:
    return iri.rsplit("/", 1)[-1].rsplit("#", 1)[-1]


def _label_tokens(label: str) -> list[str]:
    """从 rdfs:label 切出信号词：ASCII 词整体 + 长度≥2 的中文子串（去标点/空白）。

    例：``"CMC 报告"`` → ``["CMC", "报告"]``；``"毒理学研究报告"`` → ``["毒理学研究报告"]``。
    单字中文（如「书」）判别力弱，丢弃以降噪。
    """
    tokens: list[str] = []
    for m in re.finditer(r"[A-Za-z][A-Za-z0-9]+|[一-鿿]{2,}", label or ""):
        tok = m.group()
        if tok and tok not in tokens:
            tokens.append(tok)
    return tokens


def _build_haystack(structure: DocStructure) -> str:
    """打分语料：标题 + 全部标题 + 前 12 段（含 TOC，目录条目富含章节名是强信号）。"""
    parts = [structure.title, *structure.headings, *structure.paragraphs[:12]]
    return "\n".join(p for p in parts if p)


def classify(structure: DocStructure, engine) -> dict | None:
    """对解析后的文档结构打分择类，返回 ``{doc_class_iri, label, score, signals}`` 或 ``None``。

    ``signals`` 为命中的关键词（可解释，前端徽章展示）。``engine`` 提供候选类枚举。
    """
    candidates = engine.get_subclasses(REGULATORY_DOCUMENT_IRI)
    if not candidates:
        return None

    haystack = _build_haystack(structure)
    if not haystack:
        return None

    best: dict | None = None
    for cand in candidates:
        iri = cand["iri"]
        label = cand.get("label") or _local_name(iri)
        local = _local_name(iri)

        signals: list[str] = []
        score = 0
        for kw in _CURATED_SIGNALS.get(local, ()):  # 强信号
            if kw and kw in haystack:
                score += _CURATED_WEIGHT
                signals.append(kw)
        for tok in _label_tokens(label):  # label 分词通用信号
            if tok in haystack and tok not in signals:
                score += _LABEL_WEIGHT
                signals.append(tok)

        if score and (best is None or score > best["score"]):
            best = {"doc_class_iri": iri, "label": label, "score": score, "signals": signals}

    if best is None or best["score"] < _MIN_SCORE:
        return None
    return best
