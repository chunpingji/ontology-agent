"use client";

import {
  useCallback,
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
  type DragEvent,
  type ReactNode,
} from "react";
import {
  BookOpen,
  File,
  FileText,
  FolderTree,
  Info,
  ListTree,
  Loader2,
  PanelRight,
  RefreshCw,
  TriangleAlert,
  UploadCloud,
} from "lucide-react";

import {
  WordViewer,
  type DocumentLocation,
} from "@/components/extraction/word-viewer";
import { TreeView, type TreeDataItem } from "@/components/tree-view";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import {
  analyzeWordDocument,
  type SummarySource,
  type SummaryStatus,
  type WordChapterNode,
  type WordDocumentAnalysis,
  type WordPageNode,
  type WordPaginationMetadata,
  type WordSourceRange,
} from "@/lib/api";
import { cn } from "@/lib/utils";

type SelectedTreeNode =
  | { kind: "chapter"; node: WordChapterNode }
  | { kind: "page"; node: WordPageNode; parent: WordChapterNode };

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

function errorMessage(error: unknown): string {
  if (!(error instanceof Error)) return "请求失败，请稍后重试。";
  const apiBody = error.message.startsWith("API ") && error.message.includes(":")
    ? error.message.slice(error.message.indexOf(":") + 1).trim() : null;
  if (apiBody) {
    try {
      const detail = (JSON.parse(apiBody) as { detail?: unknown }).detail;
      if (typeof detail === "string") return detail;
    } catch {
      // Keep the original request error when the body is not JSON.
    }
  }
  return error.message;
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
  // A single page adds no navigation value: selecting the leaf chapter already
  // focuses the same source range. Only expose page fragments when the chapter
  // actually spans multiple pages.
  if (chapter.is_leaf && chapter.pages.length > 1) {
    children.push(
      ...chapter.pages.map((page) => ({
        id: page.node_id,
        name: pageLabel(page),
        icon: File,
      })),
    );
  }

  return {
    id: chapter.node_id,
    name: chapter.heading || "未命名章节",
    icon:
      chapter.node_type === "document"
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

  const chapter = selected.node;
  if (chapter.node_type === "document") {
    return { kind: "document", nodeId: chapter.node_id };
  }
  return {
    kind: "section",
    nodeId: chapter.node_id,
    anchorBlockId: chapter.source_range.anchor_block_id,
    startBlockId: chapter.source_range.start_block_id,
    endBlockId: chapter.source_range.end_block_id,
  };
}

function SummaryBadge({ source }: { source: SummarySource }) {
  const variant = source === "llm" ? "default" : "outline";
  return <Badge variant={variant}>{SUMMARY_SOURCE_LABELS[source]}</Badge>;
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
          <Badge variant="secondary">
            {chapter.node_type === "document" ? "文档根" : `${chapter.level} 级章节`}
          </Badge>
          <SummaryBadge source={meta.summary_source} />
        </div>
        <h3 className="break-words text-base font-semibold">{chapter.heading}</h3>
        {chapter.path.length > 0 && (
          <p className="mt-1 break-words text-xs text-muted-foreground">
            {chapter.path.join(" / ")}
          </p>
        )}
      </div>

      <section className="space-y-2">
        <h4 className="text-sm font-medium">内容摘要</h4>
        <p className="whitespace-pre-wrap rounded-lg bg-muted/50 p-3 text-sm leading-relaxed">
          {meta.content_summary || "暂无摘要。"}
        </p>
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
        <div className="mb-2 flex flex-wrap items-center gap-2">
          <Badge variant="secondary">{pageLabel(page)}</Badge>
          <SummaryBadge source={meta.summary_source} />
        </div>
        <h3 className="break-words text-base font-semibold">{parent.heading}</h3>
        <p className="mt-1 text-xs text-muted-foreground">叶章节内容片段</p>
      </div>

      <section className="space-y-2">
        <h4 className="text-sm font-medium">内容摘要</h4>
        <p className="whitespace-pre-wrap rounded-lg bg-muted/50 p-3 text-sm leading-relaxed">
          {meta.content_summary || "暂无摘要。"}
        </p>
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
          <MetaRow
            label="分页来源"
            value={page.break_source ? BREAK_SOURCE_LABELS[page.break_source] ?? page.break_source : "—"}
          />
          <MetaRow label="段落" value={meta.paragraph_count} />
          <MetaRow label="表格" value={meta.table_count} />
          <MetaRow label="字符" value={meta.character_count} />
          <MetaRow label="来源块" value={page.block_ids.length} />
        </dl>
      </section>

      <section className="space-y-2">
        <h4 className="text-sm font-medium">来源范围</h4>
        <SourceRangeDetails range={page.source_range} />
      </section>

      <section className="space-y-2">
        <h4 className="text-sm font-medium">标识与校验</h4>
        <dl className="space-y-2 font-mono text-xs">
          <MetaRow label="节点 ID" value={page.node_id} />
          <MetaRow label="内容哈希" value={meta.content_hash || "—"} />
        </dl>
      </section>
    </div>
  );
}

