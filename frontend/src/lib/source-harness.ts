import type {
  DocumentHarnessEntity, DocumentHarnessGraph, DocumentHarnessObservation,
  DocumentHarnessProperty, DocumentHarnessRelation,
} from "@/lib/api";

export const HARNESS_STATES = { candidate: "候选", accepted: "已采信", rejected: "未采信", unresolved: "未决" };
export const HARNESS_STAGES: Record<string, string> = {
  ingest: "保存输入", parse: "读取原文", discover: "发现原文候选",
  type_alignment: "对齐本体类型", entity_review: "核对实体指称与类型",
  referent_alignment: "核对编号指称", referent_candidates: "提出编号分组", referent_selection: "核对指称分组",
  assertion_alignment: "对齐关系与属性", evidence_review: "核对原文依据",
  coreference_review: "核对文档内共指", complete: "本轮结束",
};
export const OBSERVATION_KINDS = {
  field: "原字段", entity: "实体提及", relation: "关系线索", scope: "范围提示",
  validation: "校验异常", failure: "调用失败",
};
export const OBSERVATION_RESULTS = {
  accepted: "有已采信属性", rejected: "有未采信属性", pending: "待对齐 / 待核验",
  unmatched: "有菜单未匹配", invalid: "有对齐异常",
};
export type ObservationResult = keyof typeof OBSERVATION_RESULTS;
export function displayValue(value: unknown): string {
  return typeof value === "string" ? value : JSON.stringify(value) ?? "";
}
export function relationQualifier(relation: DocumentHarnessRelation) {
  return [relation.polarity === "negative" ? "否定" : relation.polarity === "uncertain" ? "极性未定" : "",
    relation.conditions.length ? `附条件：${relation.conditions.join("；")}` : ""].filter(Boolean).join(" · ");
}
export function isHierarchyRelation(relation: DocumentHarnessRelation) {
  return relation.state !== "rejected" && relation.polarity === "positive" && !relation.conditions.length;
}

/** A display index only. Membership and graph reachability never change fact acceptance. */
export function buildHarnessHierarchy(graph: DocumentHarnessGraph) {
  const entities = new Map(graph.entities.map((entity) => [entity.id, entity]));
  const roots = graph.entities.filter((entity) => entity.role === "document_root");
  const outgoing = new Map<string, DocumentHarnessRelation[]>();
  const incoming = new Map<string, DocumentHarnessRelation[]>();
  const depth = new Map<string, number>();
  const parent = new Map<string, DocumentHarnessRelation>();
  for (const relation of [...graph.relations].sort((a, b) => a.id.localeCompare(b.id))) {
    if (!entities.has(relation.subject_id) || !entities.has(relation.object_id)) continue;
    incoming.set(relation.object_id, [...incoming.get(relation.object_id) ?? [], relation]);
    if (isHierarchyRelation(relation)) outgoing.set(relation.subject_id, [...outgoing.get(relation.subject_id) ?? [], relation]);
  }
  const queue = roots.map((entity) => entity.id);
  for (const id of queue) depth.set(id, 0);
  for (let index = 0; index < queue.length; index++) {
    const id = queue[index];
    for (const relation of outgoing.get(id) ?? []) {
      if (depth.has(relation.object_id)) continue;
      depth.set(relation.object_id, depth.get(id)! + 1);
      parent.set(relation.object_id, relation);
      queue.push(relation.object_id);
    }
  }
  return { entities, roots, outgoing, incoming, depth, parent,
    disconnected: graph.entities.filter((entity) => !depth.has(entity.id)) };
}
export type HarnessHierarchy = ReturnType<typeof buildHarnessHierarchy>;

export function harnessPredicateGroupId(subjectId: string, predicateIri: string) {
  return JSON.stringify(["predicate", subjectId, predicateIri]);
}

