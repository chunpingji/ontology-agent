"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
  type DragEvent,
  type ReactNode,
} from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import {
  Ban,
  BookOpen,
  Clock3,
  File,
  FileText,
  FolderTree,
  GitBranch,
  Info,
  ListTree,
  Loader2,
  PanelRight,
  Pause,
  Play,
  RefreshCw,
  Trash2,
  TriangleAlert,
  UploadCloud,
} from "lucide-react";

import { DocumentRelationshipGraph } from "@/components/analysis/document-relationship-graph";
import { WordViewer, type DocumentLocation } from "@/components/extraction/word-viewer";
import { TreeView, type TreeDataItem } from "@/components/tree-view";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  controlDocumentAnalysisRun,
  createDocumentAnalysisRun,
  deleteDocumentAnalysisRun,
  getAllClassesWithSignal,
  getDocumentAnalysisGraph,
  getDocumentAnalysisMetadata,
  getDocumentAnalysisRun,
  getDocumentAnalysisSource,
  mergeDocumentAnalysisControlReceipt,
  shouldSubscribeDocumentAnalysisEvents,
  subscribeDocumentAnalysisEvents,
  type DocumentAnalysisControlAction,
  type DocumentAnalysisGraphArtifact,
  type DocumentAnalysisMetadataArtifact,
  type DocumentAnalysisRun,
  type DocumentAnalysisStatus,
  type DocumentGraphProjection,
  type EvidenceAnchor,
  type OntologyClassFlat,
  type SummarySource,
  type SummaryStatus,
  type WordChapterNode,
  type WordPageNode,
  type WordPaginationMetadata,
  type WordSourceRange,
} from "@/lib/api";
import { cn } from "@/lib/utils";

type SelectedTreeNode =
  | { kind: "chapter"; node: WordChapterNode }
  | { kind: "page"; node: WordPageNode; parent: WordChapterNode };
type InnerTab = "metadata" | "graph";

const SUMMARY_SOURCE_LABELS: Record<SummarySource, string> = {
  llm: "AI 生成",
  extractive_fallback: "自动摘录",
  empty: "无可摘要内容",
  none: "未生成",
};

const SUMMARY_STATUS_LABELS: Record<SummaryStatus, string> = {
  pending: "待生成",
  completed: "已完成",
  partial: "部分完成",
  disabled: "未启用",
  failed: "生成失败",
};

const PAGINATION_MODE_LABELS: Record<WordPaginationMetadata["mode"], string> = {
  rendered_markers: "Word 渲染分页标记",
  explicit_markers: "显式分页标记",
  single_page_fallback: "逻辑章节页",
};

const BREAK_SOURCE_LABELS: Record<string, string> = {
  manual: "手动分页",
  pageBreakBefore: "段前分页",
  section: "分页型分节",
  lastRendered: "Word 渲染分页",
};

const RUN_STATUS_LABELS: Record<DocumentAnalysisStatus, string> = {
  queued: "已排队",
  running: "运行中",
  paused: "已暂停",
  finished: "已完成",
  retryable_failure: "可恢复失败",
  blocked_dependency: "依赖阻塞",
  cancelled: "已取消",
  deleting: "正在删除",
  deleted: "已删除",
  expired: "已过期",
};

const STAGE_LABELS: Record<string, string> = {
  accepted: "已受理",
  storing_source: "保存源文件",
  converting: "转换文档",
  parsing: "解析结构",
  preparing_metadata: "生成分层元数据",
  freezing_inputs: "冻结运行输入",
  planning: "规划识别任务",
  extracting: "识别关系图谱",
  projecting: "生成图谱投影",
  finalizing: "提交最终快照",
  complete: "处理完成",
};

const ARTIFACT_LABELS = {
  pending: "等待中",
  ready: "已就绪",
  partial: "部分可用",
  failed: "失败",
} as const;

function errorMessage(error: unknown): string {
  if (!(error instanceof Error)) return "请求失败，请稍后重试。";
  const apiBody = error.message.startsWith("API ") && error.message.includes(":")
    ? error.message.slice(error.message.indexOf(":") + 1).trim()
    : null;
  if (apiBody) {
    try {
      const parsed = JSON.parse(apiBody) as {
        detail?: unknown;
        error?: { message?: unknown };
      };
      const detail = parsed.detail ?? parsed.error?.message;
      if (typeof detail === "string") return detail;
    } catch {
      // Keep the transport error when an upstream proxy did not return JSON.
    }
  }
  return error.message;
}

function metadataError(error: DocumentAnalysisMetadataArtifact["error"]): string | null {
  if (!error) return null;
  return error.safe_detail || error.code || "分层元数据不可用。";
}

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

function isUnavailableRunError(error: unknown): boolean {
  return error instanceof Error && /^API (404|410):/.test(error.message);
}

function pageLabel(page: WordPageNode): string {
  return page.physical_page_number == null
    ? `章节页 ${page.ordinal_in_leaf}`
    : `第 ${page.physical_page_number} 页`;
}

function buildTreeIndex(root: WordChapterNode): Map<string, SelectedTreeNode> {
  const index = new Map<string, SelectedTreeNode>();
  const visit = (chapter: WordChapterNode) => {
    index.set(chapter.node_id, { kind: "chapter", node: chapter });
    chapter.pages.forEach((page) => {
      index.set(page.node_id, { kind: "page", node: page, parent: chapter });
    });
    chapter.children.forEach(visit);
  };
  visit(root);
  return index;
}

function chapterToTreeItem(chapter: WordChapterNode): TreeDataItem {
  const children = chapter.children.map(chapterToTreeItem);
  if (chapter.is_leaf && chapter.pages.length > 1) {
    children.push(...chapter.pages.map((page) => ({
      id: page.node_id,
      name: pageLabel(page),
      icon: File,
    })));
  }
  return {
    id: chapter.node_id,
    name: chapter.heading || "未命名章节",
    icon: chapter.node_type === "document"
      ? BookOpen
      : children.length > 0
        ? FolderTree
        : FileText,
    children: children.length > 0 ? children : undefined,
  };
}

