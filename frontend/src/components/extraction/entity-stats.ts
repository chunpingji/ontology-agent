import type { TreeNode } from "@/lib/api";
import { entityColorIndex } from "./entity-mark";

export interface EntityStats {
  label: string;
  count: number;
  colorIndex: number;
}

export interface EntityGroup {
  category: string;
  categoryColorIndex: number;
  items: EntityStats[];
  totalCount: number;
}

interface TiptapNode {
  type?: string;
  content?: TiptapNode[];
  marks?: { type: string; attrs?: Record<string, unknown> }[];
}

interface ExcelContent {
  headers: string[];
  rows: Record<string, { value: string; annotations: { label: string }[] }>[];
}

function countFromTiptap(node: TiptapNode, counts: Map<string, number>) {
  if (node.content) {
    for (const child of node.content) {
      if (child.type === "paragraph" || child.type === "heading" || child.type === "tableCell") {
        countBlockEntities(child.content ?? [], counts);
      } else {
        countFromTiptap(child, counts);
      }
    }
  }
}

function countBlockEntities(nodes: TiptapNode[], counts: Map<string, number>) {
  let prevLabel: string | null = null;
  for (const node of nodes) {
    if (node.type === "text" && node.marks) {
      const entityMark = node.marks.find((m) => m.type === "entity-annotation");
      const label = (entityMark?.attrs?.label as string) ?? null;
      if (label && label !== prevLabel) {
        counts.set(label, (counts.get(label) ?? 0) + 1);
      }
      prevLabel = label;
    } else {
      prevLabel = null;
      if (node.content) countFromTiptap(node, counts);
    }
  }
}

function countFromExcel(content: ExcelContent, counts: Map<string, number>) {
  for (const row of content.rows) {
    for (const cell of Object.values(row)) {
      if (cell.annotations) {
        for (const ann of cell.annotations) {
          counts.set(ann.label, (counts.get(ann.label) ?? 0) + 1);
        }
      }
    }
  }
}

export function extractEntityStats(
  content: unknown,
  sourceType: "word" | "excel",
): EntityStats[] {
  const counts = new Map<string, number>();

  if (sourceType === "word") {
    countFromTiptap(content as TiptapNode, counts);
  } else {
    countFromExcel(content as ExcelContent, counts);
  }

  return Array.from(counts.entries())
    .map(([label, count]) => ({ label, count, colorIndex: entityColorIndex(label) }))
    .sort((a, b) => b.count - a.count);
}

function walkTree(node: TreeNode, rootLabel: string, map: Map<string, string>) {
  const label = node.label ?? node.name;
  map.set(label, rootLabel);
  for (const child of node.children) {
    walkTree(child, rootLabel, map);
  }
}

export function buildLabelToCategoryMap(trees: TreeNode[]): Map<string, string> {
  const map = new Map<string, string>();
  for (const root of trees) {
    const rootLabel = root.label ?? root.name;
    walkTree(root, rootLabel, map);
  }
  return map;
}

export function groupStatsByCategory(
  stats: EntityStats[],
  labelToCategory: Map<string, string>,
): EntityGroup[] {
  const groups = new Map<string, EntityStats[]>();

  for (const stat of stats) {
    const category = labelToCategory.get(stat.label) ?? "未分类";
    const list = groups.get(category);
    if (list) {
      list.push(stat);
    } else {
      groups.set(category, [stat]);
    }
  }

  return Array.from(groups.entries())
    .map(([category, items]) => ({
      category,
      categoryColorIndex: entityColorIndex(category),
      items: items.sort((a, b) => b.count - a.count),
      totalCount: items.reduce((sum, s) => sum + s.count, 0),
    }))
    .sort((a, b) => b.totalCount - a.totalCount);
}
