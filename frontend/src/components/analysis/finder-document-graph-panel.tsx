"use client";

import Link from "next/link";
import { useState } from "react";
import { RelationPanel } from "@/components/extraction/relation-panel";
import { WordViewer, type DocumentLocation } from "@/components/extraction/word-viewer";
import { Button } from "@/components/ui/button";
import { ReportDocumentOutline, reportDocumentError } from "@/components/reports/report-word-workspace";
import { ExpertOpinionEntry } from "@/components/reports/expert-opinion-entry";
import { useTemplateFinder, type TemplateFinderModel } from "./use-template-finder";

const stages: Record<string, string> = {
  not_started: "尚未识别", queued: "等待执行", parsing: "解析原文", finding: "本体指引1.0识别",
  rendering: "整理展示", running: "识别中", completed: "已完成", failed: "执行失败", interrupted: "执行已中断",
};

export function FinderDocumentGraphPanel({ model }: { model: TemplateFinderModel }) {
  const status = model.status;
  return <div className="flex min-h-0 flex-1 flex-col" aria-label="本体指引1.0图谱">
    <div className="space-y-2 border-b p-3">
      <p className="font-semibold">本体指引1.0</p>
      <p className="text-xs text-muted-foreground" role="status">
        {stages[status?.stage ?? status?.status ?? "not_started"] ?? status?.stage}
        {status?.counts.nodes != null && ` · ${status.counts.nodes} 个节点 · ${status.counts.properties ?? 0} 个属性`}
      </p>
      <div className="flex gap-2">
        <Button size="sm" disabled={!model.canStart} onClick={model.start}>
          {model.running ? "本体指引1.0识别中…" : status?.execution_id ? "重新识别" : "开始本体指引1.0识别"}
        </Button>
        <Button size="sm" variant="ghost" onClick={() => void model.refresh()}>刷新</Button>
      </div>
      {status?.stale && <p className="text-xs text-amber-700">输入或配置已变化。当前展示来自上次执行，可重新识别。</p>}
      {(model.error || status?.error) && <p role="alert" className="text-sm text-destructive">
        {model.error ? reportDocumentError(model.error) : status?.error}
      </p>}
      <p className="text-xs text-muted-foreground">展示本体指引1.0取数结果，来源按钮可定位原文。</p>
    </div>
    <div className="min-h-0 flex-1 overflow-auto">
      {model.graph ? <RelationPanel key={model.readerKey} docClass={model.graph.doc_class}
        relationships={model.graph.relationships} onSelectFinderSource={model.select}
        emptyMessage="本次本体指引1.0识别已完成，未找到符合配置的关系。" />
        : <p className="p-4 text-sm text-muted-foreground">{model.running ? "等待本次 Finder 结果…" : "点击开始识别后显示关系与属性。"}</p>}
    </div>
  </div>;
}

export function FinderOriginal({ model, documentIri }: { model: TemplateFinderModel; documentIri?: string }) {
  const [location, setLocation] = useState<{ key: string; value: DocumentLocation } | null>(null);
  return <div className="grid min-h-0 flex-1 gap-3 lg:grid-cols-[170px_minmax(0,1fr)]">
    <div className="min-h-0 overflow-auto rounded border p-2">
      <p className="mb-2 text-sm font-semibold">目录</p>
      {model.source?.section_tree && <ReportDocumentOutline key={model.readerKey}
        tree={model.source.section_tree} onNavigate={(node) => {
          model.select(null);
          setLocation({ key: model.readerKey, value: { kind: "section", nodeId: node.node_id,
            anchorBlockId: node.source_range.anchor_block_id, startBlockId: node.source_range.start_block_id,
            endBlockId: node.source_range.end_block_id } });
        }} />}
    </div>
    <div className="min-h-0 min-w-0 overflow-auto rounded border bg-card p-4">
      <div className="mb-3 flex items-center justify-between gap-2">
        <span className="text-sm font-semibold">{model.source?.filename ?? "文档原文"}</span>
        {documentIri && model.status?.template_id && <Link className="text-xs text-primary underline"
          href={{ pathname: `/reports/${encodeURIComponent(documentIri)}`,
            query: { template_id: model.status.template_id } }}>在报告中心查看</Link>}
        {documentIri && <ExpertOpinionEntry target={{ document_iri: documentIri }}
          sourceHash={model.source?.document_hash} />}
      </div>
      {model.loading && <p role="status">正在读取原文…</p>}
      {model.error && <p role="alert" className="text-destructive">{reportDocumentError(model.error)}</p>}
      {model.source && <WordViewer key={model.readerKey} content={model.source.content} fitTables
        activeAnchor={model.anchor} activeLocation={model.anchor ? null :
          location?.key === model.readerKey ? location.value : null} />}
    </div>
  </div>;
}

export function FinderWorkspace({ templateId, sourceJobId, documentIri }: {
  templateId: string; sourceJobId: string; documentIri?: string;
}) {
  const model = useTemplateFinder(templateId, sourceJobId);
  return <div className="grid min-h-[500px] flex-1 gap-3 lg:grid-cols-[minmax(0,1fr)_360px]">
    <FinderOriginal model={model} documentIri={documentIri} />
    <div className="flex min-h-0 flex-col rounded border"><FinderDocumentGraphPanel model={model} /></div>
  </div>;
}
