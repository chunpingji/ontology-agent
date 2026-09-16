"use client";

import { useRef, useState, type CSSProperties, type PointerEvent } from "react";
import { FileWarning, GripVertical, Loader2, RotateCw } from "lucide-react";
import {
  useFinderPdeDecision,
  useTemplateFinder,
} from "@/components/analysis/use-template-finder";
import { RelationPanel } from "@/components/extraction/relation-panel";
import { WordViewer, type DocumentLocation } from "@/components/extraction/word-viewer";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import { ExpertOpinionEntry } from "./expert-opinion-entry";
import { ReportDocumentOutline, reportDocumentError } from "./report-word-workspace";

const DEFAULT_GRAPH_WIDTH = 300;
const MIN_GRAPH_WIDTH = 220;

/** The pinned report keeps its original three-pane layout and inline PDE review. */
export function ReportFinderWorkspace({ templateId, sourceJobId, documentIri }: {
  templateId: string;
  sourceJobId: string;
  documentIri: string;
}) {
  const model = useTemplateFinder(templateId, sourceJobId);
  const review = useFinderPdeDecision(templateId, sourceJobId, model);
  const layoutRef = useRef<HTMLDivElement>(null);
  const [graphWidth, setGraphWidth] = useState(DEFAULT_GRAPH_WIDTH);
  const [resizing, setResizing] = useState(false);
  const [location, setLocation] = useState<{ key: string; value: DocumentLocation } | null>(null);
  const source = model.source;
  const graphError = model.error ? reportDocumentError(model.error) : model.status?.error;

  const limitWidth = (width: number) => {
    const max = Math.max(MIN_GRAPH_WIDTH, (layoutRef.current?.clientWidth ?? 1000) - 580);
    return Math.min(max, Math.max(MIN_GRAPH_WIDTH, width));
  };
  const finishResize = (event: PointerEvent<HTMLDivElement>) => {
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    setResizing(false);
  };

  return (
    <div
      ref={layoutRef}
      className={cn(
        "flex min-h-0 flex-1 flex-col gap-6 lg:flex-row lg:gap-0",
        resizing && "select-none",
      )}
    >
      <Card className="lg:mr-6 lg:flex lg:h-full lg:w-[220px] lg:shrink-0 lg:flex-col lg:overflow-hidden">
        <CardHeader className="p-4 pb-2">
          <CardTitle className="text-sm">目录</CardTitle>
        </CardHeader>
        <CardContent className="p-2 pt-0 lg:min-h-0 lg:flex-1 lg:overflow-y-auto">
          {model.loading && !source ? (
            <div className="space-y-2 p-1" role="status" aria-label="正在加载目录">
              <Skeleton className="h-6 w-full" />
              <Skeleton className="h-6 w-5/6" />
              <Skeleton className="h-6 w-4/6" />
            </div>
          ) : source ? (
            <ReportDocumentOutline
              key={model.readerKey}
              tree={source.section_tree}
              onNavigate={(node) => {
                model.select(null);
                setLocation({
                  key: model.readerKey,
                  value: {
                    kind: "section",
                    nodeId: node.node_id,
                    anchorBlockId: node.source_range.anchor_block_id,
                    startBlockId: node.source_range.start_block_id,
                    endBlockId: node.source_range.end_block_id,
                  },
                });
              }}
            />
          ) : (
            <p className="p-2 text-sm text-muted-foreground">正文加载失败，暂无法显示目录。</p>
          )}
        </CardContent>
      </Card>

      <Card className="min-w-0 lg:flex lg:h-full lg:flex-1 lg:flex-col lg:overflow-hidden">
        <CardHeader className="flex-row items-center justify-between gap-2 p-4 pb-2">
          <CardTitle className="text-sm">文档预览</CardTitle>
          <ExpertOpinionEntry
            target={{ document_iri: documentIri }}
            sourceHash={source?.document_hash}
          />
        </CardHeader>
        <CardContent className="p-6 lg:min-h-0 lg:flex-1 lg:overflow-y-auto">
          {model.loading && !source && (
            <p role="status" className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="size-4 animate-spin" />正在加载正文…
            </p>
          )}
          {!source && model.error && (
            <Alert variant="destructive">
              <FileWarning className="size-4" />
              <AlertTitle>文档加载失败</AlertTitle>
              <AlertDescription>{reportDocumentError(model.error)}</AlertDescription>
            </Alert>
          )}
          {source && (
            <WordViewer
              key={model.readerKey}
              content={source.content}
              fitTables
              activeAnchor={model.anchor}
              activeLocation={model.anchor ? null :
                location?.key === model.readerKey ? location.value : null}
            />
          )}
        </CardContent>
      </Card>

      <div
        role="separator"
        aria-label="调整关系图谱宽度"
        aria-orientation="vertical"
        aria-valuemin={MIN_GRAPH_WIDTH}
        aria-valuenow={graphWidth}
        tabIndex={0}
        title="拖动调整宽度 · 双击复位"
        onPointerDown={(event) => {
          if (event.button !== 0) return;
          event.preventDefault();
          event.currentTarget.setPointerCapture(event.pointerId);
          setResizing(true);
        }}
        onPointerMove={(event) => {
          if (!event.currentTarget.hasPointerCapture(event.pointerId) || !layoutRef.current) return;
          setGraphWidth(limitWidth(layoutRef.current.getBoundingClientRect().right - event.clientX));
        }}
        onPointerUp={finishResize}
        onPointerCancel={finishResize}
        onLostPointerCapture={() => setResizing(false)}
        onDoubleClick={() => setGraphWidth(DEFAULT_GRAPH_WIDTH)}
        onKeyDown={(event) => {
          if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
          event.preventDefault();
          setGraphWidth((width) => limitWidth(width + (event.key === "ArrowLeft" ? 20 : -20)));
        }}
        className={cn(
          "group relative hidden w-px shrink-0 cursor-col-resize touch-none self-stretch bg-border transition-colors lg:mx-3 lg:block",
          resizing ? "bg-primary" : "hover:bg-primary/50",
        )}
      >
        <div className="absolute inset-y-0 -left-1.5 -right-1.5 z-10" />
        <div className={cn(
          "absolute left-1/2 top-1/2 z-20 flex h-8 w-3 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full border bg-background shadow-sm transition-opacity",
          resizing ? "opacity-100" : "opacity-0 group-hover:opacity-100",
        )}>
          <GripVertical className="size-3 text-muted-foreground" />
        </div>
      </div>

      <Card
        style={{ "--graph-w": `${graphWidth}px` } as CSSProperties}
        className="w-full lg:flex lg:h-full lg:w-[var(--graph-w)] lg:shrink-0 lg:flex-col lg:overflow-hidden"
        aria-label="关系图谱"
      >
        <CardHeader className="p-4 pb-2">
          <div className="flex items-center justify-between gap-2">
            <CardTitle className="text-sm">关系图谱</CardTitle>
            <Button
              variant="outline"
              size="sm"
              className="h-7 gap-1.5 text-xs"
              disabled={!model.canStart || !source}
              title="对当前文档重新识别关系图谱"
              onClick={() => { setLocation(null); model.start(); }}
            >
              {model.running ? <Loader2 className="size-3.5 animate-spin" />
                : <RotateCw className="size-3.5" />}
              {model.running ? "识别中…" : "重新识别"}
            </Button>
          </div>
          {model.status?.stale && (
            <p className="mt-1 text-xs text-amber-700">输入或配置已变化，请重新识别。</p>
          )}
          {graphError && <p role="alert" className="mt-1 text-xs text-destructive">{graphError}</p>}
          {review.error && (
            <p role="alert" className="mt-1 text-xs text-destructive">{reportDocumentError(review.error)}</p>
          )}
        </CardHeader>
        <CardContent className="p-0 pb-2 lg:min-h-0 lg:flex-1 lg:overflow-hidden">
          <div className="relative min-h-[240px] max-h-[70vh] overflow-y-auto lg:h-full lg:min-h-0 lg:max-h-none lg:overflow-hidden">
            {model.graph ? (
              <RelationPanel
                key={model.readerKey}
                docClass={model.graph.doc_class}
                relationships={model.graph.relationships}
                onSelectFinderSource={(anchor) => { setLocation(null); model.select(anchor); }}
                decision={review.decision}
                onDecide={review.onDecide}
                decisionPending={review.pending || model.running}
                emptyMessage="识别完成，未找到符合配置的关系。"
              />
            ) : (
              <p className="px-4 py-3 text-sm text-muted-foreground">
                {graphError ? "关系图谱加载失败。" : model.loading || model.status?.has_result
                  ? "正在加载关系图谱…" : "尚无关系识别结果，点击重新识别后显示。"}
              </p>
            )}
            {model.running && (
              <div role="status" className="absolute inset-0 z-10 flex flex-col items-center justify-center gap-2 bg-background/70 backdrop-blur-sm">
                <Loader2 className="size-5 animate-spin text-muted-foreground" />
                <span className="text-xs text-muted-foreground">正在重新识别关系图谱…</span>
              </div>
            )}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
