"use client";

import { useEffect, useMemo, useRef } from "react";
import { useEditor, EditorContent } from "@tiptap/react";
import { Extension, Mark, Node, mergeAttributes } from "@tiptap/core";
import StarterKit from "@tiptap/starter-kit";
import { Table } from "@tiptap/extension-table";
import { TableRow } from "@tiptap/extension-table-row";
import { TableCell } from "@tiptap/extension-table-cell";
import { TableHeader } from "@tiptap/extension-table-header";
import Underline from "@tiptap/extension-underline";
import type { StructuredRelationSourceRef } from "@/lib/api";
import { parseRelationSourceRefKey } from "@/lib/relation-source-ref";
import { EntityAnnotation } from "./entity-mark";

// 段落/标题对齐：只读预览无需编辑命令，仅注册 textAlign 全局属性即可渲染
// 后端从 Word 还原的居中/右对齐/两端对齐（StarterKit 默认不带 text-align）。
const TextAlign = Extension.create({
  name: "textAlign",
  addGlobalAttributes() {
    return [
      {
        types: ["paragraph", "heading"],
        attributes: {
          sourceParagraphIndex: {
            default: null,
            parseHTML: (el) => {
              const raw = (el as HTMLElement).dataset.sourceParagraphIndex;
              return raw == null ? null : Number(raw);
            },
            renderHTML: (attrs) =>
              typeof attrs.sourceParagraphIndex === "number"
                ? { "data-source-paragraph-index": attrs.sourceParagraphIndex }
                : {},
          },
          textAlign: {
            default: null,
            parseHTML: (el) => (el as HTMLElement).style.textAlign || null,
            renderHTML: (attrs) =>
              attrs.textAlign ? { style: `text-align: ${attrs.textAlign}` } : {},
          },
        },
      },
    ];
  },
});

// 行内文本样式：还原后端从 Word run 采集的字体颜色/字号/字体族（015 样例预览）。
// 自研以避免新增 @tiptap/extension-text-style 依赖（气隙友好）；复刻官方 textStyle
// 语义——各属性各产出一段 style，由 mergeAttributes 合并进同一 <span>。
const TextStyle = Mark.create({
  name: "textStyle",
  parseHTML() {
    return [
      {
        tag: "span",
        getAttrs: (el) =>
          (el as HTMLElement).hasAttribute("style") ? {} : false,
      },
    ];
  },
  renderHTML({ HTMLAttributes }) {
    return ["span", mergeAttributes(HTMLAttributes), 0];
  },
  addAttributes() {
    return {
      color: {
        default: null,
        parseHTML: (el) => (el as HTMLElement).style.color || null,
        renderHTML: (attrs) =>
          attrs.color ? { style: `color: ${attrs.color}` } : {},
      },
      fontSize: {
        default: null,
        parseHTML: (el) => (el as HTMLElement).style.fontSize || null,
        renderHTML: (attrs) =>
          attrs.fontSize ? { style: `font-size: ${attrs.fontSize}` } : {},
      },
      fontFamily: {
        default: null,
        parseHTML: (el) => (el as HTMLElement).style.fontFamily || null,
        renderHTML: (attrs) =>
          attrs.fontFamily ? { style: `font-family: ${attrs.fontFamily}` } : {},
      },
    };
  },
});

const PageBreak = Node.create({
  name: "pageBreak",
  group: "block",
  atom: true,
  selectable: false,
  addAttributes() {
    return {
      mode: { default: "force" },
      source: { default: "manual" },
    };
  },
  parseHTML() {
    return [{ tag: "div[data-page-break]" }];
  },
  renderHTML({ HTMLAttributes }) {
    return [
      "div",
      {
        "data-page-break": "true",
        "data-mode": HTMLAttributes.mode || "force",
        class: "doc-page-break",
      },
    ];
  },
});

const HIGHLIGHT_CLS = "source-highlight";
const HIGHLIGHT_BODY_CLS = "source-highlight-body";

function parseSourceRef(ref: string): string[] {
  const segments = ref.split(" / ");
  return segments
    .map((s) => s.replace(/^[§表]\s*/, "").trim())
    .filter(Boolean)
    .reverse();
}

