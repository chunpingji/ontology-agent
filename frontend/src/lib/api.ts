const API_BASE = process.env.NEXT_PUBLIC_API_URL || "";

// --- Identity (RBAC headers) ----------------------------------------------
// The backend resolves the caller from trusted gateway headers (X-User /
// X-Role). In dev the workbench injects a senior_analyst identity; the value
// is persisted to localStorage so it survives reloads (FR-033, R7).
export interface Identity {
  username: string;
  role: string;
}

const DEFAULT_IDENTITY: Identity = { username: "analyst", role: "senior_analyst" };

export function getIdentity(): Identity {
  if (typeof window === "undefined") return DEFAULT_IDENTITY;
  try {
    const raw = window.localStorage.getItem("slpra.identity");
    return raw ? (JSON.parse(raw) as Identity) : DEFAULT_IDENTITY;
  } catch {
    return DEFAULT_IDENTITY;
  }
}

export function setIdentity(identity: Identity): void {
  if (typeof window !== "undefined") {
    window.localStorage.setItem("slpra.identity", JSON.stringify(identity));
  }
}

function identityHeaders(): Record<string, string> {
  const id = getIdentity();
  return { "X-User": id.username, "X-Role": id.role };
}

/** Raised when a write hits an optimistic-concurrency conflict (HTTP 409). */
export class VersionConflictError extends Error {
  status = 409;
  currentVersion: number | null;
  constructor(message: string, currentVersion: number | null) {
    super(message);
    this.name = "VersionConflictError";
    this.currentVersion = currentVersion;
  }
}

async function fetchAPI<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...identityHeaders(),
      ...options?.headers,
    },
    ...options,
  });
  if (!res.ok) {
    const body = await res.text();
    if (res.status === 409) {
      let current: number | null = null;
      let message = body;
      try {
        const parsed = JSON.parse(body);
        const detail = parsed.detail ?? parsed;
        current = detail?.current_version ?? null;
        message = detail?.message ?? body;
      } catch {
        /* keep raw body */
      }
      throw new VersionConflictError(message, current);
    }
    throw new Error(`API ${res.status}: ${body}`);
  }
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

const jsonBody = (data: unknown): RequestInit => ({ body: JSON.stringify(data) });

// Ontology
export const getModules = () => fetchAPI<Module[]>("/api/ontology/modules");
export const getClassHierarchy = (module: string) =>
  fetchAPI<TreeNode[]>(`/api/ontology/${module}/classes`);
export const getClassDetail = (iri: string) =>
  fetchAPI<ClassDetail>(`/api/ontology/classes/${encodeURIComponent(iri)}`);

// Entities
export const searchEntities = (params: Record<string, string>) => {
  const qs = new URLSearchParams(params).toString();
  return fetchAPI<EntitySearchResult>(`/api/entities?${qs}`);
};
export const getEntity = (iri: string) =>
  fetchAPI<Individual>(`/api/entities/${encodeURIComponent(iri)}`);
export const createEntity = (data: CreateEntityRequest) =>
  fetchAPI<Individual>("/api/entities", { method: "POST", body: JSON.stringify(data) });

// Reasoning
export const runAssessment = (data: AssessmentRequest) =>
  fetchAPI<AssessmentResponse>("/api/reasoning/assess", {
    method: "POST", body: JSON.stringify(data),
  });
export const calculatePDE = (data: PDERequest) =>
  fetchAPI<PDEResponse>("/api/reasoning/calculate/pde", {
    method: "POST", body: JSON.stringify(data),
  });
export const calculateMACO = (data: MACORequest) =>
  fetchAPI<MACOResult>("/api/reasoning/calculate/maco", {
    method: "POST", body: JSON.stringify(data),
  });
export const getRules = () => fetchAPI<RuleInfo[]>("/api/reasoning/rules");

