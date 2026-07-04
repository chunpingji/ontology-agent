"use client";

import { useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { AlertCircle } from "lucide-react";

import {
  getAstTemplate,
  updateAstTemplate,
} from "@/lib/api";
import { TemplateSlotEditor } from "@/components/extraction/template-slot-editor";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
} from "@/components/ui/breadcrumb";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";

export default function EditTemplatePage() {
  const { templateId } = useParams<{ templateId: string }>();
  const router = useRouter();
  const [saving, setSaving] = useState(false);

  const query = useQuery({
    queryKey: ["ast-template", templateId],
    queryFn: () => getAstTemplate(templateId),
  });

  async function handleSave(updated: Record<string, unknown>) {
    setSaving(true);
    try {
      await updateAstTemplate(templateId, { schema_json: updated });
      router.push("/settings/ast-templates");
    } catch (e) {
      alert(e instanceof Error ? e.message : "保存失败");
    } finally {
      setSaving(false);
    }
  }

  const tpl = query.data;
  const title = tpl
    ? `编辑：${tpl.name} (${tpl.version})`
    : "编辑模板";

  return (
    <div className="flex flex-col h-[calc(100vh-56px-3rem)]">
      <div className="px-6 pt-4 pb-3">
        <Breadcrumb>
          <BreadcrumbList>
            <BreadcrumbItem>
              <BreadcrumbLink asChild>
                <Link href="/settings/ast-templates">报告模板</Link>
              </BreadcrumbLink>
            </BreadcrumbItem>
            <BreadcrumbSeparator />
            <BreadcrumbItem>
              <BreadcrumbPage className="max-w-[60vw] truncate">
                {title}
              </BreadcrumbPage>
            </BreadcrumbItem>
          </BreadcrumbList>
        </Breadcrumb>
      </div>

      <div className="flex-1 min-h-0 border-t -mx-6 -mb-6">
        {query.isLoading ? (
          <div className="p-8 space-y-4">
            <Skeleton className="h-8 w-64" />
            <Skeleton className="h-[60vh] w-full" />
          </div>
        ) : query.isError ? (
          <div className="p-8 space-y-4">
            <Alert variant="destructive">
              <AlertCircle className="h-4 w-4" />
              <AlertTitle>加载失败</AlertTitle>
              <AlertDescription>
                {query.error instanceof Error
                  ? query.error.message
                  : "无法加载模板详情"}
              </AlertDescription>
            </Alert>
            <Button
              variant="outline"
              onClick={() => router.push("/settings/ast-templates")}
            >
              返回列表
            </Button>
          </div>
        ) : tpl ? (
          <TemplateSlotEditor
            key={tpl.id}
            schema={tpl.schema_json as never}
            mode="edit"
            onSave={(updated) =>
              handleSave(updated as unknown as Record<string, unknown>)
            }
            onCancel={() => router.push("/settings/ast-templates")}
            saving={saving}
            aiEnabled
            iriPattern={tpl.iri_pattern}
            sampleText={tpl.sample_text ?? null}
            sampleContentJson={tpl.sample_content_json ?? null}
            templateId={templateId}
            meta={{
              name: tpl.name,
              docNo: tpl.doc_no,
              version: tpl.version,
              status: tpl.status,
              iriPattern: tpl.iri_pattern,
              owner: tpl.owner,
              updatedAt: tpl.updated_at,
              defaultSourceFilename: tpl.default_source_filename,
              sampleConfigured: tpl.sample_content_json != null,
            }}
            versions={tpl.versions ?? []}
            onVersionSwitch={(id) => router.push(`/settings/ast-templates/${id}`)}
            onMetaSaved={() => query.refetch()}
          />
        ) : null}
      </div>
    </div>
  );
}
