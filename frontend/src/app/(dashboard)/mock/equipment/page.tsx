"use client";

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { AlertCircle, Wrench, Edit, Eye, Plus, Search, Trash2, X } from "lucide-react";
import { listMockEquipment, createMockEquipment, updateMockEquipment, deleteMockEquipment, MockEquipment } from "@/lib/api";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { DataPropertiesTable } from "@/components/mock/data-properties-table";
import { EditDialog } from "@/components/mock/edit-dialog";
import { EquipmentWeekSchedule } from "@/components/mock/equipment-week-schedule";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";

const EQUIPMENT_FIELDS = [
  { key: "equipment_id" as const, label: "设备编号", required: true },
  { key: "iri" as const, label: "IRI", required: true },
  { key: "label" as const, label: "名称", required: true },
  { key: "equipment_class_iri" as const, label: "设备类 IRI", required: true },
  { key: "workshop_code" as const, label: "车间代码", required: true },
];

export default function MockEquipmentPage() {
  const queryClient = useQueryClient();
  const [selectedEquipmentId, setSelectedEquipmentId] = useState<string | null>(null);
  const [selectedWorkshop, setSelectedWorkshop] = useState("all");
  const [searchQuery, setSearchQuery] = useState("");
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
      setSelectedEquipmentId(null);
      queryClient.invalidateQueries({ queryKey: ["mock", "equipment"] });
    },
  });

  const selectedEquipment = data?.find((eq) => eq.id === selectedEquipmentId) ?? null;

  const workshops = [...new Set(data?.map((eq) => eq.workshop_code) ?? [])].sort();
  const normalizedSearch = searchQuery.trim().toLocaleLowerCase();
  const filteredEquipment = (data ?? []).filter((eq) => {
    const matchesWorkshop = selectedWorkshop === "all" || eq.workshop_code === selectedWorkshop;
    const matchesSearch = normalizedSearch === ""
      || eq.label.toLocaleLowerCase().includes(normalizedSearch)
      || eq.equipment_id.toLocaleLowerCase().includes(normalizedSearch);
    return matchesWorkshop && matchesSearch;
  });

  return (
    <div>
      <div className="mb-6 flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold text-foreground">设备档案主数据（Mock）</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            当前系统使用的 canned 设备档案数据（来源: 642/646车间设备档案.xlsx）。真实内网设备主数据 API 接入后将替换为实时数据。
          </p>
        </div>
        <EditDialog<MockEquipment>
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
          onSave={async (eq) => {
            await createMutation.mutateAsync(eq);
          }}
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
        <div className="space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border bg-muted/20 px-4 py-3">
            <div>
              <p className="text-sm font-medium">设备列表</p>
              <p className="mt-0.5 text-xs text-muted-foreground">
                当前显示 {filteredEquipment.length} / {data.length} 台设备
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <div className="relative w-64">
                <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  value={searchQuery}
                  onChange={(event) => setSearchQuery(event.target.value)}
                  placeholder="搜索设备名称或编号"
                  className="bg-background pl-9 pr-9"
                  aria-label="按设备名称或编号搜索"
                />
                {searchQuery && (
                  <button
                    type="button"
                    className="absolute right-2 top-1/2 flex size-6 -translate-y-1/2 items-center justify-center rounded-sm text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                    onClick={() => setSearchQuery("")}
                    aria-label="清除设备搜索"
                  >
                    <X className="size-3.5" />
                  </button>
                )}
              </div>
              <label htmlFor="workshop-filter" className="text-sm text-muted-foreground">车间</label>
              <Select value={selectedWorkshop} onValueChange={setSelectedWorkshop}>
                <SelectTrigger id="workshop-filter" className="w-40 bg-background" aria-label="按车间筛选设备">
                  <SelectValue placeholder="全部设备" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">全部设备</SelectItem>
                  {workshops.map((workshop) => (
                    <SelectItem key={workshop} value={workshop}>{workshop} 车间</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>

          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
            {filteredEquipment.map((eq) => (
              <Card key={eq.equipment_id}>
                <CardHeader>
                  <div className="flex items-start gap-3">
                    <div className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-primary/10">
                      <Wrench className="size-5 text-primary" />
                    </div>
                    <div className="min-w-0 flex-1">
                      <CardTitle className="text-base">{eq.label}</CardTitle>
                      <CardDescription className="mt-1 text-xs">{eq.equipment_id}</CardDescription>
                    </div>
                    <Badge variant="secondary" className="shrink-0">{eq.workshop_code} 车间</Badge>
                  </div>
                </CardHeader>
                <CardContent>
                  <Button variant="outline" className="w-full" onClick={() => setSelectedEquipmentId(eq.id!)}>
                    <Eye className="mr-2 size-4" />
                    查看档案
                  </Button>
                </CardContent>
              </Card>
            ))}
          </div>
          {filteredEquipment.length === 0 && (
            <div className="rounded-lg border border-dashed py-12 text-center">
              <Search className="mx-auto size-8 text-muted-foreground/60" />
              <p className="mt-3 text-sm font-medium">未找到匹配设备</p>
              <p className="mt-1 text-xs text-muted-foreground">请调整设备名称、编号或车间筛选条件</p>
            </div>
          )}
        </div>
      )}

      <Sheet
        open={selectedEquipment !== null}
        onOpenChange={(open) => {
          if (!open) setSelectedEquipmentId(null);
        }}
      >
        <SheetContent size="wide" className="overflow-y-auto">
          {selectedEquipment && (
            <>
              <SheetHeader className="pr-8">
                <SheetTitle className="flex items-center gap-2">
                  <Wrench className="size-5 text-primary" />
                  {selectedEquipment.label}
                </SheetTitle>
                <SheetDescription>
                  设备编号 {selectedEquipment.equipment_id} · {selectedEquipment.workshop_code} 车间
                </SheetDescription>
              </SheetHeader>

              <div className="space-y-5 py-2">
                <EquipmentWeekSchedule equipment={selectedEquipment} />

                <div className="grid gap-3 sm:grid-cols-2">
                  <div className="rounded-lg border p-3">
                    <p className="mb-1 text-xs text-muted-foreground">设备编号</p>
                    <p className="font-medium">{selectedEquipment.equipment_id}</p>
                  </div>
                  <div className="rounded-lg border p-3">
                    <p className="mb-1 text-xs text-muted-foreground">所属车间</p>
                    <p className="font-medium">{selectedEquipment.workshop_code} 车间</p>
                  </div>
                </div>

                <div className="space-y-3">
                  <div className="rounded-md bg-muted/50 p-3">
                    <p className="mb-1 text-xs font-mono text-muted-foreground">IRI</p>
                    <p className="break-all text-xs font-mono">{selectedEquipment.iri}</p>
                  </div>
                  <div className="rounded-md bg-muted/50 p-3">
                    <p className="mb-1 text-xs font-mono text-muted-foreground">设备类 IRI</p>
                    <p className="break-all text-xs font-mono">{selectedEquipment.equipment_class_iri}</p>
                  </div>
                </div>

                <DataPropertiesTable properties={selectedEquipment.data_properties} />

                <div className="flex justify-end gap-2 border-t pt-4">
                  <Button
                    variant="outline"
                    onClick={() => {
                      if (confirm(`确定要删除设备"${selectedEquipment.label}"吗？`)) {
                        deleteMutation.mutate(selectedEquipment.id!);
                      }
                    }}
                    disabled={deleteMutation.isPending}
                  >
                    <Trash2 className="mr-2 size-4 text-destructive" />
                    删除
                  </Button>
                  <EditDialog<MockEquipment>
                    trigger={
                      <Button>
                        <Edit className="mr-2 size-4" />
                        编辑设备
                      </Button>
                    }
                    title="编辑设备"
                    description={`编辑 ${selectedEquipment.label} 的信息`}
                    item={selectedEquipment}
                    fields={EQUIPMENT_FIELDS}
                    onSave={async (updated) => {
                      await updateMutation.mutateAsync(updated);
                    }}
                  />
                </div>
              </div>
            </>
          )}
        </SheetContent>
      </Sheet>
    </div>
  );
}
