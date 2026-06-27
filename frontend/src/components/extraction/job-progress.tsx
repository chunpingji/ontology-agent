"use client";

import { useEffect, useState } from "react";
import {
  subscribeJobProgress,
  pauseAnnotation,
  resumeAnnotation,
  rerunAnnotation,
  type JobProgressEvent,
} from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

const STAGE_LABELS: Record<string, string> = {
  parsing: "解析",
  annotating: "标注",
  extracting: "抽取",
  aligning: "对齐",
  reviewing: "待审核",
  done: "完成",
  failed: "失败",
};

const STAGE_ORDER = ["parsing", "annotating", "extracting", "aligning", "reviewing"];

const ANNOTATION_STAGES = ["gliner", "typing", "triples"] as const;
const ANNOTATION_LABELS: Record<string, string> = {
  gliner: "GLiNER 定界",
  typing: "嵌入归类",
  triples: "属性三元组",
};

function annotationStageIndex(stage?: string): number {
  if (!stage) return -1;
  if (stage === "done") return ANNOTATION_STAGES.length;
  return ANNOTATION_STAGES.indexOf(stage as (typeof ANNOTATION_STAGES)[number]);
}

export function JobProgress({
  jobId,
  onDone,
}: {
  jobId: string;
  onDone?: () => void;
}) {
  const [event, setEvent] = useState<JobProgressEvent | null>(null);

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
  const isAnnotating = event?.stage === "annotating";
  const isPaused = event?.annotation_stage === "paused";
  const annoIdx = annotationStageIndex(event?.annotation_stage);

  return (
    <div className="space-y-3">
      {/* Main pipeline stages */}
      <div className="flex items-center gap-2 text-sm">
        {STAGE_ORDER.map((s, i) => {
          const stageIdx = event ? STAGE_ORDER.indexOf(event.stage) : -1;
          const reached = stageIdx >= i || (event?.pct ?? 0) >= 100;
          return (
            <span key={s} className="flex items-center gap-2">
              <Badge variant={reached ? "default" : "secondary"}>
                {STAGE_LABELS[s]}
              </Badge>
              {i < STAGE_ORDER.length - 1 && (
                <span className="text-muted-foreground">→</span>
              )}
            </span>
          );
        })}
      </div>

      {/* Annotation sub-stages (expanded when annotating) */}
      {(isAnnotating || isPaused) && (
        <div className="ml-4 rounded border border-border bg-muted/30 px-3 py-2">
          <div className="flex items-center gap-3 text-xs">
            {ANNOTATION_STAGES.map((sub, i) => {
              const done = annoIdx > i;
              const active = annoIdx === i && !isPaused;
              return (
                <span key={sub} className="flex items-center gap-1.5">
                  <span
                    className={`inline-block h-2 w-2 rounded-full ${
                      done
                        ? "bg-primary"
                        : active
                          ? "bg-primary animate-pulse"
                          : "border border-muted-foreground bg-transparent"
                    }`}
                  />
                  <span
                    className={
                      done || active
                        ? "text-foreground font-medium"
                        : "text-muted-foreground"
                    }
                  >
                    {ANNOTATION_LABELS[sub]}
                  </span>
                  {i < ANNOTATION_STAGES.length - 1 && (
                    <span className="text-muted-foreground mx-1">→</span>
                  )}
                </span>
              );
            })}
          </div>

          {/* Control buttons */}
          <div className="flex gap-2 mt-2">
            {!isPaused && (
              <Button
                size="sm"
                variant="outline"
                className="h-7 text-xs"
                onClick={() => pauseAnnotation(jobId)}
              >
                暂停标注
              </Button>
            )}
            {isPaused && (
              <>
                <Button
                  size="sm"
                  className="h-7 text-xs"
                  onClick={() => resumeAnnotation(jobId)}
                >
                  恢复标注
                </Button>
                <Button
                  size="sm"
                  variant="destructive"
                  className="h-7 text-xs"
                  onClick={() => rerunAnnotation(jobId)}
                >
                  重新运行
                </Button>
              </>
            )}
          </div>
        </div>
      )}

      {/* Progress bar */}
      <div className="h-2 w-full overflow-hidden rounded bg-muted">
        <div
          className={`h-full transition-all ${
            failed
              ? "bg-destructive"
              : isPaused
                ? "bg-yellow-500"
                : "bg-primary"
          }`}
          style={{ width: `${pct}%` }}
        />
      </div>

      {/* Status text */}
      <div className="flex items-center gap-3 text-sm">
        <span className="text-muted-foreground">
          {isPaused
            ? "标注已暂停"
            : event
              ? `${STAGE_LABELS[event.stage] ?? event.stage}（${pct}%）`
              : "等待事件…"}
        </span>
        {event?.degraded && (
          <Badge variant="warning">⚠ 降级：LLM 不可用，已回退结构化抽取</Badge>
        )}
        {failed && <Badge variant="destructive">作业失败</Badge>}
      </div>
    </div>
  );
}
