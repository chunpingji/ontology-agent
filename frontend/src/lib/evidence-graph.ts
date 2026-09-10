import type { EvidenceBranchProgress, EvidenceCandidate, EvidenceClassSchema, EvidenceGraphSchema } from "./api";

export function branchProgressText(progress?: EvidenceBranchProgress, executionStatus?: string | null): string {
  if (!progress) return "尚无分支处理记录";
  const active = executionStatus === "running" || executionStatus === "queued";
  const labels: Record<EvidenceBranchProgress["status"], string> = {
    queued: "等待识别",
    extracting_entities: active ? "正在识别关联对象" : "关联对象识别尚未完成",
    searching_entities: "已检查部分原文，继续寻找关联对象",
    entities_failed: "关联对象识别未完成，存在校验失败",
    awaiting_relation: "已识别关联对象，等待关系验证",
    extracting_relation: active ? "正在验证关系" : "关系验证尚未完成",
    relation_failed: "关系验证未通过",
    relation_checked: "已检查部分关系，尚未形成有效肯定关系",
    identified: progress.coverage_complete ? "已识别有效关系" : "已识别有效关系，覆盖尚未完成",
    no_match: progress.coverage_complete ? "本轮处理完成，未形成有效肯定关系" : "尚未完成关系识别",
  };
  let label = labels[progress.status] ?? "分支状态待核对";
  if (progress.status === "identified" && progress.property_status) {
    const propertyLabels = {
      queued: "关联实体属性等待识别",
      extracting: active ? "正在识别关联实体属性" : "关联实体属性识别尚未完成",
      incomplete: "关联实体属性识别未完成，存在校验失败",
      partial: "已检查部分关联实体属性",
      complete: "关联实体属性检查完成",
      not_applicable: "本体未声明关联实体的数据属性",
    };
    label += `；${propertyLabels[progress.property_status]}`;
  }
  const prefixes: Record<string, string> = { paused: "已暂停", pausing: "正在暂停", failed: "运行失败", cancelled: "已取消", interrupted: "运行已中断" };
  return prefixes[executionStatus ?? ""] ? `${prefixes[executionStatus!]}；${label}` : label;
}

export function evidenceLabel(candidate: EvidenceCandidate): string {
  return candidate.kind === "entity" ? candidate.text
    : candidate.predicate_label || shortIri(candidate.predicate_iri);
}

