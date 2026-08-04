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

// --- Auth token -------------------------------------------------------------
// Real authentication: POST /api/auth/login issues a signed bearer token,
// persisted to localStorage and attached as `Authorization: Bearer` on every
// request. X-User/X-Role are still sent so a dev gateway (auth_required=false)
// keeps working; under enforcement the backend resolves identity from the token.
const TOKEN_KEY = "slpra.token";

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(token: string | null): void {
  if (typeof window === "undefined") return;
  if (token) window.localStorage.setItem(TOKEN_KEY, token);
  else window.localStorage.removeItem(TOKEN_KEY);
}

function identityHeaders(): Record<string, string> {
  const id = getIdentity();
  const headers: Record<string, string> = { "X-User": id.username, "X-Role": id.role };
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;
  return headers;
}

export interface LoginResult {
  token: string;
  username: string;
  role: string;
  display_name?: string | null;
}

/** Authenticate; on success persist token + identity, then return the result. */
export async function login(username: string, password: string): Promise<LoginResult> {
  // 登录端点开放且仅读 body，不需身份头；显式只带 Content-Type。
  const loginHeaders = { "Content-Type": "application/json" };
  const res = await fetch(`${API_BASE}/api/auth/login`, {
    method: "POST",
    headers: loginHeaders,
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) {
    let detail = "用户名或密码错误";
    try {
      const parsed = JSON.parse(await res.text());
      detail = parsed.detail ?? detail;
    } catch {
      /* keep default */
    }
    throw new Error(detail);
  }
  const result = (await res.json()) as LoginResult;
  setToken(result.token);
  setIdentity({ username: result.username, role: result.role });
  return result;
}

/** Clear the local session (best-effort server notify; always clears locally). */
export async function logout(): Promise<void> {
  try {
    await fetch(`${API_BASE}/api/auth/logout`, { method: "POST", headers: identityHeaders() });
  } catch {
    /* ignore network / expired-token errors — local clear is what matters */
  }
  setToken(null);
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
    if (res.status === 401) {
      // 令牌失效/缺失：清本地会话并跳登录（登录页自身不跳，防循环）。
      setToken(null);
      if (typeof window !== "undefined" && window.location.pathname !== "/login") {
        const next = encodeURIComponent(window.location.pathname + window.location.search);
        window.location.replace(`/login?next=${next}`);
      }
      throw new Error("未认证：请重新登录");
    }
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
const MOCK_TRACES: Record<string, RuleTrace> = {
  "concl-pde-ibuprofen-001": {
    rules_fired: [
      { rule: "PDE_ORAL_ROUTE", substance: "布洛芬 (Ibuprofen)", dose: "4 mg/day", route: "口服", LD50: "636 mg/kg", NOAEL: "40 mg/kg/day", safety_factor: 100 },
      { rule: "OEB_CLASSIFICATION", OEB: "OEB-3", basis: "PDE 4.0 mg/day，NOAEL < 50 mg/kg/day" },
    ],
  },
  "concl-maco-aspirin-002": {
    rules_fired: [
      { rule: "MACO_SHARED_EQUIPMENT", substance: "阿司匹林 (Aspirin)", PDE: "4 mg/day", min_batch_size: "100 kg", MACO: "0.04 mg per swab", equipment: "反应釜 R-201" },
      { rule: "CLEANING_LIMIT", residue_limit: "≤ 10 ppm", method: "HPLC-UV", LOQ: "0.5 ppm" },
    ],
  },
  "concl-cleaning-reactor-003": {
    rules_fired: [
      { rule: "CLEANING_VALIDATION", equipment: "反应釜 R-301", product_A: "氯沙坦钾", product_B: "二甲双胍盐酸盐", acceptance: "≤ 10 ppm 残留", swab_locations: 6, rinse_cycles: 3 },
      { rule: "VISUAL_INSPECTION", result: "合格", inspector: "张工 (QC)" },
    ],
  },
  "concl-stability-losartan-004": {
    rules_fired: [
      { rule: "ICH_STABILITY", substance: "氯沙坦钾 (Losartan Potassium)", condition: "40°C/75%RH 加速 6 月", assay: "98.2%", degradation: "< 0.5%", conclusion: "稳定性合格" },
    ],
  },
  "concl-pde-metformin-005": {
    rules_fired: [
      { rule: "PDE_ORAL_ROUTE", substance: "二甲双胍盐酸盐 (Metformin HCl)", dose: "25 mg/day", route: "口服", NOAEL: "250 mg/kg/day", safety_factor: 100 },
      { rule: "OEB_CLASSIFICATION", OEB: "OEB-1", basis: "PDE 25 mg/day，低毒性" },
    ],
  },
  "concl-cross-contam-006": {
    rules_fired: [
      { rule: "CROSS_CONTAMINATION_RISK", shared_line: "固体制剂车间 Line-3", products: "5 品种共线", highest_OEB: "OEB-5", control: "密闭投料 + HEPA + 独立空调" },
      { rule: "RISK_MATRIX", severity: 5, probability: 2, RPN: 10, action: "需 QA 逐批放行审核" },
    ],
  },
  "concl-maco-omeprazole-007": {
    rules_fired: [
      { rule: "MACO_SHARED_EQUIPMENT", substance: "奥美拉唑 (Omeprazole)", PDE: "2.4 mg/day", min_batch_size: "50 kg", MACO: "0.048 mg per swab", equipment: "混合机 M-102" },
    ],
  },
};

export const getConclusionTrace = (id: string) =>
  fetchAPI<RuleTrace>(`/api/reasoning/conclusions/${id}/trace`).catch(
    () => MOCK_TRACES[id] ?? { rules_fired: [] },
  );

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

/** 研发阶段中文标签 → IRI（左侧选中的阶段分类 → 上传信封 development_phase）。 */
export const phaseIriByLabel = (label: string | null | undefined): string | null => {
  if (!label) return null;
  return DEVELOPMENT_PHASES.find((p) => p.label === label)?.iri ?? null;
};

/** drug-development 命名空间（RegulatoryDocument 更细分子类所在，非 /document/）。 */
export const DRUG_DEV_NS = "https://ontology.pharma-gmp.cn/slpra/drug-development/";

export interface DocTypeOption {
  /** 类 local-name（上传信封 doc_type，即 doc_type_to_class 覆盖的键）。 */
  localName: string;
  /** 完整类 IRI（下发 doc_type_to_class 覆盖 → 物化为此文档类）。 */
  iri: string;
  label: string;
}

/**
 * 受控文档类型 = 本体中 `RegulatoryDocument` 的全部子类（上传后由用户指定）。
 * 分组与标签逐字取自本体 TTL：`slpra-document.ttl`（/document/ 命名空间 6 子类）
 * 与 `slpra-drug-development.ttl`（/drug-development/ 命名空间 23 子类，按研发阶段分章）。
 * 后端经连接器 `doc_type_to_class` 覆盖 + 强制 module=document 物化（跨命名空间无碍）。
 */
export const DOCUMENT_TYPE_GROUPS: Array<{ group: string; options: DocTypeOption[] }> = [
  {
    group: "通用法规文档",
    options: [
      { localName: "RegulatoryDocument", iri: `${DOCUMENT_NS}RegulatoryDocument`, label: "法规文档" },
      { localName: "INDDossier", iri: `${DOCUMENT_NS}INDDossier`, label: "IND 申报资料" },
      { localName: "TechTransferReport", iri: `${DOCUMENT_NS}TechTransferReport`, label: "技术转移报告" },
      { localName: "ProcessValidationReport", iri: `${DOCUMENT_NS}ProcessValidationReport`, label: "工艺验证报告" },
      { localName: "StabilityReport", iri: `${DOCUMENT_NS}StabilityReport`, label: "稳定性报告" },
      { localName: "NDA_BLADossier", iri: `${DOCUMENT_NS}NDA_BLADossier`, label: "NDA/BLA 申报资料" },
      { localName: "PVReport", iri: `${DOCUMENT_NS}PVReport`, label: "药物警戒报告" },
    ],
  },
  {
    group: "发现与临床前",
    options: [
      { localName: "TargetValidationReport", iri: `${DRUG_DEV_NS}TargetValidationReport`, label: "靶点验证报告" },
      { localName: "HTSReport", iri: `${DRUG_DEV_NS}HTSReport`, label: "高通量筛选报告" },
      { localName: "LeadOptimizationReport", iri: `${DRUG_DEV_NS}LeadOptimizationReport`, label: "先导化合物优化报告" },
      { localName: "CandidateSelectionReport", iri: `${DRUG_DEV_NS}CandidateSelectionReport`, label: "候选药物遴选报告" },
      { localName: "PharmacologyStudyReport", iri: `${DRUG_DEV_NS}PharmacologyStudyReport`, label: "药理学研究报告" },
      { localName: "PKStudyReport", iri: `${DRUG_DEV_NS}PKStudyReport`, label: "药代动力学研究报告" },
      { localName: "ToxicologyStudyReport", iri: `${DRUG_DEV_NS}ToxicologyStudyReport`, label: "毒理学研究报告" },
      { localName: "CMCReport", iri: `${DRUG_DEV_NS}CMCReport`, label: "CMC 报告" },
    ],
  },
  {
    group: "临床",
    options: [
      { localName: "InvestigatorBrochure", iri: `${DRUG_DEV_NS}InvestigatorBrochure`, label: "研究者手册（IB）" },
      { localName: "ClinicalTrialProtocol", iri: `${DRUG_DEV_NS}ClinicalTrialProtocol`, label: "临床试验方案" },
      { localName: "InformedConsentForm", iri: `${DRUG_DEV_NS}InformedConsentForm`, label: "知情同意书（ICF）" },
      { localName: "ClinicalStudyReport", iri: `${DRUG_DEV_NS}ClinicalStudyReport`, label: "临床试验报告（CSR）" },
      { localName: "ClinicalOverview", iri: `${DRUG_DEV_NS}ClinicalOverview`, label: "临床概述" },
      { localName: "ClinicalSummary", iri: `${DRUG_DEV_NS}ClinicalSummary`, label: "临床总结" },
    ],
  },
  {
    group: "NDA/BLA（CTD 模块）",
    options: [
      { localName: "CTDModule1", iri: `${DRUG_DEV_NS}CTDModule1`, label: "CTD 模块1（行政信息）" },
      { localName: "CTDModule2", iri: `${DRUG_DEV_NS}CTDModule2`, label: "CTD 模块2（综述）" },
      { localName: "CTDModule3", iri: `${DRUG_DEV_NS}CTDModule3`, label: "CTD 模块3（药学）" },
      { localName: "CTDModule4", iri: `${DRUG_DEV_NS}CTDModule4`, label: "CTD 模块4（非临床）" },
      { localName: "CTDModule5", iri: `${DRUG_DEV_NS}CTDModule5`, label: "CTD 模块5（临床）" },
    ],
  },
  {
    group: "上市后",
    options: [
      { localName: "PSUR", iri: `${DRUG_DEV_NS}PSUR`, label: "定期安全性更新报告（PSUR/PBRER）" },
      { localName: "ADRReport", iri: `${DRUG_DEV_NS}ADRReport`, label: "药品不良反应报告" },
      { localName: "RMPDocument", iri: `${DRUG_DEV_NS}RMPDocument`, label: "风险管理计划文档" },
      { localName: "PostApprovalChangeApplication", iri: `${DRUG_DEV_NS}PostApprovalChangeApplication`, label: "上市后变更申请" },
    ],
  },
];

const _ALL_DOC_TYPES: DocTypeOption[] = DOCUMENT_TYPE_GROUPS.flatMap((g) => g.options);

/** 扁平：文档类型 local-name → 中文标签（docTypeLabel 用；覆盖两命名空间全部子类）。 */
export const DOC_TYPE_LABELS: Record<string, string> = Object.fromEntries(
  _ALL_DOC_TYPES.map((o) => [o.localName, o.label]),
);

/** 扁平：文档类型 local-name → 完整类 IRI（上传时下发 doc_type_to_class 覆盖用）。 */
export const DOC_TYPE_CLASS_IRI: Record<string, string> = Object.fromEntries(
  _ALL_DOC_TYPES.map((o) => [o.localName, o.iri]),
);

/** 默认文档类型（上传类型选择器的初始值）。 */
export const DEFAULT_DOC_TYPE = "RegulatoryDocument";

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
  /** 字段映射覆盖（如 doc_type_to_class：文档类型 local-name → 完整文档类 IRI）。 */
  fieldMapping?: Record<string, unknown>;
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
    ...(input.fieldMapping ? { field_mapping: input.fieldMapping } : {}),
  });

