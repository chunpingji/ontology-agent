"use client";

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, Users, Edit, Plus, Trash2 } from "lucide-react";
import { listMockAssessmentTeam, createMockAssessmentTeamMember, updateMockAssessmentTeamMember, deleteMockAssessmentTeamMember, MockTeamMember } from "@/lib/api";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { EditDialog } from "@/components/mock/edit-dialog";

const TEAM_MEMBER_FIELDS = [
  { key: "name" as const, label: "姓名", required: true },
  { key: "department" as const, label: "归属部门", required: true },
  { key: "role_code" as const, label: "角色代码", required: true },
  { key: "role_label" as const, label: "角色名称", required: true },
  { key: "role_class_iri" as const, label: "角色类 IRI", required: true },
];

export default function MockAssessmentTeamPage() {
  const queryClient = useQueryClient();
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["mock", "assessment-team"],
    queryFn: listMockAssessmentTeam,
  });

  const createMutation = useMutation({
    mutationFn: createMockAssessmentTeamMember,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["mock", "assessment-team"] });
    },
  });

  const updateMutation = useMutation({
    mutationFn: (member: MockTeamMember) => updateMockAssessmentTeamMember(member.id!, member),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["mock", "assessment-team"] });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: deleteMockAssessmentTeamMember,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["mock", "assessment-team"] });
    },
  });

  return (
    <div>
      <div className="mb-6 flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold text-foreground">评估小组成员（Mock）</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            当前系统使用的 canned 评估小组成员数据（复用 GxP 角色）。真实内网 OA/HR API 接入后将替换为实时数据。
          </p>
        </div>
        <EditDialog
          trigger={
            <Button>
              <Plus className="mr-2 size-4" />
              新增成员
            </Button>
          }
          title="新增评估小组成员"
          description="创建一个新的评估小组成员记录"
          item={null}
          fields={TEAM_MEMBER_FIELDS}
          onSave={(member) => createMutation.mutateAsync(member)}
        />
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
          <AlertTitle>加载失败</AlertTitle>
          <AlertDescription>{String(error)}</AlertDescription>
        </Alert>
      )}

      {!isLoading && !isError && data && (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {data.map((member, i) => (
            <Card key={member.id || i}>
              <CardHeader>
                <div className="flex items-start gap-3">
                  <div className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-primary/10">
                    <Users className="size-5 text-primary" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <CardTitle className="text-lg">{member.name}</CardTitle>
                    <CardDescription className="mt-1">
                      {member.role_label}
                    </CardDescription>
                  </div>
                  <div className="flex gap-2">
                    <EditDialog
                      trigger={
                        <Button variant="ghost" size="icon">
                          <Edit className="size-4" />
                        </Button>
                      }
                      title="编辑评估小组成员"
                      description={`编辑 ${member.name} 的信息`}
                      item={member}
                      fields={TEAM_MEMBER_FIELDS}
                      onSave={(updated) => updateMutation.mutateAsync(updated)}
                    />
                    <Button
                      variant="ghost"
                      size="icon"
                      onClick={() => {
                        if (confirm(`确定要删除成员"${member.name}"吗？`)) {
                          deleteMutation.mutate(member.id!);
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
                  <div className="flex gap-2 text-sm">
                    <span className="font-medium text-muted-foreground">归属部门:</span>
                    <span className="text-foreground">{member.department}</span>
                  </div>
                  <div className="flex gap-2 text-sm">
                    <span className="font-medium text-muted-foreground">角色代码:</span>
                    <span className="text-foreground">{member.role_code}</span>
                  </div>
                  <div className="rounded-md bg-muted/50 p-3 mt-3">
                    <p className="text-xs font-mono text-muted-foreground mb-1">角色类 IRI</p>
                    <p className="text-xs font-mono break-all">{member.role_class_iri}</p>
                  </div>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