function selectedLocation(selected: SelectedTreeNode): DocumentLocation {
  if (selected.kind === "page") {
    return {
      kind: "page",
      nodeId: selected.node.node_id,
      anchorBlockId: selected.node.source_range.anchor_block_id,
      startBlockId: selected.node.source_range.start_block_id,
      endBlockId: selected.node.source_range.end_block_id,
      blockIds: selected.node.block_ids,
    };
  }
  if (selected.node.node_type === "document") {
    return { kind: "document", nodeId: selected.node.node_id };
  }
  return {
    kind: "section",
    nodeId: selected.node.node_id,
    anchorBlockId: selected.node.source_range.anchor_block_id,
    startBlockId: selected.node.source_range.start_block_id,
    endBlockId: selected.node.source_range.end_block_id,
  };
}

function SummaryBadge({ source }: { source: SummarySource }) {
  return <Badge variant={source === "llm" ? "default" : "outline"}>{SUMMARY_SOURCE_LABELS[source]}</Badge>;
}

function MetaRow({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="grid grid-cols-[6.5rem_minmax(0,1fr)] gap-3 text-sm">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="min-w-0 break-words text-foreground">{value}</dd>
    </div>
  );
}

function SourceRangeDetails({ range }: { range: WordSourceRange }) {
  return (
    <dl className="space-y-2">
      <MetaRow label="锚点块" value={range.anchor_block_id ?? "—"} />
      <MetaRow label="起始块" value={range.start_block_id ?? "—"} />
      <MetaRow label="结束块" value={range.end_block_id ?? "—"} />
      <MetaRow label="标题索引" value={range.heading_index ?? "—"} />
    </dl>
  );
}

function ChapterMetadata({ chapter }: { chapter: WordChapterNode }) {
  const meta = chapter.layer_metadata;
  return (
    <div className="space-y-5">
      <div>
        <div className="mb-2 flex flex-wrap items-center gap-2">
          <Badge variant="secondary">{chapter.node_type === "document" ? "文档根" : `${chapter.level} 级章节`}</Badge>
          <SummaryBadge source={meta.summary_source} />
        </div>
        <h3 className="break-words text-base font-semibold">{chapter.heading}</h3>
        {chapter.path.length > 0 && <p className="mt-1 break-words text-xs text-muted-foreground">{chapter.path.join(" / ")}</p>}
      </div>
      <section className="space-y-2">
        <h4 className="text-sm font-medium">内容摘要</h4>
        <p className="whitespace-pre-wrap rounded-lg bg-muted/50 p-3 text-sm leading-relaxed">{meta.content_summary || "暂无摘要。"}</p>
        <dl className="space-y-2">
          <MetaRow label="摘要状态" value={SUMMARY_STATUS_LABELS[meta.summary_status]} />
          <MetaRow label="摘要模型" value={meta.summary_model ?? "—"} />
          <MetaRow label="Prompt 版本" value={meta.prompt_version} />
          <MetaRow label="生成时间" value={meta.generated_at ?? "—"} />
        </dl>
      </section>
      <section className="space-y-2">
        <h4 className="text-sm font-medium">结构统计</h4>
        <dl className="space-y-2">
          <MetaRow label="直接段落" value={meta.direct_paragraph_count} />
          <MetaRow label="直接表格" value={meta.direct_table_count} />
          <MetaRow label="后代章节" value={meta.descendant_section_count} />
          <MetaRow label="叶章节" value={meta.leaf_count} />
          <MetaRow label="章节页" value={meta.page_count} />
        </dl>
      </section>
      <section className="space-y-2">
        <h4 className="text-sm font-medium">来源范围</h4>
        <SourceRangeDetails range={chapter.source_range} />
      </section>
      <section className="space-y-2">
        <h4 className="text-sm font-medium">标识与校验</h4>
        <dl className="space-y-2 font-mono text-xs">
          <MetaRow label="节点 ID" value={chapter.node_id} />
          <MetaRow label="内容哈希" value={meta.content_hash || "—"} />
        </dl>
      </section>
    </div>
  );
}

function PageMetadata({ page, parent }: { page: WordPageNode; parent: WordChapterNode }) {
  const meta = page.page_metadata;
  return (
    <div className="space-y-5">
      <div>
        <div className="mb-2 flex flex-wrap items-center gap-2"><Badge variant="secondary">{pageLabel(page)}</Badge><SummaryBadge source={meta.summary_source} /></div>
        <h3 className="break-words text-base font-semibold">{parent.heading}</h3>
        <p className="mt-1 text-xs text-muted-foreground">叶章节内容片段</p>
      </div>
      <section className="space-y-2">
        <h4 className="text-sm font-medium">内容摘要</h4>
        <p className="whitespace-pre-wrap rounded-lg bg-muted/50 p-3 text-sm leading-relaxed">{meta.content_summary || "暂无摘要。"}</p>
        <dl className="space-y-2">
          <MetaRow label="摘要状态" value={SUMMARY_STATUS_LABELS[meta.summary_status]} />
          <MetaRow label="摘要模型" value={meta.summary_model ?? "—"} />
          <MetaRow label="Prompt 版本" value={meta.prompt_version} />
          <MetaRow label="生成时间" value={meta.generated_at ?? "—"} />
        </dl>
      </section>
      <section className="space-y-2">
        <h4 className="text-sm font-medium">页片段统计</h4>
        <dl className="space-y-2">
          <MetaRow label="页序号" value={page.ordinal_in_leaf} />
          <MetaRow label="物理页码" value={page.physical_page_number ?? "不可用"} />
          <MetaRow label="分页来源" value={page.break_source ? BREAK_SOURCE_LABELS[page.break_source] ?? page.break_source : "—"} />
          <MetaRow label="段落" value={meta.paragraph_count} />
          <MetaRow label="表格" value={meta.table_count} />
          <MetaRow label="字符" value={meta.character_count} />
          <MetaRow label="来源块" value={page.block_ids.length} />
        </dl>
      </section>
      <section className="space-y-2"><h4 className="text-sm font-medium">来源范围</h4><SourceRangeDetails range={page.source_range} /></section>
      <section className="space-y-2">
        <h4 className="text-sm font-medium">标识与校验</h4>
        <dl className="space-y-2 font-mono text-xs"><MetaRow label="节点 ID" value={page.node_id} /><MetaRow label="内容哈希" value={meta.content_hash || "—"} /></dl>
      </section>
    </div>
  );
}

