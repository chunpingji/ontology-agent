"use client";

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, Wrench, Edit, Plus, Trash2 } from "lucide-react";
import { listMockEquipment, createMockEquipment, updateMockEquipment, deleteMockEquipment, MockEquipment } from "@/lib/api";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { DataPropertiesTable } from "@/components/mock/data-properties-table";
import { EditDialog } from "@/components/mock/edit-dialog";

const EQUIPMENT_FIELDS = [
  { key: "equipment_id" as const, label: "设备编号", required: true },
  { key: "iri" as const, label: "IRI", required: true },
  { key: "label" as const, label: "名称", required: true },
  { key: "equipment_class_iri" as const, label: "设备类 IRI", required: true },
  { key: "workshop_code" as const, label: "车间代码", required: true },
];

export default function MockEquipmentPage() {
  const queryClient = useQueryClient();
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["mock", "equipment"],
    queryFn: listMockEquipment,
  });

  const createMutation = useMutation({
    mutationFn: createMockEquipment,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["mock", "equipment"] });
    },
  });

  const updateMutation = useMutation({
    mutationFn: (eq: MockEquipment) => updateMockEquipment(eq.id!, eq),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["mock", "equipment"] });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: deleteMockEquipment,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["mock", "equipment"] });
    },
  });

  const equipmentByWorkshop = data?.reduce((acc, eq) => {
    if (!acc[eq.workshop_code]) acc[eq.workshop_code] = [];
    acc[eq.workshop_code].push(eq);
    return acc;
  }, {} as Record<string, typeof data>);

  return (
    <div>
      <div className="mb-6 flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold text-foreground">设备档案主数据（Mock）</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            当前系统使用的 canned 设备档案数据（来源: 642/646车间设备档案.xlsx）。真实内网设备主数据 API 接入后将替换为实时数据。
          </p>
        </div>
        <EditDialog
          trigger={
            <Button>
              <Plus className="mr-2 size-4" />
              新增设备
            </Button>
          }
          title="新增设备"
          description="创建一个新的设备记录"
          item={null}
          fields={EQUIPMENT_FIELDS}
          onSave={(eq) => createMutation.mutateAsync(eq)}
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

      {!isLoading && !isError && equipmentByWorkshop && (
        <div className="space-y-6">
          {Object.entries(equipmentByWorkshop).map(([workshop, items]) => (
            <div key={workshop}>
              <h2 className="text-lg font-semibold mb-3">{workshop} 车间</h2>
              <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
                {items.map((eq) => (
                  <Card key={eq.equipment_id}>
                    <CardHeader>
                      <div className="flex items-start gap-3">
                        <div className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-primary/10">
                          <Wrench className="size-5 text-primary" />
                        </div>
                        <div className="flex-1 min-w-0">
                          <CardTitle className="text-base">{eq.label}</CardTitle>
                          <CardDescription className="mt-1 text-xs">
                            {eq.equipment_id}
                          </CardDescription>
                        </div>
                        <div className="flex gap-2">
                          <EditDialog
                            trigger={
                              <Button variant="ghost" size="icon">
                                <Edit className="size-4" />
                              </Button>
                            }
                            title="编辑设备"
                            description={`编辑 ${eq.label} 的信息`}
                            item={eq}
                            fields={EQUIPMENT_FIELDS}
                            onSave={(updated) => updateMutation.mutateAsync(updated)}
                          />
                          <Button
                            variant="ghost"
                            size="icon"
                            onClick={() => {
                              if (confirm(`确定要删除设备"${eq.label}"吗？`)) {
                                deleteMutation.mutate(eq.id!);
                              }
                            }}
                          >
                            <Trash2 className="size-4 text-destructive" />
                          </Button>
                        </div>
                      </div>
                    </CardHeader>
                    <CardContent>
                      <div className="space-y-2">
                        <div className="rounded-md bg-muted/50 p-2">
                          <p className="text-xs font-mono text-muted-foreground mb-1">IRI</p>
                          <p className="text-xs font-mono break-all">{eq.iri}</p>
                        </div>
                        <div className="rounded-md bg-muted/50 p-2">
                          <p className="text-xs font-mono text-muted-foreground mb-1">设备类 IRI</p>
                          <p className="text-xs font-mono break-all">{eq.equipment_class_iri}</p>
                        </div>
                      </div>
                      <DataPropertiesTable properties={eq.data_properties} />
                    </CardContent>
                  </Card>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