function structuredSourceKeywords(ref: StructuredRelationSourceRef): string[] {
  return [ref.parameter, ref.key, ref.header, ref.section]
    .filter((value): value is string => typeof value === "string")
    .map((value) => value.trim())
    .filter(Boolean);
}

function applyStructuredTableHighlight(
  container: HTMLElement,
  ref: StructuredRelationSourceRef,
): Element | null {
  if (typeof ref.table !== "number" || !Number.isInteger(ref.table)) return null;
  const table = container.querySelectorAll("table").item(ref.table);
  if (!table) return null;

  table.classList.add(HIGHLIGHT_CLS);
  if (typeof ref.row !== "number" || !Number.isInteger(ref.row)) return table;

  const row = table.querySelectorAll("tr").item(ref.row);
  if (!row) return table;
  if (typeof ref.column !== "number" || !Number.isInteger(ref.column)) return row;

  return row.querySelectorAll("th, td").item(ref.column) || row;
}

function applyStructuredParagraphHighlight(
  container: HTMLElement,
  ref: StructuredRelationSourceRef,
): Element | null {
  if (
    typeof ref.paragraph_index !== "number" ||
    !Number.isInteger(ref.paragraph_index)
  ) return null;
  const paragraph = container.querySelector(
    `[data-source-paragraph-index="${ref.paragraph_index}"]`,
  );
  if (!paragraph) return null;
  paragraph.classList.add(HIGHLIGHT_CLS);
  return paragraph;
}

function headingLevel(el: Element): number {
  const m = el.tagName.match(/^H(\d)$/i);
  return m ? Number(m[1]) : 0;
}

function clearHighlights(container: HTMLElement) {
  container.querySelectorAll(`.${HIGHLIGHT_CLS}`).forEach((el) => {
    el.classList.remove(HIGHLIGHT_CLS);
  });
  container.querySelectorAll(`.${HIGHLIGHT_BODY_CLS}`).forEach((el) => {
    el.classList.remove(HIGHLIGHT_BODY_CLS);
  });
}

function applyHighlight(container: HTMLElement, keywords: string[]): Element | null {
  let firstMatch: Element | null = null;

  for (const kw of keywords) {
    // Search headings
    const headings = container.querySelectorAll("h1, h2, h3, h4, h5, h6");
    for (const h of headings) {
      if (h.textContent?.includes(kw)) {
        h.classList.add(HIGHLIGHT_CLS);
        if (!firstMatch) firstMatch = h;

        const level = headingLevel(h);
        let sibling = h.nextElementSibling;
        while (sibling) {
          const sibLevel = headingLevel(sibling);
          if (sibLevel > 0 && sibLevel <= level) break;
          sibling.classList.add(HIGHLIGHT_BODY_CLS);
          sibling = sibling.nextElementSibling;
        }
        return firstMatch;
      }
    }

    // Search table headers/cells
    const cells = container.querySelectorAll("th, td");
    for (const cell of cells) {
      if (cell.textContent?.includes(kw)) {
        const table = cell.closest("table");
        if (table) {
          table.classList.add(HIGHLIGHT_CLS);
          if (!firstMatch) firstMatch = table;
          return firstMatch;
        }
      }
    }

    // Search body paragraphs / list items — 013: evidence 片段多为正文，
    // 当锚点回退为原始 evidence_span 时靠此层定位（标题/表格已先命中）。
    const blocks = container.querySelectorAll("p, li");
    for (const block of blocks) {
      if (block.textContent?.includes(kw)) {
        block.classList.add(HIGHLIGHT_CLS);
        if (!firstMatch) firstMatch = block;
        return firstMatch;
      }
    }
  }

  return firstMatch;
}

