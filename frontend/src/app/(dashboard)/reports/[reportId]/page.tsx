"use client";

import { useCallback, useMemo, useState } from "react";
import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { useQuery, useMutation } from "@tanstack/react-query";
import { AlertCircle, Check, Download, FileWarning, Loader2, Share2 } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
} from "@/components/ui/breadcrumb";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { Skeleton } from "@/components/ui/skeleton";
import { DocumentActionsMenu } from "@/components/reports/document-actions-menu";
import { Outline } from "@/components/reports/outline";
import { RelatedInfo } from "@/components/reports/related-info";
import {
  documentContentKey,
  ReadingPane,
  REPORT_SECTIONS,
  resolveDocumentContent,
  saveBlob,
} from "@/components/reports/reading-pane";
import {
  downloadReportById,
  listReportCenterItems,
  type ReportOrDocument,
} from "@/lib/api";

type ReadonlyParams = Pick<URLSearchParams, "get">;

/** 从 query 参数重建条目（Report Center 导航时携带）；参数不足则返回 null（走深链回退）。 */
function itemFromParams(routeKey: string, sp: ReadonlyParams): ReportOrDocument | null {
  const kind = sp.get("kind");
  if (kind !== "generated-report" && kind !== "uploaded-document") return null;

  const type = sp.get("type") || "";
  const sizeRaw = sp.get("size");
  const size = sizeRaw ? Number(sizeRaw) : null;
  const base = {
    key: routeKey,
    title: sp.get("title") || routeKey,
    category: type,
    type,
    date: sp.get("date"),
    size: size != null && Number.isFinite(size) ? size : null,
  };

  if (kind === "generated-report") {
    const jobId = sp.get("jobId");
    const reportId = sp.get("reportId");
    if (!jobId || !reportId) return null;
    return { ...base, kind, jobId, reportId };
  }

  const iri = sp.get("iri");
  if (!iri) return null;
  return { ...base, kind, iri };
}

function DetailBreadcrumb({ title }: { title: string }) {
  return (
    <Breadcrumb>
      <BreadcrumbList>
        <BreadcrumbItem>
          <BreadcrumbLink asChild>
            <Link href="/reports">报告中心</Link>
          </BreadcrumbLink>
        </BreadcrumbItem>
        <BreadcrumbSeparator />
        <BreadcrumbItem>
          <BreadcrumbPage className="max-w-[60vw] truncate">{title}</BreadcrumbPage>
        </BreadcrumbItem>
      </BreadcrumbList>
    </Breadcrumb>
  );
}

