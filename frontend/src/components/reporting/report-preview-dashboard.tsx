"use client";

import { useRef, useState, type ReactNode } from "react";
import { Check, ChevronDown, ChevronRight, Download, Info, ListTree, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { cn } from "@/lib/utils";
import { groupsIn, type OutputGroup, type OutputUnit, type ReportRun, type TemplateV2 } from "@/lib/reporting-v2";
import { outputCoverage, summarizeCoverage, type ReportInputSnapshot, type ReportOutputResult } from "@/lib/report-preview";
import { OutputPreview } from "./output-preview";

const statuses: Record<string, { label: string; dot: string }> = {
  pending: { label: "待检查", dot: "bg-muted-foreground" },
  ready: { label: "已就绪", dot: "bg-success" },
  missing: { label: "有缺口", dot: "bg-destructive" },
  inactive: { label: "不适用", dot: "bg-muted-foreground/50" },
  failed: { label: "生成失败", dot: "bg-destructive" },
};

export function ReportPreviewDashboard({ template, templateName, snapshot, outputs, current, generating,
  refreshing, hasSources, isReport, onDownload, onLocateSource }: {
  template?: TemplateV2; templateName?: string; snapshot?: ReportInputSnapshot; outputs: ReportOutputResult[];
  current?: ReportRun; generating: boolean; refreshing: boolean; hasSources: boolean; isReport: boolean;
  onDownload?: () => void; onLocateSource?: (node: import("@/lib/reporting-v2").OutputNode) => void;
}) {
  const [selected, setSelected] = useState("");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const tree = useRef<HTMLDivElement>(null);
  const coverage = summarizeCoverage(snapshot);
  const entries = template?.sections.flatMap((section) => {
    function collect(groups: OutputGroup[], parents: string[]): { unit: OutputUnit; ancestors: string[] }[] {
      return groups.flatMap((group) => {
        const ancestors = [...parents, group.group_id];
        return [...group.units.map((unit) => ({ unit, ancestors })), ...collect(group.groups ?? [], ancestors)];
      });
    }
    return collect(section.groups, [section.section_id]);
  }) ?? [];
  const states = new Map(entries.map(({ unit, ancestors }) => [unit.output_id,
    outputCoverage(unit.output_id, ancestors, snapshot, outputs)]));
  const selectedUnit = entries.find(({ unit }) => unit.output_id === selected)?.unit;
  const selectedCoverage = states.get(selected);
  function jumpToMissing() {
    const missing = entries.find(({ unit }) => ["missing", "failed"].includes(states.get(unit.output_id)!.state));
    if (!missing) return;
    setExpanded((previous) => new Set([...previous, ...missing.ancestors]));
    setSelected(missing.unit.output_id);
    requestAnimationFrame(() => {
      Array.from(tree.current?.querySelectorAll<HTMLButtonElement>("[data-output-id]") ?? [])
        .find((button) => button.dataset.outputId === missing.unit.output_id)?.scrollIntoView({ block: "nearest", behavior: "smooth" });
    });
  }
  function groupNode(group: OutputGroup): ReactNode {
    const open = expanded.has(group.group_id);
    const units = groupsIn([group]).flatMap((item) => item.units);
    const ready = units.filter((unit) => states.get(unit.output_id)?.state === "ready").length;
    return <div key={group.group_id} className="my-1">
      <button type="button" aria-expanded={open} className="flex w-full items-center gap-1 rounded px-2 py-1 text-left text-sm hover:bg-muted/60"
        onClick={() => setExpanded((previous) => {
          const next = new Set(previous); if (next.has(group.group_id)) next.delete(group.group_id); else next.add(group.group_id); return next;
        })}>
        {open ? <ChevronDown className="size-3.5 shrink-0 text-muted-foreground" /> : <ChevronRight className="size-3.5 shrink-0 text-muted-foreground" />}
        <span className="min-w-0 flex-1 break-words font-medium">{group.title || "未命名分组"}</span>
        <span className="text-xs tabular-nums text-muted-foreground">{snapshot ? ready : "—"}/{units.length}</span>
      </button>
      {open && <div className="ml-5 space-y-0.5">
        {group.units.map((unit) => {
          const status = statuses[states.get(unit.output_id)!.state];
          return <button type="button" key={unit.output_id} data-output-id={unit.output_id} aria-pressed={selected === unit.output_id}
            className={cn("flex w-full items-center gap-2 rounded px-2 py-1 text-left text-sm transition-colors", selected === unit.output_id ? "bg-blue-50 ring-1 ring-blue-300" : "hover:bg-muted/40")}
            onClick={() => setSelected(unit.output_id)}>
            <span className={cn("size-2 shrink-0 rounded-full", status.dot)} />
            <span className="min-w-0 flex-1 truncate">{unit.title || "未命名内容"}</span>
            <span className="shrink-0 rounded-full border px-2 text-[10px]">{status.label}</span>
          </button>;
        })}
        {group.groups?.map(groupNode)}
      </div>}
    </div>;
  }

  type Step = { title: string; desc: string; state: "done" | "active" | "pending"; detail?: ReactNode };
  const resolving = refreshing || (generating && !snapshot);
  const steps: Step[] = [
    { title: "数据抽取解析", desc: resolving ? "正在读取来源与已审核数据…" : snapshot ? "已完成 · 已固定来源与输入" : hasSources ? "来源已关联 · 等待检查" : "请先关联源文档",
      state: resolving ? "active" : snapshot ? "done" : "pending" },
    { title: "模板匹配", desc: resolving ? "正在校验模板…" : snapshot ? `已完成 · ${templateName || template?.doc_no || "当前模板"}` : "等待校验当前模板",
      state: resolving ? "active" : snapshot ? "done" : "pending" },
    { title: "覆盖率分析", desc: refreshing ? "重新分析中…" : coverage ? `已完成 · ${coverage.satisfied}/${coverage.total} 项要求已满足${coverage.missing ? `，${coverage.missing} 项未满足` : ""}` : "等待检查输入完整性",
      state: refreshing ? "active" : coverage ? "done" : "pending",
      detail: coverage && !refreshing ? <div className="mt-1 space-y-1.5 rounded-lg border bg-muted p-3">
        <div className="flex justify-between text-xs"><span className="text-muted-foreground">已满足要求</span><span className="font-semibold">{coverage.satisfied} / {coverage.total}</span></div>
        <div className="flex justify-between text-xs"><span className="text-muted-foreground">未满足要求</span><span className={cn("font-semibold", coverage.missing > 0 && "text-destructive")}>{coverage.missing}</span></div>
        <div className="flex justify-between text-xs"><span className="text-muted-foreground">不适用</span><span className="font-semibold">{coverage.inactive}</span></div>
      </div> : undefined },
    { title: "AI 行文生成", desc: generating ? "生成中…" : isReport && current?.execution_status === "completed" ? "已完成" : isReport && current?.execution_status === "failed" ? "生成失败 · 可重试未完成内容" : "等待生成报告",
      state: generating ? "active" : isReport && current?.execution_status === "completed" ? "done" : "pending" },
    { title: "DOCX 报告渲染", desc: generating ? "生成中…" : isReport && onDownload ? "已完成 · 已生成 Word 文档" : "等待中",
      state: generating ? "active" : isReport && onDownload ? "done" : "pending" },
  ];
  const progress = Math.round(steps.filter((step) => step.state === "done").length / steps.length * 100);
  return <div className="grid grid-cols-1 overflow-hidden rounded-lg border bg-card lg:grid-cols-[minmax(0,1fr)_420px]">
    <section aria-label="生成进度" className="flex min-w-0 flex-col gap-5 p-6 lg:border-r">
      <div className="space-y-2"><div className="flex items-center justify-between">
        <span className="text-[15px] font-semibold">生成进度</span><span className="text-sm font-semibold text-primary">{progress}%</span>
      </div><Progress value={progress} className="h-2" /></div>
      <div className="flex flex-col">{steps.map((step, index) => <div key={step.title} className="flex gap-4">
        <div className="flex flex-col items-center gap-1">
          <div className={cn("flex size-7 shrink-0 items-center justify-center rounded-full", step.state === "done" && "bg-success text-success-foreground", step.state === "active" && "bg-primary text-primary-foreground", step.state === "pending" && "border-2 border-border")}>
            {step.state === "done" && <Check className="size-4" />}{step.state === "active" && <Loader2 className="size-4 animate-spin" />}
          </div>{index < steps.length - 1 && <div className={cn("min-h-8 w-0.5 flex-1", step.state === "done" ? "bg-success" : "bg-border")} />}
        </div>
        <div className={cn("min-w-0 flex-1 space-y-1", index === steps.length - 1 ? "pb-1" : "pb-3")}>
          <p className={cn("text-sm", step.state === "pending" ? "font-medium text-muted-foreground" : step.state === "active" ? "font-semibold text-primary" : "font-semibold")}>{step.title}</p>
          <p className="text-xs text-muted-foreground">{step.desc}</p>{step.detail}
        </div>
      </div>)}</div>
    </section>
    <section aria-label="报告结构" className="flex min-h-0 min-w-0 flex-col border-t lg:border-t-0">
      <div className="flex items-center justify-between border-b px-5 py-3">
        <div className="flex items-center gap-2"><ListTree className="size-4" /><span className="text-sm font-semibold">报告结构</span>
          <span className={cn("text-sm font-semibold tabular-nums", !coverage ? "text-muted-foreground" : coverage.percent >= 80 ? "text-success" : coverage.percent >= 50 ? "text-amber-500" : "text-destructive")}>{coverage ? `${coverage.percent}%` : "待检查"}</span>
        </div>
        {coverage && <div className="flex items-center gap-1.5">
          <CoverageBadge tone="success" count={coverage.satisfied} label="已满足" />
          <CoverageBadge tone="destructive" count={coverage.missing} label="未满足" onClick={coverage.missing ? jumpToMissing : undefined} />
          <CoverageBadge tone="muted" count={coverage.inactive} label="不适用" />
        </div>}
      </div>
      <div ref={tree} className="max-h-[44vh] min-h-0 flex-1 overflow-y-auto px-4 py-3">
        {template?.sections.map((section) => <div key={section.section_id} className="mb-2">
          <div className="rounded bg-muted/50 px-2 py-1 text-sm font-semibold">{section.title || "未命名章节"}</div>
          <div className="ml-2 border-l pl-2">{section.groups.map(groupNode)}</div>
        </div>)}
        {!template && <p className="py-4 text-sm text-muted-foreground">等待载入报告结构。</p>}
      </div>
      {selectedUnit && selectedCoverage && <div className="max-h-[36vh] overflow-y-auto border-t px-4 py-3 text-sm space-y-3">
        <div className="flex items-center justify-between gap-2"><strong>{selectedUnit.title}</strong><span className="text-xs text-muted-foreground">{statuses[selectedCoverage.state].label}</span></div>
        {selectedCoverage.requirements.map((item, index) => <div key={`${item.requirement_id}-${item.execution_scope_id}-${index}`} className="rounded border p-2 text-xs">
          <p>{template?.definitions.inputs[item.input_id]?.label || item.input_id}{item.field_path.length ? ` · ${item.field_path.join(" / ")}` : ""}</p>
          <p className={item.activation !== "inactive" && !item.satisfied ? "text-destructive" : "text-muted-foreground"}>{item.activation === "inactive" ? "不适用" : item.satisfied ? "已满足" : "未满足"}</p>
          {snapshot?.blocking_issues.filter((issue) => item.issue_refs.includes(issue.issue_id)).map((issue) => <p key={issue.issue_id}>{issue.message || issue.code}</p>)}
        </div>)}
        {!snapshot && <p className="text-xs text-muted-foreground">刷新覆盖率后，可查看输入与缺口。</p>}
        {selectedCoverage.results.map((result) => <OutputPreview key={result.id} ast={result.payload.output_ast} compact onLocateSource={onLocateSource} />)}
      </div>}
      <div className="flex items-center justify-between gap-2 border-t px-5 py-3">
        <div className="flex items-center gap-1.5 text-xs text-muted-foreground"><Info className="size-3.5 shrink-0" /><span>生成完成后可下载 DOCX 报告</span></div>
        <Button variant="outline" size="sm" disabled={!onDownload} onClick={onDownload}><Download className="mr-1 size-3.5" />下载报告</Button>
      </div>
    </section>
  </div>;
}

function CoverageBadge({ tone, count, label, onClick }: { tone: "success" | "destructive" | "muted"; count: number; label: string; onClick?: () => void }) {
  return <button type="button" title={`${label} ${count}`} aria-label={`${label} ${count}`} disabled={!onClick} onClick={onClick}
    className={cn("flex items-center gap-1 rounded-full px-2 py-0.5", tone === "success" ? "bg-success/10 text-success" : tone === "destructive" ? "bg-destructive/10 text-destructive" : "bg-muted text-muted-foreground", onClick ? "cursor-pointer hover:opacity-80" : "cursor-default")}>
    <span className={cn("size-1.5 rounded-full", tone === "success" ? "bg-success" : tone === "destructive" ? "bg-destructive" : "bg-muted-foreground")} /><span className="text-[11px] font-semibold">{count}</span>
  </button>;
}
