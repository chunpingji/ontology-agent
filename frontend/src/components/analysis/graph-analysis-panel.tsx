"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { ListTodo, Loader2, Pause, Play, Plus, RefreshCw } from "lucide-react";

import { DocumentAnalysisHistory } from "@/components/analysis/document-analysis-history";
import { GraphAnalysisUpload } from "@/components/analysis/graph-analysis-upload";
import { SourceHarnessPanel } from "@/components/analysis/source-harness-panel";
import { TargetGraphCanvas } from "@/components/analysis/target-graph-canvas";
import { WordViewer } from "@/components/extraction/word-viewer";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  controlDocumentAnalysisRun, createReportDocumentRun, getDocumentAnalysisRun,
  getDocumentAnalysisSource, getDocumentAnalysisSourceSelection, getDocumentAnalysisTargetGraph,
  getReportDocumentRun, listDocuments, mergeDocumentAnalysisControlReceipt, formatDocumentGraphQuantity,
  DOCUMENT_HARNESS_PROTOCOL,
  type DocumentAnalysisRun, type DocumentAnalysisSourceArtifact, type DocumentAnalysisTarget,
  type DocumentAnalysisTargetGraph, type DocumentGraphEntity, type DocumentGraphProperty, type EntityShadow, type EvidenceAnchor,
} from "@/lib/api";
import { DOCUMENT_ANALYSIS_STATUS_LABELS } from "@/lib/document-analysis";
import { entityRefKey, graphEntityLabel, graphPredicateLabel, graphQualifierText, GROUP_SELECTION_LABELS } from "@/lib/document-graph";
import { assertionEndpointEntities, assertionReviewState, assertionSourceRefs, candidateGraphCounts, isSupportedTargetAssertion, targetAssertionQualifier, targetAssertions, targetGraphSubjects, targetReviewCounts, visibleTargetAssertions, REVIEW_STATE_LABELS, TARGET_STATE_LABELS } from "@/lib/target-graph";
import { cn } from "@/lib/utils";

function message(error: unknown): string {
  return error instanceof Error ? error.message : "请求失败，请重试。";
}

