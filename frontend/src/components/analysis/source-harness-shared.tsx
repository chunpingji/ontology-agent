"use client";

import { ExternalLink } from "lucide-react";
import { badgeVariants } from "@/components/ui/badge";
import type { DocumentHarnessCardRef, DocumentHarnessPredicateRef, DocumentHarnessSource, DocumentHarnessSourceRef, DocumentHarnessState, DocumentHarnessVerification } from "@/lib/api";
import { HARNESS_STATES } from "@/lib/source-harness";
import { cn } from "@/lib/utils";

export function StateBadge({ state }: { state: DocumentHarnessState }) {
  return <span className={cn(badgeVariants({ variant: "outline" }), "shrink-0 font-normal",
    state === "accepted" && "border-emerald-600/20 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400",
    state === "rejected" && "border-destructive/20 bg-destructive/5 text-destructive",
    (state === "candidate" || state === "unresolved") && "border-amber-600/20 bg-amber-500/10 text-amber-800 dark:text-amber-400",
  )}>{HARNESS_STATES[state]}</span>;
}

export function HarnessVerificationLabel({ verification }: { verification: DocumentHarnessVerification }) {
  if (!verification.method) return null;
  return <span className="text-xs text-muted-foreground" data-verification-method={verification.method}>
    {verification.method === "rule" ? "规则证明" : "模型核对"}
  </span>;
}

export function OntologyTerm({ term }: { term: DocumentHarnessCardRef }) {
  return <span className="break-words">{term.label}<code className="ml-2 break-all text-xs text-muted-foreground">{term.iri}</code></span>;
}

export function HarnessOntologyContext({ card, predicate }: {
  card: DocumentHarnessCardRef | null; predicate: DocumentHarnessPredicateRef | null;
}) {
  return <div className="space-y-1 text-xs leading-relaxed">
    <p>对齐类型卡：{card ? <OntologyTerm term={card} /> : "未记录"}</p>
    {predicate ? <>
      <p>本体谓词：<OntologyTerm term={predicate} /></p>
      <p className="break-all">所属本体命名空间：{predicate.namespace}</p>
      <p className="break-words">属性定义域：{predicate.domain_text}</p>
    </> : <p>本体谓词上下文：未记录</p>}
  </div>;
}

export function EvidenceList({ evidence, onSource }: { evidence: DocumentHarnessSourceRef[]; onSource: (ref: DocumentHarnessSourceRef) => void }) {
  return <div className="space-y-1">{evidence.map((ref, index) => <button key={`${ref.source_id}:${ref.start}:${index}`} type="button" onClick={() => onSource(ref)} className="flex w-full items-start gap-2 rounded-md border-l-2 border-primary/30 px-2 py-1.5 text-left text-xs hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"><ExternalLink className="mt-0.5 size-3 shrink-0 text-primary" /><span className="min-w-0 whitespace-pre-wrap break-words">{ref.text}<span className="ml-2 text-primary">{ref.page != null ? `第 ${ref.page} 页 · ` : ""}查看原文</span></span></button>)}</div>;
}

export function HarnessSourcePreview({ source, reference }: { source: DocumentHarnessSource; reference: DocumentHarnessSourceRef }) {
  const characters = Array.from(source.text);
  return <>
    <p className="break-all text-xs text-muted-foreground">{source.page != null && <>第 {source.page} 页 · </>}{source.section_id} · {source.block_id}{source.row != null && <> · 行 {source.row}</>}{source.column != null && <> · 列 {source.column}</>}</p>
    <blockquote className="whitespace-pre-wrap break-words rounded-md bg-muted/30 p-4 text-sm leading-7">{characters.slice(0, reference.start).join("")}<mark className="rounded bg-primary/15 px-0.5 text-foreground">{characters.slice(reference.start, reference.end).join("")}</mark>{characters.slice(reference.end).join("")}</blockquote>
  </>;
}
