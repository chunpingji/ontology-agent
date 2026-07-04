"use client";

import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { CheckCircle2, MousePointerClick, PlayCircle, Plus, Waypoints } from "lucide-react";

import {
  getMappings,
  SOURCE_ENTITY_MAPPING_TYPES,
  validateBinding,
  type BindingValidationReport,
  type PropertyBinding,
  type TBoxMapping,
} from "@/lib/api";
import { useIdentity } from "@/lib/use-identity";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import { ClassTree } from "@/components/data-mapping/class-tree";
import { BindingTable } from "@/components/data-mapping/binding-table";
import { BindingEditor } from "@/components/data-mapping/binding-editor";
import { Coverage } from "@/components/data-mapping/coverage";

const classMappingsKey = (classIri: string) => ["class-mappings", classIri] as const;

/** Prefer a source-entity mapping (only those carry property bindings), else the first. */
function pickPrimaryMapping(maps: TBoxMapping[]): TBoxMapping | null {
  return maps.find((m) => SOURCE_ENTITY_MAPPING_TYPES.includes(m.mapping_type)) ?? maps[0] ?? null;
}

interface EditorState {
  open: boolean;
  binding?: PropertyBinding;
  key: number;
}

export default function DataMappingPage() {
  const { role } = useIdentity();
  const canEdit = role === "senior_analyst";

  const [selectedClass, setSelectedClass] = useState<string | null>(null);
  const [mappingOverride, setMappingOverride] = useState<string | null>(null);
  const [editor, setEditor] = useState<EditorState>({ open: false, key: 0 });
  const [testResult, setTestResult] = useState<
    { mappingId: string; report: BindingValidationReport } | null
  >(null);

  const mappingsQuery = useQuery({
    queryKey: selectedClass ? classMappingsKey(selectedClass) : ["class-mappings", "none"],
    queryFn: () => getMappings(selectedClass as string),
    enabled: Boolean(selectedClass),
  });

  const maps = mappingsQuery.data ?? [];
  const activeMapping =
    (mappingOverride && maps.find((m) => m.id === mappingOverride)) || pickPrimaryMapping(maps);

  const testMutation = useMutation({
    mutationFn: (mappingId: string) => validateBinding(mappingId),
    onSuccess: (report, mappingId) => setTestResult({ mappingId, report }),
  });

  const openCreate = () => setEditor((s) => ({ open: true, binding: undefined, key: s.key + 1 }));
  const openEdit = (binding: PropertyBinding) =>
    setEditor((s) => ({ open: true, binding, key: s.key + 1 }));
  const closeEditor = () => setEditor((s) => ({ ...s, open: false }));

  const activeTestReport =
    testResult && activeMapping && testResult.mappingId === activeMapping.id
      ? testResult.report
      : null;

  return (
    <div className="space-y-6">
      <header>
        <div className="flex items-center gap-2">
          <Waypoints className="size-5 text-primary" />
          <h1 className="text-2xl font-semibold text-foreground">数据映射</h1>
        </div>
        <p className="mt-1 text-sm text-muted-foreground">
          选择本体类，查看并维护其源绑定与「本体属性 → 源字段」的属性绑定。
        </p>
      </header>

      <div className="grid gap-4 lg:grid-cols-12">
        {/* 左栏：类选择器 */}
        <section className="lg:col-span-4 xl:col-span-3">
          <div className="h-[calc(100vh-13rem)] min-h-[24rem]">
            <ClassTree
              selected={selectedClass}
              onSelect={(iri) => {
                setSelectedClass(iri);
                setMappingOverride(null);
              }}
            />
          </div>
        </section>

        {/* 右栏：源绑定 + 属性绑定 + 覆盖度 */}
        <section className="lg:col-span-8 xl:col-span-9">
          {!selectedClass ? (
            <EmptyState
              icon={<MousePointerClick />}
              title="未选择类"
              description="从左侧列表选择一个本体类，查看其源绑定与属性绑定。"
            />
          ) : mappingsQuery.isLoading ? (
            <div className="space-y-4">
              <Skeleton className="h-10 w-1/2" />
              <Skeleton className="h-40 w-full" />
            </div>
          ) : mappingsQuery.isError ? (
            <Alert variant="destructive">
              <AlertTitle>无法加载映射</AlertTitle>
              <AlertDescription>该类的映射读取失败，请稍后重试。</AlertDescription>
            </Alert>
          ) : !activeMapping ? (
            <EmptyState
              icon={<Waypoints />}
              title="该类暂无源绑定"
              description="该类尚未定义任何映射（TBoxMapping）。可在本体工作台的映射面板中新建。"
            />
          ) : (
            <div className="space-y-6">
              {/* 源绑定概要 */}
              <div className="rounded-lg border border-border bg-card p-4">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-sm font-medium text-foreground">源绑定</span>
                    <Badge variant="secondary" className="font-normal">
                      {activeMapping.mapping_type}
                    </Badge>
                    <span className="font-mono text-xs text-muted-foreground">
                      {activeMapping.target}
                    </span>
                    {activeMapping.source_system && (
                      <Badge variant="outline" className="font-normal">
                        {activeMapping.source_system}
                      </Badge>
                    )}
                  </div>
                  <div className="flex items-center gap-2">
                    <Button
                      variant="secondary"
                      size="sm"
                      disabled={testMutation.isPending}
                      onClick={() => testMutation.mutate(activeMapping.id)}
                    >
                      <PlayCircle />
                      {testMutation.isPending ? "测试中…" : "运行映射测试"}
                    </Button>
                    {canEdit && (
                      <Button size="sm" onClick={openCreate}>
                        <Plus />
                        新增绑定
                      </Button>
                    )}
                  </div>
                </div>

                {maps.length > 1 && (
                  <div className="mt-3 flex items-center gap-2">
                    <span className="text-xs text-muted-foreground">选择映射</span>
                    <Select
                      value={activeMapping.id}
                      onValueChange={(v) => {
                        setMappingOverride(v);
                        setTestResult(null);
                      }}
                    >
                      <SelectTrigger className="h-8 w-auto min-w-[16rem] text-xs">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {maps.map((m) => (
                          <SelectItem key={m.id} value={m.id}>
                            {m.mapping_type} · {m.target}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                )}

                {testMutation.isError && (
                  <Alert variant="destructive" className="mt-3">
                    <AlertTitle>映射测试失败</AlertTitle>
                    <AlertDescription>{String(testMutation.error)}</AlertDescription>
                  </Alert>
                )}

                {activeTestReport && (
                  <div className="mt-3 space-y-2">
                    {activeTestReport.errors.length === 0 &&
                    activeTestReport.warnings.length === 0 ? (
                      <Alert>
                        <CheckCircle2 className="size-4 text-success" />
                        <AlertTitle>映射测试通过</AlertTitle>
                        <AlertDescription>
                          健康度：{activeTestReport.health}，未发现错误或告警。
                        </AlertDescription>
                      </Alert>
                    ) : (
                      <>
                        {activeTestReport.errors.length > 0 && (
                          <Alert variant="destructive">
                            <AlertTitle>
                              错误（{activeTestReport.errors.length}）
                            </AlertTitle>
                            <AlertDescription>
                              <ul className="mt-1 space-y-1">
                                {activeTestReport.errors.map((issue, idx) => (
                                  <li key={`e-${idx}`}>
                                    [{issue.code}] {issue.message}
                                  </li>
                                ))}
                              </ul>
                            </AlertDescription>
                          </Alert>
                        )}
                        {activeTestReport.warnings.length > 0 && (
                          <Alert variant="warning">
                            <AlertTitle>
                              告警（{activeTestReport.warnings.length}）
                            </AlertTitle>
                            <AlertDescription>
                              <ul className="mt-1 space-y-1">
                                {activeTestReport.warnings.map((issue, idx) => (
                                  <li key={`w-${idx}`}>
                                    [{issue.code}] {issue.message}
                                  </li>
                                ))}
                              </ul>
                            </AlertDescription>
                          </Alert>
                        )}
                      </>
                    )}
                  </div>
                )}
              </div>

              {/* 覆盖度 */}
              <div className="rounded-lg border border-border bg-card p-4">
                <Coverage classIri={selectedClass} />
              </div>

              <Separator />

              {/* 属性绑定表 */}
              <BindingTable
                mappingId={activeMapping.id}
                canEdit={canEdit}
                onEdit={openEdit}
              />

              {canEdit && (
                <BindingEditor
                  key={editor.key}
                  mappingId={activeMapping.id}
                  binding={editor.binding}
                  open={editor.open}
                  onClose={closeEditor}
                />
              )}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