export function harnessAncestorPredicateGroupIds(hierarchy: HarnessHierarchy, entityId?: string) {
  const ids = new Set<string>();
  let current = entityId ?? "";
  while (hierarchy.parent.has(current)) {
    const relation = hierarchy.parent.get(current)!;
    ids.add(harnessPredicateGroupId(relation.subject_id, relation.predicate_iri));
    current = relation.subject_id;
  }
  return ids;
}

export function filterHarnessEntities(graph: DocumentHarnessGraph, hierarchy: HarnessHierarchy, query: string, state: string) {
  const text = query.trim().toLocaleLowerCase();
  const fields = new Map<string, string[]>();
  for (const property of graph.properties) fields.set(property.subject_id, [...fields.get(property.subject_id) ?? [],
    property.label, property.predicate_iri ?? "", displayValue(property.value), property.source_value]);
  const matched = new Set(graph.entities.filter((entity) => (state === "all" || entity.state === state)
    && (!text || [entity.label, entity.id, entity.class_label, entity.class_iri, entity.role,
      ...(entity.mentions ?? []).flatMap((mention) => [mention.label, mention.role]), ...fields.get(entity.id) ?? []]
      .filter(Boolean).join(" ").toLocaleLowerCase().includes(text))).map((entity) => entity.id));
  const visible = new Set(matched);
  for (const id of matched) {
    let current = id;
    while (hierarchy.parent.has(current)) {
      current = hierarchy.parent.get(current)!.subject_id;
      if (visible.has(current)) break;
      visible.add(current);
    }
  }
  return { matched, visible };
}

interface HarnessEntityTreeRow {
  kind: "entity";
  id: string;
  entity: DocumentHarnessEntity;
  level: number;
  relation?: DocumentHarnessRelation;
  reference: boolean;
  expandable: boolean;
  expanded: boolean;
}
interface HarnessPredicateTreeRow {
  kind: "predicate";
  id: string;
  subjectId: string;
  predicateIri: string;
  label: string;
  level: number;
  expanded: boolean;
}
export type HarnessTreeRow = HarnessEntityTreeRow | HarnessPredicateTreeRow;
export function harnessTreeRows(hierarchy: HarnessHierarchy, visible: Set<string>, maxDepth: number,
  expanded: ReadonlyMap<string, boolean>, reveal: boolean, limit = 200) {
  const rows: HarnessTreeRow[] = [];
  type PendingRow = Omit<HarnessEntityTreeRow, "expandable" | "expanded">
    | (Omit<HarnessPredicateTreeRow, "expanded"> & { relations: DocumentHarnessRelation[] });
  const stack: PendingRow[] = hierarchy.roots.filter((entity) => visible.has(entity.id))
    .map((entity): PendingRow => ({ kind: "entity", id: `root:${entity.id}`, entity, level: 0, reference: false })).reverse();
  while (stack.length && rows.length < limit) {
    // Keep a group heading with at least its first member when paginating.
    if (stack[stack.length - 1].kind === "predicate" && rows.length + 1 >= limit) break;
    const row = stack.pop()!;
    if (row.kind === "predicate") {
      const { relations, ...group } = row;
      const open = reveal || (expanded.get(row.id) ?? true);
      rows.push({ ...group, expanded: open });
      if (open) for (const relation of [...relations].reverse()) stack.push({
        kind: "entity", id: `relation:${relation.id}`,
        entity: hierarchy.entities.get(relation.object_id)!, level: row.level, relation,
        reference: hierarchy.parent.get(relation.object_id)?.id !== relation.id,
      });
      continue;
    }
    const children = (hierarchy.outgoing.get(row.entity.id) ?? []).filter((edge) => visible.has(edge.object_id));
    const expandable = !row.reference && children.length > 0;
    const open = expandable && (reveal || (expanded.get(row.entity.id) ?? row.level < maxDepth));
    rows.push({ ...row, expandable, expanded: open });
    if (open) {
      const groups = new Map<string, DocumentHarnessRelation[]>();
      for (const relation of children) {
        const group = groups.get(relation.predicate_iri) ?? [];
        group.push(relation);
        groups.set(relation.predicate_iri, group);
      }
      for (const [predicateIri, relations] of [...groups].reverse()) stack.push({
        kind: "predicate", id: harnessPredicateGroupId(row.entity.id, predicateIri),
        subjectId: row.entity.id, predicateIri, label: relations[0].label,
        level: row.level + 1, relations,
      });
    }
  }
  return { rows, truncated: stack.length > 0 };
}

