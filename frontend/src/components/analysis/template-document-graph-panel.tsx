"use client";

import { useMemo } from "react";
import type { TreeInstance } from "@headless-tree/core";
import { ExternalLink, Loader2, Pause, Play, RefreshCw } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Tree, TreeItem, TreeItemLabel } from "@/components/ui/tree";
import { useDocumentTree, type DocumentTreeNode } from "@/components/ui/use-document-tree";
import {
  getIdentity,
  type DocumentAnalysisGraphArtifact, type DocumentGraphAssertionBase,
  type DocumentGraphCoverageSubject, type DocumentGraphEntity,
  type DocumentGraphProperty, type DocumentGraphRelationship,
} from "@/lib/api";
import {
  DOCUMENT_ANALYSIS_STATUS_LABELS, documentCoverageScope, formatDocumentAnalysisReason,
} from "@/lib/document-analysis";
import { useTemplateDocumentRun } from "./use-template-document-run";

type Model = ReturnType<typeof useTemplateDocumentRun>;

export function branchProgress(branch?: DocumentGraphCoverageSubject): string {
  if (branch?.candidate_policy === "sparse-candidates-v1") {
    if (!branch.records_planned) return "本轮尚无入选候选，原文未核验";
    const parts = [`已核验 ${branch.records_examined}/${branch.records_planned} 项候选任务`];
    if (branch.records_incomplete) parts.push(`${branch.records_incomplete} 项技术未完成`);
    if (branch.records_unattempted) parts.push(`${branch.records_unattempted} 项待处理`);
    return parts.join(" · ");
  }
  if (!branch || branch.records_examined + branch.records_incomplete === 0) return "未尝试";
  const parts = [`已检查 ${branch.records_examined}/${branch.records_planned} 条原文`];
  if (branch.records_incomplete) parts.push(`${branch.records_incomplete} 条处理未完成`);
  if (branch.records_unattempted) parts.push(`${branch.records_unattempted} 条待检查`);
  return parts.join(" · ");
}

function SourceButton({ refs, label, select }: { refs: string[]; label: string; select: Model["select"] }) {
  if (!refs.length) return null;
  return <span className="inline-flex flex-wrap gap-1">
    {refs.map((ref, index) => <button key={ref} type="button" data-tree-action="source"
      className="inline-flex items-center gap-1 text-xs text-primary underline-offset-2 hover:underline"
      aria-label={`${label}${refs.length > 1 ? ` ${index + 1}` : ""}原文`}
      onClick={() => select(ref)}><ExternalLink className="size-3" />
      {label}{refs.length > 1 ? ` ${index + 1}` : ""}</button>)}
  </span>;
}

function AssertionProof({ item, select }: { item: DocumentGraphAssertionBase; select: Model["select"] }) {
  const effective = item.policy_eligible && item.structural_valid && item.model_supported
    && item.polarity === "affirmed" && !item.invalidated && item.independent_review !== "rejected";
  return <div className="space-y-1">
    <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
      <span>{effective ? "系统验证通过" : item.polarity === "negated" ? "否定陈述"
        : item.polarity === "conditional" ? "有条件陈述" : "未通过有效性核验"}</span>
      <SourceButton refs={item.source_selection_refs.predicate_bridge}
        label={item.source_selection_refs.value.length ? "属性依据" : "关系依据"} select={select} />
      <SourceButton refs={item.source_selection_refs.subject} label="主体归属" select={select} />
      <SourceButton refs={item.source_selection_refs.unit ?? []} label="单位依据" select={select} />
      <SourceButton refs={item.source_selection_refs.condition} label="条件" select={select} />
      <SourceButton refs={item.source_selection_refs.counterevidence} label="反证" select={select} />
    </div>
    {!effective && item.reason && <p className="text-xs text-muted-foreground">{item.reason}</p>}
  </div>;
}

type Predicate = { predicate_iri: string; predicate_label: string };

