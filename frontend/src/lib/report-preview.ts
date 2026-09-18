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
    demonstration?: boolean;
    sources: Record<string, { job_id?: string; kind?: string; template_id?: string; execution_id?: string }>;
    records?: Record<string, { provenance?: { record_hash?: string }; values?: unknown[] }>;
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

export function isOutputConfigurationRequirement(item: CoverageRequirement, snapshot: ReportInputSnapshot) {
  // The resolver also emits output placeholders in coverage; these are authoring
  // gaps, not requirements for a declared input. Keep their diagnostics separate.
  return !snapshot.source_bundle?.template.definitions.inputs[item.input_id]
    && (snapshot.blocking_issues ?? []).some((issue) => issue.code === "SLOT_CONFIGURATION_MISSING"
      && issue.issue_id === item.requirement_id && item.issue_refs?.includes(issue.issue_id));
}

export function summarizeCoverage(snapshot?: ReportInputSnapshot) {
  if (!snapshot) return null;
  const requirements = snapshot.coverage.filter((item) => !isOutputConfigurationRequirement(item, snapshot));
  const active = requirements.filter((item) => item.activation !== "inactive");
  const satisfied = active.filter((item) => item.satisfied).length;
  return {
    total: active.length,
    satisfied,
    missing: active.length - satisfied,
    inactive: requirements.length - active.length,
    configurationMissing: snapshot.coverage.filter((item) => isOutputConfigurationRequirement(item, snapshot)
      && item.activation !== "inactive" && !item.satisfied).length,
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
