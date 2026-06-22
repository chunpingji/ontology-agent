"use client";

import { useEffect, useState } from "react";
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
    <div className="space-y-3 rounded-lg border bg-white p-3">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-semibold text-gray-700">TTL / 发布</h3>
        <button onClick={handleValidate} disabled={busy} className="rounded border px-2.5 py-1 text-xs hover:bg-gray-50 disabled:opacity-50">
          校验
        </button>
        <button onClick={handleDiff} disabled={busy} className="rounded border px-2.5 py-1 text-xs hover:bg-gray-50 disabled:opacity-50">
          差异预览
        </button>
        <button onClick={handleExport} disabled={busy} className="rounded border px-2.5 py-1 text-xs hover:bg-gray-50 disabled:opacity-50">
          导出 TTL
        </button>
        <label className="cursor-pointer rounded border px-2.5 py-1 text-xs hover:bg-gray-50">
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
        <button onClick={handleCreateRelease} disabled={busy} className="ml-auto rounded bg-blue-600 px-2.5 py-1 text-xs text-white hover:bg-blue-700 disabled:opacity-50">
          新建发布
        </button>
      </div>

      {error && <p className="rounded bg-red-50 px-2 py-1 text-xs text-red-600">{error}</p>}
      {msg && <p className="rounded bg-green-50 px-2 py-1 text-xs text-green-700">{msg}</p>}

      {report && (
        <div className="rounded border bg-gray-50 p-2 text-xs">
          <p className="font-medium text-gray-600">
            校验：阻断 {report.blocking.length} · 警告 {report.warnings.length} · 推理机{" "}
            {report.reasoner.ran ? (report.reasoner.consistent ? "一致" : "不一致") : "未运行"}
          </p>
          {report.blocking.map((b, i) => (
            <p key={i} className="text-red-600">• [{b.code}] {b.message}</p>
          ))}
          {report.warnings.map((w, i) => (
            <p key={i} className="text-amber-600">• [{w.code}] {w.message}</p>
          ))}
        </div>
      )}

      {diff && (
        <div className="rounded border bg-gray-50 p-2 text-xs">
          <p className="mb-1 font-medium text-gray-600">
            差异：+{diff.triples_added.length} / -{diff.triples_removed.length}
          </p>
          <pre className="max-h-40 overflow-auto whitespace-pre-wrap font-mono text-[11px] text-gray-700">
            {diff.turtle_preview}
          </pre>
        </div>
      )}

      <div className="grid grid-cols-2 gap-3">
        <div>
          <h4 className="mb-1 text-xs font-medium text-gray-500">发布列表</h4>
          <ul className="divide-y text-xs">
            {releases.map((r) => (
              <li key={r.id}>
                <button
                  onClick={() => openRelease(r)}
                  className={`flex w-full items-center justify-between py-1.5 text-left hover:bg-gray-50 ${
                    active?.id === r.id ? "font-semibold text-blue-700" : ""
                  }`}
                >
                  <span>{r.release_no} · {r.title}</span>
                  <span className="rounded bg-gray-100 px-1.5 py-0.5">{r.status}</span>
                </button>
              </li>
            ))}
            {releases.length === 0 && <li className="py-2 text-gray-400">暂无发布</li>}
          </ul>
        </div>

        {active && (
          <div className="rounded border bg-gray-50 p-2">
            <p className="mb-1 text-xs font-medium text-gray-600">
              {active.release_no} · {active.status}
            </p>
            <p className="mb-2 text-[11px] text-gray-500">变更项 {active.change_log.length}</p>
            <div className="flex flex-wrap gap-1.5">
              {active.status === "draft" && (
                <button onClick={handleSubmit} disabled={busy} className="rounded border px-2 py-1 text-xs hover:bg-white disabled:opacity-50">
                  提交评审
                </button>
              )}
              {active.status === "in_review" && (
                <>
                  <button onClick={handlePublish} disabled={busy} className="rounded bg-green-600 px-2 py-1 text-xs text-white hover:bg-green-700 disabled:opacity-50">
                    发布
                  </button>
                  <button onClick={handleRollback} disabled={busy} className="rounded border px-2 py-1 text-xs hover:bg-white disabled:opacity-50">
                    回滚草稿
                  </button>
                </>
              )}
              {active.ttl_commit_sha && (
                <span className="rounded bg-gray-100 px-2 py-1 font-mono text-[11px] text-gray-600">
                  {active.ttl_commit_sha.slice(0, 8)}
                </span>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
