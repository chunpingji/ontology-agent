import { fetchAPI, identityHeaders, type AiStructureSection, type StaticDemoProfile } from "./api";

export interface InputRef {
  kind?: "input_ref";
  input_id: string;
  field_path?: string[];
  scope?: "input" | "item";
}
export interface ContentNode {
  kind: "text" | "paragraph" | "input_ref" | "claim_ref" | "if" | "repeat" | "signature_region";
  input_id?: string;
  field_path?: string[];
  scope?: "input" | "item";
  text?: string;
  children?: ContentNode[];
  otherwise?: ContentNode[];
  unknown?: ContentNode[];
  claim_id?: string;
  signature_region_id?: string;
  condition?: Record<string, unknown>;
}
export interface Projection {
  kind: string;
  property_iri?: string;
  field_path?: string[];
  fields?: Record<string, { value: Projection; required?: boolean; constraints?: Record<string, unknown> }>;
  [key: string]: unknown;
}
export interface InputDefinition {
  input_id: string;
  name: string;
  label: string;
  binding_ref: string;
  projection: Projection;
  required?: boolean;
  constraints?: Record<string, unknown>;
  [key: string]: unknown;
}
export interface BindingDefinition {
  binding_id: string;
  kind: "facts" | "context" | "workflow" | "derived";
  contract_ref: string | Record<string, unknown>;
  scope?: Record<string, unknown>;
  [key: string]: unknown;
}
export interface OutputRender {
  kind: "narrative" | "table" | "form" | "list" | "static";
  mode?: "composed" | "assisted";
  nodes?: ContentNode[];
  rows?: InputRef;
  items?: InputRef;
  columns?: { column_id: string; title: string; field_ref?: string; value?: { kind: "row_number" } }[];
  fields?: { field_id: string; label: string; value?: InputRef; signature_region_id?: string }[];
  approval_ref?: string;
  [key: string]: unknown;
}
export interface OutputUnit {
  output_id: string;
  title: string;
  bindings: { binding_ref: string }[];
  inputs: { input_ref: string; alias: string; required?: boolean }[];
  render: OutputRender;
  origin?: Record<string, unknown> | null;
}
export interface OutputGroup {
  group_id: string;
  title: string;
  units: OutputUnit[];
  groups?: OutputGroup[];
  origin?: Record<string, unknown> | null;
}
export interface SectionNarrative {
  enabled: boolean;
  instructions: string;
  policy_ref: string;
  input_refs: InputRef[];
  required_refs: InputRef[];
  claim_refs: string[];
}
export interface TemplateV2 {
  schema_version: 2;
  demo_profile?: StaticDemoProfile | null;
  template_family_id: string;
  template_revision_id: string;
  revision_no: number;
  doc_no: string;
  ontology_release_ref: string;
  style_profile_ref: string;
  publication_policy_ref: string;
  definitions: { bindings: Record<string, BindingDefinition>; inputs: Record<string, InputDefinition> };
  record_sources?: Record<string, RecordSource>;
  source_slots: { source_slot_id: string; kind: "document" | "external"; class_iri: string; required?: boolean }[];
  calculation_checks?: { check_id: string; source_slot: string; contract_ref: string }[];
  sections: { section_id: string; title: string; groups: OutputGroup[]; origin?: Record<string, unknown> | null;
    completeness_requirements?: Record<string, unknown>[]; narrative?: SectionNarrative | null }[];
  migration_issues?: { code: string; old_id: string; message: string }[];
  [key: string]: unknown;
}
export interface OutputNode {
  node_id: string;
  kind: string;
  text?: string;
  children?: OutputNode[];
  state?: string;
  header?: boolean;
  ordered?: boolean;
  record_id?: string;
  input_ref?: InputRef & { record_id?: string; execution_scope_id?: string };
  fact_refs?: string[];
  provenance_refs?: Record<string, unknown>[];
  signature_region_id?: string;
}
export interface Diagnostic {
  code: string;
  message?: string;
  schema_path?: string;
  severity?: string;
  remediation?: string;
}
export interface Compilation {
  valid: boolean;
  compilation_id: string;
  schema_hash: string;
  diagnostics: Diagnostic[];
  input_types?: Record<string, unknown>;
  requirements?: Record<string, unknown>[];
}
export interface ReportRun {
  run_id: string;
  input_snapshot_id: string | null;
  source_bundle_id: string;
  execution_status: string;
  material_status: string;
  review_status: string;
  demonstration?: boolean;
  template_status: string;
  phase: string;
  lease_expires_at?: string | null;
  attempt: number;
  revision_no: number;
  body_hash: string | null;
  body_ast: OutputNode | null;
  error?: Diagnostic;
  artifacts: { artifact_id: string; purpose: string; file_hash: string; format: string }[];
}
export interface FrozenRecord<T = Record<string, unknown>> {
  id: string;
  content_hash: string;
  actor: string;
  payload: T;
}
export interface SignatureSlot {
  signature_slot_id: string;
  region_id: string;
  role: string;
  allowed_signers?: string[];
  meanings: string[];
  required?: boolean;
}
export interface SigningSession {
  id: string;
  content_version_id: string;
  content_hash: string;
  revision_no: number;
  status: string;
  parent_id: string | null;
  envelope_ids: string[];
  envelope_request: { actor: string; payload: Record<string, unknown> } | null;
  policy: { signature_slots: SignatureSlot[] };
  signatures: FrozenRecord[];
  events: FrozenRecord[];
}
export interface RegisteredContract {
  contract_id: string;
  kind: string;
  family_id: string;
  revision_no: number;
  status: string;
  stored_definition_hash?: string;
  origin?: string;
  definition: Record<string, unknown>;
}

