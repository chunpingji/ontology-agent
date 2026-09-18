import type {
  DocumentAnalysisEntityRef,
  DocumentAnalysisGraphArtifact,
  DocumentGraphAssertionBase,
  DocumentGraphEntity,
  DocumentGraphScopeResolution,
  DocumentRelationSelection,
} from "@/lib/api";

export type GraphSelection = {
  kind: "entity" | "relationship" | "relationship_group" | "property";
  id: string;
  revision: number;
};

export const GROUP_SELECTION_LABELS: Record<DocumentRelationSelection, string> = {
  all: "全部成员", one_of: "恰选一个", alternatives: "可选成员", undetermined: "选择未决",
};

export const MODALITY_LABELS = {
  asserted: "已陈述事实", required: "要求", possible: "可能", planned: "计划", unspecified: "未明确",
} as const;

// Apply only to display names, never to business identifiers stored as property values.
function opaqueLabel(value: string): boolean {
  return /^(?:https?:\/\/|urn:)/i.test(value)
    || /^(?:[\w-]+:)?(?:[a-f\d]{32,}|[a-f\d]{8}(?:-[a-f\d]{4}){3}-[a-f\d]{12})(?:@\d+)?$/i.test(value);
}

export function ontologyDisplayLabel(label: string | null | undefined, iri: string | null | undefined, fallback: string): string {
  const name = label?.trim();
  if (name && !opaqueLabel(name)) return name;
  const localName = iri?.split(/[/#:]/).filter(Boolean).at(-1);
  return localName && !opaqueLabel(localName) ? localName : fallback;
}

export function graphClassLabel(entity: DocumentGraphEntity): string {
  return ontologyDisplayLabel(entity.class_label, entity.class_iri, "未命名类型");
}

export function graphEntityLabel(entity?: DocumentGraphEntity): string {
  if (!entity) return "当前视图未显示的实体";
  const label = entity.label?.trim();
  return label && !opaqueLabel(label) ? label : graphClassLabel(entity);
}

export function graphPredicateLabel(item: { predicate_label?: string | null; predicate_iri?: string | null }): string {
  return ontologyDisplayLabel(item.predicate_label, item.predicate_iri, "未命名属性或关系");
}

export function graphPredicateLabels(artifact?: DocumentAnalysisGraphArtifact | null): Map<string, string> {
  const terms = [
    ...(artifact?.entities.flatMap((entity) => entity.predicate_menu ?? []) ?? []),
    ...(artifact?.coverage?.subjects ?? []),
    ...(artifact?.relationships ?? []), ...(artifact?.relationship_groups ?? []), ...(artifact?.properties ?? []),
  ];
  const labels = new Map<string, string>();
  for (const term of terms) {
    const name = term.predicate_label?.trim();
    const localName = term.predicate_iri?.split(/[/#:]/).at(-1);
    if (name && !opaqueLabel(name) && name !== localName) labels.set(term.predicate_iri, name);
  }
  for (const term of terms) {
    if (!labels.has(term.predicate_iri)) labels.set(term.predicate_iri, graphPredicateLabel(term));
  }
  return labels;
}

export function graphScopeStepLabel(step: DocumentGraphScopeResolution["steps"][number], artifact: DocumentAnalysisGraphArtifact): string {
  const parent = [...artifact.relationships, ...(artifact.relationship_groups ?? [])].find(
    (item) => item.candidate_id === step.relation_ref.id && item.revision === step.relation_ref.revision,
  );
  const member = artifact.entities.find((entity) => entityRefKey(entity) === entityRefKey(step.member_ref));
  return `${parent ? graphPredicateLabel(parent) : "上级关系"} → ${graphEntityLabel(member)}`;
}

/** Public qualifiers carry text; reference metadata belongs in technical details. */
export function graphQualifierText(value: unknown): string {
  if (typeof value === "string") return value;
  if (Array.isArray(value)) return value.map(graphQualifierText).filter(Boolean).join("；");
  if (value && typeof value === "object") {
    const item = value as Record<string, unknown>;
    if (typeof item.text === "string") return item.text;
    if (Array.isArray(item.qualifiers)) return graphQualifierText(item.qualifiers);
    if (Object.keys(item).length) return "存在限定，详见原文或技术详情";
  }
  return "";
}

export function entityRefKey(ref: DocumentAnalysisEntityRef): string {
  return JSON.stringify([ref.entity_id, ref.revision]);
}

export function graphSelectionKey(selection: GraphSelection): string {
  return JSON.stringify([selection.kind, selection.id, selection.revision]);
}

/** Labels carry qualifications even when color cannot be distinguished. */
export function assertionQualifier(item: DocumentGraphAssertionBase): string {
  return [
    item.invalidated ? "已失效" : !item.policy_eligible ? "候选" : null,
    item.polarity !== "affirmed"
      ? { negated: "否定", conditional: "有条件", uncertain: "不确定" }[item.polarity] : null,
    item.modality && item.modality !== "asserted"
      ? { required: "要求", possible: "可能", planned: "计划", unspecified: "未明确" }[item.modality] : null,
    item.scope?.members.length ? "继承限定" : null,
    item.conditions.length ? "附条件" : null,
    Object.keys(item.applicability ?? {}).length ? "适用限定" : null,
  ].filter(Boolean).join(" · ");
}

export interface DocumentCanvasNode {
  id: string;
  label: string;
  subtitle: string;
  kind: "entity" | "relationship_group";
  root: boolean;
  selection: GraphSelection;
}

export interface DocumentCanvasLink {
  id: string;
  source: string;
  target: string;
  label: string;
  qualified: boolean;
  group: boolean;
  selection: GraphSelection;
}

/** Layout only: no new ontology entities, facts or inferred endpoint identities. */
export function buildDocumentGraph(artifact: DocumentAnalysisGraphArtifact) {
  const root = artifact.graph_snapshot?.root_ref;
  const predicateLabels = graphPredicateLabels(artifact);
  const nodes: DocumentCanvasNode[] = artifact.entities.map((entity) => {
    const selection: GraphSelection = { kind: "entity", id: entity.entity_id, revision: entity.revision };
    return {
      id: graphSelectionKey(selection), selection, label: graphEntityLabel(entity),
      subtitle: graphClassLabel(entity),
      kind: "entity", root: root ? entityRefKey(root) === entityRefKey(entity) : entity.seed_origin === "user_selected",
    };
  });
  const entityIds = new Map(artifact.entities.map((entity, index) => [entityRefKey(entity), nodes[index].id]));
  const links: DocumentCanvasLink[] = [];
  let missingEndpoints = 0;
  for (const edge of artifact.relationships) {
    const subject = entityIds.get(entityRefKey(edge.subject_ref));
    const object = entityIds.get(entityRefKey(edge.object_ref));
    if (!subject || !object) { missingEndpoints += 1; continue; }
    const selection: GraphSelection = { kind: "relationship", id: edge.candidate_id, revision: edge.revision };
    const qualifier = assertionQualifier(edge);
    links.push({
      id: graphSelectionKey(selection), selection,
      source: edge.direction === "subject_to_object" ? subject : object,
      target: edge.direction === "subject_to_object" ? object : subject,
      label: [predicateLabels.get(edge.predicate_iri) || graphPredicateLabel(edge), qualifier].filter(Boolean).join(" · "),
      qualified: !!qualifier, group: false,
    });
  }
  for (const group of artifact.relationship_groups ?? []) {
    const selection: GraphSelection = { kind: "relationship_group", id: group.candidate_id, revision: group.revision };
    const id = graphSelectionKey(selection);
    nodes.push({ id, selection, label: predicateLabels.get(group.predicate_iri) || graphPredicateLabel(group), root: false,
      subtitle: [GROUP_SELECTION_LABELS[group.selection], assertionQualifier(group)].filter(Boolean).join(" · "),
      kind: "relationship_group" });
    const refs = [group.subject_ref, ...group.object_refs];
    refs.forEach((ref, index) => {
      const endpoint = entityIds.get(entityRefKey(ref));
      if (!endpoint) { missingEndpoints += 1; return; }
      const outgoing = (index === 0) === (group.direction === "subject_to_object");
      links.push({ id: `${id}:${index}`, selection,
        source: outgoing ? endpoint : id, target: outgoing ? id : endpoint,
        label: index === 0 ? "主体" : "组成员", qualified: true, group: true });
    });
  }
  return { nodes, links, missingEndpoints };
}
