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
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

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
      <Card className="p-4">
        <CardContent className="p-0">
          <h3 className="mb-3 font-semibold">新增连接器</h3>
          {error && <p className="mb-2 text-sm text-destructive">{error}</p>}
          <div className="flex flex-wrap gap-2">
            <Input
              className="w-auto text-sm"
              placeholder="名称"
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
            />
            <Input
              className="w-24 text-sm"
              placeholder="类型"
              value={form.system_type}
              onChange={(e) => setForm({ ...form, system_type: e.target.value })}
            />
            <Input
              type="number"
              className="w-28 text-sm"
              placeholder="轮询(秒)"
              value={form.poll_interval_seconds}
              onChange={(e) =>
                setForm({ ...form, poll_interval_seconds: Number(e.target.value) })
              }
            />
            <Button size="sm" onClick={onCreate}>
              创建
            </Button>
          </div>
        </CardContent>
      </Card>

      <div className="space-y-3">
        {connectors.map((c) => (
          <Card key={c.id} className="p-4">
            <CardContent className="p-0">
              <div className="flex items-center justify-between">
                <div>
                  <span className="font-semibold">{c.name}</span>
                  <span className="ml-2 text-xs text-muted-foreground">{c.system_type}</span>
                  {c.last_status && (
                    <Badge
                      variant={c.last_status === "success" ? "success" : "warning"}
                      className="ml-2"
                    >
                      {c.last_status}
                    </Badge>
                  )}
                </div>
                <div className="flex gap-2">
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={busy === c.id}
                    onClick={() => onTest(c.id)}
                  >
                    探活
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={busy === c.id}
                    onClick={() => onSync(c.id)}
                  >
                    同步
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    className="border-destructive/40 text-destructive"
                    onClick={() => onDelete(c.id)}
                  >
                    删除
                  </Button>
                </div>
              </div>
              {c.last_error && (
                <p className="mt-1 text-xs text-destructive">{c.last_error}</p>
              )}
              {runs[c.id]?.length > 0 && (
                <Table className="mt-2 text-xs">
                  <TableHeader>
                    <TableRow className="text-left text-muted-foreground">
                      <TableHead className="pr-2">状态</TableHead>
                      <TableHead className="pr-2">变更数</TableHead>
                      <TableHead>开始时间</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {runs[c.id].map((r) => (
                      <TableRow key={r.id} className="border-t">
                        <TableCell className="py-1 pr-2">{r.status}</TableCell>
                        <TableCell className="py-1 pr-2">{r.change_count}</TableCell>
                        <TableCell className="py-1">{r.started_at}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </CardContent>
          </Card>
        ))}
        {connectors.length === 0 && (
          <p className="text-sm text-muted-foreground">暂无连接器</p>
        )}
      </div>
    </div>
  );
}
