"use client";

import { useState, type ReactNode } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import {
  createDatabaseConnector,
  createDocRepoConnector,
  createFileConnector,
  createRestApiConnector,
  deleteConnector,
  type Connector,
  type DocRepoAccessMode,
  type FileFormat,
  type RestAuthScheme,
  type RestPaginationStyle,
} from "@/lib/api";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

type SystemType = "rest_api" | "database" | "doc_repo" | "file";

interface FormState {
  systemType: SystemType;
  name: string;
  pollInterval: string;
  // rest_api
  baseUrl: string;
  endpoint: string;
  authScheme: RestAuthScheme;
  tokenEnv: string;
  apiKeyEnv: string;
  apiKeyHeader: string;
  paginationStyle: RestPaginationStyle;
  cursorPath: string;
  pageSize: string;
  // database
  dsnEnv: string;
  schema: string;
  includeTables: string;
  // doc_repo
  accessMode: DocRepoAccessMode;
  docBaseUrl: string;
  tokenRef: string;
  apiKeyRef: string;
  // file
  filePath: string;
  fileFormat: FileFormat | "_auto";
  sheetName: string;
}

const asString = (v: unknown): string => (typeof v === "string" ? v : "");

/**
 * 从既有连接器（编辑态）还原表单初值。connection_config 仅含**环境变量名**与
 * 非机密配置（凭据明文从不入库），故回填是安全的。
 */
function deriveForm(initial?: Connector | null): FormState {
  const cfg = (initial?.connection_config ?? {}) as Record<string, unknown>;
  const auth = (cfg.auth ?? {}) as Record<string, unknown>;
  const pagination = (cfg.pagination ?? {}) as Record<string, unknown>;

  const stRaw = (initial?.system_type ?? "rest_api").toLowerCase();
  const systemType: SystemType =
    stRaw === "database" || stRaw === "doc_repo" || stRaw === "file" ? stRaw : "rest_api";

  const schemeRaw = asString(auth.scheme);
  const authScheme: RestAuthScheme = schemeRaw === "api_key" ? "api_key" : "bearer";

  const styleRaw = asString(pagination.style);
  const paginationStyle: RestPaginationStyle =
    styleRaw === "offset" || styleRaw === "page" ? styleRaw : "cursor";

  const fmtRaw = asString(cfg.format);
  const fileFormat: FileFormat | "_auto" =
    fmtRaw === "excel" || fmtRaw === "word" || fmtRaw === "pdf" ? fmtRaw : "_auto";

  const accessRaw = asString(cfg.access_mode);
  const accessMode: DocRepoAccessMode =
    accessRaw === "http" || accessRaw === "upload" ? accessRaw : "inline";

  const tables = Array.isArray(cfg.include_tables)
    ? (cfg.include_tables as unknown[]).filter((t): t is string => typeof t === "string").join(", ")
    : "";

  return {
    systemType,
    name: initial?.name ?? "",
    pollInterval: String(initial?.poll_interval_seconds ?? 2),
    baseUrl: asString(cfg.base_url),
    endpoint: asString(cfg.endpoint),
    authScheme,
    tokenEnv: asString(auth.token_env),
    apiKeyEnv: asString(auth.api_key_env),
    apiKeyHeader: asString(auth.header),
    paginationStyle,
    cursorPath: asString(pagination.cursor_path) || "$.next",
    pageSize: String(typeof pagination.page_size === "number" ? pagination.page_size : 200),
    dsnEnv: asString(cfg.dsn_env),
    schema: asString(cfg.schema),
    includeTables: tables,
    accessMode,
    docBaseUrl: asString(cfg.base_url),
    tokenRef: asString(cfg.token_ref),
    apiKeyRef: asString(cfg.api_key_ref),
    filePath: asString(cfg.file_path),
    fileFormat,
    sheetName: asString(cfg.sheet_name),
  };
}

/**
 * 新建 / 编辑连接器（US2）。按数据源类型切换字段集，凭据字段一律只收
 * **环境变量名**（绝不收明文密钥）。无独立更新端点——「编辑」以「新建替代 +
 * 删除旧连接器」实现（先建成功再删，失败不误删）。成功后失效 ["connectors"]。
 */
