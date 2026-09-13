"use client";

import { useMemo, useState, type ReactNode } from "react";
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
} from "@/lib/api";
import {
  documentCoverageLabel,
  documentCoverageScope,
  documentRetrievalSummary,
  formatDocumentAnalysisReason,
  formatDocumentRankingPause,
} from "@/lib/document-analysis";
import { cn } from "@/lib/utils";

const PROJECTION_LABELS: Record<DocumentGraphProjection, string> = {
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

const SOURCE_ROLE_LABELS = {
  subject: "主体",
  object: "对象",
  value: "值",
  unit: "单位依据",
  predicate_bridge: "谓词桥接",
  condition: "适用条件",
  counterevidence: "反证/竞争者",
} as const;

type GraphSelection =
  | { kind: "entity"; id: string }
  | { kind: "relationship"; id: string }
  | { kind: "property"; id: string };
type SelectedGraphItem =
  | { kind: "entity"; item: DocumentGraphEntity }
  | { kind: "relationship"; item: DocumentGraphRelationship }
  | { kind: "property"; item: DocumentGraphProperty };

function shortIri(iri: string): string {
  return (
    iri
      .split("#")
      .flatMap((part) => part.split("/"))
      .filter(Boolean)
      .at(-1) || iri
  );
}

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
        当前投影未提供可回放的原文证据。
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
          {group.refs.map((selectionRef) => (
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
              <span className="min-w-0 flex-1 truncate font-mono">
                {selectionRef}
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
  const entitiesById = useMemo(
    () =>
      new Map(
        (artifact?.entities ?? []).map((entity) => [entity.entity_id, entity]),
      ),
    [artifact],
  );

  const selected: SelectedGraphItem | null = useMemo(() => {
    if (!artifact) return null;
    if (selection?.kind === "entity") {
      const item = artifact.entities.find(
        (entity) => entity.entity_id === selection.id,
      );
      if (item) return { kind: "entity", item };
    }
    if (selection?.kind === "relationship") {
      const item = artifact.relationships.find(
        (edge) => edge.candidate_id === selection.id,
      );
      if (item) return { kind: "relationship", item };
    }
    if (selection?.kind === "property") {
      const item = artifact.properties.find(
        (property) => property.candidate_id === selection.id,
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
    return root ? { kind: "entity", item: root } : null;
  }, [artifact, selection]);

  const sourceGroups = useMemo(() => {
    if (!selected) return [];
    if (selected.kind === "entity") {
      return [{ role: "实体提及", refs: selected.item.source_selection_refs }];
    }
    return Object.entries(selected.item.source_selection_refs).map(
      ([role, refs]) => ({
        role:
          SOURCE_ROLE_LABELS[role as keyof typeof SOURCE_ROLE_LABELS] || role,
        refs,
      }),
    );
  }, [selected]);

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
              <span className="text-xs text-muted-foreground">
                run revision {artifact.run_revision} · artifact{" "}
                {artifact.artifact_revision} · event {artifact.event_head}
              </span>
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
              {Object.entries(PROJECTION_LABELS).map(([value, label]) => (
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
                  {epoch.predicate_iri ? shortIri(epoch.predicate_iri) : "当前槽位"}
                  {" · "}{epoch.actual_mode}{" · "}{epoch.records.length} 条记录
                  {epoch.status === "paused" ? " · 未提交，排序暂停" : epoch.degraded ? " · 已降级" : ""}
                  {epoch.budget_accounted === false ? " · 预算未计账" : ""}
                </summary>
                {epoch.budget_accounted === false && <p className="mt-2 text-muted-foreground">本轮排序未预扣或累计预算，不代表没有模型资源消耗。</p>}
                <p className="my-2 break-all font-mono text-muted-foreground">
                  {epoch.subject_ref ? `${epoch.subject_ref.entity_id}@${epoch.subject_ref.revision} · ` : ""}
                  {epoch.epoch_id}
                </p>
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
                          <td className="break-all p-2 font-mono">{record.record_id}</td>
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
            <p className="text-sm font-medium">关系图谱尚未形成可读快照</p>
            <p className="mt-1 max-w-lg text-xs leading-relaxed text-muted-foreground">
              页面会只读刷新已提交的完整水位。空快照不代表全文没有关系，也不会触发额外识别。
            </p>
          </div>
        </div>
      ) : (
        <>
          <div
            className={cn(
              "grid min-w-0 gap-3",
              compact ? "grid-cols-2" : "sm:grid-cols-2 xl:grid-cols-4",
            )}
          >
            {[
              [`计划${documentCoverageLabel(artifact.coverage)}`, artifact.coverage.records_planned],
              [`${documentCoverageLabel(artifact.coverage)}已检`, artifact.coverage.records_examined],
              ["技术未完成", artifact.coverage.records_incomplete],
              ["待展开前沿", artifact.coverage.pending_frontiers],
            ].map(([label, value]) => (
              <Card key={label} className="min-w-0">
                <CardContent className="p-3">
                  <p className="text-xs text-muted-foreground">{label}</p>
                  <p className="mt-1 text-xl font-semibold tabular-nums">
                    {value}
                  </p>
                </CardContent>
              </Card>
            ))}
          </div>

          <div
            className={cn(
              "grid min-w-0 gap-4",
              compact
                ? "grid-cols-1"
                : "min-h-[34rem] xl:grid-cols-[minmax(16rem,0.85fr)_minmax(22rem,1.35fr)_minmax(18rem,0.9fr)]",
            )}
          >
            <Card className="min-h-0 min-w-0 overflow-hidden">
              <CardHeader className="border-b p-4">
                <CardTitle className="text-sm">
                  实体（{artifact.entities.length}）
                </CardTitle>
              </CardHeader>
              <CardContent
                className={cn(
                  "min-w-0 space-y-2 overflow-y-auto p-3",
                  compact ? "max-h-64" : "h-[30rem] xl:h-full",
                )}
              >
                {artifact.entities.map((entity) => (
                  <button
                    key={`${entity.entity_id}@${entity.revision}`}
                    type="button"
                    onClick={() =>
                      setSelection({ kind: "entity", id: entity.entity_id })
                    }
                    className={cn(
                      "w-full rounded-lg border p-3 text-left transition-colors hover:bg-muted",
                      selected?.kind === "entity" &&
                        selected.item.entity_id === entity.entity_id &&
                        "border-primary bg-primary/5",
                    )}
                  >
                    <span className="flex items-start gap-2">
                      <CircleDot className="mt-0.5 size-4 shrink-0 text-primary" />
                      <span className="min-w-0 flex-1">
                        <span className="flex flex-wrap items-center gap-1.5">
                          <span className="break-words text-sm font-medium">
                            {entity.label}
                          </span>
                          {entity.seed_origin === "user_selected" && (
                            <Badge variant="outline">用户指定根</Badge>
                          )}
                        </span>
                        <span className="mt-1 block truncate text-xs text-muted-foreground">
                          {entity.class_label || shortIri(entity.class_iri)}
                        </span>
                      </span>
                    </span>
                  </button>
                ))}
              </CardContent>
            </Card>

            <div className="min-h-0 min-w-0 space-y-4">
              <Card className="min-w-0">
                <CardHeader className="border-b p-4">
                  <CardTitle className="text-sm">
                    关系（{artifact.relationships.length}）
                  </CardTitle>
                </CardHeader>
                <CardContent className="max-h-64 space-y-2 overflow-y-auto p-3">
                  {artifact.relationships.length === 0 ? (
                    <p className="p-3 text-center text-xs text-muted-foreground">
                      当前投影尚未识别到有效关系；请结合运行状态和覆盖判断。
                    </p>
                  ) : (
                    artifact.relationships.map((edge) => {
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
                              {entitiesById.get(source.entity_id)?.label ||
                                source.entity_id}
                            </span>
                            <ArrowRight className="size-3.5 shrink-0 text-muted-foreground" />
                            <span className="min-w-0 text-primary">
                              {edge.predicate_label}
                            </span>
                            <ArrowRight className="size-3.5 shrink-0 text-muted-foreground" />
                            <span className="min-w-0 font-medium">
                              {entitiesById.get(target.entity_id)?.label ||
                                target.entity_id}
                            </span>
                          </span>
                          <span className="mt-2 flex flex-wrap gap-1.5">
                            <Badge
                              variant={
                                edge.policy_eligible && !edge.invalidated
                                  ? "default"
                                  : "outline"
                              }
                            >
                              {edge.policy_eligible && !edge.invalidated
                                ? "证明门通过"
                                : "未进入有效投影"}
                            </Badge>
                            <Badge variant="outline">
                              {POLARITY_LABELS[edge.polarity]}
                            </Badge>
                            <Badge variant="outline">
                              {edge.direction === "subject_to_object"
                                ? "主体→对象"
                                : "对象→主体"}
                            </Badge>
                            <span className="min-w-0 font-mono text-[10px] text-muted-foreground">
                              {edge.candidate_id}@{edge.revision}
                            </span>
                          </span>
                        </button>
                      );
                    })
                  )}
                </CardContent>
              </Card>

              <Card className="min-w-0">
                <CardHeader className="border-b p-4">
                  <CardTitle className="text-sm">
                    属性（{artifact.properties.length}）
                  </CardTitle>
                </CardHeader>
                <CardContent className="max-h-64 space-y-2 overflow-y-auto p-3">
                  {artifact.properties.length === 0 ? (
                    <p className="p-3 text-center text-xs text-muted-foreground">
                      当前投影暂无属性候选。
                    </p>
                  ) : (
                    artifact.properties.map((property) => (
                      <button
                        key={`${property.candidate_id}@${property.revision}`}
                        type="button"
                        onClick={() =>
                          setSelection({
                            kind: "property",
                            id: property.candidate_id,
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
                            {property.predicate_label}
                          </span>
                          <span className="block truncate text-xs text-muted-foreground">
                            {entitiesById.get(property.subject_ref.entity_id)
                              ?.label || property.subject_ref.entity_id}
                          </span>
                        </span>
                        <span className="max-w-[45%] break-words text-right text-sm">
                          {property.raw_value || "—"}
                          {property.unit && property.normalized_value != null &&
                            <span className="block text-xs text-muted-foreground">
                              规范化值：{String(property.normalized_value)} {property.unit}
                            </span>}
                        </span>
                      </button>
                    ))
                  )}
                </CardContent>
              </Card>
            </div>

            <Card className="min-h-0 min-w-0 overflow-hidden">
              <CardHeader className="border-b p-4">
                <CardTitle className="text-sm">候选详情与证据</CardTitle>
              </CardHeader>
              <CardContent
                className={cn(
                  "min-w-0 space-y-5 overflow-y-auto p-4",
                  !compact && "h-[30rem] xl:h-full",
                )}
              >
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
                            ? selected.item.label
                            : selected.item.predicate_label}
                        </h3>
                      </div>
                      <dl className="space-y-2">
                        {selected.kind === "entity" ? (
                          <>
                            <DetailRow
                              label="实体版本"
                              value={`${selected.item.entity_id}@${selected.item.revision}`}
                            />
                            <DetailRow
                              label="本体类型"
                              value={
                                <span title={selected.item.class_iri}>
                                  {selected.item.class_label ||
                                    selected.item.class_iri}
                                </span>
                              }
                            />
                            <DetailRow
                              label="身份状态"
                              value={selected.item.identity_state}
                            />
                            <DetailRow
                              label="根节点来源"
                              value={
                                selected.item.seed_origin === "user_selected"
                                  ? "用户指定（不计识别成功）"
                                  : "系统识别"
                              }
                            />
                            <DetailRow
                              label="独立审阅"
                              value={selected.item.independent_review}
                            />
                            <DetailRow
                              label="直接结果"
                              value={`${artifact.relationships.filter((item) => item.subject_ref.entity_id === selected.item.entity_id || item.object_ref.entity_id === selected.item.entity_id).length} 条关系 · ${artifact.properties.filter((item) => item.subject_ref.entity_id === selected.item.entity_id).length} 项属性`}
                            />
                          </>
                        ) : (
                          <>
                            <DetailRow
                              label="候选版本"
                              value={`${selected.item.candidate_id}@${selected.item.revision}`}
                            />
                            <DetailRow
                              label="谓词 IRI"
                              value={
                                <span className="font-mono">
                                  {selected.item.predicate_iri}
                                </span>
                              }
                            />
                            <DetailRow
                              label="主体版本"
                              value={`${selected.item.subject_ref.entity_id}@${selected.item.subject_ref.revision}`}
                            />
                            {selected.kind === "relationship" ? (
                              <>
                                <DetailRow
                                  label="对象版本"
                                  value={`${selected.item.object_ref.entity_id}@${selected.item.object_ref.revision}`}
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
                            ) : (
                              <>
                              <DetailRow
                                label="原始值"
                                value={selected.item.raw_value || "—"}
                              />
                              {selected.item.unit && selected.item.normalized_value != null &&
                                <DetailRow label="规范化值"
                                  value={`${String(selected.item.normalized_value)} ${selected.item.unit}`} />}
                              </>
                            )}
                            <DetailRow
                              label="断言极性"
                              value={POLARITY_LABELS[selected.item.polarity]}
                            />
                            <DetailRow
                              label="证明版本"
                              value={
                                selected.item.proof_ref
                                  ? `${selected.item.proof_ref.id}@${selected.item.proof_ref.revision}`
                                  : "—"
                              }
                            />
                            <DetailRow
                              label="判定版本"
                              value={
                                selected.item.decision_refs.length > 0
                                  ? selected.item.decision_refs
                                      .map((ref) => `${ref.id}@${ref.revision}`)
                                      .join("、")
                                  : "—"
                              }
                            />
                            <DetailRow
                              label="依赖版本"
                              value={
                                selected.item.dependency_refs.length > 0
                                  ? selected.item.dependency_refs
                                      .map((ref) => `${ref.id}@${ref.revision}`)
                                      .join("、")
                                  : "—"
                              }
                            />
                            <DetailRow
                              label="四层门禁"
                              value={`${selected.item.structural_valid ? "结构有效" : "结构无效"} · ${selected.item.model_supported ? "模型支持" : "模型未支持"} · ${selected.item.policy_eligible ? "投影合格" : "投影不合格"} · ${selected.item.independent_review}`}
                            />
                            <DetailRow
                              label="适用条件"
                              value={
                                selected.item.conditions.length > 0
                                  ? JSON.stringify(selected.item.conditions)
                                  : "—"
                              }
                            />
                            <DetailRow
                              label="适用范围"
                              value={
                                Object.keys(selected.item.applicability)
                                  .length > 0
                                  ? JSON.stringify(selected.item.applicability)
                                  : "—"
                              }
                            />
                            <DetailRow
                              label="判定原因"
                              value={
                                selected.item.reason ||
                                selected.item.reason_code ||
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
                  </>
                )}
              </CardContent>
            </Card>
          </div>

          <Card className="min-w-0">
            <CardHeader className="border-b p-4">
              <CardTitle className="text-sm">覆盖与未完成范围</CardTitle>
            </CardHeader>
            <CardContent className="min-w-0 space-y-4 p-4">
              <div
                className={cn(
                  "grid min-w-0 gap-2 text-xs",
                  compact ? "grid-cols-2" : "sm:grid-cols-3 xl:grid-cols-6",
                )}
              >
                {[
                  [`计划${documentCoverageLabel(artifact.coverage)}`, artifact.coverage.records_planned],
                  ["已检查", artifact.coverage.records_examined],
                  ["技术未完成", artifact.coverage.records_incomplete],
                  ["未尝试", artifact.coverage.records_unattempted],
                  ["语义待定", artifact.unresolved.undetermined],
                  ["不支持", artifact.unresolved.unsupported],
                ].map(([label, value]) => (
                  <div key={label} className="min-w-0 rounded-md bg-muted/50 p-2">
                    <span className="text-muted-foreground">{label}</span>
                    <strong className="ml-2 tabular-nums">{value}</strong>
                  </div>
                ))}
              </div>
              <p className="text-xs text-muted-foreground">{documentCoverageScope(artifact.coverage)}</p>
              <p className="text-xs text-muted-foreground">
                {documentRetrievalSummary(artifact.coverage)}
              </p>
              {artifact.coverage.subjects.length === 0 ? (
                <p className="text-xs text-muted-foreground">
                  尚无已提交的主体—谓词覆盖条目；请结合当前阶段判断，不能据此断言全文无关系。
                </p>
              ) : (
                <div className="max-w-full overflow-x-auto rounded-md border">
                  <table className="w-full min-w-[48rem] text-left text-xs">
                    <thead className="bg-muted/60 text-muted-foreground">
                      <tr>
                        {[
                          "主体",
                          "谓词",
                          "第一阶段计划",
                          "第一阶段实际执行",
                          "第二阶段计划",
                          "第二阶段实际执行",
                          "已检",
                          "未完成",
                          "未尝试",
                          "待展开前沿",
                        ].map((label) => (
                          <th key={label} className="p-2 font-medium">
                            {label}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {artifact.coverage.subjects.map((item) => (
                        <tr
                          key={`${item.subject_ref.entity_id}@${item.subject_ref.revision}:${item.predicate_iri}`}
                          className="border-t"
                        >
                          <td className="p-2 font-mono">
                            {item.subject_ref.entity_id}@
                            {item.subject_ref.revision}
                          </td>
                          <td className="p-2" title={item.predicate_iri}>
                            {item.predicate_label}
                          </td>
                          {[
                            item.phase_counts.phase1,
                            item.executed_phase_counts?.phase1 ?? "—",
                            item.phase_counts.phase2,
                            item.executed_phase_counts?.phase2 ?? "—",
                            item.records_examined,
                            item.records_incomplete,
                            item.records_unattempted,
                            item.pending_frontiers,
                          ].map((value, index) => (
                            <td key={index} className="p-2 tabular-nums">
                              {value}
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              <p className="text-xs text-muted-foreground">
                实际执行包含已启动但技术未完成的{documentCoverageLabel(artifact.coverage)}。计划中的待处理项仍需执行；旧快照未记录的实际阶段数显示为“—”。
              </p>
              {artifact.coverage.stop_reason && (
                <Alert>
                  <AlertTitle>{runContinuing ? "当前覆盖说明"
                    : artifact.coverage.stop_reason === "candidate_search_exhausted"
                      ? "本轮识别完成说明" : "覆盖未完成说明"}</AlertTitle>
                  <AlertDescription>
                    {runContinuing && <p>运行仍在继续，以下说明来自最近已提交的覆盖快照。</p>}
                    <p>{artifact.coverage.stop_reason === "ranking_paused"
                      ? formatDocumentRankingPause(artifact.ranking, runContinuing, budgetEnabled)
                      : formatDocumentAnalysisReason(artifact.coverage.stop_reason)}</p>
                    <details className="mt-2 text-xs">
                      <summary className="cursor-pointer">技术诊断</summary>
                      <p className="mt-1 break-all font-mono">{artifact.coverage.stop_reason}</p>
                    </details>
                  </AlertDescription>
                </Alert>
              )}
            </CardContent>
          </Card>
        </>
      )}
    </section>
  );
}
