"use client";

import { useCallback, useMemo, useState } from "react";
import { useInfiniteQuery, useQuery, useQueryClient, type InfiniteData } from "@tanstack/react-query";
import { AlertCircle } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { CategoryTree } from "@/components/reports/category-tree";
import { ItemList } from "@/components/reports/item-list";
import { Upload } from "@/components/reports/upload";
import { useIdentity } from "@/lib/use-identity";
import {
  deleteDocument,
  deleteReport,
  docTypeLabel,
  listReportCenterDocuments,
  listReportCenterReports,
  phaseIriByLabel,
  phaseLabel,
  type PreparedUpload,
  type ReportCenterPage as ReportPage,
  type ReportOrDocument,
} from "@/lib/api";

/** 已构造的上传占位 → 乐观列表条目（key=预测 IRI，与后端物化后的条目 key 逐字节一致）。 */
function ghostItem(prepared: PreparedUpload, status: "processing" | "ready"): ReportOrDocument {
  const metadata = (prepared.envelope.metadata ?? {}) as Record<string, unknown>;
  return {
    key: prepared.iri,
    kind: "uploaded-document",
    title: prepared.title,
    // 文件夹（category）= 研发阶段；列（type）= 文档类型——与后端列表归类同轴，保证去重对账。
    category: prepared.phaseIri ? phaseLabel(prepared.phaseIri) : "未分阶段",
    type: docTypeLabel(prepared.classIri),
    date: (metadata.created_at as string) ?? null,
    size: prepared.size,
    iri: prepared.iri,
    status,
  };
}

