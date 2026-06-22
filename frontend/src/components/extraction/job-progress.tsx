"use client";

import { useEffect, useState } from "react";
import { subscribeJobProgress, type JobProgressEvent } from "@/lib/api";

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

  useEffect(() => {
    setEvent(null);
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
              <span
                className={`rounded px-3 py-1 ${
                  reached ? "bg-blue-100 text-blue-700" : "bg-gray-100 text-gray-400"
                }`}
              >
                {STAGE_LABELS[s]}
              </span>
              {i < STAGE_ORDER.length - 1 && <span className="text-gray-300">→</span>}
            </span>
          );
        })}
      </div>

      <div className="h-2 w-full overflow-hidden rounded bg-gray-200">
        <div
          className={`h-full transition-all ${failed ? "bg-red-500" : "bg-blue-600"}`}
          style={{ width: `${pct}%` }}
        />
      </div>

      <div className="flex items-center gap-3 text-sm">
        <span className="text-gray-600">
          {event ? `${STAGE_LABELS[event.stage] ?? event.stage}（${pct}%）` : "等待事件…"}
        </span>
        {event?.degraded && (
          <span className="rounded bg-amber-100 px-2 py-0.5 text-xs text-amber-700">
            ⚠ 降级：LLM 不可用，已回退结构化抽取
          </span>
        )}
        {failed && (
          <span className="rounded bg-red-100 px-2 py-0.5 text-xs text-red-700">作业失败</span>
        )}
      </div>
    </div>
  );
}
