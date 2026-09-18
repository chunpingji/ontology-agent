"use client";

import { useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import {
  getDocumentPropertyReviews, getDocumentPropertyRepairs, getIdentity, VersionConflictError,
  type DocumentAnalysisRun, type DocumentPropertyReviewList, type DocumentPropertyRepairList,
  type DocumentPropertyReviewInput, type DocumentPropertyReviewReceipt,
  type DocumentPropertyRepairInput, type DocumentPropertyRepairReceipt,
  type DocumentPropertyReviewReason,
} from "@/lib/api";
import {
  newPropertyReviewRequestKey, propertyReviewHead, propertyReviewError,
  propertyRepairStatus, propertyRepairReason, PROPERTY_REVIEW_REASONS, type PropertyReviewTarget,
} from "@/lib/document-property-review";

interface Props {
  target: PropertyReviewTarget;
  run: DocumentAnalysisRun;
  onClose: () => void;
  onRefresh: () => void;
  onSource: (ref: string) => void;
  review: (runId: string, input: DocumentPropertyReviewInput) => Promise<DocumentPropertyReviewReceipt>;
  repair: (runId: string, input: DocumentPropertyRepairInput) => Promise<DocumentPropertyRepairReceipt>;
}

export function PropertyReviewDialog(props: Props) {
  const { target } = props;
  const { username, role } = getIdentity();
  const [session] = useState(newPropertyReviewRequestKey);
  const key = ["document-property-review", username, role, target.runId, session];
  const history = useQuery({ queryKey: [...key, "reviews"],
    queryFn: ({ signal }) => getDocumentPropertyReviews(target.runId, signal),
    retry: false, refetchOnWindowFocus: false });
  const repairs = useQuery({ queryKey: [...key, "repairs"],
    queryFn: ({ signal }) => getDocumentPropertyRepairs(target.runId, signal),
    retry: false, refetchOnWindowFocus: false,
    refetchInterval: (query) => query.state.data?.items.some((item) =>
      ["queued", "running"].includes(item.status)) ? 2500 : false,
  });
  return <Sheet open onOpenChange={(open) => { if (!open) props.onClose(); }}>
    <SheetContent className="overflow-y-auto" data-tree-action="property-review">
      <SheetHeader className="pr-6"><SheetTitle>属性专家审核</SheetTitle>
        <SheetDescription>核对当前属性及原文依据。确认只记录人工审核状态。</SheetDescription>
      </SheetHeader>
      <div className="mt-4 space-y-3">
        <p className="break-words font-medium">{target.subjectLabel} · {target.property.predicate_label}</p>
        <p className="break-words">原始值：{target.property.raw_value}</p>
        {target.property.normalized_value != null && <p className="text-sm text-muted-foreground">
          规范化值：{String(target.property.normalized_value)} {target.property.unit}</p>}
        <div className="flex flex-wrap gap-2">
          {[...new Set([...target.property.source_selection_refs.value,
            ...target.property.source_selection_refs.subject,
            ...target.property.source_selection_refs.predicate_bridge,
            ...(target.property.source_selection_refs.unit ?? []),
            ...target.property.source_selection_refs.condition,
            ...target.property.source_selection_refs.counterevidence])].map((ref, index) =>
            <Button key={ref} size="sm" variant="link" onClick={() => props.onSource(ref)}>
              查看原文依据 {index + 1}
            </Button>)}
        </div>
        {(history.isPending || repairs.isPending) && <p role="status" className="text-sm">正在读取审核记录…</p>}
        {(history.error || repairs.error) && <div role="alert" className="space-y-2 text-sm text-destructive">
          <p>{propertyReviewError(history.error ?? repairs.error)}</p>
          <Button variant="outline" onClick={() => { void history.refetch(); void repairs.refetch(); }}>重试读取审核记录</Button>
        </div>}
        {history.data && repairs.data && <ReviewForm {...props} history={history.data} repairs={repairs.data}
          refreshReviews={() => { props.onRefresh(); void history.refetch(); void repairs.refetch(); }} />}
      </div>
    </SheetContent>
  </Sheet>;
}

function ReviewForm({ target, run, history, repairs, review, repair, refreshReviews }: Props & {
  history: DocumentPropertyReviewList; repairs: DocumentPropertyRepairList; refreshReviews: () => void;
}) {
  const [initialHead] = useState(() => propertyReviewHead(history.heads, target.property));
  const [reason, setReason] = useState("");
  const [reasonCode, setReasonCode] = useState<DocumentPropertyReviewReason>("incorrect_value");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [saved, setSaved] = useState<DocumentPropertyReviewReceipt | null>(null);
  const [repairReceipt, setRepairReceipt] = useState<DocumentPropertyRepairReceipt | null>(null);
  const [hasReviewAttempt, setHasReviewAttempt] = useState(false);
  const [repairAttempted, setRepairAttempted] = useState(false);
  const [caller] = useState(() => JSON.stringify(getIdentity()));
  const request = useRef<{ payload: string; body: DocumentPropertyReviewInput; withRepair: boolean } | null>(null);
  const repairRequest = useRef<DocumentPropertyRepairInput | null>(null);
  const inFlight = useRef(false);
  const currentHead = propertyReviewHead(history.heads, target.property);
  const recorded = saved?.review ?? currentHead;
  const rejectedReceipt = saved ?? (currentHead?.decision === "rejected" ? { review: currentHead, run } : null);
  const operations = repairs.items.filter((item) => item.candidate_id === target.property.candidate_id
    && item.candidate_revision === target.property.revision && item.review_id === recorded?.review_id);
  const latestOperation = operations.find((item) => item.operation_id === repairReceipt?.operation.operation_id)
    ?? repairReceipt?.operation ?? operations[0];
  const repairReason = latestOperation ? propertyRepairReason(latestOperation.result) : "";
  const stale = !saved && (run.recognition_run_id !== target.runId
    || run.run_revision !== target.runRevision || history.run_revision !== target.runRevision
    || run.identities.graph_snapshot_id !== target.graphSnapshotId
    || (currentHead?.revision ?? 0) !== (initialHead?.revision ?? 0));
  const startRepair = async (receipt: DocumentPropertyReviewReceipt) => {
    if (JSON.stringify(getIdentity()) !== caller) throw new Error("登录身份已变化，请重新打开审核入口");
    if (!repairRequest.current) repairRequest.current = {
      request_key: newPropertyReviewRequestKey(), review_id: receipt.review.review_id,
      expected_run_revision: receipt.run.run_revision,
    };
    setRepairAttempted(true);
    setRepairReceipt(await repair(target.runId, repairRequest.current));
  };
  const submit = async (decision: "accepted" | "rejected", withRepair: boolean) => {
    if (inFlight.current || stale || !history.can_review || (decision === "rejected" && !reason.trim())) return;
    inFlight.current = true; setBusy(true); setError(null);
    try {
      const fields = { expected_run_revision: target.runRevision, graph_snapshot_id: target.graphSnapshotId,
        candidate_id: target.property.candidate_id, candidate_revision: target.property.revision,
        expected_review_revision: initialHead?.revision ?? 0, decision,
        reason_code: decision === "rejected" ? reasonCode : "other" as const, reason: reason.trim() };
      const payload = JSON.stringify(fields);
      if (request.current?.payload !== payload) request.current = {
        payload, body: { ...fields, request_key: newPropertyReviewRequestKey() }, withRepair,
      };
      request.current.withRepair = withRepair;
      setHasReviewAttempt(true);
      if (JSON.stringify(getIdentity()) !== caller) throw new Error("登录身份已变化，请重新打开审核入口");
      const receipt = await review(target.runId, request.current.body);
      setSaved(receipt);
      if (withRepair) await startRepair(receipt);
    } catch (caught) { setError(caught); }
    finally { inFlight.current = false; setBusy(false); }
  };
  const retryRepair = async () => {
    if (inFlight.current || !rejectedReceipt || !repairs.can_repair) return;
    inFlight.current = true; setBusy(true); setError(null);
    setSaved(rejectedReceipt);
    try { await startRepair(rejectedReceipt); }
    catch (caught) { setError(caught); }
    finally { inFlight.current = false; setBusy(false); }
  };
  const retryReview = async () => {
    if (inFlight.current || !request.current) return;
    inFlight.current = true; setBusy(true); setError(null);
    try {
      if (JSON.stringify(getIdentity()) !== caller) throw new Error("登录身份已变化，请重新打开审核入口");
      const receipt = await review(target.runId, request.current.body);
      setSaved(receipt);
      if (request.current.withRepair) await startRepair(receipt);
    } catch (caught) { setError(caught); }
    finally { inFlight.current = false; setBusy(false); }
  };
  return <div className="space-y-4">
    {recorded && <div className="space-y-1 rounded border p-3 text-sm">
      <p className="font-medium">{recorded.decision === "accepted" ? "专家已确认" : "专家已驳回"}</p>
      {recorded.reason && <p className="whitespace-pre-wrap break-words">理由：{recorded.reason}</p>}
      <p className="text-xs text-muted-foreground">{recorded.author} · {new Date(recorded.created_at).toLocaleString("zh-CN")}</p>
    </div>}
    {stale && <p role="alert" className="text-sm text-destructive">
      图谱、运行或审核版本已变化。理由草稿已保留，请核对最新属性后重新打开审核入口。
    </p>}
    {!saved && <fieldset disabled={busy || stale || !history.can_review} className="space-y-3">
      <label className="block space-y-1 text-sm">驳回类型
        <select aria-label="驳回类型" value={reasonCode}
          onChange={(event) => setReasonCode(event.target.value as DocumentPropertyReviewReason)}
          className="w-full rounded border bg-background p-2">
          {Object.entries(PROPERTY_REVIEW_REASONS).map(([value, label]) =>
            <option key={value} value={value}>{label}</option>)}
        </select>
      </label>
      <label className="block space-y-1 text-sm">审核理由（驳回必填）
        <Textarea aria-label="审核理由（驳回必填）" value={reason} rows={4} maxLength={4000}
          onChange={(event) => setReason(event.target.value)} placeholder="说明错误及对应原文依据" />
      </label>
      <div className="flex flex-wrap gap-2">
        <Button onClick={() => void submit("accepted", false)}>确认属性</Button>
        <Button variant="outline" disabled={!reason.trim()} onClick={() => void submit("rejected", false)}>驳回属性</Button>
        <Button variant="outline" disabled={!reason.trim() || !history.can_repair}
          onClick={() => void submit("rejected", true)}>驳回并局部重识别</Button>
      </div>
    </fieldset>}
    {!history.can_review && !saved && <p className="text-sm text-muted-foreground">
      当前运行或角色不允许审核；需高级分析师或 QA 审核自己的静止运行。
    </p>}
    {saved && <p role="status" className="text-sm">{saved.review.decision === "accepted"
      ? "人工确认已保存；系统证明资格保持原判定。" : "驳回已保存，原文及原结论保留。"}</p>}
    {error != null && !(saved && latestOperation) && <div role="alert" className="space-y-2 text-sm text-destructive">
      <p>{saved ? "驳回已保存，局部重识别尚未确认启动：" : "审核提交失败："}{propertyReviewError(error)}</p>
      {error instanceof VersionConflictError && <p>请刷新状态并核对当前版本，不能自动将草稿绑定到新结论。</p>}
    </div>}
    {error != null && !saved && hasReviewAttempt && !(error instanceof VersionConflictError) && (
      <Button variant="outline" disabled={busy} onClick={() => void retryReview()}>重试原审核提交</Button>
    )}
    {rejectedReceipt && !repairReceipt && !latestOperation && <div className="space-y-2">
      <Button variant="outline" disabled={busy || !repairs.can_repair} onClick={() => void retryRepair()}>
        {repairAttempted ? "重试局部重识别" : "局部重识别该属性"}
      </Button>
      {error instanceof VersionConflictError && <Button variant="ghost" disabled={busy} onClick={() => {
        repairRequest.current = null;
        setSaved({ ...rejectedReceipt, run });
        setError(null); refreshReviews();
      }}>刷新修复状态</Button>}
    </div>}
    {busy && <p role="status" className="text-sm">正在提交，请稍候…</p>}
    {latestOperation && <div className="space-y-2 rounded border p-3 text-sm" role="status">
      <p>{propertyRepairStatus(latestOperation.status, run.status)}</p>
      {repairReason && <p>{repairReason}</p>}
      {Array.isArray(latestOperation.result.replacement_candidate_refs)
        && latestOperation.result.replacement_candidate_refs.length > 0 && <p>
          形成 {latestOperation.result.replacement_candidate_refs.length} 项新候选，请在全部候选中核对并审核。
        </p>}
      <p className="text-xs text-muted-foreground">本次仅处理被驳回属性的相关原文，其他结论保留。</p>
    </div>}
    <p className="text-xs text-muted-foreground">专家理由用于指导重新核验，新结论仍须由原文证明。修复结束不代表新结果已获人工确认。</p>
  </div>;
}