export function GraphAnalysisPanel() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const documentIri = params.get("documentIri") ?? "";
  const explicitRunId = params.get("documentRun") || params.get("run");
  const [documents, setDocuments] = useState<EntityShadow[]>([]);
  const [documentError, setDocumentError] = useState<string | null>(null);
  const [latest, setLatest] = useState<{ iri: string; run: DocumentAnalysisRun | null } | null>(null);
  const [lookupError, setLookupError] = useState<{ iri: string; message: string } | null>(null);
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);
  const [sourceMode, setSourceMode] = useState(documentIri ? "report" : "upload");
  const [reload, setReload] = useState(0);
  const [harnessTaskCount, setHarnessTaskCount] = useState<{ runId: string; count: number | null } | null>(null);
  const [openTaskDrawerRunId, setOpenTaskDrawerRunId] = useState<string | null>(null);
  const creationController = useRef<AbortController | null>(null);
  const activeRunId = explicitRunId ?? (latest?.iri === documentIri ? latest.run?.recognition_run_id : null) ?? null;
  const currentTaskCount = harnessTaskCount?.runId === activeRunId ? harnessTaskCount.count : null;
  const taskDrawerOpen = activeRunId !== null && openTaskDrawerRunId === activeRunId;
  const onTaskDrawerOpenChange = useCallback((open: boolean) => {
    setOpenTaskDrawerRunId(open ? activeRunId : null);
  }, [activeRunId]);
  const onTaskCountChange = useCallback((count: number | null) => {
    if (!activeRunId) return;
    setHarnessTaskCount((current) => current?.runId === activeRunId && current.count === count
      ? current : { runId: activeRunId, count });
  }, [activeRunId]);
  const searching = Boolean(documentIri && !explicitRunId && latest?.iri !== documentIri && lookupError?.iri !== documentIri);

  useEffect(() => {
    if (activeRunId || sourceMode !== "report") return;
    const controller = new AbortController();
    void listDocuments(undefined, 500, controller.signal).then((result) => {
      if (controller.signal.aborted) return;
      setDocuments(result.items);
      setDocumentError(null);
    }).catch((error: unknown) => { if (!controller.signal.aborted) setDocumentError(message(error)); });
    return () => controller.abort();
  }, [reload, activeRunId, sourceMode]);

  useEffect(() => {
    if (!documentIri || explicitRunId) return;
    const controller = new AbortController();
    void getReportDocumentRun(documentIri, controller.signal).then((result) => {
      if (!controller.signal.aborted) { setLatest({ iri: documentIri, run: result.run }); setLookupError(null); }
    }).catch((error: unknown) => {
      if (!controller.signal.aborted) setLookupError({ iri: documentIri, message: message(error) });
    });
    return () => controller.abort();
  }, [documentIri, explicitRunId, reload]);

  useEffect(() => () => creationController.current?.abort(), [documentIri]);

  const select = (iri: string, runId: string | null) => {
    setHarnessTaskCount(null);
    setOpenTaskDrawerRunId(null);
    const next = new URLSearchParams(params.toString());
    next.set("tab", "graph-analysis");
    if (iri) next.set("documentIri", iri); else next.delete("documentIri");
    if (runId) next.set("documentRun", runId); else next.delete("documentRun");
    next.delete("run");
    next.delete("job_id");
    next.delete("node_id");
    router.replace(`${pathname}?${next}`, { scroll: false });
    setStartError(null);
  };

  const start = async () => {
    if (!documentIri || starting) return;
    const controller = new AbortController();
    creationController.current = controller;
    setStarting(true);
    setStartError(null);
    try {
      const requestKey = globalThis.crypto?.randomUUID?.()
        ?? `graph-report:${Date.now()}-${Math.random().toString(16).slice(2)}`;
      const receipt = await createReportDocumentRun(documentIri, requestKey, controller.signal);
      if (!controller.signal.aborted) select(documentIri, receipt.recognition_run_id);
    } catch (error) {
      if (!controller.signal.aborted) setStartError(message(error));
    } finally {
      if (creationController.current === controller) setStarting(false);
    }
  };

  return <div className="space-y-5">
    <header className="flex flex-wrap items-start justify-between gap-4" data-testid="graph-analysis-header">
      <div className="space-y-2"><h1 className="text-xl font-semibold tracking-tight">图谱分析</h1>
        <p className="text-xs text-muted-foreground">沿文档根逐层查看实体、属性与原文依据。</p>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        {activeRunId && <Button size="sm" variant="outline" disabled={starting} onClick={() => {
          setSourceMode("upload");
          select("", null);
        }}><Plus className="size-3.5" />新建分析</Button>}
        <Button size="sm" variant="outline" onClick={() => setReload((value) => value + 1)}><RefreshCw className="size-3.5" />刷新结果</Button>
        {activeRunId && currentTaskCount !== null && <Button variant="outline" size="icon" className="relative" aria-label={`人工确认任务，待处理 ${currentTaskCount} 项`} title="人工确认任务" onClick={() => onTaskDrawerOpenChange(true)}>
          <ListTodo className="size-4" />
          {currentTaskCount > 0 && <span aria-hidden="true" className="absolute -right-2 -top-2 flex min-w-5 items-center justify-center rounded-full bg-amber-600 px-1 text-[10px] font-semibold leading-5 text-white">{currentTaskCount > 99 ? "99+" : currentTaskCount}</span>}
        </Button>}
      </div>
    </header>
    {!activeRunId && <Card aria-label="创建图谱分析">
      <CardHeader className="pb-3"><CardTitle className="text-base">选择文档进行图谱分析</CardTitle></CardHeader>
      <CardContent className="space-y-3 p-4">
        <Tabs value={sourceMode} onValueChange={setSourceMode}>
          <TabsList aria-label="图谱分析文档来源" className="mb-4">
            <TabsTrigger value="upload" disabled={starting}>上传新文档</TabsTrigger>
            <TabsTrigger value="report" disabled={starting}>选择已有报告</TabsTrigger>
          </TabsList>
          <TabsContent value="upload">
            <GraphAnalysisUpload starting={starting} onStartingChange={setStarting} onCreated={(runId) => select("", runId)} />
          </TabsContent>
          <TabsContent value="report" className="space-y-3">
            <div className="flex flex-wrap items-end gap-3">
              <div className="min-w-60 flex-1 space-y-1.5 text-sm">
                <label htmlFor="graph-analysis-report">报告文档</label>
                <select id="graph-analysis-report" value={documentIri} disabled={starting} onChange={(event) => select(event.target.value, null)} className="flex h-10 w-full rounded-md border border-input bg-background px-3 text-sm focus:outline-none focus:ring-2 focus:ring-ring">
                  <option value="">选择已上传的报告</option>
                  {documentIri && !documents.some((document) => document.iri === documentIri) && <option value={documentIri}>{documentIri.split("#").at(-1)}</option>}
                  {documents.map((document) => <option key={document.iri} value={document.iri}>{document.label_zh || document.label_en || document.iri.split("#").at(-1)}</option>)}
                </select>
              </div>
              <Button variant="outline" aria-label="刷新报告列表" disabled={starting} onClick={() => setReload((value) => value + 1)}><RefreshCw className="size-4" /></Button>
              <Button disabled={!documentIri || starting || searching} onClick={() => void start()}>{starting ? <Loader2 className="size-4 animate-spin" /> : <Play className="size-4" />}{starting ? "正在创建分析" : "开始分析"}</Button>
            </div>
            {searching && <p className="text-sm text-muted-foreground" role="status">正在查找报告的现有运行…</p>}
            {[documentError, lookupError?.iri === documentIri ? lookupError.message : null, startError].filter(Boolean).map((error, index) => <p key={index} role="alert" className="break-words text-sm text-destructive">{error}</p>)}
          </TabsContent>
        </Tabs>
        <p className="text-xs text-muted-foreground">选择文件和本体类型不会启动任务；点击“开始分析”启动识别。切换报告、查看历史和刷新页面只读取结果。</p>
      </CardContent>
    </Card>}
    {activeRunId && <GraphAnalysisRunView key={activeRunId} runId={activeRunId} refreshRevision={reload} taskDrawerOpen={taskDrawerOpen} onTaskDrawerOpenChange={onTaskDrawerOpenChange} onTaskCountChange={onTaskCountChange} />}
    <details className="rounded-xl border bg-card p-4" open={!activeRunId}>
      <summary className="cursor-pointer text-sm font-medium">分析历史</summary>
      <div className="mt-3"><DocumentAnalysisHistory activeRunId={activeRunId} currentRun={null} onSelect={(runId) => select("", runId)} /></div>
    </details>
  </div>;
}