// --- 研发文档上传（报告中心 · reports）------------------------------------
// 复用 doc_repo `upload` 接入模式：构造**标准上传信封**（doc_id/doc_type/version/
// title/metadata，与后端 `_normalize_upload` 契约逐字段一致）→ 建连接器 → **立即触发
// 一次同步**（创建不自动物化、轮询器默认关闭）。同步返回后，文档已作为托管文档个体
// （facts#<doc_id>，class=document/<docType>）落库，可经 listDocuments 检索、按类型归类。
//
// 关键：文档个体 IRI 可**预测**（FACTS_NS + doc_id）——前端据此在上传瞬间乐观占位、
// 并在后端列出后按 key 去重对账，无需臆造上传端点，也不受列表刷新时序影响。

/** A-Box 事实个体命名空间（须与后端 materializer `_FACT_BASE_IRI` 保持一致）。 */
export const FACTS_NS = "http://slpra.org/facts#";

/** 一次待上传文件的已构造信封 + 预测坐标（供乐观占位/对账）。 */
export interface PreparedUpload {
  docId: string;
  /** 预测的文档个体 IRI（= FACTS_NS + docId）。 */
  iri: string;
  title: string;
  /** 用户指定的文档类型 local-name（DOC_TYPE_CLASS_IRI 的键）。 */
  docType: string;
  /** 该文档类型的完整类 IRI（下发 doc_type_to_class 覆盖 → 决定物化文档类）。 */
  classIri: string;
  /** 左侧选中的研发阶段 IRI（落 metadata.hasDevelopmentPhase → 按阶段归类）；未选为 null。 */
  phaseIri: string | null;
  size: number;
  /** 原始文件对象（供 submitUpload 把字节送入抽取管线生成标注/实体）。 */
  file: File;
  /** 标注管线源类型（word/excel）；其他类型为 null → 不生成在线预览/实体。 */
  sourceType: string | null;
  /** 提交给后端的上传信封（doc_repo `_normalize_upload` 契约）。 */
  envelope: Record<string, unknown>;
}

/** 文件扩展名 → 标注管线源类型；仅 word/excel 可标注（生成 TipTap 预览 + 识别实体）。 */
function annotationSourceType(name: string): string | null {
  const ext = name.toLowerCase().split(".").pop() || "";
  if (ext === "docx" || ext === "doc") return "word";
  if (ext === "xlsx" || ext === "xls") return "excel";
  return null;
}

function newDocId(): string {
  const rand =
    typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
      ? crypto.randomUUID()
      : `${Date.now().toString(36)}-${Math.floor(Math.random() * 1e9).toString(36)}`;
  return `upload-${rand}`;
}

/**
 * 纯函数：把一个待上传文件构造为标准上传信封 + 预测坐标（不发起任何请求）。
 * `docType`（用户上传后指定）须为 DOC_TYPE_CLASS_IRI 的键，其决定 class_iri → 类型列；
 * `phaseIri`（左侧选中的研发阶段）落 metadata.hasDevelopmentPhase → 阶段归类（文件夹）
 * 且与后端 `hasDevelopmentPhase`（阶段检索/继承的规范谓词）对齐。
 */
export function prepareUpload(
  file: File,
  opts: { docType: string; phaseIri?: string | null },
): PreparedUpload {
  const docId = newDocId();
  const stamp = new Date().toISOString();
  const docType = opts.docType;
  const phaseIri = opts.phaseIri ?? null;
  const classIri = DOC_TYPE_CLASS_IRI[docType] || `${DOCUMENT_NS}${docType}`;
  const metadata: Record<string, unknown> = {
    created_at: stamp,
    ingested_at: stamp,
    content_type: file.type || "application/octet-stream",
    file_size: file.size,
    approvalStatus: "approved",
    sourceSystem: "web-upload",
  };
  // 研发阶段 → 文档属性 → 按阶段归类；键用规范谓词 hasDevelopmentPhase（与后端
  // search_entities 阶段过滤 + _document_phase 继承同键，避免历史 development_phase 漂移）。
  if (phaseIri) metadata.hasDevelopmentPhase = phaseIri;
  return {
    docId,
    iri: `${FACTS_NS}${docId}`,
    title: file.name,
    docType,
    classIri,
    phaseIri,
    size: file.size,
    file,
    sourceType: annotationSourceType(file.name),
    envelope: {
      doc_id: docId,
      doc_type: docType,
      version: 1,
      title: file.name,
      metadata,
    },
  };
}

/**
 * 提交已构造的上传信封：
 *   1) 可标注文件（word/excel）先把字节送入抽取管线（`/jobs/auto`），约束到用户所选
 *      文档类型（doc_class_iri）→ 生成 TipTap 标注内容 + 识别实体；把返回的 job_id 回写
 *      进上传信封 metadata，物化后文档个体即携带 job 引用（详情页据此解析预览 + 关联实体）。
 *   2) 建 doc_repo（upload 模式）连接器 → **立即触发一次同步**，物化为可检索文档个体。
 * 同步为同步调用（run_sync 落库后才返回），故本函数 resolve 时文档已可被 listDocuments 检索。
 * 抽取任务创建失败不阻断入库（预览/实体降级为不可用，文档仍可见、可归类）。
 */
export async function submitUpload(prepared: PreparedUpload[]): Promise<void> {
  if (prepared.length === 0) return;

  for (const p of prepared) {
    if (!p.sourceType) continue;  // 非 word/excel：不走标注管线（无在线预览/实体）
    try {
      const job = await createAutoExtractionJob({
        file: p.file,
        source_type: p.sourceType,
        doc_class_iri: p.classIri,  // 用户所选文档类型 → 约束 NER 候选类
      });
      const meta = (p.envelope.metadata ?? {}) as Record<string, unknown>;
      meta.job_id = job.id;  // → 物化落 properties_json.job_id → resolveDocumentContent 解析预览
      p.envelope.metadata = meta;
    } catch {
      // 抽取失败不阻断文档入库；预览与关联实体优雅降级为不可用。
    }
  }

  const names = prepared.map((p) => p.title).join("、");
  // 为所选文档类型下发 doc_type_to_class 覆盖（localName → 完整类 IRI）：使非默认命名空间
  // （drug-development）的 RegulatoryDocument 子类也能正确物化并强制归 module=document。
  const docTypeToClass: Record<string, string> = {};
  for (const p of prepared) docTypeToClass[p.docType] = p.classIri;
  const connector = await createDocRepoConnector({
    name: `文档上传：${names}`.slice(0, 120),
    accessMode: "upload",
    uploadPayload: prepared.map((p) => p.envelope),
    fieldMapping: { doc_type_to_class: docTypeToClass },
  });
  // 创建连接器不自动物化（轮询器默认关闭）——显式触发一次同步，立即物化为文档个体。
  await syncConnector(connector.id);
}

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

