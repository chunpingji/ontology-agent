"use client";

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, UserCog, Edit, Plus, Trash2 } from "lucide-react";
import { listMockRoles, createMockRole, updateMockRole, deleteMockRole, MockRole } from "@/lib/api";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { DataPropertiesTable } from "@/components/mock/data-properties-table";
import { EditDialog } from "@/components/mock/edit-dialog";

const ROLE_FIELDS = [
  { key: "code" as const, label: "代码", required: true },
  { key: "iri" as const, label: "IRI", required: true },
  { key: "label" as const, label: "名称", required: true },
  { key: "role_class_iri" as const, label: "角色类 IRI", required: true },
  { key: "description" as const, label: "描述", type: "textarea" as const },
];

export default function MockRolesPage() {
  const queryClient = useQueryClient();
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["mock", "roles"],
    queryFn: listMockRoles,
  });

  const createMutation = useMutation({
    mutationFn: createMockRole,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["mock", "roles"] });
    },
  });

  const updateMutation = useMutation({
    mutationFn: (role: MockRole) => updateMockRole(role.id!, role),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["mock", "roles"] });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: deleteMockRole,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["mock", "roles"] });
    },
  });

  return (
    <div>
      <div className="mb-6 flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold text-foreground">GxP 角色主数据（Mock）</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            当前系统使用的 canned GxP 评审角色数据。真实内网 OA/HR API 接入后将替换为实时数据。
          </p>
        </div>
        <EditDialog<MockRole>
          trigger={
            <Button>
              <Plus className="mr-2 size-4" />
              新增角色
            </Button>
          }
          title="新增角色"
          description="创建一个新的角色记录"
          item={null}
          fields={ROLE_FIELDS}
          onSave={(role) => createMutation.mutateAsync(role)}
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
          {data.map((role) => (
            <Card key={role.code}>
              <CardHeader>
                <div className="flex items-start gap-3">
                  <div className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-primary/10">
                    <UserCog className="size-5 text-primary" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <CardTitle className="text-lg">{role.label}</CardTitle>
                    <CardDescription className="mt-1">
                      代码: {role.code}
                    </CardDescription>
                  </div>
                  <div className="flex gap-2">
                    <EditDialog
                      trigger={
                        <Button variant="ghost" size="icon">
                          <Edit className="size-4" />
                        </Button>
                      }
                      title="编辑角色"
                      description={`编辑 ${role.label} 的信息`}
                      item={role}
                      fields={ROLE_FIELDS}
                      onSave={(updated) => updateMutation.mutateAsync(updated)}
                    />
                    <Button
                      variant="ghost"
                      size="icon"
                      onClick={() => {
                        if (confirm(`确定要删除角色"${role.label}"吗？`)) {
                          deleteMutation.mutate(role.id!);
                        }
                      }}
                    >
                      <Trash2 className="size-4 text-destructive" />
                    </Button>
                  </div>
                </div>
              </CardHeader>
              <CardContent>
                {role.description && (
                  <p className="text-sm text-muted-foreground mb-3">
                    {role.description}
                  </p>
                )}
                <div className="space-y-2">
                  <div className="rounded-md bg-muted/50 p-3">
                    <p className="text-xs font-mono text-muted-foreground mb-1">IRI</p>
                    <p className="text-xs font-mono break-all">{role.iri}</p>
                  </div>
                  <div className="rounded-md bg-muted/50 p-3">
                    <p className="text-xs font-mono text-muted-foreground mb-1">角色类 IRI</p>
                    <p className="text-xs font-mono break-all">{role.role_class_iri}</p>
                  </div>
                </div>
                <DataPropertiesTable properties={role.data_properties} />
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