// ProseMirror normalizes \n in text nodes to spaces. Convert them to hardBreak
// nodes so line breaks within a paragraph render correctly.
function normalizeNewlines(node: Record<string, unknown>): Record<string, unknown> {
  if (node.type === "text" && typeof node.text === "string" && (node.text as string).includes("\n")) {
    const parts = (node.text as string).split("\n");
    const { text: _, ...marks } = node;
    const nodes: Record<string, unknown>[] = [];
    parts.forEach((part, i) => {
      if (part) nodes.push({ ...marks, type: "text", text: part });
      if (i < parts.length - 1) nodes.push({ type: "hardBreak" });
    });
    return { __expanded: nodes } as unknown as Record<string, unknown>;
  }
  if (Array.isArray(node.content)) {
    const newContent: Record<string, unknown>[] = [];
    for (const child of node.content as Record<string, unknown>[]) {
      const result = normalizeNewlines(child);
      if ((result as { __expanded?: unknown }).__expanded) {
        newContent.push(...((result as { __expanded: Record<string, unknown>[] }).__expanded));
      } else {
        newContent.push(result);
      }
    }
    return { ...node, content: newContent };
  }
  return node;
}

interface WordViewerProps {
  content: Record<string, unknown>;
  highlightRef?: string | null;
  /** 令表格按 colgroup 列宽比例适配纸张宽度（样例预览用；默认表格保持自然宽度）。 */
  fitTables?: boolean;
}