// --- 通用 REST/JSON 源连接器 rest_api（014 US2）----------------------------
// 声明驱动抽取的 api_endpoint 源经此连接器分页拉取；凭据**仅以环境变量名引用**入库
// （FR-006），base_url 须为内网地址（FR-022，后端 422 兜底校验）。

export type RestAuthScheme = "bearer" | "api_key";
export type RestPaginationStyle = "cursor" | "offset" | "page";

export interface RestApiConnectorInput {
  name: string;
  /** 内网 base_url（公网/云端将被后端拒绝，FR-022）。 */
  baseUrl: string;
  endpoint: string;
  authScheme: RestAuthScheme;
  /** 凭据**环境变量名**引用（bearer→token_env / api_key→api_key_env）——绝不传明文（FR-006）。 */
  tokenEnv?: string;
  apiKeyEnv?: string;
  /** api_key 模式的自定义请求头名（默认 X-API-Key）。 */
  apiKeyHeader?: string;
  paginationStyle?: RestPaginationStyle;
  cursorPath?: string;
  pageSize?: number;
  pollIntervalSeconds?: number;
}

/** 据输入构建 rest_api 的 connection_config（凭据**仅以变量名引用**，无明文入库）。 */
export const buildRestApiConfig = (input: RestApiConnectorInput): Record<string, unknown> => {
  const auth: Record<string, unknown> = { scheme: input.authScheme };
  if (input.authScheme === "bearer" && input.tokenEnv) auth.token_env = input.tokenEnv;
  if (input.authScheme === "api_key") {
    if (input.apiKeyEnv) auth.api_key_env = input.apiKeyEnv;
    if (input.apiKeyHeader) auth.header = input.apiKeyHeader;
  }
  const style = input.paginationStyle ?? "cursor";
  const pagination: Record<string, unknown> = { style, page_size: input.pageSize ?? 200 };
  if (style === "cursor") pagination.cursor_path = input.cursorPath || "$.next";
  return {
    base_url: input.baseUrl,
    endpoint: input.endpoint,
    auth,
    pagination,
  };
};

/** 创建 rest_api 连接器（复用既有 createConnector；system_type 固定 rest_api）。 */
export const createRestApiConnector = (input: RestApiConnectorInput) =>
  createConnector({
    name: input.name || "内网 REST 源",
    system_type: "rest_api",
    ingest_mode: "poll",
    poll_interval_seconds: input.pollIntervalSeconds ?? 2,
    connection_config: buildRestApiConfig(input),
  });

/** 仅列出 rest_api 连接器（客户端过滤；复用 listConnectors）。 */
export const listRestApiConnectors = () =>
  listConnectors().then((cs) =>
    cs.filter((c) => (c.system_type || "").toLowerCase() === "rest_api"),
  );

// --- 数据库源连接器 database ------------------------------------------------

export interface DatabaseConnectorInput {
  name: string;
  /** 数据库 DSN 的**环境变量名**（如 SOURCE_DB_DSN）——绝不传明文连接串（FR-006）。 */
  dsnEnv: string;
  /** 可选：仅反射指定 schema。 */
  schema?: string;
  /** 可选：仅反射指定表。 */
  includeTables?: string[];
  pollIntervalSeconds?: number;
}

export const buildDatabaseConfig = (
  input: DatabaseConnectorInput,
): Record<string, unknown> => {
  const cfg: Record<string, unknown> = { dsn_env: input.dsnEnv };
  if (input.schema) cfg.schema = input.schema;
  if (input.includeTables?.length) cfg.include_tables = input.includeTables;
  return cfg;
};

/** 创建 database 连接器（复用既有 createConnector；system_type 固定 database）。 */
export const createDatabaseConnector = (input: DatabaseConnectorInput) =>
  createConnector({
    name: input.name || "数据库源",
    system_type: "database",
    ingest_mode: "poll",
    poll_interval_seconds: input.pollIntervalSeconds ?? 2,
    connection_config: buildDatabaseConfig(input),
  });

/** 仅列出 database 连接器（客户端过滤；复用 listConnectors）。 */
export const listDatabaseConnectors = () =>
  listConnectors().then((cs) =>
    cs.filter((c) => (c.system_type || "").toLowerCase() === "database"),
  );

// --- 文件源连接器 file （Excel / Word / PDF）-------------------------------

export type FileFormat = "excel" | "word" | "pdf";

export interface FileConnectorInput {
  name: string;
  /** 文件绝对路径或 glob 模式（如 /data/imports/*.xlsx）。 */
  filePath: string;
  /** 文件格式（留空则按扩展名自动推断）。 */
  format?: FileFormat;
  /** Excel 工作表名（可选，默认第一个）。 */
  sheetName?: string;
  pollIntervalSeconds?: number;
}

export const buildFileConfig = (
  input: FileConnectorInput,
): Record<string, unknown> => {
  const cfg: Record<string, unknown> = { file_path: input.filePath };
  if (input.format) cfg.format = input.format;
  if (input.sheetName) cfg.sheet_name = input.sheetName;
  return cfg;
};

/** 创建 file 连接器（复用既有 createConnector；system_type 固定 file）。 */
export const createFileConnector = (input: FileConnectorInput) =>
  createConnector({
    name: input.name || "文件源",
    system_type: "file",
    ingest_mode: "poll",
    poll_interval_seconds: input.pollIntervalSeconds ?? 2,
    connection_config: buildFileConfig(input),
  });

/** 仅列出 file 连接器（客户端过滤；复用 listConnectors）。 */
export const listFileConnectors = () =>
  listConnectors().then((cs) =>
    cs.filter((c) => (c.system_type || "").toLowerCase() === "file"),
  );

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
export const deleteDocument = (iri: string) =>
  fetchAPI<void>(`/api/entities/${encodeURIComponent(iri)}`, { method: "DELETE" });

/** 上传文档 → 抽取任务 id 的候选键（`submitUpload` 落 `job_id`；兼容历史别名）。 */
const DOCUMENT_JOB_KEYS = [
  "job_id", "jobId", "source_job_id", "extraction_job_id", "hasJob", "sourceJob",
] as const;

/**
 * 解析某研发文档个体关联的抽取任务 id（上传时 `submitUpload` 落入
 * `properties_json.job_id`）。用于「文档 → 生成风险评估报告 / 在线预览」等按 jobId
 * 取数的场景。取不到（文档未走标注管线，或字段缺失）时返回 null，调用方据此优雅降级。
 */
export async function resolveDocumentJobId(iri: string): Promise<string | null> {
  if (!iri) return null;
  const docs = await listDocuments();
  const shadow = docs.items.find((d) => d.iri === iri);
  const props = shadow?.properties_json ?? {};
  for (const key of DOCUMENT_JOB_KEYS) {
    const value = props[key];
    if (typeof value === "string" && value) return value;
  }
  return null;
}

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
const MOCK_AUDIT_ENTRIES: ComplianceAuditEntry[] = [
  { seq: 1, action: "conclusion_created", actor: "推理引擎", entity_iri: "concl-pde-ibuprofen-001", prev_hash: null, entry_hash: "a1b2c3d4", details: { type: "PDE 计算", substance: "布洛芬" }, created_at: "2026-07-01 09:15:32" },
  { seq: 2, action: "submitted_for_review", actor: "李明 (高级分析师)", entity_iri: "concl-pde-ibuprofen-001", prev_hash: "a1b2c3d4", entry_hash: "e5f6a7b8", details: { comment: "PDE 计算完成，提交 QA 审批" }, created_at: "2026-07-01 10:22:05" },
  { seq: 3, action: "conclusion_created", actor: "推理引擎", entity_iri: "concl-maco-aspirin-002", prev_hash: "e5f6a7b8", entry_hash: "c9d0e1f2", details: { type: "MACO 计算", equipment: "反应釜 R-201" }, created_at: "2026-07-01 14:08:47" },
  { seq: 4, action: "submitted_for_review", actor: "王芳 (高级分析师)", entity_iri: "concl-maco-aspirin-002", prev_hash: "c9d0e1f2", entry_hash: "a3b4c5d6", details: { comment: "MACO 结果已核实" }, created_at: "2026-07-01 15:30:12" },
  { seq: 5, action: "conclusion_created", actor: "推理引擎", entity_iri: "concl-cleaning-reactor-003", prev_hash: "a3b4c5d6", entry_hash: "e7f8a9b0", details: { type: "清洁验证", equipment: "反应釜 R-301" }, created_at: "2026-07-02 08:45:20" },
  { seq: 6, action: "submitted_for_review", actor: "张工 (操作员)", entity_iri: "concl-cleaning-reactor-003", prev_hash: "e7f8a9b0", entry_hash: "c1d2e3f4", details: { comment: "目视检查合格，提交 QA" }, created_at: "2026-07-02 09:10:55" },
  { seq: 7, action: "conclusion_created", actor: "推理引擎", entity_iri: "concl-stability-losartan-004", prev_hash: "c1d2e3f4", entry_hash: "a5b6c7d8", details: { type: "稳定性评估", substance: "氯沙坦钾" }, created_at: "2026-07-02 11:20:33" },
  { seq: 8, action: "conclusion_created", actor: "推理引擎", entity_iri: "concl-cross-contam-006", prev_hash: "a5b6c7d8", entry_hash: "e9f0a1b2", details: { type: "交叉污染风险", line: "Line-3" }, created_at: "2026-07-03 08:00:15" },
];