function MetadataPanel({ selected, pagination }: { selected: SelectedTreeNode; pagination?: WordPaginationMetadata | null }) {
  return (
    <div className="space-y-5">
      {pagination && (
        <section className="space-y-2 rounded-lg border bg-muted/20 p-3">
          <h3 className="text-sm font-medium">分页可信度</h3>
          <dl className="space-y-2">
            <MetaRow label="分页模式" value={PAGINATION_MODE_LABELS[pagination.mode]} />
            <MetaRow label="物理页码" value={pagination.physical_page_numbers_available ? "可用" : "不可用"} />
            <MetaRow label="是否估算" value={pagination.is_estimated ? "是" : "否"} />
          </dl>
          {pagination.warning && <p className="text-xs leading-relaxed text-muted-foreground">{pagination.warning}</p>}
        </section>
      )}
      {selected.kind === "chapter" ? <ChapterMetadata chapter={selected.node} /> : <PageMetadata page={selected.node} parent={selected.parent} />}
    </div>
  );
}

function ChapterTreePanel({ tree, selectedNodeId, onSelect }: { tree: TreeDataItem; selectedNodeId: string; onSelect: (nodeId: string) => void }) {
  return (
    <TreeView
      data={tree}
      initialSelectedItemId={selectedNodeId}
      onSelectChange={(item) => item && onSelect(item.id)}
      className="min-w-0"
      aria-label="Word 章节树"
    />
  );
}

