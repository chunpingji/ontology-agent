"use client";

import { useEffect, useState } from "react";
import {
  getComplianceAudit,
  getPendingSignatures,
  verifyAudit,
  type ComplianceAuditEntry,
  type PendingConclusion,
} from "@/lib/api";
import { QaSignatureDialog } from "@/components/approvals/qa-signature-dialog";
import { RejectDialog } from "@/components/approvals/reject-dialog";
import { useIdentity } from "@/lib/use-identity";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableHeader,
  TableBody,
  TableRow,
  TableHead,
  TableCell,
} from "@/components/ui/table";

type VerifyResult = {
  ok: boolean;
  verified_count?: number;
  head_seq?: number;
  broken_at_seq?: number;
};

export default function ApprovalsPage() {
  const { role } = useIdentity();
  const isQa = role === "qa";

  const [pending, setPending] = useState<PendingConclusion[]>([]);
  const [audit, setAudit] = useState<ComplianceAuditEntry[]>([]);
  const [signing, setSigning] = useState<string | null>(null);
  const [rejecting, setRejecting] = useState<string | null>(null);
  const [verify, setVerify] = useState<VerifyResult | null>(null);

  // 仅 qa 拉取治理数据;.then 形式回填,避免 set-state-in-effect。
  // 非 qa 时直接 return(治理数据不渲染,无需清空 state)。
  useEffect(() => {
    if (!isQa) return;
    getPendingSignatures().then((r) => setPending(r.conclusions)).catch(() => {});
    getComplianceAudit().then((r) => setAudit(r.entries)).catch(() => {});
  }, [isQa]);

  // 供弹窗回调复用(事件处理,非 effect)。
  async function refresh() {
    try {
      const [p, a] = await Promise.all([getPendingSignatures(), getComplianceAudit()]);
      setPending(p.conclusions);
      setAudit(a.entries);
    } catch (e) {
      console.error(e);
    }
  }

  async function handleVerify() {
    try {
      setVerify(await verifyAudit());
    } catch (e) {
      console.error(e);
      setVerify({ ok: false });
    }
  }

  if (!isQa) {
    return (
      <div>
        <h1 className="mb-4 text-xl font-bold">审批中心</h1>
        <div className="rounded-lg border border-warning/40 bg-warning/10 p-6">
          <p className="font-medium text-warning">需要 QA 角色</p>
          <p className="mt-1 text-sm text-warning">
            电子签批、QA 拒绝与审计治理操作仅对 QA 角色开放。请使用左下角身份切换器切换到
            QA 后再进入。后端对治理端点亦有 <code>require_role(qa)</code> 硬约束。
          </p>
        </div>
      </div>
    );
  }

  return (
    <div>
      <h1 className="mb-1 text-xl font-bold">审批中心</h1>
      <p className="mb-5 text-sm text-muted-foreground">
        QA 治理工作台 —— 待签结论、21 CFR Part 11 电子签批 / 拒绝、审计链验真。
      </p>

      <div className="space-y-6">
        <section className="rounded-lg border bg-card p-4">
          <h2 className="mb-3 font-semibold">待签结论</h2>
          {pending.length === 0 ? (
            <p className="text-sm text-muted-foreground">暂无待签名结论</p>
          ) : (
            <Table className="text-sm">
              <TableHeader>
                <TableRow className="text-left text-xs text-muted-foreground">
                  <TableHead className="py-1">结论 ID</TableHead>
                  <TableHead className="py-1">风险等级</TableHead>
                  <TableHead className="py-1">类型</TableHead>
                  <TableHead className="py-1 text-right">操作</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {pending.map((c) => (
                  <TableRow key={c.id}>
                    <TableCell className="py-1.5 font-mono text-xs">{c.id.slice(0, 8)}</TableCell>
                    <TableCell className="py-1.5">
                      <Badge variant="secondary">{c.risk_level ?? "—"}</Badge>
                    </TableCell>
                    <TableCell className="py-1.5">{c.execution_type}</TableCell>
                    <TableCell className="py-1.5 text-right">
                      <Button
                        variant="default"
                        size="sm"
                        onClick={() => setSigning(c.id)}
                        className="mr-2 h-auto px-3 py-1 text-xs"
                      >
                        签批
                      </Button>
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => setRejecting(c.id)}
                        className="h-auto border-destructive/40 px-3 py-1 text-xs text-destructive hover:bg-destructive/10 hover:text-destructive"
                      >
                        拒绝
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </section>

        <section className="rounded-lg border bg-card p-4">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="font-semibold">审计哈希链</h2>
            <Button
              variant="outline"
              size="sm"
              onClick={handleVerify}
              className="h-auto px-3 py-1 text-xs"
            >
              校验审计链
            </Button>
          </div>
          {verify && (
            <p className={`mb-3 text-xs ${verify.ok ? "text-success" : "text-destructive"}`}>
              {verify.ok
                ? `链完整 ✓${
                    verify.verified_count != null ? `（已校验 ${verify.verified_count} 条）` : ""
                  }`
                : `链已被篡改 ✗${
                    verify.broken_at_seq != null ? `（断裂于 seq=${verify.broken_at_seq}）` : ""
                  }`}
            </p>
          )}
          {audit.length === 0 ? (
            <p className="text-sm text-muted-foreground">暂无审计条目</p>
          ) : (
            <div className="overflow-auto">
              <Table className="text-sm">
                <TableHeader>
                  <TableRow className="bg-muted text-left text-xs text-muted-foreground">
                    <TableHead className="px-2 py-1">seq</TableHead>
                    <TableHead className="px-2 py-1">动作</TableHead>
                    <TableHead className="px-2 py-1">操作者</TableHead>
                    <TableHead className="px-2 py-1">实体</TableHead>
                    <TableHead className="px-2 py-1">时间</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {audit.map((e, i) => (
                    <TableRow key={e.entry_hash ?? `${e.seq}-${i}`}>
                      <TableCell className="px-2 py-1 font-mono text-xs">{e.seq ?? "—"}</TableCell>
                      <TableCell className="px-2 py-1 text-xs">{e.action}</TableCell>
                      <TableCell className="px-2 py-1 text-xs">{e.actor ?? "—"}</TableCell>
                      <TableCell className="px-2 py-1 font-mono text-xs text-muted-foreground">
                        {e.entity_iri ? e.entity_iri.split("/").pop() : "—"}
                      </TableCell>
                      <TableCell className="px-2 py-1 text-xs text-muted-foreground">{e.created_at ?? "—"}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </section>
      </div>

      {signing && (
        <QaSignatureDialog
          conclusionId={signing}
          onSigned={refresh}
          onClose={() => setSigning(null)}
        />
      )}
      {rejecting && (
        <RejectDialog
          conclusionId={rejecting}
          onRejected={refresh}
          onClose={() => setRejecting(null)}
        />
      )}
    </div>
  );
}
