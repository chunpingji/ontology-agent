"use client";

import { useEffect, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Check,
  ChevronDown,
  ClipboardCheck,
  Download,
  Eye,
  Loader2,
  RotateCcw,
  ShieldCheck,
  Sparkles,
  TriangleAlert,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@/components/ui/dialog";
import { Progress } from "@/components/ui/progress";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  downloadReportById,
  generateRiskReport,
  getAnnotatedDocument,
  listReports,
  pollReportStatus,
  resolveDocumentJobId,
  type GeneratedReportDTO,
  type ReportOrDocument,
} from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * 文档预览页「操作」弹出菜单（报告中心详情页 · 右上角，紧邻分享按钮）。
 *
 * 三项 AI / 合规操作，按设计稿还原：
 *   · AI 分析——智能提取文档关键信息（占位：拟接 rerunAnnotation）
 *   · 生成风险评估报告——**已接线**「通过模板生成报告」：解析文档关联的抽取 jobId →
 *     后端按文档类别 resolve_template → 模板分节渲染；异步路径在 Drawer 中展示
 *     真实阶段进度，完成后提供预览与下载。
 *   · 审计——合规性审查与追踪（占位：拟接合规审计链）
 *
 * AI 分析 / 审计仍为占位：点击给出「即将上线」轻提示，后端接入留待后续。
 */
const RISK_KEY = "risk";

const ACTIONS = [
  {
    key: RISK_KEY,
    label: "生成风险评估报告",
    desc: "基于本体评估潜在风险",
    Icon: ShieldCheck,
    tint: "bg-amber-500/10 text-amber-600 dark:text-amber-500",
  },
  {
    key: "audit",
    label: "审计",
    desc: "合规性审查与追踪",
    Icon: ClipboardCheck,
    tint: "bg-blue-500/10 text-blue-600 dark:text-blue-500",
  },
] as const;

type Notice = { tone: "info" | "success" | "error"; text: string };

type DataPreparation = {
  status: "existing" | "refreshing" | "refreshed" | "error";
  relationshipCount?: number;
  error?: string;
};

type AssessmentSubstep = {
  key: string;
  label: string;
  status: "pending" | "running" | "completed";
  result?: string | null;
  items?: Array<{ label: string; detail?: string | null }>;
};

type ReportProgress = {
  stage?: string;
  percent?: number;
  detail?: string;
  substeps?: AssessmentSubstep[];
};

type PdeConflictSnapshot = {
  conflict_key?: string;
  summary?: string;
  effective?: {
    source_label?: string;
    pde_mg_day?: number | string | null;
    band?: number | string | null;
  } | null;
};

function hasPdeConflict(report: GeneratedReportDTO | null | undefined): boolean {
  const summary = report?.rules_summary as { pde_conflicts?: unknown } | null | undefined;
  return Array.isArray(summary?.pde_conflicts) && summary.pde_conflicts.length > 0;
}

const TONE_STYLES: Record<Notice["tone"], { className: string; Icon: typeof Sparkles }> = {
  info: { className: "text-popover-foreground", Icon: Sparkles },
  success: { className: "text-emerald-600 dark:text-emerald-500", Icon: ShieldCheck },
  error: { className: "text-destructive", Icon: TriangleAlert },
};

/** 把 `fetchAPI` 抛出的 `API 4xx: {"detail":"…"}` 提炼成可读的中文提示。 */
function friendlyError(error: unknown): string {
  const message = error instanceof Error ? error.message : String(error);
  const apiBody = message.startsWith("API ") && message.includes(":")
    ? message.slice(message.indexOf(":") + 1).trim() : null;
  if (apiBody) {
    try {
      const body = JSON.parse(apiBody);
      if (body && typeof body.detail === "string") return body.detail;
    } catch {
      /* 非 JSON 响应体——回退到原始文本 */
    }
  }
  return message;
}

function PseudoStreamingText({ text, animate }: { text: string; animate: boolean }) {
  if (!animate) {
    return <p className="mt-1 whitespace-pre-wrap leading-5 text-muted-foreground">{text}</p>;
  }
  return <AnimatedNarrativeText key={text} text={text} />;
}

