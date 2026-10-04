"use client";

import { useMemo, useState } from "react";
import { Box, ChevronDown, ChevronRight, FileText, GitFork, ListTree, Search } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import type { DocumentHarnessCalibration, DocumentHarnessEntity, DocumentHarnessGraph, DocumentHarnessProperty, DocumentHarnessRelation, DocumentHarnessSourceRef } from "@/lib/api";
import { buildHarnessHierarchy, displayValue, filterHarnessEntities, harnessTreeRows,
  HARNESS_PHASES, HARNESS_STATES, hasObservationContext, observationSubjects, type HarnessHierarchy } from "@/lib/source-harness";
import { cn } from "@/lib/utils";
import { HarnessRelationCanvas } from "./source-harness-relation-graph";
import { EvidenceList, HarnessOntologyContext, HarnessVerificationLabel, StateBadge } from "./source-harness-shared";

export { HarnessRelationCanvas } from "./source-harness-relation-graph";

type SelectionProps = {
  graph: DocumentHarnessGraph;
  selectedEntity: string | null;
  onSelectEntity: (id: string) => void;
  onSource: (ref: DocumentHarnessSourceRef) => void;
  onObservations?: (id: string) => void;
};

export function HarnessCandidates({ graph, selectedEntity, onSelectEntity, onSource, onObservations }: SelectionProps) {
  const hierarchy = useMemo(() => buildHarnessHierarchy(graph), [graph]);
  const [mode, setMode] = useState("tree");
  const [query, setQuery] = useState("");
  const [state, setState] = useState("all");
  const [maxDepth, setMaxDepth] = useState(3);
  const [expanded, setExpanded] = useState<Map<string, boolean>>(() => {
    const ancestors = new Map<string, boolean>();
    let id = selectedEntity ?? "";
    while (hierarchy.parent.has(id)) {
      id = hierarchy.parent.get(id)!.subject_id;
      ancestors.set(id, true);
    }
    return ancestors;
  });
  const [limit, setLimit] = useState(200);
  const { visible, matched } = useMemo(() => filterHarnessEntities(graph, hierarchy, query, state), [graph, hierarchy, query, state]);
  const entity = hierarchy.entities.get(selectedEntity ?? "") ?? hierarchy.roots[0] ?? null;
  const filtering = Boolean(query.trim() || state !== "all");
  const { rows, truncated } = harnessTreeRows(hierarchy, visible, maxDepth, expanded, filtering, limit);
  const disconnected = hierarchy.disconnected.filter((item) => visible.has(item.id));
  const counts = new Map<string, number>();
  for (const property of graph.properties) counts.set(property.subject_id, (counts.get(property.subject_id) ?? 0) + 1);
  const toggle = (id: string, open: boolean) => setExpanded((current) => new Map(current).set(id, !open));
  const entityButton = (item: DocumentHarnessEntity, level: number | undefined, context = false, reference = false, relation?: DocumentHarnessRelation) => <button type="button"
    data-entity-id={item.id} aria-pressed={item.id === entity?.id} onClick={() => onSelectEntity(item.id)}
    className={cn("flex min-w-0 flex-1 items-start gap-2 rounded-md px-2 py-2.5 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
      item.id === entity?.id ? "bg-primary/10 text-primary" : "hover:bg-muted", context && "opacity-65")}>
    {item.role === "document_root" ? <FileText className="mt-0.5 size-4 shrink-0" /> : <Box className="mt-0.5 size-4 shrink-0" />}
    <span className="min-w-0 flex-1"><span className="block break-words text-sm font-medium">{item.label}</span>
      <span className="mt-1 block break-words text-xs text-muted-foreground">{item.class_label || "类型尚未对齐"} · {counts.get(item.id) ?? 0} 项属性</span>
      <span className="mt-1 flex flex-wrap items-center gap-1.5"><StateBadge state={item.state} />
        {relation && <span className="text-[11px] text-muted-foreground"><span aria-hidden="true">{relation.state === "accepted" ? "━" : "┄"} </span>关系{HARNESS_STATES[relation.state]}</span>}
        {relation && !relation.predicate_iri && <span className="text-[11px] text-muted-foreground">谓词待对齐</span>}
        {reference && <span className="rounded bg-secondary px-2 py-0.5 text-xs text-secondary-foreground">引用 · 不重复展开</span>}</span>
    </span>{level != null && <span className="shrink-0 rounded bg-muted px-1 text-[11px] text-muted-foreground">L{level}</span>}
  </button>;

  return <section className="min-w-0 space-y-4" aria-label="实体图谱与属性">
    <div className="flex flex-wrap items-center justify-between gap-2"><h3 className="text-lg font-semibold">实体图谱与属性</h3><p className="text-xs text-muted-foreground">实体 {graph.entities.length} · 属性 {graph.properties.length} · 关系 {hierarchy.relations.length} · 关系组 {graph.relation_groups?.length ?? 0}</p></div>
    <div className="flex flex-wrap items-center gap-2">
      <div className="flex rounded-md bg-muted p-1" aria-label="图谱视图">
        <Button size="sm" variant={mode === "tree" ? "secondary" : "ghost"} aria-pressed={mode === "tree"} onClick={() => setMode("tree")}><ListTree className="size-4" />层级树</Button>
        <Button size="sm" variant={mode === "graph" ? "secondary" : "ghost"} aria-pressed={mode === "graph"} onClick={() => setMode("graph")}><GitFork className="size-4" />关系图</Button>
      </div>
      <label className="relative min-w-48 flex-1 sm:max-w-72"><Search className="absolute left-3 top-3 size-4 text-muted-foreground" /><Input className="pl-9" aria-label="搜索实体或属性" placeholder="搜索实体或属性" value={query} onChange={(event) => setQuery(event.target.value)} /></label>
      <select aria-label="实体状态" value={state} onChange={(event) => setState(event.target.value)} className="h-10 rounded-md border bg-background px-3 text-sm"><option value="all">全部实体状态</option>{Object.entries(HARNESS_STATES).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select>
      <select aria-label="展开层级" disabled={filtering} title={filtering ? "筛选时自动展开匹配路径" : undefined} value={String(maxDepth)} onChange={(event) => { setMaxDepth(Number(event.target.value)); setExpanded(new Map()); }} className="h-10 rounded-md border bg-background px-3 text-sm disabled:opacity-50">
        <option value="0">仅文档根</option><option value="1">展开至 1 层</option><option value="2">展开至 2 层</option><option value="3">展开至 3 层</option><option value="999999">展开全部层级</option>
      </select>
      {filtering && <Button size="sm" variant="ghost" onClick={() => { setQuery(""); setState("all"); }}>清除筛选</Button>}
      <div className="flex min-w-60 flex-1 flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground"><span className="text-emerald-700 dark:text-emerald-400">● 已采信关系</span><span className="text-amber-700 dark:text-amber-400">● 未决关系</span><span>● 候选关系</span><span className="text-destructive">● 否定 / 未采信关系</span><span>未连接实体单列；条件仍需查看关系详情。</span></div>
    </div>
    <div className={cn("grid min-w-0 overflow-hidden rounded-lg border bg-card", mode === "tree"
      ? "xl:grid-cols-[340px_minmax(0,1fr)] 2xl:grid-cols-[390px_minmax(0,1fr)]"
      : "xl:grid-cols-[minmax(0,1fr)_minmax(380px,0.46fr)] 2xl:grid-cols-[minmax(0,1fr)_480px]")}>
      {mode === "tree" && <aside className="min-w-0 border-b bg-muted/20 p-4 xl:border-b-0 xl:border-r" aria-label="文档根实体层级树">
        <div className="mb-3 flex items-center justify-between gap-2"><h4 className="text-sm font-medium">文档根 → 实体层级</h4><Button size="sm" variant="ghost" disabled={filtering} onClick={() => { setMaxDepth(0); setExpanded(new Map()); }}>全部折叠</Button></div>
        {filtering && <p className="mb-2 text-xs text-muted-foreground">筛选时自动展开匹配实体的路径。</p>}
        {!graph.entities.length && <p className="text-sm text-muted-foreground">尚无已保存的实体候选。</p>}
        {graph.entities.length > 0 && !hierarchy.roots.length && <p className="mb-3 text-xs text-muted-foreground">尚无文档根，已保存实体列在未连接分组。</p>}
        {filtering && !matched.size && <p role="status" className="py-4 text-sm text-muted-foreground">没有符合筛选条件的实体或属性。</p>}
        <ul className="max-h-[680px] space-y-1 overflow-auto pr-1" aria-label="分层实体列表">
          {rows.map((row) => row.kind === "predicate" ? <li key={row.id} data-predicate-iri={row.predicateIri ?? undefined} data-subject-id={row.subjectId} style={{ paddingLeft: Math.min(row.level, 6) * 28 - 14 }}>
            <button type="button" disabled={filtering} aria-label={`${row.expanded ? "收起" : "展开"}${row.label}关系分组`} aria-expanded={row.expanded}
              title={row.predicateIri ?? "谓词待对齐"} onClick={() => toggle(row.id, row.expanded)}
              className="flex w-full items-center gap-1.5 rounded-md py-2 pr-2 text-left text-xs text-muted-foreground hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50">
              {row.expanded ? <ChevronDown className="size-4 shrink-0" /> : <ChevronRight className="size-4 shrink-0" />}<span className="break-words">{row.label}</span>
              {row.pendingCount > 0 && <span className="shrink-0">（{row.pendingCount} 项待对齐）</span>}
            </button>
          </li> : <li key={row.id} style={{ paddingLeft: Math.min(row.level, 6) * 28 }}>
            <div className="flex items-start rounded-md border border-transparent">
              {row.expandable ? <button type="button" disabled={filtering} aria-label={`${row.expanded ? "收起" : "展开"}${row.entity.label}的下级关系`} aria-expanded={row.expanded} className="mt-3 rounded p-0.5 focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50" onClick={() => toggle(row.entity.id, row.expanded)}>{row.expanded ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}</button> : <span className="w-5 shrink-0" />}
              {entityButton(row.entity, hierarchy.depth.get(row.entity.id), !matched.has(row.entity.id), row.reference, row.relation)}
            </div>
          </li>)}
        </ul>
        {truncated && <Button variant="outline" size="sm" className="mt-3" onClick={() => setLimit((current) => current + 200)}>再显示 200 项</Button>}
        {disconnected.length > 0 && <details className="mt-4 border-t pt-3" open={filtering || !hierarchy.roots.length || Boolean(entity && !hierarchy.depth.has(entity.id))}>
          <summary className="cursor-pointer text-sm">未连接到文档根（{disconnected.length}）</summary><p className="my-2 text-xs text-muted-foreground">当前阶段：{HARNESS_PHASES[graph.progress.phase]}。语义任务已处理 {graph.progress.phase_work_counts.semantic.done} 项，确定性任务已处理 {graph.progress.phase_work_counts.deterministic.done} 项。现有属性与原文仍可查看。</p>
          <ul className="max-h-96 overflow-auto">{disconnected.slice(0, limit).map((item) => <li key={item.id}>{entityButton(item, undefined)}</li>)}</ul>
          {disconnected.length > limit && <Button variant="ghost" size="sm" onClick={() => setLimit((current) => current + 200)}>显示更多未连接实体</Button>}
        </details>}
        <p className="mt-4 text-xs leading-relaxed text-muted-foreground">层级表示关系路径；多父节点与环路保留引用，不表示组成关系或全局身份合并。</p>
      </aside>}
      {mode === "graph" && <div className="min-w-0 border-b bg-muted/10 p-3 xl:border-b-0" role="region" aria-label="关系图面板">
        <HarnessRelationCanvas graph={graph} subject={entity ?? undefined} visible={visible}
          revealGroups={filtering} onSelectEntity={onSelectEntity} />
      </div>}
      <div className={cn("min-w-0 p-4 sm:p-6", mode === "graph" && "xl:max-h-[720px] xl:overflow-y-auto xl:border-l")}
        role={mode === "graph" ? "region" : undefined} aria-label={mode === "graph" ? "节点属性面板" : undefined}>
        {mode === "graph" && <div className="mb-5 border-b pb-3"><h4 className="text-sm font-semibold">节点属性</h4><p className="mt-1 text-xs text-muted-foreground">点击左侧实体节点查看对应属性、关系和原文依据。</p></div>}
        {entity ? <HarnessEntityDetail key={entity.id} graph={graph} entity={entity} hierarchy={hierarchy} onSelectEntity={onSelectEntity} onSource={onSource} onObservations={onObservations} />
        : <p className="py-10 text-center text-sm text-muted-foreground">选择文档根或实体，查看属性、关系与原文依据。</p>}</div>
    </div>
  </section>;
}

function CalibrationDetail({ calibration }: { calibration: DocumentHarnessCalibration | null }) {
  if (!calibration) return <p className="text-xs text-muted-foreground">待语义核对 / 待确定性检查</p>;
  const labels = { not_run: "待检查", passed: "通过", invalid: "不符合", incomplete: "输入不足", not_applicable: "不适用", error: "工具失败" };
  const names = { identifier: "编号", datatype: "类型表示", unit: "单位", shacl: "SHACL" };
  const literal = calibration.literal;
  return <div className="space-y-1 text-xs"><p>{Object.entries(calibration.checks).map(([name, check]) => `${names[name as keyof typeof names]}：${labels[check.status]}${check.reason_code ? `（${check.reason_code}）` : ""}`).join(" · ")}</p>
    {literal && <p>规范值：{literal.kind === "range" ? `${literal.lower_inclusive ? "[" : "("}${literal.lower}, ${literal.upper}${literal.upper_inclusive ? "]" : ")"}` : `${literal.operator !== "eq" ? literal.operator + " " : ""}${displayValue(literal.normalized_value)}`} {literal.canonical_unit ?? ""}</p>}
    {literal && Object.keys(literal.conversion_record).length > 0 && <p>换算因子：{displayValue(literal.conversion_record.factor)} · 偏移：{displayValue(literal.conversion_record.offset)}</p>}
  </div>;
}

export function HarnessPropertyDetail({ item, onSource }: { item: DocumentHarnessProperty; onSource: SelectionProps["onSource"] }) {
  return <div className="space-y-3 rounded-md bg-muted/30 p-4 text-sm">
    <div className="flex flex-wrap items-center gap-2"><h5 className="font-medium">{item.label}：{displayValue(item.value)}</h5><StateBadge state={item.state} /><HarnessVerificationLabel verification={item.verification} />{!item.predicate_iri && <Badge variant="outline">尚未匹配合法属性</Badge>}</div>
    <p className="whitespace-pre-wrap break-words text-xs">属性核对原因：{item.reason}</p>
    <HarnessOntologyContext card={item.card} predicate={item.predicate} />
    <p className="whitespace-pre-wrap break-words text-xs text-muted-foreground">原值：{item.source_value}{item.value_component !== "whole" && <> · {item.value_component === "span" ? "原文中的属性值" : item.value_component === "lower" ? "下限" : "上限"}{item.source_unit ? `（${item.source_unit}）` : ""}</>}</p>
    <CalibrationDetail calibration={item.calibration} />
    {item.source_unit_evidence.length > 0 && <div><p className="text-xs">单位原文：{item.source_unit}</p><EvidenceList evidence={item.source_unit_evidence} onSource={onSource} /></div>}
    {item.value_evidence?.length > 0 && <div className="space-y-1"><p className="text-xs text-muted-foreground">取值位置</p><EvidenceList evidence={item.value_evidence} onSource={onSource} /></div>}
    <div className="space-y-1"><p className="text-xs text-muted-foreground">原文依据</p><EvidenceList evidence={item.evidence} onSource={onSource} />{!item.evidence.length && <p className="text-xs text-muted-foreground">未记录原文引用。</p>}</div>
  </div>;
}

function HarnessEntityDetail({ graph, entity, hierarchy, onSelectEntity, onSource, onObservations }: Omit<SelectionProps, "selectedEntity"> & { entity: DocumentHarnessEntity; hierarchy: HarnessHierarchy }) {
  const [tab, setTab] = useState("properties");
  const [propertyId, setPropertyId] = useState<string | null>(null);
  const properties = graph.properties.filter((item) => item.subject_id === entity.id);
  const selected = properties.find((item) => item.id === propertyId) ?? properties[0];
  const relations = hierarchy.relations.filter((item) => item.subject_id === entity.id || item.object_id === entity.id);
  const mentions = new Map(graph.entities.flatMap((item) => item.mentions ?? []).map((item) => [item.id, item]));
  const groups = (graph.relation_groups ?? []).filter((item) => item.subject_id === entity.id || item.object_ids.includes(entity.id));
  const targets = graph.targets.filter((item) => item.subject_id === entity.id);
  const observationContext = graph.observations.every(hasObservationContext);
  const observations = graph.observations.filter((item) => hasObservationContext(item) && item.kind !== "failure" && (observationSubjects(item).includes(entity.id) || item.object_id === entity.id));
  const path = [entity];
  let current = entity.id;
  while (hierarchy.parent.has(current)) { current = hierarchy.parent.get(current)!.subject_id; path.unshift(hierarchy.entities.get(current)!); }
  const depth = hierarchy.depth.get(entity.id);
  return <div className="space-y-5">
    <nav aria-label="所选实体路径" className="flex flex-wrap items-center gap-1 text-xs text-muted-foreground">{path.map((item, index) => <span key={item.id} className="flex items-center gap-1">{index > 0 && <ChevronRight className="size-3" />}<button type="button" className="break-words text-left hover:text-primary hover:underline" onClick={() => onSelectEntity(item.id)}>{item.label}</button></span>)}</nav>
    <div className="flex flex-wrap items-start justify-between gap-3"><div className="min-w-0 space-y-1"><h4 className="break-words text-xl font-semibold">{entity.label}</h4><p className="text-xs text-muted-foreground">{entity.role === "document_root" ? "L0 · 文档根 · 类型由任务指定" : depth == null ? "未连接到文档根" : `L${depth} · 距文档根 ${depth} 跳`} · <span className="break-all">{entity.id}</span></p></div><StateBadge state={entity.state} /></div>
    <div className="space-y-2 rounded-md bg-muted/40 p-3 text-xs"><p>主体当前类型：{entity.class_label || "尚未对齐"}<code className="ml-2 break-all text-muted-foreground">{entity.class_iri}</code></p><p className="whitespace-pre-wrap break-words text-muted-foreground">{entity.reason}</p><details><summary className="cursor-pointer">实体原文依据（{entity.evidence.length}）</summary><EvidenceList evidence={entity.evidence} onSource={onSource} /></details></div>
    <CalibrationDetail calibration={entity.calibration} />
    {entity.parent_mention_id && <button type="button" className="text-xs text-primary hover:underline" onClick={() => onSelectEntity(entity.parent_mention_id!)}>查看原提及及原文</button>}
    {entity.refined_member_ids.length > 0 && <div className="flex flex-wrap gap-2 text-xs"><span>已细分成员：</span>{entity.refined_member_ids.map((id) => <button type="button" key={id} className="text-primary hover:underline" onClick={() => onSelectEntity(id)}>{hierarchy.entities.get(id)?.label ?? id}</button>)}</div>}
    {entity.role !== "document_root" && <HarnessCoreferences graph={graph} entity={entity} onSource={onSource} />}
    <Tabs value={tab} onValueChange={setTab}>
      <TabsList className="h-auto max-w-full flex-wrap justify-start"><TabsTrigger value="properties">实体属性 {properties.length}</TabsTrigger><TabsTrigger value="relations">关联关系 {relations.length + groups.length}</TabsTrigger><TabsTrigger value="observations">关联观察 {observations.length}</TabsTrigger></TabsList>
      <TabsContent value="properties" className="space-y-4 pt-2">
        {!properties.length ? <p className="py-6 text-sm text-muted-foreground">该实体尚无属性候选。</p> : <>
          <div className="overflow-x-auto"><table className="w-full min-w-[550px] text-left text-xs"><thead className="bg-muted/40 text-muted-foreground"><tr>{["本体属性", "属性值", "定义来源", "核验状态", "原文依据"].map((label) => <th key={label} scope="col" className="px-3 py-2.5 font-normal">{label}</th>)}</tr></thead><tbody>{properties.map((item) => <tr key={item.id} className={cn("border-b", selected?.id === item.id && "bg-primary/5")}>
            <td className="max-w-52 px-3 py-3"><button type="button" aria-pressed={selected?.id === item.id} onClick={() => setPropertyId(item.id)} className="break-words text-left font-medium text-primary hover:underline">{item.label}</button></td>
            <td className="max-w-56 whitespace-pre-wrap break-words px-3 py-3">{displayValue(item.value)}</td>
            <td className="max-w-52 px-3 py-3"><span className="block break-all text-muted-foreground" title={item.predicate?.namespace}>{item.predicate?.namespace || "未记录"}</span></td>
            <td className="px-3 py-3"><StateBadge state={item.state} /></td>
            <td className="px-3 py-3">{item.evidence[0] ? <button type="button" className="whitespace-nowrap text-primary hover:underline" onClick={() => onSource(item.evidence[0])}>{item.evidence[0].page != null ? `p.${item.evidence[0].page} · ` : ""}查看</button> : "未记录"}</td>
          </tr>)}</tbody></table></div>
          {selected && <HarnessPropertyDetail item={selected} onSource={onSource} />}
        </>}
      </TabsContent>
      <TabsContent value="relations" className="space-y-3 pt-2">
        {!relations.length && !groups.length && <p className="py-6 text-sm text-muted-foreground">尚无该实体的关系候选。</p>}
        {groups.map((group) => <div key={group.id} className="space-y-3 rounded-md border p-3">
          <div className="flex flex-wrap items-center gap-2 text-sm"><span>{hierarchy.entities.get(group.subject_id)?.label} → {group.label}</span><StateBadge state={group.state} /><HarnessVerificationLabel verification={group.verification} /></div>
          <p className="text-sm">{{ options: "备选对象组", all: "所有成员均参与", unknown: "参与方式未决" }[group.participation]}{group.selection === "exactly_one" && " · 择一"}：{group.object_ids.map((id, index) => <span key={id}>{index > 0 && "、"}<button type="button" className="text-primary hover:underline" onClick={() => onSelectEntity(id)}>{hierarchy.entities.get(id)?.label ?? id}</button></span>)}</p>
          <p className="text-xs text-muted-foreground">极性：{{ positive: "肯定", negative: "否定", uncertain: "未确定" }[group.polarity]}{group.conditions.length > 0 && ` · 条件：${group.conditions.join("；")}`}</p>
          <p className="whitespace-pre-wrap break-words text-xs">{group.reason}</p>
          <CalibrationDetail calibration={group.calibration} />
          {group.ordered_object_ids && <p className="text-xs">获证顺序：{group.ordered_object_ids.map((id) => hierarchy.entities.get(id)?.label ?? id).join(" → ")}</p>}
          {group.order_evidence.length > 0 && <EvidenceList evidence={group.order_evidence} onSource={onSource} />}
          <div className="flex flex-wrap items-center gap-2 text-xs"><span>时间：{{ parallel: "并行", sequential: "先后", unspecified: "未说明" }[group.timing]}</span><StateBadge state={group.timing_state} /><span>{group.timing_reason}</span></div>
          <HarnessOntologyContext card={group.card} predicate={group.predicate} /><EvidenceList evidence={group.evidence} onSource={onSource} />
        </div>)}
        {relations.map((relation) => <div key={relation.id} className="space-y-3 rounded-md border p-3"><div className="flex flex-wrap items-center gap-2 text-sm">
          {[relation.subject_id, relation.object_id].map((id, index) => <span key={`${index}:${id}`} className="contents">{index > 0 && <span title={relation.predicate_iri ?? "谓词待对齐"}>→ {relation.label} →</span>}<button type="button" className="break-words text-primary hover:underline" onClick={() => onSelectEntity(id)}>{hierarchy.entities.get(id)?.label ?? id}</button></span>)}<StateBadge state={relation.state} />{relation.assertions.length === 1 && <HarnessVerificationLabel verification={relation.verification} />}</div>
          <p className="text-xs text-muted-foreground">极性：{relation.polarity === "positive" ? "肯定" : relation.polarity === "negative" ? "否定" : "未确定"}{relation.conditions.length > 0 && ` · 条件：${relation.conditions.join("；")}`}</p>
          <p className="whitespace-pre-wrap break-words text-xs">{relation.assertions.length > 1 ? `汇集 ${relation.assertions.length} 条原始关系依据，逐条核验结果见下方。` : relation.reason}</p><HarnessOntologyContext card={relation.card} predicate={relation.predicate} /><EvidenceList evidence={relation.evidence} onSource={onSource} />
          {relation.assertions.length > 1 && <details className="space-y-2 text-xs">
            <summary className="cursor-pointer">原始关系依据（{relation.assertions.length} 条）</summary>
            {relation.assertions.map((assertion) => <div key={assertion.id} className="space-y-2 border-l pl-3">
              <p>原文主体：{mentions.get(assertion.subject_mention_id)?.label ?? assertion.subject_mention_id} · 原文对象：{mentions.get(assertion.object_mention_id)?.label ?? assertion.object_mention_id}</p>
              <StateBadge state={assertion.state} /><HarnessVerificationLabel verification={assertion.verification} />
              <p className="whitespace-pre-wrap break-words">{assertion.reason}</p>
              <CalibrationDetail calibration={assertion.calibration} />
              <HarnessOntologyContext card={assertion.card} predicate={assertion.predicate} />
              <EvidenceList evidence={assertion.evidence} onSource={onSource} />
            </div>)}
          </details>}
        </div>)}
      </TabsContent>
      <TabsContent value="observations" className="space-y-3 pt-2"><p className="text-sm text-muted-foreground">{!observationContext ? "观察上下文暂不可用，请稍后刷新。" : observations.length ? `有 ${observations.length} 条关联原文观察。参考卡和实际对齐结果在观察详情分别展示。` : "尚无关联原文观察。"}</p>{onObservations && <Button variant="outline" size="sm" onClick={() => onObservations(entity.id)}>查看该实体的原文观察</Button>}</TabsContent>
    </Tabs>
    {targets.length > 0 && <details className="border-t pt-3"><summary className="cursor-pointer text-sm">当前本体卡目标（{targets.length}）</summary><p className="my-2 text-xs text-muted-foreground">待发现目标不是实体或事实，不用于建立图谱连边。</p><ul className="space-y-2">{targets.map((target) => <li key={target.id} className="flex flex-wrap items-center gap-2 text-xs"><span title={target.predicate_iri}>{target.kind === "relation" ? "关系" : "属性"}：{target.label}</span><span className="text-muted-foreground">{target.range_labels.join(" / ")}</span><Badge variant="outline">{target.state === "accepted" ? "已有采信项" : target.state === "candidate" ? "已有候选" : "待发现"}</Badge></li>)}</ul></details>}
  </div>;
}

export function HarnessCoreferences({ graph, entity, onSource }: {
  graph: DocumentHarnessGraph; entity: DocumentHarnessEntity; onSource: SelectionProps["onSource"];
}) {
  if (!Array.isArray(graph.coreferences) || !Array.isArray(entity.mentions)
    || graph.entities.some((item) => !Array.isArray(item.mentions))) {
    return <p role="status" className="rounded-md border p-3 text-xs text-muted-foreground">原文提及与共指数据暂不可用，请刷新结果。</p>;
  }
  const mentions = new Map(graph.entities.flatMap((item) => item.mentions).map((item) => [item.id, item]));
  const ids = new Set(entity.mentions.map((item) => item.id));
  const decisions = graph.coreferences.filter((item) => ids.has(item.left_mention_id) || ids.has(item.right_mention_id));
  return <details className="space-y-3 rounded-md border p-3 text-xs">
    <summary className="cursor-pointer font-medium">原文提及与共指 · {entity.mentions.length} 处提及 · {decisions.length} 项判定</summary>
    <p className="text-muted-foreground">{entity.mentions.length > 1 ? "这些提及已确认指向同一个文档内对象；各条属性和关系保留原文归属及条件。" : "尚未与其他提及归并；保持独立不表示已经确认是不同对象。"}</p>
    {entity.mentions.map((mention) => <div key={mention.id} className="space-y-2 border-t pt-3"><p className="font-medium">{mention.label} · {mention.role}</p><EvidenceList evidence={mention.evidence} onSource={onSource} /></div>)}
    {!decisions.length && <p className="text-muted-foreground">暂无共指判定；类型未确认或不兼容的提及不参与自动归并。</p>}
    {decisions.map((item) => <div key={item.id} className="space-y-2 border-t pt-3">
      <p className="font-medium">{mentions.get(item.left_mention_id)?.label ?? item.left_mention_id} ↔ {mentions.get(item.right_mention_id)?.label ?? item.right_mention_id} · {{ same: "同一", different: "不同", unresolved: "未决" }[item.verdict]}</p>
      {item.verdict === "same" && <p className="text-muted-foreground">{item.applied ? "已归并到同一文档内实体" : "未归并：组内存在未决、缺失或冲突判定"}</p>}
      <p className="whitespace-pre-wrap break-words">{item.reason}</p>
      <EvidenceList evidence={item.proof.length ? item.proof : item.evidence} onSource={onSource} />
    </div>)}
  </details>;
}
