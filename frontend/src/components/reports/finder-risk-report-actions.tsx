"use client";

import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, ChevronDown, Download, Eye, Loader2, RotateCcw, ShieldCheck, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Dialog, DialogContent, DialogDescription, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import type { TemplateFinderModel } from "@/components/analysis/use-template-finder";
import { fetchAPI, getAstTemplate, getReportRunArtifact, getTemplateFinder } from "@/lib/api";
import { isTemplateV2, reportPost, requestKey, type ReportRun } from "@/lib/reporting-v2";
import type { ReportInputSnapshot } from "@/lib/report-preview";
import { useIdentity } from "@/lib/use-identity";
import { cn } from "@/lib/utils";
import { reportDocumentError } from "./report-word-workspace";

const stages = ["读取与校验数据", "匹配报告模板", "风险识别与评估", "生成报告文档", "保存生成结果"];
const running = (run?: ReportRun) => !!run && ["pending", "running"].includes(run.execution_status);

// Keep task state outside SheetContent so closing the drawer leaves generation running.
// DocumentActionsMenu keys this component by document, template, source and identity.
export function FinderRiskReportActions({ templateId, sourceJobId, documentTitle, finder }: {
  templateId: string; sourceJobId: string; documentTitle: string; finder: TemplateFinderModel;
}) {
  const client = useQueryClient();
  const { identity: { username, role } } = useIdentity();
  const [open, setOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [runId, setRunId] = useState<string | null>(null);
  const [readRequested, setReadRequested] = useState(false);
  const pendingDrawer = useRef(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const pendingRequest = useRef<{ hash: string; key: string } | null>(null);
  const scope = ["finder-risk-report", username, role, templateId, sourceJobId];
  const runKey = (id: string | null) => [...scope, "run", id];
  const template = useQuery({
    queryKey: [...scope, "template"], enabled: open, retry: false,
    queryFn: async ({ signal }) => {
      const result = await getAstTemplate(templateId, signal);
      if (!isTemplateV2(result.schema_json)) throw new Error("当前模板不支持报告生成，请检查模板版本。");
      return { ...result, schema_json: result.schema_json };
    },
  });
  const run = useQuery({ queryKey: runKey(runId), enabled: !!runId,
    queryFn: ({ signal }) => fetchAPI<ReportRun>(`/api/report-runs/${runId}`, { signal }),
    refetchInterval: (query) => running(query.state.data) ? 1500 : false,
  });
  const current = run.data;
  const inputs = useQuery({ queryKey: [...scope, "inputs", current?.input_snapshot_id], enabled: !!current?.input_snapshot_id,
    queryFn: ({ signal }) => fetchAPI<ReportInputSnapshot>(`/api/report-runs/${runId}/inputs`, { signal }),
  });
  const generate = useMutation({
    mutationFn: async () => {
      if (!template.data || template.error) throw new Error("请先加载报告模板。");
      const schema = template.data.schema_json;
      if (schema.source_slots.length !== 1) throw new Error("当前模板需要多份来源，请在模板报告预览中配置来源。");
      const state = await getTemplateFinder(templateId, sourceJobId);
      if (state.status !== "completed" || !state.has_result || state.stale || !state.execution_id) {
        throw new Error("关系图谱尚未就绪或已变化，请先重新读取与校验。");
      }
      const payload = { mode: "report", template_id: templateId, draft_schema: schema,
        source_bindings: { [schema.source_slots[0].source_slot_id]: { job_id: sourceJobId, finder_execution_id: state.execution_id } },
        record_refs: {}, applicable_at: null,
      };
      const hash = JSON.stringify(payload);
      if (pendingRequest.current?.hash !== hash) pendingRequest.current = { hash, key: requestKey() };
      // Preserve the same key when a network error leaves the result uncertain.
      return reportPost<ReportRun>("report-previews", { ...payload, idempotency_key: pendingRequest.current.key });
    },
    onSuccess: (result) => {
      pendingRequest.current = null;
      client.setQueryData(runKey(result.run_id), result);
      setRunId(result.run_id);
      void client.invalidateQueries({ queryKey: ["report-preview-history", username, role] });
    },
  });
  const retry = useMutation({
    mutationFn: () => reportPost<ReportRun>(`report-runs/${runId}/attempts`, { expected_revision: current!.revision_no }),
    onSuccess: (result) => client.setQueryData(runKey(result.run_id), result),
  });
  const download = useMutation({ mutationFn: async () => {
    const artifact = current?.artifacts.find((item) => item.format === "docx");
    if (!artifact || !current) throw new Error("报告文件尚未生成。");
    const url = URL.createObjectURL(await getReportRunArtifact(current.run_id, artifact.artifact_id));
    const link = document.createElement("a");
    link.href = url; link.download = `风险评估表_${documentTitle.replace(/\.docx?$/i, "")}.docx`;
    link.click(); URL.revokeObjectURL(url);
  } });
  const busy = generate.isPending || retry.isPending || running(current);
  const artifact = current?.artifacts.find((item) => item.format === "docx");
  const completed = current?.execution_status === "completed" && !!artifact;
  const failed = generate.isError || retry.isError || current?.execution_status === "failed";
  const planning = !busy && !completed && !failed;
  // The service exposes resolving/rendering/completed, not a per-step percentage.
  // Advance only from server evidence; never simulate progress with a timer.
  const stage = completed ? 5 : current?.input_snapshot_id ? 2 : 0;
  const percent = completed ? 100 : stage === 2 ? 40 : busy ? 5 : 0;
  const readError = readRequested && (finder.error || (finder.status?.status === "failed" ? new Error("重新读取与校验失败，请重试。") : null));
  const ready = finder.status?.status === "completed" && finder.status.has_result && !finder.status.stale;
  const errors = [template.error, run.error, inputs.error, generate.error, retry.error, download.error, readError].filter(Boolean);
  function enterPlan() {
    setRunId(null); generate.reset(); retry.reset(); download.reset(); setReadRequested(false);
  }

  return <>
    <DropdownMenu open={menuOpen} onOpenChange={setMenuOpen} modal={false}>
      <DropdownMenuTrigger asChild><Button ref={trigger}>
        {busy ? <Loader2 className="animate-spin" /> : <Sparkles />}操作<ChevronDown />
      </Button></DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-64" onCloseAutoFocus={(event) => {
        if (!pendingDrawer.current) return;
        event.preventDefault(); pendingDrawer.current = false; setOpen(true);
      }}>
        <DropdownMenuItem className="items-start gap-3 py-2.5" onSelect={() => { pendingDrawer.current = true; setMenuOpen(false); }}>
          <span className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-md bg-amber-500/10 text-amber-600"><ShieldCheck className="size-4" /></span>
          <div><p className="text-sm font-medium">生成风险评估报告</p><p className="text-xs text-muted-foreground">{busy ? "查看报告生成进度" : "基于本体评估潜在风险"}</p></div>
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetContent className="gap-0 p-0 sm:max-w-xl" onCloseAutoFocus={(event) => { event.preventDefault(); trigger.current?.focus(); }}>
        <SheetHeader className="border-b px-6 py-5 pr-12">
          <SheetTitle className="flex items-center gap-2"><ShieldCheck className="size-5 text-primary" />风险评估报告</SheetTitle>
          <SheetDescription>报告生成任务可在后台继续，完成后可在线预览并下载。</SheetDescription>
        </SheetHeader>
        <div className="min-h-0 flex-1 overflow-y-auto px-6 py-6">
          <div className="mb-7 flex items-center justify-between">
            <div><p className="text-sm font-medium">{completed ? "生成完成" : failed ? "生成失败" : busy ? "正在生成" : "生成计划"}</p>
              <p className="mt-1 text-xs text-muted-foreground">{completed ? "报告已完成，可预览或下载" : busy ? "正在生成报告，可关闭面板，稍后回来查看" : failed ? "请检查失败原因后重试" : "确认以下步骤后，点击“开始生成”创建报告"}</p></div>
            <span className="text-sm font-semibold text-primary">{percent}%</span>
          </div>
          <Progress value={percent} className="mb-8 h-2" />
          <div className="space-y-1">{stages.map((label, index) => {
            const done = index < stage, active = busy && index === stage;
            return <div key={label} className={cn("flex gap-3 rounded-lg px-3 py-3", active && "bg-primary/5")}>
              <span className={cn("mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-full border text-xs", done && "border-emerald-500 bg-emerald-500 text-white", active && "border-primary text-primary")}>
                {done ? <Check className="size-3.5" /> : active ? <Loader2 className="size-3.5 animate-spin" /> : index + 1}
              </span>
              <div className="min-w-0 flex-1">
                <p className={cn("flex min-h-6 items-center text-sm font-medium", !done && !active && "text-muted-foreground")}>{label}</p>
                {index === 0 && planning && <div className="mt-2 flex items-start justify-between gap-3 rounded-md border bg-muted/30 px-3 py-2.5">
                  <p className={cn("min-w-0 pt-1 text-xs leading-5 text-muted-foreground", readError && "text-destructive", readRequested && ready && "text-emerald-600")}>
                    {finder.running ? "正在重新读取源文档并校验关系图谱…" : readError ? "重新读取与校验失败，请重试。" : readRequested && ready ? "已重新读取并校验关系图谱。" : !ready ? "关系图谱尚未就绪或已变化，请先重新读取与校验。" : "默认复用现有关系图谱数据；如源文档或抽取配置已变化，可在生成前重新读取。"}
                  </p>
                  <Button variant="outline" size="sm" className="shrink-0" disabled={!finder.canStart} onClick={() => { setReadRequested(true); finder.start(); }}>
                    {finder.running ? <Loader2 className="animate-spin" /> : <RotateCcw />}{finder.running ? "读取与校验中" : readError ? "重试读取与校验" : "重新读取与校验"}
                  </Button>
                </div>}
                {index === 1 && done && template.data && <p className="mt-1 text-xs text-muted-foreground">{template.data.name} · {template.data.version}</p>}
                {index === 2 && inputs.data && <p className="mt-1 text-xs text-muted-foreground">
                  {inputs.data.material_status === "ready" ? "输入校验完成" : "材料存在缺口或冲突，报告保留相应标记。"}
                </p>}
              </div>
            </div>;
          })}</div>
          {template.isLoading && <p role="status" className="mt-4 text-xs text-muted-foreground">正在加载报告模板…</p>}
          {errors.map((error, index) => <p key={index} role="alert" className="mt-4 whitespace-pre-wrap break-words text-sm text-destructive">{reportDocumentError(error)}</p>)}
          {current?.error && <p role="alert" className="mt-4 text-sm text-destructive">{current.error.message || current.error.code}</p>}
          {template.error && <Button className="mt-3" variant="outline" disabled={template.isFetching} onClick={() => void template.refetch()}>重新加载模板</Button>}
          {completed && <p className="mt-5 rounded-md border bg-muted/30 p-3 text-xs text-muted-foreground">演示草稿 · 请核对行文、数据来源与缺口，不能用于正式签署。</p>}
        </div>
        <div className="flex shrink-0 justify-end gap-2 border-t px-6 py-4">
          {planning && <Button disabled={!template.data || !!template.error || !ready || finder.running || !!readError || (!!runId && !current)} onClick={() => generate.mutate()}><Sparkles />开始生成</Button>}
          {busy && <Button disabled><Loader2 className="animate-spin" />生成中</Button>}
          {failed && <><Button variant="outline" onClick={enterPlan}>返回生成计划</Button><Button disabled={finder.running} onClick={() => current?.execution_status === "failed" ? retry.mutate() : generate.mutate()}><RotateCcw />重试生成</Button></>}
          {completed && <>
            <Button variant="outline" onClick={enterPlan}><RotateCcw />重新生成</Button>
            {current && artifact && <RiskReportPreview runId={current.run_id} artifactId={artifact.artifact_id} documentTitle={documentTitle} />}
            <Button disabled={download.isPending} onClick={() => download.mutate()}><Download />下载报告</Button>
          </>}
        </div>
      </SheetContent>
    </Sheet>
  </>;
}

