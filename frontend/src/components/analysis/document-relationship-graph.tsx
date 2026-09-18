"use client";

import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  ArrowRight,
  Braces,
  CircleDot,
  ExternalLink,
  FileSearch,
  GitBranch,
  ShieldCheck,
} from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type {
  DocumentAnalysisGraphArtifact,
  DocumentAnalysisStatus,
  DocumentGraphEntity,
  DocumentGraphProjection,
  DocumentGraphProperty,
  DocumentGraphRelationship,
  DocumentGraphRelationshipGroup,
} from "@/lib/api";
import { formatDocumentGraphQuantity } from "@/lib/api";
import {
  formatDocumentAnalysisReason,
  formatDocumentRankingPause,
} from "@/lib/document-analysis";
import { cn } from "@/lib/utils";
import { DocumentGraphCanvas } from "@/components/analysis/document-graph-canvas";
import {
  assertionQualifier, entityRefKey, graphClassLabel, graphEntityLabel, graphPredicateLabel, graphPredicateLabels,
  graphQualifierText, graphScopeStepLabel, GROUP_SELECTION_LABELS, MODALITY_LABELS, type GraphSelection,
} from "@/lib/document-graph";

const PROJECTION_LABELS: Record<DocumentGraphProjection, string> = {
  verified: "已验证关系图谱（含限定）",
  effective_affirmed: "有效肯定图",
  all_candidates: "全部候选",
  unassociated: "未归属实体",
  negated: "否定断言",
  conditional: "条件断言",
  undetermined: "待定候选",
  rejected: "拒绝候选",
};

const POLARITY_LABELS = {
  affirmed: "肯定",
  negated: "否定",
  conditional: "有条件",
  uncertain: "不确定",
} as const;

const REVIEW_LABELS = { unreviewed: "未人工审阅", accepted: "人工接受", rejected: "人工拒绝" } as const;
const IDENTITY_LABELS = {
  document_local: "文档内实体", verified_key: "标识已核实", verified_external: "外部身份已核实", undetermined: "身份待核实",
} as const;

const SOURCE_ROLE_LABELS = {
  selection: "组选择依据",
  subject: "主体",
  object: "对象",
  value: "值",
  unit: "单位依据",
  predicate_bridge: "关系或属性依据",
  condition: "适用条件",
  counterevidence: "反证/竞争者",
} as const;

type SelectedGraphItem =
  | { kind: "entity"; item: DocumentGraphEntity }
  | { kind: "relationship"; item: DocumentGraphRelationship }
  | { kind: "relationship_group"; item: DocumentGraphRelationshipGroup }
  | { kind: "property"; item: DocumentGraphProperty };

function availabilityLabel(
  status: DocumentAnalysisGraphArtifact["availability"],
): string {
  return {
    pending: "等待识别",
    ready: "当前快照完整",
    partial: "部分结果",
    failed: "图谱失败",
  }[status];
}

function errorText(error: DocumentAnalysisGraphArtifact["error"]): string {
  if (!error) return "服务端未返回可用的图谱快照。";
  return error.safe_detail || error.code || "服务端未返回可用的图谱快照。";
}

function DetailRow({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="grid grid-cols-[5.5rem_minmax(0,1fr)] gap-2 text-xs">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="min-w-0 break-words">{value}</dd>
    </div>
  );
}

function SelectionRefs({
  groups,
  selectedSelectionRef,
  onSelectionRef,
}: {
  groups: Array<{ role: string; refs: string[] }>;
  selectedSelectionRef?: string | null;
  onSelectionRef: (selectionRef: string) => void;
}) {
  const populated = groups.filter((group) => group.refs.length > 0);
  if (populated.length === 0) {
    return (
      <p className="text-xs text-muted-foreground">
        当前视图未提供可定位的原文证据。
      </p>
    );
  }
  return (
    <div className="space-y-3">
      {populated.map((group) => (
        <div key={group.role} className="space-y-1.5">
          <p className="text-[11px] font-medium text-muted-foreground">
            {group.role}
          </p>
          {group.refs.map((selectionRef, index) => (
            <button
              key={`${group.role}:${selectionRef}`}
              type="button"
              onClick={() => onSelectionRef(selectionRef)}
              className={cn(
                "flex w-full items-start gap-2 rounded-md border p-2 text-left text-xs transition-colors hover:bg-muted",
                selectedSelectionRef === selectionRef &&
                  "border-primary bg-primary/5",
              )}
            >
              <FileSearch className="mt-0.5 size-3.5 shrink-0 text-primary" />
              <span className="min-w-0 flex-1">
                {group.role} · 原文 {index + 1}
              </span>
              <ExternalLink className="mt-0.5 size-3.5 shrink-0" />
            </button>
          ))}
        </div>
      ))}
    </div>
  );
}

