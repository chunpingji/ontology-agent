import type {
  DocumentAnalysisObjectRef,
  DocumentAnalysisTarget,
  DocumentAnalysisTargetGraph,
  DocumentGraphAssertionBase,
  DocumentGraphEntity,
  DocumentGraphProperty,
  DocumentGraphRelationship,
  DocumentGraphRelationshipGroup,
} from "@/lib/api";
import {
  assertionQualifier,
  entityRefKey,
  graphEntityLabel,
  graphPredicateLabel,
  GROUP_SELECTION_LABELS,
  ontologyDisplayLabel,
} from "@/lib/document-graph";

export const TARGET_STATE_LABELS: Record<DocumentAnalysisTarget["state"], string> = {
  pending: "待识别", partial: "部分实证", supported: "已实证", not_found: "未找到证据",
  undetermined: "待核验", rejected: "校验未通过", negated: "原文否定",
};

export type TargetAssertion = DocumentGraphProperty | DocumentGraphRelationship | DocumentGraphRelationshipGroup;

export function assertionRefKey(ref: DocumentAnalysisObjectRef): string {
  return JSON.stringify([ref.id, ref.revision]);
}

export function targetAssertions(target: DocumentAnalysisTarget, artifact: DocumentAnalysisTargetGraph): TargetAssertion[] {
  const refs = new Set(target.assertion_refs.map(assertionRefKey));
  return [...artifact.graph.properties, ...artifact.graph.relationships, ...(artifact.graph.relationship_groups ?? [])]
    .filter((item) => refs.has(assertionRefKey({ id: item.candidate_id, revision: item.revision })));
}

export function isSupportedTargetAssertion(target: DocumentAnalysisTarget, assertion: DocumentGraphAssertionBase): boolean {
  return !assertion.invalidated
    && target.supported_assertion_refs.some((ref) => ref.id === assertion.candidate_id && ref.revision === assertion.revision);
}

export type AssertionReviewState = "accepted" | "pending" | "not_accepted";

export const REVIEW_STATE_LABELS: Record<AssertionReviewState, string> = {
  accepted: "已采信", pending: "待定", not_accepted: "未采信",
};

export function assertionReviewState(target: DocumentAnalysisTarget, assertion: DocumentGraphAssertionBase): AssertionReviewState {
  if (assertion.invalidated || assertion.independent_review === "rejected" || assertion.decision_status === "unsupported") return "not_accepted";
  return isSupportedTargetAssertion(target, assertion) ? "accepted" : "pending";
}

export function visibleTargetAssertions(target: DocumentAnalysisTarget, artifact: DocumentAnalysisTargetGraph, showNotAccepted = false): TargetAssertion[] {
  const assertions = targetAssertions(target, artifact);
  return artifact.phase === "evidence_review" && !showNotAccepted
    ? assertions.filter((assertion) => assertionReviewState(target, assertion) !== "not_accepted") : assertions;
}

export function targetReviewCounts(artifact: DocumentAnalysisTargetGraph): Record<AssertionReviewState, number> {
  const states = new Map<string, AssertionReviewState>();
  for (const target of artifact.targets) for (const assertion of targetAssertions(target, artifact)) {
    states.set(assertionRefKey({ id: assertion.candidate_id, revision: assertion.revision }), assertionReviewState(target, assertion));
  }
  const counts = { accepted: 0, pending: 0, not_accepted: 0 };
  for (const state of states.values()) counts[state] += 1;
  return counts;
}

export function targetAssertionQualifier(target: DocumentAnalysisTarget, assertion: DocumentGraphAssertionBase, phase: DocumentAnalysisTargetGraph["phase"]): string {
  return assertionQualifier(phase === "evidence_review" && assertionReviewState(target, assertion) === "accepted"
    ? { ...assertion, policy_eligible: true } : assertion);
}

export function assertionSourceRefs(assertion: DocumentGraphAssertionBase): string[] {
  return [...new Set(Object.values(assertion.source_selection_refs).flat().filter(Boolean))];
}

