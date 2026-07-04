"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Boxes,
  Database,
  FileText,
  Files,
  Globe,
  Loader2,
  Pencil,
  Trash2,
  type LucideIcon,
} from "lucide-react";
import {
  connectorStatus,
  deleteConnector,
  groupConnectorsByType,
  listConnectorRuns,
  type Connector,
} from "@/lib/api";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { StatusIndicator } from "@/components/connector/status-indicator";
import { ConnectorTest } from "@/components/connector/connector-test";
import { ConnectorForm } from "@/components/connector/connector-form";

/** system_type → 中文标签 + 分组图标（FR-010）。 */
const TYPE_META: Record<string, { label: string; icon: LucideIcon }> = {
  rest_api: { label: "REST 接口", icon: Globe },
  database: { label: "数据库", icon: Database },
  doc_repo: { label: "研发文档库", icon: FileText },
  file: { label: "文件源", icon: Files },
};

const typeMeta = (type: string) => TYPE_META[type] ?? { label: type, icon: Boxes };

const asString = (v: unknown): string => (typeof v === "string" ? v : "");

/**
 * 端点摘要（脱敏）——仅展示 base_url / DSN 环境变量名 / 文件路径 / 接入模式，
 * 绝不展示任何密钥明文（凭据本就只以环境变量名入库）。
 */
function endpointSummary(c: Connector): string {
  const cfg = (c.connection_config ?? {}) as Record<string, unknown>;
  switch (c.system_type) {
    case "rest_api": {
      const base = asString(cfg.base_url);
      return base ? `${base}${asString(cfg.endpoint)}` : "—";
    }
    case "database": {
      const dsn = asString(cfg.dsn_env);
      return dsn ? `DSN 环境变量：${dsn}` : "—";
    }
    case "doc_repo": {
      const mode = asString(cfg.access_mode) || "inline";
      const base = asString(cfg.base_url);
      return mode === "http" && base ? base : `接入模式：${mode}`;
    }
    case "file":
      return asString(cfg.file_path) || "—";
    default:
      return "—";
  }
}

/** 最近同步（从 /runs 最近一次 finished_at 派生；懒加载，避免阻塞列表）。 */
function LastSync({ id }: { id: string }) {
  const { data } = useQuery({
    queryKey: ["connector-runs", id],
    queryFn: () => listConnectorRuns(id),
    staleTime: 30_000,
  });
  const runs = data?.runs ?? [];
  const latest = runs.find((r) => r.finished_at) ?? runs[0];
  if (!latest) {
    return <span className="text-xs text-muted-foreground">最近同步：—</span>;
  }
  return (
    <span className="text-xs text-muted-foreground">
      最近同步：{latest.finished_at ?? latest.started_at ?? "—"}（{latest.status}）
    </span>
  );
}

/** 删除连接器（带二次确认 Dialog；成功后失效 ["connectors"]）。 */
function DeleteConnectorButton({ connector }: { connector: Connector }) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const mutation = useMutation({
    mutationFn: () => deleteConnector(connector.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["connectors"] });
      setOpen(false);
    },
  });

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="outline" size="sm" className="text-destructive">
          <Trash2 className="size-4" />
          删除
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>删除连接器</DialogTitle>
          <DialogDescription>
            确认删除「{connector.name}」？此操作不可撤销。
          </DialogDescription>
        </DialogHeader>
        {mutation.isError && (
          <Alert variant="destructive">
            <AlertDescription>删除失败：{String(mutation.error)}</AlertDescription>
          </Alert>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)} disabled={mutation.isPending}>
            取消
          </Button>
          <Button
            variant="destructive"
            onClick={() => mutation.mutate()}
            disabled={mutation.isPending}
          >
            {mutation.isPending && <Loader2 className="size-4 animate-spin" />}
            删除
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/**
 * 连接器列表——按 system_type 分组渲染（FR-010）。每行展示名称、类型、语义状态
 * 指示器、脱敏端点摘要、最近同步/错误。写操作（探活/编辑/删除）由 canEdit 门控。
 */
export function ConnectorList({
  connectors,
  canEdit,
}: {
  connectors: Connector[];
  canEdit: boolean;
}) {
  const groups = groupConnectorsByType(connectors);

  return (
    <div className="space-y-8">
      {groups.map((group) => {
        const meta = typeMeta(group.type);
        const Icon = meta.icon;
        return (
          <section key={group.type} className="space-y-3">
            <div className="flex items-center gap-2">
              <Icon className="size-4 text-muted-foreground" />
              <h2 className="text-sm font-semibold text-foreground">{meta.label}</h2>
              <Badge variant="secondary">{group.connectors.length}</Badge>
            </div>
            <div className="space-y-3">
              {group.connectors.map((c) => (
                <Card key={c.id}>
                  <CardContent className="flex flex-col gap-3 p-4 sm:flex-row sm:items-start sm:justify-between">
                    <div className="min-w-0 space-y-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-medium text-foreground">{c.name}</span>
                        <Badge variant="outline">{meta.label}</Badge>
                        <StatusIndicator status={connectorStatus(c)} />
                      </div>
                      <p
                        className="truncate text-xs text-muted-foreground"
                        title={endpointSummary(c)}
                      >
                        {endpointSummary(c)}
                      </p>
                      <LastSync id={c.id} />
                      {c.last_error && (
                        <p className="text-xs text-destructive">最近错误：{c.last_error}</p>
                      )}
                    </div>
                    {canEdit && (
                      <div className="flex shrink-0 flex-wrap items-start gap-2">
                        <ConnectorTest id={c.id} />
                        <ConnectorForm
                          initial={c}
                          trigger={
                            <Button variant="outline" size="sm">
                              <Pencil className="size-4" />
                              编辑
                            </Button>
                          }
                        />
                        <DeleteConnectorButton connector={c} />
                      </div>
                    )}
                  </CardContent>
                </Card>
              ))}
            </div>
          </section>
        );
      })}
    </div>
  );
}
