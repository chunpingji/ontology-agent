"use client";

import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { ENTITY_PALETTE } from "./entity-mark";
import { cn } from "@/lib/utils";
import type { EntityGroup } from "./entity-stats";

interface EntityPanelProps {
  groups: EntityGroup[];
  selectedType: string | null;
  onSelectType: (label: string | null) => void;
}

export function EntityPanel({ groups, selectedType, onSelectType }: EntityPanelProps) {
  const totalCount = groups.reduce((sum, g) => sum + g.totalCount, 0);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());

  if (totalCount === 0) {
    return (
      <div className="flex h-full items-center justify-center px-4">
        <p className="text-sm text-muted-foreground">未检测到实体</p>
      </div>
    );
  }

  const toggleCollapse = (category: string) => {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(category)) next.delete(category);
      else next.add(category);
      return next;
    });
  };

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between px-4 py-3">
        <h3 className="text-sm font-semibold">实体类型</h3>
        <Badge variant="secondary">{totalCount}</Badge>
      </div>
      <Separator />

      {selectedType && (
        <div className="px-3 pt-2">
          <Button
            variant="ghost"
            size="sm"
            className="h-7 w-full text-xs text-muted-foreground"
            onClick={() => onSelectType(null)}
          >
            清除筛选
          </Button>
        </div>
      )}

      <div className="flex-1 overflow-y-auto py-1">
        {groups.map((group) => {
          const isCollapsed = collapsed.has(group.category);
          const catColor = ENTITY_PALETTE[group.categoryColorIndex];

          return (
            <div key={group.category} className="mb-1">
              {/* Category header */}
              <button
                onClick={() => toggleCollapse(group.category)}
                className="flex w-full items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-muted-foreground hover:text-foreground transition-colors"
              >
                {isCollapsed
                  ? <ChevronRight className="h-3.5 w-3.5 shrink-0" />
                  : <ChevronDown className="h-3.5 w-3.5 shrink-0" />}
                <span
                  className="h-2.5 w-2.5 shrink-0 rounded-sm"
                  style={{ background: catColor.border }}
                />
                <span className="flex-1 truncate text-left">{group.category}</span>
                <Badge variant="outline" className="ml-auto h-5 text-[10px] tabular-nums">
                  {group.totalCount}
                </Badge>
              </button>

              {/* Entity type items */}
              {!isCollapsed && (
                <div className="space-y-0.5 px-2">
                  {group.items.map((stat) => {
                    const color = ENTITY_PALETTE[stat.colorIndex];
                    const isActive = selectedType === stat.label;

                    return (
                      <button
                        key={stat.label}
                        onClick={() => onSelectType(isActive ? null : stat.label)}
                        className={cn(
                          "flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-sm transition-colors",
                          "hover:bg-accent/60",
                          isActive && "ring-2 ring-primary/60 bg-accent",
                        )}
                      >
                        <span
                          className="h-3 w-3 shrink-0 rounded-sm"
                          style={{ background: color.border }}
                        />
                        <span className="flex-1 truncate text-left">{stat.label}</span>
                        <Badge variant="secondary" className="ml-auto tabular-nums">
                          {stat.count}
                        </Badge>
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