export function isTemplateV2(value: unknown): value is TemplateV2 {
  return !!value && typeof value === "object" && "schema_version" in value && value.schema_version === 2;
}
export function groupsIn(groups: OutputGroup[]): OutputGroup[] {
  return groups.flatMap((group) => [group, ...groupsIn(group.groups ?? [])]);
}
export function unitsIn(template: TemplateV2): OutputUnit[] {
  return template.sections.flatMap((section) => groupsIn(section.groups).flatMap((group) => group.units));
}

export function isUploadDocumentContainer(section: TemplateV2["sections"][number]): boolean {
  // The parser's filename fallback has no heading origin. An actual heading
  // called "upload" has an origin and must remain an editable section.
  return section.title === "upload" && !section.origin;
}

export function applyStructureTitles(template: TemplateV2, titles: Record<string, string> = {}): TemplateV2 {
  const next = structuredClone(template);
  for (const section of next.sections) {
    for (const group of groupsIn(section.groups)) {
      if (["表格", "字段", "段落行文", "未命名分组", ""].includes(group.title.trim()) && titles[group.group_id]?.trim()) {
        group.title = titles[group.group_id];
      }
    }
  }
  return next;
}

export function materializeTemplateStructure(
  template: TemplateV2, skeleton: AiStructureSection[],
): TemplateV2 {
  // A response must not replace sections authored while analysis was running.
  if (template.sections.length || !skeleton.length) return template;
  return { ...template, sections: skeleton.map((section) => ({
    section_id: section.id, title: section.title, origin: section.origin ? { ...section.origin } : null,
    groups: section.groups.map((group) => ({
      group_id: group.id, title: group.title, origin: group.origin ? { ...group.origin } : null,
      units: group.candidates.map((candidate) => ({
        output_id: candidate.id, title: candidate.semantic_label || candidate.label,
        origin: { ...candidate.origin }, bindings: [], inputs: [],
        render: { kind: "narrative" as const, mode: "composed" as const, nodes: [] },
      })),
    })),
  })) };
}
export function consumersOf(template: TemplateV2, inputId: string): OutputUnit[] {
  return unitsIn(template).filter((unit) => unit.inputs.some((input) => input.input_ref === inputId));
}

export interface OntologyClassContract {
  label?: string;
  parents?: string[];
  properties?: { iri: string; label?: string; datatype?: string; canonical_unit?: string }[];
  relationships?: { iri: string; label?: string; range: string[] }[];
}