function RiskReportPreview({ runId, artifactId, documentTitle }: {
  runId: string; artifactId: string; documentTitle: string;
}) {
  const [open, setOpen] = useState(false);
  const container = useRef<HTMLDivElement>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    // Dialog's portal mounts after its parent effect; schedule rendering on the next frame.
    const frame = requestAnimationFrame(() => {
      const target = container.current;
      if (!target) return;
      setLoading(true); setError(""); target.replaceChildren();
      void Promise.all([getReportRunArtifact(runId, artifactId, controller.signal), import("docx-preview")])
        .then(async ([blob, { renderAsync }]) => {
          if (controller.signal.aborted) return;
          await renderAsync(blob, target, undefined, { className: "risk-report-docx", inWrapper: true, useBase64URL: true });
          if (!controller.signal.aborted) setLoading(false);
        }).catch((reason: unknown) => {
          if (!controller.signal.aborted) { setError(reportDocumentError(reason)); setLoading(false); }
        });
    });
    return () => { cancelAnimationFrame(frame); controller.abort(); };
  }, [open, runId, artifactId]);
  return <Dialog open={open} onOpenChange={setOpen}>
    <DialogTrigger asChild><Button variant="outline"><Eye />预览</Button></DialogTrigger>
    <DialogContent className="left-0 top-0 flex h-[100dvh] w-screen max-w-none translate-x-0 translate-y-0 flex-col gap-0 overflow-hidden rounded-none border-0 p-0 sm:max-w-none sm:rounded-none">
      <div className="shrink-0 border-b px-5 py-3 pr-14"><DialogTitle>风险评估报告预览</DialogTitle><DialogDescription className="mt-1 truncate">{documentTitle}</DialogDescription></div>
      <div className="min-h-0 flex-1 overflow-auto bg-muted p-4">
        {loading && <p role="status">正在加载 Word 报告…</p>}
        {error && <p role="alert" className="text-destructive">{error}</p>}
        <div ref={container} />
      </div>
    </DialogContent>
  </Dialog>;
}
