"use client";

import { useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { getIdentity } from "@/lib/api";
import {
  downloadArtifact, reportGet, reportPost, requestKey, type FrozenRecord,
  type OutputNode, type ReportRun, type SigningSession,
} from "@/lib/reporting-v2";
import { OutputPreview } from "./output-preview";

export function ReportSigningPanel({ run }: { run: ReportRun }) {
  const [contentId, setContentId] = useState("");
  const [session, setSession] = useState<SigningSession | null>(null);
  const [reason, setReason] = useState("");
  const [password, setPassword] = useState("");
  const [workflow, setWorkflow] = useState("");
  const [slot, setSlot] = useState("");
  const [meaning, setMeaning] = useState("");
  const [final, setFinal] = useState<FrozenRecord<{ final_ast: OutputNode }> & { artifacts: FrozenRecord[] } | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const requestKeys = useRef(new Map<string, string>());
  const user = getIdentity();
  const contents = useQuery({ queryKey: ["report-contents", run.run_id],
    queryFn: () => reportGet<FrozenRecord[]>("report-runs/" + run.run_id + "/content-versions") });
  const selected = contents.data?.find((c) => c.id === contentId) ?? contents.data?.at(-1);
  const reviews = useQuery({ queryKey: ["report-reviews", selected?.id], enabled: !!selected,
    queryFn: () => reportGet<FrozenRecord[]>("report-content-versions/" + selected!.id + "/reviews") });
  const sessions = useQuery({ queryKey: ["report-sessions", selected?.id], enabled: !!selected,
    queryFn: () => reportGet<{ id: string; status: string }[]>("report-content-versions/" + selected!.id + "/signing-sessions") });
  async function post<T>(path: string, payload: Record<string, unknown>): Promise<T> {
    const key = JSON.stringify([path, Object.fromEntries(Object.entries(payload).filter(([k]) => k !== "password"))]);
    if (!requestKeys.current.has(key)) requestKeys.current.set(key, requestKey());
    return reportPost<T>(path, { ...payload, idempotency_key: requestKeys.current.get(key) });
  }
  async function action(fn: () => Promise<void>) {
    setBusy(true); setError("");
    try { await fn(); } catch (e) {
      setError(e instanceof Error ? e.message : "操作失败");
      if (session) {
        try { setSession(await reportGet<SigningSession>("report-signing-sessions/" + session.id)); }
        catch { /* Preserve the original failure; recovery is available after reconnect. */ }
      }
    }
    finally { setBusy(false); }
  }
  const selectedSlot = session?.policy.signature_slots.find((s) => s.signature_slot_id === slot);
  return <section className="border rounded p-4 space-y-4">
    <h3 className="font-semibold">正文审核与签署</h3>
    <Button disabled={busy} variant="outline" onClick={() => action(async () => {
      const content = await post<FrozenRecord>("report-runs/" + run.run_id + "/content-versions", {
        expected_body_hash: run.body_hash, attempt: run.attempt,
      }); setContentId(content.id); setSession(null); await contents.refetch();
    })}>固定此版正文</Button>
    {selected && <>
      <select aria-label="正文版本" className="border rounded p-2 ml-3" value={selected.id}
        onChange={(e) => { setContentId(e.target.value); setSession(null); setFinal(null); }}>
        {contents.data?.map((c, i) => <option key={c.id} value={c.id}>正文 {i + 1} · {c.content_hash.slice(0, 12)}</option>)}
      </select>
      <p className="text-xs break-all">正文版本哈希：{selected.content_hash}</p>
      <label className="block text-sm">审核意见<input className="block w-full rounded border p-2" value={reason} onChange={(e) => setReason(e.target.value)} /></label>
      <div className="flex gap-2">{(["approved", "rejected"] as const).map((decision) => <Button key={decision}
        disabled={busy || !reason.trim()} variant={decision === "approved" ? "default" : "outline"} onClick={() => action(async () => {
          await post("report-content-versions/" + selected.id + "/reviews", {
            expected_content_hash: selected.content_hash, decision, reason,
          }); await reviews.refetch();
        })}>{decision === "approved" ? "审核通过" : "退回修改"}</Button>)}</div>
      {reviews.data?.map((r) => <p key={r.id} className="text-sm">{r.actor} · {String(r.payload.decision)} · {String(r.payload.reason)}</p>)}
      <label className="block text-sm">工作流记录（策略要求时填写）<input className="block w-full rounded border p-2"
        value={workflow} onChange={(e) => setWorkflow(e.target.value)} /></label>
      <Button disabled={busy} variant="outline" onClick={() => action(async () => {
        setSession(await post<SigningSession>("report-content-versions/" + selected.id + "/signing-sessions", {
          expected_content_hash: selected.content_hash, review_id: reviews.data?.at(-1)?.id,
          workflow_ref: workflow || null,
          parent_ref: session?.id ?? null,
        })); await sessions.refetch();
      })}>{session ? "创建关联签署会话" : "创建签署会话"}</Button>
      {!!sessions.data?.length && <select aria-label="已有签署会话" className="border rounded p-2 ml-3" value={session?.id ?? ""}
        onChange={(e) => { const id = e.target.value; if (id) action(async () => setSession(await reportGet<SigningSession>("report-signing-sessions/" + id))); }}>
        <option value="">查看已有会话</option>{sessions.data.map((s) => <option key={s.id} value={s.id}>{s.id.slice(0, 12)} · {s.status}</option>)}
      </select>}
    </>}
    {session && <div className="border-t pt-4 space-y-3">
      <p>签署会话：{session.status} · 修订 {session.revision_no}</p>
      {session.signatures.map((s) => <div key={s.id} className="text-sm">
        <p>{String(s.payload.display_name)} · {String(s.payload.meaning)} · {String(s.payload.signed_at)}</p>
        {session.status === "open" && s.actor === user.username && <Button size="sm" variant="ghost"
          disabled={busy || !password || !reason.trim()} onClick={() => action(async () => {
            try { await post("report-signing-sessions/" + session.id + "/signature-events", {
              signature_ref: s.id, reason, password, expected_signature_revision: session.revision_no,
            }); setSession(await reportGet<SigningSession>("report-signing-sessions/" + session.id)); }
            finally { setPassword(""); }
          })}>撤销签名</Button>}
      </div>)}
      {session.events.map((event) => <p key={event.id} className="text-sm text-amber-800">签名已撤销：{String(event.payload.reason)}</p>)}
      {session.status === "open" && <>
        <select aria-label="签名位置" className="border rounded p-2" value={slot} onChange={(e) => { setSlot(e.target.value); setMeaning(""); }}>
          <option value="">选择签名位置</option>{session.policy.signature_slots.map((s) => <option key={s.signature_slot_id}
            value={s.signature_slot_id}>{s.region_id} · {s.role}</option>)}
        </select>
        <select aria-label="签署含义" className="border rounded p-2 ml-2" value={meaning} onChange={(e) => setMeaning(e.target.value)}>
          <option value="">选择签署含义</option>{selectedSlot?.meanings.map((m) => <option key={m}>{m}</option>)}
        </select>
        <label className="block text-sm">当前账户 {user.username} 的密码<input type="password" autoComplete="current-password"
          className="block border rounded p-2 mt-1" value={password} onChange={(e) => setPassword(e.target.value)} /></label>
        <Button disabled={busy || !slot || !meaning || !password} onClick={() => action(async () => {
          try { await post("report-signing-sessions/" + session.id + "/signatures", {
            content_hash: session.content_hash, signature_slot_id: slot, meaning, password,
            expected_signature_revision: session.revision_no,
          }); setSession(await reportGet<SigningSession>("report-signing-sessions/" + session.id)); }
          finally { setPassword(""); }
        })}>验证身份并签署</Button>
      </>}
      {session.status === "open" && <Button className="ml-2" variant="outline" disabled={busy || run.material_status !== "ready"} onClick={() => action(async () => {
        setFinal(await post("report-signing-sessions/" + session.id + "/envelopes", {
          content_hash: session.content_hash, signature_revision: session.revision_no, purpose: "formal",
        })); setSession(await reportGet<SigningSession>("report-signing-sessions/" + session.id));
      })}>封装正式报告</Button>}
      {session.status === "open" && <Button variant="outline" disabled={busy} onClick={() => action(async () => {
        setFinal(await post("report-signing-sessions/" + session.id + "/envelopes", {
          content_hash: session.content_hash, signature_revision: session.revision_no, purpose: "draft",
        })); setSession(await reportGet<SigningSession>("report-signing-sessions/" + session.id));
      })}>封装当前签署进度</Button>}
      {session.envelope_request?.actor === user.username && !session.envelope_ids.length && <Button disabled={busy}
        onClick={() => action(async () => {
          setFinal(await reportPost("report-signing-sessions/" + session.id + "/envelopes", session.envelope_request!.payload));
          setSession(await reportGet<SigningSession>("report-signing-sessions/" + session.id));
        })}>恢复冻结的封装请求</Button>}
      {session.envelope_ids.map((id) => <Button key={id} variant="outline" disabled={busy}
        onClick={() => action(async () => setFinal(await reportGet("report-signing-envelopes/" + id)))}>查看已保存的封装</Button>)}
    </div>}
    {final && <><OutputPreview ast={final.payload.final_ast} />
      {final.artifacts.map((a) => <Button key={a.id} onClick={() => action(() => downloadArtifact(run.run_id, a.id))}>下载已签署报告</Button>)}</>}
    {error && <p role="alert" className="text-destructive break-all">{error}</p>}
  </section>;
}
