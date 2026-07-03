"use client";

import { useCallback, useEffect, useState } from "react";
import {
  createConnector,
  createDatabaseConnector,
  createFileConnector,
  createRestApiConnector,
  deleteConnector,
  listConnectorRuns,
  listConnectors,
  syncConnector,
  testConnector,
  type Connector,
  type FileFormat,
  type MaterializationRun,
  type RestAuthScheme,
  type RestPaginationStyle,
} from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

/** 通用 REST/JSON 源默认配置（内网示例；凭据仅填环境变量名）。 */
const REST_DEFAULTS = {
  baseUrl: "http://drug-registry.intranet.local",
  endpoint: "/api/drugs",
  authScheme: "bearer" as RestAuthScheme,
  tokenEnv: "DRUG_API_TOKEN",
  apiKeyEnv: "",
  apiKeyHeader: "",
  paginationStyle: "cursor" as RestPaginationStyle,
  cursorPath: "$.next",
  pageSize: 200,
};

/** 数据库源默认配置（凭据仅填环境变量名）。 */
const DB_DEFAULTS = {
  dsnEnv: "SOURCE_DB_DSN",
  schema: "",
  includeTables: "",
};

/** 文件源默认配置。 */
const FILE_DEFAULTS = {
  filePath: "",
  format: "" as FileFormat | "",
  sheetName: "",
};

/** 连接器类型选项。 */
const SYSTEM_TYPES = ["rest_api", "database", "file", "APS", "MES", "ERP", "LIMS", "CTMS"];