function GraphAnalysisRunView({ runId, refreshRevision, taskDrawerOpen, onTaskDrawerOpenChange, onTaskCountChange }: {
  runId: string; refreshRevision: number; taskDrawerOpen: boolean;
  onTaskDrawerOpenChange: (open: boolean) => void;
  onTaskCountChange: (count: number | null) => void;
}) {
  const [run, setRun] = useState<DocumentAnalysisRun | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    void getDocumentAnalysisRun(runId, controller.signal).then((value) => {
      if (controller.signal.aborted) return;
      if (value.recognition_run_id !== runId) throw new Error("读取的运行与当前选择不一致。");
      setRun(value);
      setError(null);
    }).catch((failure: unknown) => {
      if (!controller.signal.aborted) setError(message(failure));
    });
    return () => controller.abort();
  }, [runId, retry, refreshRevision]);

  useEffect(() => {
    if (run && run.extraction_protocol !== DOCUMENT_HARNESS_PROTOCOL) onTaskCountChange(null);
  }, [run, onTaskCountChange]);

  if (error) return <div className="space-y-2"><p role="alert" className="text-sm text-destructive">{error}</p><Button variant="outline" onClick={() => setRetry((value) => value + 1)}>重新读取运行</Button></div>;
  if (!run) return <p role="status" className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="size-4 animate-spin" />正在读取运行…</p>;
  return run.extraction_protocol === DOCUMENT_HARNESS_PROTOCOL
    ? <SourceHarnessPanel key={runId} initialRun={run} refreshRevision={refreshRevision} taskDrawerOpen={taskDrawerOpen} onTaskDrawerOpenChange={onTaskDrawerOpenChange} onTaskCountChange={onTaskCountChange} />
    : <LegacyGraphAnalysisRunView key={runId} runId={runId} refreshRevision={refreshRevision} />;
}

