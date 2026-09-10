import { fetchAPI, identityHeaders, type StaticDemoProfile } from "./api";

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
  source_slots: { source_slot_id: string; kind: "document" | "external"; class_iri: string; required?: boolean }[];
  calculation_checks?: { check_id: string; source_slot: string; contract_ref: string }[];
  sections: { section_id: string; title: string; groups: OutputGroup[]; origin?: Record<string, unknown> | null;
    completeness_requirements?: Record<string, unknown>[] }[];
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
export const reportPost = <T>(path: string, body: unknown) =>
  fetchAPI<T>("/api/" + path, { method: "POST", body: JSON.stringify(body) });
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
