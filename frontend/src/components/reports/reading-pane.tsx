"use client";

import { type ReactNode } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import { Download, FileWarning, Info, Loader2, Sparkles } from "lucide-react";

import { WordViewer } from "@/components/extraction/word-viewer";
import { ReportRunPanel } from "@/components/reporting/report-run-panel";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import {
  downloadReportById,
  entitiesFromTriples,
  getAnnotatedDocument,
  pollReportStatus,
  resolveDocumentJobId,
  type DocClassification,
  type RecognizedEntity,
  type Relationship,
  type ReportOrDocument,
} from "@/lib/api";

/** 生成报告的只读预览分节（id 与 Outline 目录项一一对应，用于滚动定位）。 */
export const REPORT_SECTIONS: Array<{ id: string; label: string }> = [
  { id: "report-overview", label: "报告概览" },
  { id: "report-narratives", label: "报告行文" },
  { id: "report-rules", label: "规则摘要" },
  { id: "report-download", label: "下载原件" },
];

/**
 * 文档在线内容解析结果：可预览的 tiptap 内容 + 识别实体 + 文档分类 + 抽取关系（同一次
 * 标注请求产出，供中栏预览与右侧「关系图谱」面板 `RelationPanel` 复用），或标记为不可预览。
 */
export type DocumentContent =
  | {
      jobId: string;
      content: Record<string, unknown>;
      entities: RecognizedEntity[];
      docClass: DocClassification | null;
      relationships: Relationship[];
      recognition: {
        completion: "complete" | "incomplete" | null;
        diagnostics: string[];
        previewOnly: boolean;
      };
    }
  | { unavailable: true };

/** React Query 键：Outline 与 ReadingPane 共用同一键以复用同一次请求。 */
export function documentContentKey(item: ReportOrDocument | null): Array<string | null> {
  return ["report-detail-doc-content", item?.iri ?? null];
}

/**
 * 尽力而为地解析文档的可视内容。文档实体本身不一定携带抽取 jobId，
 * 而 `getAnnotatedDocument` 只按 jobId 取内容——因此先经 `resolveDocumentJobId`
 * 从文档 `properties_json` 探测 job 引用，取不到则优雅降级为“不可预览”（绝不抛错）。
 */
export async function resolveDocumentContent(
  item: ReportOrDocument,
  signal?: AbortSignal,
): Promise<DocumentContent> {
  if (item.kind !== "uploaded-document" || !item.iri) return { unavailable: true };

  let jobId: string | null = null;
  try {
    jobId = await resolveDocumentJobId(item.iri);
  } catch {
    return { unavailable: true };
  }

  if (!jobId) return { unavailable: true };

  try {
    const doc = await getAnnotatedDocument(jobId, false, signal);
    if (doc.content && typeof doc.content === "object") {
      return {
        jobId,
        content: doc.content as Record<string, unknown>,
        entities: entitiesFromTriples(doc.triples ?? []),
        docClass: doc.doc_class ?? null,
        relationships: doc.relationships ?? [],
        recognition: {
          completion: doc.evidence_run?.completion ?? doc.completion ?? null,
          diagnostics: doc.evidence_run?.diagnostics ?? [],
          previewOnly: doc.preview_only === true,
        },
      };
    }
  } catch (error) {
    if (signal?.aborted) throw error;
    return { unavailable: true };
  }
  return { unavailable: true };
}

/** Blob → 对象 URL → 锚点，触发浏览器保存（生成报告下载原件）。 */
export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

export function formatBytes(bytes: number | null): string {
  if (bytes == null) return "—";
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB"];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(1)} ${units[unit]}`;
}

function MetaRow({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 text-sm">
      <dt className="shrink-0 text-muted-foreground">{label}</dt>
      <dd className="min-w-0 truncate text-right text-foreground">{value}</dd>
    </div>
  );
}

/** 单节行文叙述块：标题 + 保留换行的正文（LLM 生成，仅供参考）。 */
function NarrativeBlock({ title, text }: { title: string; text: string }) {
  return (
    <div className="rounded-lg border border-border bg-card p-4">
      <h3 className="mb-1.5 text-sm font-medium text-foreground">{title}</h3>
      <p className="whitespace-pre-wrap text-sm leading-relaxed text-foreground/90">
        {text}
      </p>
    </div>
  );
}

function PaneSkeleton() {
  return (
    <div className="space-y-3">
      <Skeleton className="h-6 w-1/3" />
      <Skeleton className="h-4 w-full" />
      <Skeleton className="h-4 w-5/6" />
      <Skeleton className="h-4 w-4/6" />
      <Skeleton className="h-40 w-full" />
    </div>
  );
}

function DocumentPane({
  item,
  highlightRef,
}: {
  item: ReportOrDocument;
  highlightRef?: string | null;
}) {
  const query = useQuery({
    queryKey: documentContentKey(item),
    queryFn: ({ signal }) => resolveDocumentContent(item, signal),
  });

  if (query.isLoading) return <PaneSkeleton />;

  if (query.isError) {
    return (
      <Alert variant="destructive">
        <FileWarning className="h-4 w-4" />
        <AlertTitle>文档加载失败</AlertTitle>
        <AlertDescription>无法读取该文档内容，请稍后重试。</AlertDescription>
      </Alert>
    );
  }

  const data = query.data;
  if (!data || "unavailable" in data) {
    return (
      <EmptyState
        icon={<FileWarning />}
        title="预览不可用"
        description="暂无法在线预览该文档，请通过原始文档库获取原件。"
      />
    );
  }

  return (
    <div className="scroll-mt-24">
      <WordViewer content={data.content} highlightRef={highlightRef ?? undefined} />
    </div>
  );
}