function LegacyGraphAnalysisRunView({ runId, refreshRevision }: { runId: string; refreshRevision: number }) {
  const [run, setRun] = useState<DocumentAnalysisRun | null>(null);
  const [artifact, setArtifact] = useState<DocumentAnalysisTargetGraph | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);
  const [busy, setBusy] = useState<"pause" | "resume" | null>(null);
  const [source, setSource] = useState<DocumentAnalysisSourceArtifact | null>(null);
  const [sourceError, setSourceError] = useState<string | null>(null);
  const [selectionRef, setSelectionRef] = useState<string | null>(null);
  const [anchor, setAnchor] = useState<{ selectionRef: string; value: EvidenceAnchor | null } | null>(null);
  const [selectedSubject, setSelectedSubject] = useState<string | null>(null);
  const [selectedTarget, setSelectedTarget] = useState<string | null>(null);
  const [showNotAccepted, setShowNotAccepted] = useState(false);
  const controlController = useRef<AbortController | null>(null);
  const previewRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | null = null;
    const refresh = async () => {
      const results = await Promise.allSettled([
        getDocumentAnalysisRun(runId, controller.signal),
        getDocumentAnalysisTargetGraph(runId, controller.signal),
      ]);
      if (controller.signal.aborted) return;
      const [runResult, graphResult] = results;
      if (runResult.status === "fulfilled") setRun(runResult.value);
      if (graphResult.status === "fulfilled") setArtifact(graphResult.value);
      const failures = results.filter((result) => result.status === "rejected");
      setError(failures.map((result) => result.status === "rejected" ? message(result.reason) : "").join("；") || null);
      if (runResult.status === "fulfilled" && ["queued", "running"].includes(runResult.value.status)) timer = setTimeout(() => void refresh(), 2500);
    };
    void refresh();
    return () => { controller.abort(); if (timer) clearTimeout(timer); };
  }, [runId, nonce, refreshRevision]);

  const sourceAvailable = run?.artifacts.source === "ready" || run?.artifacts.source === "partial";
  useEffect(() => {
    if (!sourceAvailable) return;
    const controller = new AbortController();
    void getDocumentAnalysisSource(runId, undefined, controller.signal).then((value) => {
      if (controller.signal.aborted) return;
      if (value.recognition_run_id !== runId) throw new Error("原文与当前运行不一致。");
      setSource(value);
      setSourceError(null);
    }).catch((failure: unknown) => { if (!controller.signal.aborted) setSourceError(message(failure)); });
    return () => controller.abort();
  }, [runId, sourceAvailable, nonce]);

  useEffect(() => {
    if (!selectionRef || !source) return;
    const controller = new AbortController();
    void getDocumentAnalysisSourceSelection(runId, selectionRef, controller.signal).then((selection) => {
      if (controller.signal.aborted) return;
      if (selection.recognition_run_id !== runId || selection.analysis_id !== source.analysis_id || selection.document_hash !== source.document_hash || selection.structure_hash !== source.structure_hash) throw new Error("证据定位与当前原文不一致，已拒绝显示。");
      if (!selection.anchors.length) throw new Error("该证据暂时没有可定位的原文锚点。");
      setAnchor({ selectionRef, value: selection.anchors[0] });
      setSourceError(null);
      previewRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    }).catch((failure: unknown) => { if (!controller.signal.aborted) setSourceError(message(failure)); });
    return () => controller.abort();
  }, [runId, selectionRef, source]);

  useEffect(() => () => controlController.current?.abort(), []);
  const control = async (action: "pause" | "resume") => {
    if (!run || busy) return;
    const controller = new AbortController();
    controlController.current = controller;
    setBusy(action);
    try {
      const receipt = await controlDocumentAnalysisRun(runId, action, run.run_revision, crypto.randomUUID(), action === "pause" ? "图谱分析页面暂停" : "图谱分析页面继续", controller.signal);
      if (controller.signal.aborted) return;
      setRun((current) => mergeDocumentAnalysisControlReceipt(current, receipt));
      setNonce((value) => value + 1);
    } catch (failure) { if (!controller.signal.aborted) setError(message(failure)); }
    finally { if (!controller.signal.aborted) setBusy(null); }
  };
  const selectSubject = useCallback((id: string) => { setSelectedSubject(id); setSelectedTarget(null); }, []);
  const selectTarget = useCallback((id: string) => setSelectedTarget(id), []);
  const focusTarget = artifact?.targets.find((target) => target.target_id === selectedTarget) ?? null;
  const subjectId = focusTarget ? entityRefKey(focusTarget.subject_ref) : selectedSubject ?? (artifact ? entityRefKey(artifact.root) : null);
  const targets = artifact?.targets.filter((target) => entityRefKey(target.subject_ref) === subjectId) ?? [];
  const subject = artifact?.graph.entities.find((entity) => entityRefKey(entity) === subjectId);
  const subjects = artifact ? targetGraphSubjects(artifact) : [];
  const candidatePhase = artifact?.phase === "candidate_graph";
  const reviewPhase = artifact?.phase === "evidence_review";

  return <div className="space-y-4">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div className="space-y-1"><h3 className="font-medium">{run?.input.filename || "正在读取运行…"}</h3><div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">{run && <Badge variant="secondary">{DOCUMENT_ANALYSIS_STATUS_LABELS[run.status]}</Badge>}{run?.stage === "preparing_metadata" && <span>正在准备文档结构和摘要</span>}<span>运行 {runId}</span></div></div>
      <div className="flex gap-2">
        {run?.available_actions.includes("pause") && <Button variant="outline" size="sm" disabled={!!busy} onClick={() => void control("pause")}><Pause className="size-4" />{busy === "pause" ? "请求暂停中…" : "暂停"}</Button>}
        {run?.available_actions.includes("resume") && <Button size="sm" disabled={!!busy} onClick={() => void control("resume")}><Play className="size-4" />{busy === "resume" ? "请求继续中…" : "继续"}</Button>}
        <Button size="sm" variant="outline" onClick={() => setNonce((value) => value + 1)}><RefreshCw className="size-4" />刷新结果</Button>
      </div>
    </div>
    {error && <p role="alert" className="break-words text-sm text-destructive">{error}</p>}
    {run?.error && <p role="alert" className="text-sm text-destructive">{run.error.safe_detail}</p>}
    {!artifact && !error && <p role="status" className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="size-4 animate-spin" />正在加载本体识别目标…</p>}
    {artifact && <>
      <SavedDiscoveryPanel artifact={artifact} onEvidence={setSelectionRef} />
      {candidatePhase ? <CandidateGraphSummary artifact={artifact} /> : reviewPhase ? <EvidenceReviewSummary artifact={artifact} /> : <>
        <div className="grid gap-3 md:grid-cols-2">
        {(["relationships", "properties"] as const).map((kind) => {
          const counts = artifact.summary[kind];
          return <Card key={kind}><CardContent className="space-y-2 p-4"><div className="flex justify-between gap-4 text-sm"><span>{kind === "relationships" ? "关系识别完整度" : "属性识别完整度"}</span><span className="font-semibold">{counts.percent == null ? "待确定" : `${counts.percent.toFixed(1)}%`}</span></div>
            <div className="h-1.5 overflow-hidden rounded-full bg-muted"><div className="h-full bg-primary transition-all" style={{ width: `${counts.percent ?? 0}%` }} /></div>
            <p className="text-xs text-muted-foreground">完成 {counts.completed} / {counts.total} 项 · 已实证 {counts.supported} 项</p>
          </CardContent></Card>;
        })}
        </div>
        <p className="text-xs leading-relaxed text-muted-foreground">待展开 {artifact.summary.pending_expansion_count} 项。完成度以约定的本体识别目标为分母；多值对象范围尚未核对时保持未完成。运行结束不表示全文信息已穷尽。</p>
      </>}
      {artifact.summary.notes.length > 0 && <ul className="list-disc space-y-1 pl-5 text-xs text-muted-foreground">{artifact.summary.notes.map((note, index) => <li key={index}>{note}</li>)}</ul>}
      {reviewPhase && <div className="flex items-center gap-2 text-sm"><Switch id="show-not-accepted" checked={showNotAccepted} onCheckedChange={setShowNotAccepted} /><label htmlFor="show-not-accepted">显示未采信候选及原因（{targetReviewCounts(artifact).not_accepted} 条）</label></div>}
      <TargetGraphCanvas artifact={artifact} selectedSubject={subjectId ?? ""} onSelectSubject={selectSubject} onSelectTarget={selectTarget} showNotAccepted={showNotAccepted} />
      <div className="grid items-start gap-4 xl:grid-cols-[minmax(340px,0.9fr)_minmax(0,1.1fr)]">
        <Card><CardHeader className="pb-3"><CardTitle className="text-base">{subject ? graphEntityLabel(subject) : subjects.find((item) => item.id === subjectId)?.label || artifact.root.label} · {candidatePhase ? "关系候选与目标" : "识别目标"}</CardTitle>
          <label className="space-y-1.5 text-xs text-muted-foreground"><span>查看实体（包含尚未关联到文档根节点的实体）</span>
            <select aria-label="查看实体识别目标" value={subjectId ?? ""} onChange={(event) => selectSubject(event.target.value)} className="h-9 w-full rounded-md border border-input bg-background px-2 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring">
              {subjects.map((item) => <option key={item.id} value={item.id}>{item.label} · {candidatePhase ? `关系目标 ${item.relationships}` : `属性 ${item.properties} / 关系 ${item.relationships}`}</option>)}
            </select>
          </label>
          {subject && <EntitySourceLinks entity={subject} label="实体" onEvidence={setSelectionRef} />}
          {focusTarget && <Button variant="link" size="sm" className="h-auto justify-start p-0 text-xs" onClick={() => selectSubject(entityRefKey(focusTarget.subject_ref))}>查看该实体全部 {targets.length} 项目标</Button>}</CardHeader>
          <CardContent className="max-h-[690px] space-y-3 overflow-auto">
            {targets.length === 0 && <p className="text-sm text-muted-foreground">该实体尚无可展开的本体目标。</p>}
            {(focusTarget ? [focusTarget] : targets).map((target) => <TargetDetails key={target.target_id} target={target} artifact={artifact} focused={target.target_id === selectedTarget} onEvidence={(ref) => setSelectionRef(ref)} showNotAccepted={showNotAccepted} />)}
          </CardContent>
        </Card>
        <Card ref={previewRef}><CardHeader className="pb-3"><CardTitle className="text-base">{candidatePhase ? "候选原文" : "原文证据"}</CardTitle><p className="text-xs text-muted-foreground">{candidatePhase ? "点击实体或关系候选的原文引用进行定位。关系尚未核验。" : "点击关系或属性中的原文引用定位证据。"}</p></CardHeader><CardContent>
          {sourceError && <p role="alert" className="mb-3 text-sm text-destructive">{sourceError}</p>}
          {selectionRef && source && anchor?.selectionRef !== selectionRef && !sourceError && <p role="status" className="mb-3 text-xs text-muted-foreground">正在定位证据…</p>}
          {source ? <div className="max-h-[650px] overflow-auto"><WordViewer content={source.content} activeAnchor={anchor?.selectionRef === selectionRef ? anchor.value : null} fitTables /></div> : <p className="text-sm text-muted-foreground">原文准备完成后可在这里查看。</p>}
        </CardContent></Card>
      </div>
    </>}
  </div>;
}

