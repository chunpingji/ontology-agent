"use client";

import { useMemo, useState } from "react";
import { ChevronDown, ChevronRight, FileText } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { ENTITY_PALETTE, entityColorIndex } from "./entity-mark";
import type {
  DocClassification,
  Relationship,
  SubRelationship,
} from "@/lib/api";

interface RelationPanelProps {
  docClass?: DocClassification | null;
  relationships?: Relationship[];
  selectedSourceRef?: string | null;
  onSelectSourceRef?: (ref: string | null) => void;
}

interface PredicateGroup {
  predicate: string;
  items: SubRelationship[];
}

// 按谓词标签归组（顶层 + 递归子关系共用）。
function groupByPredicate(items: SubRelationship[]): PredicateGroup[] {
  const map = new Map<string, SubRelationship[]>();
  for (const it of items) {
    const key = it.predicate_label || it.predicate_iri;
    const list = map.get(key);
    if (list) list.push(it);
    else map.set(key, [it]);
  }
  return [...map.entries()].map(([predicate, groupItems]) => ({
    predicate,
    items: groupItems,
  }));
}

// 单个对象端点：可展开看数据属性 + 递归子关系（合成路线→步骤→设备/中间体）。
function EndpointRow({
  node,
  depth,
  rowKey,
  selectedSourceRef,
  onSelectSourceRef,
}: {
  node: SubRelationship;
  depth: number;
  rowKey: string;
  selectedSourceRef?: string | null;
  onSelectSourceRef?: (ref: string | null) => void;
}) {
  const hasDetail =
    node.object_data_properties.length > 0 || node.sub_relationships.length > 0;
  // 顶层（depth 0）默认展开，露出数据属性；更深层折叠以免信息过载。
  const [open, setOpen] = useState(depth === 0);
  const color = ENTITY_PALETTE[entityColorIndex(node.object_class_label)];
  const isSelected = !!(node.source_ref && node.source_ref === selectedSourceRef);

  function handleClick() {
    if (hasDetail) setOpen((v) => !v);
    if (node.source_ref && onSelectSourceRef) {
      onSelectSourceRef(isSelected ? null : node.source_ref);
    }
  }

  return (
    <div>
      <button
        onClick={handleClick}
        className={`flex w-full items-start gap-1.5 rounded-md px-2 py-1.5 text-left text-sm transition-colors hover:bg-accent/60${isSelected ? " bg-blue-50 border-l-2 border-blue-500 dark:bg-blue-950/30" : ""}`}
      >
        {hasDetail ? (
          open ? (
            <ChevronDown className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground" />
          ) : (
            <ChevronRight className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground" />
          )
        ) : (
          <span className="h-3.5 w-3.5 shrink-0" />
        )}
        <span
          className="mt-1 h-2 w-2 shrink-0 rounded-sm"
          style={{ background: color.border }}
        />
        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-baseline gap-x-1.5 gap-y-0.5">
            <span className="break-all font-medium">{node.object_text}</span>
            <span className="text-[11px] text-muted-foreground">
              {node.object_class_label}
            </span>
          </span>
          {node.source_ref && (
            <span className="mt-0.5 block truncate text-[10px] text-muted-foreground/80">
              {node.source_ref}
            </span>
          )}
        </span>
        {node.sub_relationships.length > 0 && (
          <Badge variant="outline" className="ml-auto h-5 shrink-0 text-[10px] tabular-nums">
            {node.sub_relationships.length}
          </Badge>
        )}
      </button>

      {open && hasDetail && (
        <div className="ml-5 mb-1 space-y-1 border-l-2 border-muted pl-3">
          {/* 数据属性 */}
          {node.object_data_properties.length > 0 && (
            <div className="space-y-0.5 py-0.5">
              {node.object_data_properties.map((dp, i) => (
                <div
                  key={`${dp.label}:${i}`}
                  className="flex items-baseline gap-1 text-xs"
                >
                  <span
                    className="shrink-0 text-muted-foreground"
                    title={dp.iri ?? "未匹配本体数据属性（原文）"}
                  >
                    {dp.label}
                    {!dp.iri && <span className="text-muted-foreground/50">*</span>}:
                  </span>
                  <span className="break-all">{dp.value}</span>
                </div>
              ))}
            </div>
          )}

          {/* 递归子关系（按谓词分组） */}
          {node.sub_relationships.length > 0 &&
            groupByPredicate(node.sub_relationships).map((g) => (
              <div key={g.predicate} className="pt-0.5">
                <div className="px-1 py-0.5 text-[11px] font-medium text-muted-foreground/90">
                  ↳ {g.predicate}
                  <span className="ml-1 text-muted-foreground/60">
                    ({g.items.length})
                  </span>
                </div>
                {g.items.map((sub, i) => (
                  <EndpointRow
                    key={`${rowKey}:${g.predicate}:${i}`}
                    node={sub}
                    depth={depth + 1}
                    rowKey={`${rowKey}:${g.predicate}:${i}`}
                    selectedSourceRef={selectedSourceRef}
                    onSelectSourceRef={onSelectSourceRef}
                  />
                ))}
              </div>
            ))}
        </div>
      )}
    </div>
  );
}

