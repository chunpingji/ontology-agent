"use client";

import { useParams, useRouter } from "next/navigation";
import { useCallback, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, FileDown, Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

import { ASTTreeView } from "@/components/extraction/ast-tree-view";
import { CoverageSummaryCard } from "@/components/extraction/coverage-summary-card";
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
  type AstTemplateDTO,
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
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
              {/* Left: Summary + Tree */}
              <div className="space-y-4 lg:col-span-1">
                <CoverageSummaryCard
                  coverage={coverage}
                  onScrollToMissing={handleScrollToMissing}
                />
                <Card>
                  <CardHeader className="pb-2">
                    <CardTitle className="text-sm">模板结构</CardTitle>
                  </CardHeader>
                  <CardContent className="max-h-[60vh] overflow-y-auto">
                    <ASTTreeView
                      coverage={coverage}
                      selectedSlotId={selectedSlot?.slot_id}
                      onSelectSlot={setSelectedSlot}
                      scrollToSlotId={scrollToSlotId}
                    />
                  </CardContent>
                </Card>
              </div>

              {/* Right: Detail + Document */}
              <div className="space-y-4 lg:col-span-2">
                {selectedSlot ? (
                  <Card>
                    <CardHeader className="pb-2">
                      <CardTitle className="text-sm">槽位详情</CardTitle>
                    </CardHeader>
                    <CardContent>
                      <SlotDetailPanel
                        slot={selectedSlot}
                        onClickSourceRef={setHighlightRef}
                        actionBar={
                          <SlotActionBar
                            slot={selectedSlot}
                            onDismiss={(id) => dismissMutation.mutate(id)}
                            onUndismiss={(id) => undismissMutation.mutate(id)}
                            dismissing={dismissMutation.isPending || undismissMutation.isPending}
                            onRerun={handleRerun}
                            rerunning={rerunMutation.isPending}
                          />
                        }
                      />
                    </CardContent>
                  </Card>
                ) : (
                  <Card>
                    <CardContent className="py-8 text-center text-sm text-muted-foreground">
                      点击左侧槽位查看详情
                    </CardContent>
                  </Card>
                )}

                {docContent && (
                  <Card>
                    <CardHeader className="pb-2">
                      <CardTitle className="text-sm">源文档</CardTitle>
                    </CardHeader>
                    <CardContent className="max-h-[50vh] overflow-y-auto">
                      <WordViewer
                        content={docContent}
                        highlightRef={highlightRef}
                      />
                    </CardContent>
                  </Card>
                )}
              </div>
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
            生成的报告中对应部分将标注为"信息缺失"。
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
