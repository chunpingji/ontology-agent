"use client";

import { Badge } from "@/components/ui/badge";
import type { SlotCoverageDTO } from "@/lib/api";

interface SlotDetailPanelProps {
  slot: SlotCoverageDTO;
  onClickSourceRef?: (ref: string) => void;
  actionBar?: React.ReactNode;
}

const STATUS_LABELS: Record<string, string> = {
  filled: "已填充",
  inferred: "已推断",
  missing_required: "必填缺失",
  blank_optional: "可选（空）",
  manual: "预留手工填写",
  dismissed: "不适用",
};

const STATUS_VARIANT: Record<string, "default" | "destructive" | "outline" | "secondary"> = {
  filled: "default",
  inferred: "default",
  missing_required: "destructive",
  dismissed: "secondary",
};

export function SlotDetailPanel({ slot, onClickSourceRef, actionBar }: SlotDetailPanelProps) {
  return (
    <div className="space-y-3 text-sm">
      <div className="flex items-center gap-2">
        <span className="font-semibold">{slot.label}</span>
        <Badge variant={STATUS_VARIANT[slot.status] ?? "outline"} className="text-xs">
          {STATUS_LABELS[slot.status] ?? slot.status}
        </Badge>
      </div>

      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
        <dt className="text-muted-foreground">Slot ID</dt>
        <dd className="font-mono">{slot.slot_id}</dd>

        <dt className="text-muted-foreground">来源类型</dt>
        <dd>{slot.source_kind}</dd>

        {slot.value != null && (
          <>
            <dt className="text-muted-foreground">值</dt>
            <dd>{slot.value}</dd>
          </>
        )}

        {slot.source_ref && (
          <>
            <dt className="text-muted-foreground">文档定位</dt>
            <dd>
              <button
                onClick={() => onClickSourceRef?.(slot.source_ref!)}
                className="text-blue-600 hover:underline"
              >
                {slot.source_ref}
              </button>
            </dd>
          </>
        )}

        {slot.rule_key && (
          <>
            <dt className="text-muted-foreground">规则</dt>
            <dd className="font-mono">{slot.rule_key}</dd>
          </>
        )}

        {slot.hazid && (
          <>
            <dt className="text-muted-foreground">HazID</dt>
            <dd>{slot.hazid}</dd>
          </>
        )}

        {slot.note && (
          <>
            <dt className="text-muted-foreground">备注</dt>
            <dd>{slot.note}</dd>
          </>
        )}
      </dl>

      {slot.status === "missing_required" && (
        <div className="rounded border border-orange-200 bg-orange-50 px-3 py-2 text-xs text-orange-800">
          {slot.source_kind === "extraction"
            ? "建议：重新标注文档，或检查文档是否包含该信息"
            : "该维度数据缺失，无法完成风险评估"}
        </div>
      )}

      {actionBar}
    </div>
  );
}
