"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";

import { createAstTemplate } from "@/lib/api";
import { useCreateTemplateStore } from "@/lib/ast-template-create-store";
import { TemplateSlotEditor } from "@/components/extraction/template-slot-editor";
import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
} from "@/components/ui/breadcrumb";

export default function CreateTemplatePage() {
  const router = useRouter();
  const { payload, clearPayload } = useCreateTemplateStore();
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!payload) router.replace("/settings/ast-templates");
  }, [payload, router]);

  if (!payload) return null;

  async function handleSave(updated: Record<string, unknown>) {
    if (!payload) return;
    setSaving(true);
    try {
      await createAstTemplate({
        name: payload.name,
        version: payload.version,
        doc_no: payload.docNo || undefined,
        iri_pattern: payload.iriPattern || undefined,
        schema_json: updated,
        sample_text: payload.sampleText ?? undefined,
        sample_content_json: payload.sampleContent ?? undefined,
      });
      clearPayload();
      router.push("/settings/ast-templates");
    } catch (e) {
      alert(e instanceof Error ? e.message : "创建失败");
    } finally {
      setSaving(false);
    }
  }

  function handleCancel() {
    clearPayload();
    router.push("/settings/ast-templates");
  }

  return (
    <div className="flex flex-col h-[calc(100vh-56px)]">
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
                创建：{payload.name}
              </BreadcrumbPage>
            </BreadcrumbItem>
          </BreadcrumbList>
        </Breadcrumb>
      </div>

      <div className="flex-1 min-h-0 border-t -mx-6 -mb-6">
        <TemplateSlotEditor
          key="create"
          schema={{
            template_id: payload.name,
            doc_no: payload.docNo || "QS-A-020F05",
            revision: payload.version,
            sections: [],
          } as never}
          mode="create"
          onSave={(updated) =>
            handleSave(updated as unknown as Record<string, unknown>)
          }
          onCancel={handleCancel}
          saving={saving}
          aiEnabled
          iriPattern={payload.iriPattern}
          sampleText={payload.sampleText}
          sampleContentJson={payload.sampleContent}
        />
      </div>
    </div>
  );
}
