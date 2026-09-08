"use client";

import { ModelRequestProgress } from "./model-request-progress";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
} from "@/components/ui/sheet";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  generateRiskReport,
  extractJobEvidence,
  getAnnotatedDocument,
  pauseAnnotation,
  pollReportStatus,
  rerunAnnotation,
  subscribeJobProgress,
  type AnnotatedDocument,
  type JobProgressEvent,
  type EvidenceAnchor,
  type EvidenceExtractOptions,
} from "@/lib/api";
import { WordViewer } from "./word-viewer";
import { ExcelViewer } from "./excel-viewer";
import { RelationPanel } from "./relation-panel";
import { EvidenceReviewPanel } from "./evidence-review-panel";

const ANNOTATION_LABELS: Record<string, string> = {
  gliner: "GLiNER 定界",
  typing: "嵌入归类",
  triples: "属性三元组",
  done: "完成",
  paused: "已暂停",
  queued: "等待识别",
  extracting: "关系识别",
  summarizing: "生成章节摘要",
};

const STATUS_LABELS: Record<string, string> = {
  pending: "待处理",
  running: "运行中",
  parsing: "解析中",
  annotating: "标注中",
  extracting: "抽取中",
  aligning: "对齐中",
  reviewing: "待审核",
  done: "完成",
  failed: "失败",
};

