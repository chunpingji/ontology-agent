"use client";

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, Building2, Edit, Plus, Trash2 } from "lucide-react";
import { listMockDepartments, createMockDepartment, updateMockDepartment, deleteMockDepartment, MockDepartment } from "@/lib/api";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { DataPropertiesTable } from "@/components/mock/data-properties-table";
import { EditDialog } from "@/components/mock/edit-dialog";

const DEPARTMENT_FIELDS = [
  { key: "code" as const, label: "代码", required: true },
  { key: "iri" as const, label: "IRI", required: true },
  { key: "label" as const, label: "名称", required: true },
  { key: "description" as const, label: "描述", type: "textarea" as const },
];

export default function MockDepartmentsPage() {
  const queryClient = useQueryClient();
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["mock", "departments"],
    queryFn: listMockDepartments,
  });

  const createMutation = useMutation({
    mutationFn: createMockDepartment,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["mock", "departments"] });
    },
  });

  const updateMutation = useMutation({
    mutationFn: (dept: MockDepartment) => updateMockDepartment(dept.id!, dept),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["mock", "departments"] });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: deleteMockDepartment,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["mock", "departments"] });
    },
  });

  return (
    <div>
      <div className="mb-6 flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold text-foreground">部门主数据（Mock）</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            当前系统使用的 canned 部门数据。真实内网 OA/HR API 接入后将替换为实时数据。
          </p>
        </div>
        <EditDialog
          trigger={
            <Button>
              <Plus className="mr-2 size-4" />
              新增部门
            </Button>
          }
          title="新增部门"
          description="创建一个新的部门记录"
          item={null}
          fields={DEPARTMENT_FIELDS}
          onSave={(dept) => createMutation.mutateAsync(dept)}
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
          {data.map((dept) => (
            <Card key={dept.code}>
              <CardHeader>
                <div className="flex items-start gap-3">
                  <div className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-primary/10">
                    <Building2 className="size-5 text-primary" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <CardTitle className="text-lg">{dept.label}</CardTitle>
                    <CardDescription className="mt-1">
                      代码: {dept.code}
                    </CardDescription>
                  </div>
                  <div className="flex gap-2">
                    <EditDialog
                      trigger={
                        <Button variant="ghost" size="icon">
                          <Edit className="size-4" />
                        </Button>
                      }
                      title="编辑部门"
                      description={`编辑 ${dept.label} 的信息`}
                      item={dept}
                      fields={DEPARTMENT_FIELDS}
                      onSave={(updated) => updateMutation.mutateAsync(updated)}
                    />
                    <Button
                      variant="ghost"
                      size="icon"
                      onClick={() => {
                        if (confirm(`确定要删除部门"${dept.label}"吗？`)) {
                          deleteMutation.mutate(dept.id!);
                        }
                      }}
                    >
                      <Trash2 className="size-4 text-destructive" />
                    </Button>
                  </div>
                </div>
              </CardHeader>
              <CardContent>
                {dept.description && (
                  <p className="text-sm text-muted-foreground mb-3">
                    {dept.description}
                  </p>
                )}
                <div className="rounded-md bg-muted/50 p-3">
                  <p className="text-xs font-mono text-muted-foreground mb-1">IRI</p>
                  <p className="text-xs font-mono break-all">{dept.iri}</p>
                </div>
                <DataPropertiesTable properties={dept.data_properties} />
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
