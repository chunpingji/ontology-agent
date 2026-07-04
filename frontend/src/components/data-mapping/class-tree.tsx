"use client";

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Boxes, Search } from "lucide-react";

import { getAllClasses, type OntologyClassFlat } from "@/lib/api";
import { cn } from "@/lib/utils";
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { EmptyState } from "@/components/ui/empty-state";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";

export const CLASS_TREE_KEY = ["ontology-all-classes"] as const;

interface ClassGroup {
  key: string;
  classes: OntologyClassFlat[];
}

/** Group a flat class list by `module_key`, preserving first-seen order. */
function groupByModule(classes: OntologyClassFlat[]): ClassGroup[] {
  const order: string[] = [];
  const buckets = new Map<string, OntologyClassFlat[]>();
  for (const c of classes) {
    const key = c.module_key || "其他";
    if (!buckets.has(key)) {
      buckets.set(key, []);
      order.push(key);
    }
    buckets.get(key)!.push(c);
  }
  return order
    .sort((a, b) => a.localeCompare(b, "zh-Hans-CN"))
    .map((key) => ({
      key,
      classes: buckets.get(key)!.sort((a, b) => classLabel(a).localeCompare(classLabel(b), "zh-Hans-CN")),
    }));
}

const classLabel = (c: OntologyClassFlat): string => c.label || c.name;

/**
 * 类选择器（US3）：以模块分组、可搜索的本体类列表。左栏主从视图的「主」侧，
 * 选中某类后右侧展示其源绑定与属性绑定表。约 200 个类——外层容器 max-h + 滚动。
 */
export function ClassTree({
  selected,
  onSelect,
}: {
  selected: string | null;
  onSelect: (iri: string) => void;
}) {
  const [query, setQuery] = useState("");
  const [manualOpen, setManualOpen] = useState<string[] | null>(null);

  const classesQuery = useQuery({
    queryKey: CLASS_TREE_KEY,
    queryFn: getAllClasses,
  });

  const searching = query.trim().length > 0;

  const groups = useMemo(() => {
    const all = classesQuery.data ?? [];
    const q = query.trim().toLowerCase();
    const filtered = q
      ? all.filter(
          (c) =>
            classLabel(c).toLowerCase().includes(q) ||
            c.name.toLowerCase().includes(q) ||
            c.iri.toLowerCase().includes(q),
        )
      : all;
    return groupByModule(filtered);
  }, [classesQuery.data, query]);

  const allKeys = groups.map((g) => g.key);
  const openValue = searching ? allKeys : (manualOpen ?? allKeys);

  return (
    <div className="flex h-full flex-col gap-3">
      <div className="relative">
        <Search className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
        <Input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="搜索类（标签 / 名称 / IRI）"
          className="pl-8"
        />
      </div>

      <div className="min-h-0 flex-1 overflow-auto rounded-md border border-border bg-card">
        {classesQuery.isLoading ? (
          <div className="space-y-2 p-3">
            <Skeleton className="h-6 w-full" />
            <Skeleton className="h-6 w-4/5" />
            <Skeleton className="h-6 w-3/5" />
            <Skeleton className="h-6 w-4/5" />
          </div>
        ) : classesQuery.isError ? (
          <div className="p-3">
            <Alert variant="destructive">
              <AlertTitle>无法加载类列表</AlertTitle>
              <AlertDescription>本体类读取失败，请稍后重试。</AlertDescription>
            </Alert>
          </div>
        ) : groups.length === 0 ? (
          <EmptyState
            className="m-3 border-0 bg-transparent py-10"
            icon={<Boxes />}
            title={searching ? "无匹配的类" : "暂无本体类"}
            description={searching ? "调整搜索关键词后重试。" : undefined}
          />
        ) : (
          <Accordion
            type="multiple"
            value={openValue}
            onValueChange={(v) => {
              if (!searching) setManualOpen(v);
            }}
            className="px-2"
          >
            {groups.map((group) => (
              <AccordionItem key={group.key} value={group.key} className="border-border">
                <AccordionTrigger className="py-2.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                  <span className="flex items-center gap-2">
                    {group.key}
                    <span className="rounded-md bg-muted px-1.5 py-0.5 text-[10px] font-normal text-muted-foreground">
                      {group.classes.length}
                    </span>
                  </span>
                </AccordionTrigger>
                <AccordionContent className="pb-2">
                  <ul className="space-y-0.5">
                    {group.classes.map((c) => {
                      const active = c.iri === selected;
                      return (
                        <li key={c.iri}>
                          <button
                            type="button"
                            onClick={() => onSelect(c.iri)}
                            className={cn(
                              "flex w-full flex-col items-start gap-0.5 rounded-md px-2 py-1.5 text-left transition-colors",
                              active
                                ? "bg-primary/10 text-primary"
                                : "text-foreground hover:bg-muted",
                            )}
                          >
                            <span className="truncate text-sm font-medium">{classLabel(c)}</span>
                            <span className="max-w-full truncate font-mono text-[11px] text-muted-foreground">
                              {c.name}
                            </span>
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                </AccordionContent>
              </AccordionItem>
            ))}
          </Accordion>
        )}
      </div>
    </div>
  );
}
