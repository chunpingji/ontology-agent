"use client";

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, Factory, Edit, Plus, Trash2 } from "lucide-react";
import { listMockProductionAreas, createMockProductionArea, updateMockProductionArea, deleteMockProductionArea, MockProductionArea } from "@/lib/api";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { DataPropertiesTable } from "@/components/mock/data-properties-table";
import { EditDialog } from "@/components/mock/edit-dialog";

const PRODUCTION_AREA_FIELDS = [
  { key: "code" as const, label: "车间编号", required: true },
  { key: "iri" as const, label: "IRI", required: true },
  { key: "label" as const, label: "名称", required: true },
  { key: "description" as const, label: "描述", type: "textarea" as const },
];

export default function MockProductionAreasPage() {
  const queryClient = useQueryClient();
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["mock", "production-areas"],
    queryFn: listMockProductionAreas,
  });

  const createMutation = useMutation({
    mutationFn: createMockProductionArea,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["mock", "production-areas"] });
    },
  });

  const updateMutation = useMutation({
    mutationFn: (area: MockProductionArea) => updateMockProductionArea(area.id!, area),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["mock", "production-areas"] });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: deleteMockProductionArea,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["mock", "production-areas"] });
    },
  });

  return (
    <div>
      <div className="mb-6 flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold text-foreground">生产区域主数据（Mock）</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            当前系统使用的 canned 生产车间/区域数据。真实内网设施主数据 API 接入后将替换为实时数据。
          </p>
        </div>
        <EditDialog
          trigger={
            <Button>
              <Plus className="mr-2 size-4" />
              新增区域
            </Button>
          }
          title="新增生产区域"
          description="创建一个新的生产区域记录"
          item={null}
          fields={PRODUCTION_AREA_FIELDS}
          onSave={(area) => createMutation.mutateAsync(area)}
        />
      </div>

      {isLoading && (
        <div className="space-y-3">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-32 w-full rounded-lg" />
          ))}
        </div>
      )}

      {isError && (
        <Alert variant="destructive">
          <AlertCircle className="size-4" />
          <AlertTitle>加载失败</AlertTitle>
          <AlertDescription>{String(error)}</AlertDescription>
        </Alert>
      )}

      {!isLoading && !isError && data && (
        <div className="grid gap-4 md:grid-cols-2">
          {data.map((area) => (
            <Card key={area.code}>
              <CardHeader>
                <div className="flex items-start gap-3">
                  <div className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-primary/10">
                    <Factory className="size-5 text-primary" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <CardTitle className="text-lg">{area.label}</CardTitle>
                    <CardDescription className="mt-1">
                      车间编号: {area.code}
                    </CardDescription>
                  </div>
                  <div className="flex gap-2">
                    <EditDialog
                      trigger={
                        <Button variant="ghost" size="icon">
                          <Edit className="size-4" />
                        </Button>
                      }
                      title="编辑生产区域"
                      description={`编辑 ${area.label} 的信息`}
                      item={area}
                      fields={PRODUCTION_AREA_FIELDS}
                      onSave={(updated) => updateMutation.mutateAsync(updated)}
                    />
                    <Button
                      variant="ghost"
                      size="icon"
                      onClick={() => {
                        if (confirm(`确定要删除生产区域"${area.label}"吗？`)) {
                          deleteMutation.mutate(area.id!);
                        }
                      }}
                    >
                      <Trash2 className="size-4 text-destructive" />
                    </Button>
                  </div>
                </div>
              </CardHeader>
              <CardContent>
                {area.description && (
                  <p className="text-sm text-muted-foreground mb-3">
                    {area.description}
                  </p>
                )}
                <div className="rounded-md bg-muted/50 p-3">
                  <p className="text-xs font-mono text-muted-foreground mb-1">IRI</p>
                  <p className="text-xs font-mono break-all">{area.iri}</p>
                </div>
                <DataPropertiesTable properties={area.data_properties} />
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
