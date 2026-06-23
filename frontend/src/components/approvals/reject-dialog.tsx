"use client";

import { useState } from "react";
import { rejectConclusion } from "@/lib/api";

interface Props {
  conclusionId: string;
  onRejected?: () => void;
  onClose?: () => void;
}

/** QA 拒绝对话框（21 CFR Part 11 重认证 + 拒绝原因, T011）。 */
export function RejectDialog({ conclusionId, onRejected, onClose }: Props) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    setError(null);
    setBusy(true);
    try {
      const r = await rejectConclusion({
        conclusion_id: conclusionId,
        username,
        password,
        reason,
      });
      onRejected?.();
      onClose?.();
      if (r.voided_actions > 0) {
        // 信息性提示:被抑制的非终态动作随拒绝一并作废。
        console.info(`已作废 ${r.voided_actions} 个非终态动作`);
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="w-96 rounded-lg bg-white p-5 shadow-lg">
        <h3 className="mb-1 font-semibold">QA 拒绝结论（21 CFR Part 11）</h3>
        <p className="mb-3 text-xs text-gray-500">
          拒绝前须重新认证。拒绝将使结论进入终态，并作废其被抑制的非终态动作。
        </p>
        {error && <p className="mb-2 text-sm text-red-600">{error}</p>}
        <div className="space-y-2">
          <input
            className="w-full rounded border px-2 py-1 text-sm"
            placeholder="用户名"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
          />
          <input
            type="password"
            className="w-full rounded border px-2 py-1 text-sm"
            placeholder="密码（重认证）"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
          <textarea
            className="w-full rounded border px-2 py-1 text-sm"
            placeholder="拒绝原因"
            rows={3}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />
        </div>
        <div className="mt-4 flex justify-end gap-2">
          <button className="rounded border px-3 py-1 text-sm" onClick={onClose}>
            取消
          </button>
          <button
            className="rounded bg-red-600 px-3 py-1 text-sm text-white disabled:opacity-50"
            disabled={busy || !username || !password || !reason}
            onClick={submit}
          >
            确认拒绝
          </button>
        </div>
      </div>
    </div>
  );
}
