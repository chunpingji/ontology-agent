"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { reportGet, type RegisteredContract } from "@/lib/reporting-v2";

export default function GraphRulesPage() {
  const rules = useQuery({ queryKey: ["graph-rules"], queryFn: () => reportGet<RegisteredContract[]>("graph-rules") });
  return <main className="mx-auto max-w-5xl p-6 space-y-5">
    <h1 className="text-xl font-semibold">图谱规则与计算方法</h1>
    <p className="text-sm text-muted-foreground">规则自动应用于适用的源文档图谱。方法版本、输入证据和取值决定随结果保留；模板可引用规则结果。</p>
    {rules.isPending && <p>正在加载规则…</p>}
    {rules.error && <p role="alert">{rules.error.message}</p>}
    {rules.data?.map((rule) => <article key={rule.contract_id} className="rounded border p-5 space-y-3">
      <h2 className="font-semibold">{String(rule.definition.title)} · 方法 {String(rule.definition.version)}</h2>
      <p className="text-sm">{String(rule.definition.formula)}</p>
      <p className="text-sm">适用来源：{String(rule.definition.root_class_iri).split("/").pop()}</p>
      <p className="text-sm">计算主体：{String(rule.definition.subject_class_iri).split("/").pop()}</p>
      <p className="text-sm text-muted-foreground">{String(rule.definition.limitations)}</p>
      <p className="text-sm">必需的参数缺失、识别未完成或差异未处理时，报告保留未完成状态。</p>
      <details><summary>参数、默认值与版本依据</summary><pre className="mt-3 overflow-auto text-xs">{JSON.stringify(rule.definition, null, 2)}</pre><p className="text-xs break-all">{rule.contract_id}</p></details>
    </article>)}
    <Link className="text-sm text-primary underline" href="/settings/report-contracts">管理员：规则与领域参数管理</Link>
  </main>;
}
