"use client";

import { useEffect, useState } from "react";
import { ChevronRight, FileText, History, Loader2, RefreshCw } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { listDocumentAnalysisRuns, type DocumentAnalysisRun, type DocumentAnalysisRunList } from "@/lib/api";
import { DOCUMENT_ANALYSIS_STATUS_LABELS, formatDocumentAnalysisDate } from "@/lib/document-analysis";
import { cn } from "@/lib/utils";

const PAGE_SIZE = 10;

export function DocumentAnalysisHistory({
  activeRunId,
  currentRun,
  onSelect,
  className,
}: {
  activeRunId: string | null;
  currentRun: DocumentAnalysisRun | null;
  onSelect: (runId: string) => void;
  className?: string;
}) {
  const [offset, setOffset] = useState(0);
  const [refreshNonce, setRefreshNonce] = useState(0);
  const [result, setResult] = useState<{
    offset: number;
    value: DocumentAnalysisRunList;
  } | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | null = null;
    const load = async () => {
      setLoading(true);
      try {
        const value = await listDocumentAnalysisRuns(PAGE_SIZE, offset, controller.signal);
        if (controller.signal.aborted) return;
        setResult({ offset, value });
        setError(false);
      } catch {
        if (!controller.signal.aborted) setError(true);
      } finally {
        if (!controller.signal.aborted) {
          setLoading(false);
          timer = setTimeout(load, 10_000);
        }
      }
    };
    void load();
    return () => {
      controller.abort();
      if (timer) clearTimeout(timer);
    };
  }, [offset, refreshNonce]);

  const page = result?.offset === offset ? result.value : null;

  return (
    <Card aria-label="文档分析历史" className={cn("flex min-h-0 flex-col overflow-hidden", className)}>
      <CardHeader className="shrink-0 space-y-2 border-b p-3">
        <div className="flex items-center justify-between gap-2">
          <CardTitle className="flex items-center gap-2 text-sm"><History className="size-4" />分析历史</CardTitle>
          <Button
            size="icon"
            variant="ghost"
            className="size-7 shrink-0"
            aria-label="刷新历史"
            title="刷新历史"
            disabled={loading}
            onClick={() => setRefreshNonce((value) => value + 1)}
          >
            <RefreshCw className={cn("size-3.5", loading && "animate-spin")} />
          </Button>
        </div>
        <p className="text-xs leading-relaxed text-muted-foreground">选择任务，继续查看分析结果。</p>
      </CardHeader>
      <CardContent className="min-h-0 max-h-96 flex-1 space-y-3 overflow-y-auto p-2 lg:max-h-none">
        {error && (
          <div role="alert" className="space-y-2 rounded-md p-2 text-xs text-destructive">
            <span>分析历史加载失败，请重试。</span>
            <Button className="flex h-7 text-xs" size="sm" variant="outline" disabled={loading} onClick={() => setRefreshNonce((value) => value + 1)}>重试</Button>
          </div>
        )}
        {!page && loading && <p role="status" className="flex items-center gap-2 px-2 py-4 text-xs text-muted-foreground"><Loader2 className="size-4 shrink-0 animate-spin" />正在加载分析历史…</p>}
        {page && page.items.length === 0 && !error && (
          <p className="px-2 py-4 text-xs leading-relaxed text-muted-foreground">{offset === 0 ? "暂无分析历史，上传文档并开始分析后，任务会显示在这里。" : "本页暂无任务，请返回上一页查看。"}</p>
        )}
        {page && page.items.length > 0 && (
          <ul className="space-y-1.5" aria-label="历史分析任务">
            {page.items.map((item) => {
              const run = currentRun?.recognition_run_id === item.recognition_run_id
                && currentRun.run_revision >= item.run_revision ? currentRun : item;
              const selected = item.recognition_run_id === activeRunId;
              const unavailable = ["deleting", "deleted", "expired"].includes(run.status);
              return (
                <li key={item.recognition_run_id}>
                  <button
                    type="button"
                    aria-label={`查看分析 ${item.input.filename}，${formatDocumentAnalysisDate(item.created_at)}`}
                    aria-current={selected ? "true" : undefined}
                    disabled={unavailable}
                    onClick={() => onSelect(item.recognition_run_id)}
                    className={cn(
                      "w-full min-w-0 space-y-2 rounded-lg border border-transparent p-2.5 text-left transition-colors hover:bg-muted/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50",
                      selected && "border-primary/40 bg-primary/5 hover:bg-primary/10",
                    )}
                  >
                    <span className="flex items-start gap-2">
                      <FileText className={cn("mt-0.5 size-3.5 shrink-0 text-muted-foreground", selected && "text-primary")} />
                      <span className="line-clamp-2 min-w-0 flex-1 break-all text-xs font-medium leading-relaxed" title={item.input.filename}>{item.input.filename}</span>
                      <ChevronRight className={cn("mt-0.5 size-3.5 shrink-0 text-muted-foreground", selected && "text-primary")} />
                    </span>
                    <span className="flex flex-wrap items-center gap-1.5">
                      <Badge className="px-1.5 py-0 text-[11px]" variant={run.status === "retryable_failure" || run.status === "blocked_dependency" ? "destructive" : "secondary"}>{DOCUMENT_ANALYSIS_STATUS_LABELS[run.status]}</Badge>
                      {selected && <span className="text-[11px] font-medium text-primary">当前查看</span>}
                    </span>
                    <span className="block space-y-0.5 text-[11px] leading-relaxed text-muted-foreground">
                      <span className="block truncate" title={item.input.root_class_label}>{item.input.root_class_label}</span>
                      <span className="block">创建于 {formatDocumentAnalysisDate(item.created_at)}</span>
                      {run.expires_at && <span className="block">保留至 {formatDocumentAnalysisDate(run.expires_at)}</span>}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </CardContent>
      {(offset > 0 || page?.has_more) && (
        <div className="flex shrink-0 items-center justify-between gap-1 border-t p-2 text-xs text-muted-foreground">
          <Button className="h-7 px-2 text-xs" size="sm" variant="ghost" disabled={offset === 0 || loading} onClick={() => setOffset((value) => Math.max(0, value - PAGE_SIZE))}>上一页</Button>
          <span className="whitespace-nowrap">第 {offset / PAGE_SIZE + 1} 页</span>
          <Button className="h-7 px-2 text-xs" size="sm" variant="ghost" disabled={!page?.has_more || loading || error} onClick={() => setOffset((value) => value + PAGE_SIZE)}>下一页</Button>
        </div>
      )}
    </Card>
  );
}
