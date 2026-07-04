"use client";

import { useQuery } from "@tanstack/react-query";

import { getMappingHealth } from "@/lib/api";
import { cn } from "@/lib/utils";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";

export const MAPPING_HEALTH_KEY = ["ontology-mapping-health"] as const;

type Bucket = "ok" | "unmapped" | "drift" | "orphan";

const BUCKET_META: Record<Bucket, { label: string; dot: string; text: string }> = {
  ok: { label: "健全", dot: "bg-success", text: "text-success" },
  unmapped: { label: "未映射", dot: "bg-destructive", text: "text-destructive" },
  drift: { label: "漂移", dot: "bg-warning", text: "text-warning" },
  orphan: { label: "孤立", dot: "bg-primary", text: "text-primary" },
};

const BUCKET_ORDER: Bucket[] = ["ok", "unmapped", "drift", "orphan"];

/**
 * 映射覆盖度（US3 · FR-013–015）。基于全局 {@link getMappingHealth} 的四个类级
 * 分桶（ok / 未映射 / 漂移 / 孤立）：Progress 展示「已健全类 / 全部有映射记录的类」
 * 的全局覆盖率，并高亮当前所选类落入的分桶。数字含义在标签中明确标注为「全局」。
 */
export function Coverage({ classIri }: { classIri: string | null }) {
  const healthQuery = useQuery({
    queryKey: MAPPING_HEALTH_KEY,
    queryFn: getMappingHealth,
  });

  if (healthQuery.isLoading) {
    return (
      <div className="space-y-2">
        <Skeleton className="h-4 w-40" />
        <Skeleton className="h-2 w-full" />
        <Skeleton className="h-4 w-2/3" />
      </div>
    );
  }

  if (healthQuery.isError || !healthQuery.data) {
    return (
      <Alert variant="destructive">
        <AlertTitle>无法加载覆盖度</AlertTitle>
        <AlertDescription>映射健康度读取失败，请稍后重试。</AlertDescription>
      </Alert>
    );
  }

  const health = healthQuery.data;
  const counts: Record<Bucket, number> = {
    ok: health.ok.length,
    unmapped: health.unmapped.length,
    drift: health.drift.length,
    orphan: health.orphan.length,
  };
  const total = counts.ok + counts.unmapped + counts.drift + counts.orphan;
  const pct = total > 0 ? Math.round((counts.ok / total) * 100) : 0;

  const currentBucket: Bucket | null = classIri
    ? (BUCKET_ORDER.find((b) => health[b].includes(classIri)) ?? null)
    : null;

  return (
    <div className="space-y-3">
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-sm font-medium text-foreground">类映射覆盖度（全局）</span>
        <span className="text-sm text-muted-foreground">
          {counts.ok} / {total} 类健全（{pct}%）
        </span>
      </div>

      <Progress value={pct} indicatorClassName="bg-success" />

      <div className="flex flex-wrap gap-x-4 gap-y-1.5">
        {BUCKET_ORDER.map((b) => (
          <span key={b} className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <span className={cn("size-2 rounded-full", BUCKET_META[b].dot)} />
            {BUCKET_META[b].label}
            <span className="font-medium text-foreground">{counts[b]}</span>
          </span>
        ))}
      </div>

      {classIri && (
        <p className="text-xs text-muted-foreground">
          当前类状态：
          {currentBucket ? (
            <span className={cn("font-medium", BUCKET_META[currentBucket].text)}>
              {BUCKET_META[currentBucket].label}
            </span>
          ) : (
            <span className="font-medium text-muted-foreground">无映射记录</span>
          )}
        </p>
      )}
    </div>
  );
}