/** One index and a spanning forest bound rendering to entities + edges, including disconnected cycles. */
export function buildTemplateGraphIndex(graph: DocumentAnalysisGraphArtifact) {
  const entities = new Map(graph.entities.map((entity) => [entity.entity_id, entity]));
  const outgoing = new Map<string, DocumentGraphRelationship[]>();
  const edges = new Map<string, Map<string, DocumentGraphRelationship[]>>();
  const properties = new Map<string, Map<string, DocumentGraphProperty[]>>();
  const coverage = new Map<string, Map<string, DocumentGraphCoverageSubject>>();
  const add = <T,>(index: Map<string, Map<string, T[]>>, subject: string, predicate: string, item: T) => {
    let slots = index.get(subject);
    if (!slots) { slots = new Map(); index.set(subject, slots); }
    let values = slots.get(predicate);
    if (!values) { values = []; slots.set(predicate, values); }
    values.push(item);
  };
  for (const edge of graph.relationships) {
    add(edges, edge.subject_ref.entity_id, edge.predicate_iri, edge);
    const rows = outgoing.get(edge.subject_ref.entity_id) ?? [];
    rows.push(edge);
    outgoing.set(edge.subject_ref.entity_id, rows);
  }
  for (const item of graph.properties) add(properties, item.subject_ref.entity_id, item.predicate_iri, item);
  for (const item of graph.coverage.subjects) {
    let slots = coverage.get(item.subject_ref.entity_id);
    if (!slots) { slots = new Map(); coverage.set(item.subject_ref.entity_id, slots); }
    slots.set(item.predicate_iri, item);
  }
  const parent = new Map<string, { subjectId: string; predicate: string; candidateId: string }>();
  const depth = new Map<string, number>();
  const roots: DocumentGraphEntity[] = [];
  const rootId = graph.graph_snapshot?.root_ref.entity_id;
  const orderedIds = rootId && entities.has(rootId) ? [rootId, ...entities.keys()] : [...entities.keys()];
  for (const seed of orderedIds) {
    if (depth.has(seed)) continue;
    roots.push(entities.get(seed)!);
    depth.set(seed, 0);
    const queue = [seed];
    for (let cursor = 0; cursor < queue.length; cursor++) {
      const subjectId = queue[cursor];
      for (const edge of outgoing.get(subjectId) ?? []) {
        const targetId = edge.object_ref.entity_id;
        if (!entities.has(targetId) || depth.has(targetId)) continue;
        depth.set(targetId, depth.get(subjectId)! + 1);
        parent.set(targetId, { subjectId, predicate: edge.predicate_iri, candidateId: edge.candidate_id });
        queue.push(targetId);
      }
    }
  }
  return { entities, edges, properties, coverage, roots, parent, depth, rootId };
}

type GraphIndex = ReturnType<typeof buildTemplateGraphIndex>;
const entityKey = (id: string) => JSON.stringify(["entity", id]);
const relationKey = (id: string, predicate: string) => JSON.stringify(["relation", id, predicate]);

export function templateGraphJumpPath(index: GraphIndex, entityId: string): string[] {
  const path = [entityKey(entityId)];
  let current = entityId;
  while (index.parent.has(current)) {
    const parent = index.parent.get(current)!;
    path.push(entityKey(parent.subjectId), relationKey(parent.subjectId, parent.predicate));
    current = parent.subjectId;
  }
  if (current !== index.rootId) path.push("unassociated");
  return path;
}

type GraphTreeNode = DocumentTreeNode & (
  | { kind: "group"; note?: string }
  | { kind: "entity"; entity: DocumentGraphEntity; incoming?: DocumentGraphRelationship }
  | { kind: "field"; field: Predicate; coverage?: DocumentGraphCoverageSubject }
  | { kind: "value"; value: DocumentGraphProperty }
  | { kind: "relation"; relation: Predicate; coverage?: DocumentGraphCoverageSubject }
  | { kind: "reference"; target?: DocumentGraphEntity; edge: DocumentGraphRelationship }
);

/** Project the canonical forest into stable tree occurrences; proofs stay attached
 * to their own assertion, including edges rendered as references. */
