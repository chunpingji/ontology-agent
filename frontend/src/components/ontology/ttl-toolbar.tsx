"use client";

import { useEffect, useState } from "react";
import { Button, buttonVariants } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import {
  createRelease,
  exportDiff,
  exportTTL,
  getReleases,
  importTTL,
  publishRelease,
  rollbackRelease,
  submitRelease,
  validateOntology,
  type DiffResult,
  type ReleaseDetail,
  type ReleaseSummary,
  type ValidationReport,
} from "@/lib/api";

/**
 * TTL 工具条（T038）：校验 / 导入 / 导出 / 差异预览 / 批量发布
 * （草稿 → 提交评审 → 发布 = 一次 TTL 导出 + 一次 Git 提交，宪法 III）。
 * 发布前先跑校验，阻断项存在时禁止提交（FR-013/FR-016/FR-017）。
 */
export function TtlToolbar({ onPublished }: { onPublished: () => void }) {
  const [releases, setReleases] = useState<ReleaseSummary[]>([]);
  const [active, setActive] = useState<ReleaseDetail | null>(null);
  const [report, setReport] = useState<ValidationReport | null>(null);
  const [diff, setDiff] = useState<DiffResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadReleases = () => getReleases().then(setReleases).catch(() => {});

  useEffect(() => {
    loadReleases();
  }, []);

  const wrap = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    setMsg(null);
    try {
      await fn();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const handleValidate = () =>
    wrap(async () => {
      const r = await validateOntology();
      setReport(r);
      setMsg(
        r.blocking.length === 0
          ? `校验通过（警告 ${r.warnings.length}）`
          : `存在 ${r.blocking.length} 项阻断问题`,
      );
    });

  const handleDiff = () =>
    wrap(async () => {
      setDiff(await exportDiff());
      setMsg("已生成差异预览");
    });

  const handleExport = () =>
    wrap(async () => {
      const ttl = await exportTTL();
      const blob = new Blob([ttl], { type: "text/turtle" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "slpra-core.ttl";
      a.click();
      URL.revokeObjectURL(url);
      setMsg("已导出 TTL");
    });

  const handleImport = (file: File) =>
    wrap(async () => {
      const content = await file.text();
      const res = await importTTL(content);
      setMsg(`导入完成：新增 ${res.added} / 更新 ${res.updated} / 冲突 ${res.conflicts.length}`);
    });

  const handleCreateRelease = () =>
    wrap(async () => {
      const title = window.prompt("发布标题", "T-Box 发布");
      if (!title) return;
      const rel = await createRelease(title);
      setActive(rel);
      await loadReleases();
      setMsg(`已创建发布草稿 ${rel.release_no}`);
    });

  const openRelease = (r: ReleaseSummary) =>
    wrap(async () => {
      const { getRelease } = await import("@/lib/api");
      setActive(await getRelease(r.id));
    });

  const handleSubmit = () =>
    active &&
    wrap(async () => {
      const r = await submitRelease(active.id);
      setActive(r);
      await loadReleases();
      setMsg("已提交评审");
    });

  const handlePublish = () =>
    active &&
    wrap(async () => {
      const r = await publishRelease(active.id);
      setActive(r);
      await loadReleases();
      setMsg(`已发布 ${r.release_no}（${r.ttl_commit_sha?.slice(0, 8) ?? "—"}）`);
      onPublished();
    });

  const handleRollback = () =>
    active &&
    wrap(async () => {
      const r = await rollbackRelease(active.id);
      setActive(r);
      await loadReleases();
      setMsg("已回滚到草稿");
    });

  return (
    <div className="space-y-3 rounded-lg border border-border bg-card p-3">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-semibold text-foreground">TTL / 发布</h3>
        <Button variant="outline" onClick={handleValidate} disabled={busy} size="sm" className="h-auto px-2.5 py-1 text-xs">
          校验
        </Button>
        <Button variant="outline" onClick={handleDiff} disabled={busy} size="sm" className="h-auto px-2.5 py-1 text-xs">
          差异预览
        </Button>
        <Button variant="outline" onClick={handleExport} disabled={busy} size="sm" className="h-auto px-2.5 py-1 text-xs">
          导出 TTL
        </Button>
        <label className={cn(buttonVariants({ variant: "outline", size: "sm" }), "h-auto cursor-pointer px-2.5 py-1 text-xs")}>
          导入 TTL
          <input
            type="file"
            accept=".ttl,text/turtle"
            className="hidden"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) handleImport(f);
              e.target.value = "";
            }}
          />
        </label>
        <Button onClick={handleCreateRelease} disabled={busy} size="sm" className="ml-auto h-auto px-2.5 py-1 text-xs">
          新建发布
        </Button>
      </div>

      {error && <p className="rounded bg-destructive/10 px-2 py-1 text-xs text-destructive">{error}</p>}
      {msg && <p className="rounded bg-success/10 px-2 py-1 text-xs text-success">{msg}</p>}

      {report && (
        <div className="rounded border border-border bg-muted p-2 text-xs">
          <p className="font-medium text-muted-foreground">
            校验：阻断 {report.blocking.length} · 警告 {report.warnings.length} · 推理机{" "}
            {report.reasoner.ran ? (report.reasoner.consistent ? "一致" : "不一致") : "未运行"}
          </p>
          {report.blocking.map((b, i) => (
            <p key={i} className="text-destructive">• [{b.code}] {b.message}</p>
          ))}
          {report.warnings.map((w, i) => (
            <p key={i} className="text-warning">• [{w.code}] {w.message}</p>
          ))}
        </div>
      )}

      {diff && (
        <div className="rounded border border-border bg-muted p-2 text-xs">
          <p className="mb-1 font-medium text-muted-foreground">
            差异：+{diff.triples_added.length} / -{diff.triples_removed.length}
          </p>
          <pre className="max-h-40 overflow-auto whitespace-pre-wrap font-mono text-[11px] text-foreground">
            {diff.turtle_preview}
          </pre>
        </div>
      )}

      <div className="grid grid-cols-2 gap-3">
        <div>
          <h4 className="mb-1 text-xs font-medium text-muted-foreground">发布列表</h4>
          <ul className="divide-y text-xs">
            {releases.map((r) => (
              <li key={r.id}>
                <button
                  onClick={() => openRelease(r)}
                  className={`flex w-full items-center justify-between py-1.5 text-left hover:bg-accent ${
                    active?.id === r.id ? "font-semibold text-primary" : ""
                  }`}
                >
                  <span>{r.release_no} · {r.title}</span>
                  <Badge variant="secondary" className="font-normal">{r.status}</Badge>
                </button>
              </li>
            ))}
            {releases.length === 0 && <li className="py-2 text-muted-foreground">暂无发布</li>}
          </ul>
        </div>

        {active && (
          <div className="rounded border border-border bg-muted p-2">
            <p className="mb-1 text-xs font-medium text-muted-foreground">
              {active.release_no} · {active.status}
            </p>
            <p className="mb-2 text-[11px] text-muted-foreground">变更项 {active.change_log.length}</p>
            <div className="flex flex-wrap gap-1.5">
              {active.status === "draft" && (
                <Button variant="outline" onClick={handleSubmit} disabled={busy} size="sm" className="h-auto px-2 py-1 text-xs hover:bg-background">
                  提交评审
                </Button>
              )}
              {active.status === "in_review" && (
                <>
                  <Button onClick={handlePublish} disabled={busy} size="sm" className="h-auto bg-success px-2 py-1 text-xs text-success-foreground hover:bg-success/90">
                    发布
                  </Button>
                  <Button variant="outline" onClick={handleRollback} disabled={busy} size="sm" className="h-auto px-2 py-1 text-xs hover:bg-background">
                    回滚草稿
                  </Button>
                </>
              )}
              {active.ttl_commit_sha && (
                <Badge variant="secondary" className="px-2 py-1 font-mono text-[11px] font-normal">
                  {active.ttl_commit_sha.slice(0, 8)}
                </Badge>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
