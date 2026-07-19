"use client";

import { useMemo, useState, type ReactNode } from "react";
import {
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  FileText,
  Loader2,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { ENTITY_PALETTE, entityColorIndex } from "./entity-mark";
import {
  formatRelationSourceRef,
  relationSourceRefKey,
} from "@/lib/relation-source-ref";
import type {
  DocClassification,
  PdeConflict,
  PdeConflictDecision,
  PdeDecisionChoice,
  Relationship,
  SubRelationship,
} from "@/lib/api";

interface RelationPanelProps {
  docClass?: DocClassification | null;
  relationships?: Relationship[];
  selectedSourceRef?: string | null;
  onSelectSourceRef?: (ref: string | null) => void;
  // CMCReport PDE 冲突的人工决策（页面持有查询/变更，本面板仅展示 + 回调）。
  decision?: PdeConflictDecision | null;
  onDecide?: (chosen: PdeDecisionChoice) => void;
  decisionPending?: boolean;
}

interface PredicateGroup {
  predicate: string;
  items: SubRelationship[];
}

// 按谓词标签归组（顶层 + 递归子关系共用）。
function groupByPredicate(items: SubRelationship[]): PredicateGroup[] {
  const map = new Map<string, SubRelationship[]>();
  for (const it of items) {
    const key = it.predicate_label || it.predicate_iri;
    const list = map.get(key);
    if (list) list.push(it);
    else map.set(key, [it]);
  }
  return [...map.entries()].map(([predicate, groupItems]) => ({
    predicate,
    items: groupItems,
  }));
}

function formatPropertyValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "boolean") return value ? "是" : "否";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "bigint") {
    return String(value);
  }
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

const DECISION_LABELS: Record<PdeDecisionChoice, string> = {
  derived: "采纳推导",
  asserted: "采纳原文",
  pending: "待复核",
};

// provenance.factors → 中文短标签（仅展示存在的因子）。
const FACTOR_LABELS: Array<[string, string]> = [
  ["F1_interspecies", "F1 种属外推"],
  ["F2_intraindividual", "F2 个体差异"],
  ["F3_duration", "F3 暴露周期"],
  ["F4_severity", "F4 严重度"],
  ["F5_loael", "F5 LOAEL 外推"],
  ["composite_UF", "综合不确定系数"],
  ["BW_kg", "体重 (kg)"],
  ["breathing_volume_m3", "呼吸量 (m³)"],
];

const INPUT_SOURCE_LABELS: Record<string, string> = {
  extracted: "原文抽取毒理参数",
  "mock-tox-study": "mock 毒理研究源（占位）",
};

function DecisionButton({
  active,
  disabled,
  onClick,
  children,
}: {
  active: boolean;
  disabled?: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <Button
      type="button"
      size="sm"
      variant={active ? "default" : "outline"}
      disabled={disabled}
      onClick={onClick}
      className="h-6 px-2 text-[11px]"
    >
      {children}
    </Button>
  );
}

/**
 * CMCReport「推导 vs 原文」PDE 冲突横幅：琥珀色告警 + 推导/原文对照 + 可展开推导依据
 * （F1–F5/公式）+ 人工决策三选一（采纳推导 / 采纳原文 / 待复核，CAS 持久化于页面层）。
 */
