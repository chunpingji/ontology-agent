"use client";

import { useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Eye, FileDown, Loader2, RotateCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { WordViewer } from "@/components/extraction/word-viewer";
import { ReportHistoryList } from "@/components/extraction/report-history-list";
import { downloadReport, listReports, type DocumentEvidenceIR, type EvidenceAnchor, type GeneratedReportDTO, type TiptapContent } from "@/lib/api";
import { downloadArtifact, reportGet, reportPost, requestKey, type OutputNode, type ReportRun, type TemplateV2 } from "@/lib/reporting-v2";
import { type ReportInputSnapshot, type ReportOutputResult } from "@/lib/report-preview";
import { OutputPreview } from "./output-preview";
import { ReportPreviewDashboard } from "./report-preview-dashboard";
import { ReportSigningPanel } from "./report-signing-panel";

type RunSelection = { id: string; config: string; mode: "data" | "report" };
const fieldClass = "mt-1 block w-full min-w-0 rounded border bg-background p-2 text-sm";
const running = (run?: ReportRun) => run?.execution_status === "pending" || run?.execution_status === "running";

export function TemplateReportPreview({ templateId, defaultSourceJobId, draftSchema, templateName }: {
  templateId?: string; templateName?: string;
  defaultSourceJobId?: string | null; draftSchema: TemplateV2;
}) {
  const queryClient = useQueryClient();
  const [selection, setSelection] = useState<RunSelection | null>(null);
  const [report, setReport] = useState<RunSelection | null>(null);
  const [sources, setSources] = useState<Record<string, string>>({});
  const sourceSlots = draftSchema.source_slots;
  // Follow the source workspace until the user explicitly chooses a report source.
  const sourceSelections = Object.fromEntries(sourceSlots.map((slot) => [slot.source_slot_id,
    sources[slot.source_slot_id] ?? (sourceSlots.length === 1 ? defaultSourceJobId ?? "" : ""),
  ]));
  const [records, setRecords] = useState("{}");
  const [at, setAt] = useState("");
  const [trace, setTrace] = useState<unknown>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [confirmGenerate, setConfirmGenerate] = useState(false);
  const [layout, setLayout] = useState<{ schema: string; ast: OutputNode } | null>(null);
  const [anchor, setAnchor] = useState<EvidenceAnchor | null>(null);
  const sourceArea = useRef<HTMLDivElement>(null);
  const pendingRequests = useRef(new Map<string, string>());
  const schemaKey = JSON.stringify(draftSchema);
  const config = JSON.stringify([draftSchema, sourceSelections, records, at]);
  const currentQuery = useQuery({ queryKey: ["report-run", selection?.id], enabled: !!selection,
    queryFn: () => reportGet<ReportRun>("report-runs/" + selection!.id),
    refetchInterval: (query) => running(query.state.data) ? 2000 : false });
  const current = currentQuery.data;
  const generatedQuery = useQuery({ queryKey: ["report-run", report?.id], enabled: !!report,
    queryFn: () => reportGet<ReportRun>("report-runs/" + report!.id),
    refetchInterval: (query) => running(query.state.data) ? 2000 : false });
  const inputsQuery = useQuery({ queryKey: ["report-inputs", current?.input_snapshot_id], enabled: !!current?.input_snapshot_id,
    queryFn: () => reportGet<ReportInputSnapshot>("report-runs/" + current!.run_id + "/inputs") });
  const snapshot = inputsQuery.data;
  const outputsQuery = useQuery({ queryKey: ["report-outputs", selection?.id, current?.attempt, current?.execution_status], enabled: !!current?.input_snapshot_id,
    queryFn: () => reportGet<ReportOutputResult[]>("report-runs/" + selection!.id + "/outputs") });
  const jobs = useQuery({ queryKey: ["report-source-jobs"], enabled: !!templateId,
    queryFn: () => reportGet<{ id: string; source_filename: string; status: string }[]>("extraction/jobs") });
  const sourceJobIds = [...new Set(Object.values(sourceSelections).filter(Boolean))];
  const displayedJobIds = snapshot ? [...new Set(Object.values(snapshot.source_bundle.sources)
    .map((source) => source.job_id).filter((id): id is string => !!id))] : sourceJobIds;
  const historyQuery = useQuery({ queryKey: ["report-preview-history", sourceJobIds], enabled: sourceJobIds.length > 0,
    queryFn: async () => (await Promise.all(sourceJobIds.map(listReports))).flat().sort((a, b) => b.created_at.localeCompare(a.created_at)) });
  const history = historyQuery.data ?? [];
  const isReport = selection?.mode === "report";
  const signingRun = isReport ? current : generatedQuery.data;
  const artifact = current?.artifacts.find((item) => item.format === "docx") ?? current?.artifacts[0];
  const latestDownload = history.find((item) => item.report_artifact_id || item.file_size);
  const stale = !!selection?.config && selection.config !== config;
  const hasSources = sourceSlots.every((slot) => slot.required === false || !!sourceSelections[slot.source_slot_id]);
  const disabled = !!busy || running(current);
  const canRetry = current?.execution_status === "failed" || (running(current) && !!current?.lease_expires_at
    && Date.parse(current.lease_expires_at) <= currentQuery.dataUpdatedAt);

  async function action(name: string, fn: () => Promise<void>) {
    setError(""); setBusy(name);
    try { await fn(); } catch (e) { setError(e instanceof Error ? e.message : "操作失败"); }
    finally { setBusy(""); }
  }
  async function create(mode: "data" | "report") {
    const recordRefs: unknown = JSON.parse(records);
    if (!recordRefs || typeof recordRefs !== "object" || Array.isArray(recordRefs)
      || Object.values(recordRefs).some((value) => typeof value !== "string")) throw new Error("记录引用需填写为记录名称与版本 ID 的 JSON 对象。");
    const payload = {
      mode, template_id: templateId, applicable_at: at || null, draft_schema: draftSchema,
      source_bindings: Object.fromEntries(Object.entries(sourceSelections).filter(([, job]) => job).map(([slot, job_id]) => [slot, { job_id }])),
      record_refs: recordRefs,
    };
    const hash = JSON.stringify(payload);
    if (!pendingRequests.current.has(hash)) pendingRequests.current.set(hash, requestKey());
    const result = await reportPost<ReportRun>("report-previews", { ...payload, idempotency_key: pendingRequests.current.get(hash) });
    // Retry uncertain requests with the same key; each successful refresh freezes a new snapshot.
    pendingRequests.current.delete(hash);
    queryClient.setQueryData(["report-run", result.run_id], result);
    const next = { id: result.run_id, config, mode };
    setSelection(next); setTrace(null); setLayout(null); setAnchor(null);
    if (mode === "report") { setReport(next); void historyQuery.refetch(); }
  }
  function generate() {
    if (snapshot && !stale && snapshot.material_status !== "ready") setConfirmGenerate(true);
    else void action("generate", () => create("report"));
  }
  function locateSource(node: OutputNode) {
    function find(value: unknown): EvidenceAnchor | null {
      if (!value || typeof value !== "object") return null;
      const object = value as Record<string, unknown>;
      if (["document_hash", "parser_version", "structure_hash", "evidence_id", "section_node_id", "block_id"].every((key) => typeof object[key] === "string")) return object as unknown as EvidenceAnchor;
      for (const child of Object.values(object)) { const found = find(child); if (found) return found; }
      return null;
    }
    const found = find(node.provenance_refs);
    if (!found) { setError("该引用未包含可定位的原文锚点，请查看「原文定位」中的依据记录。"); return; }
    setAnchor(found); sourceArea.current?.scrollIntoView({ block: "start", behavior: "smooth" });
  }
  async function historyDownload(item: GeneratedReportDTO) {
    const blob = await downloadReport(item.job_id, item.id);
    const url = URL.createObjectURL(blob), link = document.createElement("a");
    link.href = url; link.download = `report-${item.id.slice(0, 8)}.docx`; link.click(); URL.revokeObjectURL(url);
  }

  return <div className="flex flex-col gap-4 px-6 py-4">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <span className="text-sm font-semibold text-foreground">AST 覆盖率</span>
      <div className="flex flex-wrap items-center gap-1.5">
        <Button size="sm" variant="ghost" disabled={disabled} onClick={() => action("layout", async () => {
          const result = await reportPost<{ body_ast: OutputNode }>("report-previews", { mode: "layout", draft_schema: draftSchema, idempotency_key: requestKey() });
          setLayout({ schema: schemaKey, ast: result.body_ast });
        })}><Eye className="mr-1 size-3.5" />版式预览</Button>
        <Button variant="outline" size="sm" disabled={disabled || !templateId || !hasSources} onClick={() => action("coverage", () => create("data"))}>
          {busy === "coverage" ? <Loader2 className="mr-1 size-3.5 animate-spin" /> : <RotateCw className="mr-1 size-3.5" />}刷新覆盖率
        </Button><Button size="sm" disabled={disabled || !templateId || !hasSources} onClick={generate}>
          {busy === "generate" || (isReport && running(current)) ? <Loader2 className="mr-1 size-3.5 animate-spin" /> : <FileDown className="mr-1 size-3.5" />}
          {busy === "generate" || (isReport && running(current)) ? "生成中..." : "生成报告"}
        </Button>
      </div>
    </div>
    {templateId && <details className="text-sm">
      <summary className="cursor-pointer text-xs text-muted-foreground">来源与生成设置 · {sourceJobIds.length ? sourceJobIds.map((id) => jobs.data?.find((job) => job.id === id)?.source_filename || "已关联源文档").join("、") : "尚未关联源文档"}</summary>
      <div className="mt-3 space-y-3 rounded-lg border bg-card p-4">
        {sourceSlots.map((slot, index) => <label key={slot.source_slot_id} className="block text-sm">
          {sourceSlots.length === 1 ? "源文档" : `源文档 ${index + 1}`}{slot.required === false && "（可选）"}<span className="ml-2 text-xs text-muted-foreground">{slot.source_slot_id}</span>
          <select aria-label={`报告来源 ${slot.source_slot_id}`} className={fieldClass} value={sourceSelections[slot.source_slot_id]}
            onChange={(e) => setSources({ ...sources, [slot.source_slot_id]: e.target.value })}>
            <option value="">尚未选择来源</option>
            {sourceSelections[slot.source_slot_id] && !jobs.data?.some((job) => job.id === sourceSelections[slot.source_slot_id]) && <option value={sourceSelections[slot.source_slot_id]}>已关联源文档</option>}
            {jobs.data?.map((job) => <option key={job.id} value={job.id}>{job.source_filename}</option>)}
          </select>
        </label>)}
        <label className="block text-sm">适用时间<input type="text" className={fieldClass} value={at} placeholder="使用契约约定的时间" onChange={(e) => setAt(e.target.value)} /></label>
        <details><summary className="cursor-pointer text-xs text-muted-foreground">已审核参数与工作流记录</summary>
          <textarea aria-label="记录引用" className={fieldClass + " font-mono"} value={records} onChange={(e) => setRecords(e.target.value)} />
        </details>
      </div>
    </details>}
    {!hasSources && <p className="text-sm text-muted-foreground">需先在「源文档」页签关联文档，或在「来源与生成设置」中选择来源。</p>}
    {!templateId && <p className="text-sm text-muted-foreground">保存模板后，可选择源文档生成报告草稿。</p>}
    {stale && <p role="status" className="rounded border bg-muted/50 px-3 py-2 text-xs text-muted-foreground">模板或来源设置已更改。下方仍为上次固定的检查结果，刷新覆盖率或生成报告后更新。</p>}
    {[error, currentQuery.error?.message, inputsQuery.error?.message, outputsQuery.error?.message, jobs.error?.message, historyQuery.error?.message, current?.error?.message || current?.error?.code].filter(Boolean).map((message, index) => <div key={index} role="alert" className="max-h-48 overflow-auto whitespace-pre-wrap break-words rounded border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">{message}</div>)}
    <ReportPreviewDashboard template={snapshot?.source_bundle.template ?? draftSchema} templateName={templateName}
      snapshot={snapshot} outputs={outputsQuery.data ?? []} current={current} hasSources={hasSources} isReport={isReport}
      generating={busy === "generate" || (isReport && running(current))} refreshing={busy === "coverage" || (!isReport && running(current))}
      onLocateSource={locateSource} onDownload={artifact && current
        ? () => action("download", () => downloadArtifact(current.run_id, artifact.artifact_id))
        : !current && latestDownload ? () => action("download", () => historyDownload(latestDownload)) : undefined} />
    {current && <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
      <span>材料：{({ ready: "齐备", incomplete: "不完整", conflict: "存在冲突", invalid: "无效" } as Record<string, string>)[current.material_status] || "待检查"}</span>
      <span>· 第 {current.attempt} 次生成</span>
      {canRetry && <Button size="sm" variant="outline" disabled={!!busy} onClick={() => action("retry", async () => {
        const result = await reportPost<ReportRun>("report-runs/" + current.run_id + "/attempts", { expected_revision: current.revision_no });
        queryClient.setQueryData(["report-run", current.run_id], result); void historyQuery.refetch();
      })}>重试未完成内容</Button>}
      {report && selection?.id !== report.id && <Button size="sm" variant="ghost" onClick={() => setSelection(report)}>返回已生成报告</Button>}
      <details className="w-full">
        <summary className="cursor-pointer py-1">输入与生成记录</summary>
        <p className="py-2 break-all">报告 {current.run_id} · 输入快照 {current.input_snapshot_id ?? "尚未固定"}</p>
        <div className="flex flex-wrap gap-2">{(["inputs", "coverage", "outputs"] as const).map((path, index) => <Button key={path} size="sm" variant="ghost" disabled={!!busy || !current.input_snapshot_id} onClick={() => action("trace", async () => {
          setTrace(await reportGet("report-runs/" + current.run_id + "/" + path));
        })}>{["查看输入", "完整性与缺口", "生成与引用记录"][index]}</Button>)}</div>
        {trace != null && <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-all rounded border p-3 text-xs">{JSON.stringify(trace, null, 2)}</pre>}
      </details>
    </div>}
    {history.length > 0 && <section aria-label="历史报告" className="rounded-lg border bg-card p-4">
      <h4 className="mb-2 text-sm font-semibold">历史报告 ({history.length})</h4>
      <ReportHistoryList reports={history} canDownload={(item) => !busy && !!(item.report_artifact_id || item.file_size)}
        onDownload={(item) => void action("download", () => historyDownload(item))}
        onView={(item) => { setSelection({ id: item.report_run_id!, config: "", mode: "report" }); setTrace(null); setLayout(null); }} />
    </section>}
    {layout?.schema === schemaKey && layout && <section aria-label="版式预览" className="rounded-lg border bg-card p-4">
      <div className="mb-2 flex items-center justify-between"><h4 className="text-sm font-semibold">版式预览</h4><Button size="sm" variant="ghost" onClick={() => setLayout(null)}>收起版式预览</Button></div>
      <OutputPreview ast={layout.ast} />
    </section>}
    {current?.body_ast && <details className="rounded-lg border bg-card p-4">
      <summary className="cursor-pointer text-sm font-semibold">{isReport ? "报告正文" : "数据预览"}</summary>
      <div className="mt-3"><OutputPreview key={`${current.run_id}-${current.attempt}`} ast={current.body_ast} onLocateSource={locateSource} /></div>
    </details>}
    {displayedJobIds.length > 0 && <div ref={sourceArea} className="space-y-4">{displayedJobIds.map((jobId) => <SourceDocument key={jobId} jobId={jobId} anchor={anchor}
      filename={jobs.data?.find((job) => job.id === jobId)?.source_filename} />)}</div>}
    {signingRun?.body_hash && signingRun.execution_status === "completed" && <details className="rounded-lg border bg-card p-4">
      <summary className="cursor-pointer text-sm font-semibold">正文审核与签署</summary>
      <div className="mt-3"><ReportSigningPanel key={signingRun.run_id} run={signingRun} /></div>
    </details>}
    <Dialog open={confirmGenerate} onOpenChange={setConfirmGenerate}><DialogContent><DialogHeader><DialogTitle>覆盖率不完整</DialogTitle></DialogHeader>
      <p className="text-sm">当前材料仍有未解决的缺口。生成的报告草稿会保留缺失标记，材料齐备后才能封装正式报告。</p>
      <div className="flex justify-end gap-2 pt-2"><Button variant="outline" onClick={() => setConfirmGenerate(false)}>取消</Button>
        <Button onClick={() => { setConfirmGenerate(false); void action("generate", () => create("report")); }}>仍然生成</Button></div>
    </DialogContent></Dialog>
  </div>;
}

function SourceDocument({ jobId, filename, anchor }: { jobId: string; filename?: string; anchor: EvidenceAnchor | null }) {
  const documentQuery = useQuery({ queryKey: ["report-source-document", jobId],
    queryFn: () => reportGet<{ content: TiptapContent }>("extraction/jobs/" + jobId + "/annotated-document") });
  const documentHash = (documentQuery.data?.content?.analysis as DocumentEvidenceIR | undefined)?.document_hash;
  return <section aria-label="报告源文档" className="rounded-lg border bg-card p-4">
    <div className="mb-2 flex items-center justify-between gap-2"><h4 className="text-sm font-semibold">源文档</h4><span className="truncate text-xs text-muted-foreground">{filename}</span></div>
    {documentQuery.isLoading ? <Loader2 className="m-4 size-5 animate-spin text-muted-foreground" /> : documentQuery.data?.content ? <div className="max-h-[50vh] overflow-auto">
      <WordViewer content={documentQuery.data.content} activeAnchor={anchor?.document_hash === documentHash ? anchor : null} fitTables />
    </div> : <p className="py-4 text-sm text-muted-foreground">{documentQuery.isError ? "源文档暂时无法加载，请在「源文档」页签检查文档解析结果。" : "暂无可预览的源文档正文。"}</p>}
  </section>;
}