export function SavedDiscoveryPanel({ artifact, onEvidence }: {
  artifact: DocumentAnalysisTargetGraph; onEvidence: (ref: string) => void;
}) {
  const discovery = artifact.discovery;
  const kinds = { entity: "实体", property: "属性", relation: "关系", observation: "观察", failure: "调用失败" };
  const states = { pending: "待对齐 / 待核验", rejected: "未采信", accepted: "已采信", observation: "待对齐观察", failed: "未完成" };
  const accepted = targetReviewCounts(artifact).accepted;
  const entities = artifact.graph.entities.filter((item) => entityRefKey(item) !== entityRefKey(artifact.root)).length;
  return <Card>
    <CardHeader className="pb-3"><CardTitle className="text-base">发现进展与已保存候选</CardTitle></CardHeader>
    <CardContent className="space-y-3">
      <div className="grid gap-3 text-sm md:grid-cols-3">
        <div><span className="text-muted-foreground">调用有进展</span><p>已返回 {discovery.completed_calls} 次 · 在途 {discovery.inflight_calls} 次</p></div>
        <div><span className="text-muted-foreground">候选有产出</span><p>已保存 {discovery.candidate_count} 项 · 观察 {discovery.items.filter((item) => item.kind === "observation").length} 项</p></div>
        <div><span className="text-muted-foreground">事实已采信</span><p>实体已登记 {entities} 个 · 关系及属性 {accepted} 条</p></div>
      </div>
      <p className="text-xs text-muted-foreground">调用返回及候选保存不代表事实成立。以下内容尚未全部采信，不计为全文完整度。</p>
      <details open={discovery.items.length > 0}>
        <summary className="cursor-pointer text-sm">查看候选、观察及失败原因（{discovery.items.length} 项）</summary>
        <div className="mt-3 max-h-96 space-y-2 overflow-auto">
          {discovery.items.length === 0 && <p className="text-xs text-muted-foreground">尚无已保存候选。</p>}
          {discovery.items.map((item) => <details key={item.id} className="rounded-md border p-3 text-sm">
            <summary className="cursor-pointer space-y-1 break-words"><div className="flex flex-wrap items-center gap-2"><Badge variant="outline">{kinds[item.kind]}</Badge><Badge variant="secondary">{states[item.state]}</Badge><span>{item.label}</span></div>
              {item.reasons[0] && <p className="text-xs text-muted-foreground">{item.reasons[0]}</p>}
            </summary>
            <div className="mt-2 space-y-2 break-words text-xs">
              {(item.class_iri || item.predicate_iri) && <p>{item.class_iri ? "类型" : "谓词"}：{item.class_iri || item.predicate_iri}</p>}
              {item.subject_id && <p>主体候选：{item.subject_id}{item.object_ids.length ? ` → 对象候选：${item.object_ids.join("、")}` : ""}</p>}
              {item.reasons.map((reason, index) => <p key={index} className="text-muted-foreground">{reason}</p>)}
              {item.sources.map((source, index) => <div key={index} className="rounded bg-muted/50 p-2"><p className="whitespace-pre-wrap">{source.text}</p>
                {source.selection_ref ? <Button size="sm" variant="link" className="h-auto p-0 text-xs" onClick={() => onEvidence(source.selection_ref!)}>定位候选原文 {index + 1}</Button> : <span className="text-muted-foreground">引用未通过定位，不能用作证据</span>}
              </div>)}
            </div>
          </details>)}
        </div>
      </details>
    </CardContent>
  </Card>;
}