export function shortIri(iri?: string | null): string {
  return iri?.split(/[/#:]/).pop() || "未命名";
}

export function evidenceValue(candidate: EvidenceCandidate): string | undefined {
  if (!candidate.literal) return undefined;
  const { raw_value: value, raw_unit: unit } = candidate.literal;
  return unit && !value.includes(unit) ? `${value} ${unit}` : value;
}

export function evidenceStatus(candidate: EvidenceCandidate): string {
  if (candidate.review_status === "rejected") return "已拒绝";
  if (candidate.validation_issues.some((issue) => issue.code === "stale_dependency")) return "关联项已失效";
  if (candidate.validation_status === "conflict") return "存在冲突";
  if (candidate.validation_status === "rejected") return "校验未通过";
  if (candidate.validation_status === "pending") return "待校验";
  if (candidate.review_status === "confirmed") return candidate.review_source === "automatic" ? "自动通过" : "审核通过";
  return "待审核";
}

export function buildEvidenceGraph(candidates: EvidenceCandidate[]) {
  const entities = new Map(candidates.filter((item) => item.kind === "entity").map((item) => [item.candidate_id, item]));
  const properties = new Map<string, EvidenceCandidate[]>();
  const relationships = new Map<string, EvidenceCandidate[]>();
  const incoming = new Set<string>();
  const orphans: EvidenceCandidate[] = [];
  for (const candidate of candidates) {
    if (candidate.kind === "entity") continue;
    const subject = candidate.subject?.candidate_id;
    if (!subject || !entities.has(subject)) { orphans.push(candidate); continue; }
    const index = candidate.kind === "property" ? properties : relationships;
    const group = index.get(subject) ?? [];
    group.push(candidate);
    index.set(subject, group);
    if (candidate.kind === "relationship" && candidate.object && entities.has(candidate.object.candidate_id)) {
      incoming.add(candidate.object.candidate_id);
    }
  }
  const roots = [...entities.values()].filter((item) => item.identity?.document_root || !incoming.has(item.candidate_id));
  const reached = new Set<string>();
  function visit(id: string) {
    if (reached.has(id)) return;
    reached.add(id);
    for (const edge of relationships.get(id) ?? []) if (edge.object) visit(edge.object.candidate_id);
  }
  for (const root of roots) visit(root.candidate_id);
  // Include disconnected cycles instead of silently losing their entities.
  for (const entity of entities.values()) if (!reached.has(entity.candidate_id)) {
    roots.push(entity); visit(entity.candidate_id);
  }
  return { entities, properties, relationships, roots, orphans };
}

export type EvidenceBranch = {
  iri: string;
  label: string;
  kind: "property" | "relationship";
  declared: boolean;
  range: string[];
  candidates: EvidenceCandidate[];
};

export function evidenceBranches(definition: EvidenceClassSchema | undefined, assertions: EvidenceCandidate[]): EvidenceBranch[] {
  const branches = new Map<string, EvidenceBranch>();
  function add(kind: EvidenceBranch["kind"], iri: string, label?: string | null, range: string[] = []) {
    const key = `${kind}:${iri}`;
    const current = branches.get(key);
    if (current) { current.range = [...new Set([...current.range, ...range])]; return; }
    branches.set(key, { iri, label: label || shortIri(iri), kind, declared: true, range, candidates: [] });
  }
  for (const property of definition?.properties ?? []) add("property", property.iri, property.label);
  for (const relation of definition?.relationships ?? []) add("relationship", relation.iri, relation.label, relation.range);
  for (const candidate of assertions) {
    if (candidate.kind === "entity") continue;
    const iri = candidate.predicate_iri || "";
    const key = `${candidate.kind}:${iri}`;
    if (!branches.has(key)) branches.set(key, { iri, label: evidenceLabel(candidate), kind: candidate.kind,
      declared: false, range: [], candidates: [] });
    branches.get(key)!.candidates.push(candidate);
  }
  return [...branches.values()];
}

export function buildDocumentEvidenceGraph(candidates: EvidenceCandidate[], schema?: EvidenceGraphSchema) {
  const graph = buildEvidenceGraph(candidates);
  const classes = schema?.classes ?? {};
  const explicitRoots = [...graph.entities.values()].filter((item) => item.identity?.document_root);
  const rootClasses = new Set(explicitRoots.map((item) => item.class_iri).filter(Boolean));
  const documentClass = schema?.document_class_iri || (rootClasses.size === 1 ? [...rootClasses][0]! : null);
  const documentRoots = explicitRoots.filter((item) => documentClass && (item.class_iri === documentClass
    || classes[item.class_iri ?? ""]?.parents.includes(documentClass)));
  const connected = new Set<string>();
  const pending = documentRoots.map((item) => item.candidate_id);
  while (pending.length) {
    const id = pending.pop()!;
    if (connected.has(id) || !graph.entities.has(id)) continue;
    connected.add(id);
    for (const edge of graph.relationships.get(id) ?? []) if (edge.object) pending.push(edge.object.candidate_id);
  }
  const documentAssertions = documentRoots.flatMap((item) => [
    ...(graph.properties.get(item.candidate_id) ?? []), ...(graph.relationships.get(item.candidate_id) ?? []),
  ]);
  const branches = evidenceBranches(classes[documentClass ?? ""], documentAssertions);
  const unassociated = [...graph.entities.values()].filter((item) => !connected.has(item.candidate_id));
  return { ...graph, classes, documentClass, documentRoots, connected, branches, unassociated };
}

export function publishableEvidence(candidates: EvidenceCandidate[]) {
  const byId = new Map(candidates.map((item) => [item.candidate_id, item]));
  const blocked = new Set(candidates.filter((item) => item.validation_status !== "passed" || item.review_status !== "confirmed")
    .map((item) => item.candidate_id));
  let changed = true;
  while (changed) {
    changed = false;
    for (const item of candidates) {
      if (blocked.has(item.candidate_id)) continue;
      const refs = [item.subject, item.object, ...(item.dependency_refs ?? [])].filter((ref) => ref != null);
      if (refs.some((ref) => !byId.has(ref.candidate_id) || blocked.has(ref.candidate_id)
        || byId.get(ref.candidate_id)!.revision !== ref.revision)) {
        blocked.add(item.candidate_id); changed = true;
      }
    }
  }
  return candidates.filter((item) => !blocked.has(item.candidate_id)
    && (item.commit_status === "not_requested" || item.commit_status === "failed"));
}