function ReportPane({ item }: { item: ReportOrDocument }) {
  const status = useQuery({
    queryKey: ["report-detail-status", item.jobId, item.reportId],
    queryFn: () => pollReportStatus(item.jobId as string, item.reportId as string),
    enabled: Boolean(item.jobId && item.reportId),
    retry: false,
  });

  const download = useMutation({
    mutationFn: async () => {
      const blob = await downloadReportById(item.jobId as string, item.reportId as string);
      saveBlob(blob, item.title || item.key);
    },
  });

  const dto = status.data;
  const summary = dto?.rules_summary ?? null;
  const summaryEntries = summary ? Object.entries(summary) : [];

  const narratives = dto?.narratives ?? null;
  const hasNarratives = Boolean(
    narratives &&
      (narratives.subject_description ||
        narratives.conclusion ||
        (narratives.sections?.length ?? 0) > 0),
  );

  if (dto?.report_run_id) return <ReportRunPanel runId={dto.report_run_id} />;

  return (
    <div className="space-y-6">
      <section id="report-overview" className="scroll-mt-24 space-y-3">
        <h2 className="text-base font-semibold text-foreground">报告概览</h2>
        <dl className="space-y-2 rounded-lg border border-border bg-card p-4">
          <MetaRow label="类型" value={<Badge variant="secondary">{item.type || "报告"}</Badge>} />
          <MetaRow label="生成时间" value={item.date ? item.date.slice(0, 10) : "—"} />
          <MetaRow label="大小" value={formatBytes(item.size)} />
          {dto?.actor && <MetaRow label="生成人" value={dto.actor} />}
          {dto?.report_status && <MetaRow label="状态" value={dto.report_status} />}
        </dl>
      </section>

      <Separator />

      {/* 015: 报告行文——每节由 LLM 依行文 Prompt 融合插槽值生成的叙述文字（附 AI 标注）。 */}
      <section id="report-narratives" className="scroll-mt-24 space-y-3">
        <div className="flex items-center gap-2">
          <h2 className="text-base font-semibold text-foreground">报告行文</h2>
          {hasNarratives && (
            <Badge variant="outline" className="gap-1 text-xs text-muted-foreground">
              <Sparkles className="size-3 text-primary" />
              AI 生成
            </Badge>
          )}
        </div>
        {status.isLoading ? (
          <Skeleton className="h-24 w-full" />
        ) : hasNarratives && narratives ? (
          <div className="space-y-4">
            {narratives.subject_description && (
              <NarrativeBlock title="评估对象" text={narratives.subject_description} />
            )}
            {narratives.sections.map((s) => (
              <NarrativeBlock key={s.section_id} title={s.title} text={s.text} />
            ))}
            {narratives.conclusion && (
              <NarrativeBlock title="结论" text={narratives.conclusion} />
            )}
            <p className="text-xs text-muted-foreground">
              标注为 AI 生成的行文内容由文档抽取结果自动生成，仅供参考，不替代人工审核。
            </p>
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">本报告未包含自动生成的行文内容。</p>
        )}
      </section>

      <Separator />

      <section id="report-rules" className="scroll-mt-24 space-y-3">
        <h2 className="text-base font-semibold text-foreground">规则摘要</h2>
        {status.isLoading ? (
          <Skeleton className="h-20 w-full" />
        ) : summaryEntries.length > 0 ? (
          <dl className="space-y-2 rounded-lg border border-border bg-card p-4">
            {summaryEntries.map(([key, value]) => (
              <MetaRow key={key} label={key} value={String(value)} />
            ))}
          </dl>
        ) : (
          <p className="text-sm text-muted-foreground">暂无规则摘要，可下载完整报告查看。</p>
        )}
        {dto?.rules_fired_count != null && (
          <p className="text-sm text-muted-foreground">命中规则数：{dto.rules_fired_count}</p>
        )}
      </section>

      <Separator />

      <section id="report-download" className="scroll-mt-24 space-y-3">
        <h2 className="text-base font-semibold text-foreground">下载原件</h2>
        <Alert>
          <Info className="h-4 w-4" />
          <AlertTitle>在线预览为尽力而为</AlertTitle>
          <AlertDescription>完整、权威的报告内容以下载的原始文件为准。</AlertDescription>
        </Alert>
        <Button onClick={() => download.mutate()} disabled={download.isPending}>
          {download.isPending ? <Loader2 className="animate-spin" /> : <Download />}
          下载报告
        </Button>
        {download.isError && (
          <p className="text-sm text-destructive">下载失败，请稍后重试。</p>
        )}
      </section>
    </div>
  );
}

export function ReadingPane({
  item,
  highlightRef,
  className,
}: {
  item: ReportOrDocument;
  highlightRef?: string | null;
  className?: string;
}) {
  return (
    <div className={cn("min-w-0", className)}>
      {item.kind === "uploaded-document" ? (
        <DocumentPane item={item} highlightRef={highlightRef} />
      ) : (
        <ReportPane item={item} />
      )}
    </div>
  );
}