export function CandidateGraphSummary({ artifact }: { artifact: DocumentAnalysisTargetGraph }) {
  const counts = candidateGraphCounts(artifact);
  return <div className="space-y-3">
    <div className="flex flex-wrap items-center gap-2 text-sm"><Badge variant="secondary">候选图谱 · 关系未核验</Badge><span className="text-muted-foreground">实体已登记，关系候选均以虚线显示</span></div>
    <div className="grid gap-3 md:grid-cols-2">
      <Card><CardContent className="p-4"><div className="flex items-center justify-between gap-4 text-sm"><span>候选实体</span><span className="text-xl font-semibold">{counts.entities}</span></div><p className="mt-2 text-xs text-muted-foreground">已登记的文档实体，不包含文档根节点。</p></CardContent></Card>
      <Card><CardContent className="p-4"><div className="flex items-center justify-between gap-4 text-sm"><span>原文关系候选</span><span className="text-xl font-semibold">{counts.relationships}</span></div><p className="mt-2 text-xs text-muted-foreground">多对象关系组计为一条候选；本体目标占位不计入。</p></CardContent></Card>
    </div>
  </div>;
}

export function EvidenceReviewSummary({ artifact }: { artifact: DocumentAnalysisTargetGraph }) {
  const assertions = targetReviewCounts(artifact);
  return <div className="space-y-3">
    <div className="flex flex-wrap items-center gap-2 text-sm"><Badge variant="secondary">原文证据采信</Badge><span className="text-muted-foreground">已采信 {assertions.accepted} 条 · 待定 {assertions.pending} 条 · 未采信 {assertions.not_accepted} 条</span></div>
    <div className="grid gap-3 md:grid-cols-2">{(["relationships", "properties"] as const).map((kind) => {
      const counts = artifact.summary[kind];
      return <Card key={kind}><CardContent className="p-4"><div className="flex items-center justify-between gap-4 text-sm"><span>{kind === "relationships" ? "关系采信目标" : "属性采信目标"}</span><span className="text-xl font-semibold">{counts.supported} / {counts.total}</span></div><p className="mt-2 text-xs text-muted-foreground">已有正向采信证据的目标 / 当前本体目标总数；候选及否定陈述不计为正向成立。</p></CardContent></Card>;
    })}</div>
  </div>;
}