// Knowledge Graph
export const getKGStats = () => fetchAPI<KGStats>("/api/kg/stats");
export const getKGGraph = (params?: Record<string, string>) => {
  const qs = params ? `?${new URLSearchParams(params)}` : "";
  return fetchAPI<GraphData>(`/api/kg/graph${qs}`);
};
export const runSPARQL = (query: string) =>
  fetchAPI<Record<string, unknown>[]>("/api/kg/sparql", {
    method: "POST", body: JSON.stringify({ query }),
  });

// Integration
export const getIntegrationSpecs = () =>
  fetchAPI<IntegrationSpec[]>("/api/integration/specs");

// --- Integration realtime (能力三) -----------------------------------------
export interface Connector {
  id: string;
  system_type: string;
  name: string;
  ingest_mode: string;
  poll_interval_seconds: number;
  connection_config: Record<string, unknown> | null;
  field_mapping: Record<string, unknown> | null;
  is_active: boolean;
  last_status: string | null;
  last_error: string | null;
}

export interface MaterializationRun {
  id: string;
  connector_id: string;
  status: string;
  started_at: string;
  finished_at: string | null;
  cursor_from: Record<string, unknown> | null;
  cursor_to: Record<string, unknown> | null;
  change_count: number;
  event_ids: string[] | null;
  error_message: string | null;
}

export interface DashboardData {
  compatibility_matrix: Array<{
    equipment: string | null; product: string | null;
    risk_level: string | null; conclusion_id: string;
  }>;
  schedule_risks: Array<{
    date: string | null; equipment: string | null;
    conflict: boolean; detail: string;
  }>;
  updated_at: string;
}

export interface RuleTrace {
  rules_fired: Array<Record<string, unknown>>;
}

export const listConnectors = () =>
  fetchAPI<Connector[]>("/api/integration/connectors");
export const createConnector = (data: Partial<Connector>) =>
  fetchAPI<Connector>("/api/integration/connectors", {
    method: "POST", body: JSON.stringify(data),
  });
export const deleteConnector = (id: string) =>
  fetchAPI<void>(`/api/integration/connectors/${id}`, { method: "DELETE" });
export const testConnector = (id: string) =>
  fetchAPI<{ ok: boolean; latency_ms: number | null; error: string | null }>(
    `/api/integration/connectors/${id}/test`, { method: "POST" });
export const syncConnector = (id: string) =>
  fetchAPI<{ run_id: string; status: string }>(
    `/api/integration/connectors/${id}/sync`, { method: "POST" });
export const listConnectorRuns = (id: string) =>
  fetchAPI<{ runs: MaterializationRun[] }>(`/api/integration/connectors/${id}/runs`);
export const getDashboard = () =>
  fetchAPI<DashboardData>("/api/integration/dashboard");
export const getConclusionTrace = (id: string) =>
  fetchAPI<RuleTrace>(`/api/reasoning/conclusions/${id}/trace`);

// --- Compliance (能力六) ----------------------------------------------------
export interface PendingConclusion {
  id: string;
  risk_level: string | null;
  execution_type: string;
}

export const verifyAudit = () =>
  fetchAPI<{ ok: boolean; verified_count?: number; head_seq?: number; broken_at_seq?: number }>(
    "/api/compliance/audit/verify");
export const getPendingSignatures = () =>
  fetchAPI<{ conclusions: PendingConclusion[] }>("/api/compliance/signatures/pending");
export const signConclusion = (data: {
  conclusion_id: string; username: string; password: string; meaning: string;
}) =>
  fetchAPI<{ signature_id: string; conclusion_id: string; effective: boolean; signed_at: string }>(
    "/api/compliance/signatures", { method: "POST", body: JSON.stringify(data) });

// --- Extraction (能力二) ----------------------------------------------------
export const listExtractionConfigs = () =>
  fetchAPI<ExtractionConfig[]>("/api/extraction/configs");
export const createExtractionConfig = (data: Partial<ExtractionConfig>) =>
  fetchAPI<ExtractionConfig>("/api/extraction/configs", {
    method: "POST", body: JSON.stringify(data),
  });
export const listExtractionJobs = () =>
  fetchAPI<ExtractionJob[]>("/api/extraction/jobs");
