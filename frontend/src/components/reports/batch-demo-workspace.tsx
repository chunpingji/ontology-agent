"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { RelationPanel } from "@/components/extraction/relation-panel";
import { WordViewer, type DocumentLocation } from "@/components/extraction/word-viewer";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { getIdentity, getReportDocumentSource, type BatchDemoAvailable } from "@/lib/api";
import { ReportDocumentOutline, reportDocumentError, ReportWordWorkspace } from "./report-word-workspace";
import { useBatchDemo } from "./use-batch-demo";

export function BatchDemoWorkspaceGate({ documentIri }: { documentIri: string }) {
  const demo = useBatchDemo(documentIri);
  if (demo.isLoading) return <p className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="size-4 animate-spin" />正在读取文档图谱配置…</p>;
  if (demo.error) return <Alert variant="destructive"><AlertTitle>图谱数据读取失败</AlertTitle>
    <AlertDescription>{reportDocumentError(demo.error)}<Button className="ml-3" variant="outline" onClick={() => void demo.refetch()}>重试</Button></AlertDescription>
  </Alert>;
  if (demo.data?.available) return <BatchDemoWorkspace key={documentIri} data={demo.data} />;
  return <ReportWordWorkspace documentIri={documentIri} />;
}

export function BatchDemoWorkspace({ data }: { data: BatchDemoAvailable }) {
  const { username, role } = getIdentity();
  const source = useQuery({
    queryKey: ["report-word-source", username, role, data.document_iri],
    queryFn: ({ signal }) => getReportDocumentSource(data.document_iri, signal),
    retry: false, refetchOnWindowFocus: false,
  });
  const [location, setLocation] = useState<DocumentLocation | null>(null);
  const [highlight, setHighlight] = useState<string | null>(null);
  const [branch, setBranch] = useState("全部");
  const filters: Record<string, string[]> = {
    全部: [], 工艺链: ["hasSynthesisRoute"], 产品计划: ["describes", "hasProductionPlan"],
    设备: ["usesEquipment"], 物料: ["usesMaterial"], 清洗风险: ["hasCleaningMethod", "hasSafetyRiskAssessment", "hasDegradationPathway", "hasSharedLineData"],
  };
  const relationships = data.graph.relationships.filter((r) => !filters[branch].length || filters[branch].includes(r.predicate_iri.split("/").pop()!));
  const sourceMatches = source.data?.document_hash === data.graph.source_sha256;
  return <div className="grid min-h-0 flex-1 gap-4 lg:grid-cols-[180px_minmax(0,1fr)_minmax(300px,380px)]">
    <Card className="flex min-h-0 flex-col overflow-hidden"><CardHeader className="p-3"><CardTitle className="text-sm">目录</CardTitle></CardHeader>
      <CardContent className="min-h-0 overflow-auto p-2">{source.data && sourceMatches && <ReportDocumentOutline tree={source.data.section_tree} onNavigate={(chapter) => {
        setHighlight(null);
        setLocation({ kind: "section", nodeId: chapter.node_id, anchorBlockId: chapter.source_range.anchor_block_id,
          startBlockId: chapter.source_range.start_block_id, endBlockId: chapter.source_range.end_block_id });
      }} />}</CardContent>
    </Card>
    <Card className="flex min-h-0 flex-col overflow-hidden"><CardHeader className="p-3"><CardTitle className="text-sm">文档预览</CardTitle></CardHeader>
      <CardContent className="min-h-0 flex-1 overflow-auto p-2">
        {source.isLoading && <p className="p-4 text-sm">正在读取原件…</p>}
        {source.error && <Alert variant="destructive"><AlertDescription>{reportDocumentError(source.error)}<Button variant="outline" onClick={() => void source.refetch()}>重试</Button></AlertDescription></Alert>}
        {source.data && !sourceMatches && <Alert variant="destructive"><AlertDescription>原件版本已变化，请刷新页面重新校验演示数据。</AlertDescription></Alert>}
        {source.data && sourceMatches && <WordViewer content={source.data.content} activeLocation={location} highlightRef={highlight} fitTables />}
      </CardContent>
    </Card>
    <Card className="flex min-h-0 flex-col overflow-hidden"><CardHeader className="space-y-2 p-3">
      <CardTitle className="flex items-center justify-between text-sm">关系图谱<Badge variant="secondary">静态演示</Badge></CardTitle>
      <p className="text-xs text-muted-foreground">{data.graph.relationships.length} 条一级关系 · 展开工艺描述查看步骤与中间体</p>
      <div className="flex flex-wrap gap-1">{Object.keys(filters).map((label) => <Button key={label} size="sm" variant={branch === label ? "secondary" : "ghost"} className="h-6 px-2 text-xs" onClick={() => setBranch(label)}>{label}</Button>)}</div>
    </CardHeader><CardContent className="min-h-0 flex-1 overflow-auto p-2">
      <RelationPanel key={branch} docClass={data.graph.doc_class} relationships={relationships} collapseProperties
        selectedSourceRef={highlight} onSelectSourceRef={(ref) => { setLocation(null); setHighlight(ref); }} />
    </CardContent></Card>
  </div>;
}
