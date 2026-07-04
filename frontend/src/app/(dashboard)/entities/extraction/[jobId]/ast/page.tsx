"use client";

import { useParams, useRouter } from "next/navigation";
import { useCallback, useState, type ReactNode } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  FileDown,
  Loader2,
  Check,
  Download,
  Info,
  ListTree,
} from "lucide-react";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

import { ASTTreeView } from "@/components/extraction/ast-tree-view";
import { SlotDetailPanel } from "@/components/extraction/slot-detail-panel";
import { SlotActionBar } from "@/components/extraction/slot-action-bar";
import { ReportHistoryList } from "@/components/extraction/report-history-list";
import { WordViewer } from "@/components/extraction/word-viewer";

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

import {
  getAstCoverage,
  listReports,
  dismissSlot,
  undismissSlot,
  generateRiskReport,
  downloadReport,
  getExtractionJob,
  getAnnotatedDocument,
  rerunAnnotation,
  fetchAstTemplates,
  type ASTCoverageDTO,
  type SlotCoverageDTO,
  type GeneratedReportDTO,
} from "@/lib/api";

function getMissingSlots(coverage: ASTCoverageDTO): SlotCoverageDTO[] {
  return coverage.sections.flatMap((s) =>
    s.groups.flatMap((g) => g.slots.filter((sl) => sl.status === "missing_required")),
  );
}

function is422(err: unknown): boolean {
  return err instanceof Error && err.message.startsWith("API 422:");
}

const AST_READY_STATUS = new Set(["done", "reviewing"]);

// ── 015 生成进度：派生但忠实的 5 步状态 ────────────────────────────────
// 后端无逐步生成遥测；步骤状态一律从真实完成态派生：1-3 反映抽取/模板/覆盖率
// 的已完成结果（真实计数），4-5 反映 generateRiskReport 生命周期（等待/生成中/
// 已完成，以「已存在 DOCX 报告」为完成判据）。绝不虚构进度百分比。
type StepState = "done" | "active" | "pending";

interface GenStep {
  title: string;
  desc: string;
  state: StepState;
  detail?: ReactNode;
}

function StepRow({ step, last }: { step: GenStep; last: boolean }) {
  return (
    <div className="flex gap-4">
      <div className="flex flex-col items-center gap-1">
        <div
          className={cn(
            "flex size-7 shrink-0 items-center justify-center rounded-full",
            step.state === "done" && "bg-success text-success-foreground",
            step.state === "active" && "bg-primary text-primary-foreground",
            step.state === "pending" && "border-2 border-border",
          )}
        >
          {step.state === "done" && <Check className="size-4" />}
          {step.state === "active" && <Loader2 className="size-4 animate-spin" />}
        </div>
        {!last && (
          <div
            className={cn(
              "min-h-8 w-0.5 flex-1",
              step.state === "done" ? "bg-success" : "bg-border",
            )}
          />
        )}
      </div>
      <div className={cn("flex-1 space-y-1", last ? "pb-1" : "pb-3")}>
        <p
          className={cn(
            "text-sm",
            step.state === "pending"
              ? "font-medium text-muted-foreground"
              : step.state === "active"
                ? "font-semibold text-primary"
                : "font-semibold text-foreground",
          )}
        >
          {step.title}
        </p>
        <p className="text-xs text-muted-foreground">{step.desc}</p>
        {step.detail}
      </div>
    </div>
  );
}

function CoverageBadge({
  tone,
  count,
  onClick,
}: {
  tone: "success" | "destructive" | "muted";
  count: number;
  onClick?: () => void;
}) {
  const dot =
    tone === "success"
      ? "bg-success"
      : tone === "destructive"
        ? "bg-destructive"
        : "bg-muted-foreground";
  const bg =
    tone === "success"
      ? "bg-success/10"
      : tone === "destructive"
        ? "bg-destructive/10"
        : "bg-muted";
  const text =
    tone === "success"
      ? "text-success"
      : tone === "destructive"
        ? "text-destructive"
        : "text-muted-foreground";
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={!onClick}
      className={cn(
        "flex items-center gap-1 rounded-full px-2 py-0.5",
        bg,
        onClick ? "cursor-pointer hover:opacity-80" : "cursor-default",
      )}
    >
      <span className={cn("size-1.5 rounded-full", dot)} />
      <span className={cn("text-[11px] font-semibold", text)}>{count}</span>
    </button>
  );
}

