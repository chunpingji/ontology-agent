"use client";

import { Link2, Loader2 } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { EmptyState } from "@/components/ui/empty-state";
import { type RecognizedEntity } from "@/lib/api";

/**
 * 右侧「关联信息」面板：展示从该文档 NER 标注中识别出的本体实体（约束到用户所选文档类型）。
 * 实体与预览同源——由详情页 `resolveDocumentContent` 一次标注请求产出（`entities`），此处纯展示。
 */
export function RelatedInfo({
  isDoc,
  entities,
  isLoading,
  isError,
}: {
  /** 当前条目是否为上传文档（生成报告无文档回链 → 无关联实体）。 */
  isDoc: boolean;
  /** 识别实体（去重后）；null 表示尚未解析出（加载中/不可用）。 */
  entities: RecognizedEntity[] | null;
  isLoading?: boolean;
  isError?: boolean;
}) {
  // 生成报告没有文档回链——无关联实体。
  if (!isDoc) {
    return (
      <EmptyState
        icon={<Link2 />}
        title="无关联实体"
        description="生成报告不包含从文档识别的关联实体。"
      />
    );
  }

  if (isLoading) {
    return (
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" />
        正在识别实体…
      </div>
    );
  }

  if (isError) {
    return (
      <Alert variant="destructive">
        <AlertTitle>关联实体加载失败</AlertTitle>
        <AlertDescription>无法读取该文档的识别实体。</AlertDescription>
      </Alert>
    );
  }

  const list = entities ?? [];
  if (list.length === 0) {
    return (
      <EmptyState
        icon={<Link2 />}
        title="无关联实体"
        description="尚未从该文档识别到本体实体。"
      />
    );
  }

  return (
    <ul className="space-y-2">
      {list.map((entity) => (
        <li
          key={`${entity.classIri}::${entity.text}`}
          className="rounded-md border border-border bg-card p-3"
        >
          <p className="truncate text-sm font-medium text-foreground" title={entity.text}>
            {entity.text}
            {entity.count > 1 && (
              <span className="ml-1 text-xs font-normal text-muted-foreground">
                ×{entity.count}
              </span>
            )}
          </p>
          <div className="mt-1.5 flex items-center gap-2">
            <Badge variant="outline">{entity.classLabel}</Badge>
          </div>
        </li>
      ))}
    </ul>
  );
}