function PanelCard({ title, children, className }: { title: string; children: ReactNode; className?: string }) {
  return (
    <Card className={cn("min-h-0 overflow-hidden", className)}>
      <CardHeader className="border-b p-4"><CardTitle className="text-sm">{title}</CardTitle></CardHeader>
      <CardContent className="h-[calc(100%-3.25rem)] overflow-y-auto p-3">{children}</CardContent>
    </Card>
  );
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatDate(value: string | null): string {
  if (!value) return "运行中不自动到期";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString("zh-CN", { hour12: false });
}

function uniqueRequestKey(prefix: string): string {
  const random = globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `${prefix}:${random}`;
}

function classLabel(item: OntologyClassFlat): string {
  return item.label || item.name || item.iri;
}

function isOlderWatermark(
  incoming: { run_revision: number; artifact_revision: number; event_head: number },
  current: { run_revision: number; artifact_revision: number; event_head: number },
): boolean {
  return incoming.run_revision < current.run_revision
    || incoming.artifact_revision < current.artifact_revision
    || incoming.event_head < current.event_head;
}

export function DocumentAnalysisPanel() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const inputRef = useRef<HTMLInputElement>(null);
  const createControllerRef = useRef<AbortController | null>(null);
  const sourceControllerRef = useRef<AbortController | null>(null);
  const createSequenceRef = useRef(0);

  const [sourceFile, setSourceFile] = useState<File | null>(null);
  const [rootClassIri, setRootClassIri] = useState("");
  const [classSearch, setClassSearch] = useState("");
  const [classes, setClasses] = useState<OntologyClassFlat[]>([]);
  const [classesError, setClassesError] = useState<string | null>(null);
  const [classesLoading, setClassesLoading] = useState(true);
  const [draftRequestKey, setDraftRequestKey] = useState<string | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const [isCreating, setIsCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const [createdRunId, setCreatedRunId] = useState<string | null>(null);

  const urlRunId = searchParams.get("documentRun")?.trim() || null;
  const activeRunId = urlRunId || createdRunId;
  const activeRunIdRef = useRef<string | null>(activeRunId);
  const eventHeadRef = useRef<{ runId: string | null; value: number }>({ runId: null, value: 0 });

  const [runState, setRunState] = useState<DocumentAnalysisRun | null>(null);
  const [metadataState, setMetadataState] = useState<DocumentAnalysisMetadataArtifact | null>(null);
  const [graphState, setGraphState] = useState<{ runId: string; projection: DocumentGraphProjection; value: DocumentAnalysisGraphArtifact } | null>(null);
  const [pollError, setPollError] = useState<{ runId: string; message: string; unavailable: boolean } | null>(null);
  const [pollNonce, setPollNonce] = useState(0);
  const [projection, setProjection] = useState<DocumentGraphProjection>("effective_affirmed");
  const [innerTab, setInnerTab] = useState<InnerTab>("metadata");
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [selectedSelectionRef, setSelectedSelectionRef] = useState<{ runId: string; value: string } | null>(null);
  const [sourceReplay, setSourceReplay] = useState<{
    runId: string;
    selectionRef: string;
    content: Record<string, unknown> | null;
    anchor: EvidenceAnchor | null;
    sectionNodeId: string | null;
    spanCount: number;
  } | null>(null);
  const [sourceLoading, setSourceLoading] = useState(false);
  const [sourceError, setSourceError] = useState<string | null>(null);
  const [controlBusy, setControlBusy] = useState<DocumentAnalysisControlAction | "delete" | null>(null);
  const [controlError, setControlError] = useState<string | null>(null);
  const eventStreamStatus = runState?.recognition_run_id === activeRunId ? runState.status : null;
  const eventStreamShouldConnect = shouldSubscribeDocumentAnalysisEvents(eventStreamStatus);

  useEffect(() => {
    activeRunIdRef.current = activeRunId;
    if (eventHeadRef.current.runId !== activeRunId) {
      eventHeadRef.current = { runId: activeRunId, value: 0 };
    }
  }, [activeRunId]);

  useEffect(() => {
    if (!activeRunId || !eventStreamShouldConnect) return;
    const runId = activeRunId;
    return subscribeDocumentAnalysisEvents(runId, (event) => {
      if (activeRunIdRef.current !== runId || event.recognition_run_id !== runId) return;
      if (event.event_head <= eventHeadRef.current.value) return;
      eventHeadRef.current = { runId, value: event.event_head };
      setPollNonce((value) => value + 1);
    });
  }, [activeRunId, eventStreamShouldConnect]);

  useEffect(() => {
    const controller = new AbortController();
    getAllClassesWithSignal(controller.signal)
      .then((items) => {
        setClasses([...items].sort((a, b) => classLabel(a).localeCompare(classLabel(b), "zh-Hans-CN")));
        setClassesError(null);
      })
      .catch((error: unknown) => {
        if (!isAbortError(error)) setClassesError(errorMessage(error));
      })
      .finally(() => {
        if (!controller.signal.aborted) setClassesLoading(false);
      });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (!activeRunId) return;
    const runId = activeRunId;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | null = null;
    let stopped = false;

    const poll = async () => {
      const [runResult, metadataResult, graphResult] = await Promise.allSettled([
        getDocumentAnalysisRun(runId, controller.signal),
        getDocumentAnalysisMetadata(runId, controller.signal),
        getDocumentAnalysisGraph(runId, projection, controller.signal),
      ]);
      if (stopped || activeRunIdRef.current !== runId) return;

      let nextStatus: DocumentAnalysisStatus | null = null;
      if (runResult.status === "fulfilled" && runResult.value.recognition_run_id === runId) {
        const incoming = runResult.value;
        nextStatus = incoming.status;
        if (eventHeadRef.current.runId === runId) {
          eventHeadRef.current.value = Math.max(eventHeadRef.current.value, incoming.event_head);
        }
        setRunState((previous) => {
          if (
            previous?.recognition_run_id === runId
            && isOlderWatermark(incoming, previous)
          ) return previous;
          return incoming;
        });
        setPollError((previous) => previous?.runId === runId ? null : previous);
      } else if (runResult.status === "rejected" && !isAbortError(runResult.reason)) {
        const unavailable = isUnavailableRunError(runResult.reason);
        setPollError({ runId, message: errorMessage(runResult.reason), unavailable });
        if (unavailable) {
          sourceControllerRef.current?.abort();
          setRunState((previous) => previous?.recognition_run_id === runId ? null : previous);
          setMetadataState((previous) => previous?.recognition_run_id === runId ? null : previous);
          setGraphState((previous) => previous?.runId === runId ? null : previous);
          setSourceReplay((previous) => previous?.runId === runId ? null : previous);
          setSelectedSelectionRef((previous) => previous?.runId === runId ? null : previous);
        }
      }

      if (metadataResult.status === "fulfilled" && metadataResult.value.recognition_run_id === runId) {
        const incoming = metadataResult.value;
        setMetadataState((previous) => {
          if (
            previous?.recognition_run_id === runId
            && isOlderWatermark(incoming, previous)
          ) return previous;
          return incoming;
        });
      }

      if (graphResult.status === "fulfilled" && graphResult.value.recognition_run_id === runId && graphResult.value.projection === projection) {
        const incoming = graphResult.value;
        setGraphState((previous) => {
          if (
            previous?.runId === runId
            && previous.projection === projection
            && isOlderWatermark(incoming, previous.value)
          ) return previous;
          return { runId, projection, value: incoming };
        });
      }

      if (!stopped) {
        const terminal = nextStatus && ["finished", "cancelled", "deleted", "expired"].includes(nextStatus);
        timer = setTimeout(poll, terminal ? 10_000 : 2_000);
      }
    };

    void poll();
    return () => {
      stopped = true;
      controller.abort();
      if (timer) clearTimeout(timer);
    };
  }, [activeRunId, pollNonce, projection]);

  const currentRun = runState?.recognition_run_id === activeRunId ? runState : null;
  const currentMetadata = metadataState?.recognition_run_id === activeRunId ? metadataState : null;
  const currentGraph = graphState?.runId === activeRunId && graphState.projection === projection ? graphState.value : null;
  const currentReplay = sourceReplay?.runId === activeRunId ? sourceReplay : null;
  const currentSelectionRef = selectedSelectionRef?.runId === activeRunId ? selectedSelectionRef.value : null;

  const filteredClasses = useMemo(() => {
    const query = classSearch.trim().toLocaleLowerCase();
    if (!query) return classes;
    return classes.filter((item) => (
      classLabel(item).toLocaleLowerCase().includes(query)
      || item.name.toLocaleLowerCase().includes(query)
      || item.iri.toLocaleLowerCase().includes(query)
    ));
  }, [classSearch, classes]);
  const selectedClass = classes.find((item) => item.iri === rootClassIri) ?? null;
  const classOptions = selectedClass && !filteredClasses.some((item) => item.iri === selectedClass.iri)
    ? [selectedClass, ...filteredClasses]
    : filteredClasses;

  const chapterTree = currentMetadata?.section_tree ?? null;
  const nodeIndex = useMemo(() => chapterTree ? buildTreeIndex(chapterTree) : new Map<string, SelectedTreeNode>(), [chapterTree]);
  const displayTree = useMemo(() => chapterTree ? chapterToTreeItem(chapterTree) : null, [chapterTree]);
  const selectedNode = chapterTree
    ? nodeIndex.get(selectedNodeId ?? "") ?? nodeIndex.get(chapterTree.node_id) ?? null
    : null;
  const activeLocation = useMemo(() => selectedNode ? selectedLocation(selectedNode) : null, [selectedNode]);
  const previewContent = currentReplay?.content ?? currentMetadata?.content ?? null;
  const analysisKey = currentMetadata?.metadata_snapshot?.snapshot_id
    || currentMetadata?.analysis?.structure_hash
    || activeRunId
    || "document";

  const replaceRunInUrl = useCallback((runId: string | null) => {
    const params = new URLSearchParams(searchParams.toString());
    if (runId) {
      params.set("tab", "document");
      params.set("documentRun", runId);
    } else {
      params.delete("documentRun");
    }
    params.delete("job_id");
    params.delete("node_id");
    const query = params.toString();
    router.replace(query ? `${pathname}?${query}` : pathname, { scroll: false });
  }, [pathname, router, searchParams]);

  const chooseFile = useCallback((file: File) => {
    const suffix = file.name.includes(".") ? file.name.slice(file.name.lastIndexOf(".")).toLowerCase() : "";
    if (suffix !== ".doc" && suffix !== ".docx") {
      setCreateError("仅支持 .doc 或 .docx 文件。");
      return;
    }
    setSourceFile(file);
    setDraftRequestKey(null);
    setCreateError(null);
  }, []);

  const handleFileInput = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.currentTarget.files?.[0];
    event.currentTarget.value = "";
    if (file) chooseFile(file);
  };

  const handleDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setIsDragging(false);
    const file = event.dataTransfer.files?.[0];
    if (file) chooseFile(file);
  };

  const handleStart = async () => {
    if (!sourceFile || !rootClassIri || isCreating) return;
    const activeRunAtStart = activeRunIdRef.current;
    const requestKey = draftRequestKey ?? uniqueRequestKey("browser-document-run");
    setDraftRequestKey(requestKey);
    const requestSequence = ++createSequenceRef.current;
    createControllerRef.current?.abort();
    const controller = new AbortController();
    createControllerRef.current = controller;
    setIsCreating(true);
    setCreateError(null);
    try {
      const receipt = await createDocumentAnalysisRun(
        sourceFile,
        rootClassIri,
        requestKey,
        "generate_summary",
        controller.signal,
      );
      if (
        requestSequence !== createSequenceRef.current
        || activeRunIdRef.current !== activeRunAtStart
      ) return;
      setCreatedRunId(receipt.recognition_run_id);
      setDraftRequestKey(null);
      setSelectedNodeId(null);
      setSelectedSelectionRef(null);
      setSourceReplay(null);
      setProjection("effective_affirmed");
      setInnerTab("metadata");
      replaceRunInUrl(receipt.recognition_run_id);
    } catch (error) {
      if (requestSequence === createSequenceRef.current && !isAbortError(error)) {
        setCreateError(errorMessage(error));
      }
    } finally {
      if (requestSequence === createSequenceRef.current) setIsCreating(false);
    }
  };

  const handleControl = async (action: DocumentAnalysisControlAction | "delete") => {
    if (!currentRun || controlBusy) return;
    if (action === "cancel" && !window.confirm("取消后本运行不可恢复，已提交的快照仍会保留。确定取消吗？")) return;
    if (action === "delete" && !window.confirm("删除会清理本运行的独占源文件与产物，完成后不可恢复。确定删除吗？")) return;
    setControlBusy(action);
    setControlError(null);
    try {
      const requestKey = uniqueRequestKey(`document-run-${action}`);
      let receipt;
      if (action === "delete") {
        receipt = await deleteDocumentAnalysisRun(
          currentRun.recognition_run_id,
          currentRun.run_revision,
          requestKey,
        );
      } else {
        receipt = await controlDocumentAnalysisRun(
          currentRun.recognition_run_id,
          action,
          currentRun.run_revision,
          requestKey,
          `用户在文档分析页面请求${action}`,
        );
      }
      if (activeRunIdRef.current === currentRun.recognition_run_id) {
        setRunState((previous) => mergeDocumentAnalysisControlReceipt(previous, receipt));
        setPollNonce((value) => value + 1);
      }
    } catch (error) {
      setControlError(errorMessage(error));
    } finally {
      setControlBusy(null);
    }
  };

  const handleSelectionRef = async (selectionRef: string) => {
    const runId = activeRunIdRef.current;
    if (!runId) return;
    setSelectedSelectionRef({ runId, value: selectionRef });
    setSourceError(null);
    setSourceLoading(true);
    setInnerTab("metadata");
    sourceControllerRef.current?.abort();
    const controller = new AbortController();
    sourceControllerRef.current = controller;
    try {
      const source = await getDocumentAnalysisSource(runId, selectionRef, controller.signal);
      if (
        activeRunIdRef.current !== runId
        || source.recognition_run_id !== runId
        || sourceControllerRef.current !== controller
      ) return;
      if (currentMetadata?.analysis?.analysis_id && source.analysis_id !== currentMetadata.analysis.analysis_id) {
        throw new Error("来源定位与当前解析快照不一致，已拒绝显示。");
      }
      const selection = source.selection;
      const anchor = source.anchors[0] ?? null;
      if (selection?.section_node_id) setSelectedNodeId(selection.section_node_id);
      setSourceReplay({
        runId,
        selectionRef,
        content: source.content,
        anchor,
        sectionNodeId: selection?.section_node_id ?? null,
        spanCount: source.anchors.length || selection?.span_refs.length || 0,
      });
    } catch (error) {
      if (!isAbortError(error)) setSourceError(errorMessage(error));
    } finally {
      if (
        activeRunIdRef.current === runId
        && sourceControllerRef.current === controller
        && !controller.signal.aborted
      ) setSourceLoading(false);
    }
  };

  const selectTreeNode = (nodeId: string) => {
    setSelectedNodeId(nodeId);
    setSourceReplay(null);
    setSelectedSelectionRef(null);
    setSourceError(null);
  };

  const closeRunView = () => {
    createSequenceRef.current += 1;
    createControllerRef.current?.abort();
    sourceControllerRef.current?.abort();
    setCreatedRunId(null);
    setSelectedSelectionRef(null);
    setSourceReplay(null);
    replaceRunInUrl(null);
  };

  return (
    <div className="space-y-4">
      <input
        ref={inputRef}
        type="file"
        accept=".doc,.docx,application/msword,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        className="hidden"
        onChange={handleFileInput}
      />

      <Card aria-label="创建文档分析运行">
        <CardHeader className="border-b p-4">
          <CardTitle className="text-base">上传文档并指定本体类型</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4 p-4">
          <div className="grid gap-4 lg:grid-cols-[minmax(15rem,0.9fr)_minmax(20rem,1.1fr)_auto] lg:items-end">
            <div className="space-y-2">
              <span className="text-sm font-medium">Word 文件</span>
              <div
                role="button"
                tabIndex={0}
                onClick={() => inputRef.current?.click()}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    inputRef.current?.click();
                  }
                }}
                onDragOver={(event) => { event.preventDefault(); setIsDragging(true); }}
                onDragLeave={() => setIsDragging(false)}
                onDrop={handleDrop}
                className={cn(
                  "flex h-20 cursor-pointer items-center gap-3 rounded-lg border border-dashed px-4 outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring",
                  isDragging ? "border-primary bg-primary/5" : "hover:border-primary/50 hover:bg-muted/30",
                )}
              >
                <UploadCloud className="size-6 shrink-0 text-primary" />
                <span className="min-w-0">
                  <span className="block truncate text-sm font-medium">{sourceFile?.name || "选择或拖入 .doc / .docx"}</span>
                  <span className="block text-xs text-muted-foreground">{sourceFile ? formatFileSize(sourceFile.size) : "选择文件不会上传或启动分析"}</span>
                </span>
              </div>
            </div>

            <div className="space-y-2">
              <label htmlFor="document-root-class-search" className="text-sm font-medium">本体类型（必选）</label>
              <div className="grid h-20 grid-rows-2 gap-2">
                <Input
                  id="document-root-class-search"
                  value={classSearch}
                  onChange={(event) => setClassSearch(event.target.value)}
                  placeholder="搜索类型标签、名称或完整 IRI"
                  aria-label="搜索本体类型"
                />
                <select
                  aria-label="选择本体类型"
                  value={rootClassIri}
                  onChange={(event) => {
                    setRootClassIri(event.target.value);
                    setDraftRequestKey(null);
                    setCreateError(null);
                  }}
                  disabled={classesLoading || Boolean(classesError)}
                  className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm"
                >
                  <option value="">{classesLoading ? "正在读取本体类型…" : "请选择根类型"}</option>
                  {classOptions.map((item) => <option key={item.iri} value={item.iri}>{classLabel(item)} · {item.iri}</option>)}
                </select>
              </div>
            </div>

            <Button
              type="button"
              size="lg"
              disabled={!sourceFile || !rootClassIri || isCreating}
              onClick={() => void handleStart()}
              className="lg:mb-0.5"
            >
              {isCreating ? <Loader2 className="animate-spin" /> : <Play />}
              {isCreating ? "正在创建运行" : "开始分析"}
            </Button>
          </div>

          {selectedClass && (
            <div className="rounded-md bg-muted/40 p-2 text-xs">
              <span className="font-medium">已选：{classLabel(selectedClass)}</span>
              <span className="ml-2 break-all font-mono text-muted-foreground">{selectedClass.iri}</span>
            </div>
          )}
          {classesError && <Alert variant="destructive"><TriangleAlert className="size-4" /><AlertTitle>本体类型加载失败</AlertTitle><AlertDescription>{classesError}</AlertDescription></Alert>}
          {createError && <Alert variant="destructive"><TriangleAlert className="size-4" /><AlertTitle>无法创建文档分析运行</AlertTitle><AlertDescription>{createError}</AlertDescription></Alert>}
          {currentRun && (
            <p className="text-xs text-muted-foreground">
              当前运行的文件与本体类型已冻结；上方选择仅作为下一次显式启动的草稿，不会修改或取消当前运行。
            </p>
          )}
          <p className="text-xs leading-relaxed text-muted-foreground">
            点击“开始分析”后才会上传文件，并在后台生成分层元数据与关系图谱。结果是系统分析产物，不会自动审核或提交业务事实；关闭页面不会取消运行，数据按页面显示的保留期限清理。
          </p>
        </CardContent>
      </Card>

      {activeRunId && !currentRun && pollError?.runId !== activeRunId && (
        <Card>
          <CardContent className="flex min-h-40 items-center justify-center gap-3 p-6 text-sm text-muted-foreground">
            <Loader2 className="size-5 animate-spin" />正在只读恢复运行 {activeRunId}
          </CardContent>
        </Card>
      )}

      {pollError?.runId === activeRunId && (
        <Alert variant="destructive">
          <TriangleAlert className="size-4" />
          <AlertTitle>{pollError.unavailable ? "分析结果已删除、过期或不可访问" : "运行读取失败"}</AlertTitle>
          <AlertDescription className="flex flex-wrap items-center justify-between gap-2">
            <span>{pollError.message}</span>
            <Button variant="outline" size="sm" onClick={() => setPollNonce((value) => value + 1)}><RefreshCw />重试只读请求</Button>
          </AlertDescription>
        </Alert>
      )}

      {currentRun && (
        <Card aria-label="文档分析运行状态">
          <CardContent className="space-y-4 p-4">
            <div className="flex flex-col gap-3 xl:flex-row xl:items-start xl:justify-between">
              <div className="min-w-0 space-y-2">
                <div className="flex flex-wrap items-center gap-2">
                  <p className="break-all text-sm font-semibold">{currentRun.input.filename}</p>
                  <Badge>{RUN_STATUS_LABELS[currentRun.status]}</Badge>
                  <Badge variant="outline">{STAGE_LABELS[currentRun.stage] || currentRun.stage}</Badge>
                </div>
                <p className="text-xs"><span className="font-medium">{currentRun.input.root_class_label}</span><span className="ml-2 break-all font-mono text-muted-foreground">{currentRun.input.root_class_iri}</span></p>
                <p className="break-all font-mono text-[11px] text-muted-foreground">recognition_run_id: {currentRun.recognition_run_id}</p>
              </div>
              <div className="flex flex-wrap gap-2">
                {currentRun.available_actions.includes("pause") && <Button size="sm" variant="outline" disabled={Boolean(controlBusy)} onClick={() => void handleControl("pause")}><Pause />{controlBusy === "pause" ? "正在请求" : "暂停"}</Button>}
                {currentRun.available_actions.includes("resume") && <Button size="sm" variant="outline" disabled={Boolean(controlBusy)} onClick={() => void handleControl("resume")}><Play />{controlBusy === "resume" ? "正在请求" : "恢复"}</Button>}
                {currentRun.available_actions.includes("cancel") && <Button size="sm" variant="outline" disabled={Boolean(controlBusy)} onClick={() => void handleControl("cancel")}><Ban />{controlBusy === "cancel" ? "正在请求" : "取消"}</Button>}
                {currentRun.available_actions.includes("delete") && <Button size="sm" variant="destructive" disabled={Boolean(controlBusy)} onClick={() => void handleControl("delete")}><Trash2 />{controlBusy === "delete" ? "正在请求" : "删除"}</Button>}
                <Button size="sm" variant="ghost" onClick={closeRunView}>关闭视图</Button>
              </div>
            </div>

            <div className="grid gap-2 text-xs sm:grid-cols-2 xl:grid-cols-4">
              <div className="rounded-md bg-muted/40 p-2"><span className="text-muted-foreground">运行水位</span><strong className="ml-2">revision {currentRun.run_revision} / event {currentRun.event_head}</strong></div>
              <div className="rounded-md bg-muted/40 p-2"><span className="text-muted-foreground">记录覆盖</span><strong className="ml-2">{currentRun.progress.records_examined} 已检 / {currentRun.progress.records_incomplete} 未完成 / {currentRun.progress.records_unattempted} 未尝试</strong></div>
              <div className="rounded-md bg-muted/40 p-2"><span className="text-muted-foreground">判定</span><strong className="ml-2">{currentRun.progress.decisions.supported} 支持 / {currentRun.progress.decisions.unsupported} 不支持 / {currentRun.progress.decisions.undetermined} 待定</strong></div>
              <div className="rounded-md bg-muted/40 p-2"><Clock3 className="mr-1 inline size-3.5" /><span className="text-muted-foreground">保留至</span><strong className="ml-2">{formatDate(currentRun.expires_at)}</strong></div>
            </div>

            <div className="flex flex-wrap gap-2">
              {Object.entries(currentRun.artifacts).map(([name, status]) => <Badge key={name} variant={status === "failed" ? "destructive" : "outline"}>{name}: {ARTIFACT_LABELS[status]}</Badge>)}
              <Badge variant="outline">模型调用 {currentRun.progress.model_calls}</Badge>
              <Badge variant="outline">任务尝试 {currentRun.progress.tasks_attempted}</Badge>
              <Badge variant="outline">Phase 1 {currentRun.progress.phase_counts.phase1 ?? 0}</Badge>
              <Badge variant="outline">Phase 2 {currentRun.progress.phase_counts.phase2 ?? 0}</Badge>
            </div>
            {(currentRun.error || currentRun.progress.stop_reason) && <Alert variant="warning"><TriangleAlert className="size-4" /><AlertTitle>运行说明</AlertTitle><AlertDescription>{currentRun.error?.safe_detail || currentRun.progress.stop_reason || "运行未完整结束。"}</AlertDescription></Alert>}
            {controlError && <Alert variant="destructive"><TriangleAlert className="size-4" /><AlertTitle>运行控制失败</AlertTitle><AlertDescription>{controlError}</AlertDescription></Alert>}
          </CardContent>
        </Card>
      )}

      {activeRunId && !(pollError?.runId === activeRunId && pollError.unavailable) && (
        <Tabs value={innerTab} onValueChange={(value) => setInnerTab(value as InnerTab)}>
          <TabsList aria-label="文档分析结果视图">
            <TabsTrigger value="metadata"><FileText />分层元数据</TabsTrigger>
            <TabsTrigger value="graph"><GitBranch />关系图谱</TabsTrigger>
          </TabsList>

          <TabsContent value="metadata" className="space-y-4">
            {!currentMetadata ? (
              <Card><CardContent className="space-y-3 p-8"><div className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="size-4 animate-spin" />正在读取已提交的分层元数据</div><Skeleton className="h-3 w-full" /><Skeleton className="h-3 w-4/5" /></CardContent></Card>
            ) : (
              <>
                <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                  <Badge variant={currentMetadata.availability === "failed" ? "destructive" : "secondary"}>{ARTIFACT_LABELS[currentMetadata.availability]}</Badge>
                  <span>run revision {currentMetadata.run_revision} · artifact {currentMetadata.artifact_revision}</span>
                  {currentMetadata.metadata_snapshot && <span>metadata snapshot: {currentMetadata.metadata_snapshot.snapshot_id}</span>}
                  {currentMetadata.metadata_snapshot && <Badge variant="outline">{currentMetadata.metadata_snapshot.generation_source}</Badge>}
                </div>

                {metadataError(currentMetadata.error) && <Alert variant="destructive"><TriangleAlert className="size-4" /><AlertTitle>分层元数据处理失败</AlertTitle><AlertDescription>{metadataError(currentMetadata.error)}</AlertDescription></Alert>}
                {currentMetadata.pagination?.warning && <Alert variant="warning"><Info className="size-4" /><AlertTitle>分页说明</AlertTitle><AlertDescription>{currentMetadata.pagination.warning}</AlertDescription></Alert>}
                {currentMetadata.warnings.length > 0 && <Alert><TriangleAlert className="size-4" /><AlertTitle>解析提示</AlertTitle><AlertDescription>{currentMetadata.warnings.join("；")}</AlertDescription></Alert>}
                {currentSelectionRef && (
                  <Alert>
                    {sourceLoading ? <Loader2 className="size-4 animate-spin" /> : <Info className="size-4" />}
                    <AlertTitle>{sourceLoading ? "正在校验并定位图谱证据" : "已从关系图谱定位原文"}</AlertTitle>
                    <AlertDescription>
                      <span className="break-all font-mono text-xs">{currentSelectionRef}</span>
                      {currentReplay && <span className="ml-2">· {currentReplay.spanCount} 个物理证据锚点</span>}
                    </AlertDescription>
                  </Alert>
                )}
                {sourceError && <Alert variant="destructive"><TriangleAlert className="size-4" /><AlertTitle>原文证据定位失败</AlertTitle><AlertDescription>{sourceError}</AlertDescription></Alert>}

                {chapterTree && displayTree && selectedNode && previewContent ? (
                  <>
                    <div className="flex justify-end gap-2 lg:hidden">
                      <Sheet>
                        <SheetTrigger asChild><Button variant="outline" size="sm"><ListTree />章节树</Button></SheetTrigger>
                        <SheetContent className="overflow-y-auto"><SheetHeader><SheetTitle>Word 章节树</SheetTitle><SheetDescription>选择章节以联动原文；跨页章节可继续选择页片段。</SheetDescription></SheetHeader><ChapterTreePanel key={`mobile-tree-${analysisKey}`} tree={displayTree} selectedNodeId={selectedNode.node.node_id} onSelect={selectTreeNode} /></SheetContent>
                      </Sheet>
                      <Sheet>
                        <SheetTrigger asChild><Button variant="outline" size="sm"><PanelRight />节点元数据</Button></SheetTrigger>
                        <SheetContent className="overflow-y-auto"><SheetHeader><SheetTitle>节点元数据</SheetTitle><SheetDescription>摘要、来源范围与分页可信度。</SheetDescription></SheetHeader><MetadataPanel selected={selectedNode} pagination={currentMetadata.pagination} /></SheetContent>
                      </Sheet>
                    </div>
                    <div className="grid h-[calc(100vh-14rem)] min-h-[38rem] gap-4 lg:grid-cols-[minmax(15rem,0.8fr)_minmax(0,2.3fr)] xl:grid-cols-[minmax(15rem,0.8fr)_minmax(0,2.3fr)_minmax(18rem,0.9fr)]">
                      <PanelCard title="Word 章节树" className="hidden lg:block"><ChapterTreePanel key={`desktop-tree-${analysisKey}`} tree={displayTree} selectedNodeId={selectedNode.node.node_id} onSelect={selectTreeNode} /></PanelCard>
                      <Card className="min-h-0 overflow-hidden">
                        <CardHeader className="flex-row items-center justify-between space-y-0 border-b p-4"><CardTitle className="min-w-0 truncate text-sm">{currentMetadata.filename || currentRun?.input.filename || "Word 原文"}</CardTitle><Badge variant="outline" className="ml-3 shrink-0">{selectedNode.kind === "page" ? pageLabel(selectedNode.node) : selectedNode.node.node_type === "document" ? "全文" : `${selectedNode.node.level} 级章节`}</Badge></CardHeader>
                        <CardContent className="h-[calc(100%-3.25rem)] overflow-auto bg-muted/40 p-4">
                          <WordViewer content={previewContent} activeLocation={currentReplay?.anchor ? null : activeLocation} activeAnchor={currentReplay?.anchor ?? null} fitTables />
                        </CardContent>
                      </Card>
                      <PanelCard title="节点元数据" className="hidden xl:block"><MetadataPanel selected={selectedNode} pagination={currentMetadata.pagination} /></PanelCard>
                    </div>
                  </>
                ) : (
                  <Card><CardContent className="flex min-h-64 flex-col items-center justify-center gap-3 p-8 text-center"><FileText className="size-8 text-muted-foreground" /><p className="text-sm font-medium">文档结构尚未就绪</p><p className="text-xs text-muted-foreground">结构一经提交会在此显示；读取本 Tab 不会触发摘要重试。</p></CardContent></Card>
                )}
              </>
            )}
          </TabsContent>

          <TabsContent value="graph">
            <DocumentRelationshipGraph
              artifact={currentGraph}
              projection={projection}
              onProjectionChange={setProjection}
              onSelectionRef={(selectionRef) => void handleSelectionRef(selectionRef)}
              selectedSelectionRef={currentSelectionRef}
            />
          </TabsContent>
        </Tabs>
      )}
    </div>
  );
}
