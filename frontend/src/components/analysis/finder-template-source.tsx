"use client";

import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { listDocuments, uploadDefaultSource, type AstTemplateDTO } from "@/lib/api";
import { useIdentity } from "@/lib/use-identity";
import { FinderWorkspace } from "./finder-document-graph-panel";
import { reportDocumentError } from "@/components/reports/report-word-workspace";

export function FinderTemplateSource({ template }: { template: AstTemplateDTO }) {
  const { identity, role } = useIdentity();
  const client = useQueryClient();
  const [selected, setSelected] = useState("");
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const docs = useQuery({ queryKey: ["finder-source-documents", identity.username, role],
    queryFn: ({ signal }) => listDocuments(undefined, 100, signal) });
  const choices = (docs.data?.items ?? []).filter((doc) => doc.class_iri === template.iri_pattern)
    .flatMap((doc) => {
      const props = doc.properties_json ?? {};
      const id = ["job_id", "jobId", "source_job_id", "extraction_job_id", "hasJob", "sourceJob"]
        .map((key) => props[key]).find((value) => typeof value === "string" && value);
      return typeof id === "string" ? [{ id, label: doc.label_zh || doc.label_en || doc.iri }] : [];
    });
  const source = selected || template.default_source_job_id;
  return <section className="flex min-h-[600px] flex-col gap-3" aria-label="本体指引1.0源文档">
    <div className="flex flex-wrap items-center gap-3">
      <span className="text-sm">模板 V{template.schema_version ?? 1} · 本体指引1.0</span>
      <select aria-label="本体指引1.0源文档" className="rounded border bg-background p-2 text-sm"
        value={source ?? ""} onChange={(event) => setSelected(event.target.value)}>
        <option value="">选择源文档</option>
        {template.default_source_job_id && <option value={template.default_source_job_id}>
          默认源：{template.default_source_filename}</option>}
        {choices.filter((item) => item.id !== template.default_source_job_id).map((item) =>
          <option key={item.id} value={item.id}>{item.label}</option>)}
      </select>
      {role === "senior_analyst" && <label className="text-sm">
        {uploading ? "正在登记原件…" : "上传/替换默认源"}
        <input aria-label="上传本体指引1.0默认源" type="file" accept=".doc,.docx" disabled={uploading}
          className="ml-2 max-w-64 text-xs" onChange={async (event) => {
            const file = event.target.files?.[0];
            if (!file) return;
            setUploading(true); setError(null);
            try {
              const result = await uploadDefaultSource(template.id, file);
              setSelected(result.default_source_job_id ?? "");
              await client.invalidateQueries({ queryKey: ["ast-template", template.id] });
            } catch (failure) { setError(failure); }
            finally { setUploading(false); }
          }} />
      </label>}
    </div>
    {(error || docs.error) && <p role="alert" className="text-destructive">{reportDocumentError(error || docs.error)}</p>}
    {source ? <FinderWorkspace key={`${template.id}:${source}`} templateId={template.id} sourceJobId={source} />
      : <p className="text-sm text-muted-foreground">上传或选择登记原件后，点击“开始本体指引1.0识别”。</p>}
  </section>;
}