function EntitySourceLinks({ entity, label, onEvidence }: {
  entity: DocumentGraphEntity; label: string; onEvidence: (ref: string) => void;
}) {
  const refs = [...new Set(entity.source_selection_refs)];
  if (!refs.length) return null;
  return <div className="flex flex-wrap items-center gap-1 text-xs"><span className="text-muted-foreground">{label}：{graphEntityLabel(entity)}</span>{refs.map((ref, index) => <Button key={ref} variant="link" size="sm" className="h-6 px-1 text-xs" onClick={() => onEvidence(ref)}>{label}原文 {index + 1}</Button>)}</div>;
}

export function TargetDetails({ target, artifact, focused, onEvidence, showNotAccepted = false }: {
  target: DocumentAnalysisTarget; artifact: DocumentAnalysisTargetGraph;
  focused: boolean; onEvidence: (ref: string) => void; showNotAccepted?: boolean;
}) {
  const allAssertions = targetAssertions(target, artifact);
  const assertions = visibleTargetAssertions(target, artifact, showNotAccepted);
  const candidatePhase = artifact.phase === "candidate_graph";
  const reviewPhase = artifact.phase === "evidence_review";
  const acceptedCount = allAssertions.filter((assertion) => assertionReviewState(target, assertion) === "accepted").length;
  const notAcceptedCount = allAssertions.filter((assertion) => assertionReviewState(target, assertion) === "not_accepted").length;
  const reviewLabel = acceptedCount ? "有采信证据" : allAssertions.length && notAcceptedCount === allAssertions.length ? "未采信" : "待定";
  const entities = new Map(artifact.graph.entities.map((entity) => [entityRefKey(entity), entity]));
  return <div className={cn("rounded-lg border p-3", focused && "border-primary bg-primary/5")}>
    <div className="flex items-start justify-between gap-2"><div><span className="mr-2 text-xs text-muted-foreground">{target.kind === "relationship" ? "关系" : "属性"}</span><span className="text-sm font-medium">{graphPredicateLabel(target)}</span></div><Badge variant={!candidatePhase && target.completed ? "default" : "secondary"}>{candidatePhase ? assertions.length ? "原文候选 · 未核验" : "本体关系目标" : reviewPhase ? reviewLabel : TARGET_STATE_LABELS[target.state]}</Badge></div>
    {candidatePhase ? <p className="mt-1 text-xs text-muted-foreground">原文候选 {assertions.length} 条 · 关系未核验</p> : reviewPhase ? <p className="mt-1 text-xs text-muted-foreground">已采信 {acceptedCount} 条 · 待定 {allAssertions.length - acceptedCount - notAcceptedCount} 条 · 未采信 {notAcceptedCount} 条</p> : <p className="mt-1 text-xs text-muted-foreground">已实证 {target.supported_count} 条{target.negated_count > 0 ? ` · 原文否定 ${target.negated_count} 条` : ""} · {target.completed ? "识别完成" : "识别未完成"}{target.multiplicity !== "single" ? " · 需核对对象范围" : ""}</p>}
    <p className="mt-1 text-xs leading-relaxed text-muted-foreground">{reviewPhase && allAssertions.length ? "本体目标保持保留，各条候选的采信状态与原因分别展示。" : target.reason}</p>
    <div className="mt-2 space-y-2">
      {assertions.map((assertion) => {
        const value = "raw_value" in assertion ? assertion.raw_value
          : "object_refs" in assertion ? assertion.object_refs.map((ref) => graphEntityLabel(entities.get(entityRefKey(ref)))).join("、")
          : graphEntityLabel(entities.get(entityRefKey(assertion.object_ref)));
        const refs = assertionSourceRefs(assertion);
        const qualifier = ["object_refs" in assertion ? GROUP_SELECTION_LABELS[assertion.selection] : null, targetAssertionQualifier(target, assertion, artifact.phase)].filter(Boolean).join(" · ");
        const state = assertionReviewState(target, assertion);
        return <div key={`${assertion.candidate_id}:${assertion.revision}`} className="rounded-md bg-muted/50 p-2 text-xs">
          <div className="flex flex-wrap items-center gap-2"><span className="break-words font-medium">{value}</span><span className={cn("text-muted-foreground", reviewPhase && state === "not_accepted" && "text-destructive")}>{candidatePhase ? "原文候选 · 未核验" : reviewPhase ? REVIEW_STATE_LABELS[state] : isSupportedTargetAssertion(target, assertion) ? "原文已实证" : "待核验"}{qualifier ? ` · ${qualifier}` : ""}</span></div>
          {"raw_value" in assertion && <PropertyValues property={assertion} />}
          {reviewPhase && <p className="mt-1 text-muted-foreground">判定原因：{assertion.reason || assertion.reason_code || (state === "accepted" ? "原文证据已采信。" : state === "not_accepted" ? "该候选未采信，尚无具体原因。" : "尚无有效采信证明。")}{assertion.reason && assertion.reason_code ? `（${assertion.reason_code}）` : ""}</p>}
          {reviewPhase && assertion.validation_diagnostics?.length > 0 && <div className="mt-2 space-y-1 border-t pt-2"><p className="font-medium">独立检查结果</p>{assertion.validation_diagnostics.map((diagnostic, index) => <div key={`${diagnostic.check}:${index}`} className={cn("text-muted-foreground", diagnostic.status === "failed" && "text-destructive")}>
            <span>{({ metric: "规范化检查", shacl: "SHACL 检查", relation_graph: "关系结构检查", schema: "Schema 检查" } as const)[diagnostic.check]} · {({ passed: "通过", failed: "告警", incomplete: "未完成", not_checked: "未检查" } as const)[diagnostic.status]}</span>
            {diagnostic.message && <p>{diagnostic.message}</p>}{diagnostic.reason_codes.length > 0 && <p>{diagnostic.reason_codes.join("、")}</p>}
          </div>)}<p className="text-muted-foreground">独立告警不改变原文证据采信状态。</p></div>}
          {assertion.conditions.length > 0 && <p className="mt-1 text-muted-foreground">条件：{graphQualifierText(assertion.conditions)}</p>}
          {Object.keys(assertion.applicability ?? {}).length > 0 && <p className="mt-1 text-muted-foreground">适用：{graphQualifierText(assertion.applicability)}</p>}
          {refs.length ? <div className="mt-1 flex flex-wrap gap-1">{refs.map((ref, index) => <Button key={ref} variant="link" size="sm" className="h-6 px-1 text-xs" onClick={() => onEvidence(ref)}>原文 {index + 1}</Button>)}</div> : <p className="mt-1 text-muted-foreground">暂无可定位原文</p>}
          {assertionEndpointEntities(assertion, artifact).map(({ role, entity }) => <EntitySourceLinks key={`${role}:${entityRefKey(entity)}`} entity={entity} label={role} onEvidence={onEvidence} />)}
        </div>;
      })}
    </div>
    {allAssertions.length > assertions.length && <p className="mt-2 text-xs text-muted-foreground">已隐藏 {allAssertions.length - assertions.length} 条未采信候选；开启“显示未采信候选及原因”可查看。</p>}
    {!candidatePhase && !reviewPhase && <p className="mt-2 text-[11px] text-muted-foreground">已检查 {target.coverage.records_examined} / {target.coverage.records_planned} 条原文记录 · {target.coverage.scope_checked ? "范围已核对" : "范围待核对"}</p>}
  </div>;
}

export function PropertyValues({ property }: { property: DocumentGraphProperty }) {
  const normalized = property.normalization_available && property.normalized_value != null
    ? formatDocumentGraphQuantity({ normalized_value: property.normalized_value, unit: null })
      ?? JSON.stringify(property.normalized_value) : null;
  return <dl className="mt-2 grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1 text-xs">
    <dt className="text-muted-foreground">原文值</dt><dd className="break-words">{property.raw_value}</dd>
    <dt className="text-muted-foreground">原文单位</dt><dd>{property.raw_unit ?? "未提供"}</dd>
    <dt className="text-muted-foreground">规范化值</dt><dd className="break-words">{normalized ?? "未生成"}</dd>
    <dt className="text-muted-foreground">规范化单位</dt><dd>{property.normalization_available ? property.unit ?? "无单位" : "未生成"}</dd>
  </dl>;
}
