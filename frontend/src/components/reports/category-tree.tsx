"use client";

import { useMemo, type ReactNode } from "react";
import { Folder, Layers } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import {
  DEVELOPMENT_PHASES,
  type ReportOrDocument,
} from "@/lib/api";

interface CategoryNode {
  name: string;
  count: number;
}

/**
 * 左侧分类轴 = **研发阶段**（上传目标；文档类型是另一个轴，上传后由用户指定，作为列展示）。
 * 阶段文件夹恒按 skos:notation 定序展示（计数为 0 也保留——以便触达“空阶段”并作为上传目标）；
 * 其后追加条目里出现的其他分类（生成报告类型、“未分阶段”文档等），按计数降序、同数按名排列。
 */
function buildCategories(items: ReportOrDocument[]): CategoryNode[] {
  const counts = new Map<string, number>();
  for (const item of items) {
    const name = item.category || "未分类";
    counts.set(name, (counts.get(name) ?? 0) + 1);
  }

  // 1) 研发阶段文件夹——固定顺序在前（受控词表，恒存在）。
  const phaseLabels = DEVELOPMENT_PHASES.map((p) => p.label);
  const phaseNodes: CategoryNode[] = phaseLabels.map((name) => ({
    name,
    count: counts.get(name) ?? 0,
  }));

  // 2) 其余动态分类（报告类型、未分阶段…）——排除阶段后按计数降序。
  const phaseSet = new Set(phaseLabels);
  const dynamicNodes: CategoryNode[] = Array.from(counts.entries())
    .filter(([name]) => !phaseSet.has(name))
    .map(([name, count]) => ({ name, count }))
    .sort((a, b) => {
      if (b.count !== a.count) return b.count - a.count;
      return a.name.localeCompare(b.name, "zh-Hans-CN");
    });

  return [...phaseNodes, ...dynamicNodes];
}

export function CategoryTree({
  items,
  selected,
  onSelect,
}: {
  items: ReportOrDocument[];
  /** null 表示“全部”根。 */
  selected: string | null;
  onSelect: (category: string | null) => void;
}) {
  const categories = useMemo(() => buildCategories(items), [items]);

  const renderRow = (
    key: string,
    label: string,
    count: number,
    active: boolean,
    onClick: () => void,
    icon: ReactNode,
  ) => (
    <button
      key={key}
      type="button"
      onClick={onClick}
      className={cn(
        "flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-sm transition-colors",
        active
          ? "bg-accent text-accent-foreground"
          : "text-muted-foreground hover:bg-accent/60 hover:text-foreground",
      )}
    >
      <span className="[&_svg]:size-4">{icon}</span>
      <span className="min-w-0 flex-1 truncate" title={label}>
        {label}
      </span>
      <Badge variant="secondary" className="shrink-0">
        {count}
      </Badge>
    </button>
  );

  return (
    <div className="space-y-1">
      {renderRow(
        "__all__",
        "全部",
        items.length,
        selected === null,
        () => onSelect(null),
        <Layers />,
      )}
      {categories.map((category) =>
        renderRow(
          category.name,
          category.name,
          category.count,
          selected === category.name,
          () => onSelect(category.name),
          <Folder />,
        ),
      )}
    </div>
  );
}