export const getExtractionJob = (id: string) =>
  fetchAPI<ExtractionJob>(`/api/extraction/jobs/${id}`);

export async function createExtractionJob(params: {
  source_type: string; config_id: string; file?: File; db_source?: object;
}): Promise<ExtractionJob> {
  const fd = new FormData();
  fd.append("source_type", params.source_type);
  fd.append("config_id", params.config_id);
  if (params.file) fd.append("file", params.file);
  if (params.db_source) fd.append("db_source", JSON.stringify(params.db_source));
  const res = await fetch(`${API_BASE}/api/extraction/jobs`, {
    method: "POST", headers: identityHeaders(), body: fd,
  });
  if (!res.ok) throw new Error(`API ${res.status}: ${await res.text()}`);
  return res.json();
}

export const getJobCandidates = (jobId: string) =>
  fetchAPI<GroupedCandidates>(`/api/extraction/jobs/${jobId}/candidates`);
export const reviewCandidate = (id: string, status: string, edited?: object) =>
  fetchAPI<ExtractionCandidate>(`/api/extraction/candidates/${id}/review`, {
    method: "PUT", body: JSON.stringify({ status, edited_properties: edited }),
  });
export const mergeCandidates = (target_id: string, source_ids: string[]) =>
  fetchAPI<ExtractionCandidate[]>("/api/extraction/candidates/merge", {
    method: "POST", body: JSON.stringify({ target_id, source_ids }),
  });
export const splitCandidate = (id: string, splits: object[]) =>
  fetchAPI<ExtractionCandidate[]>(`/api/extraction/candidates/${id}/split`, {
    method: "POST", body: JSON.stringify({ splits }),
  });

/** Subscribe to job progress via SSE. Returns an unsubscribe fn. */
export function subscribeJobProgress(
  jobId: string, onEvent: (e: JobProgressEvent) => void,
): () => void {
  const id = getIdentity();
  // EventSource cannot set headers; pass identity as query for the dev gateway.
  const url = `${API_BASE}/api/extraction/jobs/${jobId}/progress?x_user=${id.username}&x_role=${id.role}`;
  const es = new EventSource(url);
  es.onmessage = (ev) => {
    try { onEvent(JSON.parse(ev.data) as JobProgressEvent); } catch { /* ignore */ }
  };
  return () => es.close();
}

// --- Extraction types (能力二) ---------------------------------------------
export interface ExtractionConfig {
  id: string;
  name: string;
  target_class_iri: string;
  source_type: string;
  column_mapping?: Record<string, string> | null;
  llm_prompt_template?: string | null;
  is_active?: boolean;
}
export interface ExtractionJob {
  id: string;
  source_type: string;
  source_filename: string | null;
  status: string;
  total_candidates: number;
  approved_count: number;
  rejected_count: number;
  error_message: string | null;
  created_at: string;
}
export interface ExtractionCandidate {
  id: string;
  target_class_iri: string;
  extracted_properties: Record<string, unknown>;
  candidate_kind: string;
  group_key: string | null;
  is_canonical: boolean;
  source_ref: string | null;
  degraded_reason: string | null;
  merged_into_id: string | null;
  action_conditions: Record<string, unknown> | null;
  alignment_result: string | null;
  aligned_iri: string | null;
  match_score: number | null;
  review_status: string;
  committed_iri: string | null;
}
export interface CandidateGroup {
  group_key: string;
  canonical_candidate_id: string | null;
  candidates: ExtractionCandidate[];
}
export interface GroupedCandidates {
  job_id: string;
  groups: CandidateGroup[];
  ungrouped: ExtractionCandidate[];
}
export interface JobProgressEvent {
  job_id: string;
  stage: string;
  pct: number;
  status: string;
  degraded: boolean;
}