export function ontologyMenu(classes: Record<string, OntologyClassContract>, iri: string): OntologyClassContract {
  const seen = new Set<string>();
  const properties = new Map<string, NonNullable<OntologyClassContract["properties"]>[number]>();
  const relationships = new Map<string, NonNullable<OntologyClassContract["relationships"]>[number]>();
  function visit(ref: string) {
    if (seen.has(ref)) return;
    seen.add(ref);
    const entry = classes[ref];
    entry?.parents?.forEach(visit);
    entry?.properties?.forEach((p) => properties.set(p.iri, p));
    entry?.relationships?.forEach((p) => relationships.set(p.iri, p));
  }
  visit(iri);
  return { properties: [...properties.values()], relationships: [...relationships.values()] };
}

export function syncBindingDependencies(template: TemplateV2): TemplateV2 {
  for (const unit of unitsIn(template)) {
    const bindings = new Set(unit.bindings.map((b) => b.binding_ref));
    const seen = new Set<string>();
    function input(ref: string) {
      const definition = template.definitions.inputs[ref];
      if (definition) binding(definition.binding_ref);
    }
    function scan(value: unknown) {
      if (Array.isArray(value)) { value.forEach(scan); return; }
      if (!value || typeof value !== "object") return;
      const object = value as Record<string, unknown>;
      if (typeof object.input_id === "string") input(object.input_id);
      if (typeof object.binding_ref === "string") binding(object.binding_ref);
      if (object.kind === "input" && typeof object.ref === "string") input(object.ref);
      if (object.kind === "binding" && typeof object.ref === "string") binding(object.ref);
      Object.values(object).forEach(scan);
    }
    function binding(ref: string) {
      bindings.add(ref);
      if (seen.has(ref)) return;
      seen.add(ref);
      scan(template.definitions.bindings[ref]);
    }
    unit.inputs.forEach((ref) => input(ref.input_ref));
    unit.bindings = [...bindings].map((binding_ref) => ({ binding_ref }));
  }
  return template;
}
export function moveUnit(template: TemplateV2, outputId: string, targetGroup: string, index: number): TemplateV2 {
  const next = structuredClone(template);
  const groups = next.sections.flatMap((s) => groupsIn(s.groups));
  const source = groups.find((g) => g.units.some((u) => u.output_id === outputId));
  const target = groups.find((g) => g.group_id === targetGroup);
  if (!source || !target) return template;
  const from = source.units.findIndex((u) => u.output_id === outputId);
  const [unit] = source.units.splice(from, 1);
  target.units.splice(Math.max(0, Math.min(index, target.units.length)), 0, unit);
  return next;
}
export function emptyTemplate(): TemplateV2 {
  const id = crypto.randomUUID();
  return {
    schema_version: 2, template_family_id: id, template_revision_id: id, revision_no: 1,
    doc_no: "", ontology_release_ref: "auto:ontology", style_profile_ref: "urn:report:style:standard:1",
    publication_policy_ref: "urn:report:policy:qa-review:1", source_slots: [],
    definitions: { bindings: {}, inputs: {} }, sections: [],
  };
}
export const requestKey = () => crypto.randomUUID();
export const reportGet = <T>(path: string) => fetchAPI<T>("/api/" + path);
export const reportPost = <T>(path: string, body: unknown, signal?: AbortSignal) =>
  fetchAPI<T>("/api/" + path, { method: "POST", body: JSON.stringify(body), signal });
export const compileTemplate = (id: string, hash: string, draft?: TemplateV2) =>
  reportPost<Compilation>("ast-templates/" + id + "/compile", { expected_hash: hash, draft_schema: draft });
export async function downloadArtifact(runId: string, artifactId: string): Promise<void> {
  const base = process.env.NEXT_PUBLIC_API_URL || "";
  const response = await fetch(base + "/api/report-runs/" + runId + "/artifacts/" + artifactId, {
    headers: identityHeaders(),
  });
  if (!response.ok) throw new Error(await response.text());
  const url = URL.createObjectURL(await response.blob());
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = "report-" + runId + "-" + artifactId.slice(0, 12) + ".docx";
  anchor.click();
  URL.revokeObjectURL(url);
}