function ConflictBanner({
  conflict,
  decision,
  onDecide,
  decisionPending,
}: {
  conflict: PdeConflict;
  decision?: PdeConflictDecision | null;
  onDecide?: (chosen: PdeDecisionChoice) => void;
  decisionPending?: boolean;
}) {
  const [showProv, setShowProv] = useState(false);
  const chosen: PdeDecisionChoice = decision?.chosen ?? "pending";
  const prov = (conflict.derived.provenance ?? {}) as Record<string, unknown>;
  const factors = (prov.factors ?? {}) as Record<string, number>;
  const inputSource =
    INPUT_SOURCE_LABELS[conflict.derived.input_source] ?? conflict.derived.input_source;

  const choose = (c: PdeDecisionChoice) => {
    if (onDecide && !decisionPending && c !== chosen) onDecide(c);
  };

  return (
    <div className="my-1 ml-5 rounded-md border border-amber-300 bg-amber-50 p-2.5 text-xs dark:border-amber-800/60 dark:bg-amber-950/30">
      <div className="flex items-start gap-1.5">
        <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-600 dark:text-amber-500" />
        <div className="min-w-0 flex-1 space-y-1.5">
          <div className="flex items-center gap-1.5">
            <span className="font-semibold text-amber-800 dark:text-amber-300">
              PDE 潜能等级冲突
            </span>
            <Badge
              variant="outline"
              className="h-4 border-amber-400 px-1 text-[10px] text-amber-700 dark:text-amber-300"
            >
              Δ{conflict.delta_bands} 带
            </Badge>
          </div>
          <p className="leading-relaxed text-amber-900/90 dark:text-amber-200/90">
            {conflict.summary}
          </p>

          {/* 推导 vs 原文 对照 */}
          <div className="grid grid-cols-2 gap-1.5">
            <div className="rounded border border-amber-200 bg-background/60 p-1.5 dark:border-amber-800/50">
              <div className="text-[10px] text-muted-foreground">推导（{inputSource}）</div>
              <div className="font-medium">
                OEB band {conflict.derived.band}
                {conflict.derived.provisional && (
                  <span className="ml-1 text-[10px] text-amber-600">· 暂定</span>
                )}
              </div>
              {conflict.derived.pde_ug_day != null && (
                <div className="text-[10px] text-muted-foreground">
                  PDE ≈ {conflict.derived.pde_ug_day} µg/日
                </div>
              )}
            </div>
            <div className="rounded border border-amber-200 bg-background/60 p-1.5 dark:border-amber-800/50">
              <div className="text-[10px] text-muted-foreground">原文</div>
              <div className="font-medium">OEB band {conflict.asserted.band}</div>
              <div className="text-[10px] text-muted-foreground">
                PDE {conflict.asserted.pde_mg_day} mg/日
              </div>
            </div>
          </div>

          {/* 推导依据（provenance） */}
          <button
            type="button"
            onClick={() => setShowProv((v) => !v)}
            className="inline-flex items-center gap-1 text-[11px] text-amber-700 hover:underline dark:text-amber-300"
          >
            {showProv ? (
              <ChevronDown className="h-3 w-3" />
            ) : (
              <ChevronRight className="h-3 w-3" />
            )}
            推导依据（F1–F5 / 公式）
          </button>
          {showProv && (
            <div className="space-y-1 rounded border border-amber-200 bg-background/60 p-1.5 dark:border-amber-800/50">
              {typeof prov.formula === "string" && (
                <div className="text-[10px] leading-relaxed text-muted-foreground">
                  {prov.formula}
                </div>
              )}
              <div className="grid grid-cols-2 gap-x-3 gap-y-0.5">
                {FACTOR_LABELS.filter(([k]) => factors[k] != null).map(([k, label]) => (
                  <div key={k} className="flex items-baseline justify-between gap-1">
                    <span className="text-muted-foreground">{label}</span>
                    <span className="tabular-nums">{factors[k]}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* 人工决策：仅在持有决策查询/变更的上下文（报告中心详情页）渲染交互控件；
              其余复用场景（抽取抽屉/模板源文档）仅展示上方冲突信息，不出现失效按钮。 */}
          {onDecide && (
          <div className="space-y-1 border-t border-amber-200 pt-1.5 dark:border-amber-800/50">
            <div className="text-[10px] font-medium text-amber-800 dark:text-amber-300">
              人工决策
            </div>
            <div className="flex flex-wrap items-center gap-1">
              <DecisionButton
                active={chosen === "derived"}
                disabled={decisionPending}
                onClick={() => choose("derived")}
              >
                采纳推导（band {conflict.derived.band}）
              </DecisionButton>
              <DecisionButton
                active={chosen === "asserted"}
                disabled={decisionPending}
                onClick={() => choose("asserted")}
              >
                采纳原文（band {conflict.asserted.band}）
              </DecisionButton>
              <DecisionButton
                active={chosen === "pending"}
                disabled={decisionPending}
                onClick={() => choose("pending")}
              >
                待复核
              </DecisionButton>
              {decisionPending && (
                <Loader2 className="h-3 w-3 animate-spin text-amber-600" />
              )}
            </div>
            {decision && decision.version > 0 && (
              <div className="text-[10px] text-muted-foreground">
                当前：{DECISION_LABELS[decision.chosen]}
                {decision.actor && ` · ${decision.actor}`}
                {decision.decided_at &&
                  ` · ${decision.decided_at.slice(0, 19).replace("T", " ")}`}
              </div>
            )}
          </div>
          )}
        </div>
      </div>
    </div>
  );
}

// 单个对象端点：可展开看数据属性 + 递归子关系（合成路线→步骤→设备/中间体）。
function EndpointRow({
  node,
  depth,
  rowKey,
  selectedSourceRef,
  onSelectSourceRef,
  decision,
  onDecide,
  decisionPending,
}: {
  node: SubRelationship;
  depth: number;
  rowKey: string;
  selectedSourceRef?: string | null;
  onSelectSourceRef?: (ref: string | null) => void;
  decision?: PdeConflictDecision | null;
  onDecide?: (chosen: PdeDecisionChoice) => void;
  decisionPending?: boolean;
}) {
  const hasDetail =
    node.object_data_properties.length > 0 || node.sub_relationships.length > 0;
  // 顶层（depth 0）默认展开，露出数据属性；更深层折叠以免信息过载。
  const [open, setOpen] = useState(depth === 0);
  const color = ENTITY_PALETTE[entityColorIndex(node.object_class_label)];
  const sourceRefKey = relationSourceRefKey(node.source_ref);
  const sourceRefLabel = formatRelationSourceRef(node.source_ref);
  const isSelected = !!(sourceRefKey && sourceRefKey === selectedSourceRef);

  function handleClick() {
    if (hasDetail) setOpen((v) => !v);
    if (sourceRefKey && onSelectSourceRef) {
      onSelectSourceRef(isSelected ? null : sourceRefKey);
    }
  }

  return (
    <div>
      <button
        onClick={handleClick}
        className={`flex w-full items-start gap-1.5 rounded-md px-2 py-1.5 text-left text-sm transition-colors hover:bg-accent/60${isSelected ? " bg-blue-50 border-l-2 border-blue-500 dark:bg-blue-950/30" : ""}`}
      >
        {hasDetail ? (
          open ? (
            <ChevronDown className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground" />
          ) : (
            <ChevronRight className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground" />
          )
        ) : (
          <span className="h-3.5 w-3.5 shrink-0" />
        )}
        <span
          className="mt-1 h-2 w-2 shrink-0 rounded-sm"
          style={{ background: color.border }}
        />
        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-baseline gap-x-1.5 gap-y-0.5">
            <span className="break-all font-medium">{node.object_text}</span>
            <span className="text-[11px] text-muted-foreground">
              {node.object_class_label}
            </span>
          </span>
          {sourceRefLabel && (
            <span className="mt-0.5 block truncate text-[10px] text-muted-foreground/80">
              {sourceRefLabel}
            </span>
          )}
        </span>
        {node.sub_relationships.length > 0 && (
          <Badge variant="outline" className="ml-auto h-5 shrink-0 text-[10px] tabular-nums">
            {node.sub_relationships.length}
          </Badge>
        )}
      </button>

      {/* PDE 冲突横幅：始终展示（不随行折叠），紧随端点标题；仅共线评估端点会携带。 */}
      {node.conflict && (
        <ConflictBanner
          conflict={node.conflict}
          decision={decision}
          onDecide={onDecide}
          decisionPending={decisionPending}
        />
      )}

      {open && hasDetail && (
        <div className="ml-5 mb-1 space-y-1 border-l-2 border-muted pl-3">
          {/* 数据属性 */}
          {node.object_data_properties.length > 0 && (
            <div className="space-y-0.5 py-0.5">
              {node.object_data_properties.map((dp, i) => (
                <div
                  key={`${dp.label}:${i}`}
                  className="flex items-baseline gap-1 text-xs"
                >
                  <span
                    className="shrink-0 text-muted-foreground"
                    title={dp.iri ?? "未匹配本体数据属性（原文）"}
                  >
                    {dp.label}
                    {!dp.iri && <span className="text-muted-foreground/50">*</span>}:
                  </span>
                  <span className="break-all">{formatPropertyValue(dp.value)}</span>
                </div>
              ))}
            </div>
          )}

          {/* 递归子关系（按谓词分组） */}
          {node.sub_relationships.length > 0 &&
            groupByPredicate(node.sub_relationships).map((g) => (
              <div key={g.predicate} className="pt-0.5">
                <div className="px-1 py-0.5 text-[11px] font-medium text-muted-foreground/90">
                  ↳ {g.predicate}
                  <span className="ml-1 text-muted-foreground/60">
                    ({g.items.length})
                  </span>
                </div>
                {g.items.map((sub, i) => (
                  <EndpointRow
                    key={`${rowKey}:${g.predicate}:${i}`}
                    node={sub}
                    depth={depth + 1}
                    rowKey={`${rowKey}:${g.predicate}:${i}`}
                    selectedSourceRef={selectedSourceRef}
                    onSelectSourceRef={onSelectSourceRef}
                    decision={decision}
                    onDecide={onDecide}
                    decisionPending={decisionPending}
                  />
                ))}
              </div>
            ))}
        </div>
      )}
    </div>
  );
}

export function RelationPanel({
  docClass,
  relationships,
  selectedSourceRef,
  onSelectSourceRef,
  decision,
  onDecide,
  decisionPending,
}: RelationPanelProps) {
  const rels = relationships ?? [];
  const groups = useMemo(() => groupByPredicate(relationships ?? []), [relationships]);
  const classificationBadge =
    docClass?.source === "explicit" && docClass.score === 0
      ? "显式指定"
      : `匹配分 ${docClass?.score ?? 0}`;

  return (
    <div className="flex h-full flex-col">
      {/* 文档分类徽章 + 可解释信号 */}
      <div className="px-4 py-3">
        {docClass ? (
          <div className="space-y-1.5">
            <div className="flex items-center gap-2">
              <FileText className="h-4 w-4 shrink-0 text-muted-foreground" />
              <span className="text-sm font-semibold">{docClass.label}</span>
              <Badge variant="secondary" className="h-5 text-[10px] tabular-nums">
                {classificationBadge}
              </Badge>
            </div>
            <div
              className="break-all font-mono text-[10px] text-muted-foreground/80"
              title={docClass.doc_class_iri}
            >
              {docClass.doc_class_iri}
            </div>
            {docClass.signals.length > 0 && (
              <div className="flex flex-wrap gap-1">
                {docClass.signals.map((sig) => (
                  <span
                    key={sig}
                    className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground"
                  >
                    {sig}
                  </span>
                ))}
              </div>
            )}
          </div>
        ) : (
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <FileText className="h-4 w-4 shrink-0" />
            未识别文档类型
          </div>
        )}
      </div>
      <Separator />

      {rels.length === 0 ? (
        <div className="flex flex-1 items-center justify-center px-4">
          <p className="text-sm text-muted-foreground">
            {docClass ? "未抽取到关系" : "仅 Word 文档支持关系抽取"}
          </p>
        </div>
      ) : (
        <div className="flex-1 overflow-y-auto py-1">
          {groups.map((group) => {
            const color =
              ENTITY_PALETTE[entityColorIndex(group.items[0].object_class_label)];
            return (
              <div key={group.predicate} className="mb-2">
                {/* 谓词组头 */}
                <div className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-muted-foreground">
                  <span
                    className="h-2.5 w-2.5 shrink-0 rounded-sm"
                    style={{ background: color.border }}
                  />
                  <span className="flex-1 truncate">{group.predicate}</span>
                  <Badge
                    variant="outline"
                    className="ml-auto h-5 text-[10px] tabular-nums"
                  >
                    {group.items.length}
                  </Badge>
                </div>
                {/* 端点 */}
                <div className="space-y-0.5 px-2">
                  {group.items.map((edge, i) => (
                    <EndpointRow
                      key={`${group.predicate}:${i}`}
                      node={edge}
                      depth={0}
                      rowKey={`${group.predicate}:${i}`}
                      selectedSourceRef={selectedSourceRef}
                      onSelectSourceRef={onSelectSourceRef}
                      decision={decision}
                      onDecide={onDecide}
                      decisionPending={decisionPending}
                    />
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