export function RelationPanel({ docClass, relationships, selectedSourceRef, onSelectSourceRef }: RelationPanelProps) {
  const rels = relationships ?? [];
  const groups = useMemo(() => groupByPredicate(relationships ?? []), [relationships]);

  return (
    <div className="flex h-full flex-col">
      {/* 文档分类徽章 + 可解释信号 */}
      <div className="px-4 py-3">
        {docClass ? (
          <div className="space-y-1.5">
            <div className="flex items-center gap-2">
              <FileText className="h-4 w-4 shrink-0 text-muted-foreground" />
              <span className="text-sm font-semibold">{docClass.label}</span>
              <Badge variant="secondary" className="h-5 text-[10px] tabular-nums">
                置信 {docClass.score}
              </Badge>
            </div>
            {docClass.signals.length > 0 && (
              <div className="flex flex-wrap gap-1">
                {docClass.signals.map((sig) => (
                  <span
                    key={sig}
                    className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground"
                  >
                    {sig}
                  </span>
                ))}
              </div>
            )}
          </div>
        ) : (
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <FileText className="h-4 w-4 shrink-0" />
            未识别文档类型
          </div>
        )}
      </div>
      <Separator />

      {rels.length === 0 ? (
        <div className="flex flex-1 items-center justify-center px-4">
          <p className="text-sm text-muted-foreground">
            {docClass ? "未抽取到关系" : "仅 Word 文档支持关系抽取"}
          </p>
        </div>
      ) : (
        <div className="flex-1 overflow-y-auto py-1">
          {groups.map((group) => {
            const color =
              ENTITY_PALETTE[entityColorIndex(group.items[0].object_class_label)];
            return (
              <div key={group.predicate} className="mb-2">
                {/* 谓词组头 */}
                <div className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-muted-foreground">
                  <span
                    className="h-2.5 w-2.5 shrink-0 rounded-sm"
                    style={{ background: color.border }}
                  />
                  <span className="flex-1 truncate">{group.predicate}</span>
                  <Badge
                    variant="outline"
                    className="ml-auto h-5 text-[10px] tabular-nums"
                  >
                    {group.items.length}
                  </Badge>
                </div>
                {/* 端点 */}
                <div className="space-y-0.5 px-2">
                  {group.items.map((edge, i) => (
                    <EndpointRow
                      key={`${group.predicate}:${i}`}
                      node={edge}
                      depth={0}
                      rowKey={`${group.predicate}:${i}`}
                      selectedSourceRef={selectedSourceRef}
                      onSelectSourceRef={onSelectSourceRef}
                    />
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