export const getComplianceAudit = (params?: {
  actor?: string; action?: string; entity_iri?: string;
}) => {
  const qs = params
    ? `?${new URLSearchParams(
        Object.entries(params).filter(([, v]) => v) as [string, string][],
      )}`
    : "";
  return fetchAPI<ComplianceAuditListResponse>(`/api/compliance/audit${qs}`).catch(
    () => ({ entries: MOCK_AUDIT_ENTRIES }),
  );
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
  // EventSource cannot set headers; pass identity + token as query. 强制态下
  // 后端 get_current_user_sse 认 ?token=；未强制时 x_user/x_role 走网关路径。
  const token = getToken();
  const tokenQs = token ? `&token=${encodeURIComponent(token)}` : "";
  const url = `${API_BASE}/api/extraction/jobs/${jobId}/progress?x_user=${id.username}&x_role=${id.role}${tokenQs}`;
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
  const response = await fetch(`${API_BASE}/api/extraction/jobs/${jobId}/annotation/rerun`, {
    method: "POST",
    headers: identityHeaders(),
  });
  if (!response.ok) throw new Error(`API ${response.status}: ${await response.text()}`);
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

// --- E6b property bindings (014 declaration-driven mapping) -----------------
/** Source-entity mapping types whose columns/fields drive extraction. */
export const SOURCE_ENTITY_MAPPING_TYPES = ["db_table", "api_endpoint", "doc_pattern"];
export interface PropertyBinding {
  id: string; class_mapping_id: string; property_iri: string;
  property_kind: string; source_path: string;
  transform_type: string; transform_config: Record<string, unknown> | null;
  is_identifier: boolean; is_label: boolean;
  object_resolution: string | null; target_class_iri: string | null;
  target_id_path: string | null; nested_binding_id: string | null;
  version: number; status: string;
}
export interface PropertyBindingInput {
  property_iri: string; property_kind?: string; source_path: string;
  transform_type?: string; transform_config?: Record<string, unknown> | null;
  is_identifier?: boolean; is_label?: boolean;
  object_resolution?: string | null; target_class_iri?: string | null;
  target_id_path?: string | null; nested_binding_id?: string | null;
  expected_version?: number;
}
export interface BindingValidationReport {
  health: string; errors: ValidationIssue[]; warnings: ValidationIssue[];
}
export const getPropertyBindings = (mappingId: string) =>
  fetchAPI<PropertyBinding[]>(`/api/ontology/mappings/${mappingId}/property-bindings`);
export const createPropertyBinding = (mappingId: string, data: PropertyBindingInput) =>
  fetchAPI<PropertyBinding>(`/api/ontology/mappings/${mappingId}/property-bindings`, {
    method: "POST", ...jsonBody(data),
  });
export const updatePropertyBinding = (id: string, data: PropertyBindingInput) =>
  fetchAPI<PropertyBinding>(`/api/ontology/property-bindings/${id}`, {
    method: "PUT", ...jsonBody(data),
  });
export const deletePropertyBinding = (id: string, expectedVersion: number) =>
  fetchAPI<void>(`/api/ontology/property-bindings/${id}?expected_version=${expectedVersion}`, {
    method: "DELETE",
  });
export const validateBinding = (mappingId: string) =>
  fetchAPI<BindingValidationReport>(`/api/ontology/mappings/${mappingId}/validate`, {
    method: "POST",
  });

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

/** 右侧「关联信息」面板的识别实体（按 类+文本 去重、累计出现次数）。 */
export interface RecognizedEntity {
  text: string;
  classIri: string;
  classLabel: string;
  count: number;
}

/** 标注三元组 → 去重后的识别实体列表（同一 类+文本 合并、累计次数），供关联信息面板渲染。 */
export function entitiesFromTriples(triples: EntityTriple[]): RecognizedEntity[] {
  const byKey = new Map<string, RecognizedEntity>();
  for (const t of triples) {
    const text = (t.entity_text || "").trim();
    if (!text) continue;
    const key = `${t.entity_class_iri}::${text}`;
    const existing = byKey.get(key);
    if (existing) {
      existing.count += 1;
    } else {
      byKey.set(key, {
        text,
        classIri: t.entity_class_iri,
        classLabel: t.entity_class_label || (t.entity_class_iri.split(/[#/]/).pop() ?? t.entity_class_iri),
        count: 1,
      });
    }
  }
  return Array.from(byKey.values());
}

// 文档级分类 + 全量关系/属性抽取（仅 Word；规则式、离线）。
export interface DocClassification {
  doc_class_iri: string;
  label: string;
  score: number;
  signals: string[];
  source?: string;
}

// 关系边上对象端点回填的数据属性；``iri`` 为 null 表示未匹配到本体数据属性（原文兜底）。
export interface RelationDataProperty {
  iri: string | null;
  label: string;
  value: unknown;
}

/**
 * Document Profile locators preserve structural coordinates so the frontend can
 * navigate to the exact section, paragraph, table row, or table cell.
 */
export interface StructuredRelationSourceRef {
  kind?: string;
  section?: string;
  heading_index?: number | null;
  paragraph_index?: number | null;
  table?: number;
  row?: number;
  column?: number | null;
  header?: string;
  key?: string;
  parameter?: string;
  system?: string;
  entity?: string;
  record?: unknown;
  location?: unknown;
  [key: string]: unknown;
}

export type RelationSourceRef = string | StructuredRelationSourceRef;

// CMCReport 共线评估端点上的「推导 PDE vs 原文 PDE」冲突（确定性推导管线产出，供人工裁决）。
export interface PdeConflict {
  conflict_key: string;
  asserted: { pde_mg_day: number; pde_ug_day: number; band: number };
  derived: {
    band: number;
    band_point: number | null;
    pde_ug_day: number | null;
    oel_ug_m3: number | null;
    provisional: boolean;
    input_source: string;
    // 可复现审计记录（method/formula/inputs/factors/intermediate/bands/provisional）。
    provenance: Record<string, unknown>;
  };
  delta_bands: number;
  summary: string;
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
  source_ref: RelationSourceRef | null;
  // 仅 CMCReport 共线评估端点可能携带；命中「推导 vs 原文」PDE 冲突时下发（人工裁决）。
  conflict?: PdeConflict | null;
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
export const getAnnotatedDocument = (jobId: string, refresh = false) =>
  fetchAPI<AnnotatedDocument>(
    `/api/extraction/jobs/${jobId}/annotated-document${refresh ? "?refresh=1" : ""}`,
  );

// CMCReport PDE 冲突的人工决策：采纳推导 / 采纳原文 / 待复核。以 (job_id, conflict_key) 唯一，
// version 乐观并发（写入用 expected_version，冲突 → 409 VersionConflictError）。
export type PdeDecisionChoice = "derived" | "asserted" | "pending";

export interface PdeConflictDecision {
  job_id: string;
  conflict_key: string;
  chosen: PdeDecisionChoice;
  note: string;
  actor: string;
  version: number;
  decided_at: string | null;
}

export const getPdeConflictDecision = (jobId: string) =>
  fetchAPI<PdeConflictDecision>(`/api/extraction/jobs/${jobId}/pde-conflict/decision`);

export const decidePdeConflict = (
  jobId: string,
  body: { chosen: PdeDecisionChoice; note?: string; expected_version: number },
) =>
  fetchAPI<PdeConflictDecision>(`/api/extraction/jobs/${jobId}/pde-conflict/decision`, {
    method: "POST",
    body: JSON.stringify(body),
  });

export async function generateRiskReport(
  jobId: string,
): Promise<Blob | { report_id: string; status: string }> {
  const res = await fetch(`${API_BASE}/api/extraction/jobs/${jobId}/risk-report`, {
    method: "POST", headers: identityHeaders(),
  });
  if (!res.ok) throw new Error(`API ${res.status}: ${await res.text()}`);
  const ct = res.headers.get("content-type") || "";
  if (ct.includes("application/json")) {
    return res.json() as Promise<{ report_id: string; status: string }>;
  }
  return res.blob();
}

// 011 AST Coverage

export interface SlotCoverageDTO {
  slot_id: string;
  label: string;
  status: string;
  source_kind: string;
  value?: string | null;
  source_ref?: string | null;
  rule_key?: string | null;
  hazid?: string | null;
  note?: string | null;
  source_span?: string | null;
  is_llm_sourced?: boolean;
}

export interface GroupCoverageDTO {
  group_id: string;
  title: string;
  kind: string;
  slots: SlotCoverageDTO[];
  is_dynamic?: boolean;
}

export interface SectionCoverageDTO {
  section_id: string;
  title: string;
  groups: GroupCoverageDTO[];
}

export interface ASTCoverageDTO {
  template_id: string;
  template_name?: string;
  template_version?: string;
  total_slots: number;
  filled: number;
  inferred: number;
  missing_required: number;
  blank_optional: number;
  manual: number;
  dismissed: number;
  sections: SectionCoverageDTO[];
}

export const getAstCoverage = (jobId: string, templateId?: string) =>
  fetchAPI<ASTCoverageDTO>(
    `/api/extraction/jobs/${jobId}/ast-coverage${templateId ? `?template_id=${templateId}` : ""}`,
  );

// 015: per-section 行文 narrative prose, generated at report time and surfaced
// in the web reading pane. `subject_description`/`conclusion` are non-null only
// when LLM-generated (deterministic values stay out); `sections` is one entry
// per leaf section that carries a 行文 Prompt.
export interface ReportSectionNarrativeDTO {
  section_id: string;
  title: string;
  text: string;
}

export interface ReportNarrativesDTO {
  subject_description: string | null;
  conclusion: string | null;
  sections: ReportSectionNarrativeDTO[];
}

export interface GeneratedReportDTO {
  id: string;
  job_id: string;
  report_type: string;
  file_path: string;
  file_size: number | null;
  rules_fired_count: number;
  rules_summary: Record<string, unknown> | null;
  actor: string;
  created_at: string;
  // 015: present on the status-poll response; null for legacy/LLM-off reports.
  narratives?: ReportNarrativesDTO | null;
}

export const listReports = (jobId: string) =>
  fetchAPI<GeneratedReportDTO[]>(`/api/extraction/jobs/${jobId}/reports`);
export const deleteReport = (jobId: string, reportId: string) =>
  fetchAPI<void>(`/api/extraction/jobs/${jobId}/reports/${reportId}`, { method: "DELETE" });

export async function dismissSlot(jobId: string, slotId: string): Promise<ASTCoverageDTO> {
  return fetchAPI<ASTCoverageDTO>(`/api/extraction/jobs/${jobId}/ast-coverage/dismiss`, {
    method: "POST", ...jsonBody({ slot_id: slotId }),
  });
}

export async function undismissSlot(jobId: string, slotId: string): Promise<ASTCoverageDTO> {
  return fetchAPI<ASTCoverageDTO>(
    `/api/extraction/jobs/${jobId}/ast-coverage/dismiss/${encodeURIComponent(slotId)}`,
    { method: "DELETE" },
  );
}

export async function downloadReport(jobId: string, reportId: string): Promise<Blob> {
  const res = await fetch(
    `${API_BASE}/api/extraction/jobs/${jobId}/reports/${reportId}/download`,
    { headers: identityHeaders() },
  );
  if (!res.ok) throw new Error(`API ${res.status}: ${await res.text()}`);
  return res.blob();
}

export async function createAutoExtractionJob(params: {
  file: File; source_type: string; target_class_iris?: string[]; doc_class_iri?: string;
}): Promise<ExtractionJob> {
  const fd = new FormData();
  fd.append("file", params.file);
  fd.append("source_type", params.source_type);
  if (params.target_class_iris) {
    fd.append("target_class_iris", JSON.stringify(params.target_class_iris));
  }
  // 用户所选文档类型 → 后端据此约束 NER 候选类（相关类子图，定向识别）。
  if (params.doc_class_iri) {
    fd.append("doc_class_iri", params.doc_class_iri);
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

// ===========================================================================
// 012 AST Template Management
// ===========================================================================

// tiptap/ProseMirror 文档 JSON（忠于原文结构的样例内容，供 WordViewer 渲染）。
export type TiptapContent = Record<string, unknown>;

// 015: lifecycle status + iri_pattern (functional doc-class resolution key,
// replaces the retired DocumentTypeMapping).
export type AstTemplateStatus = "draft" | "published" | "archived";

export interface AstTemplateDTO {
  id: string;
  name: string;
  version: string;
  doc_no: string | null;
  iri_pattern: string | null;
  status: AstTemplateStatus;
  slot_count: number;
  is_default: boolean;
  created_by: string | null;
  owner: string | null; // 015 责任人（业务负责人，区别于 created_by 创建者）
  sample_docx_filename: string | null;
  default_source_filename: string | null;
  default_source_job_id: string | null;
  created_at: string;
  updated_at: string | null;
}

export interface AstTemplateCreateInput {
  name: string;
  version?: string;
  doc_no?: string | null;
  iri_pattern?: string | null; // 015: doc-class resolution key
  schema_json: Record<string, unknown>;
  sample_text?: string | null;
  // 013: 忠于原文结构的 tiptap 样例——持久化后重新编辑时也能忠实预览。
  sample_content_json?: TiptapContent | null;
}

export interface AstTemplateUpdateInput {
  schema_json: Record<string, unknown>;
  version?: string | null;
}

// 015: in-place metadata edit — no version bump. 基本信息 tab edits
// name/doc_no/owner/status; the list-page ⋮ menu edits status/iri_pattern.
export interface AstTemplateMetaUpdateInput {
  name?: string;
  doc_no?: string | null;
  owner?: string | null;
  status?: AstTemplateStatus;
  iri_pattern?: string | null;
}

// 015 训练数据：源文档→评估报告 成对样例（report 可缺省）。
export interface TrainingPairDTO {
  id: string;
  source_filename: string;
  report_filename: string | null;
  created_at: string;
}

export interface TemplateMatchDTO {
  template_id: string;
  template_name: string;
  template_version: string;
  match_source: "iri_pattern" | "default" | "fallback";
}

export const fetchAstTemplates = () =>
  fetchAPI<AstTemplateDTO[]>("/api/ast-templates");
export const getAstTemplate = (id: string) =>
  fetchAPI<
    AstTemplateDTO & {
      schema_json: Record<string, unknown>;
      sample_text: string | null;
      sample_content_json: TiptapContent | null;
      training_pairs: TrainingPairDTO[];
      versions: { id: string; version: string; created_at: string }[];
    }
  >(`/api/ast-templates/${id}`);
export const createAstTemplate = (data: AstTemplateCreateInput) =>
  fetchAPI<AstTemplateDTO>("/api/ast-templates", { method: "POST", ...jsonBody(data) });
export const updateAstTemplate = (id: string, data: AstTemplateUpdateInput) =>
  fetchAPI<AstTemplateDTO>(`/api/ast-templates/${id}`, { method: "PUT", ...jsonBody(data) });
export const deleteAstTemplate = (id: string) =>
  fetchAPI<void>(`/api/ast-templates/${id}`, { method: "DELETE" });
export const setDefaultTemplate = (id: string) =>
  fetchAPI<AstTemplateDTO>(`/api/ast-templates/${id}/set-default`, { method: "POST" });
// 015: PATCH in-place metadata (status / iri_pattern) without a version bump.
export const updateAstTemplateMeta = (id: string, data: AstTemplateMetaUpdateInput) =>
  fetchAPI<AstTemplateDTO>(`/api/ast-templates/${id}`, { method: "PATCH", ...jsonBody(data) });
export const matchTemplateForJob = (jobId: string) =>
  fetchAPI<TemplateMatchDTO>(`/api/ast-templates/match/${jobId}`);

// 013: 后台把样例 DOCX 解析为忠于原文结构的 tiptap（不扁平化成文本），前端据此
// 忠实预览并回传结构化内容做 AI 分析——避免「解析成文本→送前台→送回」丢结构。
export interface ParseSampleResult {
  content_json: TiptapContent;
  plain_text: string;
}

export async function parseSample(file: File): Promise<ParseSampleResult> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(`${API_BASE}/api/ast-templates/parse-sample`, {
    method: "POST",
    headers: identityHeaders(),
    body: form,
  });
  if (!res.ok) throw new Error(`API ${res.status}: ${await res.text()}`);
  return (await res.json()) as ParseSampleResult;
}

// 015 基本信息 tab：默认示例文档替换 / 默认源文件 / 训练数据（源文档→评估报告）。
// 均为 multipart，复用 identityHeaders()（勿手设 Content-Type，交给浏览器带 boundary）。
async function postMultipart<T>(path: string, form: FormData, method = "POST"): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method,
    headers: identityHeaders(),
    body: form,
  });
  if (!res.ok) throw new Error(`API ${res.status}: ${await res.text()}`);
  return (await res.json()) as T;
}

// 替换默认模板示例文档（固化输出 section/格式）；返回解析后的忠实预览内容。
export async function uploadTemplateSample(
  id: string,
  file: File,
): Promise<ParseSampleResult> {
  const form = new FormData();
  form.append("file", file);
  return postMultipart<ParseSampleResult>(`/api/ast-templates/${id}/sample`, form);
}

// 默认源文件（固化输出格式的参照原件）上传 / 清除。
export async function uploadDefaultSource(id: string, file: File): Promise<AstTemplateDTO> {
  const form = new FormData();
  form.append("file", file);
  return postMultipart<AstTemplateDTO>(`/api/ast-templates/${id}/default-source`, form);
}
export const deleteDefaultSource = (id: string) =>
  fetchAPI<AstTemplateDTO>(`/api/ast-templates/${id}/default-source`, { method: "DELETE" });

// 训练数据 CRUD：源文档必填，评估报告可缺省。
export const listTrainingPairs = (id: string) =>
  fetchAPI<TrainingPairDTO[]>(`/api/ast-templates/${id}/training-pairs`);
export async function uploadTrainingPair(
  id: string,
  source: File,
  report?: File | null,
): Promise<TrainingPairDTO> {
  const form = new FormData();
  form.append("source_file", source);
  if (report) form.append("report_file", report);
  return postMultipart<TrainingPairDTO>(`/api/ast-templates/${id}/training-pairs`, form);
}
export const deleteTrainingPair = (id: string, pairId: string) =>
  fetchAPI<void>(`/api/ast-templates/${id}/training-pairs/${pairId}`, { method: "DELETE" });

// --------------------------------------------------------------------------- //
// 013 LLM Template Design Assist + Report Enhancement
// --------------------------------------------------------------------------- //

// 016: section-level ontology coverage (mirrors backend OntologyRelationBinding /
// FactSourceBinding / CoverageDeclaration). All IRIs are class/predicate TYPES —
// never a sample individual (FR-003 / SC-002).
export interface OntologyRelationBinding {
  kind: "ontology_relation";
  doc_class_iri: string;
  predicate_iri: string;
  range_class_iri: string;
  required: boolean; // default true (FR-005a)
  required_properties?: string[];
  label?: string;
}
export interface FactSourceBinding {
  kind: "fact_source";
  source: string;
  selector?: string;
  label?: string;
}
export type CoverageBinding = OntologyRelationBinding | FactSourceBinding;

// 016+: 语义化插槽来源。报告生成时本地 LLM 融合 (1) prompt（作者设定，留空=继承
// 本节 Section.prompt）与 (2) 关联本体（本节 coverage 关系图谱 + 事实源事实）合成
// 插槽正文。本身不存绑定数据——是 Section.prompt + Section.coverage 的投影。镜像后端
// SemanticSource（backend/app/services/reporting/ast_template.py）。
export interface SemanticSource {
  kind: "semantic";
  prompt?: string | null; // null → 继承 Section.prompt
  coverage_refs?: string[]; // coverageKey 过滤器；[] → 投影本节全部 coverage
}

// coverageKey：与后端 ast_template.coverage_key 逐字节一致。语义化插槽的
// coverage_refs 以此键选择本节的 coverage 绑定；清单里合成位点的 slot_id 亦是此键。
// _short = IRI 末段（最后一个 # 或 / 之后）；无分隔符时原样返回。
export function coverageKey(binding: CoverageBinding): string {
  const short = (iri: string): string => {
    const parts = (iri || "").split(/[#/]/).filter(Boolean);
    return parts.length ? parts[parts.length - 1] : iri;
  };
  if (binding.kind === "fact_source") {
    return `coverage.fact_source.${short(binding.source)}`;
  }
  return `coverage.${short(binding.predicate_iri)}__${short(binding.range_class_iri)}`;
}

export interface SuggestSlotsRequest {
  job_id?: string | null;
  document_text?: string | null;
  // 013: 结构化样例（tiptap）——首选输入，服务端派生 LLM 文本与 source_ref 锚点。
  sample_content_json?: TiptapContent | null;
  existing_template?: Record<string, unknown> | null;
  max_suggestions?: number;
  // 016 (D10): document entity type grounds ontology coverage; augments a source.
  doc_class_iri?: string | null;
}

// AI Round-1 结构骨架（后端逐字回传，形状 = slot_suggester._ROUND1_SCHEMA）。无本体
// IRI 绑定——编辑器把每个 candidate 物化为可作者填写的 semantic 槽。
export interface AiStructureCandidate {
  label: string;
  evidence_span?: string;
  evidence_offset?: number;
}
export interface AiStructureGroup {
  title: string;
  candidates: AiStructureCandidate[];
}
export interface AiStructureSection {
  title: string;
  groups: AiStructureGroup[];
}

// AI 分析输出：文档结构骨架（sections）+ 本体覆盖边（coverage）。
export interface SuggestSlotsResponse {
  document_summary: string;
  // 016: ontology-grounded coverage only (US1). 无法绑定到菜单关系边的位点静默忽略，
  // 不再返回 unresolved_candidates（取代 FR-008a）。
  coverage: CoverageBinding[];
  // Round-1 结构骨架；空模板首次分析时物化为分节 + 语义化槽（见 template-slot-editor
  // 的 materializeSkeleton）。带 IRI 绑定的旧 llm_extraction 取数流不随此字段回归。
  sections?: AiStructureSection[];
}

export const suggestSlots = (data: SuggestSlotsRequest) =>
  fetchAPI<SuggestSlotsResponse>("/api/ast-templates/suggest-slots", {
    method: "POST",
    ...jsonBody(data),
  });

// 016：候选文档类型中「已建模可覆盖关系」（≥1 条 hop-1 边）的子集。作者化 UI 据此
// 门控「关联文档类型」下拉——仅启用已建模类型（当前仅 CMC 报告，本体补充关系后自动扩大）。
export interface CoverageDocClassesResponse {
  capable: string[];
}
export const getCoverageDocClasses = (docClassIris: string[]) =>
  fetchAPI<CoverageDocClassesResponse>(
    "/api/ast-templates/coverage-doc-classes",
    { method: "POST", ...jsonBody({ doc_class_iris: docClassIris }) },
  );

// 015: design-time — derive a reusable 行文 Prompt for one section from the
// sample + the section's slot labels. Gated identically to suggest-slots.
export interface GenerateSectionPromptRequest {
  section_title: string;
  slot_labels?: string[];
  sample_text?: string;
}

export const generateSectionPrompt = (data: GenerateSectionPromptRequest) =>
  fetchAPI<{ prompt: string }>("/api/ast-templates/generate-section-prompt", {
    method: "POST",
    ...jsonBody(data),
  });

// 015+: preview the prose one section's (possibly-unsaved) 行文 Prompt produces,
// from a matched document's REAL extracted facts (same path as the report). Needs
// a source job associated in the「源文档」tab; gated identically to suggest-slots.
export interface PreviewSectionNarrativeRequest {
  job_id: string;
  template_id: string;
  section_id: string;
  prompt: string;
}

export const previewSectionNarrative = (
  data: PreviewSectionNarrativeRequest,
) =>
  fetchAPI<{ narrative: string }>(
    "/api/ast-templates/preview-section-narrative",
    { method: "POST", ...jsonBody(data) },
  );

// 013: Async report generation (when LLM enhancement flags are on)

export interface ReportJobStatus {
  report_id: string;
  status: string;
  error_message?: string | null;
}

export const startReportGeneration = (
  jobId: string,
  opts?: { template_id?: string; dismissed_slot_ids?: string[] },
) =>
  fetchAPI<ReportJobStatus>(`/api/extraction/jobs/${jobId}/reports`, {
    method: "POST",
    ...jsonBody(opts ?? {}),
  });

export const pollReportStatus = (jobId: string, reportId: string) =>
  fetchAPI<GeneratedReportDTO & { report_status?: string; report_error?: string }>(
    `/api/extraction/jobs/${jobId}/reports/${reportId}`,
  );

export async function downloadReportById(
  jobId: string,
  reportId: string,
): Promise<Blob> {
  const res = await fetch(
    `${API_BASE}/api/extraction/jobs/${jobId}/reports/${reportId}/download`,
    { headers: identityHeaders() },
  );
  if (!res.ok) throw new Error(`API ${res.status}: ${await res.text()}`);
  return res.blob();
}

/**
 * 通过（后端按文档类别解析的）模板生成风险评估报告，返回 .docx blob。
 *
 * 统一封装同步与异步两条后端路径，屏蔽差异供调用方只拿最终 blob：
 *   · LLM 增强关闭：`POST /risk-report` 直接回 docx（Blob），原样返回；
 *   · LLM 增强开启：先回 `{report_id}`，此处轮询 `pollReportStatus` 至 completed
 *     后再 `downloadReportById` 取件。
 * 生成失败 / 轮询超时抛错，交由调用方提示。两条路径最终都走 `render_risk_report`
 * 的模板分节渲染（resolve_template → RiskReportGenerator(template=…)）。
 */
export async function generateRiskReportBlob(
  jobId: string,
  opts?: { pollIntervalMs?: number; maxAttempts?: number },
): Promise<Blob> {
  const response = await generateRiskReport(jobId);
  if (response instanceof Blob) return response;

  const reportId = response.report_id;
  const interval = opts?.pollIntervalMs ?? 2000;
  const maxAttempts = opts?.maxAttempts ?? 60;
  for (let attempt = 0; attempt < maxAttempts; attempt++) {
    await new Promise((resolve) => setTimeout(resolve, interval));
    const status = await pollReportStatus(jobId, reportId);
    if (status.report_status === "completed") {
      return downloadReportById(jobId, reportId);
    }
    if (status.report_status === "failed") {
      throw new Error(status.report_error || "报告生成失败");
    }
  }
  throw new Error("报告生成超时，请稍后重试");
}

// ===========================================================================
// 015 read-only UI composition helpers (NO new endpoint — FR-027).
// Each helper is a pure projection over the existing API surface, consumed by
// the new operational pages (Connector / Approval / Report Center).
// ===========================================================================

// --- T032 Connector status mapping (US2 · FR-012 · research R4/R5) ----------
/**
 * Semantic connection status a Connector card renders. Derived from the
 * backend's `last_status` / `last_error`; air-gap-from-public-cloud is a
 * NORMAL state (never fabricated as an error) — only a genuinely failed run /
 * `last_error` yields `failed`.
 */
export type ConnectorStatus = "connected" | "connecting" | "failed" | "not-connected";

export function connectorStatus(c: Pick<Connector, "last_status" | "last_error">): ConnectorStatus {
  if (c.last_error) return "failed";
  const s = (c.last_status ?? "").toLowerCase();
  if (!s) return "not-connected";
  if (["failed", "error", "unreachable"].some((k) => s.includes(k))) return "failed";
  if (["running", "syncing", "connecting", "in_progress", "pending"].some((k) => s.includes(k)))
    return "connecting";
  if (["ok", "success", "connected", "succeeded", "synced", "completed"].some((k) => s.includes(k)))
    return "connected";
  // Unknown non-empty status → treat as connected-with-caveat rather than error.
  return "connected";
}

/** zh-CN label + which semantic token family a status uses (success/warning/destructive/muted). */
export const CONNECTOR_STATUS_META: Record<
  ConnectorStatus,
  { label: string; tone: "success" | "warning" | "destructive" | "muted" }
> = {
  connected: { label: "已连接", tone: "success" },
  connecting: { label: "连接中", tone: "warning" },
  failed: { label: "连接失败", tone: "destructive" },
  "not-connected": { label: "未连接", tone: "muted" },
};

/** Group a flat connector list by `system_type` (FR-010), preserving order. */
export function groupConnectorsByType(connectors: Connector[]): Array<{ type: string; connectors: Connector[] }> {
  const order: string[] = [];
  const buckets = new Map<string, Connector[]>();
  for (const c of connectors) {
    const key = c.system_type || "other";
    if (!buckets.has(key)) {
      buckets.set(key, []);
      order.push(key);
    }
    buckets.get(key)!.push(c);
  }
  return order.map((type) => ({ type, connectors: buckets.get(type)! }));
}

// --- T047 Approval queue mapping (US4 · FR-017 · research R3) ----------------
export type ApprovalUrgency = "high" | "medium" | "low";

export interface ApprovalDocument {
  name: string;
  format: string;
}

export interface ApprovalTimelineEntry {
  action: string;
  actor: string;
  time: string;
}

export interface SystemRecommendation {
  action: "approve" | "reject";
  reason: string;
  pdeAnalysis?: {
    reportedPDE: string;
    calculatedPDE: string;
    noael: string;
    noaelSource: string;
    bodyWeight: number;
    factors: Record<string, number>;
    factorLabels: Record<string, string>;
    oebReported: string;
    oebCalculated: string;
    deviation: string;
  };
}

export interface ApprovalTask {
  id: string;
  title: string;
  referenceNo: string;
  category: string;
  type: string;
  urgency: ApprovalUrgency;
  submitter: string;
  submittedAt: string;
  submittedLabel: string;
  entityName: string;
  entityType: string;
  phase: string;
  status: "pending" | "completed";
  unread?: boolean;
  documents: ApprovalDocument[];
  timeline: ApprovalTimelineEntry[];
  systemRecommendation?: SystemRecommendation;
  risk_level: string | null;
  execution_type: string;
}

export const APPROVAL_CATEGORIES = [
  "临床前研究审批",
  "IND 申报审批",
  "临床试验审批",
  "NDA 注册审批",
  "GMP 生产审批",
  "药理毒理变更审批",
  "药物警戒审批",
] as const;

const MOCK_APPROVAL_TASKS: ApprovalTask[] = [
  {
    id: "APR-2026-0618",
    title: "原料药 HRS-5678 临床备样生产信息-风险评估",
    referenceNo: "QS-A-020F05-001",
    category: "临床前研究审批",
    type: "非临床安全性评价",
    urgency: "high",
    submitter: "李婷",
    submittedAt: "2026-07-03T08:24:00",
    submittedLabel: "2 小时前提交",
    entityName: "化合物 HRS-5678 (ActiveIngredient)",
    entityType: "ActiveIngredient",
    phase: "临床前研究 — 毒理学评价",
    status: "pending",
    unread: true,
    risk_level: "Band 5",
    execution_type: "非临床安全性评价",
    documents: [
      { name: "原料药 HRS-5678 临床备样生产信息.docx", format: "DOCX" },
      { name: "遗传毒性试验总结报告.docx", format: "DOCX" },
      { name: "安全药理学评价数据.xlsx", format: "XLSX" },
    ],
    timeline: [
      { action: "提交申请", actor: "李婷", time: "07-03 10:24" },
    ],
    systemRecommendation: {
      action: "reject",
      reason: "报告 PDE 值与系统推算结果偏差 18,000 倍，OEB 等级应为 Band 5（极高危害），疑似安全系数遗漏或 NOAEL 引用错误，建议驳回。",
      pdeAnalysis: {
        reportedPDE: "180 mg/day",
        calculatedPDE: "10 μg/day",
        noael: "0.5 mg/kg/day",
        noaelSource: "28天重复给药毒性试验（大鼠）",
        bodyWeight: 50,
        factors: { F1: 5, F2: 10, F3: 5, F4: 1, F5: 10 },
        factorLabels: {
          F1: "种属差异（大鼠→人）",
          F2: "个体差异",
          F3: "亚慢性→慢性外推",
          F4: "无严重毒性（NOAEL 可用）",
          F5: "遗传毒性关注",
        },
        oebReported: "Band 2（报告隐含）",
        oebCalculated: "Band 5（极高危害）",
        deviation: "18,000 倍",
      },
    },
  },
  {
    id: "APR-2026-0615",
    title: "API 工艺验证批记录审核",
    referenceNo: "APR-2026-0615",
    category: "GMP 生产审批",
    type: "原料药工艺审批",
    urgency: "medium",
    submitter: "王强",
    submittedAt: "2026-07-02T14:30:00",
    submittedLabel: "昨天 14:30",
    entityName: "XR-7742 API 工艺 (ManufacturingProcess)",
    entityType: "ManufacturingProcess",
    phase: "工艺验证 — 批记录审核",
    status: "pending",
    risk_level: null,
    execution_type: "原料药工艺审批",
    documents: [
      { name: "工艺验证批记录-批号2026B003.pdf", format: "PDF" },
      { name: "中间体检测报告.xlsx", format: "XLSX" },
    ],
    timeline: [
      { action: "提交申请", actor: "王强", time: "07-02 14:30" },
    ],
  },
  {
    id: "APR-2026-0612",
    title: "IND 申请 CTD 模块三审核",
    referenceNo: "APR-2026-0612",
    category: "IND 申报审批",
    type: "IND 申请文件审批",
    urgency: "high",
    submitter: "赵燕",
    submittedAt: "2026-06-30T09:15:00",
    submittedLabel: "06-30",
    entityName: "XR-7742 IND 申请 (RegulatorySubmission)",
    entityType: "RegulatorySubmission",
    phase: "IND 申报 — CTD 文件编制",
    status: "pending",
    risk_level: null,
    execution_type: "IND 申请文件审批",
    documents: [
      { name: "CTD 模块三品质文件.pdf", format: "PDF" },
      { name: "药学研究资料汇总.docx", format: "DOCX" },
    ],
    timeline: [
      { action: "提交申请", actor: "赵燕", time: "06-30 09:15" },
    ],
  },
  {
    id: "APR-2026-0610",
    title: "药理学研究方案变更审批",
    referenceNo: "APR-2026-0610",
    category: "药理毒理变更审批",
    type: "药理毒理变更",
    urgency: "low",
    submitter: "张明",
    submittedAt: "2026-06-28T16:00:00",
    submittedLabel: "06-28",
    entityName: "XR-7742 药理研究 (PharmacologyStudy)",
    entityType: "PharmacologyStudy",
    phase: "药理学评价 — 方案变更",
    status: "pending",
    risk_level: null,
    execution_type: "药理毒理变更",
    documents: [
      { name: "药理学研究方案变更申请表.pdf", format: "PDF" },
    ],
    timeline: [
      { action: "提交申请", actor: "张明", time: "06-28 16:00" },
    ],
  },
  {
    id: "APR-2026-0608",
    title: "化合物 XR-7742 安全药理学评价",
    referenceNo: "APR-2026-0608",
    category: "临床前研究审批",
    type: "安全药理学评价",
    urgency: "medium",
    submitter: "陈工",
    submittedAt: "2026-06-27T11:00:00",
    submittedLabel: "06-27",
    entityName: "化合物 XR-7742 (ActiveIngredient)",
    entityType: "ActiveIngredient",
    phase: "临床前研究 — 安全药理学",
    status: "pending",
    risk_level: "Band 3",
    execution_type: "安全药理学评价",
    documents: [
      { name: "安全药理学研究报告.pdf", format: "PDF" },
      { name: "心血管安全性评价数据.xlsx", format: "XLSX" },
    ],
    timeline: [
      { action: "提交申请", actor: "陈工", time: "06-27 11:00" },
    ],
  },
];

export async function getApprovalTasks(): Promise<ApprovalTask[]> {
  return MOCK_APPROVAL_TASKS;
}

export function approvalCategoryCounts(tasks: ApprovalTask[]): Array<{ name: string; count: number }> {
  const counts = new Map<string, number>();
  for (const cat of APPROVAL_CATEGORIES) counts.set(cat, 0);
  for (const t of tasks) counts.set(t.category, (counts.get(t.category) ?? 0) + 1);
  return Array.from(counts.entries()).map(([name, count]) => ({ name, count }));
}

// --- T056 Report + Document unified aggregation (US5 · FR-021 · research R2) -
export type ReportKind = "generated-report" | "uploaded-document";

/** Normalized unified item rendered by Report Center / opened in Report Detail. */
export interface ReportOrDocument {
  /** Stable UI key: report id or document iri. */
  key: string;
  kind: ReportKind;
  title: string;
  category: string;
  /** Format/type label (report_type, or document type). */
  type: string;
  date: string | null;
  size: number | null;
  /** Generated-report coordinates (present only for kind==="generated-report"). */
  jobId?: string;
  reportId?: string;
  /** Document coordinates (present only for kind==="uploaded-document"). */
  iri?: string;
  /** Processing status — "processing" for optimistic uploads not yet in backend. */
  status?: "processing" | "ready";
}

export interface ReportCenterResult {
  items: ReportOrDocument[];
  /** True when the generated-report fan-out was bounded (UI shows "load more"). */
  truncated: boolean;
  jobsScanned: number;
  totalJobs: number;
}

/**
 * Compose the unified Report Center list client-side (research R2 — NO new
 * backend). Documents come from the global `listDocuments()`; generated reports
 * are aggregated via a **bounded** fan-out of `listReports(jobId)` over
 * `listExtractionJobs()`. The fan-out is explicitly capped by `maxJobs` and the
 * result reports `truncated` + `jobsScanned`/`totalJobs` so the UI can offer a
 * visible "load more" — never a silent truncation.
 */
export async function listReportCenterItems(
  opts: { maxJobs?: number } = {},
): Promise<ReportCenterResult> {
  const maxJobs = opts.maxJobs ?? 25;

  // Documents (global, single call).
  const docItems: ReportOrDocument[] = [];
  try {
    const docs = await listDocuments();
    for (const d of docs.items) {
      const phase = (d.properties_json?.hasDevelopmentPhase as string) ?? null;
      docItems.push({
        key: d.iri,
        kind: "uploaded-document",
        title: d.label_zh || d.label_en || d.iri.split("/").pop() || d.iri,
        // 文件夹（category）= 研发阶段（左侧分类轴）；类型（type）= 文档类，列展示。
        category: phase ? phaseLabel(phase) : "未分阶段",
        type: docTypeLabel(d.class_iri) || "文档",
        date: (d.properties_json?.created_at as string) ?? (d.properties_json?.ingested_at as string) ?? null,
        size: null,
        iri: d.iri,
      });
    }
  } catch {
    // Intranet source unreachable → degrade gracefully (empty docs), never crash.
  }

  // Generated reports — bounded fan-out over jobs.
  const reportItems: ReportOrDocument[] = [];
  let totalJobs = 0;
  let jobsScanned = 0;
  try {
    const jobs = await listExtractionJobs();
    totalJobs = jobs.length;
    const bounded = jobs.slice(0, maxJobs);
    jobsScanned = bounded.length;
    const perJob = await Promise.all(
      bounded.map((j) =>
        listReports(j.id)
          .then((rs) => ({ job: j, rs }))
          .catch(() => ({ job: j, rs: [] as GeneratedReportDTO[] })),
      ),
    );
    for (const { job, rs } of perJob) {
      for (const r of rs) {
        reportItems.push({
          key: r.id,
          kind: "generated-report",
          title: `${r.report_type}（${job.source_filename ?? job.id.slice(0, 8)}）`,
          category: r.report_type,
          type: r.report_type,
          date: r.created_at,
          size: r.file_size,
          jobId: r.job_id,
          reportId: r.id,
        });
      }
    }
  } catch {
    // No jobs / unreachable → degrade to documents-only.
  }

  return {
    items: [...reportItems, ...docItems],
    truncated: totalJobs > jobsScanned,
    jobsScanned,
    totalJobs,
  };
}

// ---------------------------------------------------------------------------
// Mock 外部事实源数据
// ---------------------------------------------------------------------------

export interface MockDepartment {
  id?: string;
  code: string;
  iri: string;
  label: string;
  description: string;
  data_properties: Array<{ iri: string | null; label: string; value: string }>;
}

export interface MockRole {
  id?: string;
  code: string;
  iri: string;
  label: string;
  role_class_iri: string;
  description: string;
  data_properties: Array<{ iri: string | null; label: string; value: string }>;
}

export interface MockEquipment {
  id?: string;
  equipment_id: string;
  iri: string;
  label: string;
  equipment_class_iri: string;
  workshop_code: string;
  data_properties: Array<{ iri: string | null; label: string; value: string }>;
}

export interface MockEquipmentSchedule {
  id: string;
  task_id: string;
  task_name: string;
  activity_type: "production" | "cleaning" | "setup" | "maintenance";
  status: "scheduled" | "running" | "completed" | "paused" | "cancelled";
  priority: "low" | "normal" | "high" | "urgent";
  product_id: string | null;
  product_code: string | null;
  product_name: string | null;
  batch_no: string | null;
  planned_quantity: number | null;
  quantity_unit: string | null;
  equipment_id: string;
  equipment_name: string;
  workshop_code: string;
  start_at: string;
  end_at: string;
  actual_start_at: string | null;
  actual_end_at: string | null;
  process_step: string | null;
  operator_team: string | null;
  progress: number;
  remark: string | null;
}

export interface MockProductionArea {
  id?: string;
  code: string;
  iri: string;
  label: string;
  description: string;
  data_properties: Array<{ iri: string | null; label: string; value: string }>;
}

export interface MockTeamMember {
  id?: string;
  team_type?: string;
  name: string;
  role_label: string;
  department: string;
  role_class_iri: string;
  role_code: string;
}

export async function listMockDepartments(): Promise<MockDepartment[]> {
  const r = await fetch(`${API_BASE}/api/mock-sources/departments`, { headers: identityHeaders() });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function createMockDepartment(dept: MockDepartment): Promise<{ id: string }> {
  const r = await fetch(`${API_BASE}/api/mock-sources/departments`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...identityHeaders() },
    body: JSON.stringify(dept),
  });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function updateMockDepartment(id: string, dept: MockDepartment): Promise<{ id: string }> {
  const r = await fetch(`${API_BASE}/api/mock-sources/departments/${id}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json", ...identityHeaders() },
    body: JSON.stringify(dept),
  });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function deleteMockDepartment(id: string): Promise<void> {
  const r = await fetch(`${API_BASE}/api/mock-sources/departments/${id}`, {
    method: "DELETE",
    headers: identityHeaders(),
  });
  if (!r.ok) throw new Error(await r.text());
}

export async function listMockRoles(): Promise<MockRole[]> {
  const r = await fetch(`${API_BASE}/api/mock-sources/roles`, { headers: identityHeaders() });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function createMockRole(role: MockRole): Promise<{ id: string }> {
  const r = await fetch(`${API_BASE}/api/mock-sources/roles`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...identityHeaders() },
    body: JSON.stringify(role),
  });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function updateMockRole(id: string, role: MockRole): Promise<{ id: string }> {
  const r = await fetch(`${API_BASE}/api/mock-sources/roles/${id}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json", ...identityHeaders() },
    body: JSON.stringify(role),
  });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function deleteMockRole(id: string): Promise<void> {
  const r = await fetch(`${API_BASE}/api/mock-sources/roles/${id}`, {
    method: "DELETE",
    headers: identityHeaders(),
  });
  if (!r.ok) throw new Error(await r.text());
}

export async function listMockEquipment(): Promise<MockEquipment[]> {
  const r = await fetch(`${API_BASE}/api/mock-sources/equipment`, { headers: identityHeaders() });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function listMockEquipmentSchedules(
  equipmentId: string,
  startDate: string,
  endDate: string,
): Promise<MockEquipmentSchedule[]> {
  const params = new URLSearchParams({
    equipment_id: equipmentId,
    start_date: startDate,
    end_date: endDate,
  });
  const r = await fetch(`${API_BASE}/api/mock-sources/equipment-schedules?${params}`, {
    headers: identityHeaders(),
  });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function updateMockEquipmentScheduleOccupancy(input: {
  equipment_id: string;
  schedule_date: string;
  product_code: "HRS-5678" | "HRS-1597";
}): Promise<{ id: string; equipment_id: string; schedule_date: string; product_code: string }> {
  const r = await fetch(`${API_BASE}/api/mock-sources/equipment-schedules/occupancy`, {
    method: "PUT",
    headers: { "Content-Type": "application/json", ...identityHeaders() },
    body: JSON.stringify(input),
  });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function createMockEquipment(equip: MockEquipment): Promise<{ id: string }> {
  const r = await fetch(`${API_BASE}/api/mock-sources/equipment`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...identityHeaders() },
    body: JSON.stringify(equip),
  });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function updateMockEquipment(id: string, equip: MockEquipment): Promise<{ id: string }> {
  const r = await fetch(`${API_BASE}/api/mock-sources/equipment/${id}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json", ...identityHeaders() },
    body: JSON.stringify(equip),
  });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function deleteMockEquipment(id: string): Promise<void> {
  const r = await fetch(`${API_BASE}/api/mock-sources/equipment/${id}`, {
    method: "DELETE",
    headers: identityHeaders(),
  });
  if (!r.ok) throw new Error(await r.text());
}

export async function listMockProductionAreas(): Promise<MockProductionArea[]> {
  const r = await fetch(`${API_BASE}/api/mock-sources/production-areas`, { headers: identityHeaders() });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function createMockProductionArea(area: MockProductionArea): Promise<{ id: string }> {
  const r = await fetch(`${API_BASE}/api/mock-sources/production-areas`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...identityHeaders() },
    body: JSON.stringify(area),
  });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function updateMockProductionArea(id: string, area: MockProductionArea): Promise<{ id: string }> {
  const r = await fetch(`${API_BASE}/api/mock-sources/production-areas/${id}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json", ...identityHeaders() },
    body: JSON.stringify(area),
  });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function deleteMockProductionArea(id: string): Promise<void> {
  const r = await fetch(`${API_BASE}/api/mock-sources/production-areas/${id}`, {
    method: "DELETE",
    headers: identityHeaders(),
  });
  if (!r.ok) throw new Error(await r.text());
}

export async function listMockAssessmentTeam(): Promise<MockTeamMember[]> {
  const r = await fetch(`${API_BASE}/api/mock-sources/assessment-team`, { headers: identityHeaders() });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function createMockAssessmentTeamMember(member: MockTeamMember): Promise<{ id: string }> {
  const r = await fetch(`${API_BASE}/api/mock-sources/assessment-team`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...identityHeaders() },
    body: JSON.stringify({ ...member, team_type: "assessment" }),
  });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function updateMockAssessmentTeamMember(id: string, member: MockTeamMember): Promise<{ id: string }> {
  const r = await fetch(`${API_BASE}/api/mock-sources/assessment-team/${id}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json", ...identityHeaders() },
    body: JSON.stringify({ ...member, team_type: "assessment" }),
  });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function deleteMockAssessmentTeamMember(id: string): Promise<void> {
  const r = await fetch(`${API_BASE}/api/mock-sources/assessment-team/${id}`, {
    method: "DELETE",
    headers: identityHeaders(),
  });
  if (!r.ok) throw new Error(await r.text());
}

export async function listMockApproverTeam(): Promise<MockTeamMember[]> {
  const r = await fetch(`${API_BASE}/api/mock-sources/approver-team`, { headers: identityHeaders() });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function createMockApproverTeamMember(member: MockTeamMember): Promise<{ id: string }> {
  const r = await fetch(`${API_BASE}/api/mock-sources/approver-team`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...identityHeaders() },
    body: JSON.stringify({ ...member, team_type: "approver" }),
  });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function updateMockApproverTeamMember(id: string, member: MockTeamMember): Promise<{ id: string }> {
  const r = await fetch(`${API_BASE}/api/mock-sources/approver-team/${id}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json", ...identityHeaders() },
    body: JSON.stringify({ ...member, team_type: "approver" }),
  });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function deleteMockApproverTeamMember(id: string): Promise<void> {
  const r = await fetch(`${API_BASE}/api/mock-sources/approver-team/${id}`, {
    method: "DELETE",
    headers: identityHeaders(),
  });
  if (!r.ok) throw new Error(await r.text());
}