export function buildTemplateTreeData(index: GraphIndex) {
  const nodes = new Map<string, GraphTreeNode>();
  const rootId = "graph-root";
  const root = index.rootId ? index.entities.get(index.rootId) : undefined;
  const unassociated = index.roots.filter((entity) => entity !== root);
  nodes.set(rootId, { kind: "group", name: "关系图谱", children: [
    ...(root ? [entityKey(root.entity_id)] : []), ...(unassociated.length ? ["unassociated"] : []),
  ] });
  if (unassociated.length) nodes.set("unassociated", { kind: "group",
    name: `未关联实体（${unassociated.length} 组）`, children: unassociated.map((entity) => entityKey(entity.entity_id)) });
  const incoming = new Map<string, DocumentGraphRelationship>();
  for (const entity of index.entities.values()) {
    const id = entity.entity_id;
    const menu = entity.predicate_menu;
    const properties = index.properties.get(id);
    const edges = index.edges.get(id);
    const coverage = index.coverage.get(id);
    const fields: Predicate[] = menu?.filter((item) => item.kind === "property")
      ?? [...(properties?.values() ?? [])].map((values) => values[0]);
    const relations: Predicate[] = menu?.filter((item) => item.kind === "relationship")
      ?? [...(edges?.values() ?? [])].map((values) => values[0]);
    const propertyGroup = JSON.stringify(["properties", id]);
    const emptyGroup = JSON.stringify(["fields", id]);
    const populated: string[] = [], empty: string[] = [];
    for (const field of fields) {
      const fieldId = JSON.stringify(["field", id, field.predicate_iri]);
      const values = properties?.get(field.predicate_iri) ?? [];
      (values.length ? populated : empty).push(fieldId);
      const children = values.map((value) => {
        const key = JSON.stringify(["value", id, field.predicate_iri, value.candidate_id]);
        nodes.set(key, { kind: "value", name: value.raw_value, value, children: [] });
        return key;
      });
      nodes.set(fieldId, { kind: "field", field, name: field.predicate_label, children,
        defaultExpanded: values.length > 0, coverage: coverage?.get(field.predicate_iri) });
    }
    if (empty.length) nodes.set(emptyGroup, { kind: "group", name: "待检查及暂无结果的属性", children: empty });
    nodes.set(propertyGroup, { kind: "group", name: menu ? `属性（${fields.length}）` : "属性",
      children: [...populated, ...(empty.length ? [emptyGroup] : [])], defaultExpanded: true,
      note: menu && !fields.length ? "不适用：本体未声明数据属性" : undefined });
    const children = [propertyGroup];
    for (const relation of relations) {
      const key = relationKey(id, relation.predicate_iri);
      children.push(key);
      const matches = edges?.get(relation.predicate_iri) ?? [];
      const targets = matches.map((edge) => {
        const target = index.entities.get(edge.object_ref.entity_id);
        if (target && index.parent.get(target.entity_id)?.candidateId === edge.candidate_id) {
          incoming.set(target.entity_id, edge);
          return entityKey(target.entity_id);
        }
        const ref = JSON.stringify(["reference", id, relation.predicate_iri, edge.candidate_id]);
        nodes.set(ref, { kind: "reference", edge, target, children: [],
          name: target ? `引用：${target.label} · 跳转到实体` : "目标实体不可用" });
        return ref;
      });
      nodes.set(key, { kind: "relation", relation, name: relation.predicate_label, children: targets,
        defaultExpanded: matches.length > 0, coverage: coverage?.get(relation.predicate_iri) });
    }
    nodes.set(entityKey(id), { kind: "entity", entity, name: entity.label, children,
      defaultExpanded: (index.depth.get(id) ?? 0) <= 1 });
  }
  for (const [id, edge] of incoming) {
    const node = nodes.get(entityKey(id));
    if (node?.kind === "entity") node.incoming = edge;
  }
  return { rootId, nodes };
}

