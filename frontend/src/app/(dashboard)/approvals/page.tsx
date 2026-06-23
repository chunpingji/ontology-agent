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
        <div className="rounded-lg border border-amber-200 bg-amber-50 p-6">
          <p className="font-medium text-amber-800">需要 QA 角色</p>
          <p className="mt-1 text-sm text-amber-700">
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
      <p className="mb-5 text-sm text-gray-500">
        QA 治理工作台 —— 待签结论、21 CFR Part 11 电子签批 / 拒绝、审计链验真。
      </p>

      <div className="space-y-6">
        <section className="rounded-lg border bg-white p-4">
          <h2 className="mb-3 font-semibold">待签结论</h2>
          {pending.length === 0 ? (
            <p className="text-sm text-gray-400">暂无待签名结论</p>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b text-left text-xs text-gray-500">
                  <th className="py-1">结论 ID</th>
                  <th className="py-1">风险等级</th>
                  <th className="py-1">类型</th>
                  <th className="py-1 text-right">操作</th>
                </tr>
              </thead>
              <tbody>
                {pending.map((c) => (
                  <tr key={c.id} className="border-b last:border-0">
                    <td className="py-1.5 font-mono text-xs">{c.id.slice(0, 8)}</td>
                    <td className="py-1.5">{c.risk_level ?? "—"}</td>
                    <td className="py-1.5">{c.execution_type}</td>
                    <td className="py-1.5 text-right">
                      <button
                        onClick={() => setSigning(c.id)}
                        className="mr-2 rounded bg-blue-600 px-3 py-1 text-xs text-white hover:bg-blue-700"
                      >
                        签批
                      </button>
                      <button
                        onClick={() => setRejecting(c.id)}
                        className="rounded border border-red-300 px-3 py-1 text-xs text-red-700 hover:bg-red-50"
                      >
                        拒绝
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>

        <section className="rounded-lg border bg-white p-4">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="font-semibold">审计哈希链</h2>
            <button
              onClick={handleVerify}
              className="rounded border px-3 py-1 text-xs hover:bg-gray-50"
            >
              校验审计链
            </button>
          </div>
          {verify && (
            <p className={`mb-3 text-xs ${verify.ok ? "text-green-700" : "text-red-600"}`}>
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
            <p className="text-sm text-gray-400">暂无审计条目</p>
          ) : (
            <div className="overflow-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b bg-gray-50 text-left text-xs text-gray-500">
                    <th className="px-2 py-1">seq</th>
                    <th className="px-2 py-1">动作</th>
                    <th className="px-2 py-1">操作者</th>
                    <th className="px-2 py-1">实体</th>
                    <th className="px-2 py-1">时间</th>
                  </tr>
                </thead>
                <tbody>
                  {audit.map((e, i) => (
                    <tr key={e.entry_hash ?? `${e.seq}-${i}`} className="border-b last:border-0">
                      <td className="px-2 py-1 font-mono text-xs">{e.seq ?? "—"}</td>
                      <td className="px-2 py-1 text-xs">{e.action}</td>
                      <td className="px-2 py-1 text-xs">{e.actor ?? "—"}</td>
                      <td className="px-2 py-1 font-mono text-xs text-gray-500">
                        {e.entity_iri ? e.entity_iri.split("/").pop() : "—"}
                      </td>
                      <td className="px-2 py-1 text-xs text-gray-500">{e.created_at ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
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
