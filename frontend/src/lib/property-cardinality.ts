import type { PropertyCardinality, PropertyMultiplicity } from "@/lib/api";

export interface CardinalityFormState {
  multiplicity: PropertyMultiplicity;
  min_cardinality: string;
  max_cardinality: string;
}

type CardinalitySource = Partial<PropertyCardinality> & { is_functional?: boolean };

export const MULTIPLICITY_LABELS: Record<PropertyMultiplicity, string> = {
  unspecified: "未指定",
  single: "单值",
  multiple: "多值",
};

export function cardinalityForm(source: CardinalitySource = {}): CardinalityFormState {
  const multiplicity = source.multiplicity ?? (source.is_functional ? "single" : "unspecified");
  return {
    multiplicity,
    min_cardinality: source.min_cardinality?.toString() ?? "",
    max_cardinality: multiplicity === "single" ? "1" : source.max_cardinality?.toString() ?? "",
  };
}

export function changeMultiplicity(
  current: CardinalityFormState,
  multiplicity: PropertyMultiplicity,
): CardinalityFormState {
  return {
    ...current,
    multiplicity,
    max_cardinality: multiplicity === "single"
      ? "1"
      : current.multiplicity === "single" ? "" : current.max_cardinality,
  };
}

function parseCount(value: string, label: string): number | null {
  const trimmed = value.trim();
  if (trimmed === "") return null;
  const count = Number(trimmed);
  if (!/^\d+$/.test(trimmed) || !Number.isSafeInteger(count)) {
    throw new Error(`${label}必须是非负整数`);
  }
  return count;
}

/** Always send explicit nulls so clearing a saved bound does not preserve it. */
export function cardinalityPayload(
  form: CardinalityFormState,
  domainIri: string,
): PropertyCardinality {
  const min = parseCount(form.min_cardinality, "最小数量");
  const max = parseCount(form.max_cardinality, "最大数量");
  if (form.multiplicity === "single" && max !== 1) {
    throw new Error("单值的最大数量必须为 1");
  }
  if (form.multiplicity === "multiple" && max !== null && max < 2) {
    throw new Error("多值的最大数量须至少为 2，或留空不设上限");
  }
  if (min !== null && max !== null && min > max) {
    throw new Error("最小数量不能大于最大数量");
  }
  if (!domainIri.trim() && (min !== null || (max !== null && form.multiplicity !== "single"))) {
    throw new Error("设置数量限制前请填写定义域；单值的默认上限除外");
  }
  return { multiplicity: form.multiplicity, min_cardinality: min, max_cardinality: max };
}

export function cardinalitySummary(source: CardinalitySource): string {
  const form = cardinalityForm(source);
  const parts = [MULTIPLICITY_LABELS[form.multiplicity]];
  if (form.min_cardinality !== "") parts.push(`最少 ${form.min_cardinality}`);
  if (form.max_cardinality !== "") parts.push(`最多 ${form.max_cardinality}`);
  else if (form.multiplicity === "multiple") parts.push("未设上限");
  return parts.join(" · ");
}