// Types
export interface Module {
  key: string; iri: string; label: string | null;
  class_count: number; individual_count: number;
}
export interface TreeNode {
  iri: string; name: string; label: string | null;
  individual_count: number; children: TreeNode[];
}
export interface ClassDetail {
  iri: string; name: string; label_zh: string | null; label_en: string | null;
  comment: string | null; module: string | null;
  parent_iris: string[]; children_iris: string[];
  individual_count: number;
  object_properties: PropertyInfo[];
  data_properties: PropertyInfo[];
  restrictions: RestrictionInfo[];
}
export interface PropertyInfo { iri: string; name: string; label: string | null; range: string[]; }
export interface RestrictionInfo { property: string; type: string; value?: string; cardinality?: number; }
export interface Individual {
  iri: string; name: string; class_iris: string[];
  label_zh: string | null; label_en: string | null;
  properties: Record<string, unknown>;
}
export interface EntityShadow {
  iri: string; class_iri: string; label_zh: string | null;
  label_en: string | null; module: string; properties_json: Record<string, unknown> | null;
}
export interface EntitySearchResult {
  items: EntityShadow[]; total: number; page: number; page_size: number;
}
export interface CreateEntityRequest { class_iri: string; name: string; properties: Record<string, unknown>; }
export interface AssessmentRequest { drug_iri: string; equipment_iris: string[]; assessment_type?: string; }
export interface AssessmentResponse {
  drug_iri: string; equipment_iris: string[];
  risk_level: string | null; rules_fired: RuleFired[];
  scenarios: ScenarioResult[]; requires_dedication: boolean;
  maco: MACOResult | null; recommendations: string[];
}
export interface RuleFired {
  rule_id: string; rule_group: string; description: string;
  inputs: Record<string, unknown>; conclusion: Record<string, unknown>;
  regulation_ref?: string;
}
export interface ScenarioResult { scenario_iri: string; scenario_name: string; requirements: Record<string, unknown>; }
export interface PDERequest { pod: number; bw?: number; f1?: number; f2?: number; f3?: number; f4?: number; f5?: number; mf?: number; }
export interface PDEResponse { pde_value: number; parameters: Record<string, number>; }
export interface MACORequest { pde?: number; mbs: number; tdd_next: number; min_therapeutic_dose?: number; ld50?: number; route?: string; }
export interface MACOResult { maco_value: number; method_used: string; all_methods: Record<string, number>; unit?: string; }
export interface RuleInfo { rule_id: string; group: string; description: string; regulation_ref?: string; }
export interface KGStats { total_entities: number; by_module: Record<string, number>; by_class: Record<string, number>; }
export interface GraphData { nodes: GraphNode[]; edges: GraphEdge[]; }
export interface GraphNode { id: string; label: string | null; type: string; module: string | null; }
export interface GraphEdge { source: string; target: string; label: string; }
export interface IntegrationSpec { system_type: string; description: string; endpoints: Record<string, string>[]; }

// ===========================================================================
// T-Box 维护工作台（能力一）—— 可编辑元数据 API（契约 §2–§11）
// ===========================================================================

// --- E1 class --------------------------------------------------------------
export interface TBoxRestriction {
  id: string; kind: string; property_iri: string | null;
  property_kind: string | null; filler_iri: string | null;
  cardinality: number | null; version: number; status: string;
}
export interface TBoxMapping {
  id: string; class_iri: string | null; mapping_type: string;
  target: string; source_system: string | null; health: string;
  version: number; status: string;
}
export interface TBoxClass {
  id: string; slpra_iri: string; label: string; comment: string | null;
  module: string | null; parent_iri: string | null; bfo_category: string | null;
  field_schema: Record<string, unknown> | null; status: string; version: number;
  is_reviewed: boolean; is_disabled: boolean; confidence: number | null;
  restrictions: TBoxRestriction[]; mappings: TBoxMapping[];
  created_at: string | null; updated_at: string | null;
}
export interface ClassCreateInput {
  slpra_iri: string; label: string; comment?: string | null;
  module?: string | null; parent_iri?: string | null; bfo_category?: string | null;
}
export interface ClassUpdateInput {
  expected_version: number; label?: string | null; comment?: string | null;
  module?: string | null; parent_iri?: string | null; bfo_category?: string | null;
}

