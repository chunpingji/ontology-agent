"use client";

import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import type { ApprovalTask, ApprovalUrgency } from "@/lib/api";

const URGENCY_META: Record<ApprovalUrgency, { label: string; cls: string }> = {
  high: { label: "高", cls: "border-destructive/30 bg-destructive/10 text-destructive" },
  medium: { label: "中", cls: "border-warning/30 bg-warning/10 text-warning" },
  low: { label: "低", cls: "border-border bg-muted text-muted-foreground" },
};

export function TaskCard({
  task,
  active,
  onSelect,
}: {
  task: ApprovalTask;
  active: boolean;
  onSelect: () => void;
}) {
  const u = URGENCY_META[task.urgency];
  const initial = task.submitter.slice(0, 1);

  return (
    <li>
      <button
        type="button"
        onClick={onSelect}
        className={cn(
          "flex w-full gap-3 px-4 py-3 text-left transition-colors",
          active ? "bg-accent" : "hover:bg-muted/60",
        )}
      >
        {task.unread && (
          <span className="mt-2 size-2 shrink-0 rounded-full bg-primary" />
        )}
        {!task.unread && <span className="mt-2 size-2 shrink-0" />}

        <div
          className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-full bg-muted text-sm font-medium text-muted-foreground"
        >
          {initial}
        </div>

        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium text-foreground">{task.title}</p>
          <div className="mt-1 flex items-center gap-1.5">
            <span className="truncate text-xs text-muted-foreground">{task.type}</span>
            <Badge variant="outline" className={cn("shrink-0 px-1.5 py-0 text-[10px]", u.cls)}>
              {u.label}
            </Badge>
            <span className="shrink-0 text-xs text-muted-foreground">{task.submitter}</span>
          </div>
        </div>

        <span className="shrink-0 pt-0.5 text-xs text-muted-foreground">{task.submittedLabel}</span>
      </button>
    </li>
  );
}