export function TemplateGraphTree({ graph, select }: { graph: DocumentAnalysisGraphArtifact; select: Model["select"] }) {
  const index = useMemo(() => buildTemplateGraphIndex(graph), [graph]);
  const data = useMemo(() => buildTemplateTreeData(index), [index]);
  const jump = (tree: TreeInstance<GraphTreeNode>, id: string) => {
    // Expand the complete canonical path before moving focus. Headless Tree waits
    // for newly visible rows to mount; DOM identity uses entity ID, never a label.
    for (const key of templateGraphJumpPath(index, id)) {
      if (data.nodes.has(key)) {
        const item = tree.getItemInstance(key);
        if (!item.isExpanded()) item.expand();
      }
    }
    const target = tree.getItemInstance(entityKey(id));
    tree.setSelectedItems([target.getId()]);
    target.setFocused();
    tree.updateDomFocus();
  };
  const tree = useDocumentTree(data, (node, instance) => {
    if (node.kind === "reference" && node.target) jump(instance, node.target.entity_id);
  });
  return <Tree tree={tree} aria-label="关系图谱树" indent={12}>
    {tree.getItems().map((item) => {
      const node = item.getItemData();
      return <TreeItem key={item.getId()} item={item}
        data-entity-id={node.kind === "entity" ? node.entity.entity_id : undefined}
        data-predicate={node.kind === "field" ? node.field.predicate_iri
          : node.kind === "relation" ? node.relation.predicate_iri : undefined}>
        <TreeItemLabel item={item}>
          {node.kind === "entity" ? <div className="space-y-1">
            <p className="font-medium">{node.name} <span className="font-normal text-muted-foreground">{node.entity.class_label}</span></p>
            <SourceButton refs={node.entity.source_selection_refs} label="实体名称" select={select} />
            {node.incoming && <AssertionProof item={node.incoming} select={select} />}
          </div> : node.kind === "value" ? <div className="space-y-1">
            <p>{node.value.raw_value}</p>
            {node.value.unit && node.value.normalized_value != null &&
              <p className="text-xs text-muted-foreground">规范化值：{String(node.value.normalized_value)} {node.value.unit}</p>}
            <SourceButton refs={node.value.source_selection_refs.value} label="属性值" select={select} />
            <AssertionProof item={node.value} select={select} />
          </div> : node.kind === "reference" ? <div className="space-y-1">
            {node.target ? <button type="button" data-tree-action="reference"
              className="text-left text-primary underline-offset-2 hover:underline"
              data-entity-reference={node.target.entity_id} onClick={() => jump(tree, node.target!.entity_id)}>
              {node.name}
            </button> : <p>{node.name}</p>}
            <AssertionProof item={node.edge} select={select} />
          </div> : <div className="space-y-1">
            <p className="font-medium">{node.name}
              {node.kind === "relation" && <span className="ml-2 text-xs font-normal text-muted-foreground">
                {node.children.length ? `${node.children.length} 项` : "尚无有效关系"}</span>}
            </p>
            {(node.kind === "field" || node.kind === "relation") &&
              <p className="text-xs text-muted-foreground">{branchProgress(node.coverage)}</p>}
            {node.kind === "field" && !node.children.length &&
              <p className="text-xs text-muted-foreground">尚无有效属性值</p>}
            {node.kind === "group" && node.note && <p className="text-xs text-muted-foreground">{node.note}</p>}
          </div>}
        </TreeItemLabel>
      </TreeItem>;
    })}
  </Tree>;
}

