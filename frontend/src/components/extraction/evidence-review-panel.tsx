"use client";

import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import {
  commitEvidence, createEvidenceCandidate, getIdentity, getJobEvidence, getEvidenceCoverage,
  fillEvidenceGaps, confirmEvidenceDiscovery, extractJobEvidence,
  resolveEvidenceCandidate, retryEvidenceCommit, reviewEvidenceCandidate,
  type EvidenceAnchor, type EvidenceCandidate, type EvidenceJob, type InstanceCoverageManifest,
} from "@/lib/api";

const polarity = { affirmed: "肯定", negated: "否定", conditional: "条件",
  hypothetical: "假设", uncertain: "不确定" };

export function EvidenceReviewPanel({ jobId, templateId, refreshKey = 0, onSource, onSnapshot }: {
  jobId: string; onSource: (anchor: EvidenceAnchor) => void;
  templateId?: string;
  refreshKey?: number;
  onSnapshot: (id: string | null) => void;
}) {
  const [data, setData] = useState<EvidenceJob | null>(null);
  const [coverage, setCoverage] = useState<InstanceCoverageManifest | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [actionResult, setActionResult] = useState("");
  const [reason, setReason] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [newJson, setNewJson] = useState("");
  const [editId, setEditId] = useState<string | null>(null);
  const [editJson, setEditJson] = useState("");
  const [mergeId, setMergeId] = useState("");
  // A lost HTTP response must not mint a second key on retry.
  const pendingCommit = useRef<{ key: string; items: string } | null>(null);
  const mayEdit = getIdentity().role === "senior_analyst";

  useEffect(() => {
    let ignore = false;
    const controller = new AbortController();
    getJobEvidence(jobId, controller.signal).then((result) => {
      if (!ignore) { setData(result); onSnapshot(result.snapshot_id); }
    }).catch((e) => { if (!ignore) setError(String(e)); });
    getEvidenceCoverage(jobId, templateId, controller.signal).then((result) => {
      if (!ignore) setCoverage(result);
    }).catch((e) => { if (!ignore) setError(String(e)); });
    return () => { ignore = true; controller.abort(); };
  }, [jobId, templateId, refreshKey, onSnapshot]);

  async function act(action: () => Promise<unknown>) {
    setBusy(true); setError("");
    try {
      await action();
      const result = await getJobEvidence(jobId);
      setData(result); onSnapshot(result.snapshot_id);
      setCoverage(await getEvidenceCoverage(jobId, templateId));
    } catch (e) {
      setError(String(e));
    } finally { setBusy(false); }
  }

  function review(candidate: EvidenceCandidate, decision: "confirmed" | "rejected") {
    return act(() => reviewEvidenceCandidate(candidate.candidate_id, {
      expected_revision: candidate.revision, decision, reason,
    }));
  }

  function commit() {
    const items = (data?.candidates ?? []).filter((c) => selected.includes(c.candidate_id))
      .map((c) => ({ candidate_id: c.candidate_id, revision: c.revision }))
      .sort((a, b) => a.candidate_id.localeCompare(b.candidate_id));
    const serialized = JSON.stringify(items);
    if (pendingCommit.current?.items !== serialized) {
      pendingCommit.current = { key: crypto.randomUUID(), items: serialized };
    }
    const key = pendingCommit.current.key;
    return act(async () => {
      const result = await commitEvidence(jobId, key, items);
      if (result.status === "succeeded") { pendingCommit.current = null; setSelected([]); }
    });
  }

  const labels = new Map(data?.candidates.filter((c) => c.kind === "entity").map((c) => [c.candidate_id, c.text]));
  return <section className="space-y-3 p-4 text-sm" aria-label="逐值证据审核">
    <h3 className="font-medium">逐值证据审核与提交</h3>
    <p className="text-xs text-muted-foreground">确认仅保存审核结论。否定、条件断言可提交，但不会生成无条件正向事实。</p>
    <p className="break-all text-xs">快照：{data?.snapshot_id ?? "尚未发布"}</p>
    {coverage && <details className="rounded border p-3" open={coverage.required_gaps > 0}>
      <summary className="cursor-pointer font-medium">实例覆盖：{coverage.required_gaps} 项必需缺口</summary>
      <p className="my-2 break-all text-xs">模板 {coverage.template_version} · 清单 {coverage.manifest_id}</p>
      {!coverage.snapshot_id && <p className="text-amber-700">尚未发布事实；候选不能满足正式覆盖。</p>}
      {coverage.diagnostics.map((reason) => <p key={reason} className="text-amber-700">{reason}</p>)}
      <ul className="space-y-2">
        {coverage.tasks.map((task, index) => <li key={task.coverage_task_id ?? `${task.target_id}-${index}`} className="rounded bg-muted p-2">
          <p>{task.label} · {{ filled: "已满足", missing: "缺失", confirmed_absent: "有证据确认不存在",
            not_applicable: "不适用", pending_review: "待审核", conflict: "冲突", incomplete: "未完成" }[task.status]}</p>
          <p className="break-all text-xs">主体：{task.subject_instance_iri ?? task.subject_candidate_ref?.candidate_id ?? "尚未确认"}</p>
          {!!task.subject_path?.length && <p className="break-all text-xs">主体范围：{task.subject_root_class_iri} {task.subject_path.map((step) => `→ ${step.predicate_iri}`).join(" ")}</p>}
          <p className="break-all text-xs">{task.predicate_path?.map((step) => `${step.direction === "inverse" ? "←" : "→"} ${step.predicate_iri}`).join(" ")}</p>
          <p className="text-xs">{task.reason} · 对象集合：{task.object_universe_status ?? "未解析"}</p>
          {task.objects?.filter((obj) => obj.missing_properties.length).map((obj) => <p key={obj.instance_iri} className="break-all text-xs text-amber-700">{obj.text} 缺失：{obj.missing_properties.join("、")}</p>)}
          {task.negative_assertion_ids?.length ? <p className="break-all text-xs">否定断言：{task.negative_assertion_ids.join("、")}</p> : null}
          {mayEdit && task.coverage_task_id && task.reason === "object_universe_open" && <Button size="sm" variant="outline"
            disabled={busy || !reason.trim() || data?.run?.completion !== "complete" || !coverage.snapshot_id}
            onClick={() => act(async () => {
              await confirmEvidenceDiscovery(jobId, { manifest_id: coverage.manifest_id, template_id: templateId,
                coverage_task_ids: [task.coverage_task_id!], reason });
              setActionResult("已记录本项对象集合的审核确认；发现新证据后需重新确认。");
            })}>确认本项对象已全部列出</Button>}
        </li>)}
      </ul>
      {coverage.gap_history?.map((entry, index) => <p key={index} className="text-xs">补抽第 {entry.round} 轮：{entry.reason}</p>)}
      {mayEdit && <Button className="mt-2" size="sm" variant="outline" disabled={busy || !reason.trim() || !coverage.snapshot_id}
        onClick={() => act(async () => {
          const result = await fillEvidenceGaps(jobId, { manifest_id: coverage.manifest_id, template_id: templateId, reason });
          setActionResult(`补抽停止：${result.reason}；新增 ${result.created} 条待审核候选。`);
        })}>按实例缺口补抽（最多两轮）</Button>}
    </details>}
    {data?.run?.completion === "incomplete" && <p role="status" className="text-amber-700">
      抽取未完成：{data.run.diagnostics.join("；") || "请检查任务状态"}
    </p>}
    {error && <p role="alert" className="break-words text-destructive">{error}</p>}
    {actionResult && <p role="status" className="break-words text-amber-700">{actionResult}</p>}
    <Button variant="outline" size="sm" disabled={busy} onClick={() => act(async () => {})}>刷新状态</Button>
    {mayEdit && <>
      <label className="block">审核／修改理由
        <textarea aria-label="审核理由" className="mt-1 w-full rounded border p-2" value={reason}
          onChange={(e) => setReason(e.target.value)} maxLength={4000} />
      </label>
      <div className="flex flex-wrap gap-2">
        <Button size="sm" variant="outline" disabled={busy} onClick={() => act(async () => {
          await extractJobEvidence(jobId, { pause_after: 8 });
          setActionResult("已推进本批未处理任务；失败记录保留，请查看最新完成状态。");
        })}>继续未处理任务（每批最多 8 项）</Button>
        <Button size="sm" variant="outline" disabled={busy || !reason.trim()}
          onClick={() => act(async () => {
            await extractJobEvidence(jobId, { retry_failed: true, reason, pause_after: 8 });
            setActionResult("已显式重试本批失败任务；总预算不重置，新增候选仍需审核。");
          })}>重试失败任务</Button>
      </div>
      <Button size="sm" disabled={busy || !selected.length} onClick={commit}>提交已选的已确认断言（{selected.length}）</Button>
    </>}
    {!data && <p role="status">加载证据中…</p>}
    {data && !data.candidates.length && <p>暂无证据候选；旧关系预览不是已提交事实。</p>}
    {data?.candidates.map((candidate) => <article key={candidate.candidate_id} className="space-y-2 rounded border p-3">
      <div className="flex items-start gap-2">
        {mayEdit && <input type="checkbox" aria-label={`选择 ${candidate.text || candidate.predicate_iri}`}
          disabled={busy || candidate.review_status !== "confirmed" || candidate.validation_status !== "passed"}
          checked={selected.includes(candidate.candidate_id)} onChange={(e) => setSelected((ids) => e.target.checked
            ? [...ids, candidate.candidate_id] : ids.filter((id) => id !== candidate.candidate_id))} />}
        <div className="min-w-0 break-words">
          <span>{candidate.kind === "entity" ? candidate.text : labels.get(candidate.subject?.candidate_id ?? "")}</span>
          {candidate.predicate_iri && <span className="block text-xs text-muted-foreground">{candidate.predicate_iri}</span>}
          <span className="block">{candidate.literal?.raw_value ?? labels.get(candidate.object?.candidate_id ?? "")}</span>
          <span>{polarity[candidate.assertion_status]} · v{candidate.revision}</span>
        </div>
      </div>
      <p className="text-xs">验证 {candidate.validation_status} / 审核 {candidate.review_status} / 提交 {candidate.commit_status}</p>
      {candidate.validation_issues.length > 0 && <p className="text-xs text-destructive">{candidate.validation_issues.map((i) => i.code).join("；")}</p>}
      <details><summary className="cursor-pointer">值来源与独立绑定证据</summary>
        {candidate.provenance.map((source, index) => <div key={index} className="my-2">
          <p>来源：{source.kind}</p>
          {source.anchors?.map((anchor, anchorIndex) => <Button key={anchorIndex} variant="link" size="sm"
            onClick={() => onSource(anchor)}>定位原文 {anchorIndex + 1}</Button>)}
          <pre className="max-h-56 overflow-auto whitespace-pre-wrap break-all text-xs">{JSON.stringify(source, null, 2)}</pre>
        </div>)}
        {candidate.bindings.map((binding, index) => <div key={index}>
          <p>绑定方法：{binding.method}</p>
          {binding.anchors.map((anchor, anchorIndex) => <Button key={anchorIndex} variant="link" size="sm"
            onClick={() => onSource(anchor)}>定位绑定依据 {anchorIndex + 1}</Button>)}
          <pre className="overflow-auto whitespace-pre-wrap break-all text-xs">{JSON.stringify(binding, null, 2)}</pre>
        </div>)}
      </details>
      {mayEdit && <div className="flex flex-wrap gap-2">
        <Button size="sm" disabled={busy || !reason.trim() || candidate.validation_status !== "passed" || candidate.review_status !== "pending"}
          onClick={() => review(candidate, "confirmed")}>确认</Button>
        <Button size="sm" variant="outline" disabled={busy || !reason.trim() || candidate.review_status !== "pending"}
          onClick={() => review(candidate, "rejected")}>拒绝</Button>
        <Button size="sm" variant="outline" disabled={busy} onClick={() => {
          setEditId(candidate.candidate_id); setEditJson(JSON.stringify(candidate.kind === "entity" ? { text: candidate.text }
            : candidate.kind === "property" ? { literal: candidate.literal } : { object: candidate.object }, null, 2));
        }}>修改／归并</Button>
      </div>}
      {editId === candidate.candidate_id && <div className="space-y-2">
        <p className="text-xs">编辑产生新版本并重新验证；文档值的人工更正须显式提供 manual 来源。</p>
        <textarea aria-label="候选修改 JSON" className="w-full rounded border p-2 font-mono text-xs" value={editJson}
          onChange={(e) => setEditJson(e.target.value)} rows={6} />
        <Button size="sm" disabled={busy || !reason.trim()} onClick={() => act(async () => {
          await reviewEvidenceCandidate(candidate.candidate_id, { expected_revision: candidate.revision,
            decision: "confirmed", reason, edited_payload: JSON.parse(editJson) });
          setEditId(null);
        })}>保存新版本（不自动确认）</Button>
        {candidate.kind === "entity" && <>
          <select aria-label="归并目标" className="w-full rounded border p-2" value={mergeId} onChange={(e) => setMergeId(e.target.value)}>
            <option value="">选择已确认的同类型规范实体</option>
            {data.candidates.filter((c) => c.kind === "entity" && c.class_iri === candidate.class_iri && c.review_status === "confirmed"
              && c.candidate_id !== candidate.candidate_id).map((c) => <option key={c.candidate_id} value={c.candidate_id}>{c.text} v{c.revision}</option>)}
          </select>
          <Button size="sm" variant="outline" disabled={busy || !reason.trim() || !mergeId} onClick={() => act(async () => {
            const target = data.candidates.find((c) => c.candidate_id === mergeId)!;
            await resolveEvidenceCandidate(candidate.candidate_id, { expected_revision: candidate.revision,
              target: { candidate_id: target.candidate_id, revision: target.revision }, reason });
            setEditId(null);
          })}>归并并失效依赖审核</Button>
        </>}
      </div>}
    </article>)}
    {data?.commits.map((commit) => <div key={commit.commit_id} className="rounded border p-2 text-xs">
      <p className="break-all">提交 {commit.commit_id}：{commit.status}（{commit.attempts} 次）</p>
      {commit.error && <p role="alert" className="break-words text-destructive">{commit.error}</p>}
      {mayEdit && commit.status === "failed" && <Button size="sm" variant="outline" disabled={busy}
        onClick={() => act(() => retryEvidenceCommit(commit.commit_id))}>按原清单重试</Button>}
    </div>)}
    {mayEdit && <details><summary>录入人工／结构化外部候选</summary>
      <p className="my-2 text-xs">输入单个候选 JSON；外部来源须是服务端已注册的真实记录版本。每个属性值单独录入。</p>
      <textarea aria-label="新候选 JSON" className="w-full rounded border p-2 font-mono text-xs" rows={7}
        value={newJson} onChange={(e) => setNewJson(e.target.value)} />
      <Button size="sm" disabled={busy || !reason.trim() || !newJson.trim()} onClick={() => act(async () => {
        await createEvidenceCandidate(jobId, { request_key: crypto.randomUUID(), reason, candidate: JSON.parse(newJson) });
        setNewJson("");
      })}>创建待审核候选</Button>
    </details>}
  </section>;
}
