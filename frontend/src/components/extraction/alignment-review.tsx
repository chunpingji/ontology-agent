"use client";

import { useCallback, useEffect, useState } from "react";
import {
  getJobCandidates,
  mergeCandidates,
  reviewCandidate,
  splitCandidate,
  type ExtractionCandidate,
  type GroupedCandidates,
} from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";

export function AlignmentReview({ jobId }: { jobId: string }) {
  const [data, setData] = useState<GroupedCandidates | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(
    () =>
      getJobCandidates(jobId)
        .then(setData)
        .catch((e) => setError(String(e))),
    [jobId],
  );

  useEffect(() => {
    refresh();
  }, [refresh]);

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
    try {
      await splitCandidate(c.id, [
        { ...c.extracted_properties }, { ...c.extracted_properties },
      ]);
      await refresh();
    } catch (e) {
      setError(String(e));
    }
  }

  if (!data) return <p className="px-4 py-6 text-sm text-muted-foreground">加载候选…</p>;

  const allGroups = [
    ...data.groups,
    { group_key: "（未归组）", canonical_candidate_id: null, candidates: data.ungrouped },
  ].filter((g) => g.candidates.length > 0);

  const totalCandidates = allGroups.reduce((s, g) => s + g.candidates.length, 0);

  if (totalCandidates === 0) {
    return <p className="px-4 py-6 text-sm text-muted-foreground">暂无候选实体</p>;
  }

  return (
    <div className="flex h-full flex-col">
      {/* Header bar */}
      <div className="flex items-center justify-between px-4 py-3">
        <h3 className="text-sm font-semibold">候选实体</h3>
        <Badge variant="secondary">{totalCandidates}</Badge>
      </div>
      <Separator />

      {/* Merge toolbar */}
      <div className="flex items-center gap-2 px-3 py-2">
        <Button onClick={doMerge} disabled={selected.size < 2} size="sm" className="h-7 text-xs">
          合并所选（{selected.size}）
        </Button>
        <span className="text-[10px] text-muted-foreground">先勾选 = 合并目标</span>
      </div>

      {error && (
        <div className="mx-3 mb-2 rounded border border-destructive/40 bg-destructive/10 px-2 py-1 text-xs text-destructive">
          {error}
        </div>
      )}

      {/* Candidate list */}
      <div className="flex-1 overflow-y-auto px-2 pb-2 space-y-3">
        {allGroups.map((g) => (
          <div key={g.group_key}>
            <div className="flex items-center justify-between px-2 py-1 text-xs text-muted-foreground">
              <span className="font-medium truncate">{g.group_key}</span>
              <span>{g.candidates.length}</span>
            </div>
            <div className="space-y-1">
              {g.candidates.map((c) => (
                <div
                  key={c.id}
                  className="rounded-md border bg-card p-2.5 text-sm"
                >
                  {/* Top: checkbox + badges */}
                  <div className="flex items-start gap-2">
                    <input
                      type="checkbox"
                      checked={selected.has(c.id)}
                      onChange={() => toggle(c.id)}
                      className="mt-0.5 shrink-0"
                    />
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-1">
                        <Badge variant="secondary" className="text-[10px]">
                          {c.candidate_kind}
                        </Badge>
                        {c.is_canonical && (
                          <Badge variant="success" className="text-[10px]">规范</Badge>
                        )}
                        {c.degraded_reason && (
                          <Badge variant="warning" className="text-[10px]">降级</Badge>
                        )}
                        <Badge
                          variant={c.review_status === "confirmed" ? "success" : c.review_status === "rejected" ? "destructive" : "outline"}
                          className="text-[10px]"
                        >
                          {c.review_status}
                        </Badge>
                      </div>

                      {/* Properties */}
                      <p className="mt-1 break-all text-xs text-foreground leading-snug">
                        {JSON.stringify(c.extracted_properties).slice(0, 120)}
                      </p>

                      {/* Metadata */}
                      <p className="mt-0.5 text-[10px] text-muted-foreground leading-relaxed">
                        对齐：{c.alignment_result ?? "—"} · 置信度：
                        {c.match_score != null ? c.match_score.toFixed(2) : "—"}
                        {c.source_ref ? ` · 源：${c.source_ref}` : ""}
                      </p>
                    </div>
                  </div>

                  {/* Actions */}
                  <div className="mt-2 flex gap-1.5">
                    <Button
                      onClick={() => doReview(c.id, "confirmed")}
                      size="sm"
                      className="h-6 flex-1 text-xs"
                    >
                      确认入库
                    </Button>
                    <Button
                      onClick={() => doReview(c.id, "rejected")}
                      variant="outline"
                      size="sm"
                      className="h-6 flex-1 text-xs"
                    >
                      拒绝
                    </Button>
                    <Button
                      onClick={() => doSplit(c)}
                      variant="outline"
                      size="sm"
                      className="h-6 text-xs"
                    >
                      拆分
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