interface ExtractionDrawerProps {
  jobId: string | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function ExtractionDrawer(props: ExtractionDrawerProps) {
  return <ExtractionDrawerContent key={`${props.jobId}:${props.open}`} {...props} />;
}

function ExtractionDrawerContent({ jobId, open, onOpenChange }: ExtractionDrawerProps) {
  const [doc, setDoc] = useState<AnnotatedDocument | null>(null);
  const [loading, setLoading] = useState(open && !!jobId);
  const [error, setError] = useState<string | null>(null);
  const [selectedSourceRef, setSelectedSourceRef] = useState<string | null>(null);
  const [progressEvent, setProgressEvent] = useState<JobProgressEvent | null>(null);
  const [rerunning, setRerunning] = useState(false);
  const [pauseRequested, setPauseRequested] = useState(false);
  const [evidenceRefresh, setEvidenceRefresh] = useState(0);
  const [reportGenerating, setReportGenerating] = useState(false);
  const [activeAnchor, setActiveAnchor] = useState<EvidenceAnchor | null>(null);
  const [evidenceSnapshot, setEvidenceSnapshot] = useState<string | null>(null);

  const fetchDoc = useCallback((id: string) => {
    setLoading(true);
    setError(null);
    getAnnotatedDocument(id)
      .then((result) => {
        setDoc(result);
        setSelectedSourceRef(null);
        setActiveAnchor(null);
      })
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (!open || !jobId) return;
    let ignore = false;
    const controller = new AbortController();
    getAnnotatedDocument(jobId, false, controller.signal)
      .then((result) => { if (!ignore) setDoc(result); })
      .catch((e) => { if (!ignore) setError(String(e)); })
      .finally(() => { if (!ignore) setLoading(false); });
    return () => { ignore = true; controller.abort(); };
  }, [open, jobId]);

  useEffect(() => {
    if (!open || !jobId || rerunning) return;
    let ignore = false;
    let lastRevision: number | undefined;
    let refreshTimer: ReturnType<typeof setTimeout> | undefined;
    const unsub = subscribeJobProgress(jobId, (e) => {
      if (ignore || e.job_id !== jobId || !e.annotation_stage) return;
      setProgressEvent(e);
      setPauseRequested(!!e.pause_requested);
      if (["complete", "paused", "failed", "interrupted"].includes(e.annotation_stage)) {
        ignore = true;
        clearTimeout(refreshTimer);
        unsub();
        setPauseRequested(false);
        setEvidenceRefresh((value) => value + 1);
        fetchDoc(jobId);
      } else if (e.data_revision !== undefined && e.data_revision !== lastRevision) {
        lastRevision = e.data_revision;
        if (!refreshTimer) refreshTimer = setTimeout(() => {
          refreshTimer = undefined;
          if (!ignore) setEvidenceRefresh((value) => value + 1);
        }, 1500);
      }
    });
    return () => { ignore = true; clearTimeout(refreshTimer); unsub(); };
  }, [open, jobId, rerunning, fetchDoc]);

  async function handleRecognition(options?: EvidenceExtractOptions) {
    if (!jobId || rerunning || progressEvent?.status === "running") return;
    setRerunning(true);
    setError(null);
    setPauseRequested(false);
    try {
      const run = options ? await extractJobEvidence(jobId, options) : await rerunAnnotation(jobId);
      setProgressEvent({
        job_id: run.job_id, run_id: run.run_id, stage: "annotating", annotation_stage: "queued",
        pct: 0, status: "running", degraded: false,
      });
    } catch (e) {
      setError(String(e));
    } finally {
      setRerunning(false);
    }
  }

  async function handlePause() {
    if (!jobId) return;
    setPauseRequested(true);
    try {
      await pauseAnnotation(jobId);
    } catch (e) {
      setError(String(e));
      setPauseRequested(false);
    }
  }

  async function handleGenerateReport() {
    if (!jobId) return;
    setReportGenerating(true);
    setError(null);
    try {
      const response = await generateRiskReport(jobId);
      if (response instanceof Blob) {
        _downloadBlob(response, doc?.filename);
      } else if (typeof response === "object" && response !== null && "report_id" in response) {
        const asyncResult = response as { report_id: string; status: string };
        await _pollUntilDone(jobId, asyncResult.report_id);
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setReportGenerating(false);
    }
  }

  function _downloadBlob(blob: Blob, filename?: string | null) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    const name = filename ?? "report";
    const srcName = name.toLowerCase().endsWith(".docx") ? name.slice(0, -5) : name;
    a.href = url;
    a.download = `风险评估表_${srcName}.docx`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }

  async function _pollUntilDone(jid: string, reportId: string) {
    const maxAttempts = 60;
    for (let i = 0; i < maxAttempts; i++) {
      await new Promise((r) => setTimeout(r, 2000));
      const status = await pollReportStatus(jid, reportId);
      if (status.report_status === "completed") {
        const res = await fetch(
          `/api/extraction/jobs/${jid}/reports/${reportId}/download`,
        );
        if (!res.ok) throw new Error(`下载失败: ${res.status}`);
        const blob = await res.blob();
        _downloadBlob(blob, doc?.filename);
        return;
      }
      if (status.report_status === "failed") {
        throw new Error(status.report_error || "报告生成失败");
      }
    }
    throw new Error("报告生成超时，请稍后重试");
  }

  const isAnnotating = rerunning || progressEvent?.status === "running";

  const canGenerateReport =
    !!doc &&
    !rerunning &&
    !isAnnotating &&
    !!evidenceSnapshot;
  const annoStage = progressEvent?.annotation_stage;

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent size="xl" className="flex flex-col overflow-hidden p-0">
        <SheetHeader className="shrink-0 px-6 pt-6 pb-2">
          <SheetTitle className="flex items-center gap-2">
            <span className="truncate">{doc?.filename ?? "文档标注"}</span>
            {doc?.doc_class && (
              <Badge variant="outline" className="shrink-0 text-xs font-normal">
                {doc.doc_class.label}
              </Badge>
            )}
          </SheetTitle>
          <SheetDescription>
            点击关系节点定位文档素材
          </SheetDescription>
        </SheetHeader>

        {/* Annotation status bar */}
        <div className="shrink-0 border-b px-6 py-2 flex items-center gap-3">
          {isAnnotating ? (
            <>
              <Badge variant="default" className="animate-pulse">标注中</Badge>
              {annoStage && ANNOTATION_LABELS[annoStage] && (
                <span className="text-xs text-muted-foreground">
                  {ANNOTATION_LABELS[annoStage]}
                </span>
              )}
              {progressEvent && (
                <span className="text-xs text-muted-foreground">
                  （{progressEvent.pct}%）
                </span>
              )}
              <ModelRequestProgress request={progressEvent?.model_request}
                attempts={progressEvent?.http_attempts} />
              <Button size="sm" variant="outline" className="h-7 text-xs"
                disabled={rerunning || pauseRequested} onClick={handlePause}>
                {pauseRequested ? "正在保存断点…" : "暂停识别"}
              </Button>
            </>
          ) : (
            <>
              <Badge
                variant={
                  progressEvent?.stage === "failed"
                    ? "destructive"
                    : progressEvent?.stage === "reviewing" || progressEvent?.stage === "done"
                      ? "success"
                      : "secondary"
                }
              >
                {progressEvent
                  ? ANNOTATION_LABELS[progressEvent.annotation_stage ?? ""] ??
                    STATUS_LABELS[progressEvent.status] ?? progressEvent.status
                  : doc
                    ? doc.preview_only ? "正文已加载" : doc.completion === "incomplete" ? "抽取未完成" : "标注完成"
                    : "—"}
              </Badge>
              {doc && !rerunning && (
                <Button
                  size="sm"
                  variant="outline"
                  className="h-7 text-xs"
                  onClick={() => handleRecognition()}
                >
                  重新标注
                </Button>
              )}
              {canGenerateReport && (
                <Button
                  size="sm"
                  variant="outline"
                  className="h-7 text-xs"
                  disabled={reportGenerating}
                  onClick={handleGenerateReport}
                >
                  {reportGenerating ? "生成中..." : "生成风险评估报告"}
                </Button>
              )}
              {canGenerateReport && jobId && (
                <Button
                  size="sm"
                  variant="outline"
                  className="h-7 text-xs"
                  asChild
                >
                  <Link href={`/entities/extraction/${jobId}/ast`}>
                    查看 AST 覆盖率
                  </Link>
                </Button>
              )}
            </>
          )}
        </div>

        <div className="flex min-h-0 flex-1">
          {/* Left: Document preview */}
          <div className="flex-1 overflow-y-auto border-r bg-muted/40 px-8 py-6">
            {loading && <p className="text-muted-foreground text-sm">加载标注文档中...</p>}
            {error && (
              <div className="rounded border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
                {error}
              </div>
            )}
            {doc && !loading && doc.source_type === "word" && (
              <WordViewer
                content={doc.content as Record<string, unknown>}
                highlightRef={selectedSourceRef}
                activeAnchor={activeAnchor}
              />
            )}
            {doc && !loading && doc.source_type === "excel" && (
              <ExcelViewer
                content={
                  doc.content as {
                    headers: string[];
                    rows: Record<string, { value: string; annotations: { start: number; end: number; text: string; label: string; score: number }[] }>[];
                  }
                }
              />
            )}
          </div>

          {/* Right: Relation panel (Word only) */}
          {doc && !loading && doc.source_type === "word" && (
            <div className="flex w-96 shrink-0 flex-col overflow-y-auto">
              {jobId ? <EvidenceReviewPanel key={jobId} jobId={jobId}
                running={isAnnotating} refreshKey={evidenceRefresh} onContinue={handleRecognition}
                onSource={setActiveAnchor} onSnapshot={setEvidenceSnapshot} /> : <RelationPanel
                docClass={doc.doc_class}
                relationships={doc.relationships}
                selectedSourceRef={selectedSourceRef}
                onSelectSourceRef={setSelectedSourceRef}
              />}
            </div>
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}
