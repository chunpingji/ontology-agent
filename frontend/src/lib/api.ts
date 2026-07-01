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

// Relation schema (T-Box multi-hop BFS)
export interface RelationSchemaEdge {
  hop: number;
  predicate_iri: string;
  predicate_label: string;
  domain_class_iri: string;
  domain_class_label: string;
  range_class_iri: string;
  range_class_label: string;
  range_subclasses: { iri: string; label: string }[];
  range_data_properties: { iri: string; label: string }[];
}
export const getRelationSchema = (classIri: string, maxHops = 4) =>
  fetchAPI<RelationSchemaEdge[]>(
    `/api/ontology/classes/${encodeURIComponent(classIri)}/relation-schema?max_hops=${maxHops}`,
  );

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
  changes: Array<Record<string, unknown>> | null;
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
/** 向**既有**连接器增量推送已归一化变更骨架（webhook 追加 inline_changes 并即时同步）。 */
export const webhookConnector = (id: string, changes: Array<Record<string, unknown>>) =>
  fetchAPI<{ accepted: boolean }>(`/api/integration/connectors/${id}/webhook`, {
    method: "POST", body: JSON.stringify({ changes }),
  });
export const getDashboard = () =>
  fetchAPI<DashboardData>("/api/integration/dashboard");
export const getConclusionTrace = (id: string) =>
  fetchAPI<RuleTrace>(`/api/reasoning/conclusions/${id}/trace`);

// --- 研发文档事实源 doc_repo（能力三 / 007）--------------------------------
// 复用既有 connector CRUD 与 /api/entities 检索（不新建路由/检索框架）。
export const DOCUMENT_NS = "https://ontology.pharma-gmp.cn/slpra/document/";

/** 研发阶段受控词表（skos:notation 1–6 定序；与 slpra-document.ttl 一一对应）。 */
export const DEVELOPMENT_PHASES: Array<{ iri: string; label: string; notation: string }> = [
  { iri: `${DOCUMENT_NS}Phase_DrugDiscovery`, label: "药物发现", notation: "1" },
  { iri: `${DOCUMENT_NS}Phase_Preclinical`, label: "临床前", notation: "2" },
  { iri: `${DOCUMENT_NS}Phase_ClinicalI`, label: "临床Ⅰ期", notation: "3" },
  { iri: `${DOCUMENT_NS}Phase_ClinicalII_III`, label: "临床Ⅱ/Ⅲ期", notation: "4" },
  { iri: `${DOCUMENT_NS}Phase_NDA_BLA`, label: "NDA/BLA 申报", notation: "5" },
  { iri: `${DOCUMENT_NS}Phase_PostMarket`, label: "上市后", notation: "6" },
];