export default function ReportDetailPage() {
  const params = useParams();
  const searchParams = useSearchParams();
  const routeKey = decodeURIComponent(String(params.reportId ?? ""));

  const paramItem = useMemo(
    () => itemFromParams(routeKey, searchParams),
    [routeKey, searchParams],
  );

  // 深链回退：query 参数不足时，重新聚合并按 key 查回条目。
  const fallback = useQuery({
    queryKey: ["report-center-resolve", routeKey],
    queryFn: () => listReportCenterItems({ maxJobs: 100 }),
    enabled: !paramItem,
  });

  const item: ReportOrDocument | null =
    paramItem ?? fallback.data?.items.find((entry) => entry.key === routeKey) ?? null;
  const isDoc = item?.kind === "uploaded-document";

  const [highlightRef, setHighlightRef] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const contentQuery = useQuery({
    queryKey: documentContentKey(item),
    queryFn: () => resolveDocumentContent(item as ReportOrDocument),
    enabled: Boolean(item) && isDoc,
  });
  const documentContent =
    contentQuery.data && "content" in contentQuery.data ? contentQuery.data.content : null;
  // 识别实体与预览同源（同一次标注请求）——供右侧「关联信息」面板展示。
  const relatedEntities =
    contentQuery.data && "entities" in contentQuery.data ? contentQuery.data.entities : null;

  const download = useMutation({
    mutationFn: async () => {
      if (!item || item.kind !== "generated-report" || !item.jobId || !item.reportId) {
        throw new Error("该条目不支持下载");
      }
      const blob = await downloadReportById(item.jobId, item.reportId);
      saveBlob(blob, item.title || item.key);
    },
  });

  const handleNavigate = useCallback(
    (target: string) => {
      if (isDoc) {
        setHighlightRef(target);
      } else if (typeof document !== "undefined") {
        document.getElementById(target)?.scrollIntoView({ behavior: "smooth", block: "start" });
      }
    },
    [isDoc],
  );

  const handleShare = useCallback(() => {
    if (typeof navigator === "undefined" || !navigator.clipboard) return;
    navigator.clipboard
      .writeText(window.location.href)
      .then(() => setCopied(true))
      .catch(() => undefined);
  }, []);

  // --- 加载 / 错误 / 未找到 状态（均带面包屑返回中心） ------------------------
  if (!paramItem && fallback.isLoading) {
    return (
      <div className="space-y-6">
        <DetailBreadcrumb title="加载中…" />
        <Skeleton className="h-8 w-1/3" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  if (!paramItem && fallback.isError) {
    return (
      <div className="space-y-6">
        <DetailBreadcrumb title="加载失败" />
        <Alert variant="destructive">
          <AlertCircle className="h-4 w-4" />
          <AlertTitle>加载失败</AlertTitle>
          <AlertDescription>无法加载该条目，请返回报告中心重试。</AlertDescription>
        </Alert>
      </div>
    );
  }

  if (!item) {
    return (
      <div className="space-y-6">
        <DetailBreadcrumb title="未找到" />
        <EmptyState
          icon={<FileWarning />}
          title="未找到该报告或文档"
          description="链接可能已失效，请返回报告中心重新选择。"
          action={
            <Button asChild variant="outline">
              <Link href="/reports">返回报告中心</Link>
            </Button>
          }
        />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <DetailBreadcrumb title={item.title} />

      <header className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <h1 className="truncate text-2xl font-semibold text-foreground" title={item.title}>
            {item.title}
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            {item.type || (isDoc ? "文档" : "报告")}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {item.kind === "generated-report" && (
            <Button onClick={() => download.mutate()} disabled={download.isPending}>
              {download.isPending ? <Loader2 className="animate-spin" /> : <Download />}
              下载
            </Button>
          )}
          {/* 上传文档：右上角「操作」弹出菜单（AI 分析 / 生成风险评估报告 / 审计），紧邻分享。 */}
          {isDoc && <DocumentActionsMenu />}
          <Button variant="outline" onClick={handleShare}>
            {copied ? <Check /> : <Share2 />}
            {copied ? "已复制链接" : "分享"}
          </Button>
        </div>
      </header>

      {download.isError && (
        <p className="text-sm text-destructive">下载失败，请稍后重试。</p>
      )}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[220px_1fr_260px]">
        <Card className="h-fit lg:sticky lg:top-6">
          <CardHeader className="p-4 pb-2">
            <CardTitle className="text-sm">目录</CardTitle>
          </CardHeader>
          <CardContent className="p-2 pt-0">
            {isDoc && contentQuery.isLoading ? (
              <div className="space-y-2 p-1">
                <Skeleton className="h-6 w-full" />
                <Skeleton className="h-6 w-5/6" />
                <Skeleton className="h-6 w-4/6" />
              </div>
            ) : isDoc ? (
              <Outline content={documentContent} onNavigate={handleNavigate} />
            ) : (
              <Outline sections={REPORT_SECTIONS} onNavigate={handleNavigate} />
            )}
          </CardContent>
        </Card>

        <Card className="min-w-0">
          <CardContent className="p-6">
            <ReadingPane item={item} highlightRef={highlightRef} />
          </CardContent>
        </Card>

        <Card className="h-fit lg:sticky lg:top-6">
          <CardHeader className="p-4 pb-2">
            <CardTitle className="text-sm">关联信息</CardTitle>
          </CardHeader>
          <CardContent className="p-4 pt-0">
            <RelatedInfo
              isDoc={isDoc}
              entities={relatedEntities}
              isLoading={isDoc && contentQuery.isLoading}
              isError={isDoc && contentQuery.isError}
            />
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
