"use client";

import { useCallback, useEffect, useState } from "react";
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
  getAnnotatedDocument,
  rerunAnnotation,
  subscribeJobProgress,
  type AnnotatedDocument,
  type JobProgressEvent,
} from "@/lib/api";
import { WordViewer } from "./word-viewer";
import { ExcelViewer } from "./excel-viewer";
import { RelationPanel } from "./relation-panel";

const ANNOTATION_LABELS: Record<string, string> = {
  gliner: "GLiNER 定界",
  typing: "嵌入归类",
  triples: "属性三元组",
  done: "完成",
  paused: "已暂停",
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

export function ExtractionDrawer({ jobId, open, onOpenChange }: ExtractionDrawerProps) {
  const [doc, setDoc] = useState<AnnotatedDocument | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedSourceRef, setSelectedSourceRef] = useState<string | null>(null);
  const [progressEvent, setProgressEvent] = useState<JobProgressEvent | null>(null);
  const [rerunning, setRerunning] = useState(false);
  const [reportGenerating, setReportGenerating] = useState(false);

  const fetchDoc = useCallback((id: string) => {
    setLoading(true);
    setError(null);
    getAnnotatedDocument(id)
      .then((result) => setDoc(result))
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (!open || !jobId) return;
    fetchDoc(jobId);
  }, [open, jobId, fetchDoc]);

  useEffect(() => {
    if (!open || !jobId) return;
    const unsub = subscribeJobProgress(jobId, (e) => {
      setProgressEvent(e);
      if (rerunning && (e.stage === "reviewing" || e.stage === "done")) {
        setRerunning(false);
        fetchDoc(jobId);
      }
    });
    return unsub;
  }, [open, jobId, rerunning, fetchDoc]);

  useEffect(() => {
    if (!open) {
      setSelectedSourceRef(null);
      setProgressEvent(null);
      setRerunning(false);
    }
  }, [open]);

  useEffect(() => {
    setSelectedSourceRef(null);
  }, [doc]);

  async function handleRerun() {
    if (!jobId) return;
    setRerunning(true);
    try {
      await rerunAnnotation(jobId);
    } catch (e) {
      setError(String(e));
      setRerunning(false);
    }
  }

  async function handleGenerateReport() {
    if (!jobId) return;
    setReportGenerating(true);
    setError(null);
    try {
      const blob = await generateRiskReport(jobId);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      const srcName = (doc?.filename ?? "report").replace(/\.docx$/i, "");
      a.href = url;
      a.download = `风险评估表_${srcName}.docx`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    } catch (e) {
      setError(String(e));
    } finally {
      setReportGenerating(false);
    }
  }

  const isAnnotating = rerunning || progressEvent?.stage === "annotating";

  const canGenerateReport =
    !!doc &&
    !rerunning &&
    !isAnnotating &&
    doc.doc_class?.doc_class_iri?.includes("CMCReport") &&
    (doc.relationships?.length ?? 0) > 0;
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
                  ? STATUS_LABELS[progressEvent.stage] ?? progressEvent.stage
                  : doc
                    ? "标注完成"
                    : "—"}
              </Badge>
              {doc && !rerunning && (
                <Button
                  size="sm"
                  variant="outline"
                  className="h-7 text-xs"
                  onClick={handleRerun}
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
            </>
          )}
        </div>

        <div className="flex min-h-0 flex-1">
          {/* Left: Document preview */}
          <div className="flex-1 overflow-y-auto border-r px-6 py-4">
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
              <RelationPanel
                docClass={doc.doc_class}
                relationships={doc.relationships}
                selectedSourceRef={selectedSourceRef}
                onSelectSourceRef={setSelectedSourceRef}
              />
            </div>
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}