/** 研发阶段 IRI → 中文标签（未知回退 local-name）。 */
export const phaseLabel = (iri: string | null | undefined): string => {
  if (!iri) return "—";
  const hit = DEVELOPMENT_PHASES.find((p) => p.iri === iri);
  return hit ? hit.label : iri.split(/[#/]/).pop() || iri;
};

/** doc_repo 文档类型 local-name → 中文标签（与 slpra-document.ttl 6 子类一致）。 */
export const DOC_TYPE_LABELS: Record<string, string> = {
  RegulatoryDocument: "法规文档",
  INDDossier: "IND 申报资料",
  TechTransferReport: "技术转移报告",
  ProcessValidationReport: "工艺验证报告",
  StabilityReport: "稳定性报告",
  NDA_BLADossier: "NDA/BLA 申报资料",
  PVReport: "药物警戒报告",
};
export const docTypeLabel = (classIri: string | null | undefined): string => {
  if (!classIri) return "—";
  const ln = classIri.split(/[#/]/).pop() || classIri;
  return DOC_TYPE_LABELS[ln] || ln;
};

export type DocRepoAccessMode = "inline" | "upload" | "http";

export interface DocRepoConnectorInput {
  name: string;
  accessMode: DocRepoAccessMode;
  pollIntervalSeconds?: number;
  /** http 模式：EDMS/eTMF 端点 URL。 */
  baseUrl?: string;
  /** http 模式：凭据**环境变量名**引用（如 "EDMS_TOKEN"）——绝不传明文 token（FR-010）。 */
  tokenRef?: string;
  apiKeyRef?: string;
  /** inline 模式：归一化变更骨架数组。 */
  inlineChanges?: Array<Record<string, unknown>>;
  /** upload 模式：文档上传信封数组。 */
  uploadPayload?: Array<Record<string, unknown>>;
}

/** 据接入模式构建 doc_repo 的 connection_config（凭据**仅以变量名引用**入库，无明文）。 */
export const buildDocRepoConfig = (input: DocRepoConnectorInput): Record<string, unknown> => {
  if (input.accessMode === "http") {
    const cfg: Record<string, unknown> = { access_mode: "http", base_url: input.baseUrl || "" };
    if (input.tokenRef) cfg.token_ref = input.tokenRef;
    if (input.apiKeyRef) cfg.api_key_ref = input.apiKeyRef;
    return cfg;
  }
  if (input.accessMode === "upload") {
    return { access_mode: "upload", upload_payload: input.uploadPayload || [] };
  }
  return { access_mode: "inline", inline_changes: input.inlineChanges || [] };
};

/** 创建 doc_repo 连接器（复用既有 createConnector；system_type 固定 doc_repo）。 */
export const createDocRepoConnector = (input: DocRepoConnectorInput) =>
  createConnector({
    name: input.name || "研发文档事实源",
    system_type: "doc_repo",
    ingest_mode: "poll",
    poll_interval_seconds: input.pollIntervalSeconds ?? 2,
    connection_config: buildDocRepoConfig(input),
  });

/** 仅列出 doc_repo 连接器（客户端过滤；复用 listConnectors）。 */
export const listDocRepoConnectors = () =>
  listConnectors().then((cs) =>
    cs.filter((c) => (c.system_type || "").toLowerCase() === "doc_repo"),
  );

/** 连接器的接入模式（connection_config.access_mode；未知回退 inline）。 */
export const docRepoMode = (c: Connector): DocRepoAccessMode => {
  const m = String(c.connection_config?.access_mode ?? "inline");
  return m === "upload" || m === "http" ? m : "inline";
};

/**
 * 某连接器历史上物化过的文档个体 IRI 集合（facts#<entity_id>）。
 * EntityShadow 不存连接器归属——文档→连接器的唯一回链是各 run 的 applied changes，
 * 故经既有 /runs 端点只读重建归属（无新建后端字段 / 迁移）。
 */
export const connectorDocIris = async (connectorId: string): Promise<string[]> => {
  const { runs } = await listConnectorRuns(connectorId);
  const iris = new Set<string>();
  for (const r of runs) {
    for (const ch of r.changes ?? []) {
      const eid = ch?.entity_id;
      if (typeof eid === "string" && eid) iris.add(`http://slpra.org/facts#${eid}`);
    }
  }
  return [...iris];
};

/** 列出研发文档个体（module=document），可按研发阶段过滤（US3 FR-005）。 */
export const listDocuments = (developmentPhaseIri?: string, pageSize = 100) => {
  const params: Record<string, string> = { module: "document", page_size: String(pageSize) };
  if (developmentPhaseIri) params.development_phase = developmentPhaseIri;
  return searchEntities(params);
};

/** 列出"抽取自"某文档的派生实体（extractedFrom 回链；客户端过滤，复用 /api/entities）。 */
export const listExtractedFrom = async (docIri: string, pageSize = 200): Promise<EntityShadow[]> => {
  const res = await searchEntities({ page_size: String(pageSize) });
  return res.items.filter((e) => (e.properties_json?.extractedFrom as string) === docIri);
};

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

// QA 拒绝（Part 11 重认证 + 原因）→ 既有后端端点 compliance.reject_conclusion。
export interface RejectRequest {
  conclusion_id: string; username: string; password: string; reason: string;
}
export interface RejectResponse {
  conclusion_id: string; lifecycle_state: string; voided_actions: number;
}
export const rejectConclusion = (req: RejectRequest) =>
  fetchAPI<RejectResponse>("/api/compliance/reject", {
    method: "POST", body: JSON.stringify(req),
  });

// 合规审计链（append-only 只读）。注意:与 getAudit()/`/ontology/audit`（本体审计）不同,
// 此处指向 `/compliance/audit`（合规哈希链），勿混用。
export interface ComplianceAuditEntry {
  seq: number | null;
  action: string;
  actor: string | null;
  entity_iri: string | null;
  prev_hash: string | null;
  entry_hash: string | null;
  details: Record<string, unknown> | null;
  created_at: string | null;
}
export interface ComplianceAuditListResponse { entries: ComplianceAuditEntry[]; }
export const getComplianceAudit = (params?: {
  actor?: string; action?: string; entity_iri?: string;
}) => {
  const qs = params
    ? `?${new URLSearchParams(
        Object.entries(params).filter(([, v]) => v) as [string, string][],
      )}`
    : "";
  return fetchAPI<ComplianceAuditListResponse>(`/api/compliance/audit${qs}`);
};

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

// 研发文档内容抽取（007 US2）：由文档个体人工发起 → 入队（pending）→ 手动 start。
// 候选进入既有对齐复核队列，确认后入事实层并携 extractedFrom 溯源回链（FR-004/Q1）。
export interface DocExtractionRequest {
  doc_ref: string; // 文档个体 IRI（facts#…，溯源锚点）
  content_ref: string; // 外部正文引用（按需取，平台不存全文，Q2）
  config_id: string;
}
/** 文档个体 → 创建 pending 的 doc_repo 抽取作业（不自动发起，Q1）。 */
export const enqueueDocumentExtraction = (req: DocExtractionRequest) =>
  fetchAPI<ExtractionJob>("/api/extraction/jobs/from-document", {
    method: "POST", body: JSON.stringify(req),
  });
/** 手动发起待抽取作业（授权角色）：置 running 并运行抽取管线。 */
export const startExtractionJob = (jobId: string) =>
  fetchAPI<ExtractionJob>(`/api/extraction/jobs/${jobId}/start`, { method: "POST" });

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
  document_path: string | null;
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
  annotation_stage?: string;
  pct: number;
  status: string;
  degraded: boolean;
}

export async function pauseAnnotation(jobId: string): Promise<void> {
  await fetch(`${API_BASE}/api/extraction/jobs/${jobId}/annotation/pause`, {
    method: "POST",
    headers: identityHeaders(),
  });
}

export async function resumeAnnotation(jobId: string): Promise<void> {
  await fetch(`${API_BASE}/api/extraction/jobs/${jobId}/annotation/resume`, {
    method: "POST",
    headers: identityHeaders(),
  });
}

export async function rerunAnnotation(jobId: string): Promise<void> {
  await fetch(`${API_BASE}/api/extraction/jobs/${jobId}/annotation/rerun`, {
    method: "POST",
    headers: identityHeaders(),
  });
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

// ===========================================================================
// 声明式规则层 (能力六 / spec 006) — E11/E12/E13 可版本化规则数据 (US3, T041)
//
// 规则即数据：判据阈值 / 决策规则 / 冲突策略全部走与 T-Box 一致的
// fetchAPI + 乐观并发(expected_version) 通道。模式（pattern/antecedent）是
// 受限词汇的 AST——见 RulePattern；前端表单只暴露解释器 VOCABULARY 内的算子。
// ===========================================================================

/** 解释器受限模式 AST 节点（与 backend interpreter.VOCABULARY 对齐）。 */
export type RulePattern =
  | { op: "some_values_from"; property: string; filler_class: string }
  | { op: "class_membership"; property: string; classes: string[] }
  | { op: "datatype_facet"; property: string; cmp: PatternCmp; value: number }
  | { op: "boolean_has_value"; property: string; value: boolean }
  | { op: "external_alignment"; property: string; alignment: string }
  | { op: "class_present"; class: string }
  | { op: "literal_eq"; key: string; value: unknown }
  | { op: "literal_cmp"; key: string; cmp: PatternCmp; value: unknown }
  | { op: "and"; operands: RulePattern[] }
  | { op: "or"; operands: RulePattern[] };

export type PatternCmp = "gt" | "ge" | "lt" | "le" | "eq" | "ne";
/** 受限表单直接暴露的算子（叶子节点，单一谓词/阈值），排除 and/or 复合与底层 literal_*。 */
export const PATTERN_OPS = [
  "datatype_facet",
  "boolean_has_value",
  "class_membership",
  "some_values_from",
  "external_alignment",
  "class_present",
] as const;
export const PATTERN_CMP_OPS: PatternCmp[] = ["gt", "ge", "lt", "le", "eq", "ne"];

// --- E11 分类判据 (充要定义) ------------------------------------------------
export interface TBoxClassificationCriterion {
  id: string; criterion_key: string;
  target_class_iri: string | null; target_class_label: string | null;
  pattern: RulePattern; regulation_ref: string | null; logic_role: string;
  status: string; version: number; is_disabled: boolean;
  created_at: string | null; updated_at: string | null;
}
export interface CriterionCreateInput {
  criterion_key: string; target_class_iri: string; pattern: RulePattern;
  regulation_ref?: string | null; logic_role?: string;
}
export interface CriterionUpdateInput {
  expected_version: number; target_class_iri?: string | null;
  pattern?: RulePattern; regulation_ref?: string | null;
  logic_role?: string | null; is_disabled?: boolean | null;
}
export const listClassificationCriteria = () =>
  fetchAPI<TBoxClassificationCriterion[]>("/api/ontology/classification-criteria");
export const createClassificationCriterion = (data: CriterionCreateInput) =>
  fetchAPI<TBoxClassificationCriterion>("/api/ontology/classification-criteria", {
    method: "POST", ...jsonBody(data),
  });
export const updateClassificationCriterion = (key: string, data: CriterionUpdateInput) =>
  fetchAPI<TBoxClassificationCriterion>(
    `/api/ontology/classification-criteria/${encodeURIComponent(key)}`,
    { method: "PUT", ...jsonBody(data) },
  );
export const deleteClassificationCriterion = (key: string, expectedVersion: number) =>
  fetchAPI<void>(
    `/api/ontology/classification-criteria/${encodeURIComponent(key)}?expected_version=${expectedVersion}`,
    { method: "DELETE" },
  );
export const publishClassificationCriterion = (key: string, expectedVersion: number) =>
  fetchAPI<TBoxClassificationCriterion>(
    `/api/ontology/classification-criteria/${encodeURIComponent(key)}/publish`,
    { method: "POST", ...jsonBody({ expected_version: expectedVersion }) },
  );

// --- E12 决策规则 (产生式 R-ED / R-SC / R-CP) -------------------------------
export type DecisionRuleGroup =
  | "equipment_dedication" | "scenario_identification" | "contamination_risk" | "risk_assessment";
export const DECISION_RULE_GROUPS: DecisionRuleGroup[] = [
  "equipment_dedication", "scenario_identification", "contamination_risk", "risk_assessment",
];
export interface TBoxDecisionRule {
  id: string; slpra_iri: string; rule_key: string; rule_group: DecisionRuleGroup;
  antecedent: RulePattern; consequent: Record<string, unknown>; priority: number;
  regulation_ref: string | null; label: string; comment: string | null;
  status: string; version: number; is_disabled: boolean;
  created_at: string | null; updated_at: string | null;
}
export interface DecisionRuleCreateInput {
  rule_key: string; rule_group: DecisionRuleGroup; antecedent: RulePattern;
  consequent: Record<string, unknown>; priority?: number;
  regulation_ref?: string | null; label?: string | null; comment?: string | null;
}
export interface DecisionRuleUpdateInput {
  expected_version: number; rule_group?: DecisionRuleGroup | null;
  antecedent?: RulePattern; consequent?: Record<string, unknown>;
  priority?: number | null; regulation_ref?: string | null;
  label?: string | null; comment?: string | null; is_disabled?: boolean | null;
}
export const listDecisionRules = (ruleGroup?: DecisionRuleGroup) =>
  fetchAPI<TBoxDecisionRule[]>(
    `/api/ontology/decision-rules${ruleGroup ? `?rule_group=${ruleGroup}` : ""}`,
  );
export const createDecisionRule = (data: DecisionRuleCreateInput) =>
  fetchAPI<TBoxDecisionRule>("/api/ontology/decision-rules", { method: "POST", ...jsonBody(data) });
export const updateDecisionRule = (key: string, data: DecisionRuleUpdateInput) =>
  fetchAPI<TBoxDecisionRule>(`/api/ontology/decision-rules/${encodeURIComponent(key)}`, {
    method: "PUT", ...jsonBody(data),
  });
export const deleteDecisionRule = (key: string, expectedVersion: number) =>
  fetchAPI<void>(
    `/api/ontology/decision-rules/${encodeURIComponent(key)}?expected_version=${expectedVersion}`,
    { method: "DELETE" },
  );
export const publishDecisionRule = (key: string, expectedVersion: number) =>
  fetchAPI<TBoxDecisionRule>(
    `/api/ontology/decision-rules/${encodeURIComponent(key)}/publish`,
    { method: "POST", ...jsonBody({ expected_version: expectedVersion }) },
  );

// --- E13 冲突消解策略 (固定维度集，仅 GET/PUT) ------------------------------
export interface TBoxConflictPolicy {
  id: string; slpra_iri: string; dimension: string; strategy: string;
  priority_lattice: Record<string, number> | null;
  override_direction: string | null; regulation_ref: string | null;
  label: string; comment: string | null;
  status: string; version: number; is_disabled: boolean;
  created_at: string | null; updated_at: string | null;
}
export interface ConflictPolicyUpdateInput {
  expected_version: number; strategy?: string | null;
  priority_lattice?: Record<string, number> | null;
  override_direction?: string | null; regulation_ref?: string | null;
  comment?: string | null; is_disabled?: boolean | null;
}
export const listConflictPolicies = () =>
  fetchAPI<TBoxConflictPolicy[]>("/api/ontology/conflict-policies");
export const getConflictPolicy = (dimension: string) =>
  fetchAPI<TBoxConflictPolicy>(`/api/ontology/conflict-policies/${encodeURIComponent(dimension)}`);
export const updateConflictPolicy = (dimension: string, data: ConflictPolicyUpdateInput) =>
  fetchAPI<TBoxConflictPolicy>(`/api/ontology/conflict-policies/${encodeURIComponent(dimension)}`, {
    method: "PUT", ...jsonBody(data),
  });
export const publishConflictPolicy = (dimension: string, expectedVersion: number) =>
  fetchAPI<TBoxConflictPolicy>(
    `/api/ontology/conflict-policies/${encodeURIComponent(dimension)}/publish`,
    { method: "POST", ...jsonBody({ expected_version: expectedVersion }) },
  );

// ===========================================================================
// 文档标注 + 自动抽取 + 系统配置 + 全类列表 (UI 改进)
// ===========================================================================

export interface PropertyTriple {
  iri: string;
  label: string;
  value: string;
}

export interface EntityTriple {
  entity_text: string;
  entity_class_iri: string;
  entity_class_label: string;
  segment_index: number;
  span_start: number;
  span_end: number;
  properties: PropertyTriple[];
}

// 文档级分类 + 全量关系/属性抽取（仅 Word；规则式、离线）。
export interface DocClassification {
  doc_class_iri: string;
  label: string;
  score: number;
  signals: string[];
}

// 关系边上对象端点回填的数据属性；``iri`` 为 null 表示未匹配到本体数据属性（原文兜底）。
export interface RelationDataProperty {
  iri: string | null;
  label: string;
  value: string;
}

// 子关系（如 合成路线→包含步骤→使用设备/产出中间体），``sub_relationships`` 递归。
export interface SubRelationship {
  predicate_iri: string;
  predicate_label: string;
  object_class_iri: string;
  object_class_label: string;
  object_text: string;
  object_source: string;
  object_data_properties: RelationDataProperty[];
  sub_relationships: SubRelationship[];
  source_ref: string | null;
}

// 顶层对象属性边（主语为文档分类类，如 CMCReport ─describes→ DrugProduct）。
export interface Relationship extends SubRelationship {
  subject_class_iri: string;
  subject_class_label: string;
  subject_text: string;
}

export interface AnnotatedDocument {
  source_type: string;
  filename: string | null;
  content: unknown;
  warnings?: string[];
  triples?: EntityTriple[];
  doc_class?: DocClassification | null;
  relationships?: Relationship[];
}
export const getAnnotatedDocument = (jobId: string) =>
  fetchAPI<AnnotatedDocument>(`/api/extraction/jobs/${jobId}/annotated-document`);

export async function generateRiskReport(jobId: string): Promise<Blob> {
  const res = await fetch(`${API_BASE}/api/extraction/jobs/${jobId}/risk-report`, {
    method: "POST", headers: identityHeaders(),
  });
  if (!res.ok) throw new Error(`API ${res.status}: ${await res.text()}`);
  return res.blob();
}

export async function createAutoExtractionJob(params: {
  file: File; source_type: string; target_class_iris?: string[];
}): Promise<ExtractionJob> {
  const fd = new FormData();
  fd.append("file", params.file);
  fd.append("source_type", params.source_type);
  if (params.target_class_iris) {
    fd.append("target_class_iris", JSON.stringify(params.target_class_iris));
  }
  const res = await fetch(`${API_BASE}/api/extraction/jobs/auto`, {
    method: "POST", headers: identityHeaders(), body: fd,
  });
  if (!res.ok) throw new Error(`API ${res.status}: ${await res.text()}`);
  return res.json();
}

export interface SystemConfigEntry {
  key: string;
  value: unknown;
  updated_at: string | null;
}
export const listSystemConfigs = () =>
  fetchAPI<SystemConfigEntry[]>("/api/system-config");
export const getSystemConfig = (key: string) =>
  fetchAPI<SystemConfigEntry>(`/api/system-config/${encodeURIComponent(key)}`);
export const updateSystemConfig = (key: string, value: unknown) =>
  fetchAPI<SystemConfigEntry>(`/api/system-config/${encodeURIComponent(key)}`, {
    method: "PUT", ...jsonBody({ value }),
  });

export interface OntologyClassFlat {
  iri: string;
  name: string;
  label: string | null;
  module_key: string;
}
export const getAllClasses = () =>
  fetchAPI<OntologyClassFlat[]>("/api/ontology/all-classes");
