"use client";

import { useMemo, useRef, useState, type CSSProperties } from "react";
import { useQuery } from "@tanstack/react-query";
import { FileWarning, GripVertical, Loader2 } from "lucide-react";
import { TemplateDocumentGraphPanel } from "@/components/analysis/template-document-graph-panel";
import { useReportDocumentRun } from "@/components/analysis/use-template-document-run";
import { WordViewer, type DocumentLocation } from "@/components/extraction/word-viewer";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Tree, TreeItem, TreeItemLabel } from "@/components/ui/tree";
import { useDocumentTree, type DocumentTreeNode } from "@/components/ui/use-document-tree";
import { ExpertOpinionEntry } from "./expert-opinion-entry";
import {
  getIdentity, getReportDocumentSource,
  type ReportOrDocument, type WordChapterNode,
} from "@/lib/api";

export function isWordReportDocument(item: ReportOrDocument | null): boolean {
  return item?.kind === "uploaded-document" && /\.docx?$/i.test(item.title.trim());
}

export function reportDocumentError(error: unknown): string {
  const message = error instanceof Error ? error.message : String(error);
  const body = message.match(/^API \d+:\s*([\s\S]*)$/)?.[1];
  if (body) {
    try {
      const parsed = JSON.parse(body);
      if (typeof parsed.error?.message === "string") return parsed.error.message;
      if (typeof parsed.detail === "string") return parsed.detail;
      return "文档读取失败，请重试。";
    } catch { /* A non-JSON error still has a readable message. */ }
  }
  return message;
}

export function ReportDocumentOutline({ tree, onNavigate }: {
  tree: WordChapterNode; onNavigate: (node: WordChapterNode) => void;
}) {
  const data = useMemo(() => {
    const nodes = new Map<string, DocumentTreeNode & { chapter: WordChapterNode }>();
    const visit = (chapter: WordChapterNode) => {
      nodes.set(chapter.node_id, { chapter, name: chapter.heading || "未命名章节",
        children: chapter.children.map((child) => child.node_id), defaultExpanded: true });
      chapter.children.forEach(visit);
    };
    visit(tree);
    return { rootId: tree.node_id, nodes };
  }, [tree]);
  const outline = useDocumentTree(data, (node) => onNavigate(node.chapter));
  if (!tree.children.length) return <p className="p-2 text-sm text-muted-foreground">正文已加载，未识别到章节标题。</p>;
  return <nav aria-label="目录">
    <Tree tree={outline} aria-label="文档章节" indent={12}>
      {outline.getItems().map((item) => <TreeItem key={item.getId()} item={item}
        data-chapter-id={item.getId()} className="cursor-pointer" title={item.getItemName()}>
        <TreeItemLabel item={item} />
      </TreeItem>)}
    </Tree>
  </nav>;
}

