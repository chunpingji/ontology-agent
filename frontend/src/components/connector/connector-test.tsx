"use client";

import { useMutation } from "@tanstack/react-query";
import { Activity, Loader2 } from "lucide-react";
import { testConnector } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * 连接器探活（US2 · FR-011）——按钮 + 内联结果。testConnector 返回
 * `{ ok, latency_ms, error }`：成功报延迟，失败报明确原因；网络级异常也不崩溃。
 * 空气隙无法直连公网属正常状态，仅真正不可达的内网端点才会 ok=false。
 */
export function ConnectorTest({
  id,
  className,
}: {
  id: string;
  className?: string;
}) {
  const mutation = useMutation({ mutationFn: () => testConnector(id) });
  const result = mutation.data;

  return (
    <div className={cn("flex flex-col gap-1", className)}>
      <Button
        variant="outline"
        size="sm"
        disabled={mutation.isPending}
        onClick={() => mutation.mutate()}
      >
        {mutation.isPending ? (
          <Loader2 className="size-4 animate-spin" />
        ) : (
          <Activity className="size-4" />
        )}
        探活
      </Button>
      {result && (
        <p className={cn("text-xs", result.ok ? "text-success" : "text-destructive")}>
          {result.ok
            ? `连接成功（${result.latency_ms ?? "—"} ms）`
            : `连接失败：${result.error ?? "未知错误"}`}
        </p>
      )}
      {mutation.isError && !result && (
        <p className="text-xs text-destructive">探活失败：{String(mutation.error)}</p>
      )}
    </div>
  );
}
