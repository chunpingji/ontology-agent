"use client";

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

interface WordViewerProps {
  content: Record<string, unknown>;
}

export function WordViewer({ content }: WordViewerProps) {
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

  return (
    <div className="prose prose-sm max-w-none dark:prose-invert">
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
      `}</style>
    </div>
  );
}
