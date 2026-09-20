"use client";

import { ExternalLink } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import type { DocumentAttributeCandidate } from "@/lib/api";
import { formatDocumentAnalysisReason } from "@/lib/document-analysis";

function SourceButton({ refs, label, select }: { refs: string[]; label: string; select: (selectionRef: string) => void }) {
  if (!refs.length) return null;
  return <span className="inline-flex flex-wrap gap-1">
    {refs.map((ref, index) => <button key={ref} type="button" data-tree-action="source"
      className="inline-flex items-center gap-1 text-xs text-primary underline-offset-2 hover:underline"
      aria-label={`${label}${refs.length > 1 ? ` ${index + 1}` : ""}原文`}
      onClick={() => select(ref)}><ExternalLink className="size-3" />
      {label}{refs.length > 1 ? ` ${index + 1}` : ""}</button>)}
  </span>;
}

const CALIBRATION_REASONS: Record<string, string> = {
  attribute_subject_missing: "尚未确定所属实体",
  attribute_value_missing: "原文未提供属性值",
  ontology_property_missing: "尚无可匹配的属性",
  attribute_candidate_capacity: "可能的主体或属性较多，尚未确定",
  attribute_ambiguous: "主体或属性含义存在歧义",
  attribute_not_supported: "原文不足以支持当前属性映射",
  field_column_mismatch: "引用范围与字段边界不一致",
  shacl_not_evaluated: "前置检查未通过，图约束检查尚未执行",
  datatype_mismatch: "当前属性要求的数据类型与原值不一致",
  unit_dimension_mismatch: "当前属性要求的单位维度与原值不一致",
  binding_not_checked: "原文绑定尚未完成检查",
  semantic_not_checked: "主体归属和属性含义尚未核验",
  semantic_undetermined: "主体归属或属性含义尚未确定",
  semantic_not_supported: "原文不支持当前主体归属或属性含义",
  predicate_not_supported: "原文不支持当前属性含义",
  owner_row_mismatch: "主体与当前原值不属于同一记录",
  attribute_unresolved: "主体、属性或值尚未完成校准",
  value_empty: "原文未提供属性值",
  quantity_approximate: "原文是近似值，不能视为精确数值",
  quantity_form_not_allowed: "原文的数值形式不符合当前属性要求",
  quantity_endpoint_unresolved: "区间端点的业务含义尚未确定",
};

function calibrationReason(reason: string) {
  return CALIBRATION_REASONS[reason] ?? formatDocumentAnalysisReason(reason);
}

function ontologyTerm(iri: string) {
  return iri.split(/[\/#:]/).at(-1) || iri;
}

function parsedAttributeText(parsed: DocumentAttributeCandidate["parsed_value"]): string | null {
  if (parsed?.value != null) return String(parsed.value);
  const quantity = parsed?.quantity;
  if (!quantity) return null;
  if (quantity.kind === "range" && quantity.lower != null && quantity.upper != null) {
    return `${quantity.lower_inclusive ? "[" : "("}${quantity.lower}, ${quantity.upper}${quantity.upper_inclusive ? "]" : ")"}`;
  }
  const operators: Record<string, string> = { gt: ">", ge: "≥", lt: "<", le: "≤", approx: "≈", eq: "=" };
  const operator = quantity.operator && operators[quantity.operator];
  const number = quantity.scalar ?? quantity.lower ?? quantity.upper;
  return operator && number != null ? `${operator} ${number}` : number ?? null;
}

export function AttributeCalibrationList({ candidates, select }: {
  candidates: DocumentAttributeCandidate[]; select: (selectionRef: string) => void;
}) {
  const pending = candidates.filter((candidate) => candidate.status !== "resolved");
  if (!pending.length) return null;
  const checkNames: Record<string, string> = {
    binding: "原文绑定", metric: "值校验", semantic: "属性含义", shacl: "图约束",
  };
  const checkStates: Record<string, string> = {
    passed: "通过", pass: "通过", valid: "通过", supported: "通过",
    failed: "未通过", invalid: "未通过", rejected: "未通过",
    not_checked: "尚未执行", not_evaluated: "尚未执行", incomplete: "未完成",
    pending: "待检查", undetermined: "未确定", skipped: "尚未执行",
  };
  return <section aria-label="待校准属性" className="space-y-2 rounded-md border p-3">
    <h4 className="text-sm font-medium">待校准属性 {pending.length} 项</h4>
    <p className="text-xs text-muted-foreground">待校准，不用于推理/报告。</p>
    {pending.map((candidate) => {
      const parsed = candidate.parsed_value;
      const parsedText = parsedAttributeText(parsed);
      const reasons = [...new Set([...candidate.reason_codes, ...(parsed?.issues ?? [])])];
      return <article key={candidate.candidate_id} className="space-y-1 border-t pt-2">
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <span className="font-medium">{candidate.field_label || "未命名字段"}</span>
          {candidate.status === "rejected_mapping" && <Badge variant="outline">当前映射被拒绝</Badge>}
        </div>
        <p className="break-words text-sm">原值：{candidate.raw_value || "未提供"}</p>
        <p className="break-words text-xs text-muted-foreground">
          {parsedText != null ? `解析值：${parsedText}` : "尚无可解析的规范值"}
          {parsed?.datatype_iri && ` · 类型：${ontologyTerm(parsed.datatype_iri)}`}
          {parsed?.precision && ` · 精度：${({ year: "年", month: "月", day: "日" } as Record<string, string>)[parsed.precision] ?? parsed.precision}`}
          {parsed?.quantity?.source_unit && ` · 单位：${parsed.quantity.source_unit}`}
        </p>
        <p className="text-xs text-muted-foreground">候选归属：{candidate.options.length
          ? candidate.options.map((option) => `${option.subject_label || "待确定实体"} → ${option.predicate_label || ontologyTerm(option.predicate_iri)}`).join("；")
          : "尚未确定主体和属性"}</p>
        {reasons.length > 0 && <p className="text-xs text-muted-foreground">
          {reasons.map(calibrationReason).join("；")}</p>}
        {Object.keys(candidate.checks).length > 0 && <p className="text-xs text-muted-foreground">
          {Object.entries(candidate.checks).map(([name, state]) =>
            `${checkNames[name] ?? name}：${checkStates[state] ?? calibrationReason(state)}`).join("；")}
        </p>}
        <div className="flex flex-wrap gap-2">
          <SourceButton refs={candidate.source_selection_refs.label} label="字段" select={select} />
          <SourceButton refs={candidate.source_selection_refs.value} label="原值" select={select} />
        </div>
      </article>;
    })}
  </section>;
}