/** The selected source type owns the root; path choices and type narrowings remain explicit. */
export function changeSourceType(template: TemplateV2, slotId: string, classIri: string): TemplateV2 {
  const next = structuredClone(template);
  const slot = next.source_slots.find((s) => s.source_slot_id === slotId);
  if (!slot) return next;
  const previous = slot.class_iri;
  slot.class_iri = classIri;
  for (const binding of Object.values(next.definitions.bindings)) {
    if (binding.kind !== "facts" || binding.scope?.source_slot !== slotId || typeof binding.contract_ref !== "object") continue;
    const root = binding.scope?.root as { kind?: string } | undefined;
    if (root?.kind && root.kind !== "source_root") continue;
    if (binding.contract_ref.root_class_iri === previous || binding.contract_ref.root_class_iri === "auto:class") {
      binding.contract_ref.root_class_iri = "auto:class";
      if (binding.contract_ref.result_class_iri === previous) binding.contract_ref.result_class_iri = "auto:class";
    }
    binding.contract_ref.release_ref = "template.ontology_release_ref";
  }
  return next;
}

export function resolveModelBinding(binding: BindingDefinition, template: TemplateV2,
  classes: Record<string, OntologyClassContract>): BindingDefinition {
  if (binding.kind !== "facts" || typeof binding.contract_ref !== "object") return binding;
  const next = structuredClone(binding);
  const contract = next.contract_ref as Record<string, unknown>;
  if (["template.ontology_release_ref", "unresolved:ontology", "auto:ontology"].includes(String(contract.release_ref))) contract.release_ref = template.ontology_release_ref;
  const slot = template.source_slots.find((s) => s.source_slot_id === binding.scope?.source_slot);
  const root = binding.scope?.root as { kind?: string } | undefined;
  if ((!root?.kind || root.kind === "source_root") && (!contract.root_class_iri || contract.root_class_iri === "auto:class")) {
    contract.root_class_iri = slot?.class_iri;
  }
  if (!contract.result_class_iri || contract.result_class_iri === "auto:class") {
    let ends = [String(contract.root_class_iri ?? "")];
    for (const step of (binding.scope?.predicate_path ?? []) as { predicate_iri: string; direction?: string }[]) {
      if (step.direction === "inverse") { ends = []; break; }
      ends = [...new Set(ends.flatMap((c) => ontologyMenu(classes, c).relationships?.find((r) => r.iri === step.predicate_iri)?.range ?? []))];
    }
    if (ends.length === 1) contract.result_class_iri = ends[0];
  }
  return next;
}

export function automaticChecks(template: TemplateV2, rules: RegisteredContract[]) {
  const checks = structuredClone(template.calculation_checks ?? []).filter((check) => {
    const rule = rules.find((r) => r.contract_id === check.contract_ref);
    const source = template.source_slots.find((s) => s.source_slot_id === check.source_slot);
    return !rule || check.check_id !== `pde:${check.source_slot}` || source?.class_iri === rule.definition.root_class_iri;
  });
  for (const source of template.source_slots) for (const rule of rules) {
    if (rule.kind !== "calculation" || rule.definition.root_class_iri !== source.class_iri) continue;
    if (!checks.some((c) => c.source_slot === source.source_slot_id && c.contract_ref === rule.contract_id)) {
      checks.push({ check_id: `pde:${source.source_slot_id}`, source_slot: source.source_slot_id, contract_ref: rule.contract_id });
    }
  }
  return checks;
}

