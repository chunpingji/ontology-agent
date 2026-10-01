"use client";

import { useEffect, useRef, useState } from "react";
import { FileText, Loader2, Pause, Play } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  answerDocumentInterpretationTask, controlDocumentAnalysisRun, DOCUMENT_HARNESS_PROTOCOL, getDocumentAnalysisRun,
  getDocumentHarnessGraph, getDocumentHarnessSource, mergeDocumentAnalysisControlReceipt,
  type DocumentAnalysisRun, type DocumentHarnessGraph, type DocumentHarnessSource, type DocumentHarnessSourceRef,
  type DocumentInterpretationMeaning, type DocumentInterpretationScope, type DocumentInterpretationTask,
} from "@/lib/api";
import { DOCUMENT_ANALYSIS_STATUS_LABELS } from "@/lib/document-analysis";
import { harnessCompletionMessage, harnessCosts, HARNESS_STAGES, HARNESS_WORK_STATES } from "@/lib/source-harness";
import { HarnessCandidates } from "./source-harness-workspace";
import { HarnessObservations } from "./source-harness-observations";
import { HarnessSourcePreview } from "./source-harness-shared";

export { HarnessCandidates, HarnessRelationCanvas, HarnessPropertyDetail } from "./source-harness-workspace";
export { HarnessObservations, HarnessObservationDetail, HarnessCandidateWorkList } from "./source-harness-observations";
export { HarnessSourcePreview } from "./source-harness-shared";
export { observationResults, filterHarnessObservations } from "@/lib/source-harness";

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "请求失败，请重试。";
}

