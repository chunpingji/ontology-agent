"use client";

import { Button } from "@/components/ui/button";
import type { SlotCoverageDTO } from "@/lib/api";

interface SlotActionBarProps {
  slot: SlotCoverageDTO;
  onDismiss?: (slotId: string) => void;
  onUndismiss?: (slotId: string) => void;
  dismissing?: boolean;
  onRerun?: () => void;
  rerunning?: boolean;
  extraActions?: React.ReactNode;
}

export function SlotActionBar({
  slot,
  onDismiss,
  onUndismiss,
  dismissing,
  onRerun,
  rerunning,
  extraActions,
}: SlotActionBarProps) {
  const showRerun =
    slot.status === "missing_required" && slot.source_kind === "extraction" && onRerun;

  if (
    slot.status !== "missing_required" &&
    slot.status !== "dismissed" &&
    !showRerun
  ) {
    return extraActions ? <div className="mt-2">{extraActions}</div> : null;
  }

  return (
    <div className="mt-2 flex items-center gap-2">
      {slot.status === "missing_required" && (
        <Button
          size="sm"
          variant="outline"
          className="h-7 text-xs"
          disabled={dismissing}
          onClick={() => onDismiss?.(slot.slot_id)}
        >
          {dismissing ? "处理中..." : "标记为不适用"}
        </Button>
      )}
      {slot.status === "dismissed" && (
        <Button
          size="sm"
          variant="outline"
          className="h-7 text-xs"
          disabled={dismissing}
          onClick={() => onUndismiss?.(slot.slot_id)}
        >
          {dismissing ? "处理中..." : "撤销标记"}
        </Button>
      )}
      {showRerun && (
        <Button
          size="sm"
          variant="outline"
          className="h-7 text-xs"
          disabled={rerunning}
          onClick={onRerun}
        >
          {rerunning ? "标注中..." : "重新标注"}
        </Button>
      )}
      {extraActions}
    </div>
  );
}
