"use client";

import { Database, GitBranch, MapPinned, ScanSearch } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import type { DocumentAnalysisRun } from "@/lib/api";
import { cn } from "@/lib/utils";

function Metric({ label, value, detail }: {
  label: string;
  value: string | number;
  detail?: string;
}) {
  return <div className="min-w-0 rounded-md bg-muted/40 p-3">
    <p className="text-xs text-muted-foreground">{label}</p>
    <p className="mt-1 text-lg font-semibold tabular-nums">{value}</p>
    {detail && <p className="mt-1 text-xs leading-relaxed text-muted-foreground">{detail}</p>}
  </div>;
}

function ProgressBar({ value, total, label }: { value: number; total: number; label: string }) {
  const percent = total > 0 ? Math.min(100, Math.max(0, value / total * 100)) : 0;
  return <div className="space-y-1.5">
    <div className="flex items-center justify-between gap-3 text-xs">
      <span>{label}</span>
      <span className="tabular-nums text-muted-foreground">{value} / {total}</span>
    </div>
    <div className="h-1.5 overflow-hidden rounded-full bg-muted">
      <div className="h-full rounded-full bg-primary transition-[width]" style={{ width: `${percent}%` }} />
    </div>
  </div>;
}

export function DocumentAnalysisProgressPanel({ run }: { run: DocumentAnalysisRun }) {
  const progress = run.progress;
  const discovery = progress.record_discovery;
  const processed = progress.records_examined + progress.records_incomplete;
  const decisions = progress.decisions;
  const decided = decisions.supported + decisions.unsupported + decisions.undetermined;
  const routingAvailable = discovery?.routing_cards != null;
  const routed = discovery?.routed_groups ?? 0;
  const readingGroups = discovery?.reading_groups ?? 0;

  return <section aria-label="本体引导识别进度" className="space-y-4 rounded-lg border p-4">
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div>
        <h3 className="text-sm font-semibold">本体引导识别进度</h3>
        <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
          根本体 Schema Card 定位文档元数据区域，再从授权原文中发现并核验实体、属性和关系。
        </p>
      </div>
      <Badge variant="outline">
        {discovery?.mode === "semantic" ? "语义区域路由" : discovery ? "词项区域路由" : "等待区域路由"}
      </Badge>
    </div>

    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      <article className="space-y-3 rounded-lg border p-3">
        <div className="flex items-center gap-2 text-xs font-medium"><GitBranch className="size-4 text-primary" />Schema Card</div>
        <Metric label="根关系卡片" value={discovery?.routing_cards ?? "—"}
          detail={routingAvailable ? "由冻结根本体的属性和出向关系编译" : "当前运行尚未提交路由卡片"} />
      </article>
      <article className="space-y-3 rounded-lg border p-3">
        <div className="flex items-center gap-2 text-xs font-medium"><MapPinned className="size-4 text-primary" />文档区域</div>
        <div className="grid grid-cols-2 gap-2">
          <Metric label="元数据节点" value={discovery?.metadata_nodes ?? "—"} />
          <Metric label="选中区域" value={discovery?.selected_regions ?? "—"} />
        </div>
        {discovery && <ProgressBar value={routed} total={readingGroups} label="已路由原文组" />}
        {discovery && (discovery.unrouted_groups ?? 0) > 0 && <p className="text-xs text-muted-foreground">
          {discovery.unrouted_groups} 组未命中 Schema 区域，不进入实体发现。
        </p>}
      </article>
      <article className="space-y-3 rounded-lg border p-3">
        <div className="flex items-center gap-2 text-xs font-medium"><ScanSearch className="size-4 text-primary" />候选执行</div>
        <div className="grid grid-cols-2 gap-2">
          <Metric
            label={discovery?.execution_mode === "region_batch" ? "已调度区域批次" : "已调度"}
            value={discovery?.admitted_pairs ?? 0}
          />
          <Metric
            label={discovery?.execution_mode === "region_batch" ? "待调度区域批次" : "待调度"}
            value={discovery?.remaining_pairs ?? 0}
          />
        </div>
        {discovery && <p className="text-xs text-muted-foreground">
          {discovery.unselected_pairs} 个原文组与执行卡组合未入选，未经模型识别。
        </p>}
      </article>
      <article className="space-y-3 rounded-lg border p-3">
        <div className="flex items-center gap-2 text-xs font-medium"><Database className="size-4 text-primary" />核验与图谱</div>
        <ProgressBar value={processed} total={progress.records_planned} label="记录覆盖" />
        <div className="grid grid-cols-3 gap-2 text-center text-xs">
          <div><strong className="block text-sm tabular-nums">{decisions.supported}</strong><span className="text-muted-foreground">支持</span></div>
          <div><strong className="block text-sm tabular-nums">{decisions.unsupported}</strong><span className="text-muted-foreground">不支持</span></div>
          <div><strong className="block text-sm tabular-nums">{decisions.undetermined}</strong><span className="text-muted-foreground">未决</span></div>
        </div>
        {!decided && <p className="text-xs text-muted-foreground">尚无完成证据核验的声明。</p>}
      </article>
    </div>

    <div className="flex flex-wrap gap-2 text-xs">
      <Badge variant="secondary">任务已尝试 {progress.tasks_attempted}</Badge>
      <Badge variant="outline">模型调用 {progress.model_calls}</Badge>
      {(progress.model_calls_reserved ?? 0) > progress.model_calls && (
        <Badge variant="outline">已预留 {progress.model_calls_reserved}</Badge>
      )}
      {(progress.model_calls_unresolved ?? 0) > 0 && (
        <Badge variant="outline">待核实调用 {progress.model_calls_unresolved}</Badge>
      )}
      <Badge variant="outline">记录未完成 {progress.records_incomplete}</Badge>
      <Badge variant="outline" className={cn(progress.records_unattempted > 0 && "border-amber-500/50")}>记录未处理 {progress.records_unattempted}</Badge>
    </div>
  </section>;
}