export function SourceHarnessPanel({ initialRun, refreshRevision = 0, taskDrawerOpen, onTaskDrawerOpenChange, onTaskCountChange }: {
  initialRun: DocumentAnalysisRun;
  refreshRevision?: number;
  taskDrawerOpen: boolean;
  onTaskDrawerOpenChange: (open: boolean) => void;
  onTaskCountChange: (count: number | null) => void;
}) {
  const runId = initialRun.recognition_run_id;
  const [activeTab, setActiveTab] = useState("entities");
  const [run, setRun] = useState(initialRun);
  const [graph, setGraph] = useState<DocumentHarnessGraph | null>(null);
  const [refreshedAt, setRefreshedAt] = useState<Date | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);
  const [busy, setBusy] = useState<"pause" | "resume" | null>(null);
  const [selectedEntity, setSelectedEntity] = useState<string | null>(null);
  const [locateRevision, setLocateRevision] = useState(0);
  const [observationSubject, setObservationSubject] = useState("all");
  const [sourceRef, setSourceRef] = useState<DocumentHarnessSourceRef | null>(null);
  const [source, setSource] = useState<DocumentHarnessSource | null>(null);
  const [sourceError, setSourceError] = useState<string | null>(null);
  const [sourceRetry, setSourceRetry] = useState(0);
  const controlController = useRef<AbortController | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | null = null;
    const refresh = async () => {
      const [runResult, graphResult] = await Promise.allSettled([
        getDocumentAnalysisRun(runId, controller.signal),
        getDocumentHarnessGraph(runId, controller.signal),
      ]);
      if (controller.signal.aborted) return;
      const failures: string[] = [];
      if (runResult.status === "fulfilled") {
        if (runResult.value.recognition_run_id !== runId || runResult.value.extraction_protocol !== DOCUMENT_HARNESS_PROTOCOL) {
          failures.push("运行与当前文档分析流程不一致，已拒绝显示。");
        } else setRun(runResult.value);
      } else failures.push(errorMessage(runResult.reason));
      if (graphResult.status === "fulfilled") {
        if (graphResult.value.protocol !== DOCUMENT_HARNESS_PROTOCOL || graphResult.value.run_id !== runId) {
          failures.push("图谱与当前运行不一致，已拒绝显示。");
        } else { setGraph(graphResult.value); setRefreshedAt(new Date()); }
      } else failures.push(errorMessage(graphResult.reason));
      setError(failures.join("；") || null);
      if (runResult.status === "rejected" || ["queued", "running", "pausing"].includes(runResult.value.status)) {
        timer = setTimeout(() => void refresh(), 2500);
      }
    };
    void refresh();
    return () => { controller.abort(); if (timer) clearTimeout(timer); };
  }, [runId, nonce, refreshRevision]);

  useEffect(() => {
    if (!sourceRef) return;
    const controller = new AbortController();
    void getDocumentHarnessSource(runId, sourceRef.source_id, controller.signal).then((value) => {
      if (controller.signal.aborted) return;
      if (value.source_id !== sourceRef.source_id) throw new Error("来源与所选引文不一致。");
      const excerpt = Array.from(value.text).slice(sourceRef.start, sourceRef.end).join("");
      if (sourceRef.start < 0 || sourceRef.end <= sourceRef.start || sourceRef.end > Array.from(value.text).length || excerpt !== sourceRef.text) {
        throw new Error("所选引文与原文位置不一致，已拒绝标注。");
      }
      setSource(value);
      setSourceError(null);
    }).catch((failure: unknown) => {
      if (!controller.signal.aborted) setSourceError(errorMessage(failure));
    });
    return () => controller.abort();
  }, [runId, sourceRef, sourceRetry]);

  useEffect(() => () => controlController.current?.abort(), []);
  const control = async (action: "pause" | "resume") => {
    if (busy) return;
    const controller = new AbortController();
    controlController.current = controller;
    setBusy(action);
    setError(null);
    try {
      const receipt = await controlDocumentAnalysisRun(runId, action, run.run_revision,
        crypto.randomUUID(), action === "pause" ? "图谱分析页面暂停" : "图谱分析页面继续", controller.signal);
      if (controller.signal.aborted) return;
      setRun((current) => mergeDocumentAnalysisControlReceipt(current, receipt) ?? current);
      setNonce((value) => value + 1);
    } catch (failure) { if (!controller.signal.aborted) setError(errorMessage(failure)); }
    finally { if (!controller.signal.aborted) setBusy(null); }
  };
  const selectSource = (ref: DocumentHarnessSourceRef) => {
    setSource(null); setSourceError(null); setSourceRef({ ...ref });
  };
  const subject = graph?.entities.find((entity) => entity.id === selectedEntity)
    ?? graph?.entities.find((entity) => entity.role === "document_root");
  const selectEntity = (id: string) => {
    setSelectedEntity(id);
    setLocateRevision((value) => value + 1);
    setActiveTab("entities");
  };
  const tasks = graph?.interpretation_tasks ?? [];
  const pendingTaskCount = tasks.filter((task) => !task.answer).length;

  useEffect(() => {
    onTaskCountChange(graph ? pendingTaskCount : null);
  }, [graph, pendingTaskCount, onTaskCountChange]);

  return <div className="min-w-0 space-y-6" data-testid="source-harness-v2">
    <section aria-label="当前分析文档" className="flex flex-wrap items-center justify-between gap-4 rounded-lg border bg-muted/30 p-4">
      <div className="flex min-w-0 items-center gap-3"><div className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary"><FileText className="size-5" /></div><div className="min-w-0 space-y-1.5">
        <h2 className="break-words text-sm font-semibold">{run.input.filename}</h2>
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground"><span>根类型：{run.input.root_class_label || "未记录"}</span><span aria-hidden="true">·</span><span>{!graph ? "正在读取阅读范围…" : graph.progress.reading.complete ? "本轮原文阅读范围已处理" : "本轮原文阅读范围尚未处理完成"}</span></div>
      </div></div>
      <div className="flex flex-wrap items-center gap-3">
        <Badge variant={run.status === "finished" ? "success" : "secondary"} className="px-2 py-0.5 text-[11px]">{run.status === "finished" ? "本轮已结束" : DOCUMENT_ANALYSIS_STATUS_LABELS[run.status]}</Badge>
        {refreshedAt && <time dateTime={refreshedAt.toISOString()} title="本页面最近成功读取图谱结果的时间" className="text-xs text-muted-foreground">更新于 {refreshedAt.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false })}</time>}
        {run.available_actions.includes("pause") && <Button variant="outline" size="sm" disabled={!!busy} onClick={() => void control("pause")}><Pause className="size-4" />{busy === "pause" ? "请求暂停中…" : "暂停"}</Button>}
        {run.available_actions.includes("resume") && <Button size="sm" disabled={!!busy} onClick={() => void control("resume")}><Play className="size-4" />{busy === "resume" ? "请求继续中…" : "继续"}</Button>}
      </div>
    </section>
    {error && <p role="alert" className="break-words text-sm text-destructive">{error}</p>}
    {run.error && <p role="alert" className="break-words text-sm text-destructive">{run.error.safe_detail}</p>}
    {!graph && !error && <p role="status" className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="size-4 animate-spin" />正在读取已保存的分析结果…</p>}
    {graph && <>
      <HarnessProgress graph={graph} />
      <Tabs value={activeTab} onValueChange={setActiveTab}>
        <TabsList aria-label="图谱分析分区" className="h-auto max-w-full flex-wrap justify-start">
          <TabsTrigger value="entities">实体图谱与属性</TabsTrigger>
          <TabsTrigger value="costs">各阶段模型成本</TabsTrigger>
          <TabsTrigger value="observations">原文观察与对齐结果</TabsTrigger>
        </TabsList>
        <TabsContent value="entities" forceMount hidden={activeTab !== "entities"} className="mt-6">
          <HarnessCandidates key={locateRevision} graph={graph} selectedEntity={subject?.id ?? null} onSelectEntity={setSelectedEntity} onSource={selectSource} onObservations={(id) => { setObservationSubject(`entity:${id}`); setActiveTab("observations"); }} />
        </TabsContent>
        <TabsContent value="costs" forceMount hidden={activeTab !== "costs"} className="mt-6">
          <HarnessStageCosts graph={graph} />
        </TabsContent>
        <TabsContent value="observations" forceMount hidden={activeTab !== "observations"} className="mt-6">
          <HarnessObservations graph={graph} selectedEntity={subject?.id ?? null} onSelectEntity={selectEntity} onSource={selectSource} subjectFilter={observationSubject} onSubjectFilter={setObservationSubject} />
        </TabsContent>
      </Tabs>
    </>}
    <Sheet open={taskDrawerOpen} onOpenChange={onTaskDrawerOpenChange}>
      <SheetContent className="gap-0 p-0 sm:max-w-2xl xl:max-w-3xl" onCloseAutoFocus={(event) => { if (sourceRef) event.preventDefault(); }}>
        <SheetHeader className="shrink-0 border-b px-5 py-4 pr-12">
          <SheetTitle>人工确认任务</SheetTitle>
          <SheetDescription>当前运行待处理 {pendingTaskCount} 项，共 {tasks.length} 项。核对原文后确认含义和作用范围。</SheetDescription>
        </SheetHeader>
        <div className="min-h-0 flex-1 overflow-y-auto p-4 sm:p-5">
          <HarnessInterpretationTasks tasks={tasks} onSource={(ref) => { onTaskDrawerOpenChange(false); selectSource(ref); }} onAnswer={async (task, meaning, scope) => {
            await answerDocumentInterpretationTask(runId, task.id, {
              meaning, scope, expected_revision: task.scope_revisions[scope] ?? 0,
            });
            setGraph(await getDocumentHarnessGraph(runId));
          }} />
        </div>
      </SheetContent>
    </Sheet>
    <Dialog open={sourceRef != null} onOpenChange={(open) => { if (!open) setSourceRef(null); }}>
      <DialogContent className="max-h-[90dvh] w-[calc(100%_-_2rem)] max-w-4xl overflow-y-auto">
        <DialogHeader><DialogTitle>原文定位</DialogTitle><DialogDescription>按保存的精确引用定位原文，取值引用与完整声明分别核对。</DialogDescription></DialogHeader>
        {sourceError && <div className="space-y-3"><p role="alert" className="text-sm text-destructive">{sourceError}</p><Button variant="outline" onClick={() => { setSourceError(null); setSourceRetry((value) => value + 1); }}>重新读取原文</Button></div>}
        {!source && !sourceError && <p role="status" className="text-sm text-muted-foreground">正在读取原文…</p>}
        {source && sourceRef && <HarnessSourcePreview source={source} reference={sourceRef} />}
      </DialogContent>
    </Dialog>
  </div>;
}