/** 连接器管理：CRUD + 探活 + 同步触发 + 物化运行列表。 */
export function ConnectorManager() {
  const [connectors, setConnectors] = useState<Connector[]>([]);
  const [runs, setRuns] = useState<Record<string, MaterializationRun[]>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [form, setForm] = useState({ name: "", system_type: "rest_api", poll_interval_seconds: 2 });
  const [rest, setRest] = useState(REST_DEFAULTS);
  const [db, setDb] = useState(DB_DEFAULTS);
  const [file, setFile] = useState(FILE_DEFAULTS);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    listConnectors().then(setConnectors).catch((e) => setError(String(e)));
  }, []);
  useEffect(() => refresh(), [refresh]);

  const isRest = form.system_type.toLowerCase() === "rest_api";
  const isDb = form.system_type.toLowerCase() === "database";
  const isFile = form.system_type.toLowerCase() === "file";

  const onCreate = async () => {
    setError(null);
    try {
      if (isRest) {
        await createRestApiConnector({
          name: form.name || "内网 REST 源",
          baseUrl: rest.baseUrl,
          endpoint: rest.endpoint,
          authScheme: rest.authScheme,
          tokenEnv: rest.authScheme === "bearer" ? rest.tokenEnv : undefined,
          apiKeyEnv: rest.authScheme === "api_key" ? rest.apiKeyEnv : undefined,
          apiKeyHeader:
            rest.authScheme === "api_key" ? rest.apiKeyHeader || undefined : undefined,
          paginationStyle: rest.paginationStyle,
          cursorPath: rest.cursorPath,
          pageSize: Number(rest.pageSize) || 200,
          pollIntervalSeconds: Number(form.poll_interval_seconds) || 2,
        });
      } else if (isDb) {
        const tables = db.includeTables.trim()
          ? db.includeTables.split(",").map((t) => t.trim()).filter(Boolean)
          : undefined;
        await createDatabaseConnector({
          name: form.name || "数据库源",
          dsnEnv: db.dsnEnv,
          schema: db.schema || undefined,
          includeTables: tables,
          pollIntervalSeconds: Number(form.poll_interval_seconds) || 2,
        });
      } else if (isFile) {
        await createFileConnector({
          name: form.name || "文件源",
          filePath: file.filePath,
          format: (file.format as FileFormat) || undefined,
          sheetName: file.sheetName || undefined,
          pollIntervalSeconds: Number(form.poll_interval_seconds) || 2,
        });
      } else {
        await createConnector({
          name: form.name || "新连接器",
          system_type: form.system_type,
          ingest_mode: "poll",
          poll_interval_seconds: Number(form.poll_interval_seconds) || 2,
          connection_config: { source_mode: "inline", inline_changes: [] },
        });
      }
      setForm({ name: "", system_type: "rest_api", poll_interval_seconds: 2 });
      setRest(REST_DEFAULTS);
      setDb(DB_DEFAULTS);
      setFile(FILE_DEFAULTS);
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
          <div className="flex flex-wrap items-center gap-2">
            <Input
              className="w-auto text-sm"
              placeholder="名称"
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
            />
            <Select
              value={form.system_type}
              onValueChange={(v) => setForm({ ...form, system_type: v })}
            >
              <SelectTrigger className="w-32 text-sm">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {SYSTEM_TYPES.map((t) => (
                  <SelectItem key={t} value={t}>
                    {t}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
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

          {isRest && (
            <div className="mt-3 space-y-3 rounded-md border border-dashed p-3">
              <p className="text-xs font-medium text-muted-foreground">
                REST/JSON 源配置（声明驱动 API 抽取 · US2）
              </p>
              <div className="flex flex-wrap gap-3">
                <div className="space-y-1">
                  <Label className="text-xs text-muted-foreground">base_url（内网）</Label>
                  <Input
                    className="w-64 text-sm"
                    value={rest.baseUrl}
                    onChange={(e) => setRest({ ...rest, baseUrl: e.target.value })}
                    placeholder="http://drug-registry.intranet.local"
                  />
                </div>
                <div className="space-y-1">
                  <Label className="text-xs text-muted-foreground">endpoint</Label>
                  <Input
                    className="w-44 text-sm"
                    value={rest.endpoint}
                    onChange={(e) => setRest({ ...rest, endpoint: e.target.value })}
                    placeholder="/api/drugs"
                  />
                </div>
              </div>

              <div className="flex flex-wrap items-end gap-3">
                <div className="space-y-1">
                  <Label className="text-xs text-muted-foreground">认证方式</Label>
                  <Select
                    value={rest.authScheme}
                    onValueChange={(v) => setRest({ ...rest, authScheme: v as RestAuthScheme })}
                  >
                    <SelectTrigger className="w-32 text-sm">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="bearer">bearer</SelectItem>
                      <SelectItem value="api_key">api_key</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                {rest.authScheme === "bearer" ? (
                  <div className="space-y-1">
                    <Label className="text-xs text-muted-foreground">token 环境变量名</Label>
                    <Input
                      className="w-48 text-sm"
                      value={rest.tokenEnv}
                      onChange={(e) => setRest({ ...rest, tokenEnv: e.target.value })}
                      placeholder="DRUG_API_TOKEN"
                    />
                  </div>
                ) : (
                  <>
                    <div className="space-y-1">
                      <Label className="text-xs text-muted-foreground">api_key 环境变量名</Label>
                      <Input
                        className="w-48 text-sm"
                        value={rest.apiKeyEnv}
                        onChange={(e) => setRest({ ...rest, apiKeyEnv: e.target.value })}
                        placeholder="DRUG_API_KEY"
                      />
                    </div>
                    <div className="space-y-1">
                      <Label className="text-xs text-muted-foreground">请求头名（可选）</Label>
                      <Input
                        className="w-36 text-sm"
                        value={rest.apiKeyHeader}
                        onChange={(e) => setRest({ ...rest, apiKeyHeader: e.target.value })}
                        placeholder="X-API-Key"
                      />
                    </div>
                  </>
                )}
              </div>

              <div className="flex flex-wrap items-end gap-3">
                <div className="space-y-1">
                  <Label className="text-xs text-muted-foreground">分页方式</Label>
                  <Select
                    value={rest.paginationStyle}
                    onValueChange={(v) =>
                      setRest({ ...rest, paginationStyle: v as RestPaginationStyle })
                    }
                  >
                    <SelectTrigger className="w-28 text-sm">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="cursor">cursor</SelectItem>
                      <SelectItem value="offset">offset</SelectItem>
                      <SelectItem value="page">page</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                {rest.paginationStyle === "cursor" && (
                  <div className="space-y-1">
                    <Label className="text-xs text-muted-foreground">cursor_path</Label>
                    <Input
                      className="w-36 text-sm"
                      value={rest.cursorPath}
                      onChange={(e) => setRest({ ...rest, cursorPath: e.target.value })}
                      placeholder="$.next"
                    />
                  </div>
                )}
                <div className="space-y-1">
                  <Label className="text-xs text-muted-foreground">每页条数</Label>
                  <Input
                    type="number"
                    className="w-24 text-sm"
                    value={rest.pageSize}
                    onChange={(e) => setRest({ ...rest, pageSize: Number(e.target.value) })}
                  />
                </div>
              </div>

              <p className="text-xs text-warning-foreground">
                ⚠ 仅填写凭据的<strong>环境变量名</strong>；明文 token/密钥经 env
                注入、绝不入库（FR-006）。base_url 须为<strong>内网地址</strong>，公网/云端将被拒绝（FR-022）。
              </p>
            </div>
          )}

          {isDb && (
            <div className="mt-3 space-y-3 rounded-md border border-dashed p-3">
              <p className="text-xs font-medium text-muted-foreground">
                数据库源配置
              </p>
              <div className="flex flex-wrap gap-3">
                <div className="space-y-1">
                  <Label className="text-xs text-muted-foreground">DSN 环境变量名</Label>
                  <Input
                    className="w-64 text-sm"
                    value={db.dsnEnv}
                    onChange={(e) => setDb({ ...db, dsnEnv: e.target.value })}
                    placeholder="SOURCE_DB_DSN"
                  />
                </div>
                <div className="space-y-1">
                  <Label className="text-xs text-muted-foreground">schema（可选）</Label>
                  <Input
                    className="w-36 text-sm"
                    value={db.schema}
                    onChange={(e) => setDb({ ...db, schema: e.target.value })}
                    placeholder="public"
                  />
                </div>
                <div className="space-y-1">
                  <Label className="text-xs text-muted-foreground">仅指定表（逗号分隔，可选）</Label>
                  <Input
                    className="w-64 text-sm"
                    value={db.includeTables}
                    onChange={(e) => setDb({ ...db, includeTables: e.target.value })}
                    placeholder="drug_product, equipment"
                  />
                </div>
              </div>
              <p className="text-xs text-warning-foreground">
                ⚠ 仅填写 DSN 的<strong>环境变量名</strong>（如 SOURCE_DB_DSN）；明文连接串绝不入库（FR-006）。
              </p>
            </div>
          )}

          {isFile && (
            <div className="mt-3 space-y-3 rounded-md border border-dashed p-3">
              <p className="text-xs font-medium text-muted-foreground">
                文件源配置（Excel / Word / PDF）
              </p>
              <div className="flex flex-wrap gap-3">
                <div className="space-y-1">
                  <Label className="text-xs text-muted-foreground">文件路径或 glob 模式</Label>
                  <Input
                    className="w-80 text-sm"
                    value={file.filePath}
                    onChange={(e) => setFile({ ...file, filePath: e.target.value })}
                    placeholder="/data/imports/设备清单.xlsx"
                  />
                </div>
                <div className="space-y-1">
                  <Label className="text-xs text-muted-foreground">格式（留空自动推断）</Label>
                  <Select
                    value={file.format || "_auto"}
                    onValueChange={(v) =>
                      setFile({ ...file, format: v === "_auto" ? "" : (v as FileFormat) })
                    }
                  >
                    <SelectTrigger className="w-28 text-sm">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="_auto">自动</SelectItem>
                      <SelectItem value="excel">Excel</SelectItem>
                      <SelectItem value="word">Word</SelectItem>
                      <SelectItem value="pdf">PDF</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                {(file.format === "excel" || file.format === "") && (
                  <div className="space-y-1">
                    <Label className="text-xs text-muted-foreground">工作表名（可选）</Label>
                    <Input
                      className="w-36 text-sm"
                      value={file.sheetName}
                      onChange={(e) => setFile({ ...file, sheetName: e.target.value })}
                      placeholder="Sheet1"
                    />
                  </div>
                )}
              </div>
              <p className="text-xs text-muted-foreground">
                支持绝对路径（/data/imports/report.docx）或 glob 模式（/data/imports/*.xlsx）。
              </p>
            </div>
          )}
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