export interface RecordSource {
  provider: "assessment_team" | "approver_team" | "equipment" | "production_areas" | "equipment_schedules";
  contract_ref: string;
  filters: Record<string, string | string[]>;
  input_filters?: Record<string, InputRef>;
}
export interface SemanticSourceOption {
  key: string;
  kind: "graph" | "mock";
  label: string;
  provider?: RecordSource["provider"];
  contract_ref?: string;
  available_count: number | null;
  state: string;
  fields: { key: string; label: string; projection: Projection; available?: number; preview_values?: string[] }[];
}
export interface SemanticPatch {
  output_id: string;
  unit: OutputUnit;
  bindings: Record<string, BindingDefinition>;
  inputs: Record<string, InputDefinition>;
  record_sources: Record<string, RecordSource>;
}
export interface SemanticSuggestion {
  completion: "complete" | "incomplete";
  patches: SemanticPatch[];
  diagnostics: { output_id?: string; code: string; message: string }[];
  options: SemanticSourceOption[];
}
export function applySemanticPatches(current: TemplateV2, baseline: TemplateV2, patches: SemanticPatch[]): TemplateV2 {
  if (current.template_revision_id !== baseline.template_revision_id
    || current.ontology_release_ref !== baseline.ontology_release_ref
    || JSON.stringify(current.source_slots) !== JSON.stringify(baseline.source_slots)) return current;
  const next = structuredClone(current);
  const before = new Map(unitsIn(baseline).map((unit) => [unit.output_id, unit]));
  for (const patch of patches) {
    const unit = unitsIn(next).find((item) => item.output_id === patch.output_id);
    const old = before.get(patch.output_id);
    if (!unit || !old || JSON.stringify(unit) !== JSON.stringify(old)) continue;
    if (old.bindings.some(({ binding_ref: key }) => JSON.stringify(current.definitions.bindings[key]) !== JSON.stringify(baseline.definitions.bindings[key]))
      || old.inputs.some(({ input_ref: key }) => JSON.stringify(current.definitions.inputs[key]) !== JSON.stringify(baseline.definitions.inputs[key]))
      || JSON.stringify(current.record_sources) !== JSON.stringify(baseline.record_sources)) continue;
    Object.assign(unit, structuredClone(patch.unit));
    Object.assign(next.definitions.bindings, structuredClone(patch.bindings));
    Object.assign(next.definitions.inputs, structuredClone(patch.inputs));
    if (Object.keys(patch.record_sources).length) next.record_sources = { ...next.record_sources, ...structuredClone(patch.record_sources) };
  }
  return syncBindingDependencies(next);
}

/** Reassemble untouched column placeholders from one physical sample header into a table Slot. */
export function coalesceUnconfiguredTables(template: TemplateV2): TemplateV2 {
  const next = structuredClone(template);
  for (const section of next.sections) for (const group of groupsIn(section.groups)) {
    const tables = new Map<string, OutputUnit[]>();
    const editedTables = new Set<string>();
    for (const unit of group.units) {
      const anchor = unit.origin?.label_anchor as { table_path?: string[]; row_index?: number; column_index?: number } | undefined;
      if (!anchor?.table_path?.length || anchor.row_index !== 0) continue;
      const key = JSON.stringify(anchor.table_path);
      if (unit.inputs.length || unit.bindings.length || unit.render.kind !== "narrative"
        || unit.render.mode === "assisted" || unit.render.nodes?.length) { editedTables.add(key); continue; }
      tables.set(key, [...tables.get(key) || [], unit]);
    }
    for (const [key, headers] of tables) {
      const columns = headers.map((unit) => (unit.origin?.label_anchor as { column_index?: number }).column_index);
      if (headers.length < 3 || editedTables.has(key) || new Set(columns).size !== headers.length) continue;
      const first = headers[0];
      first.origin = { ...first.origin, slot_columns: headers.map((unit) => ({ output_id: unit.output_id, title: unit.title, origin: structuredClone(unit.origin) })) };
      first.title = headers.map((unit) => unit.title).join(" / ");
      const mergedIds = new Set(headers.slice(1).map((unit) => unit.output_id));
      group.units = group.units.filter((unit) => !mergedIds.has(unit.output_id));
    }
  }
  return next;
}

// 从忠于原文结构的 tiptap 文档提取纯文本（供 legacy 无 sample_text 时的行文 Prompt 生成）。
export function tiptapToText(node: unknown): string {
  if (!node || typeof node !== "object") return "";
  const n = node as { type?: string; text?: string; content?: unknown[] };
  if (n.type === "text" && typeof n.text === "string") return n.text;
  const inner = Array.isArray(n.content)
    ? n.content.map(tiptapToText).join("")
    : "";
  // 段落级节点之间补换行，尽量保留原文段落边界。
  return n.type === "paragraph" || n.type === "heading" ? `${inner}\n` : inner;
}