export function DocumentRelationshipGraph({
  artifact,
  projection,
  onProjectionChange,
  onSelectionRef,
  selectedSelectionRef,
  compact = false,
  runStatus,
  rankingBudgetEnabled,
}: {
  artifact: DocumentAnalysisGraphArtifact | null;
  projection: DocumentGraphProjection;
  onProjectionChange: (projection: DocumentGraphProjection) => void;
  onSelectionRef: (selectionRef: string) => void;
  selectedSelectionRef?: string | null;
  compact?: boolean;
  runStatus?: DocumentAnalysisStatus;
  rankingBudgetEnabled?: boolean;
}) {
  const runContinuing = runStatus === "running" || runStatus === "queued";
  const budgetEnabled = rankingBudgetEnabled ?? artifact?.ranking?.budget_enabled ?? true;
  const [selection, setSelection] = useState<GraphSelection | null>(null);
  const detailsRef = useRef<HTMLDivElement>(null);
  useEffect(() => { detailsRef.current?.scrollTo({ top: 0 }); }, [selection]);
  const entitiesByRef = useMemo(
    () =>
      new Map(
        (artifact?.entities ?? []).map((entity) => [entityRefKey(entity), entity]),
      ),
    [artifact],
  );

  const selected: SelectedGraphItem | null = useMemo(() => {
    if (!artifact) return null;
    if (selection?.kind === "entity") {
      const item = artifact.entities.find(
        (entity) => entity.entity_id === selection.id && entity.revision === selection.revision,
      );
      if (item) return { kind: "entity", item };
    }
    if (selection?.kind === "relationship") {
      const item = artifact.relationships.find(
        (edge) => edge.candidate_id === selection.id && edge.revision === selection.revision,
      );
      if (item) return { kind: "relationship", item };
    }
    if (selection?.kind === "relationship_group") {
      const item = artifact.relationship_groups?.find((group) => group.candidate_id === selection.id && group.revision === selection.revision);
      if (item) return { kind: "relationship_group", item };
    }
    if (selection?.kind === "property") {
      const item = artifact.properties.find(
        (property) => property.candidate_id === selection.id && property.revision === selection.revision,
      );
      if (item) return { kind: "property", item };
    }
    const rootRef = artifact.graph_snapshot?.root_ref;
    const root = rootRef
      ? artifact.entities.find(
          (entity) =>
            entity.entity_id === rootRef.entity_id &&
            entity.revision === rootRef.revision,
        )
      : artifact.entities.find(
          (entity) => entity.seed_origin === "user_selected",
        );
    const fallback = root ?? artifact.entities[0];
    return fallback ? { kind: "entity", item: fallback } : null;
  }, [artifact, selection]);

  const sourceGroups = useMemo(() => {
    if (!selected || !artifact) return [];
    if (selected.kind === "entity") {
      return [{ role: "实体提及", refs: selected.item.source_selection_refs }];
    }
    const inherited = artifact?.scope_resolutions?.find((scope) => scope.scope_id === selected.item.scope?.scope_id);
    return [...Object.entries(selected.item.source_selection_refs).map(
      ([role, refs]) => ({
        role:
          SOURCE_ROLE_LABELS[role as keyof typeof SOURCE_ROLE_LABELS] || role,
        refs: refs ?? [],
      }),
    ), ...(inherited?.steps.map((step, index) => ({ role: `继承依据 ${index + 1}：${graphScopeStepLabel(step, artifact)}`,
      refs: step.evidence_selection_ids })) ?? [])];
  }, [selected, artifact]);

  const focusedRef = selected?.kind === "entity" ? selected.item : selected?.item.subject_ref;
  const entityLabel = (ref: { entity_id: string; revision: number }) => graphEntityLabel(entitiesByRef.get(entityRefKey(ref)));
  const predicateLabels = graphPredicateLabels(artifact);
  const predicateLabel = (item: { predicate_iri: string; predicate_label?: string }) => predicateLabels.get(item.predicate_iri) || graphPredicateLabel(item);
  const isFocused = (ref: { entity_id: string; revision: number }) => !focusedRef || entityRefKey(ref) === entityRefKey(focusedRef);
  const relatedEdges = artifact?.relationships.filter((edge) => isFocused(edge.subject_ref) || isFocused(edge.object_ref)) ?? [];
  const relatedGroups = artifact?.relationship_groups?.filter((group) => isFocused(group.subject_ref) || group.object_refs.some(isFocused)) ?? [];
  const relatedProperties = artifact?.properties.filter((property) => isFocused(property.subject_ref)) ?? [];
  const canvasSelection: GraphSelection | null = selected ? {
    kind: selected.kind,
    id: selected.kind === "entity" ? selected.item.entity_id : selected.item.candidate_id,
    revision: selected.item.revision,
  } : null;

  return (
    <section
      className="min-w-0 space-y-4 [overflow-wrap:anywhere]"
      aria-label="关系图谱分析结果"
    >
      <Card className="min-w-0">
        <CardContent
          className={cn(
            "flex min-w-0 flex-col gap-3 p-4",
            !compact && "lg:flex-row lg:items-center lg:justify-between",
          )}
        >
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="outline" aria-label="图谱排序预算限制">排序预算限制：{budgetEnabled ? "已启用" : "已禁用"}</Badge>
            <Badge
              variant={
                artifact?.availability === "failed"
                  ? "destructive"
                  : "secondary"
              }
            >
              {artifact ? availabilityLabel(artifact.availability) : "正在读取"}
            </Badge>
            {artifact && (
              <details className="text-xs text-muted-foreground">
                <summary className="cursor-pointer">快照技术信息</summary>
                <p className="mt-1">运行版本 {artifact.run_revision} · 图谱版本 {artifact.artifact_revision} · 事件序号 {artifact.event_head}</p>
              </details>
            )}
          </div>
          <label className="flex min-w-0 flex-wrap items-center gap-2 text-xs text-muted-foreground">
            图谱视图
            <select
              aria-label="图谱投影"
              value={projection}
              onChange={(event) =>
                onProjectionChange(
                  event.target.value as DocumentGraphProjection,
                )
              }
              className="h-9 min-w-0 max-w-48 rounded-md border border-input bg-background px-3 text-sm text-foreground"
            >
              {Object.entries(PROJECTION_LABELS).filter(([value]) => value !== "verified"
                || artifact?.extraction_protocol === "ontology-tool-extraction-v1").map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          {!budgetEnabled && <p className="text-xs text-muted-foreground">预算统计已暂停（显示启用期间累计值）。排序模型仍可运行，输入长度、超时和单次重试限制保持有效。</p>}
        </CardContent>
      </Card>

      {artifact?.error && artifact.graph_snapshot && (
        <Alert variant="destructive">
          <AlertTitle>关系图谱运行异常，正在显示最后已提交快照</AlertTitle>
          <AlertDescription>{errorText(artifact.error)}</AlertDescription>
        </Alert>
      )}

      {artifact?.ranking?.paused && (
        <Alert variant="warning">
          <AlertTitle>{runContinuing ? "最近提交的排序快照" : "排序已暂停"}</AlertTitle>
          <AlertDescription>
            {runContinuing && <p>运行正在继续，以下为最近已提交快照的说明。</p>}
            <p>{formatDocumentRankingPause(artifact.ranking, runContinuing, budgetEnabled)}</p>
          </AlertDescription>
        </Alert>
      )}

      {artifact?.ranking && (
        <details className="min-w-0 rounded-lg border p-4">
          <summary className="cursor-pointer text-sm font-medium">
            记录处理顺序与检索诊断 · 已提交 {artifact.ranking.committed_epochs} 轮
            {artifact.ranking.paused ? " · 排序已暂停" : artifact.ranking.degraded ? " · 排序已降级" : ""}
          </summary>
          <div className="mt-4 space-y-3 text-xs">
            {!budgetEnabled && <p className="font-medium">预算统计已暂停（显示启用期间累计值）。重新启用后从关闭前的累计量继续。</p>}
            <p className="text-muted-foreground">
              检索排名用于安排处理顺序，原始分数不代表事实正确概率。关系是否成立仍以原文证明和验证结果为准。
            </p>
            <p>
              请求模式：{artifact.ranking.requested_mode}；实际模式：
              {artifact.ranking.actual_modes.join("、") || "尚无已提交排序"}
            </p>
            {artifact.ranking.reasons.length > 0 && (
              <div className="space-y-1">
                <p>降级或未完成原因：{artifact.ranking.reasons.map((reason) => formatDocumentAnalysisReason(reason)).join(" ")}</p>
                <p className="break-all font-mono text-muted-foreground">技术码：{artifact.ranking.reasons.join("、")}</p>
              </div>
            )}
            <p>
              排序请求预留 {artifact.ranking.cost.model_calls} 次 · 已记录请求 {artifact.ranking.cost.observed_requests ?? "—"} 次
              {" "}· 输入对预留 {artifact.ranking.cost.input_pairs}
              {" "}· 预留 tokens {artifact.ranking.cost.reserved_input_tokens ?? artifact.ranking.cost.input_tokens}
              {" "}· 已计量 tokens {artifact.ranking.cost.measured_input_tokens ?? "—"}
              {" "}· 未计量请求 {artifact.ranking.cost.unknown_request_count ?? "—"}
              {" "}· 重试 {artifact.ranking.cost.retries}
              {" "}· 耗时 {artifact.ranking.cost.elapsed_seconds.toFixed(2)} 秒
              {" "}· 排队 {(artifact.ranking.cost.queue_seconds ?? 0).toFixed(2)} 秒
            </p>
            {artifact.ranking.epochs.map((epoch) => (
              <details key={epoch.epoch_id} className="min-w-0 rounded-md border p-3">
                <summary className="cursor-pointer break-all">
                  {epoch.predicate_iri ? predicateLabels.get(epoch.predicate_iri) || graphPredicateLabel(epoch) : "当前属性或关系"}
                  {" · "}{epoch.actual_mode}{" · "}{epoch.records.length} 条记录
                  {epoch.status === "paused" ? " · 未提交，排序暂停" : epoch.degraded ? " · 已降级" : ""}
                  {epoch.budget_accounted === false ? " · 预算未计账" : ""}
                </summary>
                {epoch.budget_accounted === false && <p className="mt-2 text-muted-foreground">本轮排序未预扣或累计预算，不代表没有模型资源消耗。</p>}
                {epoch.subject_ref && <p className="my-2 text-muted-foreground">主体：{entityLabel(epoch.subject_ref)}</p>}
                <details className="my-2 text-muted-foreground">
                  <summary className="cursor-pointer">排序技术标识</summary>
                  <p className="break-all font-mono">{epoch.epoch_id}</p>
                </details>
                {epoch.reason && <p className="mb-2">{formatDocumentAnalysisReason(epoch.reason)}<span className="ml-1 break-all font-mono text-muted-foreground">（{epoch.reason}）</span></p>}
                <div className="max-h-64 max-w-full overflow-auto">
                  <table className="w-full min-w-[32rem] text-left text-xs">
                    <thead className="text-muted-foreground">
                      <tr><th className="p-2">池内名次</th><th className="p-2">记录</th><th className="p-2">通道</th><th className="p-2">意图内名次</th><th className="p-2">原始分数</th></tr>
                    </thead>
                    <tbody>
                      {epoch.records.map((record) => (
                        <tr key={record.record_id} className="border-t">
                          <td className="p-2 tabular-nums">{record.rank}</td>
                          <td className="p-2" title={record.record_id}>记录 {record.rank}</td>
                          <td className="p-2">{record.channels.join("、") || "—"}</td>
                          <td className="p-2">{Object.entries(record.intent_ranks).map(([intent, rank]) => `${intent}: ${rank}`).join("；") || "—"}</td>
                          <td className="p-2">{Object.entries(record.raw_scores).map(([intent, score]) => `${intent}: ${score}`).join("；") || "—"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </details>
            ))}
          </div>
        </details>
      )}

      {!artifact ? (
        <div className="min-h-64 animate-pulse rounded-lg border bg-muted/20" />
      ) : artifact.availability === "failed" && !artifact.graph_snapshot ? (
        <Alert variant="destructive">
          <AlertTitle>关系图谱识别失败</AlertTitle>
          <AlertDescription>{errorText(artifact.error)}</AlertDescription>
        </Alert>
      ) : !artifact.graph_snapshot ? (
        <div className="flex min-h-64 flex-col items-center justify-center gap-3 rounded-lg border border-dashed p-8 text-center">
          <GitBranch className="size-8 text-muted-foreground" />
          <div>
            <p className="text-sm font-medium">暂无关系图谱</p>
            <p className="mt-1 max-w-lg text-xs leading-relaxed text-muted-foreground">
              识别结果生成后将在此显示。
            </p>
          </div>
        </div>
      ) : (
        <>
          <div className="grid min-w-0 gap-4 xl:grid-cols-[minmax(0,1fr)_19rem]">
            <div className="min-w-0 self-start xl:sticky xl:top-0">
              <DocumentGraphCanvas artifact={artifact} selected={canvasSelection} onSelect={(next) => {
                setSelection(next);
                if (window.matchMedia("(max-width: 1279px)").matches) {
                  detailsRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
                }
              }} />
            </div>
            <div ref={detailsRef} aria-label="节点属性与关系" className="flex min-h-0 min-w-0 flex-col gap-4 xl:max-h-[48rem] xl:overflow-y-auto">
              <Card className="min-w-0 shrink-0">
                <CardHeader className="border-b p-4">
                  <CardTitle className="text-sm">
                    关联关系（{relatedEdges.length} 条单边 · {relatedGroups.length} 个组）
                  </CardTitle>
                </CardHeader>
                <CardContent className="max-h-64 space-y-2 overflow-y-auto p-3">
                  {relatedEdges.length === 0 && !relatedGroups.length ? (
                    <p className="p-3 text-center text-xs text-muted-foreground">
                      当前节点在此视图中暂无关联关系。
                    </p>
                  ) : (
                    relatedEdges.map((edge) => {
                      const source =
                        edge.direction === "subject_to_object"
                          ? edge.subject_ref
                          : edge.object_ref;
                      const target =
                        edge.direction === "subject_to_object"
                          ? edge.object_ref
                          : edge.subject_ref;
                      return (
                        <button
                          key={`${edge.candidate_id}@${edge.revision}`}
                          type="button"
                          onClick={() =>
                            setSelection({
                              kind: "relationship",
                              id: edge.candidate_id,
                              revision: edge.revision,
                            })
                          }
                          className={cn(
                            "w-full rounded-lg border p-3 text-left transition-colors hover:bg-muted",
                            selected?.kind === "relationship" &&
                              selected.item.candidate_id ===
                                edge.candidate_id &&
                              "border-primary bg-primary/5",
                          )}
                        >
                          <span className="flex flex-wrap items-center gap-1.5 text-sm">
                            <span className="min-w-0 font-medium">
                              {entityLabel(source)}
                            </span>
                            <ArrowRight className="size-3.5 shrink-0 text-muted-foreground" />
                            <span className="min-w-0 text-primary">
                              {predicateLabel(edge)}
                            </span>
                            <ArrowRight className="size-3.5 shrink-0 text-muted-foreground" />
                            <span className="min-w-0 font-medium">
                              {entityLabel(target)}
                            </span>
                          </span>
                          {assertionQualifier(edge) && <span className="mt-1 block text-xs text-muted-foreground">{assertionQualifier(edge)}</span>}
                          <span className="mt-2 flex flex-wrap gap-1.5">
                            <Badge
                              variant={
                                edge.policy_eligible && !edge.invalidated
                                  ? "default"
                                  : "outline"
                              }
                            >
                              {edge.policy_eligible && !edge.invalidated
                                ? "验证通过"
                                : "未纳入有效图谱"}
                            </Badge>
                            <Badge variant="outline">
                              {POLARITY_LABELS[edge.polarity]}
                            </Badge>
                            <Badge variant="outline">
                              {edge.direction === "subject_to_object"
                                ? "主体→对象"
                                : "对象→主体"}
                            </Badge>
                          </span>
                        </button>
                      );
                    })
                  )}
                  {relatedGroups.map((group) => (
                    <button key={`${group.candidate_id}@${group.revision}`} type="button"
                      onClick={() => setSelection({ kind: "relationship_group", id: group.candidate_id, revision: group.revision })}
                      className="w-full rounded-lg border p-3 text-left text-sm hover:bg-muted">
                      <span className="font-medium">{entityLabel(group.subject_ref)} · {predicateLabel(group)}</span>
                      <span className="mt-1 block text-xs text-muted-foreground">
                        {[GROUP_SELECTION_LABELS[group.selection], assertionQualifier(group)].filter(Boolean).join(" · ")}
                        {"："}{group.object_refs.map(entityLabel).join("、")}
                      </span>
                    </button>
                  ))}
                </CardContent>
              </Card>

              <Card className="min-w-0 shrink-0">
                <CardHeader className="border-b p-4">
                  <CardTitle className="text-sm">
                    属性（{relatedProperties.length}）
                  </CardTitle>
                </CardHeader>
                <CardContent className="max-h-64 space-y-2 overflow-y-auto p-3">
                  {relatedProperties.length === 0 ? (
                    <p className="p-3 text-center text-xs text-muted-foreground">
                      当前节点在此投影中暂无属性候选。
                    </p>
                  ) : (
                    relatedProperties.map((property) => (
                      <button
                        key={`${property.candidate_id}@${property.revision}`}
                        type="button"
                        onClick={() =>
                          setSelection({
                            kind: "property",
                            id: property.candidate_id,
                            revision: property.revision,
                          })
                        }
                        className={cn(
                          "flex w-full items-start justify-between gap-3 rounded-lg border p-3 text-left transition-colors hover:bg-muted",
                          selected?.kind === "property" &&
                            selected.item.candidate_id ===
                              property.candidate_id &&
                            "border-primary bg-primary/5",
                        )}
                      >
                        <span className="min-w-0">
                          <span className="block text-sm font-medium">
                            {predicateLabel(property)}
                          </span>
                          <span className="block truncate text-xs text-muted-foreground">
                            {entityLabel(property.subject_ref)}
                          </span>
                        </span>
                        <span className="max-w-[45%] break-words text-right text-sm">
                          {property.raw_value || "—"}
                          {assertionQualifier(property) && <span className="block text-xs text-muted-foreground">{assertionQualifier(property)}</span>}
                          {formatDocumentGraphQuantity(property) != null &&
                            <span className="block text-xs text-muted-foreground">
                              规范化值：{formatDocumentGraphQuantity(property)}
                            </span>}
                        </span>
                      </button>
                    ))
                  )}
                </CardContent>
              </Card>
              <Card className="order-first min-h-0 min-w-0 shrink-0 overflow-hidden">
                <CardHeader className="border-b p-4">
                  <CardTitle className="text-sm">{selected?.kind === "entity" ? "节点详情与证据" : selected?.kind === "property" ? "属性详情与证据" : "关系详情与证据"}</CardTitle>
                </CardHeader>
                <CardContent className="min-w-0 space-y-5 p-4">
                  {!selected ? (
                    <p className="text-sm text-muted-foreground">
                      选择实体、关系或属性查看详情。
                    </p>
                  ) : (
                    <>
                      <section className="space-y-2">
                        <div className="flex min-w-0 items-start gap-2">
                          {selected.kind === "entity" ? (
                            <CircleDot className="size-4 shrink-0" />
                          ) : selected.kind === "relationship" ? (
                            <GitBranch className="size-4 shrink-0" />
                          ) : (
                            <Braces className="size-4 shrink-0" />
                          )}
                          <h3 className="min-w-0 break-words text-sm font-semibold">
                            {selected.kind === "entity"
                              ? graphEntityLabel(selected.item)
                              : predicateLabel(selected.item)}
                          </h3>
                        </div>
                        <dl className="space-y-2">
                          {selected.kind === "entity" ? (
                            <>
                              <DetailRow
                                label="本体类型"
                                value={graphClassLabel(selected.item)}
                              />
                              <DetailRow
                                label="身份状态"
                                value={IDENTITY_LABELS[selected.item.identity_state]}
                              />
                              {!!selected.item.external_provenance?.length && <DetailRow
                                label="外部身份来源"
                                value={<div>{selected.item.external_provenance.map((source, index) =>
                                  <p key={`${source.system}:${source.dataset}:${source.record_key}:${index}`}>
                                    {source.system} / {source.dataset}
                                  </p>)}<p className="text-xs text-muted-foreground">外部记录用于身份核验，文档事实仍以原文为依据。</p></div>}
                              />}
                              <DetailRow
                                label="节点来源"
                                value={
                                  selected.item.seed_origin === "user_selected"
                                    ? "用户指定（不计识别成功）"
                                    : "系统识别"
                                }
                              />
                              <DetailRow
                                label="独立审阅"
                                value={REVIEW_LABELS[selected.item.independent_review]}
                              />
                              <DetailRow
                                label="直接结果"
                                value={`${relatedEdges.length} 条关系 · ${relatedGroups.length} 个关系组 · ${relatedProperties.length} 项属性`}
                              />
                            </>
                          ) : (
                            <>
                              <DetailRow
                                label={selected.kind === "property" ? "属性名称" : "关系名称"}
                                value={predicateLabel(selected.item)}
                              />
                              <DetailRow
                                label="主体"
                                value={entityLabel(selected.item.subject_ref)}
                              />
                              {selected.kind === "relationship_group" && <DetailRow label="关系方向" value={selected.item.direction === "subject_to_object" ? "主体 → 对象" : "对象 → 主体"} />}
                              {selected.kind === "relationship" ? (
                                <>
                                  <DetailRow
                                    label="对象"
                                    value={entityLabel(selected.item.object_ref)}
                                  />
                                  <DetailRow
                                    label="关系方向"
                                    value={
                                      selected.item.direction ===
                                      "subject_to_object"
                                        ? "主体 → 对象"
                                        : "对象 → 主体"
                                    }
                                  />
                                </>
                              ) : selected.kind === "relationship_group" ? (
                                <>
                                  <DetailRow label="组选择" value={GROUP_SELECTION_LABELS[selected.item.selection]} />
                                  <DetailRow label="组成员" value={selected.item.object_refs.map(entityLabel).join("、")} />
                                </>
                              ) : (
                                <>
                                <DetailRow
                                  label="原始值"
                                  value={selected.item.raw_value || "—"}
                                />
                                {formatDocumentGraphQuantity(selected.item) != null &&
                                  <DetailRow label="规范化值"
                                    value={formatDocumentGraphQuantity(selected.item)} />}
                                </>
                              )}
                              <DetailRow
                                label="断言极性"
                                value={POLARITY_LABELS[selected.item.polarity]}
                              />
                              {selected.item.modality && <DetailRow label="陈述模态" value={MODALITY_LABELS[selected.item.modality]} />}
                              {!!selected.item.scope?.members.length && <DetailRow label="继承范围" value={
                                artifact.scope_resolutions?.find((scope) => scope.scope_id === selected.item.scope?.scope_id)?.steps.map((step) =>
                                  `${graphScopeStepLabel(step, artifact)}（${step.selection ? GROUP_SELECTION_LABELS[step.selection] : "单对象"} · ${POLARITY_LABELS[step.polarity]} · ${MODALITY_LABELS[step.modality]}）${[...step.conditions, ...step.applicability.map((value) => value.text)].join("；")}`).join("；") ?? "范围依赖未解析，不能视为无限定事实"
                              } />}
                              <DetailRow
                                label="验证状态"
                                value={`${selected.item.structural_valid ? "结构校验通过" : "结构校验未通过"} · ${selected.item.model_supported ? "模型核验支持" : "模型核验未支持"} · ${selected.item.policy_eligible && !selected.item.invalidated ? "可纳入有效图谱" : "未纳入有效图谱"}`}
                              />
                              <DetailRow
                                label="独立审阅"
                                value={REVIEW_LABELS[selected.item.independent_review]}
                              />
                              <DetailRow
                                label="适用条件"
                                value={
                                  selected.item.conditions.length > 0
                                    ? graphQualifierText(selected.item.conditions)
                                    : "—"
                                }
                              />
                              <DetailRow
                                label="适用范围"
                                value={
                                  Object.keys(selected.item.applicability)
                                    .length > 0
                                    ? graphQualifierText(selected.item.applicability)
                                    : "—"
                                }
                              />
                              <DetailRow
                                label="判定原因"
                                value={
                                  selected.item.reason ||
                                  (selected.item.reason_code ? formatDocumentAnalysisReason(selected.item.reason_code) : null) ||
                                  "—"
                                }
                              />
                            </>
                          )}
                        </dl>
                      </section>
                      <section className="space-y-2">
                        <h3 className="flex items-center gap-2 text-sm font-medium">
                          <ShieldCheck className="size-4" />
                          原文证据
                        </h3>
                        <SelectionRefs
                          groups={sourceGroups}
                          selectedSelectionRef={selectedSelectionRef}
                          onSelectionRef={onSelectionRef}
                        />
                      </section>
                      <details key={`${selected.kind}:${selected.kind === "entity" ? selected.item.entity_id : selected.item.candidate_id}:${selected.item.revision}`} className="rounded-md border p-3 text-xs text-muted-foreground">
                        <summary className="cursor-pointer">技术详情（标识与版本）</summary>
                        <dl className="mt-3 space-y-2">
                          <DetailRow label="内部标识" value={selected.kind === "entity" ? selected.item.entity_id : selected.item.candidate_id} />
                          <DetailRow label="版本" value={selected.item.revision} />
                          <DetailRow label="本体 IRI" value={selected.kind === "entity" ? selected.item.class_iri : selected.item.predicate_iri} />
                          {selected.kind === "entity" ? (
                            !!selected.item.external_provenance?.length && <DetailRow label="外部记录" value={selected.item.external_provenance.map((source) => `${source.system} / ${source.dataset} · ${source.record_key}@${source.record_version}`).join("；")} />
                          ) : <>
                            <DetailRow label="主体版本" value={`${selected.item.subject_ref.entity_id}@${selected.item.subject_ref.revision}`} />
                            {selected.kind === "relationship" && <DetailRow label="对象版本" value={`${selected.item.object_ref.entity_id}@${selected.item.object_ref.revision}`} />}
                            {selected.kind === "relationship_group" && <DetailRow label="成员版本" value={selected.item.object_refs.map((ref) => `${ref.entity_id}@${ref.revision}`).join("、")} />}
                            <DetailRow label="证明版本" value={selected.item.proof_ref ? `${selected.item.proof_ref.id}@${selected.item.proof_ref.revision}` : "—"} />
                            <DetailRow label="判定版本" value={selected.item.decision_refs.map((ref) => `${ref.id}@${ref.revision}`).join("、") || "—"} />
                            <DetailRow label="依赖版本" value={selected.item.dependency_refs.map((ref) => `${ref.id}@${ref.revision}`).join("、") || "—"} />
                            <DetailRow label="范围引用" value={selected.item.scope?.members.map((step) => `${step.relation_ref.id}@${step.relation_ref.revision} → ${step.member_ref.entity_id}@${step.member_ref.revision}`).join("；") || "—"} />
                            <DetailRow label="条件数据" value={JSON.stringify(selected.item.conditions)} />
                            <DetailRow label="范围数据" value={JSON.stringify(selected.item.applicability)} />
                            <DetailRow label="判定代码" value={selected.item.reason_code || "—"} />
                          </>}
                          <DetailRow label="证据引用" value={sourceGroups.flatMap((group) => group.refs).join("、") || "—"} />
                        </dl>
                      </details>
                    </>
                  )}
                </CardContent>
              </Card>
            </div>
          </div>

        </>
      )}
    </section>
  );
}