export const createClass = (data: ClassCreateInput) =>
  fetchAPI<TBoxClass>("/api/ontology/classes", { method: "POST", ...jsonBody(data) });
export const updateClass = (iri: string, data: ClassUpdateInput) =>
  fetchAPI<TBoxClass>(`/api/ontology/classes/${encodeURIComponent(iri)}`, {
    method: "PUT", ...jsonBody(data),
  });
export const deleteClass = (iri: string, expectedVersion: number) =>
  fetchAPI<void>(
    `/api/ontology/classes/${encodeURIComponent(iri)}?expected_version=${expectedVersion}`,
    { method: "DELETE" },
  );
export const disableClass = (iri: string, expectedVersion: number) =>
  fetchAPI<TBoxClass>(`/api/ontology/classes/${encodeURIComponent(iri)}/disable`, {
    method: "POST", ...jsonBody({ expected_version: expectedVersion }),
  });
export const reviewClass = (iri: string, expectedVersion: number) =>
  fetchAPI<TBoxClass>(`/api/ontology/classes/${encodeURIComponent(iri)}/review`, {
    method: "POST", ...jsonBody({ expected_version: expectedVersion }),
  });
export const getTBoxClass = (iri: string) =>
  fetchAPI<TBoxClass>(`/api/ontology/classes/${encodeURIComponent(iri)}`);

// --- E2 link type ----------------------------------------------------------
export interface TBoxLinkType {
  id: string; slpra_iri: string; label: string; comment: string | null;
  domain_iri: string | null; range_iri: string | null; inverse_iri: string | null;
  min_cardinality: number | null; max_cardinality: number | null;
  is_functional: boolean; is_symmetric: boolean; is_transitive: boolean;
  status: string; version: number; is_disabled: boolean;
  inherited_from_iri?: string | null; inherited_from_label?: string | null;
}
export interface LinkTypeInput {
  slpra_iri?: string; label?: string; comment?: string | null;
  domain_iri?: string | null; range_iri?: string | null; inverse_iri?: string | null;
  min_cardinality?: number | null; max_cardinality?: number | null;
  is_functional?: boolean; is_symmetric?: boolean; is_transitive?: boolean;
  expected_version?: number;
}
export const listLinkTypes = (domainIri?: string, includeInherited = false) =>
  fetchAPI<TBoxLinkType[]>(`/api/ontology/link-types${propQuery(domainIri, includeInherited)}`);
export const createLinkType = (data: LinkTypeInput) =>
  fetchAPI<TBoxLinkType>("/api/ontology/link-types", { method: "POST", ...jsonBody(data) });
export const updateLinkType = (iri: string, data: LinkTypeInput) =>
  fetchAPI<TBoxLinkType>(`/api/ontology/link-types/${encodeURIComponent(iri)}`, {
    method: "PUT", ...jsonBody(data),
  });
export const deleteLinkType = (iri: string, expectedVersion: number) =>
  fetchAPI<void>(
    `/api/ontology/link-types/${encodeURIComponent(iri)}?expected_version=${expectedVersion}`,
    { method: "DELETE" },
  );

// --- E3 data property ------------------------------------------------------
export interface TBoxDataProperty {
  id: string; slpra_iri: string; label: string; comment: string | null;
  domain_iri: string | null; datatype: string; unit: string | null;
  controlled_vocab: Record<string, unknown> | null;
  status: string; version: number; is_disabled: boolean;
  inherited_from_iri?: string | null; inherited_from_label?: string | null;
}
export interface DataPropertyInput {
  slpra_iri?: string; label?: string; comment?: string | null;
  domain_iri?: string | null; datatype?: string; unit?: string | null;
  controlled_vocab?: Record<string, unknown> | null; expected_version?: number;
}
export interface RiskDataPropertyInput {
  slpra_iri: string; label: string; domain_iri?: string | null;
  datatype?: string; vocab: string;
}
export interface RiskVocabulary { key: string; label: string; values: string[]; }

