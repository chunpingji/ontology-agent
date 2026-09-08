"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link2 } from "lucide-react";

import {
  deletePropertyBinding,
  getPropertyBindings,
  VersionConflictError,
  type PropertyBinding,
} from "@/lib/api";
import { cn } from "@/lib/utils";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { MAPPING_HEALTH_KEY } from "@/components/data-mapping/coverage";

export const propertyBindingsKey = (mappingId: string) =>
  ["property-bindings", mappingId] as const;

const CONFLICT_MESSAGE = "版本冲突，请刷新后重试";

const localName = (iri: string): string => iri.split("/").flatMap((part) => part.split("#")).pop() || iri;

type StatusTone = "success" | "warning" | "destructive" | "secondary";

const STATUS_TONE: Record<string, StatusTone> = {
  ok: "success",
  active: "success",
  drift: "warning",
  unmapped: "destructive",
  orphan: "warning",
  error: "destructive",
};

function StatusBadge({ status }: { status: string }) {
  const tone = STATUS_TONE[status.toLowerCase()] ?? "secondary";
  const variant = tone === "secondary" ? "secondary" : tone;
  return (
    <Badge variant={variant} className="font-normal">
      {status}
    </Badge>
  );
}

/**
 * 属性绑定表（US3 · FR-014）：展示某类源绑定（TBoxMapping）下的属性绑定——
 * 属性 / 种类 / 源字段 / 转换 / 标识·标签开关（只读）/ 状态。senior_analyst 可
 * 逐行编辑、删除；删除携 version 乐观并发，409 → 冲突提示。
 */
export function BindingTable({
  mappingId,
  canEdit,
  onEdit,
}: {
  mappingId: string;
  canEdit: boolean;
  onEdit: (binding: PropertyBinding) => void;
}) {
  const queryClient = useQueryClient();
  const [actionError, setActionError] = useState<string | null>(null);

  const bindingsQuery = useQuery({
    queryKey: propertyBindingsKey(mappingId),
    queryFn: () => getPropertyBindings(mappingId),
  });

  const deleteMutation = useMutation({
    mutationFn: (binding: PropertyBinding) =>
      deletePropertyBinding(binding.id, binding.version),
    onMutate: () => setActionError(null),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: propertyBindingsKey(mappingId) });
      queryClient.invalidateQueries({ queryKey: MAPPING_HEALTH_KEY });
    },
    onError: (err) => {
      setActionError(err instanceof VersionConflictError ? CONFLICT_MESSAGE : String(err));
    },
  });

  const handleDelete = (binding: PropertyBinding) => {
    if (
      typeof window !== "undefined" &&
      !window.confirm(`删除属性绑定「${localName(binding.property_iri)}」？`)
    ) {
      return;
    }
    deleteMutation.mutate(binding);
  };

  if (bindingsQuery.isLoading) {
    return (
      <div className="space-y-2">
        <Skeleton className="h-9 w-full" />
        <Skeleton className="h-9 w-full" />
        <Skeleton className="h-9 w-full" />
      </div>
    );
  }

  if (bindingsQuery.isError) {
    return (
      <Alert variant="destructive">
        <AlertTitle>无法加载属性绑定</AlertTitle>
        <AlertDescription>属性绑定读取失败，请稍后重试。</AlertDescription>
      </Alert>
    );
  }

  const bindings = bindingsQuery.data ?? [];

  if (bindings.length === 0) {
    return (
      <EmptyState
        icon={<Link2 />}
        title="无属性绑定"
        description={
          canEdit
            ? "该源绑定尚未声明任何「本体属性 → 源字段」绑定。"
            : "该源绑定尚未声明任何属性绑定。"
        }
      />
    );
  }

  return (
    <div className="space-y-3">
      {actionError && (
        <Alert variant="destructive">
          <AlertTitle>操作失败</AlertTitle>
          <AlertDescription>{actionError}</AlertDescription>
        </Alert>
      )}

      <TooltipProvider delayDuration={200}>
        <div className="rounded-md border border-border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>属性</TableHead>
                <TableHead>种类</TableHead>
                <TableHead>源字段</TableHead>
                <TableHead>转换</TableHead>
                <TableHead>标识 / 标签</TableHead>
                <TableHead>状态</TableHead>
                {canEdit && <TableHead className="text-right">操作</TableHead>}
              </TableRow>
            </TableHeader>
            <TableBody>
              {bindings.map((b) => {
                const hasConfig = b.transform_config && Object.keys(b.transform_config).length > 0;
                return (
                  <TableRow key={b.id}>
                    <TableCell className="max-w-[220px]">
                      <div className="truncate text-sm font-medium text-foreground">
                        {localName(b.property_iri)}
                      </div>
                      <div className="truncate font-mono text-[11px] text-muted-foreground">
                        {b.property_iri}
                      </div>
                    </TableCell>
                    <TableCell>
                      <Badge variant="outline" className="font-normal">
                        {b.property_kind}
                      </Badge>
                    </TableCell>
                    <TableCell className="font-mono text-xs text-muted-foreground">
                      {b.source_path || "—"}
                    </TableCell>
                    <TableCell>
                      <span className="flex items-center gap-1.5">
                        <Badge variant="secondary" className="font-normal">
                          {b.transform_type || "none"}
                        </Badge>
                        {hasConfig && (
                          <Tooltip>
                            <TooltipTrigger asChild>
                              <span className="cursor-help text-[11px] text-muted-foreground underline decoration-dotted">
                                规则
                              </span>
                            </TooltipTrigger>
                            <TooltipContent className="max-w-xs">
                              <pre className="whitespace-pre-wrap break-all font-mono text-[11px]">
                                {JSON.stringify(b.transform_config, null, 2)}
                              </pre>
                            </TooltipContent>
                          </Tooltip>
                        )}
                      </span>
                    </TableCell>
                    <TableCell>
                      <div className="flex items-center gap-3">
                        <span className="flex items-center gap-1.5">
                          <Switch checked={b.is_identifier} disabled aria-label="是否标识符" />
                          <span
                            className={cn(
                              "text-xs",
                              b.is_identifier ? "text-foreground" : "text-muted-foreground",
                            )}
                          >
                            id
                          </span>
                        </span>
                        <span className="flex items-center gap-1.5">
                          <Switch checked={b.is_label} disabled aria-label="是否标签" />
                          <span
                            className={cn(
                              "text-xs",
                              b.is_label ? "text-foreground" : "text-muted-foreground",
                            )}
                          >
                            label
                          </span>
                        </span>
                      </div>
                    </TableCell>
                    <TableCell>
                      <StatusBadge status={b.status} />
                    </TableCell>
                    {canEdit && (
                      <TableCell className="text-right">
                        <div className="flex justify-end gap-1">
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() => onEdit(b)}
                          >
                            编辑
                          </Button>
                          <Button
                            variant="outline"
                            size="sm"
                            className="text-destructive hover:bg-destructive/10"
                            disabled={deleteMutation.isPending}
                            onClick={() => handleDelete(b)}
                          >
                            删除
                          </Button>
                        </div>
                      </TableCell>
                    )}
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </div>
      </TooltipProvider>
    </div>
  );
}
