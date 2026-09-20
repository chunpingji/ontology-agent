"use client";

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { ArrowDown, Brain, FileText, Network, Radio, Terminal, Wrench } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { cn } from "@/lib/utils";
import { getDocumentHarness, getDocumentHarnessContext, shouldSubscribeDocumentAnalysisEvents,
  type DocumentAnalysisStatus, type DocumentHarness, type HarnessContext,
  type HarnessPredicateSchemaCard, type HarnessSchemaCard, type HarnessSnapshot } from "@/lib/api";

const PHASES = { discovery: "声明发现", verification: "独立核验" };
const RECORD_PHASES = { discovery: "识别记录中的实体和属性", verification: "核验实体和属性" };
const ATTRIBUTE_PHASES = { discovery: "属性消歧", verification: "属性归属核验" };
const phaseLabel = (stage: keyof typeof PHASES, taskKind?: string) =>
  (taskKind === "property_disambiguation" ? ATTRIBUTE_PHASES
    : taskKind === "record_discovery" ? RECORD_PHASES : PHASES)[stage];
const ATTRIBUTE_STATUS = { pending: "待消歧", resolved: "已确认归属", unresolved: "未决" };
const ATTRIBUTE_WORK_STATUS = {
  pending: "待处理", active: "执行中", examined: "本轮已处理", incomplete: "本轮未完成",
};
const ATTRIBUTE_REASONS: Record<string, string> = {
  attribute_value_missing: "原文字段缺少值",
  ontology_property_missing: "本体中没有匹配的合法属性",
  attribute_subject_missing: "尚未找到相关已登记主体",
  attribute_candidate_capacity: "候选主体与属性对超出本轮容量",
  attribute_ambiguous: "原文仍支持多个归属解释",
  attribute_not_supported: "属性归属未通过原文核验",
  attribute_subject_unresolved: "原文尚不足以确定字段主体",
  attribute_deferred_to_disambiguation: "该字段已交由独立消歧任务处理",
  attribute_answer_outside_scope: "模型回答超出授权字段或候选范围",
};
const TOOL_NAMES: Record<string, string> = {
  get_schema_card: "读取本体约束", inspect_evidence: "读取原文证据", propose_mentions: "实体提及识别",
  resolve_source_anchor: "定位原文引用", query_instances: "查询外部实体", retrieve_evidence: "检索证据",
  check_claim_binding: "校验主体与关系归属", validate_metric: "校验数值与单位",
  validate_graph: "本体与图谱约束校验", propose_repair: "提出修正",
};
const QUANTITY: Record<string, string> = { scalar: "单值", interval: "区间", lower_bound: "下界", upper_bound: "上界", physical: "物理量", dimensionless: "无量纲", count: "计数", not_declared: "未声明" };
const STATUS: Record<string, string> = { running: "执行中", completed: "完成", success: "成功", ok: "成功",
  blocked: "未通过", failed: "失败", error: "失败", interrupted: "已中断", incomplete: "返回不完整", no_match: "无匹配" };
