"""文档标注服务：读取上传文件 → 三阶段 NER 标注 → 前端可渲染结构 + 属性三元组。

阶段一：GLiNER 以本体种子标签集（数据属性 domain 类 + 根/近根类，30-40 量级）召回实体 span；
阶段二：每个 span 的**上下文窗口文本**经本地嵌入余弦匹配到**最具体**的本体类
（``ontology_typer.type_spans``，阈值 0.50，低于则丢弃）。span 的 label/className 取匹配
类标签，score 取余弦相似度，前端据此按类着色、tooltip 显示「实体类型 · 置信度」；
阶段三：对每个已归类实体，以其本体类的数据属性标签集跑 GLiNER 抽取属性值 → 三元组
（``_extract_property_triples``），同时供标注展示与候选审核。

GLiNER 仅负责定界（找 span 边界），其给出的种子标签被丢弃，最终类别由嵌入归类决定。

Word → tiptap JSON（ProseMirror doc，entity mark 内嵌 span）
Excel → 结构化行数据，每个单元格附 entity annotations

性能关键：整篇文档的所有文本段先收集成一个列表，**一次** GLiNER batch 推理 + **一次**
嵌入归类，再把 span 分发回各段。逐段调用在 CPU 上对几百段会到分钟级（请求超时）。

返回 ``(content, warnings, triples)``：warnings 携带 get_class_properties domain bug 的记录，
供抽取任务以告警上浮（用户：在本特性中仅记录、不修复，根因修复留待后续 feature）。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Callable

from app.config import settings
from app.services.extraction.ontology_typer import (
    GET_CLASS_PROPERTIES_DOMAIN_BUG,
    seed_labels,
    type_spans,
)

logger = logging.getLogger(__name__)


def _get_extractor():
    from app.services.extraction.gliner_extractor import get_gliner_extractor

    return get_gliner_extractor()


def _extract_spans_batch(
    extractor, texts: list[str], labels: list[str]
) -> list[list[dict]]:
    """阶段一：批量召回整篇文档所有文本段的候选 span（一次 GLiNER 推理）。

    返回与 ``texts`` 等长的 span 列表的列表；第 i 项为 ``texts[i]`` 的
    ``[{start, end, text, label, score}]``（label 为种子类，稍后被归类覆盖）。
    提取器不可用 → 等长空列表列表（优雅降级，结构化主路径零回归）。
    """
    if not extractor or not extractor.is_available() or not texts or not labels:
        return [[] for _ in texts]
    return extractor.extract_batch_with_spans(texts, labels)


_CONTEXT_WINDOW = 40


def _span_with_context(
    seg_text: str, start: int, end: int, window: int = _CONTEXT_WINDOW
) -> str:
    """拼接 span 前后上下文窗口，为嵌入提供语义锚点。

    对无语义内容的 span（如 "HRS-1234"），周围上下文（"原料药…临床备样"）
    使嵌入向量携带正确语义方向，避免随机命中错误本体类。
    """
    prefix = seg_text[max(0, start - window):start]
    suffix = seg_text[end:end + window]
    span_text = seg_text[start:end]
    return f"{prefix}{span_text}{suffix}".strip()


def _type_and_filter_spans(
    all_spans: list[list[dict]],
    segment_texts: list[str],
    engine,
) -> list[list[dict]]:
    """阶段二：把每段的候选 span 按上下文窗口文本归类到最具体的本体类，低于阈值丢弃。

    全文档 span 文本展平成一个列表做**一次**嵌入归类（共享类向量缓存），再回填。
    归类返回 ``None`` 的 span 被丢弃（无可信本体类）。
    """
    flat_texts: list[str] = []
    positions: list[tuple[int, int]] = []  # (段下标, 段内 span 下标)
    for seg_idx, spans in enumerate(all_spans):
        seg_text = segment_texts[seg_idx] if seg_idx < len(segment_texts) else ""
        for span_idx, span in enumerate(spans):
            flat_texts.append(
                _span_with_context(seg_text, span["start"], span["end"])
            )
            positions.append((seg_idx, span_idx))

    typed = type_spans(flat_texts, engine)

    result: list[list[dict]] = [[] for _ in all_spans]
    for (seg_idx, span_idx), match in zip(positions, typed):
        if match is None:
            continue
        span = all_spans[seg_idx][span_idx]
        result[seg_idx].append({
            "start": span["start"],
            "end": span["end"],
            "text": span["text"],
            "label": match["label"],       # 匹配到的本体类标签（实体类型）
            "className": match["label"],    # className=label → 前端按类型着色
            "score": match["score"],        # 余弦相似度（置信度）
            "iri": match["iri"],
        })
    return result


_PROPERTY_CONTEXT_WINDOW = 200


def _property_schema_for_class(engine, class_iri: str) -> dict:
    """从类 IRI 派生属性标签集（供 GLiNER 三元组抽取）。

    使用 ``get_data_properties_by_domain`` 规避 owlready2 ``get_class_properties`` bug。
    """
    props = engine.get_data_properties_by_domain(class_iri)
    labels: list[str] = []
    label_to_iri: dict[str, str] = {}
    for p in props:
        label = (p.get("label") or p.get("name") or "").strip()
        iri = p.get("iri")
        if label and iri and label not in label_to_iri:
            label_to_iri[label] = iri
            labels.append(label)
    return {"labels": labels, "label_to_iri": label_to_iri}


def _extract_property_triples(
    typed_spans: list[list[dict]],
    segment_texts: list[str],
    engine,
) -> list[dict]:
    """阶段三：对每个已归类实体 span，以其本体类数据属性为标签跑 GLiNER 抽取属性值。

    返回三元组列表，每个三元组含实体定位信息 + 抽取到的 ``(property_iri, value)`` 对。
    GLiNER 不可用 / 类无数据属性 → 该实体 properties 为空列表（结构保留、优雅降级）。
    """
    extractor = _get_extractor()
    if not extractor or not extractor.is_available():
        return []

    by_class: dict[str, list[tuple[int, dict]]] = {}
    for seg_idx, spans in enumerate(typed_spans):
        for span in spans:
            by_class.setdefault(span["iri"], []).append((seg_idx, span))

    triples: list[dict] = []
    for class_iri, entries in by_class.items():
        schema = _property_schema_for_class(engine, class_iri)
        if not schema["labels"]:
            for seg_idx, span in entries:
                triples.append({
                    "entity_text": span["text"],
                    "entity_class_iri": class_iri,
                    "entity_class_label": span["label"],
                    "segment_index": seg_idx,
                    "span_start": span["start"],
                    "span_end": span["end"],
                    "properties": [],
                })
            continue

        contexts: list[str] = []
        for seg_idx, span in entries:
            seg_text = segment_texts[seg_idx] if seg_idx < len(segment_texts) else ""
            contexts.append(_span_with_context(
                seg_text, span["start"], span["end"],
                window=_PROPERTY_CONTEXT_WINDOW,
            ))

        batch_spans = extractor.extract_batch_with_spans(
            contexts, schema["labels"], settings.gliner_threshold,
        )

        for (seg_idx, span), entity_spans in zip(entries, batch_spans):
            props: list[dict] = []
            seen_labels: set[str] = set()
            for es in entity_spans:
                label = es["label"]
                iri = schema["label_to_iri"].get(label)
                if iri and label not in seen_labels:
                    seen_labels.add(label)
                    props.append({"iri": iri, "label": label, "value": es["text"]})
            triples.append({
                "entity_text": span["text"],
                "entity_class_iri": class_iri,
                "entity_class_label": span["label"],
                "segment_index": seg_idx,
                "span_start": span["start"],
                "span_end": span["end"],
                "properties": props,
            })
    return triples


def _annotate_texts(
    texts: list[str],
    engine,
    progress_fn: Callable[[str], None] | None = None,
    should_pause_fn: Callable[[], bool] | None = None,
    checkpoint: dict | None = None,
) -> tuple[list[list[dict]], list[dict], dict | None]:
    """端到端三阶段：seed → GLiNER 定界 → 嵌入归类 → 属性三元组。

    ``progress_fn`` 在每阶段开始时回调阶段名（"gliner"/"typing"/"triples"/"done"）。
    ``should_pause_fn`` 在每阶段间检查——返回 True 时中断并返回 checkpoint（第三元素）。
    ``checkpoint`` 传入已保存的中间结果以跳过已完成阶段。
    返回 ``(typed_spans, triples, checkpoint_or_None)``——checkpoint 为 None 表示正常完成。
    """
    extractor = _get_extractor()
    labels = seed_labels(engine)

    # Stage 1: GLiNER span detection
    if checkpoint and checkpoint.get("completed_stage") in ("gliner", "typing"):
        raw = checkpoint["raw_spans"]
    else:
        if progress_fn:
            progress_fn("gliner")
        raw = _extract_spans_batch(extractor, texts, labels)
        if should_pause_fn and should_pause_fn():
            return [], [], {"completed_stage": "gliner", "raw_spans": raw}

    # Stage 2: embedding-based semantic typing
    if checkpoint and checkpoint.get("completed_stage") == "typing":
        typed = checkpoint["typed_spans"]
    else:
        if progress_fn:
            progress_fn("typing")
        typed = _type_and_filter_spans(raw, texts, engine)
        if should_pause_fn and should_pause_fn():
            return typed, [], {
                "completed_stage": "typing",
                "raw_spans": raw,
                "typed_spans": [[dict(s) for s in seg] for seg in typed],
            }

    # Stage 3: property triple extraction
    if progress_fn:
        progress_fn("triples")
    triples = _extract_property_triples(typed, texts, engine)
    if progress_fn:
        progress_fn("done")

    return typed, triples, None


# Word 段落样式名 → tiptap heading 层级（保留文档大纲）；中英文内置样式名都识别。
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


_PT = 12700  # 1 point = 12700 EMU


def _font_size_heading_level(font_size_emu: int | None) -> int:
    """字号 → heading 层级启发式（段落样式名无法确定时的回退）。

    中文文档常用正文样式 + 手动字号排版；>=20pt 二号/小二→h1，>=16pt 三号→h2，>=14pt 四号→h3。
    """
    if not font_size_emu:
        return 0
    pt = font_size_emu / _PT
    if pt >= 20:
        return 1
    if pt >= 16:
        return 2
    if pt >= 14:
        return 3
    return 0


def _para_font_size(para) -> int | None:
    """段落有效字号（EMU）：首个非空 run → 段落样式字号。"""
    for r in para.runs:
        if (r.text or "").strip() and r.font.size:
            return r.font.size
    try:
        if para.style and para.style.font.size:
            return para.style.font.size
    except AttributeError:
        pass
    return None


# Word 段落对齐 → tiptap textAlign（左对齐为默认 → None 省略）。键为 WD_ALIGN_PARAGRAPH 枚举值。
_ALIGN_MAP = {1: "center", 2: "right", 3: "justify"}


def _para_align(para) -> str | None:
    a = para.alignment
    if a is None:
        return None
    try:
        return _ALIGN_MAP.get(int(a))
    except (TypeError, ValueError):
        return None


def _style_font_attr(para, attr: str):
    """段落样式字体属性（bold/italic/underline）；style 缺失或无属性 → None。"""
    try:
        return getattr(para.style.font, attr, None) if para.style else None
    except AttributeError:
        return None


def _para_runs_and_text(para) -> tuple[str, list[tuple[int, int, list[str]]]]:
    """由 run 自行拼接段落文本（而非 para.text），保证文本与 run 偏移严格对齐，
    使行内样式（粗体/斜体/下划线/删除线）能与 NER span 按字符边界无缝合并。

    run 属性为 None 时表示"继承段落样式"，需回退到 para.style.font 解析有效值。
    """
    style_bold = _style_font_attr(para, "bold")
    style_italic = _style_font_attr(para, "italic")
    style_underline = _style_font_attr(para, "underline")

    parts: list[str] = []
    runs: list[tuple[int, int, list[str]]] = []
    cursor = 0
    for r in para.runs:
        t = r.text or ""
        if not t:
            continue
        marks: list[str] = []
        bold = r.bold if r.bold is not None else style_bold
        if bold:
            marks.append("bold")
        italic = r.italic if r.italic is not None else style_italic
        if italic:
            marks.append("italic")
        underline = r.underline if r.underline is not None else style_underline
        if underline:
            marks.append("underline")
        if getattr(r.font, "strike", None):
            marks.append("strike")
        parts.append(t)
        runs.append((cursor, cursor + len(t), marks))
        cursor += len(t)
    return "".join(parts), runs


def _inline_nodes(
    text: str,
    spans: list[dict],
    runs: list[tuple[int, int, list[str]]] | None = None,
) -> list[dict]:
    """文本 + 行内样式 run + NER span → tiptap text 节点序列。

    按所有 run/span 边界切段，每段叠加其覆盖的行内样式 mark（bold/italic/…）与
    entity-annotation mark（落在某 span 内时），从而既保留 Word 行内格式又保留实体标注。
    """
    if not text:
        return []
    runs = runs or []
    bounds = {0, len(text)}
    for s, e, _ in runs:
        bounds.update((s, e))
    for sp in spans:
        bounds.update((sp["start"], sp["end"]))
    points = sorted(b for b in bounds if 0 <= b <= len(text))

    nodes: list[dict] = []
    for a, b in zip(points, points[1:]):
        if a >= b:
            continue
        marks: list[dict] = []
        for rs, re_, rmarks in runs:
            if rs <= a < re_:
                marks.extend({"type": m} for m in rmarks)
                break
        for sp in spans:
            if sp["start"] <= a < sp["end"]:
                marks.append({
                    "type": "entity-annotation",
                    "attrs": {
                        "label": sp["label"],
                        "className": sp.get("className", sp["label"]),
                        "score": sp["score"],
                    },
                })
                break
        node: dict = {"type": "text", "text": text[a:b]}
        if marks:
            node["marks"] = marks
        nodes.append(node)
    return nodes


def _text_to_tiptap_nodes(text: str, spans: list[dict]) -> list[dict]:
    """纯文本 + NER span → tiptap text 节点（无行内样式，表格单元格用）。"""
    return _inline_nodes(text, spans)


def annotate_word(
    file_path: str | Path,
    engine,
    progress_fn: Callable[[str], None] | None = None,
    should_pause_fn: Callable[[], bool] | None = None,
    checkpoint: dict | None = None,
) -> tuple[dict, list[str], list[dict], dict | None]:
    """解析 Word 文档 → tiptap ProseMirror JSON，三阶段 NER 标注实体 + 属性三元组。

    返回 ``(doc_json, warnings, triples, checkpoint_or_None)``。
    checkpoint 非 None 表示标注被暂停（doc_json 仍为完整结构，但标注可能不完整）。
    """
    from docx import Document

    doc = Document(str(file_path))

    # 索引段落/表格 XML 元素 → python-docx 对象，供按 body 子元素顺序遍历。
    _paras = {p._element: p for p in doc.paragraphs}
    _tables = {t._element: t for t in doc.tables}

    # Pass 1：按文档 body 顺序收集段落与表格，记录结构 + 文本。
    # prefix_lens[i] = 表格数据行表头前缀长度（段落为 0），用于 NER 后偏移校正。
    elements: list[dict] = []
    all_texts: list[str] = []
    prefix_lens: list[int] = []

    for child in doc.element.body:
        if child in _paras:
            para = _paras[child]
            text, runs = _para_runs_and_text(para)
            if text.strip():
                level = _heading_level(para.style.name if para.style else None)
                if not level:
                    level = _font_size_heading_level(_para_font_size(para))
                elements.append({
                    "kind": "para",
                    "level": level,
                    "align": _para_align(para),
                    "runs": runs,
                    "text_idx": len(all_texts),
                })
                all_texts.append(text)
                prefix_lens.append(0)
            else:
                elements.append({"kind": "empty"})
        elif child in _tables:
            table = _tables[child]
            table_rows: list[list[int]] = []
            headers: list[str] = []
            for ri, row in enumerate(table.rows):
                row_idxs: list[int] = []
                for ci, cell in enumerate(row.cells):
                    cell_text = cell.text.strip()
                    if ri == 0:
                        headers.append(cell_text)
                        all_texts.append(cell_text)
                        prefix_lens.append(0)
                    else:
                        hdr = headers[ci] if ci < len(headers) else ""
                        if hdr and cell_text:
                            prefix = f"{hdr}："
                            all_texts.append(f"{prefix}{cell_text}")
                            prefix_lens.append(len(prefix))
                        else:
                            all_texts.append(cell_text)
                            prefix_lens.append(0)
                    row_idxs.append(len(all_texts) - 1)
                table_rows.append(row_idxs)
            elements.append({"kind": "table", "rows": table_rows})

    # 端到端三阶段标注（一次 batch）。
    all_spans, triples, ckpt = _annotate_texts(
        all_texts, engine, progress_fn, should_pause_fn, checkpoint,
    )

    # 表格数据行的 span 偏移需减去表头前缀长度，还原为原始单元格文本内的坐标。
    for seg_idx, plen in enumerate(prefix_lens):
        if plen > 0:
            adjusted = []
            for span in all_spans[seg_idx]:
                ns, ne = span["start"] - plen, span["end"] - plen
                if ns >= 0 and ne > ns:
                    adjusted.append({**span, "start": ns, "end": ne})
            all_spans[seg_idx] = adjusted

    # Pass 2：按记录顺序组装 tiptap 节点（段落与表格交叉保持原位）。
    content: list[dict] = []
    for elem in elements:
        if elem["kind"] == "empty":
            content.append({"type": "paragraph"})
            continue
        if elem["kind"] == "para":
            idx = elem["text_idx"]
            children = _inline_nodes(all_texts[idx], all_spans[idx], elem["runs"])
            level = elem["level"]
            node: dict = (
                {"type": "heading", "attrs": {"level": level}}
                if level
                else {"type": "paragraph"}
            )
            if elem["align"]:
                node.setdefault("attrs", {})["textAlign"] = elem["align"]
            node["content"] = children
            content.append(node)
        elif elem["kind"] == "table":
            rows_data: list[dict] = []
            for row_idxs in elem["rows"]:
                cells: list[dict] = []
                for cell_idx in row_idxs:
                    plen = prefix_lens[cell_idx]
                    ctext = all_texts[cell_idx][plen:]
                    cchildren = _text_to_tiptap_nodes(ctext, all_spans[cell_idx])
                    cells.append({
                        "type": "tableCell",
                        "content": [{"type": "paragraph", "content": cchildren}],
                    })
                rows_data.append({"type": "tableRow", "content": cells})
            if rows_data:
                content.append({"type": "table", "content": rows_data})

    return {"type": "doc", "content": content}, _warnings(all_texts), triples, ckpt


def annotate_excel(
    file_path: str | Path,
    engine,
    ner_columns: list[str] | None = None,
    progress_fn: Callable[[str], None] | None = None,
    should_pause_fn: Callable[[], bool] | None = None,
    checkpoint: dict | None = None,
) -> tuple[dict[str, Any], list[str], list[dict], dict | None]:
    """解析 Excel → 结构化行数据 + 三阶段 NER 标注 + 属性三元组。

    返回 ``(data, warnings, triples, checkpoint_or_None)``。
    """
    from openpyxl import load_workbook

    wb = load_workbook(str(file_path), read_only=True, data_only=True)
    ws = wb.active
    if ws is None:
        return {"headers": [], "rows": []}, [], [], None

    rows_iter = ws.iter_rows(values_only=False)

    header_row = next(rows_iter, None)
    if header_row is None:
        wb.close()
        return {"headers": [], "rows": []}, [], [], None

    headers = [str(c.value or f"col_{i}") for i, c in enumerate(header_row)]
    ner_set = set(ner_columns) if ner_columns else set(headers)

    # Pass 1：收集所有行/列单元格文本，记录哪些需要 NER。
    raw_rows: list[dict[str, str]] = []
    ner_texts: list[str] = []
    ner_slots: list[tuple[int, str]] = []
    for row in rows_iter:
        cells: dict[str, str] = {}
        for i, cell in enumerate(row):
            if i >= len(headers):
                break
            col_name = headers[i]
            value = cell.value
            text = str(value) if value is not None else ""
            cells[col_name] = text
            if col_name in ner_set and text:
                ner_slots.append((len(raw_rows), col_name))
                ner_texts.append(text)
        raw_rows.append(cells)
    wb.close()

    # 端到端三阶段标注所有需标注单元格。
    ner_spans, triples, ckpt = _annotate_texts(
        ner_texts, engine, progress_fn, should_pause_fn, checkpoint,
    )
    spans_by_slot: dict[tuple[int, str], list[dict]] = {
        slot: spans for slot, spans in zip(ner_slots, ner_spans)
    }

    # Pass 2：组装行数据。
    rows: list[dict] = []
    for r_idx, cells in enumerate(raw_rows):
        out_cells: dict[str, Any] = {}
        for col_name, text in cells.items():
            annotations = spans_by_slot.get((r_idx, col_name), [])
            out_cells[col_name] = {"value": text, "annotations": annotations}
        rows.append(out_cells)

    return {"headers": headers, "rows": rows}, _warnings(ner_texts), triples, ckpt


def _warnings(texts: list[str]) -> list[str]:
    """记录 get_class_properties domain bug（仅当确有文本走了 NER 时上浮，避免空噪声）。"""
    return [GET_CLASS_PROPERTIES_DOMAIN_BUG] if any(texts) else []
