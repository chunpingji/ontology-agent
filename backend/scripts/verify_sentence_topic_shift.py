#!/usr/bin/env python3
"""离线验证：bge-small-zh 相邻句余弦相似度能否检测语义转移。

用法：
    cd backend
    uv run python scripts/verify_sentence_topic_shift.py [path_to.docx]

默认使用 data/uploads/原料药 HRS-1234 临床备样生产信息.docx。

输出：
    1. 每个叙述型章节的逐句相邻 cosine 曲线（ASCII 柱状图）
    2. 自动检测的语义转移点（cosine 骤降 ≥ 阈值）
    3. 句组分割结果 + 每组的 top-3 本体类匹配
    4. 统计摘要：区分度是否足够

验证假设：步骤切换 / 主题切换处的相邻 cosine 会有可辨识的谷值。
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

# ── CJK 分句 ─────────────────────────────────────────────────────────

_SENT_SPLIT_RE = re.compile(
    r"(?<=[。！？；\n])"        # 中文句末标点
    r"|(?<=[.!?;])\s+"         # 英文句末标点 + 空白
)

_MIN_SENT_CHARS = 8


def segment_sentences(text: str) -> list[str]:
    """CJK 友好的分句：按句末标点切分，合并过短片段。"""
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


# ── 文档结构解析 ──────────────────────────────────────────────────────

@dataclass
class Section:
    heading: str
    level: int
    paras: list[str]

    @property
    def full_text(self) -> str:
        return "\n".join(self.paras)

    @property
    def char_count(self) -> int:
        return sum(len(p) for p in self.paras)


def _heading_level(style_name: str | None) -> int:
    if not style_name:
        return 0
    s = style_name.strip()
    low = s.lower()
    if low == "title" or s == "标题":
        return 1
    if low == "subtitle" or s == "副标题":
        return 2
    if "heading" in low or s.startswith("标题"):
        m = re.search(r"\d+", s)
        if m:
            return max(1, min(6, int(m.group())))
    return 0


def parse_sections(doc_path: Path) -> list[Section]:
    from docx import Document

    doc = Document(str(doc_path))
    sections: list[Section] = []
    current = Section(heading="(前言)", level=0, paras=[])

    for para in doc.paragraphs:
        text = (para.text or "").strip()
        if not text:
            continue
        style_name = para.style.name if para.style else None
        level = _heading_level(style_name)
        if level > 0:
            if current.heading or current.paras:
                sections.append(current)
            current = Section(heading=text, level=level, paras=[])
        else:
            current.paras.append(text)

    if current.heading or current.paras:
        sections.append(current)
    return sections


# ── 嵌入 + 相似度计算 ────────────────────────────────────────────────

def load_embedder(model_path: str):
    from sentence_transformers import SentenceTransformer
    print(f"加载模型: {model_path}")
    return SentenceTransformer(model_path)


def embed_sentences(model, sentences: list[str]):
    import numpy as np
    vecs = model.encode(sentences, normalize_embeddings=True)
    return np.asarray(vecs, dtype="float32")


def adjacent_cosines(vecs) -> list[float]:
    """计算相邻句向量的余弦相似度（归一化后等价于点积）。"""
    sims = []
    for i in range(len(vecs) - 1):
        sim = float(vecs[i] @ vecs[i + 1])
        sims.append(sim)
    return sims


# ── 语义转移检测 ──────────────────────────────────────────────────────

@dataclass
class TopicShift:
    position: int
    cosine_before: float
    cosine_at: float
    drop: float
    sent_before: str
    sent_after: str


def detect_shifts(
    sims: list[float],
    sentences: list[str],
    drop_threshold: float = 0.08,
    abs_low_threshold: float = 0.75,
) -> list[TopicShift]:
    """检测语义转移点：相邻 cosine 骤降 OR 绝对值低于阈值。

    策略：同时考虑相对下降（与前一对比）和绝对低值。
    """
    if len(sims) < 2:
        return []
    shifts: list[TopicShift] = []
    for i in range(len(sims)):
        drop = sims[i - 1] - sims[i] if i > 0 else 0
        is_relative_drop = drop >= drop_threshold
        is_abs_low = sims[i] < abs_low_threshold
        if is_relative_drop or is_abs_low:
            shifts.append(TopicShift(
                position=i,
                cosine_before=sims[i - 1] if i > 0 else 1.0,
                cosine_at=sims[i],
                drop=drop,
                sent_before=sentences[i][:60],
                sent_after=sentences[i + 1][:60],
            ))
    return shifts


def split_into_groups(
    sentences: list[str], shifts: list[TopicShift]
) -> list[list[str]]:
    """按检测到的转移点将句子切分为语义句组。"""
    if not shifts:
        return [sentences]
    boundaries = sorted({s.position + 1 for s in shifts})
    groups: list[list[str]] = []
    prev = 0
    for b in boundaries:
        if prev < b:
            groups.append(sentences[prev:b])
        prev = b
    if prev < len(sentences):
        groups.append(sentences[prev:])
    return groups


# ── 可视化 ────────────────────────────────────────────────────────────

def _bar(val: float, width: int = 50) -> str:
    filled = int(val * width)
    return "█" * filled + "░" * (width - filled)


def print_cosine_curve(
    section_heading: str,
    sentences: list[str],
    sims: list[float],
    shifts: list[TopicShift],
):
    shift_positions = {s.position for s in shifts}
    print(f"\n{'='*78}")
    print(f"章节: {section_heading}")
    print(f"句数: {len(sentences)},  相邻对数: {len(sims)}")
    print(f"{'='*78}")
    print(f"{'对':>4}  {'cosine':>7}  {'图示':<52}  句子片段")
    print(f"{'-'*4}  {'-'*7}  {'-'*52}  {'-'*30}")

    for i, sim in enumerate(sims):
        marker = " ◀ SHIFT" if i in shift_positions else ""
        snip = sentences[i + 1][:28].replace("\n", " ")
        color_bar = _bar(sim)
        print(f"{i:>4}  {sim:>7.4f}  {color_bar}  {snip}{marker}")

    if sims:
        mean_sim = sum(sims) / len(sims)
        min_sim = min(sims)
        max_sim = max(sims)
        std_sim = (sum((s - mean_sim) ** 2 for s in sims) / len(sims)) ** 0.5
        print(f"\n  统计: mean={mean_sim:.4f}  std={std_sim:.4f}  "
              f"min={min_sim:.4f}  max={max_sim:.4f}  "
              f"range={max_sim - min_sim:.4f}")
        print(f"  转移点: {len(shifts)} 处")


def print_groups_with_tags(
    groups: list[list[str]], model, class_labels: list[str]
):
    """对每个句组做 top-3 本体类标签匹配（演示语义 tag 效果）。"""
    import numpy as np

    if not class_labels:
        print("\n  (无本体类标签，跳过 tag 匹配)")
        return

    class_vecs = model.encode(class_labels, normalize_embeddings=True)
    class_mat = np.asarray(class_vecs, dtype="float32")

    print(f"\n  句组 → 本体类 tag (top-3):")
    print(f"  {'-'*70}")
    for gi, group in enumerate(groups):
        group_text = " ".join(group)[:256]
        gvec = model.encode([group_text], normalize_embeddings=True)
        sims = (class_mat @ gvec[0]).tolist()
        ranked = sorted(range(len(sims)), key=lambda k: sims[k], reverse=True)
        top3 = [(class_labels[k], sims[k]) for k in ranked[:3]]
        preview = group_text[:50].replace("\n", " ")
        print(f"  组{gi}: [{len(group)}句] \"{preview}…\"")
        for label, score in top3:
            print(f"        → {label} ({score:.4f})")


# ── 统计摘要 ──────────────────────────────────────────────────────────

def print_summary(all_stats: list[dict]):
    print(f"\n{'='*78}")
    print("总体统计摘要")
    print(f"{'='*78}")

    all_sims = []
    all_shifts = 0
    all_pairs = 0
    for s in all_stats:
        all_sims.extend(s["sims"])
        all_shifts += s["n_shifts"]
        all_pairs += s["n_pairs"]

    if not all_sims:
        print("  无足够叙述段数据")
        return

    mean = sum(all_sims) / len(all_sims)
    std = (sum((s - mean) ** 2 for s in all_sims) / len(all_sims)) ** 0.5
    mn, mx = min(all_sims), max(all_sims)

    print(f"  分析章节数: {len(all_stats)}")
    print(f"  总相邻对数: {all_pairs}")
    print(f"  总转移点数: {all_shifts}")
    print(f"  相邻 cosine 全局: mean={mean:.4f}  std={std:.4f}  "
          f"min={mn:.4f}  max={mx:.4f}")
    print()

    # 判定区分度
    print("  ── 区分度判定 ──")
    if std < 0.03:
        print("  ⚠ 标准差过小 (<0.03): 相邻句相似度几乎恒定,")
        print("    bge 在此域内无法有效区分语义转移, 句组切分方案不可行。")
        verdict = "NOT_FEASIBLE"
    elif std < 0.05:
        print("  △ 标准差偏小 (0.03~0.05): 有微弱信号但噪声大,")
        print("    需要结合文档结构（标题/表格边界）辅助, 单独依赖不可靠。")
        verdict = "MARGINAL"
    elif mx - mn < 0.15:
        print("  △ 极差偏小 (<0.15): 最大/最小 cosine 差距有限,")
        print("    骤降检测可能产生较多假阳/假阴。")
        verdict = "MARGINAL"
    else:
        print("  ✓ 区分度足够: std 和极差均达到可用水平,")
        print("    句组语义分割方案可行。")
        verdict = "FEASIBLE"

    print(f"\n  结论: {verdict}")
    print()

    # cosine 分布直方图
    print("  相邻 cosine 分布:")
    buckets = [0] * 10
    for s in all_sims:
        idx = min(int(s * 10), 9)
        buckets[idx] += 1
    max_count = max(buckets) if buckets else 1
    for i in range(9, -1, -1):
        lo, hi = i / 10, (i + 1) / 10
        bar = "█" * int(buckets[i] / max_count * 40) if max_count > 0 else ""
        print(f"  {lo:.1f}-{hi:.1f} | {bar} ({buckets[i]})")


# ── 本体类标签（模拟；正式版从 engine 取） ───────────────────────────

SAMPLE_CLASS_LABELS = [
    "药物产品", "原料药", "合成路线", "合成步骤", "中间体",
    "粗品", "工艺设备", "反应釜", "离心机", "旋转蒸发器",
    "清洗过程", "残留物", "安全风险评估", "质量风险评估",
    "存放条件", "降解途径", "酸降解", "碱降解", "氧化降解",
    "光降解", "热降解", "共线评估数据", "CMC报告",
    "起始物料", "关键中间体", "包装方式", "有效期",
    "分析方法", "溶解度", "稳定性", "杂质",
]


# ── 主流程 ────────────────────────────────────────────────────────────

MIN_NARRATIVE_CHARS = 120
MIN_NARRATIVE_SENTS = 4


def main():
    default_doc = Path(__file__).resolve().parent.parent / "data" / "uploads" / "原料药 HRS-1234 临床备样生产信息.docx"
    doc_path = Path(sys.argv[1]) if len(sys.argv) > 1 else default_doc

    if not doc_path.exists():
        print(f"文档不存在: {doc_path}")
        print("用法: uv run python scripts/verify_sentence_topic_shift.py [path_to.docx]")
        sys.exit(1)

    print(f"文档: {doc_path.name}")
    print(f"路径: {doc_path}")

    # 1. 解析文档章节
    sections = parse_sections(doc_path)
    print(f"\n共 {len(sections)} 个章节")

    # 2. 加载嵌入模型
    model_path = str(Path(__file__).resolve().parent.parent / "models" / "bge-small-zh-v1.5")
    model = load_embedder(model_path)

    # 3. 筛选叙述型长章节
    narrative_sections = [
        s for s in sections
        if s.char_count >= MIN_NARRATIVE_CHARS
        and len(s.paras) >= 3
    ]
    print(f"叙述型长章节 (≥{MIN_NARRATIVE_CHARS}字, ≥3段): {len(narrative_sections)} 个")

    if not narrative_sections:
        print("无足够长度的叙述段，扩大范围到所有多段落章节…")
        narrative_sections = [s for s in sections if len(s.paras) >= 2]
        print(f"扩大后: {len(narrative_sections)} 个章节")

    # 4. 逐章节分析
    all_stats: list[dict] = []
    for sec in narrative_sections:
        full_text = sec.full_text
        sentences = segment_sentences(full_text)

        if len(sentences) < MIN_NARRATIVE_SENTS:
            continue

        vecs = embed_sentences(model, sentences)
        sims = adjacent_cosines(vecs)
        shifts = detect_shifts(sims, sentences)

        print_cosine_curve(sec.heading, sentences, sims, shifts)

        groups = split_into_groups(sentences, shifts)
        print_groups_with_tags(groups, model, SAMPLE_CLASS_LABELS)

        all_stats.append({
            "heading": sec.heading,
            "n_sents": len(sentences),
            "n_pairs": len(sims),
            "n_shifts": len(shifts),
            "sims": sims,
        })

    # 5. 统计摘要
    print_summary(all_stats)


if __name__ == "__main__":
    main()
