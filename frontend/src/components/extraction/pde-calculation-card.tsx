"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import type { EvidenceAnchor, PDECalculation } from "@/lib/api";

export type CalculationChoice = "derived" | "asserted" | "rejected" | "pending";
export type CalculationAction = (result: PDECalculation, choice: CalculationChoice, reason: string) => Promise<void>;

const choices: Record<string, string> = {
  automatic: "校验相符，自动通过", pending: "待处理", derived: "已采纳计算值",
  asserted: "已保留原文值", rejected: "已提出异议，待解决",
};
const sources: Record<string, string> = {
  document: "原文", method_default: "方法默认值", species_table: "按种属计算", duration_table: "按周期计算",
};
const differences: Record<string, string> = {
  pde_ratio: "PDE 数值比值超过阈值", oeb_band: "原文与计算 PDE 换算的 OEB 分档不同",
  oeb_assertion: "原文明确给出的 OEB 与计算分档不同",
};

export function PDECalculationCard({ result, busy, onSource, onDecide }: {
  result: PDECalculation; busy: boolean; onSource: (anchor: EvidenceAnchor) => void; onDecide?: CalculationAction;
}) {
  const [choice, setChoice] = useState<CalculationChoice | null>(null);
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");
  async function submit() {
    if (!onDecide || !choice || !reason.trim()) return;
    setError("");
    try { await onDecide(result, choice, reason.trim()); setChoice(null); setReason(""); }
    catch (e) { setError(String(e)); }
  }
  return <section aria-label="PDE 计算校验" className="mx-2 my-2 space-y-2 rounded border p-3 text-xs"
    data-calculation-id={result.calculation_id}>
    <p className="font-semibold">PDE 计算校验 · {choices[result.review_status] ?? result.review_status}</p>
    <dl className="grid grid-cols-2 gap-1">
      <dt>原文 PDE</dt><dd>{result.asserted_pde_mg_day ?? "缺失"} mg/day</dd>
      <dt>计算 PDE</dt><dd>{result.derived_pde_mg_day ?? "未完成"} mg/day</dd>
      <dt>原文侧／计算侧 OEB</dt><dd>{result.asserted_band ?? "—"} ／ {result.derived_band ?? "—"}</dd>
      {result.inputs.asserted_oeb && <><dt>原文明确 OEB</dt><dd>{result.inputs.asserted_oeb}</dd></>}
      <dt>数值相差</dt><dd>{result.ratio ?? "—"} 倍（大于 {result.ratio_threshold} 倍提示差异）</dd>
      <dt>报告采用 PDE</dt><dd>{result.effective_pde_mg_day ? `${result.effective_pde_mg_day} mg/day` : "待处理"}</dd>
    </dl>
    {result.differences?.map((difference) => <p key={difference} className="text-amber-700">差异：{differences[difference] ?? difference}</p>)}
    {result.blocks_conclusion && <p className="text-amber-700" role="status">校验或异议处理未完成，报告结论保持待评估。</p>}
    {!result.linked_to_source && <p className="text-amber-700">该实体尚未通过关系绑定到源文档，暂不能用于报告。</p>}
    {result.stale_decision && <p className="text-amber-700">输入已变化，原取值决定已失效，请重新处理。</p>}
    {result.issues.map((item, i) => <p key={i} className="text-amber-700">{item.parameter}：{item.message}</p>)}
    <details>
      <summary className="cursor-pointer">计算依据、输入来源及方法版本</summary>
      <p className="mt-2 break-words">{result.formula}</p>
      <p>NOAEL：{result.inputs.noael ?? "缺失"} mg/kg/day</p>
      <ul className="my-2">{Object.entries(result.factors).map(([name, factor]) =>
        <li key={name}>{name.toUpperCase()} = {factor.value}（{sources[factor.source] ?? factor.source}）</li>)}</ul>
      {Object.entries(result.input_evidence).map(([name, items]) => items.map((item) => <div key={item.candidate_id} className="my-1">
        <span>{name}：{item.literal.raw_value} </span>
        {item.provenance.flatMap((p) => p.anchors ?? []).map((anchor, i) => <button key={i}
          className="mr-2 text-primary underline" onClick={() => onSource(anchor)}>定位原文 {i + 1}</button>)}
      </div>))}
      <p className="text-muted-foreground">{result.limitations}</p>
      <p className="mt-2 break-all">方法 {result.method_version} · {result.method_hash}</p>
      <p className="break-all">计算记录：{result.calculation_id}</p>
    </details>
    {result.decision && <p className="break-words text-muted-foreground">{result.decision.actor} · {result.decision.decided_at} · 第 {result.decision.revision} 次处理：{result.decision.reason}</p>}
    {onDecide && <div className="flex flex-wrap gap-2">
      {result.status !== "incomplete" && result.linked_to_source && <>
        <Button size="sm" variant="outline" disabled={busy} onClick={() => { setChoice("derived"); setError(""); }}>采纳计算值</Button>
        <Button size="sm" variant="outline" disabled={busy} onClick={() => { setChoice("asserted"); setError(""); }}>保留原文值</Button>
      </>}
      <Button size="sm" variant="outline" disabled={busy} onClick={() => { setChoice("rejected"); setError(""); }}>对校验结果提出异议</Button>
      {result.decision && <Button size="sm" variant="outline" disabled={busy} onClick={() => { setChoice("pending"); setError(""); }}>撤回取值决定</Button>}
    </div>}
    {choice && <div className="space-y-2 rounded bg-muted/40 p-2">
      <label className="block">{choices[choice]}：处理理由（必填）
        <textarea aria-label="PDE 处理理由" value={reason} onChange={(e) => setReason(e.target.value)}
          className="mt-1 w-full rounded border p-2" rows={3} maxLength={4000} disabled={busy} />
      </label>
      {error && <p role="alert" className="text-destructive">{error}</p>}
      <Button size="sm" disabled={busy || !reason.trim()} onClick={submit}>保存处理结果</Button>
      <Button className="ml-2" size="sm" variant="ghost" disabled={busy} onClick={() => setChoice(null)}>取消</Button>
    </div>}
  </section>;
}
