"use client";

import { Suspense, useState } from "react";
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { AlertCircle } from "lucide-react";

import {
  getAstTemplate,
  updateAstTemplate,
} from "@/lib/api";
import { WordViewer } from "@/components/extraction/word-viewer";
import { BatchDemoTemplate } from "@/components/reports/batch-demo-template";
import { OutputTemplateEditor } from "@/components/reporting/output-template-editor";
import { isTemplateV2, reportPost, type FrozenRecord, type TemplateV2 } from "@/lib/reporting-v2";
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
  return <Suspense fallback={<p className="p-6 text-sm text-muted-foreground">正在加载模板…</p>}>
    <EditTemplateContent />
  </Suspense>;
}

function EditTemplateContent() {
  const { templateId } = useParams<{ templateId: string }>();
  const searchParams = useSearchParams();
  const router = useRouter();
  const [saving, setSaving] = useState(false);
  async function migrate() {
    if (!query.data) return;
    setSaving(true);
    try {
      const plan = await reportPost<FrozenRecord<{ target_schema: TemplateV2 }>>(
        "ast-templates/" + templateId + "/migration-plan", { expected_hash: query.data.schema_hash });
      await handleSave(plan.payload.target_schema);
    } catch (e) { alert(e instanceof Error ? e.message : "迁移失败"); }
    finally { setSaving(false); }
  }

  const query = useQuery({
    queryKey: ["ast-template", templateId],
    queryFn: () => getAstTemplate(templateId),
  });

  async function handleSave(updated: Record<string, unknown>) {
    setSaving(true);
    try {
      const result = isTemplateV2(updated)
        ? await reportPost<{ id: string }>("ast-templates/" + templateId + "/revisions", {
            schema: updated, expected_hash: query.data?.schema_hash,
            expected_revision: query.data?.revision_no,
          })
        : await updateAstTemplate(templateId, { schema_json: updated });
      router.push(`/settings/ast-templates/${result.id}`);
    } catch (e) {
      alert(e instanceof Error ? e.message : "保存失败");
    } finally {
      setSaving(false);
    }
  }

  const tpl = query.data;
  const title = tpl
    ? `${tpl.demo_profile ? "演示模板" : "编辑"}：${tpl.name} (${tpl.version})`
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

      {tpl && searchParams.get("created") === "1" && <p role="status" className="mb-3 rounded border bg-muted/30 px-4 py-2 text-sm">
        模板已创建并保存为草稿。可继续编辑，后续修改请点击「保存新修订」。
      </p>}

      <div className="flex-1 min-h-0 border-t -mx-6 -mb-6">
        {tpl && !isTemplateV2(tpl.schema_json) && <div className="border-b bg-amber-50 p-3 text-sm flex items-center gap-4">
          <p className="flex-1">这是历史 Slot 模板。迁移将创建新的 V2 草稿，并保留原版本与待确认事项。</p>
          <Button disabled={saving} onClick={migrate}>创建 V2 迁移草稿</Button>
        </div>}
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
        ) : tpl?.demo_profile ? (
          <BatchDemoTemplate key={tpl.id} templateId={tpl.id} />
        ) : tpl ? isTemplateV2(tpl.schema_json) ? (
          <OutputTemplateEditor key={tpl.id} schema={tpl.schema_json} templateId={tpl.id}
            initialTab={searchParams.get("tab") === "template" ? "template" : undefined}
            schemaHash={tpl.schema_hash} saving={saving} onSave={handleSave}
            onCancel={() => router.push("/settings/ast-templates")}
            defaultSourceJobId={tpl.default_source_job_id} versions={tpl.versions} sampleContentJson={tpl.sample_content_json}
            sampleText={tpl.sample_text}
            meta={{
              name: tpl.name,
              docNo: tpl.doc_no,
              version: tpl.version,
              status: tpl.status,
              iriPattern: tpl.iri_pattern,
              owner: tpl.owner,
              updatedAt: tpl.updated_at,
              defaultSourceFilename: tpl.default_source_filename,
              defaultSourceJobId: tpl.default_source_job_id,
              sampleConfigured: tpl.sample_content_json != null,
            }}
            onMetaSaved={() => query.refetch()}
            onVersionSwitch={(id) => router.push("/settings/ast-templates/" + id)} />
        ) : (
          <div className="h-full overflow-auto p-6 space-y-4">
            <p>历史模板只读。编辑内容请先创建 V2 迁移草稿。</p>
            <select aria-label="历史模板版本" className="border rounded p-2" value={tpl.id}
              onChange={(e) => router.push("/settings/ast-templates/" + e.target.value)}>
              {tpl.versions?.map((v) => <option key={v.id} value={v.id}>{v.version}</option>)}
            </select>
            {tpl.sample_content_json && <WordViewer content={tpl.sample_content_json} fitTables />}
            <details><summary>原模板定义与来源</summary><pre className="text-xs overflow-auto">{JSON.stringify(tpl.schema_json, null, 2)}</pre></details>
          </div>
        ) : null}
      </div>
    </div>
  );
}
