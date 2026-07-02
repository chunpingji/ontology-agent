"use client";

import { useEffect, useRef } from "react";
import { useEditor, EditorContent } from "@tiptap/react";
import { Extension } from "@tiptap/core";
import StarterKit from "@tiptap/starter-kit";
import { Table } from "@tiptap/extension-table";
import { TableRow } from "@tiptap/extension-table-row";
import { TableCell } from "@tiptap/extension-table-cell";
import { TableHeader } from "@tiptap/extension-table-header";
import Underline from "@tiptap/extension-underline";
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

const HIGHLIGHT_CLS = "source-highlight";
const HIGHLIGHT_BODY_CLS = "source-highlight-body";

function parseSourceRef(ref: string): string[] {
  const segments = ref.split(" / ");
  return segments
    .map((s) => s.replace(/^[§表]\s*/, "").trim())
    .filter(Boolean)
    .reverse();
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

interface WordViewerProps {
  content: Record<string, unknown>;
  highlightRef?: string | null;
}

export function WordViewer({ content, highlightRef }: WordViewerProps) {
  const wrapperRef = useRef<HTMLDivElement>(null);

  const editor = useEditor({
    extensions: [
      StarterKit,
      TextAlign,
      Underline,
      Table,
      TableRow,
      TableCell,
      TableHeader,
      EntityAnnotation,
    ],
    content: content as Parameters<typeof useEditor>[0] extends {
      content?: infer C;
    }
      ? C
      : never,
    editable: false,
    immediatelyRender: true,
  });

  useEffect(() => {
    if (!wrapperRef.current) return;
    const container = wrapperRef.current;

    clearHighlights(container);

    if (!highlightRef) return;

    const keywords = parseSourceRef(highlightRef);
    if (keywords.length === 0) return;

    // Delay slightly to ensure DOM is ready after render
    const timer = setTimeout(() => {
      const firstMatch = applyHighlight(container, keywords);
      if (firstMatch) {
        firstMatch.scrollIntoView({ behavior: "smooth", block: "start" });
      }
    }, 50);

    return () => clearTimeout(timer);
  }, [highlightRef]);

  return (
    <div ref={wrapperRef} className="prose prose-sm max-w-none dark:prose-invert">
      <EditorContent editor={editor} />
      <style>{`
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