export function ConnectorForm({
  trigger,
  initial,
}: {
  trigger: ReactNode;
  initial?: Connector | null;
}) {
  const isEdit = !!initial;
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState<FormState>(() => deriveForm(initial));

  const set = <K extends keyof FormState,>(key: K, value: FormState[K]) =>
    setForm((prev) => ({ ...prev, [key]: value }));

  const validationError = ((): string | null => {
    if (!form.name.trim()) return "请填写连接器名称。";
    if (form.systemType === "rest_api" && !form.baseUrl.trim()) return "请填写内网 base_url。";
    if (form.systemType === "database" && !form.dsnEnv.trim())
      return "请填写 DSN 环境变量名。";
    if (form.systemType === "doc_repo" && form.accessMode === "http" && !form.docBaseUrl.trim())
      return "HTTP 接入模式需填写端点 base_url。";
    if (form.systemType === "file" && !form.filePath.trim()) return "请填写文件路径。";
    return null;
  })();

  const mutation = useMutation({
    mutationFn: async () => {
      const poll = Number(form.pollInterval) || 2;
      if (form.systemType === "rest_api") {
        await createRestApiConnector({
          name: form.name,
          baseUrl: form.baseUrl,
          endpoint: form.endpoint,
          authScheme: form.authScheme,
          tokenEnv: form.authScheme === "bearer" ? form.tokenEnv || undefined : undefined,
          apiKeyEnv: form.authScheme === "api_key" ? form.apiKeyEnv || undefined : undefined,
          apiKeyHeader:
            form.authScheme === "api_key" ? form.apiKeyHeader || undefined : undefined,
          paginationStyle: form.paginationStyle,
          cursorPath: form.cursorPath || undefined,
          pageSize: Number(form.pageSize) || 200,
          pollIntervalSeconds: poll,
        });
      } else if (form.systemType === "database") {
        const includeTables = form.includeTables.trim()
          ? form.includeTables.split(",").map((t) => t.trim()).filter(Boolean)
          : undefined;
        await createDatabaseConnector({
          name: form.name,
          dsnEnv: form.dsnEnv,
          schema: form.schema || undefined,
          includeTables,
          pollIntervalSeconds: poll,
        });
      } else if (form.systemType === "doc_repo") {
        await createDocRepoConnector({
          name: form.name,
          accessMode: form.accessMode,
          baseUrl: form.accessMode === "http" ? form.docBaseUrl || undefined : undefined,
          tokenRef: form.accessMode === "http" ? form.tokenRef || undefined : undefined,
          apiKeyRef: form.accessMode === "http" ? form.apiKeyRef || undefined : undefined,
          pollIntervalSeconds: poll,
        });
      } else {
        await createFileConnector({
          name: form.name,
          filePath: form.filePath,
          format: form.fileFormat === "_auto" ? undefined : form.fileFormat,
          sheetName: form.sheetName || undefined,
          pollIntervalSeconds: poll,
        });
      }
      // 「编辑」= 新建替代 + 删除旧（新建成功后才删，避免失败误删）。
      if (initial) await deleteConnector(initial.id);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["connectors"] });
      setOpen(false);
      if (!isEdit) setForm(deriveForm(null));
    },
  });

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>{isEdit ? "编辑连接器" : "新建连接器"}</DialogTitle>
          <DialogDescription>
            按数据源类型填写连接参数；凭据仅填环境变量名（不存明文），base_url 须为内网地址。
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="conn-name">名称</Label>
              <Input
                id="conn-name"
                value={form.name}
                onChange={(e) => set("name", e.target.value)}
                placeholder="内网 REST 源"
              />
            </div>
            <div className="space-y-1.5">
              <Label>数据源类型</Label>
              <Select
                value={form.systemType}
                onValueChange={(v) => set("systemType", v as SystemType)}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="rest_api">REST 接口</SelectItem>
                  <SelectItem value="database">数据库</SelectItem>
                  <SelectItem value="doc_repo">研发文档库</SelectItem>
                  <SelectItem value="file">文件源</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>

          {form.systemType === "rest_api" && (
            <div className="space-y-3 rounded-md border border-dashed border-border p-3">
              <div className="grid gap-3 sm:grid-cols-2">
                <div className="space-y-1.5">
                  <Label htmlFor="rest-base">base_url（内网）</Label>
                  <Input
                    id="rest-base"
                    value={form.baseUrl}
                    onChange={(e) => set("baseUrl", e.target.value)}
                    placeholder="http://drug-registry.intranet.local"
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="rest-ep">endpoint</Label>
                  <Input
                    id="rest-ep"
                    value={form.endpoint}
                    onChange={(e) => set("endpoint", e.target.value)}
                    placeholder="/api/drugs"
                  />
                </div>
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                <div className="space-y-1.5">
                  <Label>认证方式</Label>
                  <Select
                    value={form.authScheme}
                    onValueChange={(v) => set("authScheme", v as RestAuthScheme)}
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="bearer">Bearer Token</SelectItem>
                      <SelectItem value="api_key">API Key</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                {form.authScheme === "bearer" ? (
                  <div className="space-y-1.5">
                    <Label htmlFor="rest-token">token 环境变量名（不存明文）</Label>
                    <Input
                      id="rest-token"
                      value={form.tokenEnv}
                      onChange={(e) => set("tokenEnv", e.target.value)}
                      placeholder="DRUG_API_TOKEN"
                    />
                  </div>
                ) : (
                  <>
                    <div className="space-y-1.5">
                      <Label htmlFor="rest-key">api_key 环境变量名（不存明文）</Label>
                      <Input
                        id="rest-key"
                        value={form.apiKeyEnv}
                        onChange={(e) => set("apiKeyEnv", e.target.value)}
                        placeholder="DRUG_API_KEY"
                      />
                    </div>
                    <div className="space-y-1.5">
                      <Label htmlFor="rest-header">请求头名（可选）</Label>
                      <Input
                        id="rest-header"
                        value={form.apiKeyHeader}
                        onChange={(e) => set("apiKeyHeader", e.target.value)}
                        placeholder="X-API-Key"
                      />
                    </div>
                  </>
                )}
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                <div className="space-y-1.5">
                  <Label>分页方式</Label>
                  <Select
                    value={form.paginationStyle}
                    onValueChange={(v) => set("paginationStyle", v as RestPaginationStyle)}
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="cursor">游标（cursor）</SelectItem>
                      <SelectItem value="offset">偏移（offset）</SelectItem>
                      <SelectItem value="page">页码（page）</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                {form.paginationStyle === "cursor" && (
                  <div className="space-y-1.5">
                    <Label htmlFor="rest-cursor">cursor_path</Label>
                    <Input
                      id="rest-cursor"
                      value={form.cursorPath}
                      onChange={(e) => set("cursorPath", e.target.value)}
                      placeholder="$.next"
                    />
                  </div>
                )}
                <div className="space-y-1.5">
                  <Label htmlFor="rest-size">每页条数</Label>
                  <Input
                    id="rest-size"
                    type="number"
                    value={form.pageSize}
                    onChange={(e) => set("pageSize", e.target.value)}
                  />
                </div>
              </div>
            </div>
          )}

          {form.systemType === "database" && (
            <div className="space-y-3 rounded-md border border-dashed border-border p-3">
              <div className="space-y-1.5">
                <Label htmlFor="db-dsn">DSN 环境变量名（不存明文）</Label>
                <Input
                  id="db-dsn"
                  value={form.dsnEnv}
                  onChange={(e) => set("dsnEnv", e.target.value)}
                  placeholder="SOURCE_DB_DSN"
                />
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                <div className="space-y-1.5">
                  <Label htmlFor="db-schema">schema（可选）</Label>
                  <Input
                    id="db-schema"
                    value={form.schema}
                    onChange={(e) => set("schema", e.target.value)}
                    placeholder="public"
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="db-tables">仅指定表（逗号分隔，可选）</Label>
                  <Input
                    id="db-tables"
                    value={form.includeTables}
                    onChange={(e) => set("includeTables", e.target.value)}
                    placeholder="drug_product, equipment"
                  />
                </div>
              </div>
            </div>
          )}

          {form.systemType === "doc_repo" && (
            <div className="space-y-3 rounded-md border border-dashed border-border p-3">
              <div className="space-y-1.5">
                <Label>接入模式</Label>
                <Select
                  value={form.accessMode}
                  onValueChange={(v) => set("accessMode", v as DocRepoAccessMode)}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="inline">内联骨架（inline）</SelectItem>
                    <SelectItem value="upload">上传（upload）</SelectItem>
                    <SelectItem value="http">HTTP 端点（EDMS/eTMF）</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              {form.accessMode === "http" && (
                <div className="grid gap-3 sm:grid-cols-2">
                  <div className="space-y-1.5 sm:col-span-2">
                    <Label htmlFor="doc-base">端点 base_url（内网）</Label>
                    <Input
                      id="doc-base"
                      value={form.docBaseUrl}
                      onChange={(e) => set("docBaseUrl", e.target.value)}
                      placeholder="http://edms.intranet.local"
                    />
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor="doc-token">token 环境变量名（不存明文）</Label>
                    <Input
                      id="doc-token"
                      value={form.tokenRef}
                      onChange={(e) => set("tokenRef", e.target.value)}
                      placeholder="EDMS_TOKEN"
                    />
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor="doc-key">api_key 环境变量名（可选）</Label>
                    <Input
                      id="doc-key"
                      value={form.apiKeyRef}
                      onChange={(e) => set("apiKeyRef", e.target.value)}
                      placeholder="EDMS_API_KEY"
                    />
                  </div>
                </div>
              )}
              {form.accessMode !== "http" && (
                <p className="text-xs text-muted-foreground">
                  {form.accessMode === "inline"
                    ? "内联模式：由 webhook / 程序推送归一化变更骨架，无需端点配置。"
                    : "上传模式：文档以上传信封提交，无需端点配置。"}
                </p>
              )}
            </div>
          )}

          {form.systemType === "file" && (
            <div className="space-y-3 rounded-md border border-dashed border-border p-3">
              <div className="space-y-1.5">
                <Label htmlFor="file-path">文件路径或 glob 模式</Label>
                <Input
                  id="file-path"
                  value={form.filePath}
                  onChange={(e) => set("filePath", e.target.value)}
                  placeholder="/data/imports/设备清单.xlsx"
                />
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                <div className="space-y-1.5">
                  <Label>格式（留空自动推断）</Label>
                  <Select
                    value={form.fileFormat}
                    onValueChange={(v) => set("fileFormat", v as FileFormat | "_auto")}
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="_auto">自动推断</SelectItem>
                      <SelectItem value="excel">Excel</SelectItem>
                      <SelectItem value="word">Word</SelectItem>
                      <SelectItem value="pdf">PDF</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                {(form.fileFormat === "excel" || form.fileFormat === "_auto") && (
                  <div className="space-y-1.5">
                    <Label htmlFor="file-sheet">工作表名（可选）</Label>
                    <Input
                      id="file-sheet"
                      value={form.sheetName}
                      onChange={(e) => set("sheetName", e.target.value)}
                      placeholder="Sheet1"
                    />
                  </div>
                )}
              </div>
            </div>
          )}

          <div className="space-y-1.5">
            <Label htmlFor="conn-poll">轮询间隔（秒）</Label>
            <Input
              id="conn-poll"
              type="number"
              className="w-32"
              value={form.pollInterval}
              onChange={(e) => set("pollInterval", e.target.value)}
            />
          </div>

          <p className="text-xs text-muted-foreground">
            凭据字段仅接受<strong>环境变量名</strong>（如 DRUG_API_TOKEN /
            SOURCE_DB_DSN）；明文密钥经 env 注入、绝不入库。
          </p>

          {validationError && (
            <Alert variant="destructive">
              <AlertDescription>{validationError}</AlertDescription>
            </Alert>
          )}
          {mutation.isError && (
            <Alert variant="destructive">
              <AlertDescription>保存失败：{String(mutation.error)}</AlertDescription>
            </Alert>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)} disabled={mutation.isPending}>
            取消
          </Button>
          <Button
            onClick={() => mutation.mutate()}
            disabled={mutation.isPending || !!validationError}
          >
            {mutation.isPending && <Loader2 className="size-4 animate-spin" />}
            {isEdit ? "保存（新建并替换）" : "创建"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
