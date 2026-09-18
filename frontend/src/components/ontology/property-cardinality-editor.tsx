"use client";

import type { PropertyMultiplicity } from "@/lib/api";
import {
  changeMultiplicity,
  MULTIPLICITY_LABELS,
  type CardinalityFormState,
} from "@/lib/property-cardinality";
import { Field } from "@/components/ontology/field";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

const HINTS: Record<PropertyMultiplicity, string> = {
  unspecified: "尚未声明单值或多值策略，仍可单独设置数量限制。",
  single: "最多一个值；是否必填由最小数量单独决定。",
  multiple: "允许多个值，不要求至少两个；最大数量留空表示未设上限。",
};

export function PropertyCardinalityEditor({
  value,
  onChange,
}: {
  value: CardinalityFormState;
  onChange: (value: CardinalityFormState) => void;
}) {
  return (
    <div className="space-y-2 rounded border bg-background/50 p-2">
      <Field label="取值数量">
        <Select
          value={value.multiplicity}
          onValueChange={(next: PropertyMultiplicity) => onChange(changeMultiplicity(value, next))}
        >
          <SelectTrigger aria-label="取值数量" className="h-auto rounded px-2 py-1 text-sm">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {(Object.keys(MULTIPLICITY_LABELS) as PropertyMultiplicity[]).map((mode) => (
              <SelectItem key={mode} value={mode}>{MULTIPLICITY_LABELS[mode]}</SelectItem>
            ))}
          </SelectContent>
        </Select>
      </Field>
      <p className="text-xs text-muted-foreground">{HINTS[value.multiplicity]}</p>
      <div className="flex gap-2">
        <Field label="最小数量" className="w-1/2">
          <Input
            type="number"
            min={0}
            max={value.multiplicity === "single" ? 1 : undefined}
            step={1}
            aria-label="最小数量"
            placeholder="未指定"
            value={value.min_cardinality}
            onChange={(event) => onChange({ ...value, min_cardinality: event.target.value })}
            className="h-auto rounded px-2 py-1 text-sm shadow-none"
          />
        </Field>
        <Field label="最大数量" className="w-1/2">
          <Input
            type="number"
            min={value.multiplicity === "multiple" ? 2 : 0}
            step={1}
            aria-label="最大数量"
            placeholder="未设上限"
            value={value.max_cardinality}
            disabled={value.multiplicity === "single"}
            onChange={(event) => onChange({ ...value, max_cardinality: event.target.value })}
            className="h-auto rounded px-2 py-1 text-sm shadow-none"
          />
        </Field>
      </div>
      <p className="text-xs text-muted-foreground">
        最小数量为 1 表示必填；留空表示未声明，0 表示允许缺省。
      </p>
    </div>
  );
}
