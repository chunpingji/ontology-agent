"use client";

import { useState } from "react";
import { signConclusion } from "@/lib/api";

interface Props {
  conclusionId: string;
  onSigned?: () => void;
  onClose?: () => void;
}

/** 21 CFR Part 11 QA 电子签名对话框：重认证 + 签名含义（能力六, T057）。 */
export function QaSignatureDialog({ conclusionId, onSigned, onClose }: Props) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [meaning, setMeaning] = useState("已复核批准生效");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    setError(null);
    setBusy(true);
    try {
      await signConclusion({ conclusion_id: conclusionId, username, password, meaning });
      onSigned?.();
      onClose?.();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="w-96 rounded-lg bg-white p-5 shadow-lg">
        <h3 className="mb-1 font-semibold">QA 电子签名（21 CFR Part 11）</h3>
        <p className="mb-3 text-xs text-gray-500">
          签名前须重新认证。签名将不可分割地绑定此结论并使其生效。
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
          <input
            className="w-full rounded border px-2 py-1 text-sm"
            placeholder="签名含义"
            value={meaning}
            onChange={(e) => setMeaning(e.target.value)}
          />
        </div>
        <div className="mt-4 flex justify-end gap-2">
          <button className="rounded border px-3 py-1 text-sm" onClick={onClose}>
            取消
          </button>
          <button
            className="rounded bg-blue-600 px-3 py-1 text-sm text-white disabled:opacity-50"
            disabled={busy || !username || !password}
            onClick={submit}
          >
            签名并生效
          </button>
        </div>
      </div>
    </div>
  );
}
