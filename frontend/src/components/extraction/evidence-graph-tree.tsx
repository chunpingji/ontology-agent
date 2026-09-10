"use client";

import { memo, useMemo, useRef, useState, type ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { branchProgressText, buildDocumentEvidenceGraph, evidenceBranches, evidenceLabel, evidenceStatus, evidenceValue, shortIri,
  type EvidenceBranch } from "@/lib/evidence-graph";
import type { EvidenceAnchor, EvidenceBranchProgress, EvidenceCandidate, EvidenceGraphSchema, PDECalculation } from "@/lib/api";
import { PDECalculationCard, type CalculationAction } from "./pde-calculation-card";

type Props = {
  candidates: EvidenceCandidate[];
  schema?: EvidenceGraphSchema;
  branchProgress?: Record<string, EvidenceBranchProgress>;
  executionStatus?: string | null;
  busy: boolean;
  onSource: (anchor: EvidenceAnchor) => void;
  onReject?: (candidate: EvidenceCandidate) => void;
  calculations?: PDECalculation[];
  onCalculation?: CalculationAction;
};
type Actions = Pick<Props, "busy" | "onSource" | "onReject">;

const assertionLabels = { affirmed: "", negated: "否定", conditional: "有条件",
  hypothetical: "假设", uncertain: "不确定" };

function Status({ candidate }: { candidate: EvidenceCandidate }) {
  const accepted = candidate.review_status === "confirmed" && candidate.validation_status === "passed";
  return <span className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] ${accepted
    ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300"
    : candidate.review_status === "rejected" ? "bg-red-50 text-red-700 dark:bg-red-950 dark:text-red-300"
      : "bg-amber-50 text-amber-700 dark:bg-amber-950 dark:text-amber-300"}`}>
    {evidenceStatus(candidate)}
  </span>;
}

function SourceDetails({ candidate, busy, onSource, onReject }: Actions & { candidate: EvidenceCandidate }) {
  const [showEvidence, setShowEvidence] = useState(false);
  const anchors = candidate.provenance.flatMap((source) => source.anchors ?? []);
  const excerpts = [...new Set(candidate.provenance.flatMap((source) => source.excerpts ?? []))];
  return <div className="space-y-1.5 px-2 py-1 text-xs">
    {candidate.review_status === "rejected" && <p className="text-destructive">拒绝理由：{candidate.review_reason || "已记录拒绝结论"}</p>}
    {candidate.validation_status !== "passed" && <p className="text-amber-700">
      {candidate.validation_issues.some((issue) => issue.code === "stale_dependency")
        ? "关联实体或关系已被修改、拒绝，需重新识别后才能使用。" : "此项尚未通过系统校验，暂不用于报告。"}
    </p>}
    <div className="flex flex-wrap items-center gap-1">
      {anchors.slice(0, 1).map((anchor, index) => <Button key={`${anchor.evidence_id}:${index}`} size="sm" variant="link" className="h-6 px-1 text-xs"
        onClick={() => onSource(anchor)}>
        {anchor.physical_page_number ? `第 ${anchor.physical_page_number} 页 · ` : ""}查看原文
      </Button>)}
      <Button size="sm" variant="ghost" className="h-6 px-2 text-xs" aria-expanded={showEvidence}
        onClick={() => setShowEvidence((open) => !open)}>识别依据</Button>
      {onReject && candidate.review_status !== "rejected" && <Button size="sm" variant="ghost" className="h-6 px-2 text-xs"
        disabled={busy} onClick={() => onReject(candidate)}>提出异议</Button>}
    </div>
    {showEvidence && <div className="space-y-1 rounded bg-muted/50 p-2">
      <p className="text-muted-foreground">{candidate.provenance.map((source) => ({ document: "源文档", manual: "人工录入", external_record: "外部记录", derived: "计算结果" }[source.kind])).filter((label, index, labels) => labels.indexOf(label) === index).join("、")}</p>
      {!!excerpts.length && <blockquote className="border-l-2 pl-2">{excerpts.join("；")}</blockquote>}
      {anchors.slice(1).map((anchor, index) => <Button key={`${anchor.evidence_id}:${index}`} size="sm" variant="link" className="h-6 px-1 text-xs"
        onClick={() => onSource(anchor)}>查看原文 {index + 2}</Button>)}
      {candidate.bindings.flatMap((binding) => binding.anchors).map((anchor, index) => <Button key={`${anchor.evidence_id}:${index}`} size="sm" variant="link" className="h-6 px-1 text-xs"
        onClick={() => onSource(anchor)}>查看归属依据 {index + 1}</Button>)}
    </div>}
  </div>;
}

function AssertionRow({ candidate, hideLabel = false, children, ...actions }: Actions & {
  candidate: EvidenceCandidate; hideLabel?: boolean; children?: ReactNode;
}) {
    const value = evidenceValue(candidate);
    return <li data-candidate-id={candidate.candidate_id} className="min-w-0">
      <details className="rounded" open={candidate.kind === "relationship"}>
        <summary className="cursor-pointer rounded px-2 py-1.5 text-xs hover:bg-muted/70">
          <span className="inline-flex max-w-[95%] flex-wrap items-baseline gap-1.5 align-top">
            {!hideLabel && <span className="text-muted-foreground" title={candidate.predicate_iri ?? undefined}>{evidenceLabel(candidate)}{value != null ? "：" : ""}</span>}
            {value != null && <span className="break-words font-medium">{value}</span>}
            {assertionLabels[candidate.assertion_status] && <span className="text-amber-700">{assertionLabels[candidate.assertion_status]}</span>}
            <Status candidate={candidate} />
          </span>
        </summary>
        <SourceDetails candidate={candidate} {...actions} />
        {children}
      </details>
    </li>;
}

export const EvidenceGraphTree = memo(function EvidenceGraphTree({ candidates, schema, branchProgress, executionStatus, calculations = [], onCalculation, ...actions }: Props) {
  const graph = useMemo(() => buildDocumentEvidenceGraph(candidates, schema), [candidates, schema]);
  const calculationsBySubject = useMemo(() => {
    const index = new Map<string, PDECalculation[]>();
    for (const value of calculations) {
      const group = index.get(value.subject_candidate_id) ?? [];
      group.push(value);
      index.set(value.subject_candidate_id, group);
    }
    return index;
  }, [calculations]);
  const treeRef = useRef<HTMLDivElement>(null);
  const rendered = new Set(graph.documentRoots.map((item) => item.candidate_id));
  const classLabel = (iri?: string | null) => graph.classes[iri ?? ""]?.label || shortIri(iri);

  function revealEntity(id: string) {
    const documentRoot = graph.documentRoots.some((item) => item.candidate_id === id);
    const node = treeRef.current?.querySelector<HTMLElement>(documentRoot
      ? "[data-document-class]" : `li[data-candidate-id="${CSS.escape(id)}"]`);
    if (!node) return;
    if (node instanceof HTMLDetailsElement) node.open = true;
    let ancestor: HTMLElement | null = node;
    while (ancestor && ancestor !== treeRef.current) {
      if (ancestor instanceof HTMLDetailsElement) ancestor.open = true;
      ancestor = ancestor.parentElement;
    }
    const details = node.querySelector("details");
    if (details) details.open = true;
    node.querySelector("summary")?.focus();
    node.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  function relationEvidence(candidate?: EvidenceCandidate) {
    return candidate && <details className="mx-2 mb-1 rounded bg-muted/30 text-xs">
      <summary className="cursor-pointer px-2 py-1 text-muted-foreground">
        <span className="mr-2">关系识别依据</span>
        {assertionLabels[candidate.assertion_status] && <span className="mr-2 text-amber-700">{assertionLabels[candidate.assertion_status]}</span>}
        <Status candidate={candidate} />
      </summary>
      <SourceDetails candidate={candidate} {...actions} />
    </details>;
  }

  function entityReference(entity: EvidenceCandidate, relation?: EvidenceCandidate) {
    const documentRoot = graph.documentRoots.some((item) => item.candidate_id === entity.candidate_id);
    return <li key={entity.candidate_id} className="px-2 py-1 text-xs">
      <button className="text-primary hover:underline" onClick={() => revealEntity(entity.candidate_id)}>
        ↗ {documentRoot ? "返回文档属性与关系" : `${entity.text}（查看实体）`}
      </button>
      {relationEvidence(relation)}
    </li>;
  }

  function entityNode(entity: EvidenceCandidate, depth: number, relation?: EvidenceCandidate, detached = false): ReactNode {
    const id = entity.candidate_id;
    if (rendered.has(id) || depth >= 8) return entityReference(entity, relation);
    rendered.add(id);
    const properties = graph.properties.get(id) ?? [];
    const relationships = graph.relationships.get(id) ?? [];
    const branches = evidenceBranches(graph.classes[entity.class_iri ?? ""], [...properties, ...relationships]);
    const checks = calculationsBySubject.get(id) ?? [];
    return <li key={id} data-candidate-id={id}>
      <details open={!detached && depth < 2}>
        <summary className="cursor-pointer rounded px-2 py-2 hover:bg-muted/70">
          <span className="inline-flex max-w-[95%] flex-wrap items-center gap-x-2 gap-y-1 align-top">
            <span className="break-words text-sm font-medium">{entity.text}</span>
            <span className="text-[11px] text-muted-foreground" title={entity.class_iri ?? undefined}>{entity.class_label || classLabel(entity.class_iri)}</span>
            {assertionLabels[entity.assertion_status] && <span className="text-xs text-amber-700">{assertionLabels[entity.assertion_status]}</span>}
            <Status candidate={entity} />
            {checks.map((c) => <span key={c.calculation_id} className={`text-xs ${c.blocks_conclusion ? "text-amber-700" : "text-emerald-700"}`}>
              PDE：{c.status === "incomplete" ? "参数待补充" : c.blocks_conclusion ? "存在待处理差异" : "校验已完成"}
            </span>)}
          </span>
        </summary>
        <SourceDetails candidate={entity} {...actions} />
        {relationEvidence(relation)}
        {checks.map((c) => <PDECalculationCard key={c.calculation_id} result={c} busy={actions.busy}
          onSource={actions.onSource} onDecide={onCalculation} />)}
        {branchSections(branches, depth, detached)}
      </details>
    </li>;
  }

  function branchNode(branch: EvidenceBranch, depth: number, referencesOnly: boolean) {
    const key = `${branch.kind}:${branch.iri}`;
    const range = branch.range.map(classLabel).join(" / ");
    const progress = depth === 0 && branch.kind === "relationship" ? branchProgress?.[branch.iri] : undefined;
    const statusText = branch.kind === "property" ? "尚无已识别值"
      : depth === 0 ? branchProgressText(progress, executionStatus) : "尚无已识别关系，处理状态待核对";
    return <li key={key} data-predicate-iri={branch.iri} data-branch-kind={branch.kind} className="min-w-0">
      {!branch.candidates.length ? <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1 px-2 py-1.5 text-xs">
        <span title={branch.iri}>{branch.label}</span>
        {range && <span className="text-[11px] text-muted-foreground">{range}</span>}
        <span className="ml-auto text-muted-foreground" role="status">{statusText}</span>
      </div> : branch.kind === "property" && branch.candidates.length === 1
        ? <ul><AssertionRow candidate={branch.candidates[0]} {...actions} /></ul>
        : <details open={depth < 2}>
          <summary className="cursor-pointer rounded px-2 py-1.5 text-xs hover:bg-muted/70">
            <span title={branch.iri} className="font-medium">{branch.label}</span>
            <span className="ml-2 text-muted-foreground">{branch.candidates.length} {branch.kind === "property" ? "个值" : "条关系"}</span>
            {range && <span className="ml-2 text-[11px] text-muted-foreground">{range}</span>}
          </summary>
          <ul className="ml-3 space-y-1 border-l pl-2">
            {branch.candidates.map((candidate) => {
              if (candidate.kind === "property") return <AssertionRow key={candidate.candidate_id} candidate={candidate} hideLabel {...actions} />;
              const entity = candidate.object && graph.entities.get(candidate.object.candidate_id);
              return entity ? <li key={candidate.candidate_id} data-candidate-id={candidate.candidate_id}>
                <ul>{referencesOnly ? entityReference(entity, candidate) : entityNode(entity, depth + 1, candidate)}</ul>
              </li> : <AssertionRow key={candidate.candidate_id} candidate={candidate} {...actions}>
                <p className="px-2 pb-2 text-xs text-muted-foreground">关联对象尚未识别</p>
              </AssertionRow>;
            })}
          </ul>
        </details>}
      {depth === 0 && branch.kind === "relationship" && progress && <p className="px-2 pb-1 text-[11px] text-muted-foreground">
        {branch.candidates.length > 0 && <span role="status">{branchProgressText(progress, executionStatus)}。 </span>}
        对象任务 {progress.discovery_tasks} · 关系任务 {progress.relationship_tasks} · 未完成任务 {progress.failed_tasks}
        {progress.property_status !== "not_applicable" && progress.property_tasks !== undefined && <span>
          {" · "}属性任务 {progress.property_tasks} · 属性候选 {progress.positive_property_count ?? 0}
        </span>}
        {progress.reason_codes.some((code) => ["source_excerpt_mismatch", "ambiguous_source_quote", "source_quote_outside_scope", "citation_repair_unresolved"].includes(code))
          && <span>；原文引用校验未通过</span>}
      </p>}
    </li>;
  }

  function branchSections(branches: EvidenceBranch[], depth: number, referencesOnly = false): ReactNode {
    const declared = branches.filter((branch) => branch.declared);
    const extra = branches.filter((branch) => !branch.declared);
    return <div className="ml-3 space-y-2 border-l pl-2">
      {(["property", "relationship"] as const).map((kind) => {
        const items = declared.filter((branch) => branch.kind === kind);
        return items.length > 0 && <section key={kind} aria-label={depth === 0 ? (kind === "property" ? "文档属性" : "文档关系") : undefined}>
          <p className="px-2 py-1 text-[11px] font-medium text-muted-foreground">{kind === "property" ? "属性" : "关系"} · {items.length}</p>
          <ul className="space-y-0.5">{items.map((branch) => branchNode(branch, depth, referencesOnly))}</ul>
        </section>;
      })}
      {extra.length > 0 && <details className="text-xs">
        <summary className="cursor-pointer px-2 py-1.5 text-muted-foreground">其他已识别属性与关系（{extra.length}）</summary>
        <ul>{extra.map((branch) => branchNode(branch, depth, referencesOnly))}</ul>
      </details>}
    </div>;
  }

  const mainBranches = branchSections(graph.branches, 0);
  // Deep connected paths stay under the document, with references back to their
  // predicates. They must not be mislabeled as unassociated entities.
  const more: ReactNode[] = [];
  for (const id of graph.connected) if (!rendered.has(id)) more.push(entityNode(graph.entities.get(id)!, 1));
  const groups = new Map<string, EvidenceCandidate[]>();
  for (const entity of graph.unassociated) {
    const iri = entity.class_iri ?? "";
    const group = groups.get(iri) ?? [];
    group.push(entity);
    groups.set(iri, group);
  }
  if (!schema && !candidates.length) return null;
  return <div ref={treeRef} aria-label="关系图谱树" role="region" className="min-w-0">
    <details open data-document-class={graph.documentClass ?? ""} className="rounded border p-2">
      <summary className="cursor-pointer px-1 py-1 text-sm font-semibold">
        {graph.documentClass ? classLabel(graph.documentClass) : "源文档"}
        {graph.documentClass && classLabel(graph.documentClass) !== shortIri(graph.documentClass)
          && <span className="ml-2 text-xs font-normal text-muted-foreground">{shortIri(graph.documentClass)}</span>}
      </summary>
      {schema?.document_label && <p className="break-words px-1 pb-2 text-xs text-muted-foreground">{schema.document_label}</p>}
      {!graph.documentRoots.length && <p className="px-2 py-2 text-xs text-muted-foreground">
        {graph.documentClass ? "尚未识别到文档实体，以下展示该类型的属性和关系。" : "尚未确定源文档类型，识别结果列在下方。"}
      </p>}
      {graph.documentClass && !graph.classes[graph.documentClass] && <p className="px-2 py-1 text-xs text-muted-foreground">暂未取得该类型的本体定义，当前展示已有识别记录。</p>}
      {mainBranches}
      {!!more.length && <details className="mt-2 text-xs">
        <summary className="cursor-pointer px-2 py-1 text-muted-foreground">深层关联实体</summary>
        <ul>{more}</ul>
      </details>}
    </details>
    {!!(graph.unassociated.length + graph.orphans.length) && <details data-unassociated className="mt-3 rounded border p-2">
      <summary className="cursor-pointer text-xs text-muted-foreground">尚未关联到文档（{graph.unassociated.length + graph.orphans.length}）</summary>
      <p className="py-2 text-xs text-muted-foreground">这些结果尚无从源文档出发的关系路径。按实体类型归类展示，可展开查看原文或提出异议。</p>
      {[...groups].sort(([a], [b]) => classLabel(a).localeCompare(classLabel(b), "zh-CN")).map(([iri, entities]) => <details key={iri} data-class-iri={iri} className="ml-2 border-l pl-2">
        <summary className="cursor-pointer px-2 py-1.5 text-xs">{classLabel(iri)}（{entities.length}）</summary>
        <ul>{entities.map((entity) => entityNode(entity, 1, undefined, true))}</ul>
      </details>)}
      {!!graph.orphans.length && <details className="ml-2 border-l pl-2">
        <summary className="cursor-pointer px-2 py-1.5 text-xs">主体尚未识别的属性与关系（{graph.orphans.length}）</summary>
        <ul>{graph.orphans.map((candidate) => <AssertionRow key={candidate.candidate_id} candidate={candidate} {...actions} />)}</ul>
      </details>}
    </details>}
  </div>;
});
