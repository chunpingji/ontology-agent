"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";

import { createAstTemplate, uploadTemplateSample } from "@/lib/api";
import { useCreateTemplateStore } from "@/lib/ast-template-create-store";
import { OutputTemplateEditor } from "@/components/reporting/output-template-editor";
import { emptyTemplate } from "@/lib/reporting-v2";
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
  const [initialTemplate] = useState(emptyTemplate);

  useEffect(() => {
    if (!payload) router.replace("/settings/ast-templates");
  }, [payload, router]);

  if (!payload) return null;

  async function handleSave(updated: Record<string, unknown>) {
    if (!payload) return;
    setSaving(true);
    try {
      const created = await createAstTemplate({
        name: payload.name,
        version: payload.version,
        doc_no: payload.docNo || undefined,
        iri_pattern: payload.iriPattern || undefined,
        schema_json: updated,
        sample_text: payload.sampleText ?? undefined,
        sample_content_json: payload.sampleContent ?? undefined,
      });
      // 创建成功后，把原始示例 .docx 附加为「输出格式模板」（复用已加固的 /sample 端点：
      // 唯一文件名落盘、校验后再提交、提交后清理）。缺此步则新建模板生成报告不会套用模板
      // 格式——创建流程原本只存解析文本/JSON，原始 docx 已随 parse-sample 临时文件删除。
      if (payload.sampleFile) {
        try {
          await uploadTemplateSample(created.id, payload.sampleFile);
        } catch {
          // 模板已创建成功，仅格式附加失败：提示用户可在编辑页「替换」重试，不阻断创建。
          alert("模板已创建，但输出格式模板附加失败，可在模板编辑页重新上传示例文档。");
        }
      }
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
        <OutputTemplateEditor
          key="create"
          schema={{
            ...initialTemplate,
            doc_no: payload.docNo || "",
            source_slots: payload.iriPattern ? [{
              source_slot_id: "source", kind: "document", class_iri: payload.iriPattern,
            }] : [],
          }}
          onSave={(updated) =>
            handleSave(updated as unknown as Record<string, unknown>)
          }
          onCancel={handleCancel}
          saving={saving}
          sampleContentJson={payload.sampleContent}
          sampleText={payload.sampleText}
        />
      </div>
    </div>
  );
}
