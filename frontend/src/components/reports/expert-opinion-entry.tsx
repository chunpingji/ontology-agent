"use client";

import { useId, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { MessageSquarePlus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import {
  createExpertOpinion, exportExpertOpinions, getIdentity, listExpertOpinions,
  type ExpertOpinionCategory, type ExpertOpinionContext, type ExpertOpinionTarget,
} from "@/lib/api";
import { saveBlob } from "./reading-pane";

const categories: Record<ExpertOpinionCategory, string> = {
  general: "总体意见", relationship: "关系错误", attribute: "属性错误", missing: "事实遗漏",
  subject_binding: "主体挂接", negation: "否定与条件", pruning: "疑似误剪枝",
};

function newOpinionKey(): string {
  // getRandomValues also works on the HTTP origins used by intranet deployments.
  return Array.from(crypto.getRandomValues(new Uint8Array(16)),
    (byte) => byte.toString(16).padStart(2, "0")).join("");
}

function errorText(error: unknown) {
  const message = error instanceof Error ? error.message : String(error);
  const body = message.match(/^API \d+:\s*([\s\S]*)$/)?.[1];
  if (body) {
    try {
      const parsed = JSON.parse(body);
      return typeof parsed.detail === "string" ? parsed.detail
        : parsed.error?.message ?? "请求失败，请检查内容后重试。";
    } catch { /* Preserve a readable non-JSON error. */ }
  }
  return message;
}

interface EntryProps {
  target: ExpertOpinionTarget;
  runId?: string;
  sourceHash?: string;
}

export function ExpertOpinionEntry(props: EntryProps) {
  const { username, role } = getIdentity();
  return <OpinionSheet key={JSON.stringify([username, role, props.target, props.runId])} {...props} />;
}

function OpinionSheet(props: EntryProps) {
  const [open, setOpen] = useState(false);
  return <Sheet open={open} onOpenChange={setOpen}>
    <SheetTrigger asChild><Button variant="outline" size="sm">
      <MessageSquarePlus className="mr-2 size-4" />专家意见
    </Button></SheetTrigger>
    <SheetContent className="overflow-y-auto">
      <SheetHeader className="pr-6"><SheetTitle>专家意见</SheetTitle>
        <SheetDescription>记录报告或图谱中的问题、原文位置及修改建议。</SheetDescription>
      </SheetHeader>
      {open && <OpinionPanel {...props} />}
    </SheetContent>
  </Sheet>;
}

function OpinionPanel({ target, runId, sourceHash }: EntryProps) {
  const { username, role } = getIdentity();
  const [session] = useState(newOpinionKey);
  const queryKey = ["expert-opinions", username, role, target, runId, session];
  const source = useQuery({ queryKey: [...queryKey, "context"],
    queryFn: ({ signal }) => listExpertOpinions(target, runId, 0, signal),
    retry: false, refetchOnWindowFocus: false });
  return <div className="space-y-6">
    {source.isPending && <p role="status">正在读取审核上下文…</p>}
    {source.error && <div role="alert" className="space-y-2 text-sm text-destructive">
      <p>{errorText(source.error)}</p>
      <Button variant="outline" onClick={() => void source.refetch()}>重试</Button>
    </div>}
    {source.data && <>
      <p className="break-words text-sm font-medium">{source.data.context.filename}</p>
      {source.data.can_submit ? <OpinionForm initialContext={source.data.context}
        sourceHash={sourceHash} queryKey={queryKey} />
        : <p className="text-sm text-muted-foreground">当前角色可查看自己的意见；提交需高级分析师或 QA 角色。</p>}
      <OpinionHistory target={target} runId={runId} queryKey={queryKey}
        sourceHash={source.data.context.source_hash} />
    </>}
  </div>;
}

function OpinionForm({ initialContext, sourceHash, queryKey }: {
  initialContext: ExpertOpinionContext; sourceHash?: string; queryKey: readonly unknown[];
}) {
  // Keep the reviewed graph immutable even if the worker publishes a newer graph.
  const [context] = useState(initialContext);
  const [category, setCategory] = useState<ExpertOpinionCategory>("general");
  const [location, setLocation] = useState("");
  const [opinion, setOpinion] = useState("");
  const [suggestion, setSuggestion] = useState("");
  const [saved, setSaved] = useState(false);
  const request = useRef<{ payload: string; key: string } | null>(null);
  const inFlight = useRef(false);
  const id = useId();
  const client = useQueryClient();
  const mutation = useMutation({ mutationFn: createExpertOpinion, onSuccess: () => {
    request.current = null;
    setOpinion(""); setSuggestion(""); setSaved(true);
    void client.invalidateQueries({ queryKey });
  } });
  const stale = context.source_hash !== initialContext.source_hash
    || (!!sourceHash && context.source_hash !== sourceHash);
  return <form className="space-y-3" onSubmit={async (event) => {
    event.preventDefault();
    if (!opinion.trim() || stale || inFlight.current) return;
    const body = { context, category, location: location.trim(), opinion: opinion.trim(), suggestion: suggestion.trim() };
    const payload = JSON.stringify(body);
    if (request.current?.payload !== payload) request.current = { payload, key: newOpinionKey() };
    inFlight.current = true; setSaved(false);
    try { await mutation.mutateAsync({ ...body, request_key: request.current.key }); }
    catch { /* Mutation error is shown below; keep the draft and request key for retry. */ }
    finally { inFlight.current = false; }
  }}>
    {stale && <p role="alert" className="text-sm text-destructive">原文版本已变化，请保留意见内容并刷新预览后重新打开入口。</p>}
    <fieldset disabled={mutation.isPending} className="space-y-3">
      <div className="space-y-1"><label htmlFor={`${id}-category`} className="text-sm">意见类型</label>
        <select id={`${id}-category`} value={category} onChange={(e) => setCategory(e.target.value as ExpertOpinionCategory)}
          className="flex h-9 w-full rounded-md border border-input bg-background px-3 text-sm">
          {Object.entries(categories).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select></div>
      <div className="space-y-1"><label htmlFor={`${id}-location`} className="text-sm">原文位置（选填）</label>
        <Input id={`${id}-location`} value={location} maxLength={500} onChange={(e) => setLocation(e.target.value)}
          placeholder="如：第 3 节、设备表第 2 行，或相关实体名称" /></div>
      <div className="space-y-1"><label htmlFor={`${id}-opinion`} className="text-sm">专家意见（必填）</label>
        <Textarea id={`${id}-opinion`} required maxLength={6000} rows={4} value={opinion}
          onChange={(e) => { setOpinion(e.target.value); setSaved(false); }} placeholder="说明发现的问题及原文依据" /></div>
      <div className="space-y-1"><label htmlFor={`${id}-suggestion`} className="text-sm">修改建议（选填）</label>
        <Textarea id={`${id}-suggestion`} maxLength={4000} rows={2} value={suggestion}
          onChange={(e) => setSuggestion(e.target.value)} /></div>
      <Button type="submit" disabled={stale || !opinion.trim()}>{mutation.isPending ? "正在保存…" : "保存意见"}</Button>
    </fieldset>
    {saved && <p role="status" className="text-sm">意见已保存。</p>}
    {mutation.error && <p role="alert" className="text-sm text-destructive">{errorText(mutation.error)}</p>}
    <p className="text-xs text-muted-foreground">保存后保留原记录，补充时可新增意见。意见供后续审核使用，提交后不会自动修改图谱或批准剪枝校准。</p>
  </form>;
}

function OpinionHistory({ target, runId, queryKey, sourceHash }: {
  target: ExpertOpinionTarget; runId?: string; queryKey: readonly unknown[]; sourceHash: string;
}) {
  const [offset, setOffset] = useState(0);
  const history = useQuery({ queryKey: [...queryKey, "history", offset],
    queryFn: ({ signal }) => listExpertOpinions(target, runId, offset, signal),
    retry: false, refetchOnWindowFocus: false });
  const download = useMutation({ mutationFn: () => exportExpertOpinions(target), onSuccess: (data) => {
    saveBlob(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }), "专家意见.json");
  } });
  return <section aria-label="我的已保存意见" className="space-y-3 border-t pt-4">
    <div className="flex items-center justify-between gap-2"><h3 className="text-sm font-medium">我的已保存意见{history.data ? `（${history.data.total}）` : ""}</h3>
      <Button size="sm" variant="outline" disabled={download.isPending || !history.data?.total}
        onClick={() => download.mutate()}>{download.isPending ? "正在导出…" : "导出 JSON"}</Button></div>
    {download.error && <p role="alert" className="text-sm text-destructive">{errorText(download.error)}</p>}
    {history.isPending && <p role="status" className="text-sm">正在读取意见…</p>}
    {history.error && <div role="alert"><p>{errorText(history.error)}</p>
      <Button variant="outline" onClick={() => void history.refetch()}>重试读取意见</Button></div>}
    {history.data?.total === 0 && <p className="text-sm text-muted-foreground">暂无已保存意见。</p>}
    {history.data?.items.map((item) => <article key={item.opinion_id} className="space-y-2 rounded-md border p-3 text-sm">
      <p className="font-medium">{categories[item.category]}</p>
      <p className="text-xs text-muted-foreground">{item.author} · {new Date(item.created_at).toLocaleString("zh-CN")}
        {item.context.source_hash !== sourceHash && " · 旧原文版本"}</p>
      {item.location && <p className="break-words">位置：{item.location}</p>}
      <p className="whitespace-pre-wrap break-words">{item.opinion}</p>
      {item.suggestion && <p className="whitespace-pre-wrap break-words">建议：{item.suggestion}</p>}
    </article>)}
    {history.data && (offset > 0 || history.data.total > history.data.limit) && <div className="flex items-center gap-2">
      <Button variant="outline" size="sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - history.data!.limit))}>上一页</Button>
      <span className="text-xs">第 {Math.floor(offset / history.data.limit) + 1} 页</span>
      <Button variant="outline" size="sm" disabled={offset + history.data.limit >= history.data.total}
        onClick={() => setOffset(offset + history.data!.limit)}>下一页</Button>
    </div>}
  </section>;
}
