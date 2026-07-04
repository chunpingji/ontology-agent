"use client";

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ClipboardCheck, Folder, Search } from "lucide-react";

import {
  getApprovalTasks,
  approvalCategoryCounts,
  APPROVAL_CATEGORIES,
  type ApprovalTask,
} from "@/lib/api";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { TaskCard } from "@/components/approval/task-card";
import { ApprovalDetail } from "@/components/approval/approval-detail";

type Tab = "pending" | "completed";

export default function ApprovalPage() {
  const [tab, setTab] = useState<Tab>("pending");
  const [selectedCategory, setSelectedCategory] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [search, setSearch] = useState("");

  const query = useQuery({
    queryKey: ["approval-tasks"],
    queryFn: getApprovalTasks,
  });

  const allTasks = query.data ?? [];
  const tabTasks = allTasks.filter((t) => (tab === "pending" ? t.status === "pending" : t.status === "completed"));
  const pendingCount = allTasks.filter((t) => t.status === "pending").length;
  const categories = useMemo(() => approvalCategoryCounts(tabTasks), [tabTasks]);

  const filtered = tabTasks.filter((t) => {
    if (selectedCategory && t.category !== selectedCategory) return false;
    if (search) {
      const q = search.toLowerCase();
      return t.title.toLowerCase().includes(q) || t.submitter.includes(q) || t.type.includes(q);
    }
    return true;
  });

  const categoryLabel = selectedCategory ?? "全部";
  const categoryCount = selectedCategory
    ? categories.find((c) => c.name === selectedCategory)?.count ?? 0
    : tabTasks.length;

  const selectedTask = filtered.find((t) => t.id === selectedId) ?? filtered[0] ?? null;

  return (
    <div className="-m-6 flex h-[calc(100vh-3.5rem)]">
      {/* ── Left: Category sidebar ── */}
      <aside className="flex w-56 shrink-0 flex-col border-r border-border bg-card">
        <div className="border-b border-border px-4 py-4">
          <h2 className="text-base font-semibold text-foreground">审批管理</h2>
          <div className="mt-3 flex gap-1">
            <button
              type="button"
              onClick={() => { setTab("pending"); setSelectedCategory(null); }}
              className={cn(
                "rounded-md px-3 py-1.5 text-sm font-medium transition-colors",
                tab === "pending"
                  ? "bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:bg-muted",
              )}
            >
              待办
              {pendingCount > 0 && (
                <span className={cn(
                  "ml-1.5 inline-flex size-5 items-center justify-center rounded-full text-xs",
                  tab === "pending" ? "bg-primary-foreground/20 text-primary-foreground" : "bg-destructive/10 text-destructive",
                )}>
                  {pendingCount}
                </span>
              )}
            </button>
            <button
              type="button"
              onClick={() => { setTab("completed"); setSelectedCategory(null); }}
              className={cn(
                "rounded-md px-3 py-1.5 text-sm font-medium transition-colors",
                tab === "completed"
                  ? "bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:bg-muted",
              )}
            >
              已完
            </button>
          </div>
        </div>

        <nav className="flex-1 space-y-0.5 overflow-auto px-2 py-2">
          {categories.map((cat) => {
            const active = selectedCategory === cat.name;
            return (
              <button
                key={cat.name}
                type="button"
                onClick={() => setSelectedCategory(active ? null : cat.name)}
                className={cn(
                  "flex w-full items-center gap-2 rounded-md px-3 py-2 text-left text-sm transition-colors",
                  active
                    ? "bg-accent font-medium text-accent-foreground"
                    : "text-foreground hover:bg-muted",
                )}
              >
                <Folder className="size-4 shrink-0 text-muted-foreground" />
                <span className="min-w-0 flex-1 truncate">{cat.name}</span>
                <span className="shrink-0 text-xs text-muted-foreground">{cat.count}</span>
              </button>
            );
          })}
        </nav>
      </aside>

      {/* ── Middle: Task list ── */}
      <section className="flex w-80 shrink-0 flex-col border-r border-border bg-background">
        <div className="border-b border-border px-4 py-3">
          <div className="flex items-center gap-2">
            <ClipboardCheck className="size-4 text-muted-foreground" />
            <span className="text-sm font-semibold text-foreground">{categoryLabel}</span>
            <Badge variant="outline" className="text-xs">
              {categoryCount} 待办
            </Badge>
          </div>
          <div className="relative mt-2">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input
              placeholder="搜索审批任务..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="h-8 pl-8 text-sm"
            />
          </div>
        </div>

        <div className="flex-1 overflow-auto">
          {query.isLoading ? (
            <div className="space-y-2 p-3">
              {Array.from({ length: 4 }).map((_, i) => (
                <Skeleton key={i} className="h-20 w-full rounded-md" />
              ))}
            </div>
          ) : filtered.length === 0 ? (
            <p className="p-6 text-center text-sm text-muted-foreground">
              {search ? "无匹配结果" : "暂无审批任务"}
            </p>
          ) : (
            <ul className="divide-y divide-border">
              {filtered.map((task) => (
                <TaskCard
                  key={task.id}
                  task={task}
                  active={task.id === (selectedTask?.id ?? null)}
                  onSelect={() => setSelectedId(task.id)}
                />
              ))}
            </ul>
          )}
        </div>
      </section>

      {/* ── Right: Detail ── */}
      <main className="flex min-w-0 flex-1 flex-col overflow-auto bg-background">
        {selectedTask ? (
          <ApprovalDetail task={selectedTask} />
        ) : (
          <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
            请选择一项审批任务查看详情
          </div>
        )}
      </main>
    </div>
  );
}