export function assertionEndpointEntities(
  assertion: TargetAssertion, artifact: DocumentAnalysisTargetGraph,
): Array<{ role: string; entity: DocumentGraphEntity }> {
  const entities = new Map(artifact.graph.entities.map((entity) => [entityRefKey(entity), entity]));
  const refs = [{ role: "主体", ref: assertion.subject_ref }, ...(
    "object_refs" in assertion ? assertion.object_refs.map((ref) => ({ role: "对象", ref }))
      : "object_ref" in assertion ? [{ role: "对象", ref: assertion.object_ref }] : []
  )];
  return refs.flatMap(({ role, ref }) => {
    const entity = entities.get(entityRefKey(ref));
    return entity ? [{ role, entity }] : [];
  });
}

export function candidateGraphCounts(artifact: DocumentAnalysisTargetGraph): { entities: number; relationships: number } {
  const rootId = entityRefKey(artifact.root);
  return {
    entities: new Set(artifact.graph.entities.filter((entity) => entityRefKey(entity) !== rootId).map(entityRefKey)).size,
    relationships: new Set(artifact.targets.filter((target) => target.kind === "relationship")
      .flatMap((target) => targetAssertions(target, artifact).map((item) => assertionRefKey({ id: item.candidate_id, revision: item.revision })))).size,
  };
}

/** Every counted subject remains reachable even without an edge to the root. */
export function targetGraphSubjects(artifact: DocumentAnalysisTargetGraph): Array<{
  id: string; label: string; properties: number; relationships: number;
}> {
  const rootId = entityRefKey(artifact.root);
  const subjects = new Map([[rootId, {
    id: rootId, label: artifact.root.label, properties: 0, relationships: 0,
  }]]);
  for (const entity of artifact.graph.entities) {
    const id = entityRefKey(entity);
    if (!subjects.has(id)) subjects.set(id, {
      id, label: graphEntityLabel(entity), properties: 0, relationships: 0,
    });
  }
  for (const target of artifact.targets) {
    const id = entityRefKey(target.subject_ref);
    let subject = subjects.get(id);
    if (!subject) {
      subject = { id, label: target.subject_label, properties: 0, relationships: 0 };
      subjects.set(id, subject);
    }
    if (target.kind === "property") subject.properties += 1;
    else subject.relationships += 1;
  }
  return [...subjects.values()];
}

export interface TargetCanvasNode {
  id: string;
  label: string;
  subtitle: string;
  depth: number;
  kind: "entity" | "placeholder" | "group";
  targetId?: string;
  root: boolean;
  expandable: boolean;
}

export interface TargetCanvasEdge {
  id: string;
  source: string;
  target: string;
  label: string;
  supported: boolean;
  targetId: string;
  reviewState?: AssertionReviewState;
}