const shortName = (iri: string) => iri.split(/[/#]/).filter(Boolean).at(-1) || iri;
const json = (value: unknown) => typeof value === "string" ? value : JSON.stringify(value, null, 2);
const newer = (old: HarnessSnapshot, incoming: HarnessSnapshot) => old.session_id === incoming.session_id
  ? old.sequence >= incoming.sequence : (old.updated_at || "") > (incoming.updated_at || "");

export function useDocumentHarness(runId: string | null, status: DocumentAnalysisStatus | null) {
  const [data, setData] = useState<DocumentHarness | null>(null);
  const [error, setError] = useState<string | null>(null);
  const applySnapshot = useCallback((snapshot: HarnessSnapshot) => {
    setData((old) => !old || old.recognition_run_id !== runId || (old.snapshot && newer(old.snapshot, snapshot))
      ? old : { ...old, snapshot });
  }, [runId]);
  useEffect(() => {
    if (!runId) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function refresh() {
      try {
        const result = await getDocumentHarness(runId!, controller.signal);
        if (controller.signal.aborted || result.recognition_run_id !== runId) return;
        setData((old) => {
          const previous = old?.recognition_run_id === runId ? old.snapshot : null;
          return previous && (!result.snapshot || newer(previous, result.snapshot)) ? { ...result, snapshot: previous } : result;
        });
        setError(null);
      } catch { if (!controller.signal.aborted) setError("Harness运行信息暂时不可读取"); }
      finally { if (!controller.signal.aborted && shouldSubscribeDocumentAnalysisEvents(status)) timer = setTimeout(refresh, 3000); }
    }
    void refresh();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [runId, status]);
  return { data: data?.recognition_run_id === runId ? data : null, error, applySnapshot };
}

function JsonBlock({ value }: { value: unknown }) {
  return <pre className="mt-2 max-h-72 overflow-auto whitespace-pre-wrap break-all rounded-md bg-muted/40 p-3 font-mono text-xs leading-6">{json(value) ?? "未提供"}</pre>;
}
function Fold({ title, children }: { title: string; children: ReactNode }) {
  return <details className="border-t py-3"><summary className="cursor-pointer text-xs font-medium">{title}</summary>{children}</details>;
}

export function HarnessConfiguration({ data }: { data: DocumentHarness | null }) {
  const config = data?.configuration;
  return <div className="grid min-w-0 gap-3 md:grid-cols-2">
    <section className="space-y-2 rounded-md bg-muted/40 p-3" aria-label="模型与工具配置">
      <p className="font-medium text-foreground">模型与工具</p><p className="break-words">{config?.model || "尚无冻结模型信息"} {config?.api_protocol && `· ${config.api_protocol}`}</p>
      <div className="flex flex-wrap gap-2">{config && [["GLiNER", config.gliner_enabled], ["Mock", config.mock_enabled], ["实验词表", config.vocabulary_enabled]].map(([name, enabled]) => <Badge variant="outline" key={String(name)}>{name} · {enabled ? "已启用" : "未启用"}</Badge>)}</div>
      <p>本次观察期间实际工具调用：{Object.entries(data?.snapshot?.tool_counts || {}).map(([name, count]) => `${TOOL_NAMES[name] || name} ${count} 次`).join("、") || "暂无"}</p>
    </section>
    <section className="space-y-2 rounded-md bg-muted/40 p-3" aria-label="请求预算与用量">
      <p className="font-medium text-foreground">请求预算与用量</p>
      <p>输入上限 {config?.request_budget?.max_input_tokens?.toLocaleString() ?? "未记录"} · 输出上限 {config?.request_budget?.max_output_tokens?.toLocaleString() ?? "未记录"} tokens</p>
      <p>上下文上限 {config?.request_budget?.max_context_tokens?.toLocaleString() ?? "未记录"} tokens</p>
      <p>当前请求输入估算 {data?.snapshot?.call?.input_tokens?.toLocaleString() ?? "未记录"} tokens</p>
      <details><summary className="cursor-pointer">服务端返回用量</summary><JsonBlock value={data?.snapshot?.call?.usage ?? "当前请求尚无用量返回"} /></details>
    </section>
  </div>;
}

export function HarnessAttributeDisambiguations({ data, status }: {
  data: DocumentHarness | null; status: DocumentAnalysisStatus;
}) {
  const attributes = data?.attribute_disambiguations || [];
  const pending = attributes.filter((item) => item.attribute_status !== "resolved").length;
  const activeStatus = status === "running" ? "执行中" : status === "paused" ? "已暂停"
    : status === "queued" ? "等待执行" : "执行已停止";
  return <Fold title={`待消歧属性与处理结果 · ${pending} 项待处理，共 ${attributes.length} 项`}>
    {attributes.length ? <div className="mt-3 overflow-x-auto"><table aria-label="属性消歧结果" className="w-full min-w-[36rem] text-left text-xs">
      <thead><tr className="border-b text-muted-foreground">{["原文字段", "原文值", "候选主体/属性对", "处理结果", "原因"].map((title) => <th key={title} className="px-3 py-2 font-medium">{title}</th>)}</tr></thead>
      <tbody>{attributes.map((item) => <tr key={item.field_id} className="border-b align-top last:border-0">
        <td className="max-w-48 whitespace-pre-wrap break-words px-3 py-3">{item.label ?? "尚未准备字段"}</td>
        <td className="max-w-64 whitespace-pre-wrap break-words px-3 py-3">{item.value == null || item.value === "" ? "未提供值" : item.value}</td>
        <td className="px-3 py-3">{item.candidate_count == null ? "尚未准备" : `${item.candidate_count} 对`}</td>
        <td className="px-3 py-3"><p>{ATTRIBUTE_STATUS[item.attribute_status]}</p><p className="mt-1 text-muted-foreground">{item.work_status === "active" ? activeStatus : ATTRIBUTE_WORK_STATUS[item.work_status]} · 已尝试 {item.disambiguation_attempts} 轮</p></td>
        <td className="max-w-64 break-words px-3 py-3">{item.reason_code ? ATTRIBUTE_REASONS[item.reason_code] || item.reason_code : item.work_status === "pending" ? "等待处理" : "—"}</td>
      </tr>)}</tbody>
    </table></div> : <p className="mt-2 text-xs text-muted-foreground">当前没有待消歧属性记录。</p>}
  </Fold>;
}

function SchemaCard({ card, classLabels = {} }: { card: HarnessSchemaCard; classLabels?: Record<string, string> }) {
  const [raw, setRaw] = useState(false);
  return <><div className="flex flex-wrap items-center justify-between gap-2"><h4 className="font-semibold">本体 Schema 卡片 <Badge variant="secondary">已窄化</Badge></h4><Button size="sm" variant="ghost" onClick={() => setRaw(!raw)}>{raw ? "结构化" : "原始 JSON"}</Button></div>
    {raw ? <JsonBlock value={card} /> : "class_cards" in card ? <div className="space-y-6 pt-3">
      {card.class_cards.map((entry) => <section key={entry.class_iri} aria-label={`${entry.label}的属性约束`} className="space-y-3 border-b pb-4 last:border-0">
        <h5 className="text-sm font-semibold" title={entry.class_iri}>{entry.label || shortName(entry.class_iri)}</h5>
        <SchemaDetails card={{ ...entry, class_iris: [entry.class_iri], predicates: entry.properties }} classLabels={{ ...classLabels, [entry.class_iri]: entry.label }} />
      </section>)}
    </div> : <SchemaDetails card={card} classLabels={classLabels} />}</>;
}

function SchemaDetails({ card, classLabels }: {
  card: Omit<HarnessPredicateSchemaCard, "schema_card_id">; classLabels: Record<string, string>;
}) {
  const labels = new Map(card.predicates.flatMap((p) => (p.range_classes || []).map((c) => [c.iri, c.label])));
  return <div className="space-y-4 pt-3 text-xs">
      <section><p className="mb-2 text-muted-foreground">允许类型</p><div className="flex flex-wrap gap-2">{card.class_iris.map((iri) => <Badge title={iri} key={iri} variant="outline">{classLabels[iri] || labels.get(iri) || shortName(iri)}</Badge>)}</div></section>
      {card.predicates.map((p) => <section key={p.iri} className="space-y-3 rounded-lg border p-4">
        <div className="flex flex-wrap items-center gap-2"><strong className="text-sm">{p.label || "未命名谓词"}</strong><Badge variant="secondary">{p.kind === "relationship" ? "关系" : "属性"}</Badge></div>
        {p.description && <p>{p.description}</p>}
        <dl className="grid grid-cols-[6rem_minmax(0,1fr)] gap-2">
          <dt className="text-muted-foreground">基数</dt><dd>最少 {p.min_count ?? "未声明"} · 最多 {p.max_count ?? "未声明"}</dd>
          <dt className="text-muted-foreground">{p.kind === "relationship" ? "对象类型" : "数据类型"}</dt><dd className="break-words">{(p.range_class_iris || p.datatype_iris || []).map((iri) => classLabels[iri] || labels.get(iri) || shortName(iri)).join("、") || "未声明"}</dd>
          <dt className="text-muted-foreground">标准单位</dt><dd>{p.canonical_unit ?? "未声明"}</dd>
          <dt className="text-muted-foreground">约束状态</dt><dd>{p.constraint_status === "resolved" ? "已解析" : p.constraint_status === "constraint_unresolved" ? "存在未解析约束" : p.constraint_status || "未声明"}</dd>
        </dl>
      </section>)}
      <Fold title="数量与单位策略">{card.quantity_policies?.length ? card.quantity_policies.map((policy) => <div key={policy.predicate_iri} className="mt-2 space-y-2 rounded-md bg-muted/40 p-3">
        <p>{card.predicates.find((item) => item.iri === policy.predicate_iri)?.label || shortName(policy.predicate_iri)}</p>
        <p>允许形式：{policy.allowed_forms.map((value) => QUANTITY[value] || value).join("、")}</p>
        <p>单位要求：{QUANTITY[policy.unit_requirement] || policy.unit_requirement} · 目标单位：{policy.allowed_target_units.join("、") || "未声明"}</p>
        <p>端点角色：{policy.endpoint_role === "lower" ? "下界" : policy.endpoint_role === "upper" ? "上界" : "未声明"}</p>
      </div>) : <p className="mt-2 text-muted-foreground">未声明</p>}</Fold>
      <Fold title="身份键">{card.identity_keys?.length ? card.identity_keys.map((key, index) => <div key={index} className="mt-2 space-y-2 rounded-md bg-muted/40 p-3">
        <p>{classLabels[key.class_iri] || shortName(key.class_iri)} · {key.property_iris.map((iri) => card.predicates.find((p) => p.iri === iri)?.label || shortName(iri)).join(" + ")}</p>
        <p>作用域：{key.scope === "document" ? "文档内" : key.scope === "dataset" ? "数据集" : key.scope === "global" ? "全局" : key.scope} · 命名空间：{key.namespace || "未声明"}</p>
      </div>) : <p className="mt-2 text-muted-foreground">未声明</p>}</Fold>
      <Fold title={`未解析约束 · ${card.unsupported_constraints?.length || 0}`}>{card.unsupported_constraints?.length ? card.unsupported_constraints.map((issue, index) => <div key={index} className="mt-2 rounded-md bg-muted/40 p-3">
        <p>{card.predicates.find((p) => p.iri === issue.predicate_iri)?.label || shortName(issue.predicate_iri)} · {issue.construct}</p><p className="mt-1 text-muted-foreground">{issue.reason_code}</p>
      </div>) : <p className="mt-2 text-muted-foreground">无</p>}</Fold>
    </div>;
}
const SOURCE_INPUT_FIELDS = new Set(["source_catalog", "evidence_units", "evidence_refs"]);
const isObject = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === "object" && !Array.isArray(value);

function splitPromptInput(input: Record<string, unknown>): {
  task: Record<string, unknown>; sources: Record<string, unknown>;
} {
  const task: Record<string, unknown> = {};
  const sources: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(input)) {
    if (SOURCE_INPUT_FIELDS.has(key)) sources[key] = value;
    else if (key === "members" && Array.isArray(value)) {
      const members = value.map((member) => isObject(member) ? splitPromptInput(member) : null);
      task.members = value.map((member, index) => members[index]?.task ?? member);
      sources.members = members.flatMap((member, index) => member && Object.keys(member.sources).length
        ? [{ task_id: isObject(value[index]) ? value[index].task_id : undefined, ...member.sources }]
        : []);
    } else if (key === "shared_context" && isObject(value)) {
      const { source_sections, ...other } = value;
      if (source_sections !== undefined) sources.shared_context = { source_sections };
      if (Object.keys(other).length) task.shared_context = other;
    } else if (key !== "schema_card") task[key] = value;
  }
  return { task, sources };
}

function Prompt({ context }: { context: HarnessContext }) {
  const [raw, setRaw] = useState(false);
  let task: Record<string, unknown> = {};
  const marker = "\n阶段回答JSON Schema（工具轮也须遵守）：\n";
  const [instructionText, embeddedSchema] = context.request.instructions.split(marker);
  let answerSchema: unknown = context.request.text?.format;
  if (!answerSchema && embeddedSchema) {
    try { answerSchema = JSON.parse(embeddedSchema); } catch { answerSchema = embeddedSchema; }
  }
  try { const content = context.request.input[0]?.content;
    if (Array.isArray(content) && typeof content[0]?.text === "string") {
      const parsed: unknown = JSON.parse(content[0].text);
      if (isObject(parsed)) task = parsed;
    }
  } catch { /* Original input remains available, never synthesize missing fields. */ }
  const sections = splitPromptInput(task);
  const taskKind = task.purpose === "property_disambiguation"
    || (isObject(task.task) && task.task.purpose === "property_disambiguation")
    || isObject(task.attribute_disambiguation) ? "property_disambiguation"
    : "kind" in context.schema_card ? context.schema_card.kind : undefined;
  const title = task.stage === "verification"
    ? taskKind === "property_disambiguation" ? "当前属性归属核验目标"
      : taskKind === "record_discovery" ? "当前实体与属性核验目标" : "当前声明核验目标"
    : task.stage === "discovery"
      ? taskKind === "property_disambiguation" ? "当前属性消歧任务"
        : taskKind === "record_discovery" ? "当前实体与属性发现任务" : "当前声明发现任务"
      : "当前任务";
  return <><div className="flex items-center justify-between"><h4 className="font-semibold">提示词</h4><Button size="sm" variant="ghost" onClick={() => setRaw(!raw)}>{raw ? "结构化" : "原始输入"}</Button></div>
    {raw ? <><p className="text-xs text-muted-foreground">实际提交字段；不透明推理字段已隐藏。</p><JsonBlock value={context.request} /></> : <div className="space-y-4 pt-3">
      <section className="rounded-md bg-muted/40 p-4"><p className="mb-2 text-xs font-medium">指令 · instructions</p><p className="whitespace-pre-wrap break-words text-xs leading-6">{instructionText}</p></section>
      <p className="text-xs font-medium">当前任务输入 · {phaseLabel(task.stage as keyof typeof PHASES, taskKind) || "见原始输入"}</p>
      <Fold title={title}><JsonBlock value={sections.task} /><p className="mt-2 text-xs text-muted-foreground">本体约束见“本体 Schema 卡片”页。</p></Fold>
      <Fold title="授权原文与摘要 · 查看输入片段"><JsonBlock value={sections.sources} /></Fold>
      <Fold title="阶段返回 JSON Schema · 查看回答结构"><JsonBlock value={answerSchema || "本次请求未单列回答 JSON Schema；查看原始输入。"} /></Fold>
      <Fold title="本轮消息与工具返回 · 按提交顺序"><JsonBlock value={context.request.input} /></Fold>
    </div>}</>;
}
function ContextPanel({ runId, callId, open }: { runId: string; callId: string | undefined; open: boolean }) {
  const [context, setContext] = useState<HarnessContext | null>(null);
  const [error, setError] = useState<{ callId: string; message: string } | null>(null);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    if (!open || !callId) return;
    const controller = new AbortController();
    getDocumentHarnessContext(runId, callId, controller.signal).then((value) => {
      if (!controller.signal.aborted && value.call_id === callId && value.recognition_run_id === runId) { setContext(value); setError(null); }
    }).catch(() => { if (!controller.signal.aborted) setError({ callId: callId!, message: "该调用上下文已变化或暂不可读取，请等待最新调用或重试。" }); });
    return () => controller.abort();
  }, [runId, callId, open, retry]);
  const current = context && context.call_id === callId && context.recognition_run_id === runId ? context : null;
  return <div className="mt-3 min-w-0">{error?.callId === callId && error ? <div className="text-xs text-muted-foreground">{error.message}<Button variant="ghost" size="sm" onClick={() => setRetry(retry + 1)}>重试</Button></div>
    : !current ? <p className="py-4 text-xs text-muted-foreground">{callId ? "正在读取本次实际提交的上下文…" : "尚无模型调用上下文"}</p>
    : <Tabs defaultValue="prompt">
      <TabsList className="grid w-full grid-cols-2" aria-label="上下文视图"><TabsTrigger value="prompt" className="gap-2"><FileText className="size-4" />提示词</TabsTrigger><TabsTrigger value="schema" className="gap-2"><Network className="size-4" />本体 Schema 卡片</TabsTrigger></TabsList>
      <TabsContent value="prompt" forceMount className="h-[28rem] overflow-auto rounded-lg border p-4 data-[state=inactive]:hidden"><Prompt context={current} /></TabsContent>
      <TabsContent value="schema" forceMount className="h-[28rem] overflow-auto rounded-lg border p-4 data-[state=inactive]:hidden">{"members" in current.schema_card ? <div className="space-y-6">{current.schema_card.members.map((member) => <section key={member.task_id} className="border-b pb-4 last:border-0"><SchemaCard card={member.card} classLabels={current.class_labels} /></section>)}</div> : <SchemaCard card={current.schema_card} classLabels={current.class_labels} />}</TabsContent>
    </Tabs>}
    <p className="mt-3 border-t pt-3 text-xs text-muted-foreground">两页关联同一次模型调用；切换只改变展示，保留各自滚动位置。</p>
  </div>;
}

