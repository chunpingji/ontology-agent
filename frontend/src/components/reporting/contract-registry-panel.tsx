"use client";

import Link from "next/link";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { getIdentity } from "@/lib/api";
import { reportGet, reportPost, type FrozenRecord, type RegisteredContract } from "@/lib/reporting-v2";

const kinds = ["parameter", "workflow", "condition", "condition_binding", "rule", "claim", "view", "conversion", "policy", "style", "static", "prompt", "migration_review", "vocabulary", "property_type"];
const domains: Record<string, { label: string; kinds: string[] }> = {
  model: { label: "模型、词表与单位", kinds: ["ontology", "property_type", "vocabulary", "conversion"] },
  rules: { label: "规则与领域参数", kinds: ["calculation", "rule", "condition", "condition_binding", "claim", "parameter"] },
  workflow: { label: "流程与审核", kinds: ["policy", "workflow"] },
  template: { label: "模板内容与版式", kinds: ["style", "static", "view"] },
  technical: { label: "运行与迁移诊断", kinds: ["context", "prompt", "migration_review"] },
};
const field = "border rounded p-2 w-full bg-background text-sm";

export function ContractRegistryPanel() {
  const [domain, setDomain] = useState("rules");
  const [kind, setKind] = useState("style");
  const [family, setFamily] = useState("");
  const [definition, setDefinition] = useState('{"font":"Arial","font_size_pt":11,"assets":[]}');
  const [selected, setSelected] = useState("");
  const [reason, setReason] = useState("");
  const [values, setValues] = useState("{}");
  const [recordKey, setRecordKey] = useState("");
  const [subject, setSubject] = useState("");
  const [at, setAt] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const user = getIdentity();
  const contracts = useQuery({ queryKey: ["report-contracts"], queryFn: () => reportGet<RegisteredContract[]>("report-contracts") });
  const active = contracts.data?.find((c) => c.contract_id === selected);
  const records = useQuery({ queryKey: ["report-records", selected], enabled: !!active && ["parameter", "workflow"].includes(active.kind),
    queryFn: () => reportGet<(FrozenRecord & { state: string; revision_no: number; record_key: string })[]>("report-contracts/" + selected + "/records") });
  async function action(fn: () => Promise<void>) {
    setBusy(true); setError("");
    try { await fn(); await contracts.refetch(); }
    catch (e) { setError(e instanceof Error ? e.message : "操作失败"); }
    finally { setBusy(false); }
  }
  return <div className="space-y-4 max-w-5xl mx-auto p-6">
    <h1 className="text-xl font-semibold">依赖管理与诊断</h1>
    <p className="text-sm text-muted-foreground">供管理员核对不可变定义及历史记录。模板的数据来源、取数字段和版式在模板中配置，本体按模型发布流程管理。</p>
    <div className="flex gap-4 text-sm"><Link className="text-primary underline" href="/settings/ast-templates">模板管理</Link><Link className="text-primary underline" href="/settings/graph-rules">图谱规则与方法</Link></div>
    <div className="flex flex-wrap gap-2" aria-label="管理职责">{Object.entries(domains).map(([key, value]) => <Button key={key} variant={domain === key ? "default" : "outline"} onClick={() => { setDomain(key); setSelected(""); }}>{value.label}</Button>)}</div>
    {user.role === "senior_analyst" && <details className="border rounded p-4"><summary>高级：登记外部定义修订</summary>
      <div className="grid grid-cols-2 gap-3 mt-4">
        <label>契约类型<select className={field} value={kind} onChange={(e) => setKind(e.target.value)}>{kinds.map((k) => <option key={k}>{k}</option>)}</select></label>
        <label>契约家族标识<input className={field} value={family} onChange={(e) => setFamily(e.target.value)} /></label>
      </div>
      <label className="block mt-3">契约定义<textarea className={field + " min-h-64 font-mono"} value={definition} onChange={(e) => setDefinition(e.target.value)} /></label>
      <Button disabled={busy || !family.trim()} onClick={() => action(async () => {
        const revision = 1 + Math.max(0, ...(contracts.data ?? []).filter((c) => c.family_id === family).map((c) => c.revision_no));
        const row = await reportPost<FrozenRecord>("report-contracts", { kind, family_id: family, revision_no: revision, definition: JSON.parse(definition) });
        setSelected(row.id);
      })}>保存草稿</Button>
      <Button className="ml-3" variant="outline" disabled={busy} onClick={() => action(async () => {
        const row = await reportPost<FrozenRecord>("report-contracts/ontology-snapshots", {}); setSelected(row.id);
      })}>登记当前本体结构快照</Button>
    </details>}
    <label className="block">已有契约<select className={field} value={selected} onChange={(e) => setSelected(e.target.value)}>
      <option value="">选择契约</option>{contracts.data?.filter((c) => domains[domain].kinds.includes(c.kind)).map((c) => <option key={c.contract_id} value={c.contract_id}>{c.kind} · {c.family_id} · v{c.revision_no} · {c.status}</option>)}
    </select></label>
    {active && <>
      <p className="text-xs break-all">引用：{active.contract_id}</p>
      <pre className="border rounded p-3 overflow-auto max-h-80 text-xs">{JSON.stringify(active, null, 2)}</pre>
      {user.role === "senior_analyst" && !active.origin && <Button variant="outline" onClick={() => { setKind(active.kind); setFamily(active.family_id);
        const data = { ...active.definition }; for (const key of ["review_ref", "association_review_ref", "claims_reviewed", "nodes_hash"]) delete data[key];
        setDefinition(JSON.stringify(data, null, 2)); }}>以此修订为草稿起点</Button>}
      {active.origin && <p className="text-sm text-muted-foreground">{active.origin === "bundled_executor" ? "内置规则由规则服务维护方法版本，输入依据和取值决定保存在图谱及报告中。" : "系统方案随代码版本固定；实际审核记录在报告流程中完成。"}</p>}
      {!active.origin && <label className="block">审核或变更理由<input className={field} value={reason} onChange={(e) => setReason(e.target.value)} /></label>}
      {user.role === "senior_analyst" && !active.origin && <div className="flex gap-2">{[
        ["reviewed", "确认语义审核"], ["published", "发布该修订"], ["rejected", "退回"], ["disabled", "停用"],
      ].map(([decision, label]) => <Button key={decision} variant="outline" disabled={busy || !reason.trim()} onClick={() => action(async () => {
        await reportPost("report-contracts/" + selected + "/decisions", { decision, reason, expected_hash: active.stored_definition_hash });
      })}>{label}</Button>)}</div>}
      {["workflow", "parameter"].includes(active.kind) && <details className="border rounded p-4" open><summary>业务记录</summary>
        <div className="grid grid-cols-3 gap-3 mt-3">
          <label>记录标识<input className={field} value={recordKey} onChange={(e) => setRecordKey(e.target.value)} /></label>
          <label>适用主体<input className={field} value={subject} onChange={(e) => setSubject(e.target.value)} /></label>
          <label>适用时间<input className={field} value={at} onChange={(e) => setAt(e.target.value)} /></label>
        </div>
        <label className="block mt-3">记录值<textarea className={field + " min-h-32 font-mono"} value={values} onChange={(e) => setValues(e.target.value)} /></label>
        <Button disabled={busy || !recordKey.trim() || active.status !== "published"} onClick={() => action(async () => {
          const revision = 1 + Math.max(0, ...(records.data ?? []).filter((r) => r.record_key === recordKey).map((r) => r.revision_no));
          await reportPost("report-contracts/" + selected + "/records", { record_key: recordKey, revision_no: revision, values: JSON.parse(values), subject_id: subject || null, applicable_at: at || null });
          await records.refetch();
        })}>登记记录修订</Button>
        {records.data?.map((r) => <div key={r.id} className="border-t mt-4 pt-3 text-sm">
          <p className="break-all">{r.id} · v{r.revision_no} · {r.state}</p>
          <pre className="overflow-auto text-xs">{JSON.stringify(r.payload, null, 2)}</pre>
          {user.role === "qa" && (["approved", "rejected"] as const).map((decision) => <Button key={decision} variant="outline" size="sm" disabled={busy || !reason.trim()}
            onClick={() => action(async () => { await reportPost("report-records/" + r.id + "/reviews", { decision, reason, expected_hash: r.content_hash }); await records.refetch(); })}>{decision === "approved" ? "核对通过" : "退回记录"}</Button>)}
        </div>)}
      </details>}
    </>}
    {error && <p role="alert" className="text-destructive break-all">{error}</p>}
  </div>;
}