function HarnessInterpretationTasks({ tasks, onSource, onAnswer }: {
  tasks: DocumentInterpretationTask[];
  onSource: (reference: DocumentHarnessSourceRef) => void;
  onAnswer: (task: DocumentInterpretationTask, meaning: DocumentInterpretationMeaning,
    scope: DocumentInterpretationScope) => Promise<void>;
}) {
  return <section aria-label="原文歧义人工确认" className="space-y-4">
    {!tasks.length && <p className="rounded-md border border-dashed p-6 text-sm text-muted-foreground">当前运行暂无待人工确认的原文歧义。</p>}
    {[...tasks].sort((left, right) => Number(Boolean(left.answer)) - Number(Boolean(right.answer))).map((task) => <HarnessInterpretationTaskForm
      key={`${task.id}:${task.answer?.scope ?? "pending"}:${task.answer?.revision ?? 0}`}
      task={task} onSource={onSource} onAnswer={onAnswer} />)}
  </section>;
}

function HarnessInterpretationTaskForm({ task, onSource, onAnswer }: {
  task: DocumentInterpretationTask;
  onSource: (reference: DocumentHarnessSourceRef) => void;
  onAnswer: (task: DocumentInterpretationTask, meaning: DocumentInterpretationMeaning,
    scope: DocumentInterpretationScope) => Promise<void>;
}) {
  const [meaning, setMeaning] = useState<DocumentInterpretationMeaning | "">(task.answer?.meaning ?? "");
  const [scope, setScope] = useState<DocumentInterpretationScope>(task.answer?.scope ?? "occurrence");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const submit = async () => {
    if (!meaning || saving) return;
    setSaving(true); setError(null);
    try { await onAnswer(task, meaning, scope); }
    catch (failure) { setError(errorMessage(failure)); }
    finally { setSaving(false); }
  };
  return <div className="space-y-3 rounded-md border bg-background p-4">
    <p className="text-sm font-medium">{task.subject_label} → {task.relation_label} → {task.object_labels.join("、")}</p>
    <div className="space-y-1 text-xs">{task.evidence.map((ref, index) => <button type="button" key={`${ref.source_id}:${ref.start}:${index}`} onClick={() => onSource(ref)} className="block text-left text-primary hover:underline">原文：{ref.text}</button>)}</div>
    {task.questions.map((question, index) => <fieldset key={index} className="space-y-2">
      <legend className="text-sm font-medium">{index + 1}. {question.prompt}</legend>
      <div className="flex flex-wrap gap-x-5 gap-y-2">{question.options.map((option) => {
        const disabled = index === 1 && meaning === "unresolved" && option.value !== "occurrence";
        return <label key={option.value} className="flex items-center gap-1.5 text-sm"><input
          type="radio" name={`${task.id}:${index}`} value={option.value} disabled={disabled}
          checked={index === 0 ? meaning === option.value : scope === option.value}
          onChange={() => {
            if (index === 0) {
              setMeaning(option.value as DocumentInterpretationMeaning);
              if (option.value === "unresolved") setScope("occurrence");
            } else setScope(option.value as DocumentInterpretationScope);
          }} />{option.label}</label>;
      })}</div>
    </fieldset>)}
    {task.answer && <p className="text-xs text-muted-foreground">已保存人工解释，作用范围：{task.questions[1].options.find((option) => option.value === task.answer?.scope)?.label ?? task.answer.scope}。模型关系状态仍按原判定显示。</p>}
    {error && <p role="alert" className="text-xs text-destructive">{error}</p>}
    <Button size="sm" disabled={!meaning || saving} onClick={() => void submit()}>{saving ? "保存中…" : "保存人工解释"}</Button>
  </div>;
}