export function TemplateDocumentGraphPanel({ model }: { model: Model }) {
  const { run, graph } = model;
  const { username, role } = getIdentity();
  const root = graph?.entities.find((item) => item.entity_id === graph.graph_snapshot?.root_ref.entity_id);
  const rankingCalls = model.ranking?.cost.model_calls ?? graph?.ranking?.cost.model_calls ?? 0;
  return <section className="flex min-h-0 flex-1 flex-col" aria-label="关系图谱">
    <div className="space-y-2 border-b px-4 py-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold">关系图谱</h3>
        <Button variant="outline" size="sm" disabled={!model.canCreate || model.running || model.busy || model.loading}
          onClick={model.start}><RefreshCw className="mr-1 size-3.5" />{run ? "重新识别" : "开始识别"}</Button>
      </div>
      <p className="text-xs text-muted-foreground">查看实体、属性、关系及其原文依据</p>
      {run && <>
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant="outline">{DOCUMENT_ANALYSIS_STATUS_LABELS[run.status]}</Badge>
          <span className="text-xs text-muted-foreground" role="status">
            已处理 {run.progress.tasks_attempted} 项 · 识别调用 {run.progress.model_calls} 次
            {rankingCalls > 0 && ` · 排序调用 ${rankingCalls} 次`}
          </span>
          {model.running && <Loader2 className="size-3.5 animate-spin" />}
        </div>
        <p className="text-xs text-muted-foreground">{documentCoverageScope(run.progress)}</p>
        {run.progress.candidate_policy === "sparse-candidates-v1" && (
          <p className="text-xs text-muted-foreground" role="status">
            入选候选 {run.progress.records_planned} 项 · 已核验 {run.progress.records_examined} 项
            {` · 技术未完成 ${run.progress.records_incomplete} 项 · 待处理 ${run.progress.records_unattempted} 项`}
          </p>
        )}
        <div className="flex flex-wrap gap-2">
          {run.available_actions.includes("pause") && <Button size="sm" variant="outline"
            disabled={model.busy} onClick={() => model.control("pause")}><Pause className="mr-1 size-3" />暂停并保存结果</Button>}
          {run.available_actions.includes("resume") && <Button size="sm" variant="outline"
            disabled={model.busy} onClick={() => model.control("resume")}><Play className="mr-1 size-3" />从断点继续识别</Button>}
          {run.available_actions.includes("cancel") && <Button size="sm" variant="ghost"
            disabled={model.busy} onClick={() => model.control("cancel")}>结束本次运行</Button>}
        </div>
        {run.progress.stop_reason && <p className="text-xs text-muted-foreground">
          {formatDocumentAnalysisReason(run.progress.stop_reason)}</p>}
        {graph?.evidence_repair?.enabled && <div className="text-xs text-muted-foreground" role="status">
          <p>补证任务 {graph.evidence_repair.total} 项 · 已安排重验 {graph.evidence_repair.rechecks} 次</p>
          {Object.entries(graph.evidence_repair.reason_counts).map(([reason, count]) =>
            <p key={reason}>{formatDocumentAnalysisReason(reason)}：{count} 项</p>)}
        </div>}
        {model.running && !root && <p className="text-xs text-muted-foreground" role="status">
          {rankingCalls > 0 ? "正在定位相关原文，尚未完成首项识别。" : "正在解析文档并准备原文索引。"}
        </p>}
        {run.available_actions.includes("ranking_budget_disable") && run.progress.stop_reason === "ranking_paused" && (
          <Button size="sm" variant="outline" disabled={model.busy}
            onClick={() => model.control("ranking_budget_disable")}>取消本次排序预算限制</Button>
        )}
        {run.error && <p className="text-xs text-destructive" role="alert">{run.error.safe_detail}</p>}
        <label className="flex items-center gap-2 text-xs">结果范围
          <select aria-label="图谱结果范围" className="min-w-0 rounded border bg-background p-1"
            value={model.projection} onChange={(event) => model.setProjection(event.target.value as Model["projection"])}>
            <option value="effective_affirmed">有效肯定关系与属性</option>
            <option value="all_candidates">全部候选及待核验结果</option>
            <option value="undetermined">未决结果</option>
            <option value="negated">否定陈述</option>
            <option value="conditional">有条件陈述</option>
          </select>
        </label>
      </>}
      {model.error && <div role="alert" className="text-xs text-destructive">
        {model.error.message}<Button size="sm" variant="ghost" onClick={model.refresh}>重试读取</Button>
      </div>}
      {model.sourceLoading && <p role="status" className="text-xs text-muted-foreground">正在定位原文…</p>}
      {model.sourceError && <p role="alert" className="text-xs text-destructive">原文定位失败：{model.sourceError.message}</p>}
    </div>
    <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-3">
      {graph && <TemplateGraphTree key={JSON.stringify([username, role, run?.recognition_run_id, model.projection])} graph={graph} select={model.select} />}
      {!root && <p className="text-sm text-muted-foreground">{model.loading ? "正在读取运行…"
        : run ? "尚未生成关系图谱，已完成的结果会逐步显示。" : "尚未开始识别。"}</p>}
      {graph && <p className="text-xs text-muted-foreground">未决 {graph.unresolved.undetermined} 项 ·
        {graph.coverage.candidate_policy === "sparse-candidates-v1"
          ? "未形成判定（含未发现候选）" : "未完成核验"} {graph.unresolved.not_checked} 项。系统验证结果尚未经人工确认。</p>}
    </div>
  </section>;
}
