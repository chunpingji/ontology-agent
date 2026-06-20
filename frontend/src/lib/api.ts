const API_BASE = process.env.NEXT_PUBLIC_API_URL || "";

async function fetchAPI<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...options?.headers },
    ...options,
  });
  if (!res.ok) {
    const error = await res.text();
    throw new Error(`API ${res.status}: ${error}`);
  }
  return res.json();
}

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
