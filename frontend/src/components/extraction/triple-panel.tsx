"use client";

import { useMemo, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { ENTITY_PALETTE, entityColorIndex } from "./entity-mark";
import type { EntityTriple } from "@/lib/api";

interface TriplePanelProps {
  triples: EntityTriple[];
}

interface ClassGroup {
  classIri: string;
  classLabel: string;
  colorIndex: number;
  entities: EntityTriple[];
}

export function TriplePanel({ triples }: TriplePanelProps) {
  const [expandedEntities, setExpandedEntities] = useState<Set<string>>(
    new Set(),
  );

  const groups = useMemo(() => {
    const map = new Map<string, EntityTriple[]>();
    for (const t of triples) {
      const list = map.get(t.entity_class_iri);
      if (list) list.push(t);
      else map.set(t.entity_class_iri, [t]);
    }
    const result: ClassGroup[] = [];
    for (const [classIri, entities] of map) {
      result.push({
        classIri,
        classLabel: entities[0].entity_class_label,
        colorIndex: entityColorIndex(entities[0].entity_class_label),
        entities,
      });
    }
    return result.sort((a, b) => b.entities.length - a.entities.length);
  }, [triples]);

  const withProps = triples.filter((t) => t.properties.length > 0).length;

  if (triples.length === 0) {
    return (
      <div className="flex h-full items-center justify-center px-4">
        <p className="text-sm text-muted-foreground">未提取到属性三元组</p>
      </div>
    );
  }

  const toggleEntity = (key: string) => {
    setExpandedEntities((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between px-4 py-3">
        <h3 className="text-sm font-semibold">属性三元组</h3>
        <Badge variant="secondary">{withProps} / {triples.length}</Badge>
      </div>
      <Separator />

      <div className="flex-1 overflow-y-auto py-1">
        {groups.map((group) => {
          const color = ENTITY_PALETTE[group.colorIndex];

          return (
            <div key={group.classIri} className="mb-2">
              {/* Class header */}
              <div className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-muted-foreground">
                <span
                  className="h-2.5 w-2.5 shrink-0 rounded-sm"
                  style={{ background: color.border }}
                />
                <span className="flex-1 truncate">{group.classLabel}</span>
                <Badge
                  variant="outline"
                  className="ml-auto h-5 text-[10px] tabular-nums"
                >
                  {group.entities.length}
                </Badge>
              </div>

              {/* Entity items */}
              <div className="space-y-0.5 px-2">
                {group.entities.map((entity, idx) => {
                  const key = `${entity.entity_class_iri}:${entity.segment_index}:${entity.span_start}:${idx}`;
                  const isExpanded = expandedEntities.has(key);
                  const hasProp = entity.properties.length > 0;

                  return (
                    <div key={key}>
                      <button
                        onClick={() => hasProp && toggleEntity(key)}
                        className="flex w-full items-center gap-1.5 rounded-md px-2.5 py-1.5 text-sm hover:bg-accent/60 transition-colors"
                      >
                        {hasProp ? (
                          isExpanded ? (
                            <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                          ) : (
                            <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                          )
                        ) : (
                          <span className="h-3.5 w-3.5 shrink-0" />
                        )}
                        <span className="flex-1 truncate text-left">
                          {entity.entity_text}
                        </span>
                        {hasProp && (
                          <Badge
                            variant="secondary"
                            className="ml-auto tabular-nums text-[10px]"
                          >
                            {entity.properties.length}
                          </Badge>
                        )}
                      </button>

                      {isExpanded && (
                        <div className="ml-7 mb-1 space-y-0.5 border-l-2 border-muted pl-3">
                          {entity.properties.map((prop) => (
                            <div
                              key={prop.iri}
                              className="flex items-baseline gap-1 text-xs"
                            >
                              <span className="shrink-0 text-muted-foreground">
                                {prop.label}:
                              </span>
                              <span className="break-all">
                                {typeof prop.value === "string"
                                  ? prop.value
                                  : JSON.stringify(prop.value)}
                              </span>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