export function ReportWordWorkspace({ documentIri }: { documentIri: string }) {
  const layout = useRef<HTMLDivElement>(null);
  const [graphWidth, setGraphWidth] = useState(300);
  const model = useReportDocumentRun(documentIri);
  const { username, role } = getIdentity();
  const run = model.run;
  const runId = run?.recognition_run_id;
  const structureReady = !!runId && ["ready", "partial"].includes(run?.artifacts.structure ?? "");
  const metadata = model.metadata;
  const preview = useQuery({
    queryKey: ["report-word-source", username, role, documentIri],
    queryFn: ({ signal }) => getReportDocumentSource(documentIri, signal),
    enabled: !model.loading && !model.error && !structureReady,
    retry: false,
    refetchOnWindowFocus: false,
  });
  const content = structureReady ? metadata?.content : preview.data?.content;
  const tree = structureReady ? metadata?.section_tree : preview.data?.section_tree;
  const identity = structureReady ? metadata?.analysis : preview.data;
  const readerKey = JSON.stringify([documentIri, structureReady ? runId : null,
    identity?.document_hash, identity?.structure_hash]);
  const [location, setLocation] = useState<{ key: string; value: DocumentLocation } | null>(null);
  const sourceError = structureReady ? model.metadataError : preview.error;
  const loading = model.loading || (structureReady
    ? !metadata && !sourceError && !model.error : preview.isLoading);
  const selected = model.sourceSelection;
  const anchor = selected && structureReady && selected.recognition_run_id === runId
    && selected.document_hash === identity?.document_hash
    && selected.structure_hash === identity?.structure_hash ? selected.anchors[0] : null;
  const readError = sourceError || (!content && model.error);
  const refresh = () => {
    model.refresh();
    if (!structureReady) void preview.refetch();
  };
  const selectChapter = (node: WordChapterNode) => {
    model.select("");
    setLocation({ key: readerKey, value: {
      kind: "section", nodeId: node.node_id,
      anchorBlockId: node.source_range.anchor_block_id,
      startBlockId: node.source_range.start_block_id,
      endBlockId: node.source_range.end_block_id,
    } });
  };
  const graphModel = {
    ...model,
    canCreate: model.canCreate && !!content && !readError,
    error: model.error ? new Error(reportDocumentError(model.error)) : null,
    select: (ref: string) => { setLocation(null); model.select(ref); },
  };
  return <div ref={layout} style={{ "--report-graph-width": `${graphWidth}px` } as CSSProperties}
    className="grid min-h-0 flex-1 gap-4 lg:grid-cols-[200px_minmax(0,1fr)_1px_var(--report-graph-width)]">
    <Card className="flex min-h-0 flex-col">
      <CardHeader className="p-4 pb-2"><CardTitle className="text-sm">目录</CardTitle></CardHeader>
      <CardContent className="min-h-0 overflow-y-auto p-2">
        {loading ? <p className="p-2 text-sm text-muted-foreground">正在加载目录…</p>
          : tree ? <ReportDocumentOutline key={JSON.stringify([username, role, readerKey])}
            tree={tree} onNavigate={selectChapter} />
            : <p className="p-2 text-sm text-muted-foreground">{readError ? "正文加载失败，暂无法显示目录。" : "正在准备文档章节。"}</p>}
      </CardContent>
    </Card>
    <Card className="flex min-h-0 min-w-0 flex-col">
      <CardHeader className="flex-row items-center justify-between gap-2 p-4 pb-2">
        <CardTitle className="text-sm">文档预览</CardTitle>
        <div className="flex items-center gap-2">
          <ExpertOpinionEntry target={{ document_iri: documentIri }} runId={runId} sourceHash={identity?.document_hash} />
          <Button variant="ghost" size="sm" onClick={refresh}>刷新</Button>
        </div>
      </CardHeader>
      <CardContent className="min-h-0 flex-1 overflow-auto p-4">
        {readError && <Alert variant="destructive" className="mb-3">
          <FileWarning className="size-4" /><AlertTitle>文档加载失败</AlertTitle>
          <AlertDescription>{reportDocumentError(readError)}</AlertDescription>
        </Alert>}
        {loading && !content && <p role="status" className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="size-4 animate-spin" />正在加载正文…</p>}
        {content && <WordViewer key={readerKey} content={content} fitTables
          activeLocation={location?.key === readerKey ? location.value : null}
          activeAnchor={anchor ?? null} />}
      </CardContent>
    </Card>
    <div role="separator" aria-label="调整关系图谱宽度" aria-orientation="vertical"
      tabIndex={0} aria-valuemin={220} aria-valuenow={graphWidth}
      className="relative hidden cursor-col-resize touch-none bg-border lg:block"
      title="拖动调整宽度 · 双击复位"
      onPointerDown={(event) => event.currentTarget.setPointerCapture(event.pointerId)}
      onPointerMove={(event) => {
        if (!event.currentTarget.hasPointerCapture(event.pointerId) || !layout.current) return;
        const bounds = layout.current.getBoundingClientRect();
        setGraphWidth(Math.max(220, Math.min(bounds.right - event.clientX, bounds.width - 580)));
      }}
      onPointerUp={(event) => event.currentTarget.releasePointerCapture(event.pointerId)}
      onDoubleClick={() => setGraphWidth(300)}
      onKeyDown={(event) => {
        if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
        event.preventDefault();
        const max = Math.max(220, (layout.current?.clientWidth ?? 1000) - 580);
        setGraphWidth((width) => Math.max(220, Math.min(max,
          width + (event.key === "ArrowLeft" ? 20 : -20))));
      }}>
      <div className="absolute inset-y-0 -left-2 -right-2" />
      <GripVertical className="absolute left-1/2 top-1/2 size-3 -translate-x-1/2" />
    </div>
    <Card className="flex min-h-[300px] min-w-0 flex-col overflow-hidden lg:min-h-0">
      <TemplateDocumentGraphPanel model={graphModel} />
    </Card>
  </div>;
}
