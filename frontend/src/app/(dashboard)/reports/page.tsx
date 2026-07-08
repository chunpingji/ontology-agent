"use client";

import { useCallback, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
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
  listReportCenterItems,
  phaseIriByLabel,
  phaseLabel,
  type PreparedUpload,
  type ReportCenterResult,
  type ReportOrDocument,
} from "@/lib/api";

const INITIAL_MAX_JOBS = 25;

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
  const { role } = useIdentity();
  const canDelete = role === "senior_analyst";
  const queryClient = useQueryClient();

  const [selected, setSelected] = useState<string | null>(null);
  const [maxJobs, setMaxJobs] = useState(INITIAL_MAX_JOBS);
  // 乐观占位（上传中/刚完成、后端列表尚未刷新出的条目）。key=预测 IRI。
  const [optimistic, setOptimistic] = useState<ReportOrDocument[]>([]);

  const queryKey = useMemo(() => ["report-center", maxJobs] as const, [maxJobs]);
  const query = useQuery({
    queryKey,
    queryFn: () => listReportCenterItems({ maxJobs }),
  });

  const backendItems = useMemo(() => query.data?.items ?? [], [query.data]);
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
      query.refetch().then((result) => {
        const confirmed = new Set((result.data?.items ?? []).map((item) => item.key));
        setOptimistic((prev) => prev.filter((item) => !confirmed.has(item.key)));
      });
    },
    [query],
  );

  const handleDelete = useCallback(
    async (item: ReportOrDocument) => {
      if (typeof window !== "undefined" && !window.confirm(`确认删除"${item.title}"？`)) {
        return;
      }
      // Optimistic UI removal.
      setOptimistic((prev) => prev.filter((entry) => entry.key !== item.key));
      queryClient.setQueryData<ReportCenterResult>(queryKey, (prev) =>
        prev ? { ...prev, items: prev.items.filter((entry) => entry.key !== item.key) } : prev,
      );
      // Persist deletion to backend.
      try {
        if (item.kind === "generated-report" && item.jobId && item.reportId) {
          await deleteReport(item.jobId, item.reportId);
        } else if (item.kind === "uploaded-document" && item.iri) {
          await deleteDocument(item.iri);
        }
      } catch {
        queryClient.invalidateQueries({ queryKey });
      }
    },
    [queryClient, queryKey],
  );

  const data = query.data;
  const filtered = selected ? allItems.filter((item) => item.category === selected) : allItems;

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-semibold text-foreground">报告中心</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          平台生成的报告与研发文档的统一中心。
        </p>
      </div>

      {query.isLoading ? (
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-[240px_1fr]">
          <Skeleton className="h-64 w-full" />
          <div className="space-y-2">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        </div>
      ) : query.isError ? (
        <Alert variant="destructive">
          <AlertCircle className="h-4 w-4" />
          <AlertTitle>加载失败</AlertTitle>
          <AlertDescription>
            无法加载报告中心内容，请稍后重试。
            <Button
              variant="link"
              className="h-auto p-0 pl-2 text-destructive"
              onClick={() => query.refetch()}
            >
              重试
            </Button>
          </AlertDescription>
        </Alert>
      ) : (
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

            <ItemList
              items={filtered}
              canDelete={canDelete}
              onDelete={handleDelete}
              selectedCategory={selected}
            />

            {data?.truncated && (
              <div className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-border bg-muted px-4 py-2.5 text-sm text-muted-foreground">
                <span>
                  已扫描 {data.jobsScanned} / {data.totalJobs} 个任务，报告可能未完全加载。
                </span>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={query.isFetching}
                  onClick={() => setMaxJobs((prev) => prev * 2)}
                >
                  加载更多
                </Button>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
