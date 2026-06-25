"use client";

import { Field } from "@/components/ontology/field";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { PATTERN_CMP_OPS, PATTERN_OPS, type PatternCmp, type RulePattern } from "@/lib/api";

/**
 * 受限模式编辑器（US3 / FR-004 / research.md R5）。
 *
 * 只暴露解释器 VOCABULARY 中的「叶子」算子（单一谓词 + 阈值/取值），刻意**不**提供
 * 通用类表达式编辑器：复合 and/or 与底层 literal_* 仅作只读 JSON 展示，引导到专家路径。
 * 这样领域专家改阈值/换谓词时无从写出越界模式，写回的 AST 必然落在 interpreter 可执行集内。
 */

const OP_LABEL: Record<(typeof PATTERN_OPS)[number], string> = {
  datatype_facet: "数值阈值 · datatype_facet",
  boolean_has_value: "布尔取值 · boolean_has_value",
  class_membership: "关系成员 · class_membership",
  some_values_from: "存在量化 · some_values_from",
  external_alignment: "外部对齐 · external_alignment",
  class_present: "类已断言 · class_present",
};

const CMP_LABEL: Record<PatternCmp, string> = {
  gt: "> 大于",
  ge: "≥ 不小于",
  lt: "< 小于",
  le: "≤ 不大于",
  eq: "= 等于",
  ne: "≠ 不等于",
};

type LeafOp = (typeof PATTERN_OPS)[number];
type LeafPattern = Extract<RulePattern, { op: LeafOp }>;

const isLeafPattern = (p: RulePattern): p is LeafPattern =>
  (PATTERN_OPS as readonly string[]).includes(p.op);

/** 切换算子时给出该算子的空白骨架，避免遗留无关字段污染 AST。 */
function blankFor(op: LeafOp): LeafPattern {
  switch (op) {
    case "datatype_facet":
      return { op, property: "", cmp: "gt", value: 0 };
    case "boolean_has_value":
      return { op, property: "", value: true };
    case "class_membership":
      return { op, property: "", classes: [] };
    case "some_values_from":
      return { op, property: "", filler_class: "" };
    case "external_alignment":
      return { op, property: "", alignment: "" };
    case "class_present":
      return { op, class: "" };
  }
}

const fieldCls = "h-auto rounded px-2 py-1 text-sm";
const monoCls = "h-auto rounded px-2 py-1 font-mono text-xs";

export function RulePatternEditor({
  value,
  onChange,
  disabled = false,
}: {
  value: RulePattern;
  onChange: (next: RulePattern) => void;
  disabled?: boolean;
}) {
  // 复合/底层算子超出受限表单可表达范围 → 只读 JSON，提示走专家路径。
  if (!isLeafPattern(value)) {
    return (
      <div className="space-y-1 rounded border bg-muted p-2">
        <p className="text-[11px] text-muted-foreground">
          复合模式（{value.op}）超出受限编辑器范围，仅只读展示——请在 TTL / 专家路径维护。
        </p>
        <pre className="overflow-x-auto rounded bg-background p-2 font-mono text-[11px] text-foreground">
          {JSON.stringify(value, null, 2)}
        </pre>
      </div>
    );
  }

  return (
    <div className="space-y-2 rounded border bg-muted p-2">
      <Field label="算子 op" hint="解释器受限词汇">
        <Select
          value={value.op}
          onValueChange={(next) => onChange(blankFor(next as LeafOp))}
          disabled={disabled}
        >
          <SelectTrigger className={fieldCls}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {PATTERN_OPS.map((o) => (
              <SelectItem key={o} value={o} className="text-sm">
                {OP_LABEL[o]}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </Field>

      {/* class_present 只引用一个药物类名；其余算子都先取一个谓词 property。 */}
      {value.op === "class_present" ? (
        <Field label="类名 class" hint="个体已断言的药物类（短名/IRI 子串）">
          <Input
            value={value.class}
            onChange={(e) => onChange({ ...value, class: e.target.value })}
            disabled={disabled}
            className={monoCls}
          />
        </Field>
      ) : (
        <Field label="谓词 property" hint="数据/对象属性短名">
          <Input
            value={value.property}
            onChange={(e) => onChange({ ...value, property: e.target.value })}
            disabled={disabled}
            className={monoCls}
          />
        </Field>
      )}

      {value.op === "datatype_facet" && (
        <div className="flex gap-2">
          <Field label="比较 cmp" className="w-1/2">
            <Select
              value={value.cmp}
              onValueChange={(c) => onChange({ ...value, cmp: c as PatternCmp })}
              disabled={disabled}
            >
              <SelectTrigger className={fieldCls}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {PATTERN_CMP_OPS.map((c) => (
                  <SelectItem key={c} value={c} className="text-sm">
                    {CMP_LABEL[c]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>
          <Field label="阈值 value" hint="数值" className="w-1/2">
            <Input
              type="number"
              value={String(value.value)}
              onChange={(e) => onChange({ ...value, value: Number(e.target.value) })}
              disabled={disabled}
              className={fieldCls}
            />
          </Field>
        </div>
      )}

      {value.op === "boolean_has_value" && (
        <Field label="取值 value" hint="布尔">
          <Select
            value={value.value ? "true" : "false"}
            onValueChange={(v) => onChange({ ...value, value: v === "true" })}
            disabled={disabled}
          >
            <SelectTrigger className={fieldCls}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="true" className="text-sm">true（成立）</SelectItem>
              <SelectItem value="false" className="text-sm">false（不成立）</SelectItem>
            </SelectContent>
          </Select>
        </Field>
      )}

      {value.op === "class_membership" && (
        <Field label="候选类 classes" hint="逗号分隔，命中任一即真">
          <Input
            value={value.classes.join(", ")}
            onChange={(e) =>
              onChange({
                ...value,
                classes: e.target.value
                  .split(",")
                  .map((s) => s.trim())
                  .filter(Boolean),
              })
            }
            disabled={disabled}
            className={monoCls}
          />
        </Field>
      )}

      {value.op === "some_values_from" && (
        <Field label="填充类 filler_class" hint="∃ property . filler_class">
          <Input
            value={value.filler_class}
            onChange={(e) => onChange({ ...value, filler_class: e.target.value })}
            disabled={disabled}
            className={monoCls}
          />
        </Field>
      )}

      {value.op === "external_alignment" && (
        <Field label="对齐 alignment" hint="外部类 IRI 子串（ChEBI/ATC…）">
          <Input
            value={value.alignment}
            onChange={(e) => onChange({ ...value, alignment: e.target.value })}
            disabled={disabled}
            className={monoCls}
          />
        </Field>
      )}
    </div>
  );
}

/** 单行人读摘要，用于列表项。 */
export function describePattern(p: RulePattern): string {
  switch (p.op) {
    case "datatype_facet":
      return `${p.property} ${p.cmp} ${p.value}`;
    case "boolean_has_value":
      return `${p.property} = ${p.value}`;
    case "class_membership":
      return `${p.property} ∈ {${p.classes.join(", ")}}`;
    case "some_values_from":
      return `∃ ${p.property} . ${p.filler_class}`;
    case "external_alignment":
      return `${p.property} ⇝ ${p.alignment}`;
    case "class_present":
      return `class ∋ ${p.class}`;
    case "and":
      return `(${p.operands.map(describePattern).join(" ∧ ")})`;
    case "or":
      return `(${p.operands.map(describePattern).join(" ∨ ")})`;
    default:
      return JSON.stringify(p);
  }
}
