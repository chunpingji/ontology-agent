"use client";

import { useEffect, useState } from "react";
import { subscribeJobProgress, type JobProgressEvent } from "@/lib/api";
import { Badge } from "@/components/ui/badge";

const STAGE_LABELS: Record<string, string> = {
  parsing: "解析",
  extracting: "抽取",
  aligning: "对齐",
  reviewing: "待审核",
  done: "完成",
  failed: "失败",
};

const STAGE_ORDER = ["parsing", "extracting", "aligning", "reviewing"];

/**
 * 作业进度（T019, US1）：`EventSource` 订阅 `/jobs/{id}/progress`，
 * 渲染进度条与阶段；命中降级（LLM 不可用）时显示降级标记（FR-002/007）。
 */
export function JobProgress({
  jobId,
  onDone,
}: {
  jobId: string;
  onDone?: () => void;
}) {
  const [event, setEvent] = useState<JobProgressEvent | null>(null);

  // jobId 改变时由父级以 key 重挂载（event 经 useState 初值复位为 null）；
  // effect 只负责订阅，避免在 effect 体内同步 setState。
  useEffect(() => {
    const unsub = subscribeJobProgress(jobId, (e) => {
      setEvent(e);
      if (e.stage === "reviewing" || e.stage === "done" || e.stage === "failed") {
        onDone?.();
      }
    });
    return unsub;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId]);

  const pct = event?.pct ?? 0;
  const failed = event?.stage === "failed";

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 text-sm">
        {STAGE_ORDER.map((s, i) => {
          const reached = event ? STAGE_ORDER.indexOf(event.stage) >= i || event.pct >= 100 : false;
          return (
            <span key={s} className="flex items-center gap-2">
              <Badge variant={reached ? "default" : "secondary"}>
                {STAGE_LABELS[s]}
              </Badge>
              {i < STAGE_ORDER.length - 1 && <span className="text-muted-foreground">→</span>}
            </span>
          );
        })}
      </div>

      <div className="h-2 w-full overflow-hidden rounded bg-muted">
        <div
          className={`h-full transition-all ${failed ? "bg-destructive" : "bg-primary"}`}
          style={{ width: `${pct}%` }}
        />
      </div>

      <div className="flex items-center gap-3 text-sm">
        <span className="text-muted-foreground">
          {event ? `${STAGE_LABELS[event.stage] ?? event.stage}（${pct}%）` : "等待事件…"}
        </span>
        {event?.degraded && (
          <Badge variant="warning">
            ⚠ 降级：LLM 不可用，已回退结构化抽取
          </Badge>
        )}
        {failed && (
          <Badge variant="destructive">作业失败</Badge>
        )}
      </div>
    </div>
  );
}