export default function ASTPage() {
  const params = useParams();
  const router = useRouter();
  const queryClient = useQueryClient();
  const jobId = params.jobId as string;

  const [selectedSlot, setSelectedSlot] = useState<SlotCoverageDTO | null>(null);
  const [scrollToSlotId, setScrollToSlotId] = useState<string | null>(null);
  const [highlightRef, setHighlightRef] = useState<string | undefined>(undefined);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [selectedTemplateId, setSelectedTemplateId] = useState<string | undefined>(undefined);

  const jobQuery = useQuery({
    queryKey: ["extraction-job", jobId],
    queryFn: () => getExtractionJob(jobId),
  });

  const templatesQuery = useQuery({
    queryKey: ["ast-templates"],
    queryFn: fetchAstTemplates,
    enabled: AST_READY_STATUS.has(jobQuery.data?.status ?? ""),
  });
  const templates = templatesQuery.data ?? [];

  const coverageQuery = useQuery({
    queryKey: ["ast-coverage", jobId, selectedTemplateId ?? "default"],
    queryFn: () => getAstCoverage(jobId, selectedTemplateId),
    enabled: AST_READY_STATUS.has(jobQuery.data?.status ?? ""),
    retry: (count, err) => !is422(err) && count < 1,
  });

  const reportsQuery = useQuery({
    queryKey: ["reports", jobId],
    queryFn: () => listReports(jobId),
    enabled: AST_READY_STATUS.has(jobQuery.data?.status ?? ""),
  });

  const docQuery = useQuery({
    queryKey: ["annotated-doc", jobId],
    queryFn: () => getAnnotatedDocument(jobId),
    enabled: !!jobQuery.data?.document_path && AST_READY_STATUS.has(jobQuery.data?.status ?? ""),
  });

  const coverage = coverageQuery.data ?? null;
  const reports = reportsQuery.data ?? [];
  const docContent = (docQuery.data?.content as Record<string, unknown>) ?? null;

  const dismissMutation = useMutation({
    mutationFn: (slotId: string) => dismissSlot(jobId, slotId),
    onSuccess: (updated) => {
      queryClient.setQueryData(["ast-coverage", jobId], updated);
      if (selectedSlot) {
        const flat = updated.sections.flatMap((s) => s.groups.flatMap((g) => g.slots));
        setSelectedSlot(flat.find((s) => s.slot_id === selectedSlot.slot_id) ?? null);
      }
    },
  });

  const undismissMutation = useMutation({
    mutationFn: (slotId: string) => undismissSlot(jobId, slotId),
    onSuccess: (updated) => {
      queryClient.setQueryData(["ast-coverage", jobId], updated);
      if (selectedSlot) {
        const flat = updated.sections.flatMap((s) => s.groups.flatMap((g) => g.slots));
        setSelectedSlot(flat.find((s) => s.slot_id === selectedSlot.slot_id) ?? null);
      }
    },
  });

  const generateMutation = useMutation({
    mutationFn: () => generateRiskReport(jobId),
    onSuccess: async (result) => {
      if (result instanceof Blob) {
        const url = URL.createObjectURL(result);
        const a = document.createElement("a");
        a.href = url;
        a.download = `risk-report-${jobId.slice(0, 8)}.docx`;
        a.click();
        URL.revokeObjectURL(url);
      }
      queryClient.invalidateQueries({ queryKey: ["reports", jobId] });
    },
  });

  const rerunMutation = useMutation({
    mutationFn: () => rerunAnnotation(jobId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["ast-coverage", jobId] });
    },
  });

  const handleGenerateReport = () => {
    if (!coverage) return;
    if (coverage.missing_required > 0) {
      setConfirmOpen(true);
      return;
    }
    generateMutation.mutate();
  };

  const doGenerate = () => {
    setConfirmOpen(false);
    generateMutation.mutate();
  };

  const handleDownload = async (report: GeneratedReportDTO) => {
    try {
      const blob = await downloadReport(jobId, report.id);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = report.file_path.split("/").pop() ?? `report-${report.id.slice(0, 8)}.docx`;
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      /* download errors are non-critical */
    }
  };

  const handleScrollToMissing = () => {
    if (!coverage) return;
    for (const section of coverage.sections) {
      for (const group of section.groups) {
        for (const slot of group.slots) {
          if (slot.status === "missing_required") {
            setScrollToSlotId(slot.slot_id);
            return;
          }
        }
      }
    }
  };

  const handleRerun = useCallback(() => {
    rerunMutation.mutate();
  }, [rerunMutation]);

  const jobReady = AST_READY_STATUS.has(jobQuery.data?.status ?? "");
  const isLoading = jobQuery.isLoading || (jobReady && coverageQuery.isLoading);

  if (isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-8 w-48" />
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
          <Skeleton className="h-64 lg:col-span-1" />
          <Skeleton className="h-64 lg:col-span-2" />
        </div>
      </div>
    );
  }

  const job = jobQuery.data;

  if (job && !AST_READY_STATUS.has(job.status)) {
    const message =
      job.status === "failed"
        ? "该作业抽取失败"
        : "该作业尚未完成抽取";
    return (
      <div className="space-y-4">
        <Button variant="ghost" size="sm" onClick={() => router.back()}>
          <ArrowLeft className="mr-1 h-4 w-4" /> 返回
        </Button>
        <div className="rounded border border-yellow-500/40 bg-yellow-50 px-4 py-3 text-sm text-yellow-800">
          {message}
          {job.status === "failed" && job.error_message && (
            <span className="ml-1 text-muted-foreground">— {job.error_message}</span>
          )}
        </div>
      </div>
    );
  }

  if (is422(coverageQuery.error)) {
    return (
      <div className="space-y-4">
        <Button variant="ghost" size="sm" onClick={() => router.back()}>
          <ArrowLeft className="mr-1 h-4 w-4" /> 返回
        </Button>
        <div className="rounded border border-yellow-500/40 bg-yellow-50 px-4 py-3 text-sm text-yellow-800">
          该文档类型不支持风险评估
        </div>
      </div>
    );
  }

  const anyError = jobQuery.error || coverageQuery.error;
  if (anyError && !coverage) {
    return (
      <div className="space-y-4">
        <Button variant="ghost" size="sm" onClick={() => router.back()}>
          <ArrowLeft className="mr-1 h-4 w-4" /> 返回
        </Button>
        <div className="rounded border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive">
          {String(anyError)}
        </div>
      </div>
    );
  }

  const mutationError = dismissMutation.error || undismissMutation.error || generateMutation.error;

  const missingSlots = coverage ? getMissingSlots(coverage) : [];

  // 覆盖率派生量（completed = 已填充 + 已推断，与 CoverageSummaryCard 口径一致）。
  const completed = coverage ? coverage.filled + coverage.inferred : 0;
  const totalSlots = coverage?.total_slots ?? 0;
  const missing = coverage?.missing_required ?? 0;
  const dismissed = coverage?.dismissed ?? 0;
  const hasReport = reports.length > 0 || generateMutation.isSuccess;
  const generating = generateMutation.isPending;

  const steps: GenStep[] = coverage
    ? [
        {
          title: "数据抽取解析",
          desc: `已完成 · 抽取 ${job?.total_candidates ?? 0} 个候选，${job?.approved_count ?? 0} 条已确认`,
          state: "done",
        },
        {
          title: "模板匹配",
          desc: coverage.template_name
            ? `已完成 · 命中模板「${coverage.template_name}${coverage.template_version ? ` ${coverage.template_version}` : ""}」`
            : "已完成 · 使用默认模板",
          state: "done",
        },
        {
          title: "覆盖率分析",
          desc: `已完成 · ${completed}/${totalSlots} 插槽已填充${missing > 0 ? `，${missing} 项必填缺失` : ""}`,
          state: "done",
          detail: (
            <div className="mt-1 space-y-1.5 rounded-lg border bg-muted p-3">
              <div className="flex justify-between text-xs">
                <span className="text-muted-foreground">已填充插槽</span>
                <span className="font-semibold text-foreground">
                  {completed} / {totalSlots}
                </span>
              </div>
              <div className="flex justify-between text-xs">
                <span className="text-muted-foreground">缺失必填项</span>
                <span
                  className={cn(
                    "font-semibold",
                    missing > 0 ? "text-destructive" : "text-foreground",
                  )}
                >
                  {missing}
                </span>
              </div>
              <div className="flex justify-between text-xs">
                <span className="text-muted-foreground">已忽略</span>
                <span className="font-semibold text-foreground">{dismissed}</span>
              </div>
            </div>
          ),
        },
        {
          title: "AI 行文生成",
          desc: hasReport
            ? "已完成 · 依行文 Prompt 融合插槽值生成叙述"
            : generating
              ? "生成中 · 依行文 Prompt 融合插槽值生成叙述"
              : "等待中 · 依行文 Prompt 融合插槽值生成叙述",
          state: hasReport ? "done" : generating ? "active" : "pending",
        },
        {
          title: "DOCX 报告渲染",
          desc: hasReport
            ? "已完成 · 已生成最终 Word 文档"
            : generating
              ? "生成中 · 生成最终 Word 文档"
              : "等待中 · 生成最终 Word 文档",
          state: hasReport ? "done" : generating ? "active" : "pending",
        },
      ]
    : [];

  const doneSteps = steps.filter((s) => s.state === "done").length;
  const genPct = steps.length ? Math.round((doneSteps / steps.length) * 100) : 0;
  const latestReport = reports[0];

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Button variant="ghost" size="sm" onClick={() => router.back()}>
            <ArrowLeft className="mr-1 h-4 w-4" /> 返回
          </Button>
          <h1 className="text-lg font-semibold">AST 覆盖率</h1>
          <span className="text-sm text-muted-foreground">Job {jobId.slice(0, 8)}</span>
          {templates.length > 1 && (
            <Select
              value={selectedTemplateId ?? "__auto__"}
              onValueChange={(v) => {
                setSelectedTemplateId(v === "__auto__" ? undefined : v);
                setSelectedSlot(null);
              }}
            >
              <SelectTrigger className="h-8 w-48 text-xs">
                <SelectValue placeholder="自动匹配模板" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="__auto__">自动匹配</SelectItem>
                {templates.map((t) => (
                  <SelectItem key={t.id} value={t.id}>
                    {t.name} ({t.version})
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
          {coverage?.template_name && (
            <span className="text-xs text-muted-foreground">
              {coverage.template_name}
              {coverage.template_version ? ` ${coverage.template_version}` : ""}
            </span>
          )}
        </div>
        <Button onClick={handleGenerateReport} disabled={generateMutation.isPending}>
          {generateMutation.isPending ? (
            <Loader2 className="mr-1 h-4 w-4 animate-spin" />
          ) : (
            <FileDown className="mr-1 h-4 w-4" />
          )}
          {generateMutation.isPending ? "生成中..." : "生成报告"}
        </Button>
      </div>

      {mutationError && (
        <div className="rounded border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
          {String(mutationError)}
        </div>
      )}

      {coverage && (
        <Tabs defaultValue="coverage">
          <TabsList>
            <TabsTrigger value="coverage">覆盖率</TabsTrigger>
            <TabsTrigger value="history">
              历史报告{reports.length > 0 && ` (${reports.length})`}
            </TabsTrigger>
          </TabsList>

          <TabsContent value="coverage" className="mt-4">
            <div className="flex flex-col gap-4">
              <div className="grid grid-cols-1 overflow-hidden rounded-lg border bg-card lg:grid-cols-[1fr_480px]">
                {/* 左：生成进度 */}
                <div className="flex flex-col gap-5 p-6 lg:border-r">
                  <div className="space-y-2">
                    <div className="flex items-center justify-between">
                      <span className="text-[15px] font-semibold text-foreground">
                        生成进度
                      </span>
                      <span className="text-sm font-semibold text-primary">
                        {genPct}%
                      </span>
                    </div>
                    <Progress value={genPct} className="h-2" />
                  </div>
                  <div className="flex flex-col">
                    {steps.map((s, i) => (
                      <StepRow
                        key={s.title}
                        step={s}
                        last={i === steps.length - 1}
                      />
                    ))}
                  </div>
                </div>

                {/* 右：报告结构 */}
                <div className="flex min-h-0 flex-col">
                  <div className="flex items-center justify-between border-b px-5 py-4">
                    <div className="flex items-center gap-2">
                      <ListTree className="size-4 text-foreground" />
                      <span className="text-sm font-semibold text-foreground">
                        报告结构
                      </span>
                    </div>
                    <div className="flex items-center gap-1.5">
                      <CoverageBadge tone="success" count={completed} />
                      <CoverageBadge
                        tone="destructive"
                        count={missing}
                        onClick={missing > 0 ? handleScrollToMissing : undefined}
                      />
                      <CoverageBadge tone="muted" count={dismissed} />
                    </div>
                  </div>

                  <div className="max-h-[52vh] min-h-0 flex-1 overflow-y-auto px-4 py-3">
                    <ASTTreeView
                      coverage={coverage}
                      selectedSlotId={selectedSlot?.slot_id}
                      onSelectSlot={setSelectedSlot}
                      scrollToSlotId={scrollToSlotId}
                    />
                  </div>

                  {selectedSlot && (
                    <div className="max-h-[40vh] overflow-y-auto border-t px-4 py-3">
                      <SlotDetailPanel
                        slot={selectedSlot}
                        onClickSourceRef={setHighlightRef}
                        actionBar={
                          <SlotActionBar
                            slot={selectedSlot}
                            onDismiss={(id) => dismissMutation.mutate(id)}
                            onUndismiss={(id) => undismissMutation.mutate(id)}
                            dismissing={
                              dismissMutation.isPending || undismissMutation.isPending
                            }
                            onRerun={handleRerun}
                            rerunning={rerunMutation.isPending}
                          />
                        }
                      />
                    </div>
                  )}

                  <div className="flex items-center justify-between border-t px-5 py-3">
                    <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                      <Info className="size-3.5" />
                      <span>生成完成后可下载 DOCX 报告</span>
                    </div>
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={!hasReport || !latestReport}
                      onClick={() => latestReport && handleDownload(latestReport)}
                    >
                      <Download className="mr-1 size-3.5" />
                      下载报告
                    </Button>
                  </div>
                </div>
              </div>

              {/* 源文档（全宽，可用时显示，供 evidence 高亮联动） */}
              {docContent && (
                <Card>
                  <CardHeader className="pb-2">
                    <CardTitle className="text-sm">源文档</CardTitle>
                  </CardHeader>
                  <CardContent className="max-h-[50vh] overflow-y-auto">
                    <WordViewer content={docContent} highlightRef={highlightRef} />
                  </CardContent>
                </Card>
              )}
            </div>
          </TabsContent>

          <TabsContent value="history" className="mt-4">
            <Card>
              <CardHeader>
                <CardTitle className="text-base">报告历史</CardTitle>
              </CardHeader>
              <CardContent>
                <ReportHistoryList reports={reports} onDownload={handleDownload} />
              </CardContent>
            </Card>
          </TabsContent>
        </Tabs>
      )}

      {/* Pre-check confirmation dialog */}
      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>覆盖率不完整</DialogTitle>
          </DialogHeader>
          <p className="text-sm">
            当前仍有 <strong>{coverage?.missing_required ?? 0}</strong> 个必填槽位缺失。
            生成的报告中对应部分将标注为「信息缺失」。
          </p>
          {missingSlots.length > 0 && (
            <ul className="max-h-40 overflow-y-auto rounded border px-3 py-2 text-xs text-muted-foreground">
              {missingSlots.map((s) => (
                <li key={s.slot_id} className="py-0.5">
                  <span className="font-mono">{s.slot_id}</span>
                  {s.label && <span className="ml-1">— {s.label}</span>}
                </li>
              ))}
            </ul>
          )}
          <p className="text-sm text-muted-foreground">
            您可以先标记不适用的槽位，或选择继续生成。
          </p>
          <div className="flex justify-end gap-2 pt-2">
            <Button variant="outline" onClick={() => setConfirmOpen(false)}>
              取消
            </Button>
            <Button onClick={doGenerate} disabled={generateMutation.isPending}>
              {generateMutation.isPending ? "生成中..." : "仍然生成"}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
