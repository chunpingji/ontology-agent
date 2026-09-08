"use client";

import { useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import {
  downloadArtifact, reportGet, reportPost, requestKey, type OutputNode, type ReportRun, type TemplateV2,
} from "@/lib/reporting-v2";
import { OutputPreview } from "./output-preview";
import { ReportSigningPanel } from "./report-signing-panel";

export function ReportRunPanel({ templateId, runId, sourceSlots = [], defaultSourceJobId, draftSchema }: {
  templateId?: string; runId?: string;
  sourceSlots?: { source_slot_id: string; class_iri: string }[];
  defaultSourceJobId?: string | null;
  draftSchema?: TemplateV2;
}) {
  const [run, setRun] = useState<ReportRun | null>(null);
  const [sources, setSources] = useState<Record<string, string>>({});
  // Follow the source workspace until the user explicitly chooses a report source.
  const sourceSelections = Object.fromEntries(sourceSlots.map((slot) => [slot.source_slot_id,
    sources[slot.source_slot_id] ?? (sourceSlots.length === 1 ? defaultSourceJobId ?? "" : ""),
  ]));
  const [mode, setMode] = useState<"data" | "report">("data");
  const [records, setRecords] = useState("{}");
  const [at, setAt] = useState("");
  const [trace, setTrace] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const pendingRequest = useRef<{ hash: string; key: string } | null>(null);
  const existing = useQuery({ queryKey: ["report-run", runId],
    queryFn: () => reportGet<ReportRun>("report-runs/" + runId), enabled: !!runId });
  const jobs = useQuery({ queryKey: ["report-source-jobs"], enabled: !!templateId,
    queryFn: () => reportGet<{ id: string; source_filename: string; status: string }[]>("extraction/jobs") });
  const current = run ?? existing.data;
  async function action(fn: () => Promise<void>) {
    setError(""); setBusy(true);
    try { await fn(); } catch (e) { setError(e instanceof Error ? e.message : "操作失败"); }
    finally { setBusy(false); }
  }
  async function create() {
    const payload = {
      mode, template_id: templateId, applicable_at: at || null,
      draft_schema: draftSchema,
      source_bindings: Object.fromEntries(Object.entries(sourceSelections).filter(([, job]) => job).map(([slot, job_id]) => [slot, { job_id }])),
      record_refs: JSON.parse(records) as Record<string, string>,
    };
    const hash = JSON.stringify(payload);
    if (pendingRequest.current?.hash !== hash) pendingRequest.current = { hash, key: requestKey() };
    const result = await reportPost<ReportRun>("report-previews", { ...payload, idempotency_key: pendingRequest.current.key });
    setRun(result); setTrace(null);
  }
  return <div className="space-y-4">
    {templateId && <div className="border rounded p-4 space-y-3">
      <div className="flex gap-4">{(["data", "report"] as const).map((value) => <label key={value}>
        <input type="radio" name={"mode-" + templateId} checked={mode === value} onChange={() => setMode(value)} />
        {value === "data" ? " 数据预览" : " 报告草稿"}</label>)}</div>
      {sourceSlots.map((slot) => <label key={slot.source_slot_id} className="block text-sm">
        {slot.source_slot_id} · {slot.class_iri}
        <select className="block w-full border rounded p-2 mt-1" value={sourceSelections[slot.source_slot_id]}
          onChange={(e) => setSources({ ...sources, [slot.source_slot_id]: e.target.value })}>
          <option value="">尚未选择来源</option>
          {jobs.data?.map((job) => <option key={job.id} value={job.id}>{job.source_filename} · {job.status}</option>)}
        </select>
      </label>)}
      <label className="block text-sm">适用时间<input type="text" className="border rounded p-2 ml-3" value={at} placeholder="使用契约约定的时间"
        onChange={(e) => setAt(e.target.value)} /></label>
      <details><summary>已审核参数与工作流记录</summary>
        <textarea aria-label="记录引用" className="w-full border rounded p-2 font-mono" value={records} onChange={(e) => setRecords(e.target.value)} />
      </details>
      <Button disabled={busy} onClick={() => action(create)}>{busy ? "处理中…" : "冻结来源并预览"}</Button>
      <Button variant="ghost" disabled={busy} onClick={() => { pendingRequest.current = null; setRun(null); setTrace(null); }}>开始新的报告</Button>
    </div>}
    {error && <p role="alert" className="text-destructive break-all">{error}</p>}
    {existing.isError && <p role="alert">{existing.error.message}</p>}
    {current && <>
      <div className="flex flex-wrap gap-3 text-sm items-center">
        <span>生成：{current.execution_status}</span><span>材料：{current.material_status}</span>
        <span>审核：{current.review_status}</span><span>第 {current.attempt} 次生成</span>
        <Button variant="outline" disabled={busy} onClick={() => action(async () => {
          setRun(await reportPost<ReportRun>("report-runs/" + current.run_id + "/attempts", { expected_revision: current.revision_no }));
        })}>重试未完成内容</Button>
        {current.artifacts.map((a) => <Button key={a.artifact_id} variant="outline" onClick={() => action(() => downloadArtifact(current.run_id, a.artifact_id))}>
          下载{a.purpose === "formal" ? "正式报告" : "固定草稿"}</Button>)}
      </div>
      <p className="text-xs text-muted-foreground break-all">报告 {current.run_id} · 输入快照 {current.input_snapshot_id}</p>
      <div className="flex gap-2">{(["inputs", "coverage", "outputs"] as const).map((path, i) => <Button key={path} size="sm" variant="ghost" onClick={() => action(async () => {
        setTrace(await reportGet("report-runs/" + current.run_id + "/" + path));
      })}>{["查看输入", "完整性与缺口", "生成与引用记录"][i]}</Button>)}</div>
      {trace != null && <details open className="border rounded p-3 max-h-80 overflow-auto"><summary>固定快照详情</summary>
        <pre className="text-xs whitespace-pre-wrap break-all">{JSON.stringify(trace, null, 2)}</pre></details>}
      <OutputPreview ast={current.body_ast as OutputNode | null} />
      {current.body_hash && current.execution_status === "completed" && <ReportSigningPanel key={current.run_id} run={current} />}
    </>}
  </div>;
}