function MetadataPanel({
  selected,
  pagination,
}: {
  selected: SelectedTreeNode;
  pagination?: WordPaginationMetadata;
}) {
  return (
    <div className="space-y-5">
      {pagination && (
        <section className="space-y-2 rounded-lg border bg-muted/20 p-3">
          <h3 className="text-sm font-medium">分页可信度</h3>
          <dl className="space-y-2">
            <MetaRow label="分页模式" value={PAGINATION_MODE_LABELS[pagination.mode]} />
            <MetaRow
              label="物理页码"
              value={pagination.physical_page_numbers_available ? "可用" : "不可用"}
            />
            <MetaRow label="是否估算" value={pagination.is_estimated ? "是" : "否"} />
          </dl>
          {pagination.warning && (
            <p className="text-xs leading-relaxed text-muted-foreground">
              {pagination.warning}
            </p>
          )}
        </section>
      )}
      {selected.kind === "chapter" ? (
        <ChapterMetadata chapter={selected.node} />
      ) : (
        <PageMetadata page={selected.node} parent={selected.parent} />
      )}
    </div>
  );
}

function ChapterTreePanel({
  tree,
  selectedNodeId,
  onSelect,
}: {
  tree: TreeDataItem;
  selectedNodeId: string;
  onSelect: (nodeId: string) => void;
}) {
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

function PanelCard({
  title,
  children,
  className,
}: {
  title: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <Card className={cn("min-h-0 overflow-hidden", className)}>
      <CardHeader className="border-b p-4">
        <CardTitle className="text-sm">{title}</CardTitle>
      </CardHeader>
      <CardContent className="h-[calc(100%-3.25rem)] overflow-y-auto p-3">
        {children}
      </CardContent>
    </Card>
  );
}

function LoadingDocument({ filename }: { filename: string }) {
  return (
    <Card>
      <CardContent className="flex min-h-72 flex-col items-center justify-center gap-5 p-8 text-center">
        <div className="rounded-full bg-primary/10 p-4 text-primary">
          <Loader2 className="size-8 animate-spin" />
        </div>
        <div className="space-y-2">
          <h3 className="font-medium">正在即时分析 Word 文档</h3>
          <p className="max-w-xl break-all text-sm text-muted-foreground">{filename}</p>
          <p className="max-w-xl text-sm text-muted-foreground">
            正在构建章节树、识别分页并生成分层摘要。分析结果只返回当前页面，不创建抽取作业。
          </p>
        </div>
        <div className="w-full max-w-md space-y-2">
          <Skeleton className="h-2 w-full" />
          <Skeleton className="mx-auto h-2 w-4/5" />
        </div>
      </CardContent>
    </Card>
  );
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function DocumentAnalysisPanel() {
  const inputRef = useRef<HTMLInputElement>(null);
  const requestIdRef = useRef(0);
  const [sourceFile, setSourceFile] = useState<File | null>(null);
  const [analysis, setAnalysis] = useState<WordDocumentAnalysis | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [isDragging, setIsDragging] = useState(false);
  const [analysisError, setAnalysisError] = useState<string | null>(null);

  const runAnalysis = useCallback(async (file: File) => {
    const suffix = file.name.slice(file.name.lastIndexOf(".")).toLowerCase();
    if (suffix !== ".doc" && suffix !== ".docx") {
      setAnalysisError("仅支持 .doc 或 .docx 文件。");
      return;
    }

    const requestId = ++requestIdRef.current;
    setSourceFile(file);
    setAnalysis(null);
    setSelectedNodeId(null);
    setAnalysisError(null);
    setIsAnalyzing(true);
    try {
      const result = await analyzeWordDocument(file);
      if (requestId !== requestIdRef.current) return;
      setAnalysis(result);
      setSelectedNodeId(result.section_tree.node_id);
    } catch (error) {
      if (requestId !== requestIdRef.current) return;
      setAnalysisError(errorMessage(error));
    } finally {
      if (requestId === requestIdRef.current) setIsAnalyzing(false);
    }
  }, []);

  const handleFileInput = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.currentTarget.files?.[0];
    event.currentTarget.value = "";
    if (file) void runAnalysis(file);
  };

  const handleDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setIsDragging(false);
    if (isAnalyzing) return;
    const file = event.dataTransfer.files?.[0];
    if (file) void runAnalysis(file);
  };

  const clearAnalysis = () => {
    requestIdRef.current += 1;
    setSourceFile(null);
    setAnalysis(null);
    setSelectedNodeId(null);
    setAnalysisError(null);
    setIsAnalyzing(false);
    if (inputRef.current) inputRef.current.value = "";
  };

  const chapterTree = analysis?.section_tree;
  const nodeIndex = useMemo(
    () => (chapterTree ? buildTreeIndex(chapterTree) : new Map<string, SelectedTreeNode>()),
    [chapterTree],
  );
  const displayTree = useMemo(
    () => (chapterTree ? chapterToTreeItem(chapterTree) : null),
    [chapterTree],
  );
  const selected = chapterTree
    ? nodeIndex.get(selectedNodeId ?? "") ?? nodeIndex.get(chapterTree.node_id) ?? null
    : null;

  const activeLocation = useMemo(
    () => (selected ? selectedLocation(selected) : null),
    [selected],
  );
  const analysisKey = chapterTree?.layer_metadata.content_hash || analysis?.filename || "word";

  return (
    <div className="space-y-4">
      <input
        ref={inputRef}
        type="file"
        accept=".doc,.docx,application/msword,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        className="hidden"
        onChange={handleFileInput}
      />

      {!analysis && !isAnalyzing && (
        <Card>
          <CardContent className="p-5 sm:p-8">
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
              onDragOver={(event) => {
                event.preventDefault();
                setIsDragging(true);
              }}
              onDragLeave={() => setIsDragging(false)}
              onDrop={handleDrop}
              className={cn(
                "flex min-h-80 cursor-pointer flex-col items-center justify-center gap-5 rounded-xl border border-dashed p-8 text-center outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring",
                isDragging
                  ? "border-primary bg-primary/5"
                  : "border-border bg-muted/20 hover:border-primary/50 hover:bg-muted/40",
              )}
            >
              <div className="rounded-full bg-primary/10 p-5 text-primary">
                <UploadCloud className="size-10" />
              </div>
              <div className="space-y-2">
                <h2 className="text-lg font-semibold">上传 Word 文档即时分析</h2>
                <p className="text-sm text-muted-foreground">
                  拖拽 .doc / .docx 到此处，或点击选择文件
                </p>
              </div>
              <Button type="button" onClick={(event) => {
                event.stopPropagation();
                inputRef.current?.click();
              }}>
                <FileText />
                选择 Word 文档
              </Button>
              <p className="max-w-2xl text-xs leading-relaxed text-muted-foreground">
                文件仅用于本次临时解析，不创建 ExtractionJob，不执行分类、实体识别、关系抽取或知识图谱写入；刷新或关闭页面后结果即丢弃。
              </p>
            </div>
          </CardContent>
        </Card>
      )}

      {analysisError && (
        <Alert variant="destructive">
          <TriangleAlert className="h-4 w-4" />
          <AlertTitle>Word 文档分析失败</AlertTitle>
          <AlertDescription>{analysisError}</AlertDescription>
        </Alert>
      )}

      {isAnalyzing && sourceFile && <LoadingDocument filename={sourceFile.name} />}

      {analysis && chapterTree && displayTree && selected && !isAnalyzing && (
        <>
          <Card>
            <CardContent className="flex flex-col gap-3 p-4 xl:flex-row xl:items-center xl:justify-between">
              <div className="flex min-w-0 flex-1 flex-wrap items-center gap-2">
                <div className="mr-2 min-w-0">
                  <p className="truncate text-sm font-medium">{analysis.filename}</p>
                  {sourceFile && (
                    <p className="text-xs text-muted-foreground">
                      {formatFileSize(sourceFile.size)} · 本次页面内临时分析
                    </p>
                  )}
                </div>
                <Badge variant="outline">
                  {PAGINATION_MODE_LABELS[analysis.pagination.mode]}
                </Badge>
                <SummaryBadge source={chapterTree.layer_metadata.summary_source} />
              </div>

              <div className="flex flex-wrap items-center gap-2">
                <Sheet>
                  <SheetTrigger asChild>
                    <Button variant="outline" size="sm" className="lg:hidden">
                      <ListTree />章节树
                    </Button>
                  </SheetTrigger>
                  <SheetContent className="overflow-y-auto">
                    <SheetHeader>
                      <SheetTitle>Word 章节树</SheetTitle>
                      <SheetDescription>
                        选择章节以联动原文；跨页章节可继续选择页片段。
                      </SheetDescription>
                    </SheetHeader>
                    <ChapterTreePanel
                      key={`mobile-tree-${analysisKey}`}
                      tree={displayTree}
                      selectedNodeId={selected.node.node_id}
                      onSelect={setSelectedNodeId}
                    />
                  </SheetContent>
                </Sheet>
                <Sheet>
                  <SheetTrigger asChild>
                    <Button variant="outline" size="sm" className="xl:hidden">
                      <PanelRight />节点元数据
                    </Button>
                  </SheetTrigger>
                  <SheetContent className="overflow-y-auto">
                    <SheetHeader>
                      <SheetTitle>节点元数据</SheetTitle>
                      <SheetDescription>摘要、来源范围与分页可信度。</SheetDescription>
                    </SheetHeader>
                    <MetadataPanel selected={selected} pagination={analysis.pagination} />
                  </SheetContent>
                </Sheet>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => sourceFile && void runAnalysis(sourceFile)}
                >
                  <RefreshCw />重新分析
                </Button>
                <Button variant="outline" size="sm" onClick={() => inputRef.current?.click()}>
                  <UploadCloud />选择其他文件
                </Button>
                <Button variant="ghost" size="sm" onClick={clearAnalysis}>
                  清空
                </Button>
              </div>
            </CardContent>
          </Card>

          {analysis.pagination.warning && (
            <Alert variant="warning">
              <Info className="h-4 w-4" />
              <AlertTitle>分页说明</AlertTitle>
              <AlertDescription>{analysis.pagination.warning}</AlertDescription>
            </Alert>
          )}

          {analysis.warnings.length > 0 && (
            <Alert>
              <TriangleAlert className="h-4 w-4" />
              <AlertTitle>解析提示</AlertTitle>
              <AlertDescription>{analysis.warnings.join("；")}</AlertDescription>
            </Alert>
          )}

          <div className="grid h-[calc(100vh-14rem)] min-h-[38rem] gap-4 lg:grid-cols-[minmax(15rem,0.8fr)_minmax(0,2.3fr)] xl:grid-cols-[minmax(15rem,0.8fr)_minmax(0,2.3fr)_minmax(18rem,0.9fr)]">
            <PanelCard title="Word 章节树" className="hidden lg:block">
              <ChapterTreePanel
                key={`desktop-tree-${analysisKey}`}
                tree={displayTree}
                selectedNodeId={selected.node.node_id}
                onSelect={setSelectedNodeId}
              />
            </PanelCard>

            <Card className="min-h-0 overflow-hidden">
              <CardHeader className="flex-row items-center justify-between space-y-0 border-b p-4">
                <CardTitle className="min-w-0 truncate text-sm">
                  {analysis.filename || "Word 原文"}
                </CardTitle>
                <Badge variant="outline" className="ml-3 shrink-0">
                  {selected.kind === "page"
                    ? pageLabel(selected.node)
                    : selected.node.node_type === "document"
                      ? "全文"
                      : `${selected.node.level} 级章节`}
                </Badge>
              </CardHeader>
              <CardContent className="h-[calc(100%-3.25rem)] overflow-auto bg-muted/40 p-4">
                <WordViewer
                  content={analysis.content}
                  activeLocation={activeLocation}
                  fitTables
                />
              </CardContent>
            </Card>

            <PanelCard title="节点元数据" className="hidden xl:block">
              <MetadataPanel
                selected={selected}
                pagination={analysis.pagination}
              />
            </PanelCard>
          </div>
        </>
      )}
    </div>
  );
}
