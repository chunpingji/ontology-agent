"use client";

import { useEffect, useState } from "react";
import {
  getJobCandidates,
  mergeCandidates,
  reviewCandidate,
  splitCandidate,
  type ExtractionCandidate,
  type GroupedCandidates,
} from "@/lib/api";

/**
 * 对齐审核闭环（T026, US2）：展示候选 / 置信度 / 跨源归组 / 规范实例标记，
 * 承载确认、拒绝、合并、拆分（FR-009/010）。仅 `confirmed` 入库。
 */
export function AlignmentReview({ jobId }: { jobId: string }) {
  const [data, setData] = useState<GroupedCandidates | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);

  async function refresh() {
    try {
      setData(await getJobCandidates(jobId));
    } catch (e) {
      setError(String(e));
    }
  }

  useEffect(() => {
    setSelected(new Set());
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId]);

  function toggle(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function doReview(id: string, status: "confirmed" | "rejected") {
    setError(null);
    try {
      await reviewCandidate(id, status);
      await refresh();
    } catch (e) {
      setError(String(e));
    }
  }

  async function doMerge() {
    const ids = [...selected];
    if (ids.length < 2) {
      setError("合并至少需要选择 2 个候选");
      return;
    }
    const [target, ...sources] = ids;
    try {
      await mergeCandidates(target, sources);
      setSelected(new Set());
      await refresh();
    } catch (e) {
      setError(String(e));
    }
  }

  async function doSplit(c: ExtractionCandidate) {
    // 简化：把单个候选拆成两份副本，分析师随后分别编辑。
    try {
      await splitCandidate(c.id, [
        { ...c.extracted_properties }, { ...c.extracted_properties },
      ]);
      await refresh();
    } catch (e) {
      setError(String(e));
    }
  }

  if (!data) return <p className="text-sm text-gray-500">加载候选…</p>;

  const allGroups = [
    ...data.groups,
    { group_key: "（未归组）", canonical_candidate_id: null, candidates: data.ungrouped },
  ].filter((g) => g.candidates.length > 0);

  return (
    <div className="space-y-4">
      {error && (
        <div className="rounded border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700">
          {error}
        </div>
      )}

      <div className="flex items-center gap-3">
        <button
          onClick={doMerge}
          disabled={selected.size < 2}
          className="rounded bg-purple-600 px-3 py-1.5 text-sm text-white hover:bg-purple-700 disabled:opacity-40"
        >
          合并所选（{selected.size}）
        </button>
        <span className="text-xs text-gray-500">先勾选的为合并目标（规范实例）</span>
      </div>

      {allGroups.map((g) => (
        <div key={g.group_key} className="rounded-lg border">
          <div className="flex items-center justify-between border-b bg-gray-50 px-3 py-2 text-sm">
            <span className="font-medium">归组键：{g.group_key}</span>
            <span className="text-xs text-gray-500">{g.candidates.length} 个候选</span>
          </div>
          <table className="w-full text-sm">
            <tbody>
              {g.candidates.map((c) => (
                <tr key={c.id} className="border-b last:border-0">
                  <td className="w-8 py-2 pl-3">
                    <input
                      type="checkbox"
                      checked={selected.has(c.id)}
                      onChange={() => toggle(c.id)}
                    />
                  </td>
                  <td className="py-2">
                    <div className="flex items-center gap-2">
                      <span className="rounded bg-gray-100 px-1.5 py-0.5 text-xs">
                        {c.candidate_kind}
                      </span>
                      {c.is_canonical && (
                        <span className="rounded bg-green-100 px-1.5 py-0.5 text-xs text-green-700">
                          规范实例
                        </span>
                      )}
                      {c.degraded_reason && (
                        <span className="rounded bg-amber-100 px-1.5 py-0.5 text-xs text-amber-700">
                          降级
                        </span>
                      )}
                      <span className="text-gray-700">
                        {JSON.stringify(c.extracted_properties).slice(0, 80)}
                      </span>
                    </div>
                    <div className="mt-0.5 text-xs text-gray-400">
                      对齐：{c.alignment_result ?? "—"} · 置信度：
                      {c.match_score != null ? c.match_score.toFixed(2) : "—"} · 状态：
                      {c.review_status}
                      {c.source_ref ? ` · 源：${c.source_ref}` : ""}
                    </div>
                  </td>
                  <td className="py-2 pr-3 text-right">
                    <div className="flex justify-end gap-2">
                      <button
                        onClick={() => doReview(c.id, "confirmed")}
                        className="rounded bg-blue-600 px-2 py-1 text-xs text-white hover:bg-blue-700"
                      >
                        确认入库
                      </button>
                      <button
                        onClick={() => doReview(c.id, "rejected")}
                        className="rounded border px-2 py-1 text-xs text-gray-600 hover:bg-gray-50"
                      >
                        拒绝
                      </button>
                      <button
                        onClick={() => doSplit(c)}
                        className="rounded border px-2 py-1 text-xs text-gray-600 hover:bg-gray-50"
                      >
                        拆分
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
    </div>
  );
}
