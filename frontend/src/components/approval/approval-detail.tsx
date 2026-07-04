"use client";

import { useState } from "react";
import {
  AlertTriangle,
  Ban,
  CheckCircle2,
  FileText,
  PenLine,
} from "lucide-react";

import type { ApprovalTask } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { QaSignatureDialog } from "@/components/approvals/qa-signature-dialog";
import { RejectDialog } from "@/components/approvals/reject-dialog";
import { useIdentity } from "@/lib/use-identity";
import { cn } from "@/lib/utils";

const FORMAT_COLORS: Record<string, string> = {
  PDF: "text-destructive",
  DOCX: "text-primary",
  XLSX: "text-success",
  DOC: "text-primary",
  XLS: "text-success",
};

function UrgencyBadge({ urgency }: { urgency: string }) {
  const cls =
    urgency === "high"
      ? "border-destructive/30 bg-destructive/10 text-destructive"
      : urgency === "medium"
        ? "border-warning/30 bg-warning/10 text-warning"
        : "border-border bg-muted text-muted-foreground";
  const label = urgency === "high" ? "高" : urgency === "medium" ? "中" : "低";
  return <Badge variant="outline" className={cn("text-xs", cls)}>{label}</Badge>;
}

export function ApprovalDetail({ task }: { task: ApprovalTask }) {
  const { role } = useIdentity();
  const canDecide = role === "qa";
  const [signing, setSigning] = useState(false);
  const [rejecting, setRejecting] = useState(false);
  const rec = task.systemRecommendation;
  const pde = rec?.pdeAnalysis;

  return (
    <div className="flex flex-col">
      {/* ── Header ── */}
      <div className="flex items-start justify-between gap-4 border-b border-border px-6 py-4">
        <div className="min-w-0">
          <h2 className="text-lg font-semibold text-foreground">{task.title}</h2>
          <p className="mt-0.5 text-xs text-muted-foreground">
            {task.referenceNo} · 提交人: {task.submitter} · {task.submittedLabel}
          </p>
        </div>
        <div className="flex shrink-0 gap-2">
          <Button
            variant="outline"
            size="sm"
            disabled={!canDecide}
            onClick={() => setRejecting(true)}
            className="border-destructive/40 text-destructive hover:bg-destructive/10 hover:text-destructive"
          >
            <Ban className="mr-1 size-3.5" />
            驳回
          </Button>
          <Button
            size="sm"
            disabled={!canDecide}
            onClick={() => setSigning(true)}
            className="bg-success text-white hover:bg-success/90"
          >
            <CheckCircle2 className="mr-1 size-3.5" />
            通过
          </Button>
        </div>
      </div>

      {/* ── Body ── */}
      <div className="flex-1 space-y-6 overflow-auto px-6 py-5">
        {/* System recommendation alert */}
        {rec && (
          <div className={cn(
            "rounded-lg border p-4",
            rec.action === "reject"
              ? "border-destructive/30 bg-destructive/5"
              : "border-success/30 bg-success/5",
          )}>
            <div className="flex items-center gap-2 text-sm font-semibold">
              <AlertTriangle className={cn("size-4", rec.action === "reject" ? "text-destructive" : "text-success")} />
              <span className={rec.action === "reject" ? "text-destructive" : "text-success"}>
                系统风险提示 — 建议{rec.action === "reject" ? "驳回" : "通过"}
              </span>
            </div>
            <p className="mt-2 text-sm text-foreground">{rec.reason}</p>

            {pde && (
              <div className="mt-3 space-y-3">
                <div className="grid grid-cols-2 gap-3">
                  <div className="rounded-md border border-border bg-background p-3">
                    <p className="text-xs text-muted-foreground">报告声称 PDE</p>
                    <p className="mt-0.5 text-lg font-bold text-foreground">{pde.reportedPDE}</p>
                  </div>
                  <div className="rounded-md border border-destructive/30 bg-destructive/5 p-3">
                    <p className="text-xs text-destructive">系统推算 PDE</p>
                    <p className="mt-0.5 text-lg font-bold text-destructive">{pde.calculatedPDE}</p>
                    <p className="text-xs text-destructive">偏差 {pde.deviation}</p>
                  </div>
                </div>

                <div className="rounded-md border border-border bg-background p-3">
                  <p className="mb-2 text-xs font-medium text-muted-foreground">推算过程</p>
                  <p className="text-xs text-muted-foreground">
                    NOAEL = {pde.noael}（{pde.noaelSource}），体重修正 = {pde.bodyWeight} kg
                  </p>
                  <div className="mt-2 overflow-hidden rounded border border-border text-xs">
                    <table className="w-full">
                      <thead>
                        <tr className="border-b border-border bg-muted">
                          <th className="px-3 py-1.5 text-left font-medium text-muted-foreground">安全系数</th>
                          <th className="px-3 py-1.5 text-left font-medium text-muted-foreground">说明</th>
                          <th className="px-3 py-1.5 text-right font-medium text-muted-foreground">取值</th>
                        </tr>
                      </thead>
                      <tbody>
                        {Object.entries(pde.factors).map(([key, val]) => (
                          <tr key={key} className="border-b border-border last:border-0">
                            <td className="px-3 py-1.5 font-mono font-medium text-foreground">{key}</td>
                            <td className="px-3 py-1.5 text-muted-foreground">{pde.factorLabels[key]}</td>
                            <td className="px-3 py-1.5 text-right font-mono text-foreground">{val}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <p className="mt-2 text-xs text-muted-foreground">
                    PDE = (NOAEL × 体重) / (F1×F2×F3×F4×F5) = ({pde.noael.replace(" mg/kg/day", "")} × {pde.bodyWeight}) / ({Object.values(pde.factors).join("×")}) = <span className="font-semibold text-destructive">{pde.calculatedPDE}</span>
                  </p>
                </div>

                <div className="flex gap-3">
                  <div className="flex-1 rounded-md border border-border bg-background p-3">
                    <p className="text-xs text-muted-foreground">报告隐含 OEB</p>
                    <p className="mt-0.5 text-sm font-medium text-foreground">{pde.oebReported}</p>
                  </div>
                  <div className="flex-1 rounded-md border border-destructive/30 bg-destructive/5 p-3">
                    <p className="text-xs text-destructive">系统判定 OEB</p>
                    <p className="mt-0.5 text-sm font-semibold text-destructive">{pde.oebCalculated}</p>
                  </div>
                </div>
              </div>
            )}
          </div>
        )}

        {/* Summary */}
        <section>
          <h3 className="mb-3 text-sm font-semibold text-foreground">审批摘要</h3>
          <div className="grid grid-cols-2 gap-x-8 gap-y-3">
            <Field label="审批类型" value={task.type} />
            <Field label="关联实体" value={task.entityName} />
            <Field label="研究阶段" value={task.phase} />
            <Field label="紧急程度">
              <UrgencyBadge urgency={task.urgency} />
            </Field>
          </div>
        </section>

        <Separator />

        {/* Documents */}
        <section>
          <h3 className="mb-3 text-sm font-semibold text-foreground">关联文档</h3>
          <ul className="space-y-2">
            {task.documents.map((doc) => (
              <li key={doc.name} className="flex items-center gap-3">
                <FileText className="size-5 shrink-0 text-muted-foreground" />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm text-foreground">{doc.name}</p>
                  <p className={cn("text-[10px] font-semibold uppercase", FORMAT_COLORS[doc.format] ?? "text-muted-foreground")}>
                    {doc.format}
                  </p>
                </div>
              </li>
            ))}
          </ul>
        </section>

        <Separator />

        {/* Timeline */}
        <section>
          <h3 className="mb-3 text-sm font-semibold text-foreground">审批流程</h3>
          <ol className="space-y-3 border-l-2 border-border pl-4">
            {task.timeline.map((entry, i) => (
              <li key={i} className="relative">
                <span className="absolute -left-[21px] top-1 size-2.5 rounded-full border-2 border-success bg-success" />
                <p className="text-sm font-medium text-foreground">{entry.action}</p>
                <p className="text-xs text-muted-foreground">
                  {entry.actor} · {entry.time}
                </p>
              </li>
            ))}
          </ol>
        </section>

        {!canDecide && (
          <p className="text-xs text-muted-foreground">
            仅 QA 角色可执行签批 / 驳回操作，请切换至 QA 身份后操作。
          </p>
        )}
      </div>

      {signing && (
        <QaSignatureDialog
          conclusionId={task.id}
          onSigned={() => setSigning(false)}
          onClose={() => setSigning(false)}
        />
      )}
      {rejecting && (
        <RejectDialog
          conclusionId={task.id}
          onRejected={() => setRejecting(false)}
          onClose={() => setRejecting(false)}
        />
      )}
    </div>
  );
}

function Field({ label, value, children }: { label: string; value?: string; children?: React.ReactNode }) {
  return (
    <div>
      <p className="text-xs text-muted-foreground">{label}</p>
      {children ?? <p className="mt-0.5 text-sm text-foreground">{value}</p>}
    </div>
  );
}
