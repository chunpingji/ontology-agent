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
  DocumentGraphEntity,
  DocumentGraphProjection,
  DocumentGraphProperty,
  DocumentGraphRelationship,
} from "@/lib/api";
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
}: {
  artifact: DocumentAnalysisGraphArtifact | null;
  projection: DocumentGraphProjection;
  onProjectionChange: (projection: DocumentGraphProjection) => void;
  onSelectionRef: (selectionRef: string) => void;
  selectedSelectionRef?: string | null;
}) {
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
    <section className="space-y-4" aria-label="关系图谱分析结果">
      <Card>
        <CardContent className="flex flex-col gap-3 p-4 lg:flex-row lg:items-center lg:justify-between">
          <div className="flex flex-wrap items-center gap-2">
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
          <label className="flex items-center gap-2 text-xs text-muted-foreground">
            图谱视图
            <select
              aria-label="图谱投影"
              value={projection}
              onChange={(event) =>
                onProjectionChange(
                  event.target.value as DocumentGraphProjection,
                )
              }
              className="h-9 max-w-48 rounded-md border border-input bg-background px-3 text-sm text-foreground"
            >
              {Object.entries(PROJECTION_LABELS).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
        </CardContent>
      </Card>

      {artifact?.error && artifact.graph_snapshot && (
        <Alert variant="destructive">
          <AlertTitle>关系图谱运行异常，正在显示最后已提交快照</AlertTitle>
          <AlertDescription>{errorText(artifact.error)}</AlertDescription>
        </Alert>
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
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            {[
              ["计划记录", artifact.coverage.records_planned],
              ["记录已检", artifact.coverage.records_examined],
              ["技术未完成", artifact.coverage.records_incomplete],
              ["待展开前沿", artifact.coverage.pending_frontiers],
            ].map(([label, value]) => (
              <Card key={label}>
                <CardContent className="p-3">
                  <p className="text-xs text-muted-foreground">{label}</p>
                  <p className="mt-1 text-xl font-semibold tabular-nums">
                    {value}
                  </p>
                </CardContent>
              </Card>
            ))}
          </div>

          <div className="grid min-h-[34rem] gap-4 xl:grid-cols-[minmax(16rem,0.85fr)_minmax(22rem,1.35fr)_minmax(18rem,0.9fr)]">
            <Card className="min-h-0 overflow-hidden">
              <CardHeader className="border-b p-4">
                <CardTitle className="text-sm">
                  实体（{artifact.entities.length}）
                </CardTitle>
              </CardHeader>
              <CardContent className="h-[30rem] space-y-2 overflow-y-auto p-3 xl:h-full">
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

            <div className="min-h-0 space-y-4">
              <Card>
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
                            <span className="font-medium">
                              {entitiesById.get(source.entity_id)?.label ||
                                source.entity_id}
                            </span>
                            <ArrowRight className="size-3.5 text-muted-foreground" />
                            <span className="text-primary">
                              {edge.predicate_label}
                            </span>
                            <ArrowRight className="size-3.5 text-muted-foreground" />
                            <span className="font-medium">
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
                            <span className="font-mono text-[10px] text-muted-foreground">
                              {edge.candidate_id}@{edge.revision}
                            </span>
                          </span>
                        </button>
                      );
                    })
                  )}
                </CardContent>
              </Card>

              <Card>
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
                        </span>
                      </button>
                    ))
                  )}
                </CardContent>
              </Card>
            </div>

            <Card className="min-h-0 overflow-hidden">
              <CardHeader className="border-b p-4">
                <CardTitle className="text-sm">候选详情与证据</CardTitle>
              </CardHeader>
              <CardContent className="h-[30rem] space-y-5 overflow-y-auto p-4 xl:h-full">
                {!selected ? (
                  <p className="text-sm text-muted-foreground">
                    选择实体、关系或属性查看详情。
                  </p>
                ) : (
                  <>
                    <section className="space-y-2">
                      <div className="flex items-center gap-2">
                        {selected.kind === "entity" ? (
                          <CircleDot className="size-4" />
                        ) : selected.kind === "relationship" ? (
                          <GitBranch className="size-4" />
                        ) : (
                          <Braces className="size-4" />
                        )}
                        <h3 className="break-words text-sm font-semibold">
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
                              <DetailRow
                                label="原始值"
                                value={selected.item.raw_value || "—"}
                              />
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

          <Card>
            <CardHeader className="border-b p-4">
              <CardTitle className="text-sm">覆盖与未完成范围</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4 p-4">
              <div className="grid gap-2 text-xs sm:grid-cols-3 xl:grid-cols-6">
                {[
                  ["计划记录", artifact.coverage.records_planned],
                  ["已检查", artifact.coverage.records_examined],
                  ["技术未完成", artifact.coverage.records_incomplete],
                  ["未尝试", artifact.coverage.records_unattempted],
                  ["语义待定", artifact.unresolved.undetermined],
                  ["不支持", artifact.unresolved.unsupported],
                ].map(([label, value]) => (
                  <div key={label} className="rounded-md bg-muted/50 p-2">
                    <span className="text-muted-foreground">{label}</span>
                    <strong className="ml-2 tabular-nums">{value}</strong>
                  </div>
                ))}
              </div>
              {artifact.coverage.subjects.length === 0 ? (
                <p className="text-xs text-muted-foreground">
                  尚无已提交的主体—谓词覆盖条目；请结合当前阶段判断，不能据此断言全文无关系。
                </p>
              ) : (
                <div className="overflow-x-auto rounded-md border">
                  <table className="w-full min-w-[48rem] text-left text-xs">
                    <thead className="bg-muted/60 text-muted-foreground">
                      <tr>
                        {[
                          "主体",
                          "谓词",
                          "Phase 1",
                          "Phase 2",
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
                            item.phase_counts.phase2,
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
              {artifact.coverage.stop_reason && (
                <Alert>
                  <AlertTitle>停止原因</AlertTitle>
                  <AlertDescription>
                    {artifact.coverage.stop_reason}
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