/** Display only: placeholders never become entities or positive assertions. */
export function buildTargetGraph(
  artifact: DocumentAnalysisTargetGraph,
  expanded: ReadonlySet<string>,
  limit = 80,
  focusSubject?: string,
  showNotAccepted = false,
): { nodes: TargetCanvasNode[]; edges: TargetCanvasEdge[]; hiddenItems: number } {
  const rootId = entityRefKey(artifact.root);
  const candidatePhase = artifact.phase === "candidate_graph";
  const entities = new Map(artifact.graph.entities.map((entity) => [entityRefKey(entity), entity]));
  const bySubject = new Map<string, DocumentAnalysisTarget[]>();
  for (const target of artifact.targets) {
    const id = entityRefKey(target.subject_ref);
    bySubject.set(id, [...(bySubject.get(id) ?? []), target]);
  }
  const nodes = new Map<string, TargetCanvasNode>();
  const edges: TargetCanvasEdge[] = [];
  let hiddenItems = 0;
  const addEntity = (id: string, depth: number): boolean => {
    if (nodes.has(id)) return true;
    if (nodes.size >= limit) { hiddenItems += 1; return false; }
    const entity = entities.get(id);
    const targets = bySubject.get(id) ?? [];
    const properties = targets.filter((target) => target.kind === "property");
    const isRoot = id === rootId;
    const classLabel = isRoot ? ontologyDisplayLabel(entity?.class_label, artifact.root.class_iri, "文档")
      : ontologyDisplayLabel(entity?.class_label, entity?.class_iri, "未知类型");
    nodes.set(id, {
      id, depth, kind: "entity", root: isRoot,
      label: isRoot ? artifact.root.label : entity ? graphEntityLabel(entity) : "待核验实体",
      subtitle: candidatePhase ? `${classLabel} · ${isRoot ? "文档根" : "已登记实体"}`
        : `${classLabel} · 属性 ${properties.filter((target) => target.completed).length}/${properties.length}`,
      expandable: targets.some((target) => target.kind === "relationship"),
    });
    return true;
  };
  const focusId = focusSubject && (entities.has(focusSubject) || bySubject.has(focusSubject))
    ? focusSubject : rootId;
  addEntity(focusId, 0);
  const queue = [focusId];
  const visited = new Set<string>();
  while (queue.length) {
    const subjectId = queue.shift()!;
    if (visited.has(subjectId) || !expanded.has(subjectId)) continue;
    visited.add(subjectId);
    const depth = (nodes.get(subjectId)?.depth ?? 0) + 1;
    for (const target of bySubject.get(subjectId) ?? []) {
      if (target.kind !== "relationship") continue;
      const assertions = visibleTargetAssertions(target, artifact, showNotAccepted).filter((item) => "object_ref" in item || "object_refs" in item);
      for (const assertion of assertions) {
        if (!("object_ref" in assertion) && !("object_refs" in assertion)) continue;
        const key = assertionRefKey({ id: assertion.candidate_id, revision: assertion.revision });
        const reviewState = artifact.phase === "evidence_review" ? assertionReviewState(target, assertion) : undefined;
        const supported = !candidatePhase && (reviewState ? reviewState === "accepted" : isSupportedTargetAssertion(target, assertion));
        const label = [graphPredicateLabel(target), targetAssertionQualifier(target, assertion, artifact.phase)].filter(Boolean).join(" · ");
        const group = "object_refs" in assertion;
        const refs = group ? assertion.object_refs : [assertion.object_ref];
        const groupId = `group:${key}`;
        if (group) {
          if (nodes.size >= limit) { hiddenItems += 1; continue; }
          nodes.set(groupId, { id: groupId, label: graphPredicateLabel(target),
            subtitle: GROUP_SELECTION_LABELS[assertion.selection], depth, kind: "group", root: false,
            expandable: false, targetId: target.target_id });
          edges.push({ id: key, source: assertion.direction === "subject_to_object" ? subjectId : groupId,
            target: assertion.direction === "subject_to_object" ? groupId : subjectId,
            supported, label, targetId: target.target_id, reviewState });
        }
        for (const ref of refs) {
          const objectId = entityRefKey(ref);
          if (!addEntity(objectId, depth + (group ? 1 : 0))) continue;
          const from = group ? groupId : subjectId;
          edges.push({ id: `${key}:${objectId}`, source: assertion.direction === "subject_to_object" ? from : objectId,
            target: assertion.direction === "subject_to_object" ? objectId : from,
            label: group ? GROUP_SELECTION_LABELS[assertion.selection] : label, supported, targetId: target.target_id, reviewState });
          queue.push(objectId);
        }
      }
      // An observed member does not close an unknown-size relation target.
      if (candidatePhase ? assertions.length === 0 : !target.completed || assertions.length === 0) {
        if (nodes.size >= limit) { hiddenItems += 1; continue; }
        const id = `target:${target.target_id}`;
        nodes.set(id, { id, depth, kind: "placeholder", root: false, expandable: false,
          label: target.range_types.map((type) => ontologyDisplayLabel(type.label, type.iri, "待识别类型")).join(" / ") || "待识别对象",
          subtitle: candidatePhase ? "本体关系目标 · 尚无原文候选"
            : artifact.phase === "evidence_review" ? "本体关系目标 · 对象范围待核对" : TARGET_STATE_LABELS[target.state], targetId: target.target_id });
        edges.push({ id, source: subjectId, target: id, label: graphPredicateLabel(target), supported: false, targetId: target.target_id });
      }
    }
  }
  return { nodes: [...nodes.values()], edges, hiddenItems };
}
