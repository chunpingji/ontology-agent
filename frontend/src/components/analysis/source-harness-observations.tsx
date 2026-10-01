"use client";

import { useState } from "react";
import { ChevronLeft, ChevronRight, Search } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import type { DocumentHarnessGraph, DocumentHarnessObservation, DocumentHarnessSourceRef } from "@/lib/api";
import { displayValue, filterHarnessObservations, HARNESS_WORK_REASONS, HARNESS_WORK_STATES, hasObservationContext, observationResults, observationSubjects, OBSERVATION_KINDS, OBSERVATION_RESULTS } from "@/lib/source-harness";
import { EvidenceList, HarnessOntologyContext, HarnessVerificationLabel, OntologyTerm, StateBadge } from "./source-harness-shared";

interface ObservationProps {
  graph: DocumentHarnessGraph;
  selectedEntity: string | null;
  onSelectEntity: (id: string) => void;
  onSource: (source: DocumentHarnessSourceRef) => void;
  subjectFilter?: string;
  onSubjectFilter?: (subject: string) => void;
}

export function HarnessObservations({ graph, selectedEntity, onSelectEntity, onSource, subjectFilter, onSubjectFilter }: ObservationProps) {
  const [kind, setKind] = useState("all");
  const [localSubject, setLocalSubject] = useState("all");
  const subject = subjectFilter ?? localSubject;
  const setSubject = onSubjectFilter ?? setLocalSubject;
  const [result, setResult] = useState("all");
  const [query, setQuery] = useState("");
  const [paging, setPaging] = useState({ key: "", page: 1 });
  const [detailId, setDetailId] = useState<string | null>(null);
  const entities = new Map(graph.entities.map((item) => [item.id, item]));
  const properties = new Map(graph.properties.map((item) => [item.id, item]));
  const validContext = graph.observations.every(hasObservationContext);
  const visible = validContext ? filterHarnessObservations(graph, kind, subject, result, selectedEntity, query) : [];
  const failures = graph.observations.filter((item) => item.kind === "failure");
  const total = graph.observations.length - failures.length;
  const key = JSON.stringify([kind, subject, result, query, subject === "selected" ? selectedEntity : null]);
  const pages = Math.max(1, Math.ceil(visible.length / 10));
  const page = Math.min(pages, paging.key === key ? paging.page : 1);
  // Persist the reset, so returning to an earlier filter cannot restore its old page.
  if (paging.key !== key || paging.page > pages) setPaging({ key, page });
  const detail = validContext ? graph.observations.find((item) => item.id === detailId) : undefined;
  const reset = () => { setKind("all"); setSubject("all"); setResult("all"); setQuery(""); };
  const selectedSubject = subject === "selected" ? selectedEntity : subject.startsWith("entity:") ? subject.slice(7) : null;
  const selectClass = "h-10 max-w-full rounded-md border border-input bg-background px-3 text-sm";

  return <section className="min-w-0 space-y-4" aria-label="原文观察与对齐结果">
    <div className="flex flex-wrap items-center justify-between gap-2"><h3 className="text-lg font-semibold">原文观察与对齐结果（{total}）</h3><span className="text-xs text-muted-foreground">原文与本体映射分别查看</span></div>
    <p className="text-xs leading-relaxed text-muted-foreground">原文观察会在属性采信后保留，观察数量不等于未采信数量。参考卡仅用于发现，主体与属性的确认状态分别展示。</p>
    {!validContext && <p role="alert" className="text-sm text-muted-foreground">观察上下文暂不可用，请稍后刷新。其他已保存的实体和属性仍可查看。</p>}
    <div className="flex flex-wrap gap-2">
      <select aria-label="观察类别" value={kind} onChange={(event) => setKind(event.target.value)} className={selectClass}><option value="all">全部类别</option>{Object.entries(OBSERVATION_KINDS).filter(([value]) => value !== "failure").map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select>
      <select aria-label="观察主体" value={subject} onChange={(event) => setSubject(event.target.value)} className={`${selectClass} sm:max-w-64`}><option value="all">全部主体</option><option value="selected" disabled={!selectedEntity}>当前主体：{entities.get(selectedEntity ?? "")?.label ?? "未选择"}</option><option value="unowned">主体尚未确定</option>{graph.entities.map((entity) => <option key={entity.id} value={`entity:${entity.id}`}>{entity.label} · {entity.id}</option>)}</select>
      <select aria-label="观察处理结果" value={result} onChange={(event) => setResult(event.target.value)} className={selectClass}><option value="all">全部处理结果</option>{Object.entries(OBSERVATION_RESULTS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select>
      <label className="relative min-w-52 flex-1"><Search className="absolute left-3 top-3 size-4 text-muted-foreground" /><Input className="pl-9" aria-label="搜索原文、属性或本体卡" placeholder="搜索原文、属性或本体卡" value={query} onChange={(event) => setQuery(event.target.value)} /></label>
      <Button variant="ghost" onClick={reset}>重置</Button>
    </div>
    {selectedEntity && <div className="flex flex-wrap items-center gap-2 text-xs"><span className="text-muted-foreground">当前选中：{entities.get(selectedEntity)?.label}</span><button type="button" className="text-primary hover:underline" onClick={() => setSubject("selected")}>只看该实体的观察 →</button></div>}
    {!visible.length ? <div role="status" className="rounded-md border border-dashed p-8 text-center text-sm text-muted-foreground">{!validContext ? "暂时无法读取观察列表。" : total ? "没有符合筛选条件的观察。" : "尚无已保存的原文观察。"}{validContext && total > 0 && <Button variant="link" onClick={reset}>清除筛选</Button>}</div> : <>
      <div className="overflow-x-auto rounded-lg border"><table className="w-full min-w-[950px] text-left text-xs"><thead className="bg-muted/40 text-muted-foreground"><tr>{["原文观察 / 位置", "类别", "候选主体", "本体卡 → 属性或实体", "处理结果", "操作"].map((label) => <th key={label} scope="col" className="px-3 py-3 font-normal">{label}</th>)}</tr></thead><tbody>
        {visible.slice((page - 1) * 10, page * 10).map((item) => {
          const subjects = observationSubjects(item);
          const outcomes = observationResults(item, properties, selectedSubject);
          return <tr key={item.id} className="border-t align-top hover:bg-muted/20">
            <td className="max-w-80 px-3 py-4"><button type="button" className="line-clamp-3 break-words text-left text-sm font-medium hover:text-primary" onClick={() => setDetailId(item.id)}>{item.label}{item.value != null && `：${item.value}`}</button><p className="mt-1 text-muted-foreground">{item.evidence[0]?.page != null ? `p.${item.evidence[0].page} · ` : ""}<span className="break-all">{item.id}</span></p></td>
            <td className="px-3 py-4"><Badge variant="outline">{OBSERVATION_KINDS[item.kind]}</Badge></td>
            <td className="max-w-60 space-y-2 px-3 py-4">{subjects.length ? subjects.map((id) => <button key={id} type="button" className="block break-words text-left text-primary hover:underline" onClick={() => onSelectEntity(id)}>{entities.get(id)?.label ?? id}</button>) : <span className="text-muted-foreground">尚未确定</span>}{item.object_id && <p className="break-words text-muted-foreground">对象：{entities.get(item.object_id)?.label ?? item.object_id}</p>}</td>
            <td className="max-w-80 space-y-2 px-3 py-4">{item.alignments.length ? item.alignments.map((alignment, index) => <div key={index}><p className="break-words font-medium">{alignment.card?.label ?? "未记录对齐卡"}</p><p className="break-words text-muted-foreground">主体：{entities.get(alignment.subject_id)?.label ?? alignment.subject_id}</p><p className="mt-1 break-words text-muted-foreground">{alignment.property_ids.map((id) => { const property = properties.get(id); return property?.subject_id === alignment.subject_id ? property.label : null; }).filter(Boolean).join(" / ") || "暂无属性映射"}</p></div>) : <span className="text-muted-foreground">{item.kind === "entity" ? "实体提及 · 当前类型见主体详情" : "尚无对齐结果"}</span>}</td>
            <td className="max-w-52 px-3 py-4"><div className="flex flex-wrap gap-1">{outcomes.length ? outcomes.map((state) => <Badge key={state} variant="outline" className={state === "accepted" ? "border-emerald-600/20 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400" : "text-muted-foreground"}>{OBSERVATION_RESULTS[state]}</Badge>) : <span className="text-muted-foreground">观察保留</span>}</div></td>
            <td className="px-3 py-4"><Button variant="link" size="sm" className="h-auto whitespace-nowrap p-0" aria-label={`查看观察 ${item.id} 详情`} onClick={() => setDetailId(item.id)}>查看详情 ↗</Button></td>
          </tr>;
        })}
      </tbody></table></div>
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground"><p aria-live="polite">显示 {(page - 1) * 10 + 1}–{Math.min(page * 10, visible.length)} 条，共 {visible.length} 条{visible.length !== total && `（全部 ${total} 条）`}</p><div className="flex items-center gap-2"><Button size="sm" variant="outline" disabled={page === 1} onClick={() => setPaging({ key, page: page - 1 })}><ChevronLeft className="size-4" />上一页</Button><span>{page} / {pages}</span><Button size="sm" variant="outline" disabled={page === pages} onClick={() => setPaging({ key, page: page + 1 })}>下一页<ChevronRight className="size-4" /></Button></div></div>
    </>}
    <HarnessCandidateWorkList graph={graph} onSource={onSource} onSelectEntity={onSelectEntity} />
    <details className="border-t pt-3"><summary className="cursor-pointer text-sm font-medium">模型调用失败（{failures.length}）</summary><p className="my-3 text-xs text-muted-foreground">技术失败单独记录，不作为原文未匹配或未采信原因。</p>{failures.map((item) => <div key={item.id} className="space-y-1 rounded-md border p-3 text-sm"><p>{item.label}</p><p className="whitespace-pre-wrap break-words text-xs text-destructive">{item.reason}</p></div>)}</details>
    <Dialog open={Boolean(detail)} onOpenChange={(open) => { if (!open) setDetailId(null); }}>
      <DialogContent className="max-h-[90dvh] w-[calc(100%_-_2rem)] max-w-4xl overflow-y-auto">
        <DialogHeader><DialogTitle>原文观察详情</DialogTitle><DialogDescription className="break-all">{detail ? `${detail.id} · ${OBSERVATION_KINDS[detail.kind]}` : "逐主体查看对齐结果"}</DialogDescription></DialogHeader>
        {detail && <HarnessObservationDetail graph={graph} item={detail} onSelectEntity={(id) => { setDetailId(null); onSelectEntity(id); }} onSource={(ref) => { setDetailId(null); onSource(ref); }} />}
      </DialogContent>
    </Dialog>
  </section>;
}

export function HarnessObservationDetail({ graph, item, onSelectEntity, onSource }: {
  graph: DocumentHarnessGraph; item: DocumentHarnessObservation;
  onSelectEntity: ObservationProps["onSelectEntity"]; onSource: ObservationProps["onSource"];
}) {
  const entities = new Map(graph.entities.map((entity) => [entity.id, entity]));
  const properties = new Map(graph.properties.map((property) => [property.id, property]));
  return <div className="space-y-6">
    <div className="space-y-3 rounded-md bg-muted/40 p-4"><h4 className="text-sm font-semibold">原文观察：{item.label}</h4>{item.value != null && <p className="whitespace-pre-wrap break-words text-sm">完整原值：{item.value}</p>}<EvidenceList evidence={item.evidence} onSource={onSource} />{!item.evidence.length && <p className="text-xs text-muted-foreground">未记录精确原文引用。</p>}</div>
    <div className="space-y-2"><h4 className="text-sm font-semibold">发现时参考的本体卡</h4><ul className="space-y-2 text-xs">{item.discovery_cards.map((card) => <li key={`${card.role}:${card.iri}`}><Badge variant="outline" className="mb-1 mr-2">{card.role === "document_properties" ? "文档根属性指引" : "阅读卡"}</Badge><OntologyTerm term={card} /></li>)}</ul>{!item.discovery_cards.length && <p className="text-xs text-muted-foreground">未记录发现参考卡。</p>}<p className="text-xs text-muted-foreground">参考卡用于发现，不代表实际采用的类型或属性。</p></div>
    <div className="space-y-3 border-t pt-4"><h4 className="text-sm font-semibold">候选主体与当前类型</h4>
      {!observationSubjects(item).length && <p className="text-xs">候选主体：尚未确定</p>}
      {observationSubjects(item).map((id) => { const entity = entities.get(id); return <div key={id} className="space-y-1 text-xs"><p>候选主体：<button type="button" className="text-primary underline underline-offset-2" onClick={() => onSelectEntity(id)}>{entity?.label ?? id}</button>{entity && <span className="ml-2"><StateBadge state={entity.state} /></span>}</p><p>主体当前类型：{entity?.class_iri ? <OntologyTerm term={{ iri: entity.class_iri, label: entity.class_label || entity.class_iri }} /> : "尚未对齐"}</p>{item.field_id && !item.alignments.some((alignment) => alignment.subject_id === id) && <p className="text-muted-foreground">尚无该主体的属性对齐结果；字段归属仍须独立核对。</p>}</div>; })}
      {item.object_id && <p className="text-xs">关系对象：<button type="button" className="text-primary underline" onClick={() => onSelectEntity(item.object_id!)}>{entities.get(item.object_id)?.label ?? item.object_id}</button></p>}
    </div>
    <div className="space-y-4"><h4 className="text-sm font-semibold">按主体查看对齐结果</h4>
      {!item.alignments.length && <p className="text-sm text-muted-foreground">尚无已记录的属性对齐结果。观察与原文继续保留。</p>}
      {item.alignments.map((alignment, index) => <div key={`${alignment.subject_id}:${alignment.card?.iri}:${index}`} className="space-y-3 rounded-lg border p-4 text-xs">
        <p className="text-sm font-semibold">属性对齐主体：{entities.get(alignment.subject_id)?.label ?? alignment.subject_id}</p>
        <p className="rounded bg-primary/5 p-3">本次对齐类型卡：{alignment.card ? <OntologyTerm term={alignment.card} /> : "未记录"}</p>
        {alignment.property_ids.map((id) => { const property = properties.get(id); return property && property.subject_id === alignment.subject_id ? <div key={id} className="space-y-2 border-l-2 border-primary/30 pl-3">
          <div className="flex flex-wrap items-center gap-2"><Badge variant="secondary">数据属性</Badge><p className="font-medium">{property.label}：{displayValue(property.value)}</p><Badge variant="outline">对齐成功</Badge><StateBadge state={property.state} /><HarnessVerificationLabel verification={property.verification} /></div>
          <HarnessOntologyContext card={property.card} predicate={property.predicate} /><p className="whitespace-pre-wrap break-words">属性核对原因：{property.reason}</p>
          {property.value_evidence?.length > 0 && <div className="space-y-1"><p className="text-muted-foreground">取值位置</p><EvidenceList evidence={property.value_evidence} onSource={onSource} /></div>}
        </div> : <p key={id} className="text-muted-foreground">关联属性不可用：{id}</p>; })}
        {alignment.attempts.filter((attempt) => attempt.state !== "mapped").map((attempt, index) => <div key={index} className="space-y-2 border-l-2 border-muted-foreground/30 pl-3"><p className="font-medium">{attempt.state === "unmatched" ? "该属性菜单未匹配" : "属性对齐异常"}</p><p className="whitespace-pre-wrap break-words">{attempt.reason}</p><details><summary className="cursor-pointer">{attempt.state === "unmatched" ? "已检查的属性菜单" : "涉及的属性"}（{attempt.predicates.length}）</summary>{attempt.predicates.map((predicate) => <div key={predicate.iri} className="mt-2"><HarnessOntologyContext card={alignment.card} predicate={predicate} /></div>)}{!attempt.predicates.length && <p className="mt-2">当前类型卡没有合法属性。</p>}</details></div>)}
        <Button variant="outline" size="sm" onClick={() => onSelectEntity(alignment.subject_id)}>在实体树中定位主体</Button>
      </div>)}
    </div>
    <p className="whitespace-pre-wrap break-words border-t pt-3 text-xs text-muted-foreground">观察记录说明（不代表各主体的最终判定）：{item.reason}</p>
    <p className="text-xs text-muted-foreground">未保存的卡片来源显示“未记录”，不从主体当前类型倒推；不同主体和菜单的判定分别保留。</p>
  </div>;
}

export function HarnessCandidateWorkList({ graph, onSource, onSelectEntity }: {
  graph: DocumentHarnessGraph;
  onSource: ObservationProps["onSource"];
  onSelectEntity: ObservationProps["onSelectEntity"];
}) {
  const [status, setStatus] = useState("all");
  const [page, setPage] = useState(1);
  const visible = graph.candidate_work.filter((item) => status === "all" || item.status === status);
  const pages = Math.max(1, Math.ceil(visible.length / 10));
  const currentPage = Math.min(page, pages);
  const endpoints = new Map<string, { id: string; label: string }>();
  for (const entity of graph.entities) {
    endpoints.set(entity.id, { id: entity.id, label: entity.label });
    for (const mention of entity.mentions ?? []) endpoints.set(mention.id, { id: entity.id, label: mention.label });
  }
  const endpoint = (id: string) => {
    const entity = endpoints.get(id);
    return entity
      ? <button type="button" className="break-words text-primary hover:underline" onClick={() => onSelectEntity(entity.id)}>{entity.label}</button>
      : <span className="break-all">{id}</span>;
  };
  return <section className="space-y-3 border-t pt-4" aria-label="候选执行情况">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h4 className="text-sm font-semibold">候选执行情况（{graph.candidate_work.length}）</h4>
      <select aria-label="候选任务状态" className="rounded-md border bg-background p-2 text-xs" value={status}
        onChange={(event) => { setStatus(event.target.value); setPage(1); }}>
        <option value="all">全部任务状态</option>
        {Object.entries(HARNESS_WORK_STATES).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
      </select>
    </div>
    <p className="text-xs text-muted-foreground">候选执行情况与事实采信分别记录。剪枝表示本轮未处理；已处理任务也可能没有形成事实。</p>
    {!visible.length && <p className="text-xs text-muted-foreground">暂无符合条件的候选任务。</p>}
    {visible.slice((currentPage - 1) * 10, currentPage * 10).map((item) => <div key={item.id}
      className="space-y-2 rounded-md border p-3 text-xs" data-candidate-work-id={item.id} data-work-status={item.status}>
      <div className="flex flex-wrap items-center gap-2"><Badge variant="outline">{item.kind === "relation_alignment" ? "关系候选" : "共指候选"}</Badge><span>{HARNESS_WORK_STATES[item.status]}</span></div>
      <p>{endpoint(item.subject_id)}<span className="mx-2">{item.kind === "coreference_review" ? "与" : "→"}</span>
        {item.object_ids.map((id, index) => <span key={id}>{index > 0 && "、"}{endpoint(id)}</span>)}</p>
      {item.predicate_iri && <p className="break-all text-muted-foreground">候选谓词：{item.predicate_iri}</p>}
      {item.reason_code && <p>{HARNESS_WORK_REASONS[item.reason_code] ?? item.reason_code}</p>}
      <EvidenceList evidence={item.evidence} onSource={onSource} />
    </div>)}
    {pages > 1 && <div className="flex items-center justify-end gap-2 text-xs">
      <Button variant="outline" size="sm" disabled={currentPage === 1} onClick={() => setPage(currentPage - 1)}>上一页候选</Button>
      <span>{currentPage} / {pages}</span>
      <Button variant="outline" size="sm" disabled={currentPage === pages} onClick={() => setPage(currentPage + 1)}>下一页候选</Button>
    </div>}
  </section>;
}
