"use client";

import { useCallback, useEffect, useState } from "react";
import {
  createConnector,
  deleteConnector,
  listConnectorRuns,
  listConnectors,
  syncConnector,
  testConnector,
  type Connector,
  type MaterializationRun,
} from "@/lib/api";

/** 连接器管理：CRUD + 探活 + 同步触发 + 物化运行列表（能力三, T049）。 */
export function ConnectorManager() {
  const [connectors, setConnectors] = useState<Connector[]>([]);
  const [runs, setRuns] = useState<Record<string, MaterializationRun[]>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [form, setForm] = useState({ name: "", system_type: "APS", poll_interval_seconds: 2 });
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    listConnectors().then(setConnectors).catch((e) => setError(String(e)));
  }, []);
  useEffect(() => refresh(), [refresh]);

  const onCreate = async () => {
    setError(null);
    try {
      await createConnector({
        name: form.name || "新连接器",
        system_type: form.system_type,
        ingest_mode: "poll",
        poll_interval_seconds: Number(form.poll_interval_seconds) || 2,
        connection_config: { source_mode: "inline", inline_changes: [] },
      });
      setForm({ name: "", system_type: "APS", poll_interval_seconds: 2 });
      refresh();
    } catch (e) {
      setError(String(e));
    }
  };

  const onTest = async (id: string) => {
    setBusy(id);
    try {
      const r = await testConnector(id);
      alert(r.ok ? `连接正常 (${r.latency_ms}ms)` : `连接失败：${r.error}`);
    } finally {
      setBusy(null);
    }
  };

  const onSync = async (id: string) => {
    setBusy(id);
    try {
      await syncConnector(id);
      const r = await listConnectorRuns(id);
      setRuns((prev) => ({ ...prev, [id]: r.runs }));
      refresh();
    } finally {
      setBusy(null);
    }
  };

  const onDelete = async (id: string) => {
    if (!confirm("确认删除该连接器？")) return;
    await deleteConnector(id);
    refresh();
  };

  return (
    <div className="space-y-4">
      <div className="rounded-lg border bg-white p-4">
        <h3 className="mb-3 font-semibold">新增连接器</h3>
        {error && <p className="mb-2 text-sm text-red-600">{error}</p>}
        <div className="flex flex-wrap gap-2">
          <input
            className="rounded border px-2 py-1 text-sm"
            placeholder="名称"
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
          />
          <input
            className="w-24 rounded border px-2 py-1 text-sm"
            placeholder="类型"
            value={form.system_type}
            onChange={(e) => setForm({ ...form, system_type: e.target.value })}
          />
          <input
            type="number"
            className="w-28 rounded border px-2 py-1 text-sm"
            placeholder="轮询(秒)"
            value={form.poll_interval_seconds}
            onChange={(e) =>
              setForm({ ...form, poll_interval_seconds: Number(e.target.value) })
            }
          />
          <button
            className="rounded bg-blue-600 px-3 py-1 text-sm text-white"
            onClick={onCreate}
          >
            创建
          </button>
        </div>
      </div>

      <div className="space-y-3">
        {connectors.map((c) => (
          <div key={c.id} className="rounded-lg border bg-white p-4">
            <div className="flex items-center justify-between">
              <div>
                <span className="font-semibold">{c.name}</span>
                <span className="ml-2 text-xs text-gray-400">{c.system_type}</span>
                {c.last_status && (
                  <span
                    className={`ml-2 rounded px-1.5 py-0.5 text-xs ${
                      c.last_status === "success"
                        ? "bg-green-100 text-green-700"
                        : "bg-amber-100 text-amber-700"
                    }`}
                  >
                    {c.last_status}
                  </span>
                )}
              </div>
              <div className="flex gap-2">
                <button
                  className="rounded border px-2 py-1 text-xs disabled:opacity-50"
                  disabled={busy === c.id}
                  onClick={() => onTest(c.id)}
                >
                  探活
                </button>
                <button
                  className="rounded border px-2 py-1 text-xs disabled:opacity-50"
                  disabled={busy === c.id}
                  onClick={() => onSync(c.id)}
                >
                  同步
                </button>
                <button
                  className="rounded border border-red-200 px-2 py-1 text-xs text-red-600"
                  onClick={() => onDelete(c.id)}
                >
                  删除
                </button>
              </div>
            </div>
            {c.last_error && <p className="mt-1 text-xs text-red-500">{c.last_error}</p>}
            {runs[c.id]?.length > 0 && (
              <table className="mt-2 w-full text-xs">
                <thead>
                  <tr className="text-left text-gray-400">
                    <th className="pr-2">状态</th>
                    <th className="pr-2">变更数</th>
                    <th>开始时间</th>
                  </tr>
                </thead>
                <tbody>
                  {runs[c.id].map((r) => (
                    <tr key={r.id} className="border-t">
                      <td className="py-1 pr-2">{r.status}</td>
                      <td className="py-1 pr-2">{r.change_count}</td>
                      <td className="py-1">{r.started_at}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        ))}
        {connectors.length === 0 && (
          <p className="text-sm text-gray-400">暂无连接器</p>
        )}
      </div>
    </div>
  );
}
