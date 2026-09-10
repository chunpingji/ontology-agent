"use client";

import { ExternalLink, Loader2, Pause, Play, RefreshCw } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  type DocumentAnalysisGraphArtifact, type DocumentGraphAssertionBase,
  type DocumentGraphCoverageSubject, type DocumentGraphEntity,
} from "@/lib/api";
import { DOCUMENT_ANALYSIS_STATUS_LABELS, formatDocumentAnalysisReason } from "@/lib/document-analysis";
import { useTemplateDocumentRun } from "./use-template-document-run";

type Model = ReturnType<typeof useTemplateDocumentRun>;

export function branchProgress(branch?: DocumentGraphCoverageSubject): string {
  if (!branch || branch.records_examined + branch.records_incomplete === 0) return "未尝试";
  const parts = [`已检查 ${branch.records_examined}/${branch.records_planned} 条原文`];
  if (branch.records_incomplete) parts.push(`${branch.records_incomplete} 条处理未完成`);
  if (branch.records_unattempted) parts.push(`${branch.records_unattempted} 条待检查`);
  return parts.join(" · ");
}

function SourceButton({ refs, label, select }: { refs: string[]; label: string; select: Model["select"] }) {
  if (!refs.length) return null;
  return <span className="inline-flex flex-wrap gap-1">
    {refs.map((ref, index) => <button key={ref} type="button"
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
      <SourceButton refs={item.source_selection_refs.condition} label="条件" select={select} />
      <SourceButton refs={item.source_selection_refs.counterevidence} label="反证" select={select} />
    </div>
    {!effective && item.reason && <p className="text-xs text-muted-foreground">{item.reason}</p>}
  </div>;
}

function EntityBranch({ entity, graph, select, ancestors = [] }: {
  entity: DocumentGraphEntity; graph: DocumentAnalysisGraphArtifact;
  select: Model["select"]; ancestors?: string[];
}) {
  const menu = entity.predicate_menu;
  const coverage = graph.coverage.subjects.filter((item) => item.subject_ref.entity_id === entity.entity_id);
  const edges = graph.relationships.filter((item) => item.subject_ref.entity_id === entity.entity_id);
  const properties = graph.properties.filter((item) => item.subject_ref.entity_id === entity.entity_id);
  const relationships = menu?.filter((item) => item.kind === "relationship")
    ?? [...new Map(edges.map((item) => [item.predicate_iri, item])).values()];
  const fields = menu?.filter((item) => item.kind === "property")
    ?? [...new Map(properties.map((item) => [item.predicate_iri, item])).values()];
  const renderField = (field: typeof fields[number]) => {
            const values = properties.filter((item) => item.predicate_iri === field.predicate_iri);
            return <div key={field.predicate_iri} className="border-l-2 pl-2" data-predicate={field.predicate_iri}>
              <p className="text-xs font-medium">{field.predicate_label}</p>
              <p className="text-xs text-muted-foreground">{branchProgress(coverage.find((item) => item.predicate_iri === field.predicate_iri))}</p>
              {values.map((item) => <div key={item.candidate_id} className="mt-1 space-y-1">
                <p className="break-words text-sm">{item.raw_value}</p>
                <SourceButton refs={item.source_selection_refs.value} label="属性值" select={select} />
                <AssertionProof item={item} select={select} />
              </div>)}
              {!values.length && <p className="text-xs text-muted-foreground">尚无有效属性值</p>}
            </div>;
  };
  const cycle = ancestors.includes(entity.entity_id);
  return <details open={ancestors.length <= 1} className="min-w-0 rounded border bg-background p-2"
    data-entity-id={entity.entity_id}>
    <summary className="cursor-pointer break-words text-sm font-medium">
      {entity.label} <span className="font-normal text-muted-foreground">{entity.class_label}</span>
    </summary>
    <div className="mt-2 space-y-3">
      <SourceButton refs={entity.source_selection_refs} label="实体名称" select={select} />
      {cycle ? <p className="text-xs text-muted-foreground">该实体已在上级路径展示。</p> : <>
        <div className="space-y-2">
          <p className="text-xs font-medium">属性{menu ? `（${fields.length}）` : ""}</p>
          {menu && !fields.length && <p className="text-xs text-muted-foreground">不适用：本体未声明数据属性</p>}
          {fields.filter((field) => properties.some((item) => item.predicate_iri === field.predicate_iri)).map(renderField)}
          {fields.some((field) => !properties.some((item) => item.predicate_iri === field.predicate_iri)) && (
            <details>
              <summary className="cursor-pointer text-xs text-muted-foreground">待检查及暂无结果的属性</summary>
              <div className="mt-2 space-y-2">
                {fields.filter((field) => !properties.some((item) => item.predicate_iri === field.predicate_iri)).map(renderField)}
              </div>
            </details>
          )}
        </div>
        {relationships.map((relation) => {
          const matches = edges.filter((item) => item.predicate_iri === relation.predicate_iri);
          return <details key={relation.predicate_iri} open={matches.length > 0}
            className="border-l-2 pl-2" data-predicate={relation.predicate_iri}>
            <summary className="cursor-pointer text-sm">{relation.predicate_label}
              <span className="ml-2 text-xs text-muted-foreground">{matches.length ? `${matches.length} 项` : "尚无有效关系"}</span>
              <span className="block text-xs text-muted-foreground">{branchProgress(coverage.find((item) => item.predicate_iri === relation.predicate_iri))}</span>
            </summary>
            {matches.map((edge) => {
              const target = graph.entities.find((item) => item.entity_id === edge.object_ref.entity_id);
              return <div key={edge.candidate_id} className="mt-2 space-y-2">
                <AssertionProof item={edge} select={select} />
                {target && <EntityBranch entity={target} graph={graph} select={select}
                  ancestors={[...ancestors, entity.entity_id]} />}
              </div>;
            })}
          </details>;
        })}
      </>}
    </div>
  </details>;
}

export function TemplateDocumentGraphPanel({ model }: { model: Model }) {
  const { run, graph } = model;
  const root = graph?.entities.find((item) => item.entity_id === graph.graph_snapshot?.root_ref.entity_id);
  const linked = new Set(graph?.relationships.map((item) => item.object_ref.entity_id));
  const unassociated = graph?.entities.filter((item) => item !== root && !linked.has(item.entity_id)) ?? [];
  const rankingCalls = graph?.ranking?.cost.model_calls ?? 0;
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
      {root && graph && <EntityBranch entity={root} graph={graph} select={model.select} />}
      {!root && <p className="text-sm text-muted-foreground">{model.loading ? "正在读取运行…"
        : run ? "尚未生成关系图谱，已完成的结果会逐步显示。" : "尚未开始识别。"}</p>}
      {unassociated.length > 0 && graph && <details><summary className="text-xs">未关联实体（{unassociated.length}）</summary>
        {unassociated.map((entity) => <EntityBranch key={entity.entity_id} entity={entity} graph={graph} select={model.select} />)}
      </details>}
      {graph && <p className="text-xs text-muted-foreground">未决 {graph.unresolved.undetermined} 项 ·
        未完成核验 {graph.unresolved.not_checked} 项。系统验证结果尚未经人工确认。</p>}
    </div>
  </section>;
}