export function HarnessProgress({ graph }: { graph: DocumentHarnessGraph }) {
  const progress = graph.progress;
  const windows = progress.reading_windows;
  const phaseLabels = { reading: "局部阅读", entities: "实体核验", coreference: "共指核验", graph: "图谱分析", done: "本轮完成" };
  return <section className="space-y-3" aria-label="阅读与任务进度">
    <p className="text-sm font-medium">原文范围已处理 {progress.reading.processed_characters.toLocaleString()} / {progress.reading.total_characters.toLocaleString()} 字符</p>
    <p className="text-sm">完整覆盖 {progress.reading.complete_characters?.toLocaleString() ?? "待更新"} / {progress.reading.total_characters.toLocaleString()} 字符</p>
    <p className="text-xs text-muted-foreground">已处理包含已保存的局部结果；完整覆盖仅统计确认读完的范围，两者均按原文去重。阅读批次：已保存 {windows.saved} / {windows.total}，处理中 {windows.active}，覆盖完整 {windows.complete}，覆盖不完整 {windows.incomplete}。</p>
    <p className="text-xs text-muted-foreground">当前阶段：{phaseLabels[progress.phase]}。阅读结果保存后，继续核验实体、共指和图谱事实。</p>
    <div className="flex flex-wrap gap-x-6 gap-y-2 text-xs">{Object.entries(HARNESS_WORK_STATES).map(([status, label]) => <span key={status} className="text-muted-foreground">{label}<strong className="ml-2 tabular-nums text-foreground">{progress.work_counts[status as keyof typeof progress.work_counts]}</strong></span>)}</div>
    <div className="flex flex-wrap gap-x-8 gap-y-2 text-xs">{[["已完成调用", progress.completed_calls], ["已保存候选", progress.candidate_count], ["已采信事实", progress.fact_count]].map(([label, count]) => <span key={label} className="text-muted-foreground">{label}<strong className="ml-2 text-base font-semibold tabular-nums text-foreground">{count}</strong></span>)}</div>
    <p className="text-xs text-muted-foreground">已采信事实的证明来源：规则证明 {progress.rule_verified_count} · 模型核对 {progress.llm_verified_count}</p>
    {progress.candidate_scope_limited && <p className="text-xs text-amber-700 dark:text-amber-400">本轮候选范围受限，仍有未处理的候选。</p>}
    <p className="text-xs text-muted-foreground">{harnessCompletionMessage(graph)}。阅读覆盖、任务处理和事实采信分别计数。</p>
  </section>;
}