export function observationResults(item: DocumentHarnessObservation, properties: Map<string, DocumentHarnessProperty>, subjectId: string | null = null) {
  const results = new Set<ObservationResult>();
  for (const alignment of item.alignments) {
    if (subjectId && alignment.subject_id !== subjectId) continue;
    for (const id of alignment.property_ids) {
      const property = properties.get(id);
      if (property && property.subject_id === alignment.subject_id) results.add(
        property.state === "accepted" || property.state === "rejected" ? property.state : "pending");
    }
    for (const attempt of alignment.attempts) if (attempt.state !== "mapped") results.add(attempt.state);
  }
  if (!results.size && item.kind === "field") results.add("pending");
  return [...results];
}
export function observationSubjects(item: DocumentHarnessObservation) {
  return [...new Set([...item.candidate_subject_ids, ...item.alignments.map((alignment) => alignment.subject_id)])];
}
export function hasObservationContext(item: DocumentHarnessObservation) {
  return Boolean(item.kind) && Array.isArray(item.candidate_subject_ids)
    && Array.isArray(item.alignments) && Array.isArray(item.discovery_cards);
}
export function filterHarnessObservations(graph: DocumentHarnessGraph, kind: string, subject: string, result: string,
  selectedEntity: string | null, query = "") {
  const properties = new Map(graph.properties.map((item) => [item.id, item]));
  const entities = new Map(graph.entities.map((item) => [item.id, item]));
  const subjectId = subject === "selected" ? selectedEntity : subject.startsWith("entity:") ? subject.slice(7) : null;
  const text = query.trim().toLocaleLowerCase();
  return graph.observations.filter((item) => {
    if (item.kind === "failure") return false;
    const subjects = observationSubjects(item);
    const search = [item.label, item.value, item.reason, ...item.evidence.map((ref) => ref.text),
      ...subjects.map((id) => entities.get(id)?.label ?? id),
      ...item.discovery_cards.flatMap((card) => [card.label, card.iri]),
      ...item.alignments.flatMap((alignment) => [alignment.card?.label, alignment.card?.iri,
        ...alignment.attempts.flatMap((attempt) => [attempt.reason, ...attempt.predicates.flatMap((p) => [p.label, p.iri, p.namespace])]),
        ...alignment.property_ids.flatMap((id) => { const p = properties.get(id); return p ? [p.label, p.predicate_iri, displayValue(p.value)] : []; })])];
    return (kind === "all" || item.kind === kind)
      && (subject === "all" || (subject === "unowned" ? !subjects.length && !item.object_id
        : subjectId != null && (subjects.includes(subjectId) || item.object_id === subjectId)))
      && (result === "all" || observationResults(item, properties, subjectId).includes(result as ObservationResult))
      && (!text || search.filter(Boolean).join(" ").toLocaleLowerCase().includes(text));
  });
}

export function harnessCosts(costs: DocumentHarnessGraph["progress"]["stage_costs"]) {
  const sumTokens = (key: "input_tokens" | "output_tokens") => costs.length && costs.every((cost) => cost[key] != null)
    ? costs.reduce((total, cost) => total + cost[key]!, 0) : null;
  const input = sumTokens("input_tokens"), output = sumTokens("output_tokens");
  return { calls: costs.reduce((sum, cost) => sum + cost.calls, 0), seconds: costs.reduce((sum, cost) => sum + cost.seconds, 0),
    input, output, tokens: input == null || output == null ? null : input + output };
}
