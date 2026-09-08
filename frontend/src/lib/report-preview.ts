import type { FrozenRecord, OutputNode, TemplateV2 } from "./reporting-v2";

export interface CoverageRequirement {
  requirement_id: string;
  input_id: string;
  field_path: string[];
  origin_refs: string[];
  execution_scope_id: string;
  required: boolean;
  activation: string;
  satisfied: boolean;
  issue_refs: string[];
}

export interface ReportInputSnapshot {
  input_snapshot_id: string;
  source_bundle: {
    template: TemplateV2;
    preview_mode?: string;
    sources: Record<string, { job_id?: string }>;
  };
  coverage: CoverageRequirement[];
  material_status: string;
  blocking_issues: { issue_id: string; code: string; state: string; message: string }[];
}

export type ReportOutputResult = FrozenRecord<{
  output_id: string;
  execution_scope_id: string;
  output_ast: OutputNode;
  execution_status: string;
  inactive: boolean;
}>;

export function summarizeCoverage(snapshot?: ReportInputSnapshot) {
  if (!snapshot) return null;
  const active = snapshot.coverage.filter((item) => item.activation !== "inactive");
  const satisfied = active.filter((item) => item.satisfied).length;
  return {
    total: active.length,
    satisfied,
    missing: active.length - satisfied,
    inactive: snapshot.coverage.length - active.length,
    // An empty/failed resolution is not proof of complete materials.
    percent: active.length ? Math.round(satisfied / active.length * 100)
      : snapshot.material_status === "ready" ? 100 : 0,
  };
}

export function outputCoverage(outputId: string, ancestors: string[], snapshot?: ReportInputSnapshot,
  outputs: ReportOutputResult[] = []) {
  const origins = new Set([outputId, ...ancestors]);
  const requirements = snapshot?.coverage.filter((item) => item.origin_refs.some((id) => origins.has(id))) ?? [];
  const results = outputs.filter((item) => item.payload.output_id === outputId);
  const state = !snapshot ? "pending"
    : results.some((item) => item.payload.execution_status === "failed") ? "failed"
    : requirements.some((item) => item.activation !== "inactive" && !item.satisfied) ? "missing"
    : results.length && results.every((item) => item.payload.inactive) ? "inactive"
    : results.length && results.every((item) => item.payload.execution_status === "completed") ? "ready"
    : "pending";
  return { state, requirements, results };
}