// Shared query builder for domain-scoped property listings (relations + data props).
const propQuery = (domainIri?: string, includeInherited = false) => {
  if (!domainIri) return "";
  const p = new URLSearchParams({ domain_iri: domainIri });
  if (includeInherited) p.set("include_inherited", "true");
  return `?${p.toString()}`;
};
export const listDataProperties = (domainIri?: string, includeInherited = false) =>
  fetchAPI<TBoxDataProperty[]>(`/api/ontology/data-properties${propQuery(domainIri, includeInherited)}`);
export const createDataProperty = (data: DataPropertyInput) =>
  fetchAPI<TBoxDataProperty>("/api/ontology/data-properties", { method: "POST", ...jsonBody(data) });
export const updateDataProperty = (iri: string, data: DataPropertyInput) =>
  fetchAPI<TBoxDataProperty>(`/api/ontology/data-properties/${encodeURIComponent(iri)}`, {
    method: "PUT", ...jsonBody(data),
  });
export const deleteDataProperty = (iri: string, expectedVersion: number) =>
  fetchAPI<void>(
    `/api/ontology/data-properties/${encodeURIComponent(iri)}?expected_version=${expectedVersion}`,
    { method: "DELETE" },
  );
export const getRiskVocabularies = () =>
  fetchAPI<RiskVocabulary[]>("/api/ontology/risk-vocabularies");
export const createRiskDataProperty = (data: RiskDataPropertyInput) =>
  fetchAPI<TBoxDataProperty>("/api/ontology/data-properties/risk", {
    method: "POST", ...jsonBody(data),
  });

// --- E4 action -------------------------------------------------------------
export interface TBoxAction {
  id: string; slpra_iri: string; label: string; comment: string | null;
  actor_iri: string | null; target_iri: string | null;
  precondition: Record<string, unknown> | null;
  postcondition: Record<string, unknown> | null;
  params: Record<string, unknown> | null;
  status: string; version: number; is_disabled: boolean;
}
export interface ActionInput {
  slpra_iri?: string; label?: string; comment?: string | null;
  actor_iri?: string | null; target_iri?: string | null;
  precondition?: Record<string, unknown> | null;
  postcondition?: Record<string, unknown> | null;
  params?: Record<string, unknown> | null; expected_version?: number;
}
export const getActions = () => fetchAPI<TBoxAction[]>("/api/ontology/actions");
export const createAction = (data: ActionInput) =>
  fetchAPI<TBoxAction>("/api/ontology/actions", { method: "POST", ...jsonBody(data) });
export const updateAction = (iri: string, data: ActionInput) =>
  fetchAPI<TBoxAction>(`/api/ontology/actions/${encodeURIComponent(iri)}`, {
    method: "PUT", ...jsonBody(data),
  });
export const deleteAction = (iri: string, expectedVersion: number) =>
  fetchAPI<void>(
    `/api/ontology/actions/${encodeURIComponent(iri)}?expected_version=${expectedVersion}`,
    { method: "DELETE" },
  );

// --- E5 restriction --------------------------------------------------------
export interface RestrictionInput {
  kind?: string; property_iri?: string | null; property_kind?: string | null;
  filler_iri?: string | null; cardinality?: number | null; expected_version?: number;
}
export const createRestriction = (classIri: string, data: RestrictionInput) =>
  fetchAPI<TBoxRestriction>(
    `/api/ontology/classes/${encodeURIComponent(classIri)}/restrictions`,
    { method: "POST", ...jsonBody(data) },
  );
export const updateRestriction = (id: string, data: RestrictionInput) =>
  fetchAPI<TBoxRestriction>(`/api/ontology/restrictions/${id}`, {
    method: "PUT", ...jsonBody(data),
  });
export const deleteRestriction = (id: string, expectedVersion: number) =>
  fetchAPI<void>(`/api/ontology/restrictions/${id}?expected_version=${expectedVersion}`, {
    method: "DELETE",
  });

// --- E6 mapping + health ---------------------------------------------------
export interface MappingInput {
  mapping_type: string; target: string; source_system?: string | null;
  expected_version?: number;
}
export interface MappingHealth {
  ok: string[]; unmapped: string[]; drift: string[]; orphan: string[];
}
export const getMappings = (classIri: string) =>
  fetchAPI<TBoxMapping[]>(`/api/ontology/classes/${encodeURIComponent(classIri)}/mappings`);