export function HarnessStageCosts({ graph }: { graph: DocumentHarnessGraph }) {
  const costs = graph.progress.stage_costs;
  const total = harnessCosts(costs);
  const format = (value: number | null) => value == null ? "未知" : value.toLocaleString();
  const order = Object.keys(HARNESS_STAGES);
  const rows = [...costs].sort((a, b) => order.indexOf(a.stage) - order.indexOf(b.stage));
  return <section className="space-y-4" aria-label="各阶段模型成本">
    <div className="flex flex-wrap items-center justify-between gap-2"><h3 className="text-lg font-semibold">各阶段模型成本</h3><p className="text-xs text-muted-foreground">{total.calls} 次调用 · 已测累计耗时 {total.seconds.toFixed(1)} 秒 · Token 合计 {format(total.tokens)}</p></div>
    {total.unmeasuredAttempts > 0 && <p className="text-xs text-muted-foreground">{total.unmeasuredAttempts} 次尝试缺少耗时测量，未计入已测耗时。</p>}
    {!rows.length ? <p className="rounded-md border border-dashed p-6 text-sm text-muted-foreground">尚无已记录的模型调用成本。</p> : <div className="overflow-x-auto rounded-lg border"><table className="w-full min-w-[700px] text-left text-xs">
      <thead className="bg-muted/40 text-muted-foreground"><tr>{["阶段", "模型调用", "已测耗时(秒)", "输入 Token", "输出 Token", "已测耗时占比"].map((label) => <th key={label} scope="col" className="px-4 py-3 font-normal">{label}</th>)}</tr></thead>
      <tbody>{rows.map((cost, index) => { const share = total.seconds > 0 ? cost.seconds / total.seconds * 100 : null; return <tr key={cost.stage} className="border-t"><td className="px-4 py-3.5"><span className={`mr-2 inline-block size-1.5 rounded-full ${index < 3 ? "bg-primary" : "bg-teal-600"}`} />{HARNESS_STAGES[cost.stage] ?? cost.stage}</td><td className="px-4 py-3.5 tabular-nums">{cost.calls}</td><td className="px-4 py-3.5 tabular-nums">{cost.seconds.toFixed(1)}</td><td className="px-4 py-3.5 tabular-nums">{format(cost.input_tokens)}</td><td className="px-4 py-3.5 tabular-nums">{format(cost.output_tokens)}</td><td className="px-4 py-3.5"><div className="flex items-center gap-3"><span className="h-1.5 w-28 overflow-hidden rounded-full bg-muted"><span className={`block h-full rounded-full ${index < 3 ? "bg-primary" : "bg-teal-600"}`} style={{ width: `${share ?? 0}%` }} /></span><span className="tabular-nums">{share == null ? "—" : `${share.toFixed(0)}%`}</span></div></td></tr>; })}</tbody>
    </table></div>}
    <p className="text-xs leading-relaxed text-muted-foreground">按实际调用累计，含重试；已测耗时不等于整轮运行时间。缺失的 Token 显示“未知”，不计为 0；占比仅表示已测模型请求耗时。</p>
  </section>;
}
