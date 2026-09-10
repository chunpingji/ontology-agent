"use client";

import { useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Check, ClipboardList, Download, Eye, Loader2, RotateCcw } from "lucide-react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { Progress } from "@/components/ui/progress";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import {
  downloadReportById, generateBatchDemo, getBatchDemo, getIdentity,
  type BatchDemoAvailable,
} from "@/lib/api";
import { cn } from "@/lib/utils";
import { saveBlob } from "./reading-pane";
import { BatchReportPreview } from "./batch-report-preview";
import { reportDocumentError } from "./report-word-workspace";

const STAGES = ["读取与校验数据", "匹配报告模板", "批记录生成", "生成报告文档", "保存生成结果"];


export function BatchRecordDrawer({ data, open, onOpenChange }: {
  data: BatchDemoAvailable; open: boolean; onOpenChange: (open: boolean) => void;
}) {
  const client = useQueryClient();
  const { role } = getIdentity();
  const canGenerate = role === "senior_analyst";
  const requestKey = useRef<string | null>(null);
  const [active, setActive] = useState(0);
  const [preview, setPreview] = useState(false);
  const [freshPlan, setFreshPlan] = useState(false);
  const generate = useMutation({
    mutationFn: async () => {
      setActive(0);
      const latest = await getBatchDemo(data.document_iri);
      if (!latest.available || latest.graph_hash !== data.graph_hash || latest.template_hash !== data.template_hash) {
        throw new Error("图谱或模板版本已变化，请重新读取后再生成。");
      }
      if (!latest.validation.passed) throw new Error("输入契约校验未通过，请检查缺失项。");
      setActive(2);
      // getRandomValues also works on HTTP intranet deployments.
      requestKey.current ??= Array.from(crypto.getRandomValues(new Uint8Array(16)),
        (value) => value.toString(16).padStart(2, "0")).join("");
      return generateBatchDemo(data.document_iri, {
        graph_hash: data.graph_hash, template_hash: data.template_hash, request_key: requestKey.current,
      });
    },
    onSuccess: () => {
      setActive(5);
      setFreshPlan(false);
      void client.invalidateQueries({ queryKey: ["batch-demo"] });
      void client.invalidateQueries({ queryKey: ["report-center"] });
      void client.invalidateQueries({ queryKey: ["report-center-resolve"] });
    },
  });
  const report = generate.data ?? (freshPlan ? null : data.latest_report);
  const complete = Boolean(report) && !generate.isPending;
  const download = useMutation({
    mutationFn: async () => {
      if (!report) return;
      const blob = await downloadReportById(report.job_id, report.id);
      saveBlob(blob, `批记录报告_演示草稿_HRS-5592_${report.id.slice(0, 8)}.docx`);
    },
  });
  const refresh = useMutation({
    mutationFn: () => client.invalidateQueries({ queryKey: ["batch-demo"] }),
  });
  return <>
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="gap-0 p-0 sm:max-w-2xl">
        <SheetHeader className="border-b px-6 py-5 pr-12">
          <SheetTitle className="flex items-center gap-2"><ClipboardList className="size-5 text-primary" />批记录报告<Badge variant="secondary">静态演示</Badge></SheetTitle>
          <SheetDescription>读取当前关系图谱，匹配演示模板，生成并保存 Word 批记录草稿。</SheetDescription>
        </SheetHeader>
        <div className="min-h-0 flex-1 overflow-y-auto p-6">
          <p className="mb-2 text-sm font-medium">{generate.isError ? "生成失败" : complete ? "生成完成 · 已保存" : generate.isPending ? "正在生成" : "生成计划"}</p>
          <Progress value={complete ? 100 : generate.isPending ? (active === 0 ? 5 : 40) : 0} className="mb-6 h-2" />
          <div className="space-y-5">
            {STAGES.map((label, index) => {
              const done = complete || (generate.isPending && index < active);
              const running = generate.isPending && index === active;
              return <div key={label} className="flex gap-3">
                <span className={cn("flex size-7 shrink-0 items-center justify-center rounded-full border text-xs", done && "border-primary bg-primary text-primary-foreground", running && "border-primary text-primary")}>
                  {done ? <Check className="size-4" /> : running ? <Loader2 className="size-4 animate-spin" /> : index + 1}
                </span>
                <div className="min-w-0 flex-1 pt-0.5">
                  <p className="text-sm font-medium">{label}<span className="ml-2 text-xs font-normal text-muted-foreground">{done ? "已完成" : running ? "执行中" : "待执行"}</span></p>
                  {complete && <p className="mt-1 text-xs text-muted-foreground">{report?.stages[index]?.detail}</p>}
                  {index === 0 && <details open className="mt-3 rounded-md border p-3 text-xs">
                    <summary className="cursor-pointer font-medium">图谱输入校验 · {data.validation.checks.filter((c) => c.passed).length}/{data.validation.checks.length} 项通过</summary>
                    <div className="mt-3 space-y-2">{data.validation.checks.map((check) => <div key={check.id}>
                      <p className={cn("flex items-center justify-between gap-2", !check.passed && "text-destructive")}><span>{check.label}</span><span>{check.passed ? `${check.count} 个实体 ✓` : "未通过"}</span></p>
                      {check.errors.map((error) => <p key={error} className="mt-1 text-destructive">{error}</p>)}
                    </div>)}</div>
                  </details>}
                  {index === 1 && <details className="mt-3 rounded-md border p-3 text-xs"><summary className="cursor-pointer font-medium">{data.template.name} v{data.template.version}</summary>
                    <p className="mt-2 text-muted-foreground">输入类型：CMCReport · 模板按关系路径绑定当前图谱；导出采用指定批生产记录样例版式</p>
                    {data.validation.checks.map((check) => <div key={check.id} className="mt-2 break-words"><p>{check.label}</p><p className="mt-1 font-mono text-muted-foreground">{check.path.map((p) => p.split("/").pop()).join(" → ")}</p></div>)}
                  </details>}
                  {index === 2 && <p className="mt-2 text-xs leading-5 text-muted-foreground">填充设备、物料、四个工艺阶段、操作要求和中间体；实际操作记录、批号及签名栏留空，供打印后填写。</p>}
                </div>
              </div>;
            })}
          </div>
          <details className="mt-6 rounded-md bg-muted/40 p-3 text-xs"><summary className="cursor-pointer font-medium">演示数据说明与原文待核对项</summary><ul className="mt-2 list-disc space-y-2 pl-4">{data.graph.warnings.map((w) => <li key={w}>{w}</li>)}</ul></details>
          {complete && <p className="mt-4 break-all text-xs text-muted-foreground">保存时间：{report?.created_at ? new Date(report.created_at).toLocaleString() : "刚刚"} · 报告编号：{report?.id}</p>}
          {generate.isError && <Alert variant="destructive" className="mt-4"><AlertDescription>{reportDocumentError(generate.error)}</AlertDescription></Alert>}
          {download.isError && <Alert variant="destructive" className="mt-4"><AlertDescription>下载失败：{reportDocumentError(download.error)}</AlertDescription></Alert>}
          {!canGenerate && <p className="mt-4 text-sm text-muted-foreground">生成批记录需要高级分析员角色。</p>}
        </div>
        <div className="flex flex-wrap justify-end gap-2 border-t px-6 py-4">
          {!complete && !generate.isPending && <Button variant="outline" disabled={refresh.isPending} onClick={() => refresh.mutate()}><RotateCcw />重新读取与校验</Button>}
          {complete ? <>
            <Button variant="outline" disabled={!canGenerate} onClick={() => { setFreshPlan(true); requestKey.current = null; generate.reset(); }}>重新生成</Button>
            <Button variant="outline" onClick={() => setPreview(true)}><Eye />预览</Button>
            <Button disabled={download.isPending} onClick={() => download.mutate()}>{download.isPending ? <Loader2 className="animate-spin" /> : <Download />}下载报告</Button>
          </> : <Button disabled={generate.isPending || !data.validation.passed || !canGenerate} onClick={() => generate.mutate()}>
            {generate.isPending && <Loader2 className="animate-spin" />}{generate.isPending ? "正在生成并保存" : generate.isError ? "重试生成" : "开始生成"}
          </Button>}
        </div>
        <Dialog open={preview} onOpenChange={setPreview}>
          <DialogContent className="flex h-[90dvh] max-w-5xl flex-col gap-0 p-0 sm:max-w-5xl">
            <div className="border-b p-5 pr-12"><DialogTitle>批记录报告预览（演示草稿）</DialogTitle><DialogDescription className="mt-1">已保存的报告内容，与下载 Word 使用同一份生成结果。</DialogDescription></div>
            <div className="min-h-0 flex-1 overflow-auto bg-muted/30 p-6"><article className="mx-auto max-w-4xl bg-background p-6 shadow-sm">{report && <BatchReportPreview node={report.body_ast} />}</article></div>
          </DialogContent>
        </Dialog>
      </SheetContent>
    </Sheet>

    {generate.isPending && !open && <Button className="fixed bottom-6 right-6 z-50 shadow-lg" onClick={() => onOpenChange(true)}><Loader2 className="animate-spin" />查看批记录生成进度</Button>}
  </>;
}
