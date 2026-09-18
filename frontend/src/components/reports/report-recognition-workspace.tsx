"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useRecognitionContext } from "@/components/analysis/use-template-finder";
import { FinderWorkspace } from "@/components/analysis/finder-document-graph-panel";
import { BatchDemoWorkspaceGate } from "./batch-demo-workspace";
import { reportDocumentError, ReportWordWorkspace } from "./report-word-workspace";
import { ReportFinderWorkspace } from "./report-finder-workspace";

export function ReportRecognitionWorkspace({ documentIri, templateId }: {
  documentIri: string; templateId?: string | null;
}) {
  const context = useRecognitionContext(documentIri, templateId);
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  if (context.isLoading) return <p role="status">正在读取模板识别配置…</p>;
  if (context.error) return <p role="alert" className="text-destructive">{reportDocumentError(context.error)}</p>;
  const data = context.data;
  if (!data) return null;
  const selected = data.selected;
  return <div className="flex min-h-0 flex-1 flex-col gap-3">
    <div className="flex items-center gap-3 text-sm">
      <label htmlFor="recognition-template">模板上下文</label>
      <select id="recognition-template" className="max-w-md rounded border bg-background p-2"
        value={selected?.template_id ?? ""} onChange={(event) => {
          const query = new URLSearchParams(params.toString());
          if (event.target.value) query.set("template_id", event.target.value);
          else query.delete("template_id");
          router.replace(`${pathname}?${query}`);
        }}>
        {!data.selection_locked && <option value="">{data.selection_required ? "请选择模板" : "文档默认上下文"}</option>}
        {data.templates.map((option) => <option key={option.template_id} value={option.template_id}>
          {option.name} · {data.selection_locked ? option.version : `V${option.schema_version}`} · {option.recognition_mode === "finder_legacy" ? "本体指引1.0" :
            option.recognition_mode === "static_demo" ? "静态演示" : "本体指引识别"}
        </option>)}
      </select>
    </div>
    {data.selection_required ? <p>该原件关联多个模板，请先选择识别上下文。</p>
      : selected?.recognition_mode === "finder_legacy"
        ? data.selection_locked
          ? <ReportFinderWorkspace key={`${selected.template_id}:${data.source_job_id}`}
            templateId={selected.template_id} sourceJobId={data.source_job_id} documentIri={documentIri} />
          : <FinderWorkspace key={`${selected.template_id}:${data.source_job_id}`} templateId={selected.template_id}
            sourceJobId={data.source_job_id} documentIri={documentIri} />
        : selected?.recognition_mode === "ontology_guided"
          ? <ReportWordWorkspace key={`${documentIri}:${selected.template_id}`} documentIri={documentIri}
            templateId={selected.template_id} />
          : <BatchDemoWorkspaceGate key={documentIri} documentIri={documentIri} />}
  </div>;
}
