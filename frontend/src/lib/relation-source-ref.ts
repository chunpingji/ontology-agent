import type {
  RelationSourceRef,
  StructuredRelationSourceRef,
} from "@/lib/api";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function numericPart(
  ref: StructuredRelationSourceRef,
  key: "table" | "row" | "column",
  label: string,
): string | null {
  const value = ref[key];
  return typeof value === "number" && Number.isFinite(value)
    ? `${label} ${value + 1}`
    : null;
}

/** Stable string stored in the page-level selection state and passed to WordViewer. */
export function relationSourceRefKey(
  ref: RelationSourceRef | null | undefined,
): string | null {
  if (typeof ref === "string") return ref;
  if (!ref) return null;
  try {
    return JSON.stringify(ref);
  } catch {
    return String(ref);
  }
}

/** Human-readable label for both legacy string refs and Document Profile locators. */
export function formatRelationSourceRef(
  ref: RelationSourceRef | null | undefined,
): string | null {
  if (typeof ref === "string") return ref;
  if (!ref) return null;

  const parts: string[] = [];
  if (typeof ref.section === "string" && ref.section.trim()) {
    parts.push(`§ ${ref.section.trim()}`);
  }

  const table = numericPart(ref, "table", "表");
  const row = numericPart(ref, "row", "行");
  const column = numericPart(ref, "column", "列");
  if (table) parts.push(table);
  if (row) parts.push(row);
  if (column) parts.push(column);

  // Truthy `||` (not nullish `??`): an empty-string `parameter` yields to a
  // meaningful `key`/`header` rather than rendering blank. Mirrors the backend
  // `format_source_ref` (source_ref.py) so web and DOCX show the same label.
  const detail = ref.parameter || ref.key || ref.header;
  if (typeof detail === "string" && detail.trim()) {
    parts.push(detail.trim());
  }

  if (parts.length > 0) return parts.join(" · ");

  const externalParts = [ref.system, ref.entity, ref.record]
    .filter((value): value is string => typeof value === "string" && !!value.trim())
    .map((value) => value.trim());
  if (externalParts.length > 0) return externalParts.join(" · ");

  try {
    return JSON.stringify(ref);
  } catch {
    return "来源位置";
  }
}

/** Decode only keys produced from structured refs; legacy refs remain plain strings. */
export function parseRelationSourceRefKey(
  key: string,
): StructuredRelationSourceRef | null {
  const trimmed = key.trim();
  if (!trimmed.startsWith("{") || !trimmed.endsWith("}")) return null;
  try {
    const parsed: unknown = JSON.parse(trimmed);
    return isRecord(parsed) ? parsed : null;
  } catch {
    return null;
  }
}
