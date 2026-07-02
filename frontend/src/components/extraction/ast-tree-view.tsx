"use client";

import { useState, useCallback, useEffect, useRef } from "react";
import { Badge } from "@/components/ui/badge";
import { ChevronRight, ChevronDown } from "lucide-react";
import type { ASTCoverageDTO, SlotCoverageDTO, SectionCoverageDTO, GroupCoverageDTO } from "@/lib/api";

const STATUS_CONFIG: Record<string, { color: string; label: string; className?: string }> = {
  filled: { color: "bg-green-500", label: "已填充" },
  inferred: { color: "bg-blue-500", label: "已推断" },
  missing_required: { color: "bg-red-500", label: "缺失" },
  blank_optional: { color: "bg-gray-400", label: "可选空" },
  manual: { color: "bg-yellow-500", label: "手工" },
  dismissed: { color: "bg-gray-300", label: "不适用", className: "line-through text-muted-foreground" },
};

interface ASTTreeViewProps {
  coverage: ASTCoverageDTO;
  selectedSlotId?: string | null;
  onSelectSlot?: (slot: SlotCoverageDTO) => void;
  scrollToSlotId?: string | null;
}

export function ASTTreeView({ coverage, selectedSlotId, onSelectSlot, scrollToSlotId }: ASTTreeViewProps) {
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(new Set());
  const slotRefs = useRef<Map<string, HTMLDivElement>>(new Map());

  const toggleGroup = useCallback((groupId: string) => {
    setExpandedGroups((prev) => {
      const next = new Set(prev);
      if (next.has(groupId)) next.delete(groupId);
      else next.add(groupId);
      return next;
    });
  }, []);

  useEffect(() => {
    if (!scrollToSlotId) return;
    for (const section of coverage.sections) {
      for (const group of section.groups) {
        for (const slot of group.slots) {
          if (slot.slot_id === scrollToSlotId) {
            setExpandedGroups((prev) => new Set([...prev, group.group_id]));
            requestAnimationFrame(() => {
              const el = slotRefs.current.get(scrollToSlotId);
              el?.scrollIntoView({ behavior: "smooth", block: "center" });
            });
            return;
          }
        }
      }
    }
  }, [scrollToSlotId, coverage]);

  return (
    <div className="space-y-1">
      {coverage.sections.map((section) => (
        <SectionNode key={section.section_id} section={section}>
          {section.groups.map((group) => (
            <GroupNode
              key={group.group_id}
              group={group}
              expanded={expandedGroups.has(group.group_id)}
              onToggle={() => toggleGroup(group.group_id)}
            >
              {group.slots.map((slot) => (
                <SlotNode
                  key={slot.slot_id}
                  slot={slot}
                  selected={slot.slot_id === selectedSlotId}
                  onClick={() => onSelectSlot?.(slot)}
                  ref={(el) => {
                    if (el) slotRefs.current.set(slot.slot_id, el);
                    else slotRefs.current.delete(slot.slot_id);
                  }}
                />
              ))}
            </GroupNode>
          ))}
        </SectionNode>
      ))}
    </div>
  );
}

function SectionNode({ section, children }: { section: SectionCoverageDTO; children: React.ReactNode }) {
  return (
    <div className="mb-2">
      <div className="text-sm font-semibold text-foreground px-2 py-1 bg-muted/50 rounded">
        {section.title}
      </div>
      <div className="ml-2 border-l pl-2">{children}</div>
    </div>
  );
}

function GroupNode({
  group,
  expanded,
  onToggle,
  children,
}: {
  group: GroupCoverageDTO;
  expanded: boolean;
  onToggle: () => void;
  children: React.ReactNode;
}) {
  const slotCount = group.slots.length;
  const filledCount = group.slots.filter(
    (s) => s.status === "filled" || s.status === "inferred",
  ).length;

  return (
    <div className={`my-1 ${group.is_dynamic ? "bg-violet-50/50 rounded" : ""}`}>
      <button
        onClick={onToggle}
        className="flex w-full items-center gap-1 rounded px-2 py-1 text-left text-sm hover:bg-muted/60"
      >
        {expanded ? (
          <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
        ) : (
          <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
        )}
        <span className="flex-1 font-medium">{group.title}</span>
        {group.is_dynamic && (
          <Badge variant="outline" className="h-5 shrink-0 text-[10px] font-normal border-violet-300 text-violet-600">
            本体属性
          </Badge>
        )}
        <span className="text-xs text-muted-foreground tabular-nums">
          {filledCount}/{slotCount}
        </span>
      </button>
      {expanded && <div className="ml-5 space-y-0.5">{children}</div>}
    </div>
  );
}

import { forwardRef } from "react";

const SlotNode = forwardRef<
  HTMLDivElement,
  { slot: SlotCoverageDTO; selected: boolean; onClick: () => void }
>(function SlotNode({ slot, selected, onClick }, ref) {
  const cfg = STATUS_CONFIG[slot.status] ?? STATUS_CONFIG.blank_optional;

  return (
    <div
      ref={ref}
      onClick={onClick}
      className={`flex cursor-pointer items-center gap-2 rounded px-2 py-1 text-sm transition-colors ${
        selected ? "bg-blue-50 ring-1 ring-blue-300" : "hover:bg-muted/40"
      }`}
    >
      <span className={`inline-block h-2 w-2 shrink-0 rounded-full ${cfg.color}`} />
      <span className={`flex-1 truncate ${cfg.className ?? ""}`}>{slot.label}</span>
      <Badge variant="outline" className="h-5 shrink-0 text-[10px] font-normal">
        {cfg.label}
      </Badge>
    </div>
  );
});