function AnimatedNarrativeText({ text }: { text: string }) {
  const [visibleLength, setVisibleLength] = useState(0);

  useEffect(() => {
    const timer = window.setInterval(() => {
      setVisibleLength((length) => {
        if (length >= text.length) {
          window.clearInterval(timer);
          return text.length;
        }
        return Math.min(text.length, length + 4);
      });
    }, 24);
    return () => window.clearInterval(timer);
  }, [text]);

  return (
    <p className="mt-1 whitespace-pre-wrap leading-5 text-muted-foreground">
      {text.slice(0, visibleLength)}
      {visibleLength < text.length ? (
        <span className="ml-0.5 inline-block h-3.5 w-0.5 animate-pulse bg-primary align-middle" />
      ) : null}
    </p>
  );
}

export function DocumentActionsMenu({ item }: { item: ReportOrDocument }) {
  const queryClient = useQueryClient();
  // 瞬时提示（info/success/error）：自动消隐。生成过程的「生成中」态由 mutation 的
  // pending 独立驱动（常驻至落定），二者互斥渲染。
  const [notice, setNotice] = useState<Notice | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [jobId, setJobId] = useState<string | null>(null);
  const [reportId, setReportId] = useState<string | null>(null);
  const [report, setReport] = useState<(GeneratedReportDTO & { report_status?: string }) | null>(null);
  const [syncBlob, setSyncBlob] = useState<Blob | null>(null);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [assessmentExpanded, setAssessmentExpanded] = useState(false);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [previewBlob, setPreviewBlob] = useState<Blob | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [generationPlanActive, setGenerationPlanActive] = useState(false);
  const [dataPreparation, setDataPreparation] = useState<DataPreparation>({ status: "existing" });
  const previewContainerRef = useRef<HTMLDivElement>(null);
  // 从 DropdownMenu 进入 Drawer 时，等待菜单的 FocusScope 完成关闭后再打开。
  // 避免两个 Radix 浮层短暂重叠，导致 body pointer-events 锁的恢复顺序错乱。
  const pendingRiskDrawerRef = useRef(false);

  useEffect(() => {
    if (!notice) return;
    const timer = setTimeout(() => setNotice(null), notice.tone === "error" ? 4000 : 2500);
    return () => clearTimeout(timer);
  }, [notice]);

  useEffect(() => {
    if (!previewOpen || !previewBlob || !previewContainerRef.current) return;
    let cancelled = false;
    const container = previewContainerRef.current;
    container.replaceChildren();

    void import("docx-preview")
      .then(({ renderAsync }) => renderAsync(previewBlob, container, undefined, {
        className: "risk-report-docx",
        inWrapper: true,
        ignoreWidth: false,
        ignoreHeight: false,
        ignoreFonts: false,
        breakPages: true,
        ignoreLastRenderedPageBreak: false,
        useBase64URL: true,
        renderHeaders: true,
        renderFooters: true,
        renderFootnotes: true,
        renderEndnotes: true,
      }))
      .then(() => {
        if (!cancelled) setPreviewLoading(false);
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setPreviewLoading(false);
        setPreviewError(`Word 文档预览失败：${friendlyError(error)}`);
      });

    return () => {
      cancelled = true;
      container.replaceChildren();
    };
  }, [previewBlob, previewOpen]);

  const generate = useMutation({
    mutationFn: async () => {
      const iri = item.kind === "uploaded-document" ? item.iri : undefined;
      if (!iri) throw new Error("仅支持对上传文档生成风险评估报告");
      const resolvedJobId = jobId ?? await resolveDocumentJobId(iri);
      if (!resolvedJobId) throw new Error("该文档未关联抽取任务，无法生成报告");
      setJobId(resolvedJobId);
      const response = await generateRiskReport(resolvedJobId);
      if (response instanceof Blob) {
        setSyncBlob(response);
        // 同步路径也读取刚持久化的报告详情，恢复完成态的评估子步骤和冲突留痕。
        // 详情读取失败不影响已经成功返回的 Word 下载结果。
        try {
          const reports = await listReports(resolvedJobId);
          const latest = reports.find((entry) => entry.report_type === "risk_assessment");
          if (latest) {
            setReportId(latest.id);
            const detail = await pollReportStatus(resolvedJobId, latest.id);
            setReport(detail);
            if (hasPdeConflict(detail)) setAssessmentExpanded(true);
          }
        } catch {
          /* 保留 syncBlob 完成态；用户仍可预览/下载本次生成文件。 */
        }
        return;
      }
      setReportId(response.report_id);
      for (let attempt = 0; attempt < 120; attempt++) {
        await new Promise((resolve) => setTimeout(resolve, 1500));
        const next = await pollReportStatus(resolvedJobId, response.report_id);
        setReport(next);
        const nextProgress = (next.rules_summary as { _progress?: ReportProgress } | null)?._progress;
        if (nextProgress?.stage === "assess" || hasPdeConflict(next)) {
          setAssessmentExpanded(true);
        }
        if (next.report_status === "completed") return;
        if (next.report_status === "failed") throw new Error(next.report_error || "报告生成失败");
      }
      throw new Error("报告生成超时，请稍后重试");
    },
    onSuccess: () => {
      setNotice({ tone: "success", text: "风险评估报告已生成，可预览或下载" });
      // 新报告随后出现在报告中心列表——失效缓存，返回列表时自动刷新出来。
      queryClient.invalidateQueries({ queryKey: ["report-center"] });
    },
    onError: (error) => setNotice({ tone: "error", text: friendlyError(error) }),
  });

  const refreshData = useMutation({
    mutationFn: async () => {
      const iri = item.kind === "uploaded-document" ? item.iri : undefined;
      if (!iri) throw new Error("仅支持对上传文档重新读取与校验数据");
      const resolvedJobId = jobId ?? await resolveDocumentJobId(iri);
      if (!resolvedJobId) throw new Error("该文档未关联抽取任务，无法重新读取数据");
      setJobId(resolvedJobId);

      const annotated = await getAnnotatedDocument(resolvedJobId, true);
      const docClassIri = annotated.doc_class?.doc_class_iri ?? "";
      if (!docClassIri.includes("CMCReport")) {
        throw new Error("重新读取的数据未识别为 CMCReport，请先检查文档分类");
      }
      const relationshipCount = annotated.relationships?.length ?? 0;
      if (relationshipCount === 0) {
        throw new Error("重新读取完成，但未识别到关系图谱数据，请检查文档内容或抽取配置");
      }
      return relationshipCount;
    },
    onMutate: () => setDataPreparation({ status: "refreshing" }),
    onSuccess: (relationshipCount) => {
      setDataPreparation({ status: "refreshed", relationshipCount });
    },
    onError: (error) => {
      setDataPreparation({ status: "error", error: friendlyError(error) });
    },
  });

  const busy = generate.isPending;

  const openRiskDrawer = async () => {
    setDrawerOpen(true);
    // 本组件内已有生成结果或任务在跑时直接恢复现场；页面刷新后的首次进入才查历史。
    if (busy || report || syncBlob || historyLoading || generationPlanActive) return;

    const iri = item.kind === "uploaded-document" ? item.iri : undefined;
    if (!iri) return;
    setHistoryLoading(true);
    try {
      const resolvedJobId = jobId ?? await resolveDocumentJobId(iri);
      if (!resolvedJobId) return;
      setJobId(resolvedJobId);
      const reports = await listReports(resolvedJobId);
      const latest = reports.find((entry) => entry.report_type === "risk_assessment");
      if (!latest) {
        setGenerationPlanActive(true);
        return;
      }
      // 列表响应不含在线预览 narratives，继续读取详情以恢复完整的上次结果。
      const detail = await pollReportStatus(resolvedJobId, latest.id);
      setReportId(latest.id);
      setReport(detail);
      if (hasPdeConflict(detail)) setAssessmentExpanded(true);
    } catch (error) {
      setNotice({ tone: "error", text: `读取上次报告失败：${friendlyError(error)}` });
    } finally {
      setHistoryLoading(false);
    }
  };

  const handleSelect = (key: string, label: string) => {
    if (key === RISK_KEY) {
      pendingRiskDrawerRef.current = true;
      setMenuOpen(false);
      return;
    }
    setNotice({ tone: "info", text: `「${label}」功能即将上线` });
  };

  const enterGenerationPlan = () => {
    setGenerationPlanActive(true);
    setReport(null);
    setReportId(null);
    setSyncBlob(null);
    setNotice(null);
    setAssessmentExpanded(false);
    setPreviewOpen(false);
    setPreviewBlob(null);
    setPreviewError(null);
    generate.reset();
    refreshData.reset();
    setDataPreparation({ status: "existing" });
  };

  const startGeneration = () => {
    setGenerationPlanActive(false);
    setReport(null);
    setReportId(null);
    setSyncBlob(null);
    setNotice(null);
    setAssessmentExpanded(false);
    setPreviewOpen(false);
    setPreviewBlob(null);
    setPreviewError(null);
    generate.reset();
    generate.mutate();
  };

  const preview = async () => {
    setPreviewOpen(true);
    setPreviewLoading(true);
    setPreviewError(null);
    try {
      const blob = syncBlob ?? (jobId && reportId ? await downloadReportById(jobId, reportId) : null);
      if (!blob) throw new Error("未找到可预览的报告文件");
      setPreviewBlob(blob);
    } catch (error) {
      setPreviewLoading(false);
      setPreviewError(friendlyError(error));
    }
  };

  const download = async () => {
    try {
      const blob = syncBlob ?? (jobId && reportId ? await downloadReportById(jobId, reportId) : null);
      if (!blob) return;
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `风险评估表_${(item.title?.toLowerCase().endsWith(".docx") ? item.title.slice(0, -5) : item.title || "report")}.docx`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (error) {
      setNotice({ tone: "error", text: friendlyError(error) });
    }
  };

  const rulesSummary = report?.rules_summary as
    | {
        _progress?: ReportProgress;
        rows?: Array<Record<string, unknown>>;
        coverage?: {
          total_slots?: number;
          missing_required?: number;
          slots?: Array<{ label?: string; status?: string; value?: string | null; note?: string | null }>;
        };
        pde_conflicts?: PdeConflictSnapshot[];
      }
    | undefined;
  const progress = rulesSummary?._progress;
  const completed = Boolean(syncBlob || report?.report_status === "completed");
  const planning = !historyLoading && !busy && !completed && !generate.isError;
  const pdeConflicts = Array.isArray(rulesSummary?.pde_conflicts)
    ? rulesSummary.pde_conflicts
    : [];
  const pendingConflictCount = pdeConflicts.filter((conflict) => !conflict.effective).length;
  const rawAssessmentSubsteps: AssessmentSubstep[] = progress?.substeps?.length
    ? progress.substeps
    : completed && report
      ? [
          { key: "facts", label: "构建评估事实", status: "completed", result: "评估事实已构建" },
          {
            key: "rules",
            label: "执行风险规则",
            status: "completed",
            result: pendingConflictCount
              ? `${rulesSummary?.rows?.length ?? 0} 项 · ${pendingConflictCount} 项待评估`
              : `${rulesSummary?.rows?.length ?? 0} 项 · 已输出评估结果`,
            items: rulesSummary?.rows?.map((row) => ({
              label: String(row.hazid ?? "风险规则"),
              detail: `控制前 ${String(row.pre ?? "-")} · 控制后 ${String(row.post ?? "-")}`,
            })),
          },
          {
            key: "coverage",
            label: "校验信息完整性",
            status: "completed",
            result: rulesSummary?.coverage?.missing_required
              ? `${rulesSummary.coverage.total_slots ?? 0} 项 · ${rulesSummary.coverage.missing_required} 项缺失`
              : `${rulesSummary?.coverage?.total_slots ?? 0} 项 · 信息完整`,
            items: rulesSummary?.coverage?.slots?.map((slot) => ({
              label: slot.label ?? "信息项",
              detail: `${slot.status ?? "未知"} · ${slot.value ?? slot.note ?? "无值"}`,
            })),
          },
          {
            key: "narratives",
            label: "生成章节行文",
            status: "completed",
            result: report.narratives?.sections?.length
              ? `${report.narratives.sections.length} 个章节`
              : "无待生成章节",
          },
        ]
      : [];
  const persistedNarrativeItems = report?.narratives?.sections?.map((section) => ({
    label: section.title || "章节行文",
    detail: section.text,
  })) ?? [];
  const assessmentSubsteps = rawAssessmentSubsteps.map((substep) =>
    substep.key === "narratives" && !substep.items?.length && persistedNarrativeItems.length
      ? { ...substep, items: persistedNarrativeItems }
      : substep,
  );

  const stages = [
    ["prepare", "读取与校验数据"],
    ["template", "匹配报告模板"],
    ["assess", "风险识别与评估"],
    ["render", "生成报告文档"],
    ["finalize", "保存生成结果"],
  ] as const;
  const currentIndex = completed ? stages.length : Math.max(0, stages.findIndex(([key]) => key === progress?.stage));

  return (
    <Sheet open={drawerOpen} onOpenChange={setDrawerOpen}>
      {busy ? (
        <SheetTrigger asChild>
          <Button type="button">
            <Loader2 className="animate-spin" />
            查看生成进度
          </Button>
        </SheetTrigger>
      ) : (
        <DropdownMenu open={menuOpen} onOpenChange={setMenuOpen} modal={false}>
          <DropdownMenuTrigger asChild>
            <Button>
              <Sparkles />
              操作
              <ChevronDown />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent
            align="end"
            className="w-64"
            onCloseAutoFocus={(event) => {
              if (!pendingRiskDrawerRef.current) return;
              event.preventDefault();
              pendingRiskDrawerRef.current = false;
              void openRiskDrawer();
            }}
          >
            {ACTIONS.map(({ key, label, desc, Icon, tint }) => (
              <DropdownMenuItem
                key={key}
                className="items-start gap-3 py-2.5"
                onSelect={() => handleSelect(key, label)}
              >
                <span
                  className={cn(
                    "mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-md [&_svg]:size-4",
                    tint,
                  )}
                >
                  <Icon />
                </span>
                <div className="min-w-0 space-y-0.5">
                  <p className="text-sm font-medium leading-none text-foreground">{label}</p>
                  <p className="text-xs leading-snug text-muted-foreground">{desc}</p>
                </div>
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>
      )}

      <SheetContent className="gap-0 p-0 sm:max-w-xl">
          <SheetHeader className="border-b px-6 py-5 pr-12">
            <SheetTitle className="flex items-center gap-2"><ShieldCheck className="size-5 text-primary" />风险评估报告</SheetTitle>
            <SheetDescription>报告生成任务可在后台继续，完成后可在线预览并下载。</SheetDescription>
          </SheetHeader>
          <div className="flex-1 overflow-y-auto px-6 py-6">
            <div className="mb-7 flex items-center justify-between">
              <div>
                <p className="text-sm font-medium">{historyLoading ? "正在读取已生成报告" : completed ? "生成完成" : generate.isError ? "生成失败" : busy ? "正在生成" : "生成计划"}</p>
                <p className="mt-1 text-xs text-muted-foreground">{progress?.detail ?? (historyLoading ? "正在检查该文档的历史报告…" : completed ? "报告已完成，可预览或下载" : planning ? "确认以下步骤后，点击“开始生成”创建报告" : "正在创建报告生成任务…")}</p>
              </div>
              {!historyLoading && <span className="text-sm font-semibold text-primary">{completed ? 100 : progress?.percent ?? (busy ? 5 : 0)}%</span>}
            </div>
            <Progress value={completed ? 100 : progress?.percent ?? (busy ? 5 : 0)} className={cn("mb-8 h-2", historyLoading && "animate-pulse")} />

            <div className="space-y-1">
              {stages.map(([key, label], index) => {
                const done = completed || index < currentIndex;
                const active = !completed && index === currentIndex && busy;
                const isDataPreparation = key === "prepare";
                const isAssessment = key === "assess";
                const hasAssessmentTrace = isAssessment
                  && (assessmentSubsteps.length > 0 || pdeConflicts.length > 0);
                const showAssessmentTrace = hasAssessmentTrace
                  && assessmentExpanded;
                return <div key={key} className={cn("flex gap-3 rounded-lg px-3 py-3", active && "bg-primary/5")}>
                  <span className={cn("mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-full border text-xs", done && "border-emerald-500 bg-emerald-500 text-white", active && "border-primary text-primary")}>
                    {done ? <Check className="size-3.5" /> : active ? <Loader2 className="size-3.5 animate-spin" /> : index + 1}
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex min-h-6 items-center gap-2">
                      <p className={cn("text-sm font-medium", !done && !active && "text-muted-foreground")}>{label}</p>
                      {isAssessment && pdeConflicts.length > 0 ? (
                        <span className={cn(
                          "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium",
                          pendingConflictCount
                            ? "bg-destructive/10 text-destructive"
                            : "bg-amber-500/10 text-amber-700 dark:text-amber-400",
                        )}>
                          <TriangleAlert className="size-3" />
                          {pdeConflicts.length} 项冲突
                          {pendingConflictCount ? ` · ${pendingConflictCount} 项待裁决` : " · 已裁决"}
                        </span>
                      ) : null}
                      {hasAssessmentTrace ? (
                        <button
                          type="button"
                          className="ml-auto inline-flex items-center gap-1 rounded px-1.5 py-1 text-[11px] text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                          aria-label={showAssessmentTrace ? "收起风险识别与评估详情" : "展开风险识别与评估详情"}
                          aria-expanded={showAssessmentTrace}
                          onClick={() => setAssessmentExpanded((expanded) => !expanded)}
                        >
                          <span>{showAssessmentTrace ? "收起留痕" : "查看留痕"}</span>
                          <ChevronDown className={cn("size-4 transition-transform", showAssessmentTrace && "rotate-180")} />
                        </button>
                      ) : null}
                    </div>
                    {active && <p className="mt-1 text-xs text-muted-foreground">{progress?.detail}</p>}
                    {planning && isDataPreparation ? (
                      <div className="mt-2 flex items-start justify-between gap-3 rounded-md border bg-muted/30 px-3 py-2.5">
                        <p className={cn(
                          "min-w-0 pt-1 text-xs leading-5 text-muted-foreground",
                          dataPreparation.status === "error" && "text-destructive",
                          dataPreparation.status === "refreshed" && "text-emerald-600 dark:text-emerald-500",
                        )}>
                          {dataPreparation.status === "refreshing"
                            ? "正在重新读取源文档并校验关系图谱…"
                            : dataPreparation.status === "refreshed"
                              ? `已重新读取并校验 · ${dataPreparation.relationshipCount ?? 0} 条关系`
                              : dataPreparation.status === "error"
                                ? `重新读取与校验失败：${dataPreparation.error ?? "未知错误"}`
                                : "默认复用现有关系图谱数据；如源文档或抽取配置已变化，可在生成前重新读取。"}
                        </p>
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          className="shrink-0"
                          disabled={refreshData.isPending}
                          onClick={() => refreshData.mutate()}
                        >
                          {refreshData.isPending ? (
                            <Loader2 className="animate-spin" />
                          ) : (
                            <RotateCcw />
                          )}
                          {refreshData.isPending
                            ? "读取与校验中"
                            : dataPreparation.status === "error"
                              ? "重试读取与校验"
                              : dataPreparation.status === "refreshed"
                                ? "再次读取与校验"
                                : "重新读取与校验"}
                        </Button>
                      </div>
                    ) : null}
                    {showAssessmentTrace ? (
                      <div className="mt-3 space-y-1 border-l border-border pl-3">
                        <Accordion type="multiple" defaultValue={["narratives"]} className="w-full">
                        {assessmentSubsteps.map((substep) => substep.key === "narratives" ? (
                          <AccordionItem key={substep.key} value={substep.key} className="border-b-0">
                          <AccordionTrigger className="min-h-8 gap-2 rounded-md px-2 py-1.5 text-xs hover:bg-muted/60 hover:no-underline">
                          <span className="flex min-w-0 flex-1 items-center gap-2 text-left">
                            <span className={cn(
                              "flex size-4 shrink-0 items-center justify-center rounded-full border",
                              substep.status === "completed" && "border-emerald-500 bg-emerald-500 text-white",
                              substep.status === "running" && "border-primary text-primary",
                            )}>
                              {substep.status === "completed" ? <Check className="size-2.5" /> : substep.status === "running" ? <Loader2 className="size-2.5 animate-spin" /> : <span className="size-1 rounded-full bg-muted-foreground/40" />}
                            </span>
                            <span className="font-medium text-foreground">{substep.label}</span>
                            <span className={cn(
                              "ml-auto text-right text-muted-foreground",
                              substep.status === "running" && "text-primary",
                              substep.status === "completed" && "text-foreground",
                            )}>
                              {substep.status === "pending" ? "等待执行" : substep.status === "running" ? "评估中" : `已完成 · ${substep.result ?? "已输出结果"}`}
                            </span>
                          </span>
                          </AccordionTrigger>
                          <AccordionContent className="pb-1 pl-8 pr-2">
                            {substep.items?.length ? (
                              <div className="max-h-80 space-y-2 overflow-y-auto rounded-md bg-muted/40 p-2">
                                {substep.items.map((item, itemIndex) => (
                                  <div key={`${substep.key}-${itemIndex}`} className="border-b border-border/60 px-1 py-2 last:border-0">
                                    <p className="font-medium text-foreground">{item.label}</p>
                                    {item.detail ? (
                                      <PseudoStreamingText
                                        text={item.detail}
                                        animate={busy && substep.status === "running"}
                                      />
                                    ) : null}
                                  </div>
                                ))}
                              </div>
                            ) : (
                              <p className="rounded-md bg-muted/40 px-3 py-2 text-muted-foreground">章节生成完成后将在这里逐字显示</p>
                            )}
                          </AccordionContent>
                          </AccordionItem>
                        ) : (
                          <AccordionItem key={substep.key} value={substep.key} className="border-b-0">
                            <AccordionTrigger className="min-h-8 gap-2 rounded-md px-2 py-1.5 text-xs hover:bg-muted/60 hover:no-underline">
                              <span className="flex min-w-0 flex-1 items-center gap-2 text-left">
                                <span className={cn(
                                  "flex size-4 shrink-0 items-center justify-center rounded-full border",
                                  substep.status === "completed" && "border-emerald-500 bg-emerald-500 text-white",
                                  substep.status === "running" && "border-primary text-primary",
                                )}>
                                  {substep.status === "completed" ? <Check className="size-2.5" /> : substep.status === "running" ? <Loader2 className="size-2.5 animate-spin" /> : <span className="size-1 rounded-full bg-muted-foreground/40" />}
                                </span>
                                <span className="font-medium text-foreground">{substep.label}</span>
                                <span className={cn("ml-auto truncate text-muted-foreground", substep.status === "running" && "text-primary", substep.status === "completed" && "text-foreground")}>
                                  {substep.status === "pending" ? "等待执行" : substep.status === "running" ? "评估中" : substep.result ?? "已输出结果"}
                                </span>
                              </span>
                            </AccordionTrigger>
                            <AccordionContent className="pb-1 pl-8 pr-2">
                              {substep.items?.length ? (
                                <div className="max-h-56 space-y-1 overflow-y-auto rounded-md bg-muted/40 p-2">
                                  {substep.items.map((item, itemIndex) => {
                                    const isApsWarning = item.label === "APS排期告警";
                                    return (
                                      <div
                                        key={`${substep.key}-${itemIndex}`}
                                        className={cn(
                                          "border-b border-border/60 px-1 py-2 last:border-0",
                                          isApsWarning && "text-destructive",
                                        )}
                                      >
                                        <p className={cn("flex items-center gap-1.5 font-medium", !isApsWarning && "text-foreground")}>
                                          {isApsWarning ? <TriangleAlert className="size-4 shrink-0" /> : null}
                                          {item.label}
                                        </p>
                                        {item.detail ? (
                                          <p className={cn("mt-0.5 leading-4", isApsWarning ? "text-destructive" : "text-muted-foreground")}>
                                            {item.detail}
                                          </p>
                                        ) : null}
                                      </div>
                                    );
                                  })}
                                </div>
                              ) : (
                                <p className="rounded-md bg-muted/40 px-3 py-2 text-muted-foreground">
                                  {substep.status === "completed" ? substep.result ?? "暂无明细" : "步骤完成后显示明细"}
                                </p>
                              )}
                            </AccordionContent>
                          </AccordionItem>
                        ))}
                        </Accordion>
                        {pdeConflicts.map((conflict, conflictIndex) => {
                          const pending = !conflict.effective;
                          return (
                            <div
                              key={conflict.conflict_key ?? conflictIndex}
                              className={cn(
                                "mt-2 flex gap-2 rounded-md border px-3 py-2.5 text-xs",
                                pending
                                  ? "border-destructive/30 bg-destructive/5 text-destructive"
                                  : "border-amber-500/30 bg-amber-500/5 text-amber-800 dark:text-amber-300",
                              )}
                            >
                              <TriangleAlert className="mt-0.5 size-4 shrink-0" />
                              <div className="min-w-0">
                                <p className="font-medium">
                                  PDE/OEB 潜能等级冲突{pending ? "待裁决" : "已裁决"}
                                </p>
                                <p className="mt-1 leading-5 opacity-90">
                                  {conflict.summary ?? "原文 PDE/OEB 与确定性推导结果不一致。"}
                                </p>
                                {conflict.effective ? (
                                  <p className="mt-1 font-medium">
                                    本报告采用{conflict.effective.source_label ?? "已选"}值
                                    {conflict.effective.pde_mg_day != null ? ` · PDE ${conflict.effective.pde_mg_day} mg/日` : ""}
                                    {conflict.effective.band != null ? ` · OEB band ${conflict.effective.band}` : ""}
                                  </p>
                                ) : (
                                  <p className="mt-1 font-medium">总体风险结论保持待评估，不得认定全部风险可以接受。</p>
                                )}
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    ) : null}
                  </div>
                </div>;
              })}
            </div>

            {generate.isError && <div className="mt-6 rounded-lg border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive">{friendlyError(generate.error)}</div>}

          </div>
          <div className="flex justify-end gap-3 border-t px-6 py-4">
            {planning && <Button disabled={refreshData.isPending || dataPreparation.status === "error"} onClick={startGeneration}><Sparkles />开始生成</Button>}
            {generate.isError && <Button onClick={enterGenerationPlan}><RotateCcw />重新生成</Button>}
            {completed && <>
              <Button variant="outline" onClick={enterGenerationPlan}><RotateCcw />重新生成</Button>
              <Button variant="outline" onClick={preview}><Eye />预览</Button>
              <Button onClick={download}><Download />下载报告</Button>
            </>}
            {historyLoading && <Button disabled><Loader2 className="animate-spin" />加载中</Button>}
            {busy && <Button disabled><Loader2 className="animate-spin" />生成中</Button>}
          </div>
      </SheetContent>

      <Dialog open={previewOpen} onOpenChange={setPreviewOpen}>
        <DialogContent className="left-0 top-0 flex h-[100dvh] w-screen max-w-none translate-x-0 translate-y-0 flex-col gap-0 overflow-hidden rounded-none border-0 p-0 sm:max-w-none sm:rounded-none">
          <div className="flex h-14 shrink-0 items-center border-b bg-background px-5 pr-14">
            <div className="min-w-0">
              <DialogTitle className="truncate text-base">风险评估报告预览</DialogTitle>
              <DialogDescription className="mt-1 truncate text-xs">
                {item.title || "生成的 Word 报告"}
              </DialogDescription>
            </div>
          </div>
          <div className="relative min-h-0 flex-1 overflow-auto bg-muted/60">
            {previewLoading ? (
              <div className="absolute inset-0 z-10 flex items-center justify-center bg-background/75">
                <div className="flex items-center gap-2 text-sm text-muted-foreground">
                  <Loader2 className="size-4 animate-spin text-primary" />
                  正在加载 Word 文档预览…
                </div>
              </div>
            ) : null}
            {previewError ? (
              <div className="absolute inset-0 z-10 flex items-center justify-center p-8">
                <div className="max-w-md rounded-lg border border-destructive/30 bg-background p-5 text-sm text-destructive shadow-sm">
                  <div className="flex items-start gap-2">
                    <TriangleAlert className="mt-0.5 size-4 shrink-0" />
                    <span>{previewError}</span>
                  </div>
                </div>
              </div>
            ) : null}
            <div ref={previewContainerRef} className="min-h-full min-w-full" />
          </div>
        </DialogContent>
      </Dialog>

      {busy && !drawerOpen ? (
        <div
          role="status"
          className="fixed bottom-6 right-6 z-50"
        >
          <SheetTrigger asChild>
            <button
              type="button"
              className="flex items-center gap-2 rounded-md border border-border bg-popover px-4 py-2.5 text-sm text-popover-foreground shadow-lg transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
              aria-label="打开风险评估报告生成进度"
            >
              <Loader2 className="size-4 animate-spin text-primary" />
              <span>正在通过模板生成风险评估报告…</span>
              <span className="text-xs text-primary">查看进度</span>
            </button>
          </SheetTrigger>
        </div>
      ) : notice ? (
        <div
          role="status"
          className={cn(
            "fixed bottom-6 right-6 z-50 flex max-w-sm items-center gap-2 rounded-md border border-border bg-popover px-4 py-2.5 text-sm shadow-lg",
            TONE_STYLES[notice.tone].className,
          )}
        >
          {(() => {
            const Icon = TONE_STYLES[notice.tone].Icon;
            return <Icon className="size-4 shrink-0" />;
          })()}
          <span>{notice.text}</span>
        </div>
      ) : null}
    </Sheet>
  );
}
