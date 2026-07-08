"use client";

import {
  useCallback,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type MouseEvent as ReactMouseEvent,
} from "react";
import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  AlertCircle,
  Check,
  Download,
  FileWarning,
  GripVertical,
  Link2,
  Loader2,
  RotateCw,
  Share2,
} from "lucide-react";

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
import { RelationPanel } from "@/components/extraction/relation-panel";
import { DocumentActionsMenu } from "@/components/reports/document-actions-menu";
import { Outline } from "@/components/reports/outline";
import {
  documentContentKey,
  ReadingPane,
  REPORT_SECTIONS,
  resolveDocumentContent,
  saveBlob,
} from "@/components/reports/reading-pane";
import {
  decidePdeConflict,
  downloadReportById,
  getPdeConflictDecision,
  listReportCenterItems,
  rerunAnnotation,
  resolveDocumentJobId,
  VersionConflictError,
  type PdeDecisionChoice,
  type ReportOrDocument,
} from "@/lib/api";
import { cn } from "@/lib/utils";

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

/** 右栏「关系图谱」宽度（px）：默认值 + 拖拽下限，经中/右栏分隔条调整、双击复位。 */
const DEFAULT_GRAPH_WIDTH = 300;
const MIN_GRAPH_WIDTH = 220;

export default function ReportDetailPage() {
  const params = useParams();
  const searchParams = useSearchParams();
  const queryClient = useQueryClient();
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

  // 中栏「文档预览」↔ 右栏「关系图谱」之间的可拖拽分隔条：以 CSS 变量驱动右栏宽度，
  // 仅桌面端（lg）生效；移动端两栏纵向堆叠、分隔条隐藏。
  const layoutRef = useRef<HTMLDivElement>(null);
  const [graphWidth, setGraphWidth] = useState(DEFAULT_GRAPH_WIDTH);
  const [resizing, setResizing] = useState(false);
  const [rerunError, setRerunError] = useState<string | null>(null);
  const [decisionError, setDecisionError] = useState<string | null>(null);

  const contentQuery = useQuery({
    queryKey: documentContentKey(item),
    queryFn: () => resolveDocumentContent(item as ReportOrDocument),
    enabled: Boolean(item) && isDoc,
  });
  const documentContent =
    contentQuery.data && "content" in contentQuery.data ? contentQuery.data.content : null;
  // 文档分类 + 抽取关系与预览同源（同一次标注请求）——供右侧「关系图谱」面板展示。
  const docClass =
    contentQuery.data && "docClass" in contentQuery.data ? contentQuery.data.docClass : null;
  // 稳定引用（空数组字面量每次渲染都是新引用，会令下方 useMemo/查询依赖每帧变化）。
  const relationships = useMemo(
    () =>
      contentQuery.data && "relationships" in contentQuery.data
        ? contentQuery.data.relationships
        : [],
    [contentQuery.data],
  );
  // 决策端点以 jobId 定位（与关系图谱同源）；仅当关系载荷携带 PDE 冲突时才拉取/展示决策。
  const jobId =
    contentQuery.data && "jobId" in contentQuery.data ? contentQuery.data.jobId : null;
  const hasConflict = useMemo(
    () => relationships.some((r) => Boolean(r.conflict)),
    [relationships],
  );

  const decisionKey = useMemo(() => ["pde-conflict-decision", jobId] as const, [jobId]);
  const decisionQuery = useQuery({
    queryKey: decisionKey,
    queryFn: () => getPdeConflictDecision(jobId as string),
    enabled: Boolean(jobId) && hasConflict,
  });
  const decision = decisionQuery.data ?? null;

  // CAS 写入：以当前已知 version 提交；版本冲突（他人已改）→ 刷新决策后提示重试。
  const decide = useMutation({
    mutationFn: (chosen: PdeDecisionChoice) =>
      decidePdeConflict(jobId as string, { chosen, expected_version: decision?.version ?? 0 }),
    onMutate: () => setDecisionError(null),
    onSuccess: (updated) => queryClient.setQueryData(decisionKey, updated),
    onError: (err) => {
      queryClient.invalidateQueries({ queryKey: decisionKey });
      setDecisionError(
        err instanceof VersionConflictError
          ? "决策已被他人更新，已为你刷新最新状态，请重试。"
          : "决策保存失败，请重试。",
      );
    },
  });

  const download = useMutation({
    mutationFn: async () => {
      if (!item || item.kind !== "generated-report" || !item.jobId || !item.reportId) {
        throw new Error("该条目不支持下载");
      }
      const blob = await downloadReportById(item.jobId, item.reportId);
      saveBlob(blob, item.title || item.key);
    },
  });

  // 重新识别：丢弃后端标注缓存并全量重跑三阶段标注，再阻塞式 refetch 同源文档内容。
  // rerunAnnotation 删缓存后，getAnnotatedDocument 的 GET 因缓存缺失同步重算并回填，故一次
  // refetch 即「等待→拿到新结果」；documentContentKey 同键同源，中栏预览与右栏关系图谱一并刷新。
  const rerun = useMutation({
    mutationFn: async () => {
      if (!item || item.kind !== "uploaded-document" || !item.iri) {
        throw new Error("仅支持对上传文档重新识别");
      }
      const jobId = await resolveDocumentJobId(item.iri);
      if (!jobId) throw new Error("该文档未关联抽取任务，无法重新识别");
      await rerunAnnotation(jobId);
      await queryClient.refetchQueries({ queryKey: documentContentKey(item) });
    },
    onMutate: () => setRerunError(null),
    onError: () => setRerunError("重新识别失败，请重试或检查源文档是否仍可用"),
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

  // 拖拽分隔条：以指针到容器右缘的距离反推右栏宽度，钳制到 [下限, 容器宽-580]
  // （为左侧目录 + 中栏预览预留最小空间）；监听挂在 window 上，拖出面板也不丢事件。
  const startResize = useCallback((e: ReactMouseEvent) => {
    e.preventDefault();
    const container = layoutRef.current;
    if (!container) return;
    const rect = container.getBoundingClientRect();
    setResizing(true);
    const onMove = (ev: MouseEvent) => {
      const raw = rect.right - ev.clientX;
      const max = Math.max(MIN_GRAPH_WIDTH, rect.width - 580);
      setGraphWidth(Math.min(max, Math.max(MIN_GRAPH_WIDTH, raw)));
    };
    const onUp = () => {
      setResizing(false);
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
  }, []);
  const resetGraphWidth = useCallback(() => setGraphWidth(DEFAULT_GRAPH_WIDTH), []);

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
    // 桌面端（lg）：整页固定为「外壳内容区」高度（100vh − 顶栏 56px − main 内边距 3rem），
    // 面包屑/标题占顶部、下方内容区 flex-1 撑满，各栏内部各自滚动——不再把整页撑出纵向滚动条。
    // 移动端：不加高度约束，纵向堆叠、随页面自然滚动（沿用原行为）。
    <div className="flex flex-col gap-6 lg:h-[calc(100vh-56px-3rem)] lg:overflow-hidden">
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
          {isDoc && <DocumentActionsMenu item={item} />}
          <Button variant="outline" onClick={handleShare}>
            {copied ? <Check /> : <Share2 />}
            {copied ? "已复制链接" : "分享"}
          </Button>
        </div>
      </header>

      {download.isError && (
        <p className="text-sm text-destructive">下载失败，请稍后重试。</p>
      )}

      <div
        ref={layoutRef}
        className="flex flex-col gap-6 lg:min-h-0 lg:flex-1 lg:flex-row lg:gap-0"
      >
        <Card className="lg:mr-6 lg:flex lg:h-full lg:w-[220px] lg:shrink-0 lg:flex-col lg:overflow-hidden">
          <CardHeader className="p-4 pb-2">
            <CardTitle className="text-sm">目录</CardTitle>
          </CardHeader>
          <CardContent className="p-2 pt-0 lg:min-h-0 lg:flex-1 lg:overflow-y-auto">
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

        <Card className="min-w-0 lg:flex lg:h-full lg:flex-1 lg:flex-col lg:overflow-hidden">
          {/* 中栏「文档预览」：桌面端内容超出时在此卡片内部滚动（lg:overflow-y-auto），
              不再撑开整页；WordViewer 的证据高亮 scrollIntoView 定位到本滚动容器内。 */}
          <CardContent className="p-6 lg:min-h-0 lg:flex-1 lg:overflow-y-auto">
            <ReadingPane item={item} highlightRef={highlightRef} />
          </CardContent>
        </Card>

        {/* 中栏预览 ↔ 右栏关系图谱 之间的分隔条：拖拽调宽、双击复位；仅桌面端（lg）显示，
            命中区沿整条边界铺满，正文任意滚动位置皆可抓取。 */}
        <div
          role="separator"
          aria-orientation="vertical"
          onMouseDown={startResize}
          onDoubleClick={resetGraphWidth}
          title="拖动调整宽度 · 双击复位"
          className={cn(
            "group relative hidden w-px shrink-0 cursor-col-resize self-stretch bg-border transition-colors lg:mx-3 lg:block",
            resizing ? "bg-primary" : "hover:bg-primary/50",
          )}
        >
          <div className="absolute inset-y-0 -left-1.5 -right-1.5 z-10" />
          <div
            className={cn(
              "absolute left-1/2 top-1/2 z-20 flex h-8 w-3 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full border bg-background shadow-sm transition-opacity",
              resizing ? "opacity-100" : "opacity-0 group-hover:opacity-100",
            )}
          >
            <GripVertical className="size-3 text-muted-foreground" />
          </div>
        </div>

        {/* 右栏「关系图谱」：复用报告模板管理·源文档页签的 RelationPanel，同一次标注结果
            驱动；点击关系端点经 highlightRef 联动中栏正文预览高亮定位原文。宽度经上方
            分隔条拖拽调整（CSS 变量 --graph-w，仅 lg 生效；移动端整宽堆叠）。 */}
        <Card
          style={{ "--graph-w": `${graphWidth}px` } as CSSProperties}
          className="w-full lg:flex lg:h-full lg:w-[var(--graph-w)] lg:shrink-0 lg:flex-col lg:overflow-hidden"
        >
          <CardHeader className="p-4 pb-2">
            <div className="flex items-center justify-between gap-2">
              <CardTitle className="text-sm">关系图谱</CardTitle>
              {/* 重新识别：对当前文档全量重跑标注，刷新中栏预览与本图谱（同源同键）。 */}
              {isDoc && (
                <Button
                  variant="outline"
                  size="sm"
                  className="h-7 gap-1.5 text-xs"
                  disabled={!documentContent || rerun.isPending}
                  title={
                    !documentContent
                      ? "暂无可重识别的标注"
                      : "对当前文档重新完整标注（实体+关系），较慢"
                  }
                  onClick={() => rerun.mutate()}
                >
                  {rerun.isPending ? (
                    <Loader2 className="size-3.5 animate-spin" />
                  ) : (
                    <RotateCw className="size-3.5" />
                  )}
                  {rerun.isPending ? "识别中…" : "重新识别"}
                </Button>
              )}
            </div>
            {rerunError && <p className="mt-1 text-xs text-destructive">{rerunError}</p>}
            {decisionError && <p className="mt-1 text-xs text-destructive">{decisionError}</p>}
          </CardHeader>
          <CardContent className="p-0 pb-2 lg:min-h-0 lg:flex-1 lg:overflow-hidden">
            {!isDoc ? (
              <div className="px-4 pb-2">
                <EmptyState
                  icon={<Link2 />}
                  title="无关系图谱"
                  description="生成报告不包含从源文档识别的关系图谱。"
                />
              </div>
            ) : contentQuery.isLoading ? (
              <div className="flex items-center gap-2 px-4 pb-2 text-sm text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" />
                正在识别关系…
              </div>
            ) : contentQuery.isError ? (
              <div className="px-4 pb-2">
                <Alert variant="destructive">
                  <AlertCircle className="h-4 w-4" />
                  <AlertTitle>关系图谱加载失败</AlertTitle>
                  <AlertDescription>无法读取该文档的识别关系。</AlertDescription>
                </Alert>
              </div>
            ) : (
              <div className="relative max-h-[70vh] overflow-y-auto lg:h-full lg:max-h-none lg:overflow-hidden">
                <RelationPanel
                  docClass={docClass}
                  relationships={relationships}
                  selectedSourceRef={highlightRef}
                  onSelectSourceRef={setHighlightRef}
                  decision={decision}
                  onDecide={(chosen) => decide.mutate(chosen)}
                  decisionPending={decide.isPending}
                />
                {rerun.isPending && (
                  <div className="absolute inset-0 z-10 flex flex-col items-center justify-center gap-2 bg-background/70 backdrop-blur-sm">
                    <Loader2 className="size-5 animate-spin text-muted-foreground" />
                    <span className="text-xs text-muted-foreground">正在重新识别关系图谱…</span>
                  </div>
                )}
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
