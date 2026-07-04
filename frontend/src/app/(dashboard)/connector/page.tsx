"use client";

import { useQuery } from "@tanstack/react-query";
import { AlertCircle, Plug, Plus } from "lucide-react";
import { listConnectors } from "@/lib/api";
import { useIdentity } from "@/lib/use-identity";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { Skeleton } from "@/components/ui/skeleton";
import { ConnectorForm } from "@/components/connector/connector-form";
import { ConnectorList } from "@/components/connector/connector-list";

/**
 * 数据源接入（US2）——连接器按 system_type 分组展示，四态齐全（加载/空/错误/有数据）。
 * senior_analyst 可新建/编辑/探活/删除；operator/qa 仅浏览（写操作按钮隐藏）。
 */
export default function ConnectorPage() {
  const { role } = useIdentity();
  const canEdit = role === "senior_analyst";
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["connectors"],
    queryFn: listConnectors,
  });

  return (
    <div>
      <div className="mb-6 flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-foreground">数据源接入</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            按数据源类型管理连接器：REST 接口 / 数据库 / 研发文档库 /
            文件源。凭据仅以环境变量名引用，绝不存明文。
          </p>
        </div>
        {canEdit && (
          <ConnectorForm
            trigger={
              <Button>
                <Plus className="size-4" />
                新建连接器
              </Button>
            }
          />
        )}
      </div>

      {isLoading && (
        <div className="space-y-3">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-24 w-full rounded-lg" />
          ))}
        </div>
      )}

      {isError && (
        <Alert variant="destructive">
          <AlertCircle className="size-4" />
          <AlertTitle>加载连接器失败</AlertTitle>
          <AlertDescription>{String(error)}</AlertDescription>
        </Alert>
      )}

      {!isLoading && !isError && data && data.length === 0 && (
        <EmptyState
          icon={<Plug />}
          title="暂无连接器"
          description={
            canEdit
              ? "点击右上角「新建连接器」以接入首个数据源。"
              : "尚未配置任何数据源连接器。"
          }
        />
      )}

      {!isLoading && !isError && data && data.length > 0 && (
        <ConnectorList connectors={data} canEdit={canEdit} />
      )}
    </div>
  );
}
