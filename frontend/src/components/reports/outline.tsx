"use client";

import { ListTree } from "lucide-react";

import { EmptyState } from "@/components/ui/empty-state";
import { cn } from "@/lib/utils";

interface OutlineEntry {
  /** 稳定 key。 */
  key: string;
  /** 展示文本。 */
  label: string;
  /** 传给 onNavigate 的目标：文档=标题文本（交给 WordViewer 高亮滚动）；报告=分节 id。 */
  target: string;
  /** 缩进层级（1 起）。 */
  level: number;
}

function collectText(node: Record<string, unknown>): string {
  if (typeof node.text === "string") return node.text;
  const children = Array.isArray(node.content) ? (node.content as Array<Record<string, unknown>>) : [];
  return children.map(collectText).join("");
}

function extractHeadings(content: Record<string, unknown> | null | undefined): OutlineEntry[] {
  const entries: OutlineEntry[] = [];
  let index = 0;

  const walk = (nodes: Array<Record<string, unknown>>): void => {
    for (const node of nodes) {
      if (node.type === "heading") {
        const text = collectText(node).trim();
        if (text) {
          const attrs = (node.attrs ?? {}) as { level?: number };
          entries.push({
            key: `heading-${index}`,
            label: text,
            target: text,
            level: attrs.level ?? 1,
          });
          index += 1;
        }
      }
      if (Array.isArray(node.content)) {
        walk(node.content as Array<Record<string, unknown>>);
      }
    }
  };

  const top = Array.isArray(content?.content)
    ? (content?.content as Array<Record<string, unknown>>)
    : [];
  walk(top);
  return entries;
}

export function Outline({
  content,
  sections,
  onNavigate,
}: {
  /** 文档模式：tiptap 内容，抽取标题作为目录。 */
  content?: Record<string, unknown> | null;
  /** 报告模式：预置分节（id + 标签）。 */
  sections?: Array<{ id: string; label: string }>;
  onNavigate: (target: string) => void;
}) {
  const entries: OutlineEntry[] = sections
    ? sections.map((section) => ({
        key: section.id,
        label: section.label,
        target: section.id,
        level: 1,
      }))
    : extractHeadings(content);

  if (entries.length === 0) {
    return (
      <EmptyState
        icon={<ListTree />}
        title="暂无目录"
        description="该内容未包含可导航的标题分节。"
      />
    );
  }

  return (
    <nav aria-label="目录" className="space-y-0.5">
      {entries.map((entry) => (
        <button
          key={entry.key}
          type="button"
          onClick={() => onNavigate(entry.target)}
          style={{ paddingLeft: `${(entry.level - 1) * 12 + 8}px` }}
          className={cn(
            "block w-full truncate rounded-md py-1.5 pr-2 text-left text-sm text-muted-foreground",
            "transition-colors hover:bg-accent hover:text-accent-foreground",
          )}
          title={entry.label}
        >
          {entry.label}
        </button>
      ))}
    </nav>
  );
}