export const createMapping = (classIri: string, data: MappingInput) =>
  fetchAPI<TBoxMapping>(`/api/ontology/classes/${encodeURIComponent(classIri)}/mappings`, {
    method: "POST", ...jsonBody(data),
  });
export const updateMapping = (id: string, data: MappingInput) =>
  fetchAPI<TBoxMapping>(`/api/ontology/mappings/${id}`, { method: "PUT", ...jsonBody(data) });
export const deleteMapping = (id: string, expectedVersion: number) =>
  fetchAPI<void>(`/api/ontology/mappings/${id}?expected_version=${expectedVersion}`, {
    method: "DELETE",
  });
export const getMappingHealth = () =>
  fetchAPI<MappingHealth>("/api/ontology/mappings/health");

// --- §8 validation ---------------------------------------------------------
export interface ValidationIssue { code: string; message: string; entity_iri: string | null; }
export interface ValidationReport {
  blocking: ValidationIssue[]; warnings: ValidationIssue[];
  reasoner: { ran: boolean; consistent: boolean | null; note: string | null };
}
export const validateOntology = () =>
  fetchAPI<ValidationReport>("/api/ontology/validate", { method: "POST" });

// --- §9 import / export / diff ---------------------------------------------
export interface DiffResult {
  turtle_preview: string; triples_added: string[]; triples_removed: string[];
}
export interface ImportResult { added: number; updated: number; conflicts: string[]; }
export const exportTTL = async (): Promise<string> => {
  const res = await fetch(`${API_BASE}/api/ontology/export/ttl`, { headers: identityHeaders() });
  if (!res.ok) throw new Error(`API ${res.status}`);
  return res.text();
};
export const exportDiff = () => fetchAPI<DiffResult>("/api/ontology/export/diff");
export const importTTL = (content: string) =>
  fetchAPI<ImportResult>("/api/ontology/import/ttl", { method: "POST", ...jsonBody({ content }) });

// --- §10 release -----------------------------------------------------------
export interface ReleaseSummary {
  id: string; release_no: string; title: string; status: string;
  ttl_commit_sha: string | null; published_at: string | null; created_at: string | null;
}
export interface ChangeLogItem {
  id: string; entity_table: string; entity_id: string; change_kind: string;
  before: Record<string, unknown> | null; after: Record<string, unknown> | null;
}
export interface ReleaseDetail extends ReleaseSummary {
  ttl_diff: string | null;
  validation_report: ValidationReport | null;
  change_log: ChangeLogItem[];
}
export const getReleases = () => fetchAPI<ReleaseSummary[]>("/api/ontology/releases");
export const getRelease = (id: string) => fetchAPI<ReleaseDetail>(`/api/ontology/releases/${id}`);
export const createRelease = (title: string) =>
  fetchAPI<ReleaseDetail>("/api/ontology/releases", { method: "POST", ...jsonBody({ title }) });
export const submitRelease = (id: string) =>
  fetchAPI<ReleaseDetail>(`/api/ontology/releases/${id}/submit`, { method: "POST" });
export const publishRelease = (id: string) =>
  fetchAPI<ReleaseDetail>(`/api/ontology/releases/${id}/publish`, { method: "POST" });
export const rollbackRelease = (id: string) =>
  fetchAPI<ReleaseDetail>(`/api/ontology/releases/${id}/rollback`, { method: "POST" });

// --- §11 audit -------------------------------------------------------------
export interface AuditEntry {
  id: number; action: string; entity_iri: string | null; actor: string | null;
  release_id: string | null; details: Record<string, unknown> | null; created_at: string | null;
}
export const getAudit = (params?: Record<string, string>) => {
  const qs = params ? `?${new URLSearchParams(params)}` : "";
  return fetchAPI<AuditEntry[]>(`/api/ontology/audit${qs}`);
};