export function WordViewer({ content, highlightRef, fitTables }: WordViewerProps) {
  const wrapperRef = useRef<HTMLDivElement>(null);

  const normalizedContent = useMemo(
    () => normalizeNewlines(content),
    [content],
  ) as Parameters<typeof useEditor>[0] extends { content?: infer C } ? C : never;

  const editor = useEditor({
    extensions: [
      StarterKit,
      TextAlign,
      TextStyle,
      Underline,
      Table,
      TableRow,
      TableCell,
      TableHeader,
      EntityAnnotation,
      PageBreak,
    ],
    content: normalizedContent,
    editable: false,
    immediatelyRender: true,
  });

  useEffect(() => {
    if (editor && normalizedContent) {
      editor.commands.setContent(normalizedContent);
    }
  }, [editor, normalizedContent]);

  useEffect(() => {
    if (!wrapperRef.current) return;
    const container = wrapperRef.current;

    clearHighlights(container);

    if (!highlightRef) return;

    const structuredRef = parseRelationSourceRefKey(highlightRef);
    const keywords = structuredRef
      ? structuredSourceKeywords(structuredRef)
      : parseSourceRef(highlightRef);
    if (!structuredRef && keywords.length === 0) return;

    // Delay slightly to ensure DOM is ready after render
    const timer = setTimeout(() => {
      const firstMatch =
        (structuredRef && applyStructuredTableHighlight(container, structuredRef)) ||
        (structuredRef && applyStructuredParagraphHighlight(container, structuredRef)) ||
        applyHighlight(container, keywords);
      if (firstMatch) {
        firstMatch.scrollIntoView({ behavior: "smooth", block: "start" });
      }
    }, 50);

    return () => clearTimeout(timer);
  }, [highlightRef]);

  // Page-break pagination: insert spacer elements so blocks don't cross page boundaries
  useEffect(() => {
    if (!wrapperRef.current) return;
    const wrapper = wrapperRef.current;
    const tiptap = wrapper.querySelector(".tiptap") as HTMLElement | null;
    if (!tiptap) return;

    const run = (retry = false) => {
      tiptap
        .querySelectorAll(".page-break-spacer")
        .forEach((el) => el.remove());

      // 页面尺寸：优先用后端提取的 Word 页面比例，否则 A4
      const cAttrs = (content as { attrs?: { pageWidth?: number; pageHeight?: number } }).attrs;
      const ratio =
        cAttrs?.pageWidth && cAttrs?.pageHeight
          ? cAttrs.pageHeight / cAttrs.pageWidth
          : 297 / 210;
      const pagePx = Math.round(wrapper.offsetWidth * ratio);
      if (pagePx <= 0) return;
      wrapper.style.setProperty("--page-height", `${pagePx}px`);

      const gapPx = 20;
      const style = getComputedStyle(wrapper);
      const padTop = parseFloat(style.paddingTop);
      const padBottom = parseFloat(style.paddingBottom);
      const contentHeight = pagePx - padTop - padBottom;

      const blocks = Array.from(tiptap.children).filter(
        (n) => !(n as HTMLElement).classList?.contains("page-break-spacer"),
      ) as HTMLElement[];
      if (blocks.length === 0) return;

      const tiptapRect = tiptap.getBoundingClientRect();
      const measures = blocks.map((b) => {
        const r = b.getBoundingClientRect();
        return { el: b, top: r.top - tiptapRect.top, height: r.height };
      });

      let shift = 0;
      const inserts: { before: HTMLElement; h: number }[] = [];
      // 第一页内容区底部（tiptap 坐标原点已在 padTop 之后）
      let boundary = contentHeight;
      const stride = pagePx + gapPx;

      for (const m of measures) {
        const adjTop = m.top + shift;
        const adjBot = adjTop + m.height;

        // ── 显式分页标记 ──
        if (m.el.classList.contains("doc-page-break")) {
          const mode = m.el.getAttribute("data-mode") || "force";

          if (mode === "ensureStart") {
            // 已在页首 → 不分页（避免空白首页）
            const EPS = 2;
            while (adjTop > boundary) boundary += stride;
            const distFromPageStart = adjTop - (boundary - contentHeight);
            if (distFromPageStart > EPS) {
              const h = boundary - adjTop + padBottom + gapPx + padTop;
              inserts.push({ before: m.el, h });
              shift += h;
              boundary += stride;
            }
          } else {
            // force: 无条件分页
            while (adjTop > boundary) boundary += stride;
            const remaining = boundary - adjTop;
            const h = remaining + padBottom + gapPx + padTop;
            inserts.push({ before: m.el, h });
            shift += h;
            boundary += stride;
          }
          continue;
        }

        // ── 大块内容 ──
        if (m.height > contentHeight * 0.9) {
          // 高度在 (0.9,1.0] 页高的块能放进单页：跨页时推到下页页首，避免被切成两半。
          // 但**超过整页高度**的块无论如何都放不下单页，推到下页既挡不住跨页、又会在其
          // 前（尤其它已在页首时）留下整张空白页 → 这类块不推，只推进边界让其自然跨页（Codex R2）。
          const fitsOnePage = m.height <= contentHeight;
          if (fitsOnePage && adjTop < boundary && adjBot > boundary) {
            const h = boundary - adjTop + padBottom + gapPx + padTop;
            inserts.push({ before: m.el, h });
            shift += h;
          }
          const shiftedBot = m.top + shift + m.height;
          while (boundary <= shiftedBot) boundary += stride;
          continue;
        }

        // ── 普通块跨页 → 推到下页 ──
        if (adjTop < boundary && adjBot > boundary) {
          const h = boundary - adjTop + padBottom + gapPx + padTop;
          inserts.push({ before: m.el, h });
          shift += h;
          boundary += stride;
        }

        while (m.top + shift > boundary) boundary += stride;
      }

      // 反向插入 spacer（避免影响未处理元素的 DOM 位置）
      for (const ins of [...inserts].reverse()) {
        const div = document.createElement("div");
        div.className = "page-break-spacer";
        div.setAttribute("contenteditable", "false");
        div.dataset.prosemirrorIgnore = "true";
        div.style.height = `${ins.h}px`;
        const gap = document.createElement("div");
        gap.className = "page-break-gap";
        gap.style.height = `${gapPx}px`;
        div.appendChild(gap);
        tiptap.insertBefore(div, ins.before);
      }

      // 二次验证：首次插入后 margin collapsing 可能偏移，重新计算一次
      if (!retry && inserts.length > 0) {
        requestAnimationFrame(() => run(true));
      }
    };

    const raf = requestAnimationFrame(() => run());

    // 监听宽度和高度变化
    let prevWidth = wrapper.offsetWidth;
    let prevHeight = wrapper.offsetHeight;
    const ro = new ResizeObserver(() => {
      const w = wrapper.offsetWidth;
      const h = wrapper.offsetHeight;
      if (w !== prevWidth || h !== prevHeight) {
        prevWidth = w;
        prevHeight = h;
        run();
      }
    });
    ro.observe(wrapper);

    // 字体加载后重新分页
    let fontCleanup: (() => void) | undefined;
    if (document.fonts) {
      let mounted = true;
      document.fonts.ready.then(() => { if (mounted) run(); });
      fontCleanup = () => { mounted = false; };
    }

    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
      fontCleanup?.();
      tiptap
        .querySelectorAll(".page-break-spacer")
        .forEach((el) => el.remove());
    };
  }, [content, editor]);

  return (
    <div
      ref={wrapperRef}
      className={`paper-pages prose prose-sm mx-auto max-w-none dark:prose-invert${
        fitTables ? " paper-fit-tables" : ""
      }`}
    >
      <EditorContent editor={editor} />
      <style>{`
        .paper-pages {
          padding: 3rem 4rem;
          min-height: var(--page-height, 29.7cm);
          border-radius: 2px;
          border: 1px solid hsl(0 0% 85%);
          box-shadow: 0 1px 3px rgba(0,0,0,0.1), 0 1px 2px rgba(0,0,0,0.06);
          background: white;
        }
        :is(.dark) .paper-pages {
          border-color: hsl(var(--border));
          background: hsl(var(--card));
          box-shadow: 0 1px 3px rgba(0,0,0,0.3);
        }
        .page-break-spacer {
          margin: 0 -4rem;
          background: white;
          pointer-events: none;
          display: flex;
          flex-direction: column;
          justify-content: flex-end;
        }
        :is(.dark) .page-break-spacer {
          background: hsl(var(--card));
        }
        .page-break-gap {
          background: hsl(0 0% 94%);
          border-top: 1px solid hsl(0 0% 82%);
          border-bottom: 1px solid hsl(0 0% 82%);
          box-shadow:
            inset 0 2px 3px rgba(0,0,0,0.04),
            inset 0 -2px 3px rgba(0,0,0,0.04);
        }
        :is(.dark) .page-break-gap {
          background: hsl(var(--muted));
          border-color: hsl(var(--border));
        }
        .doc-page-break {
          height: 0;
          overflow: hidden;
          margin: 0;
          padding: 0;
        }
        .entity-annotation {
          position: relative;
          cursor: default;
        }
        .entity-annotation::after {
          content: attr(data-entity-label) " · " attr(data-entity-score) "%";
          position: absolute;
          bottom: calc(100% + 4px);
          left: 50%;
          transform: translateX(-50%);
          background: hsl(0 0% 15%);
          color: white;
          padding: 3px 8px;
          border-radius: 4px;
          font-size: 11px;
          line-height: 1.4;
          white-space: nowrap;
          opacity: 0;
          pointer-events: none;
          transition: opacity 0.15s;
          z-index: 50;
        }
        .entity-annotation:hover::after {
          opacity: 1;
        }
        .tiptap table {
          border-collapse: collapse;
          width: 100%;
          margin: 1em 0;
        }
        .tiptap table td,
        .tiptap table th {
          border: 1px solid hsl(0 0% 80%);
          padding: 6px 10px;
          vertical-align: top;
        }
        .tiptap table th {
          background: hsl(0 0% 96%);
          font-weight: 600;
        }
        .paper-fit-tables .tiptap table {
          width: 100% !important;
          min-width: 0 !important;
          table-layout: fixed;
        }
        .paper-fit-tables .tiptap table td,
        .paper-fit-tables .tiptap table th {
          overflow-wrap: anywhere;
        }
        .${HIGHLIGHT_CLS} {
          background: rgba(59, 130, 246, 0.08) !important;
          border-left: 3px solid #3B82F6;
          padding-left: 8px;
          transition: background 0.3s;
        }
        .${HIGHLIGHT_BODY_CLS} {
          background: rgba(59, 130, 246, 0.04);
        }
        table.${HIGHLIGHT_CLS} td,
        table.${HIGHLIGHT_CLS} th {
          background: rgba(59, 130, 246, 0.06) !important;
        }
      `}</style>
    </div>
  );
}
