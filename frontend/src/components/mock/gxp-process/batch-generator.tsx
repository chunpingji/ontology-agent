"use client";

import { useState } from "react";
import { FileText, Scissors } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Check, Field } from "./controls";
import { createInitialOperations, parameterRequirement } from "./data";
import { MOCK_CMC_REPORTS, batchNumberIssue, type MockCmcReport } from "./batch";

const templates = createInitialOperations();

export function BatchGenerator({ existingBatchNumbers, onClose, onGenerate }: {
  existingBatchNumbers: string[];
  onClose: () => void;
  onGenerate: (reportId: string, batchNumber: string, selected: string[]) => void;
}) {
  const [reportId, setReportId] = useState(MOCK_CMC_REPORTS[0].id);
  const [batchNumber, setBatchNumber] = useState("");
  const report = MOCK_CMC_REPORTS.find((item) => item.id === reportId)!;
  return <Dialog open onOpenChange={(open) => { if (!open) onClose(); }}>
    <DialogContent className="max-h-[90vh] w-[calc(100%-2rem)] max-w-4xl overflow-y-auto">
      <DialogHeader><DialogTitle className="flex items-center gap-2"><Scissors className="size-5 text-primary" />生成批号工艺</DialogTitle><DialogDescription>选择研发类 CMC 示例报告，裁剪通用操作并生成批 GxP 配置草稿。</DialogDescription></DialogHeader>
      <div className="flex flex-wrap items-center gap-2 rounded-md bg-primary/5 px-3 py-2 text-xs text-muted-foreground"><Badge variant="secondary">静态演示</Badge>报告列表、摘录及参数均为模拟数据；草稿仅保留在本次会话。</div>
      <div className="grid gap-4 sm:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <Field label="报告中心 · 研发类 CMC 文档"><Select value={reportId} onValueChange={setReportId}><SelectTrigger aria-label="研发类 CMC 文档" className="min-w-0"><SelectValue /></SelectTrigger><SelectContent>{MOCK_CMC_REPORTS.map((item) => <SelectItem key={item.id} value={item.id}>{item.title}</SelectItem>)}</SelectContent></Select></Field>
        <Field label="目标批号"><Input value={batchNumber} onChange={(event) => setBatchNumber(event.target.value)} placeholder="例如 DEMO-20260910-01" maxLength={64} /></Field>
      </div>
      <div className="space-y-2 rounded-md border p-3 text-xs"><div className="flex flex-wrap items-center gap-2 font-medium"><FileText className="size-4 text-primary" />{report.id}<Badge variant="outline">研发 / CMC</Badge><span className="text-muted-foreground">{report.version} · {report.date}</span></div><p className="leading-relaxed text-muted-foreground">{report.route}</p></div>
      <TrimmingPreview key={report.id} report={report} batchNumber={batchNumber} existingBatchNumbers={existingBatchNumbers} onClose={onClose} onGenerate={onGenerate} />
    </DialogContent>
  </Dialog>;
}

function TrimmingPreview({ report, batchNumber, existingBatchNumbers, onClose, onGenerate }: {
  report: MockCmcReport; batchNumber: string; existingBatchNumbers: string[];
  onClose: () => void; onGenerate: (reportId: string, batchNumber: string, selected: string[]) => void;
}) {
  const [selected, setSelected] = useState(report.operations.map((item) => item.operationId));
  const issue = batchNumberIssue(batchNumber, existingBatchNumbers);
  const selectedSteps = new Set(selected.map((id) => id.split(".")[0])).size;
  return <>
    <div className="flex flex-wrap items-center justify-between gap-2"><div><h3 className="text-sm font-semibold">操作裁剪预览</h3><p role="status" className="mt-1 text-xs text-muted-foreground">保留 {selectedSteps} 个步骤 / {selected.length} 个操作 · 裁剪 {templates.length - selected.length} 个通用操作</p></div><div className="flex gap-1"><Button variant="ghost" size="sm" onClick={() => setSelected(report.operations.map((item) => item.operationId))}>全选适用操作</Button><Button variant="ghost" size="sm" onClick={() => setSelected([])}>清空选择</Button></div></div>
    <div aria-label="适用操作清单" className="max-h-[38vh] space-y-2 overflow-y-auto rounded-md border bg-muted/20 p-2">
      {report.operations.map((clause, index) => {
        const operation = templates.find((item) => item.id === clause.operationId)!;
        return <div key={clause.operationId} className="space-y-2 rounded-md border bg-background p-3">
          <div className="flex flex-wrap items-center justify-between gap-2"><Check label={`${String(index + 1).padStart(2, "0")} ${operation.name}`} checked={selected.includes(operation.id)} onChange={(checked) => setSelected((ids) => checked ? [...ids, operation.id] : ids.filter((id) => id !== operation.id))} /><span className="text-[11px] text-muted-foreground">{operation.id} · {clause.section}</span></div>
          <p className="text-xs leading-relaxed text-muted-foreground">{clause.excerpt}</p>
          {!!clause.parameters.length && <div className="flex flex-wrap gap-1.5">{clause.parameters.map((parameter) => <Badge key={parameter.id} variant="secondary" className="font-normal">{parameter.name} {parameterRequirement(parameter)}</Badge>)}</div>}
        </div>;
      })}
    </div>
    <p className="text-xs leading-relaxed text-muted-foreground">采用内置通用操作结构，填入报告示例参数。未勾选及报告未列出的操作不纳入本批草稿；操作衔接、未列明参数及批准依据仍需完善。</p>
    {(batchNumber.length > 0 && issue || !selected.length) && <p role="alert" className="text-xs text-destructive">{batchNumber.length > 0 && issue || "请至少保留一个操作。"}</p>}
    <DialogFooter className="gap-2"><Button variant="outline" onClick={onClose}>取消</Button><Button disabled={!!issue || !selected.length} onClick={() => onGenerate(report.id, batchNumber, selected)}><Scissors />生成批 GxP 草稿</Button></DialogFooter>
  </>;
}