export default function ReportCenterPage() {
  const { identity, role } = useIdentity();
  const canDelete = role === "senior_analyst";
  const queryClient = useQueryClient();

  const [selected, setSelected] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState(false);
  // 乐观占位（上传中/刚完成、后端列表尚未刷新出的条目）。key=预测 IRI。
  const [optimistic, setOptimistic] = useState<ReportOrDocument[]>([]);

  const documentsKey = useMemo(
    () => ["report-center", identity.username, role, "documents"] as const,
    [identity.username, role],
  );
  const reportsKey = useMemo(
    () => ["report-center", identity.username, role, "reports"] as const,
    [identity.username, role],
  );
  const documents = useQuery({
    queryKey: documentsKey,
    queryFn: ({ signal }) => listReportCenterDocuments(signal),
    retry: false,
  });
  const reports = useInfiniteQuery({
    queryKey: reportsKey,
    initialPageParam: 1,
    queryFn: ({ pageParam, signal }) => listReportCenterReports(pageParam, signal),
    getNextPageParam: (last) =>
      last.page * last.page_size < last.total ? last.page + 1 : undefined,
    retry: false,
  });

  const reportItems = useMemo(
    () => reports.data?.pages.flatMap((page) => page.items) ?? [],
    [reports.data],
  );
  const backendItems = useMemo(
    () => [...(documents.data ?? []), ...reportItems],
    [documents.data, reportItems],
  );
  const backendKeys = useMemo(
    () => new Set(backendItems.map((item) => item.key)),
    [backendItems],
  );

  // 合并：乐观占位在前（渲染时去掉已被后端列出的，避免重影），后端条目在后。
  const ghosts = useMemo(
    () => optimistic.filter((item) => !backendKeys.has(item.key)),
    [optimistic, backendKeys],
  );
  const allItems = useMemo(() => [...ghosts, ...backendItems], [ghosts, backendItems]);

  // 上传目标 = 左侧选中的**研发阶段**（文件夹轴）。仅当选中项是研发阶段时可上传（其
  // 阶段 IRI 随信封落 hasDevelopmentPhase）；否则（全部 / 报告类型 / 未分阶段）为 null →
  // 上传面板提示先选研发阶段。文档**类型**由用户在选文件后单独指定（另一个轴）。
  const uploadPhaseIri = useMemo(() => phaseIriByLabel(selected), [selected]);

  const handleUploadStart = useCallback((prepared: PreparedUpload[]) => {
    // 占位分类（文件夹）恒等于所选研发阶段（phaseIri 由 selected 推导），故无需再切分类。
    const items = prepared.map((p) => ghostItem(p, "processing"));
    setOptimistic((prev) => {
      const incoming = new Set(items.map((item) => item.key));
      return [...items, ...prev.filter((item) => !incoming.has(item.key))];
    });
  }, []);

  const handleUploadEnd = useCallback(
    (iris: string[], ok: boolean) => {
      const target = new Set(iris);
      if (!ok) {
        setOptimistic((prev) => prev.filter((item) => !target.has(item.key)));
        return;
      }
      // 翻牌为「就绪」（占位据预测 IRI 已可直接打开），再刷新并入后端真实数据。
      setOptimistic((prev) =>
        prev.map((item) => (target.has(item.key) ? { ...item, status: "ready" } : item)),
      );
      // 刷新后，把后端已确认列出的占位从占位集剔除（事件驱动对账，非副作用轮询）。
      documents.refetch().then((result) => {
        const confirmed = new Set((result.data ?? []).map((item) => item.key));
        setOptimistic((prev) => prev.filter((item) => !confirmed.has(item.key)));
      });
    },
    [documents],
  );

  const handleDelete = useCallback(
    async (item: ReportOrDocument) => {
      if (typeof window !== "undefined" && !window.confirm(`确认删除"${item.title}"？`)) {
        return;
      }
      const queryKey = item.kind === "generated-report" ? reportsKey : documentsKey;
      await queryClient.cancelQueries({ queryKey });
      setDeleteError(false);
      setOptimistic((prev) => prev.filter((entry) => entry.key !== item.key));
      if (item.kind === "generated-report") {
        queryClient.setQueryData<InfiniteData<ReportPage>>(reportsKey, (prev) => prev && ({
          ...prev,
          pages: prev.pages.map((page) => ({
            ...page,
            items: page.items.filter((entry) => entry.key !== item.key),
            total: Math.max(0, page.total - 1),
          })),
        }));
      } else {
        queryClient.setQueryData<ReportOrDocument[]>(documentsKey, (prev) =>
          prev?.filter((entry) => entry.key !== item.key),
        );
      }
      try {
        if (item.kind === "generated-report" && item.jobId && item.reportId) {
          await deleteReport(item.jobId, item.reportId);
        } else if (item.kind === "uploaded-document" && item.iri) {
          await deleteDocument(item.iri);
        }
      } catch {
        setDeleteError(true);
      } finally {
        // 删除会改变 offset，刷新已加载页以补齐条目和总数；失败时也恢复服务端列表。
        await queryClient.invalidateQueries({ queryKey });
      }
    },
    [queryClient, reportsKey, documentsKey],
  );

  const totalReports = reports.data?.pages[0]?.total ?? 0;
  const filtered = selected ? allItems.filter((item) => item.category === selected) : allItems;

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-semibold text-foreground">报告中心</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          平台生成的报告与研发文档的统一中心。
        </p>
      </div>

      {deleteError && (
        <Alert variant="destructive">
          <AlertCircle className="h-4 w-4" />
          <AlertTitle>删除失败</AlertTitle>
          <AlertDescription>未能删除该条目，请稍后重试。</AlertDescription>
        </Alert>
      )}

      {[
        { label: "文档", failed: documents.isError, refetch: documents.refetch },
        { label: "报告", failed: reports.isError && !reports.isFetchNextPageError, refetch: reports.refetch },
      ].map(({ label, failed, refetch }) => failed && (
        <Alert key={label} variant="destructive">
          <AlertCircle className="h-4 w-4" />
          <AlertTitle>{label}加载失败</AlertTitle>
          <AlertDescription>
            无法加载{label}，请稍后重试。
            <Button
              variant="link"
              className="h-auto p-0 pl-2 text-destructive"
              onClick={() => void refetch()}
            >
              重试
            </Button>
          </AlertDescription>
        </Alert>
      ))}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[240px_1fr]">
        <Card className="sticky top-20 h-fit self-start">
          <CardHeader className="p-4 pb-2">
            <CardTitle className="text-sm">分类</CardTitle>
          </CardHeader>
          <CardContent className="p-2 pt-0">
            {/* 上传目标即此处选中的分类——单一分类入口，避免二次选择。 */}
            <CategoryTree items={allItems} selected={selected} onSelect={setSelected} />
          </CardContent>
        </Card>

        <div className="space-y-4">
          <Upload phaseIri={uploadPhaseIri} onStart={handleUploadStart} onEnd={handleUploadEnd} />

          {(documents.isPending || reports.isPending) && (
            <p role="status" className="text-sm text-muted-foreground">
              {documents.isPending && "正在加载文档…"}
              {reports.isPending && "正在加载报告…"}
            </p>
          )}
          {allItems.length === 0 && (documents.isPending || reports.isPending) ? (
            <div className="space-y-2">
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-full" />
            </div>
          ) : (
            <ItemList
              items={filtered}
              canDelete={canDelete}
              onDelete={handleDelete}
              selectedCategory={selected}
            />
          )}

          {reports.data && (
            <div className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-border bg-muted px-4 py-2.5 text-sm text-muted-foreground">
              <span>
                已加载 {reportItems.length} / {totalReports} 份报告
                {reports.isFetchNextPageError && "，更多报告加载失败。"}
              </span>
              {reports.hasNextPage && (
                <Button
                  variant="outline"
                  size="sm"
                  disabled={reports.isFetching}
                  onClick={() => void reports.fetchNextPage()}
                >
                  {reports.isFetchingNextPage ? "加载中…" : reports.isFetchNextPageError ? "重试加载更多" : "加载更多"}
                </Button>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