export function DocumentHarnessStream({ status, data, connected, error }: {
  status: DocumentAnalysisStatus; data: DocumentHarness | null; connected: boolean; error: string | null;
}) {
  const snapshot = data?.snapshot; const call = snapshot?.call;
  const live = shouldSubscribeDocumentAnalysisEvents(status);
  const writing = live && connected && call?.status === "running";
  const outputRef = useRef<HTMLDivElement>(null); const following = useRef(true);
  const [follow, setFollow] = useState(true);
  useEffect(() => { if (following.current && outputRef.current) outputRef.current.scrollTop = outputRef.current.scrollHeight; }, [snapshot?.output, call?.call_id]);
  return <section aria-label="Harness实时输出" className="mb-3 min-w-0 overflow-hidden rounded-lg border bg-background">
    <div className="flex flex-wrap items-center justify-between gap-2 border-b px-4 py-3 text-xs">
      <div className="flex flex-wrap items-center gap-2"><Radio className={cn("size-4", writing ? "text-primary" : "text-muted-foreground")} /><strong>LLM 实时输出</strong><span className="text-muted-foreground">{call ? `${call.model} · ${phaseLabel(call.stage, call.task_kind)}` : "等待模型调用"}</span></div>
      <Badge title={snapshot?.updated_at ? `最近返回 ${new Date(snapshot.updated_at).toLocaleTimeString()}` : undefined} variant="outline">{live ? connected ? "实时已连接" : "实时未连接 · 定时读取" : "实时已停止"}</Badge>
    </div>
    <div className="relative px-4 py-2">
      <div ref={outputRef} onScroll={(event) => { const element = event.currentTarget;
        const atEnd = element.scrollHeight - element.scrollTop - element.clientHeight < 24;
        following.current = atEnd; setFollow(atEnd);
      }} tabIndex={0} aria-label="LLM 输出文本" className="h-14 overflow-auto whitespace-pre-wrap break-all font-mono text-xs leading-6">
        {snapshot?.output || <span className="font-sans text-muted-foreground">{error || (call?.status === "running" && live ? "模型请求进行中，等待正文返回…" : "该运行尚无可展示的模型正文")}</span>}
        {writing && <span aria-label="正在接收模型输出" className="ml-1 inline-block h-3 w-1.5 bg-primary motion-safe:animate-pulse" />}
      </div>
      {!follow && <Button size="sm" variant="secondary" className="absolute bottom-2 right-5" onClick={() => { following.current = true; setFollow(true); if (outputRef.current) outputRef.current.scrollTop = outputRef.current.scrollHeight; }}><ArrowDown />回到最新</Button>}
      {snapshot?.truncated.includes("output") && <p className="text-xs text-muted-foreground">仅显示当前输出末尾 128 Ki 字符。</p>}
    </div>

  </section>;
}


