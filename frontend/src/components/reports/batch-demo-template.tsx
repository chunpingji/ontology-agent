"use client";

import { useState } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { ClipboardList, ExternalLink, Loader2 } from "lucide-react";
import { getBatchDemoTemplate, getIdentity } from "@/lib/api";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { BatchDemoWorkspace } from "./batch-demo-workspace";
import { BatchRecordDrawer } from "./batch-record-drawer";
import { BatchReportPreview } from "./batch-report-preview";
import { reportDocumentError } from "./report-word-workspace";

export function BatchDemoTemplate({ templateId }: { templateId: string }) {
  const { username, role } = getIdentity();
  const query = useQuery({
    queryKey: ["batch-demo", "template", username, role, templateId],
    queryFn: ({ signal }) => getBatchDemoTemplate(templateId, signal),
    retry: false, refetchOnWindowFocus: false,
  });
  const [open, setOpen] = useState(false);
  const data = query.data;
  if (query.isLoading) return <p className="flex items-center gap-2 p-6 text-sm"><Loader2 className="size-4 animate-spin" />正在读取共享演示数据…</p>;
  if (query.error || !data) return <Alert variant="destructive" className="m-6 w-auto"><AlertDescription>
    {reportDocumentError(query.error)}<Button variant="outline" onClick={() => void query.refetch()}>重新读取</Button>
  </AlertDescription></Alert>;
  return <div className="flex h-full min-h-0 flex-col gap-4 p-4">
    <header className="flex flex-wrap items-start justify-between gap-3">
      <div className="min-w-0 space-y-1">
        <h1 className="flex flex-wrap items-center gap-2 text-lg font-semibold">{data.template.name}<Badge variant="secondary">静态演示</Badge></h1>
        <p className="text-sm text-muted-foreground">与报告中心共享原件、关系图谱和生成结果 · CMCReport · v{data.template.version}</p>
      </div>
      <div className="flex flex-wrap gap-2">
        <Button variant="outline" asChild><Link href={{ pathname: `/reports/${encodeURIComponent(data.document_iri)}`,
          query: { kind: "uploaded-document", iri: data.document_iri, title: data.graph.source_filename, type: "CMCReport" } }}><ExternalLink />报告中心原件</Link></Button>
        <Button onClick={() => setOpen(true)}><ClipboardList />生成批记录报告</Button>
      </div>
    </header>
    <Tabs defaultValue="source" className="flex min-h-0 flex-1 flex-col">
      <TabsList className="shrink-0"><TabsTrigger value="source">原文与关系图谱</TabsTrigger><TabsTrigger value="contract">模板契约</TabsTrigger><TabsTrigger value="preview">报告预览</TabsTrigger></TabsList>
      <TabsContent value="source" className="min-h-0 flex-1 overflow-auto"><div className="flex h-full min-h-0 flex-col"><BatchDemoWorkspace data={data} /></div></TabsContent>
      <TabsContent value="contract" className="min-h-0 flex-1 overflow-auto rounded-lg border p-5">
        <h2 className="text-base font-semibold">批记录内容与输入校验</h2>
        <p className="my-3 text-sm text-muted-foreground">{data.validation.checks.filter((check) => check.passed).length}/{data.validation.checks.length} 项通过。按以下关系校验输入，再填入批生产记录样例版式。</p>
        {data.template.layout && <p className="mb-4 text-sm">导出版式：<Link className="text-primary underline" href={`/settings/ast-templates/${data.template.layout.reference_template_id}`}>{data.template.layout.reference_name} · 模板样例</Link>（封面、检查表、工艺操作表）</p>}
        <div className="overflow-x-auto"><table className="w-full text-left text-sm">
          <thead className="border-b bg-muted/40"><tr><th className="p-3">输入内容</th><th className="p-3">数据来源关系</th><th className="p-3">输入校验</th></tr></thead>
          <tbody>{data.validation.checks.map((check) => <tr key={check.id} className="border-b align-top">
            <td className="p-3 font-medium">{check.label}</td>
            <td className="p-3"><span className="break-words font-mono text-xs">{check.path.map((p) => p.split("/").pop()).join(" → ")}</span></td>
            <td className="p-3">{check.passed ? `${check.count} 个实体 · 通过` : <span className="text-destructive">{check.errors.join("；")}</span>}</td>
          </tr>)}</tbody>
        </table></div>
        <h3 className="mt-6 font-medium">打印后手工填写</h3><p className="mt-2 text-sm text-muted-foreground">{data.template.manual_fields.join("、")}；各工艺操作的实际记录及操作/复核签名。</p>
      </TabsContent>
      <TabsContent value="preview" className="min-h-0 flex-1 overflow-auto rounded-lg border bg-muted/30 p-5">
        <p className="mb-4 text-sm text-muted-foreground">{data.latest_report ? "已保存的最新生成结果，与报告中心和下载 Word 一致。" : "使用共享静态数据填充的模板样例；点击「生成批记录报告」可生成并保存 Word。"}</p>
        <article className="mx-auto max-w-4xl bg-background p-6 shadow-sm"><BatchReportPreview node={data.latest_report?.body_ast ?? data.preview_ast} /></article>
      </TabsContent>
    </Tabs>
    <BatchRecordDrawer key={`${data.document_iri}:${data.graph_hash}:${data.template_hash}`} data={data} open={open} onOpenChange={setOpen} />
  </div>;
}
