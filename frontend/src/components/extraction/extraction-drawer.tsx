"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
} from "@/components/ui/sheet";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  getAnnotatedDocument,
  getClassHierarchy,
  getModules,
  rerunAnnotation,
  subscribeJobProgress,
  type AnnotatedDocument,
  type JobProgressEvent,
  type TreeNode,
} from "@/lib/api";
import { ENTITY_PALETTE } from "./entity-mark";
import { WordViewer } from "./word-viewer";
import { ExcelViewer } from "./excel-viewer";
import { EntityPanel } from "./entity-panel";
import { TriplePanel } from "./triple-panel";
import { AlignmentReview } from "./alignment-review";
import {
  extractEntityStats,
  buildLabelToCategoryMap,
  groupStatsByCategory,
} from "./entity-stats";

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
  const [selectedEntityType, setSelectedEntityType] = useState<string | null>(null);
  const [hierarchyTrees, setHierarchyTrees] = useState<TreeNode[]>([]);
  const [progressEvent, setProgressEvent] = useState<JobProgressEvent | null>(null);
  const [rerunning, setRerunning] = useState(false);

  const fetchDocAndHierarchy = useCallback((id: string) => {
    setLoading(true);
    setError(null);

    const fetchDoc = getAnnotatedDocument(id);
    const fetchHierarchy = getModules()
      .then((modules) =>
        Promise.all(modules.map((m) => getClassHierarchy(m.key))),
      )
      .then((treesPerModule) => treesPerModule.flat())
      .catch(() => [] as TreeNode[]);

    Promise.all([fetchDoc, fetchHierarchy])
      .then(([docResult, trees]) => {
        setDoc(docResult);
        setHierarchyTrees(trees);
      })
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (!open || !jobId) return;
    fetchDocAndHierarchy(jobId);
  }, [open, jobId, fetchDocAndHierarchy]);

  useEffect(() => {
    if (!open || !jobId) return;
    const unsub = subscribeJobProgress(jobId, (e) => {
      setProgressEvent(e);
      if (rerunning && (e.stage === "reviewing" || e.stage === "done")) {
        setRerunning(false);
        fetchDocAndHierarchy(jobId);
      }
    });
    return unsub;
  }, [open, jobId, rerunning, fetchDocAndHierarchy]);

  useEffect(() => {
    if (!open) {
      setSelectedEntityType(null);
      setProgressEvent(null);
      setRerunning(false);
    }
  }, [open]);

  useEffect(() => {
    setSelectedEntityType(null);
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

  const entityGroups = useMemo(() => {
    if (!doc) return [];
    const stats = extractEntityStats(doc.content, doc.source_type as "word" | "excel");
    const labelToCategory = buildLabelToCategoryMap(hierarchyTrees);
    return groupStatsByCategory(stats, labelToCategory);
  }, [doc, hierarchyTrees]);

  const entityStats = useMemo(
    () => entityGroups.flatMap((g) => g.items),
    [entityGroups],
  );

  const filterStyle = useMemo(() => {
    if (!selectedEntityType) return null;
    const escaped = CSS.escape(selectedEntityType);
    const stat = entityStats.find((s) => s.label === selectedEntityType);
    const color = stat ? ENTITY_PALETTE[stat.colorIndex] : null;
    const outlineColor = color?.border ?? "#3B82F6";

    return `
      .entity-filter-active .entity-annotation {
        opacity: 0.2 !important;
        transition: opacity 0.2s ease;
      }
      .entity-filter-active .entity-annotation[data-entity-label="${escaped}"] {
        opacity: 1 !important;
        outline: 2px solid ${outlineColor};
        outline-offset: 1px;
        box-shadow: 0 0 0 4px ${outlineColor}20;
        transition: opacity 0.2s ease, outline 0.2s ease, box-shadow 0.2s ease;
      }
    `;
  }, [selectedEntityType, entityStats]);

  const isAnnotating = rerunning || progressEvent?.stage === "annotating";
  const annoStage = progressEvent?.annotation_stage;

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent size="xl" className="flex flex-col overflow-hidden p-0">
        <SheetHeader className="shrink-0 px-6 pt-6 pb-2">
          <SheetTitle>{doc?.filename ?? "文档标注"}</SheetTitle>
          <SheetDescription>
            实体标注预览 — 点击右侧实体类型高亮显示
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
            </>
          )}
        </div>

        <div className="flex min-h-0 flex-1">
          {/* Left: Document preview */}
          <div
            className={`flex-1 overflow-y-auto border-r px-6 py-4${selectedEntityType ? " entity-filter-active" : ""}`}
          >
            {filterStyle && <style>{filterStyle}</style>}

            {loading && <p className="text-muted-foreground text-sm">加载标注文档中...</p>}
            {error && (
              <div className="rounded border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
                {error}
              </div>
            )}
            {doc && !loading && doc.source_type === "word" && (
              <WordViewer content={doc.content as Record<string, unknown>} />
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

          {/* Right: Tabs for entity panel + alignment review */}
          {doc && !loading && (
            <div className="flex w-96 shrink-0 flex-col">
              <Tabs defaultValue="entities" className="flex h-full flex-col">
                <TabsList className="mx-3 mt-3 shrink-0">
                  <TabsTrigger value="entities" className="text-xs">实体类型</TabsTrigger>
                  <TabsTrigger value="triples" className="text-xs">属性三元组</TabsTrigger>
                  <TabsTrigger value="review" className="text-xs">对齐审核</TabsTrigger>
                </TabsList>
                <TabsContent value="entities" className="flex-1 overflow-hidden mt-0">
                  <EntityPanel
                    groups={entityGroups}
                    selectedType={selectedEntityType}
                    onSelectType={setSelectedEntityType}
                  />
                </TabsContent>
                <TabsContent value="triples" className="flex-1 overflow-hidden mt-0">
                  <TriplePanel triples={doc?.triples ?? []} />
                </TabsContent>
                <TabsContent value="review" className="flex-1 overflow-hidden mt-0">
                  {jobId && <AlignmentReview key={jobId} jobId={jobId} />}
                </TabsContent>
              </Tabs>
            </div>
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}