export function DocumentHarnessInformation({ runId, status, data, error, children }: {
  runId: string; status: DocumentAnalysisStatus; data: DocumentHarness | null;
  error: string | null; children: ReactNode;
}) {
  const [expanded, setExpanded] = useState(false);
  const [contextOpen, setContextOpen] = useState(false);
  const snapshot = data?.snapshot;
  const call = snapshot?.call;
  const live = shouldSubscribeDocumentAnalysisEvents(status);
  return <details aria-label="Harness运行信息" className="rounded-lg border text-xs text-muted-foreground"
    onToggle={(event) => {
      // Nested details have independent state; only the outer disclosure gates context reads.
      if (event.target === event.currentTarget) setExpanded(event.currentTarget.open);
    }}>
    <summary className="cursor-pointer px-4 py-3 font-medium text-foreground">Harness运行信息</summary>
    <div className="space-y-3 border-t p-4">
      {error && <p role="status" className="text-destructive">{error}</p>}
      <HarnessConfiguration data={data} />
      <HarnessAttributeDisambiguations data={data} status={status} />
      <div className="flex flex-wrap items-start gap-x-6 border-t text-foreground">
        <details className="min-w-0 py-2 open:w-full"><summary className="cursor-pointer text-xs font-medium"><Brain className="mr-2 inline size-4 text-muted-foreground" />Thinking <span className="ml-2 font-normal text-muted-foreground">{snapshot?.thinking ? "模型返回内容" : "暂无内容"}</span></summary><JsonBlock value={snapshot?.thinking || "模型尚未返回可读 Thinking 内容。"} />{snapshot?.truncated.includes("thinking") && <p className="text-xs text-muted-foreground">仅显示末尾 128 Ki 字符。</p>}</details>
        <details className="min-w-0 py-2 open:w-full"><summary className="cursor-pointer text-xs font-medium"><Wrench className="mr-2 inline size-4 text-muted-foreground" />操作 <span className="ml-2 font-normal text-muted-foreground">最近 {snapshot?.operations.length || 0} 项</span></summary>
          <div className="mt-3 max-h-96 space-y-2 overflow-auto">{snapshot?.operations.length ? [...snapshot.operations].reverse().map((operation) => <details key={operation.id} className="rounded-md border p-3 text-xs">
            <summary className="cursor-pointer"><span className="mr-2 text-muted-foreground">{{ model: "模型请求", tool: "工具调用", validation: "确定性校验", graph: "图谱更新" }[operation.kind]}</span><strong>{TOOL_NAMES[operation.name] || operation.name}</strong><span className="ml-3">{operation.status === "running" && !live ? "执行已停止" : STATUS[operation.status] || operation.status}</span><span className="ml-2 text-muted-foreground">{operation.elapsed_ms == null ? "" : `${(operation.elapsed_ms / 1000).toFixed(1)}s`}</span></summary>
            {operation.arguments && <><p className="mt-3">参数{operation.arguments.truncated && "（已截断）"}</p><JsonBlock value={operation.arguments.text} /></>}
            {operation.result && <><p className="mt-3">结果{operation.result.truncated && "（已截断）"}</p><JsonBlock value={operation.result.text} /></>}
          </details>) : <p className="text-xs text-muted-foreground">尚无操作记录</p>}</div><p className="mt-2 text-xs text-muted-foreground">模型和工具返回需经完整校验后才进入关系图谱。</p>
        </details>
        <details className="min-w-0 py-2 open:w-full" onToggle={(event) => { if (event.target === event.currentTarget) setContextOpen(event.currentTarget.open); }}><summary className="cursor-pointer text-xs font-medium"><Terminal className="mr-2 inline size-4 text-muted-foreground" />上下文 <span className="ml-2 font-normal text-muted-foreground">当前调用 · 只读</span></summary>
          {call && <div className="mt-3 flex flex-wrap gap-x-6 gap-y-2 rounded-md bg-primary/5 p-3 text-xs">{(!call.task_kind || call.task_kind === "recognition") && <><span>主体：{call.subject_label || "当前任务主体"}</span><span>本轮：{call.members?.length ? call.members.map((member) => member.predicate_label).join("、") : call.predicate_label || "当前任务谓词"}{(call.member_count || 1) > 1 && `（${call.member_count} 项）`}</span></>}<span>{phaseLabel(call.stage, call.task_kind)}</span></div>}
          <ContextPanel runId={runId} callId={call?.call_id} open={expanded && contextOpen} />
        </details>
      </div>
      {children}
    </div>
  </details>;
}
