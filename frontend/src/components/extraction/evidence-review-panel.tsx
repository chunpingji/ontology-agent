"use client";

import { memo, useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import {
  commitEvidence, getIdentity, getJobEvidence, getEvidenceCoverage,
  fillEvidenceGaps, confirmEvidenceDiscovery,
  retryEvidenceCommit, reviewEvidenceCandidate,
  decideCalculation,
  type EvidenceAnchor, type EvidenceCandidate, type EvidenceJob, type EvidenceCoverage,
  type EvidenceExtractOptions,
} from "@/lib/api";
import { evidenceLabel, evidenceValue, publishableEvidence } from "@/lib/evidence-graph";
import { EvidenceGraphTree } from "./evidence-graph-tree";
import type { CalculationAction } from "./pde-calculation-card";

type EvidenceReviewProps = {
  jobId: string; onSource: (anchor: EvidenceAnchor) => void;
  templateId?: string; refreshKey?: number; running?: boolean; onSnapshot: (id: string | null) => void;
  onContinue: (options: EvidenceExtractOptions) => void;
};

export const EvidenceReviewPanel = memo(function EvidenceReviewPanel(props: EvidenceReviewProps) {
  return <EvidenceReviewSession key={`${props.jobId}:${props.templateId ?? ""}`} {...props} />;
});

function EvidenceReviewSession({ jobId, templateId, refreshKey = 0, running = false, onSource, onSnapshot, onContinue }: EvidenceReviewProps) {
  const [data, setData] = useState<EvidenceJob | null>(null);
  const [coverageResult, setCoverage] = useState<EvidenceCoverage | null>(null);
  const [coverageError, setCoverageError] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [actionResult, setActionResult] = useState("");
  const [rejectId, setRejectId] = useState<string | null>(null);
  const [rejectReason, setRejectReason] = useState("");
  const [taskReason, setTaskReason] = useState("");
  const pendingCommit = useRef<{ key: string; items: string } | null>(null);
  const mounted = useRef(false);
  const readRequest = useRef<AbortController | null>(null);
  const mayEdit = getIdentity().role === "senior_analyst";
  const coverage = !running && coverageResult?.availability === "available" ? coverageResult : null;
  const candidates = data?.candidates ?? [];
  const rejecting = candidates.find((candidate) => candidate.candidate_id === rejectId);
  const publishable = publishableEvidence(candidates).slice(0, 1000);

  const refresh = useCallback(async () => {
    readRequest.current?.abort();
    const controller = new AbortController();
    readRequest.current = controller;
    const current = () => mounted.current && !controller.signal.aborted;
    await Promise.all([
      getJobEvidence(jobId, controller.signal, "latest_run").then((result) => {
        if (current()) { setData(result); setError(""); if (!running) onSnapshot(result.snapshot_id); }
      }).catch((e) => { if (current()) setError(`识别结果读取失败：${String(e)}`); }),
      !running && getEvidenceCoverage(jobId, templateId, controller.signal).then((result) => {
        if (current()) { setCoverage(result); setCoverageError(""); }
      }).catch((e) => {
        if (current()) { setCoverage(null); setCoverageError(String(e)); }
      }),
    ]);
  }, [jobId, templateId, onSnapshot, running]);

  useEffect(() => {
    mounted.current = true;
    void refresh();
    return () => { mounted.current = false; readRequest.current?.abort(); };
  }, [refresh, refreshKey]);

  async function act<T>(action?: () => Promise<T>, message?: string | ((result: T) => string),
    afterSuccess?: (result: T) => void) {
    readRequest.current?.abort();
    setBusy(true); setError(""); setActionResult(""); setCoverageError("");
    try {
      if (action) {
        const result = await action();
        if (!mounted.current) return;
        afterSuccess?.(result);
        setActionResult(typeof message === "function" ? message(result) : message ?? "操作已完成。");
      }
      if (!mounted.current) return;
      setCoverage(null);
      await refresh();
    } catch (e) {
      if (mounted.current) setError(String(e));
    } finally { if (mounted.current) setBusy(false); }
  }

  function publish() {
    const items = publishable.map((candidate) => ({ candidate_id: candidate.candidate_id, revision: candidate.revision }))
      .sort((a, b) => a.candidate_id.localeCompare(b.candidate_id));
    const serialized = JSON.stringify(items);
    if (pendingCommit.current?.items !== serialized) pendingCommit.current = { key: crypto.randomUUID(), items: serialized };
    return act(() => commitEvidence(jobId, pendingCommit.current!.key, items),
      (result) => result.status === "succeeded" ? "已发布通过项，可用于报告。"
        : result.status === "failed" ? "发布失败，请查看错误后重试。" : "正在发布通过项。",
      (result) => { if (result.status === "succeeded") pendingCommit.current = null; });
  }

  function reject() {
    if (!rejecting || !rejectReason.trim()) return;
    return act(() => reviewEvidenceCandidate(rejecting.candidate_id, {
      expected_revision: rejecting.revision, expected_review_status: rejecting.review_status,
      decision: "rejected", reason: rejectReason.trim(),
    }), "已拒绝此项并记录理由，相关依赖项已停止用于后续报告。",
    () => { setRejectId(null); setRejectReason(""); });
  }

  const handleCalculation: CalculationAction = useCallback(async (result, choice, reason) => {
    readRequest.current?.abort();
    setBusy(true);
    try {
      await decideCalculation(jobId, { subject_candidate_id: result.subject_candidate_id,
        calculation_id: result.calculation_id, expected_revision: result.decision_revision, choice, reason });
      if (mounted.current) { setActionResult("PDE 处理结果已记录，将用于后续报告。"); await refresh(); }
    } finally { if (mounted.current) setBusy(false); }
  }, [jobId, refresh]);

  const handleReject = useCallback((candidate: EvidenceCandidate) => {
    setRejectId(candidate.candidate_id); setRejectReason(""); setError("");
  }, []);

  const diagnostics = new Map<string, number>();
  for (const code of data?.run?.diagnostics ?? []) diagnostics.set(code, (diagnostics.get(code) ?? 0) + 1);
  const diagnosticLabels: Record<string, string> = {
    ambiguous_source_quote: "原文引用存在歧义", source_quote_outside_scope: "原文引用超出允许范围",
    model_unavailable: "模型调用不可用",
    model_request_failed: "模型请求失败，尚未完成验证",
    model_timeout: "模型响应超时", model_total_timeout: "模型排队与响应已达到总等待时限",
    model_cancelled: "请求已取消，可从断点继续", model_partial_refusal: "部分提案缺少支持",
    source_excerpt_mismatch: "引用与原文不一致", unsupported_type: "实体类型缺少原文支持",
    context_budget_exceeded: "完整原文记录超出上下文预算，尚未完成验证",
    incomplete_record_target: "逻辑记录不完整，未启动识别",
    unknown_or_disallowed_citation_source: "引用来源不在当前字段允许范围",
    record_mapping_mismatch: "引用与原文记录结构不一致",
    condition_review_incomplete: "原文条件尚未逐项完成复核",
    condition_review_conflict: "原文条件复核结果存在冲突",
  };
  const counts = {
    entity: candidates.filter((candidate) => candidate.kind === "entity").length,
    property: candidates.filter((candidate) => candidate.kind === "property").length,
    relationship: candidates.filter((candidate) => candidate.kind === "relationship").length,
    rejected: candidates.filter((candidate) => candidate.review_status === "rejected").length,
  };
  return <section className="space-y-3 p-3 text-sm" aria-label="关系图谱识别结果">
    <p className="text-xs leading-relaxed text-muted-foreground">通过系统校验的识别结果默认通过审核。发现问题时，展开对应节点，提出异议并填写拒绝理由。</p>
    {data && <p className="text-xs text-muted-foreground">{counts.entity} 个实体 · {counts.property} 个属性 · {counts.relationship} 条关系
      {counts.rejected > 0 && <span className="ml-2 text-destructive">{counts.rejected} 项已拒绝</span>}</p>}
    <div className="flex flex-wrap items-center gap-2">
      {mayEdit && <Button size="sm" disabled={busy || !publishable.length}
        title={!publishable.length ? "暂无尚未发布且通过审核的识别结果" : "将通过项发布为报告可用的数据"}
        onClick={publish}>发布通过项（{publishable.length}）</Button>}
      <Button variant="outline" size="sm" disabled={busy} onClick={() => act()}>刷新状态</Button>
      <span className="text-xs text-muted-foreground">{data?.snapshot_id ? "已发布" : "尚未发布"}</span>
    </div>
    {data?.run?.completion === "incomplete" && <p role="status" className="text-xs text-amber-700">识别尚未完成，当前展示已识别的结果。</p>}
    {data?.extraction_version?.outdated && <p role="status" className="text-xs text-amber-700">当前包含旧版本识别结果，可通过“重新识别”更新。</p>}
    {error && !rejecting && <p role="alert" className="break-words text-destructive">{error}</p>}
    {actionResult && <p role="status" className="break-words text-xs text-emerald-700">{actionResult}</p>}
    {!data && !error && <p role="status">加载识别结果中…</p>}
    {data?.calculation_required && !data.calculations?.length && <p role="status" className="text-xs text-amber-700">
      PDE 尚未校验：等待识别共线评估实体及其毒理参数。识别审核通过不代表 PDE 计算通过。
    </p>}
    {data && !candidates.length && <p className="text-muted-foreground">暂无识别结果。完成文档识别后，实体及属性将在此处显示。</p>}
    <EvidenceGraphTree candidates={candidates} schema={data?.graph_schema} busy={busy} onSource={onSource}
      branchProgress={data?.run?.branch_progress} executionStatus={data?.execution_status}
      calculations={data?.calculations} onCalculation={mayEdit ? handleCalculation : undefined}
      onReject={mayEdit ? handleReject : undefined} />

    <Dialog open={!!rejecting} onOpenChange={(open) => { if (!open && !busy) { setRejectId(null); setError(""); } }}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>拒绝识别结果</DialogTitle>
          <DialogDescription>填写异议原因。拒绝后，此项及依赖它的属性、关系将不再用于后续报告。</DialogDescription>
        </DialogHeader>
        {rejecting && <p className="break-words font-medium">{evidenceLabel(rejecting)}{rejecting.literal ? `：${evidenceValue(rejecting)}` : ""}</p>}
        <label className="space-y-1 text-sm">拒绝理由<span className="ml-1 text-destructive">（必填）</span>
          <textarea aria-label="拒绝理由" className="mt-1 w-full rounded border p-2" rows={3} maxLength={4000}
            placeholder="例如：原文描述的是生产计划，并非已完成的生产记录。" value={rejectReason}
            onChange={(event) => setRejectReason(event.target.value)} disabled={busy} />
        </label>
        {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
        <DialogFooter>
          <Button variant="outline" disabled={busy} onClick={() => setRejectId(null)}>取消</Button>
          <Button variant="destructive" disabled={busy || !rejectReason.trim()}
            title={!rejectReason.trim() ? "请先填写拒绝理由" : undefined} onClick={reject}>{busy ? "正在保存…" : "确认拒绝"}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>

    {coverageResult?.availability === "unavailable" && <div role="status" className="space-y-1 rounded border p-2 text-xs text-amber-700">
      <p>报告数据检查暂不可用：{coverageResult.error.message}</p>
      <Link className="underline" href={coverageResult.template_id
        ? `/settings/ast-templates/${encodeURIComponent(coverageResult.template_id)}` : "/settings/ast-templates"}>打开模板设置</Link>
    </div>}
    {coverageError && <div role="status" className="rounded border p-2 text-xs text-amber-700">
      <p>报告数据检查加载失败，可刷新重试。识别结果和操作已单独保存。</p>
      <details><summary>查看错误详情</summary><p className="break-words">{coverageError}</p></details>
    </div>}
    {coverage && <details className="rounded border p-2 text-xs">
      <summary className="cursor-pointer">报告所需数据：{coverage.required_gaps} 项缺口 · {{ ready: "已满足", invalid: "数据无效", conflict: "存在冲突", incomplete: "未完成" }[coverage.material_status]}</summary>
      <ul className="mt-2 space-y-2">{coverage.tasks.map((task, index) => <li key={task.coverage_task_id ?? `${task.target_id}-${index}`}>
        <p>{task.label} · {{ filled: "已满足", missing: "缺失", confirmed_absent: "有证据确认不存在", not_applicable: "不适用",
          pending_review: "待审核", conflict: "冲突", incomplete: "未完成", invalid: "无效", unavailable: "来源不可用" }[task.status]}</p>
        {mayEdit && task.coverage_task_id && task.reason === "object_universe_open" && <Button size="sm" variant="outline"
          disabled={busy || !taskReason.trim() || data?.run?.completion !== "complete" || !coverage.snapshot_id}
          title={!taskReason.trim() ? "请在识别工具中填写操作理由" : undefined}
          onClick={() => act(() => confirmEvidenceDiscovery(jobId, { manifest_id: coverage.manifest_id, template_id: templateId,
            coverage_task_ids: [task.coverage_task_id!], reason: taskReason }), "已确认本项对象全部列出。")}>确认对象已全部列出</Button>}
      </li>)}</ul>
    </details>}
    <details className="border-t pt-2 text-xs">
      <summary className="cursor-pointer text-muted-foreground">识别进度与工具</summary>
      {!!diagnostics.size && <p className="my-2 text-amber-700">{[...diagnostics].map(([code, count]) => `${diagnosticLabels[code] ?? code}${count > 1 ? `（${count} 次）` : ""}`).join("；")}</p>}
      {mayEdit && <div className="mt-2 space-y-2">
        <Button size="sm" variant="outline" disabled={busy || running}
          onClick={() => onContinue({ pause_after: 8 })}>继续未处理任务（每批最多 8 项）</Button>
        <label className="block">重试／补充识别理由
          <textarea aria-label="识别操作理由" className="mt-1 w-full rounded border p-2" value={taskReason} maxLength={4000}
            onChange={(event) => setTaskReason(event.target.value)} />
        </label>
        <div className="flex flex-wrap gap-2">
          <Button size="sm" variant="outline" disabled={busy || running || !taskReason.trim()} title={!taskReason.trim() ? "请填写识别操作理由" : undefined}
            onClick={() => onContinue({ retry_failed: true, reason: taskReason, pause_after: 8 })}>重试失败任务</Button>
          {coverage && <Button size="sm" variant="outline" disabled={busy || !taskReason.trim() || !coverage.snapshot_id}
            title={!coverage.snapshot_id ? "请先发布通过项" : !taskReason.trim() ? "请填写识别操作理由" : undefined}
            onClick={() => act(() => fillEvidenceGaps(jobId, { manifest_id: coverage.manifest_id, template_id: templateId, reason: taskReason }),
              (result) => `已补充识别 ${result.created} 项。`)}>补充识别缺失数据</Button>}
        </div>
      </div>}
    </details>
    {data?.commits.filter((commit) => commit.status === "failed").map((commit) => <div key={commit.commit_id} className="rounded border p-2 text-xs">
      <p className="text-destructive">发布失败：{commit.error}</p>
      {mayEdit && <Button size="sm" variant="outline" disabled={busy}
        onClick={() => act(() => retryEvidenceCommit(commit.commit_id), (result) => result.status === "succeeded" ? "已重新发布通过项。" : "发布仍未成功，请查看错误详情。")}>重试发布</Button>}
    </div>)}
  </section>;
}
