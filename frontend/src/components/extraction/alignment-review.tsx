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
import { Card } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableRow } from "@/components/ui/table";

/**
 * 对齐审核闭环（T026, US2）：展示候选 / 置信度 / 跨源归组 / 规范实例标记，
 * 承载确认、拒绝、合并、拆分（FR-009/010）。仅 `confirmed` 入库。
 */
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

  // jobId 改变时由父级以 key 重挂载（selected 经 useState 初值复位为空集）；
  // effect 只负责拉取候选，setState 在 .then 回调中执行而非 effect 体内同步调用。
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

  if (!data) return <p className="text-sm text-muted-foreground">加载候选…</p>;

  const allGroups = [
    ...data.groups,
    { group_key: "（未归组）", canonical_candidate_id: null, candidates: data.ungrouped },
  ].filter((g) => g.candidates.length > 0);

  return (
    <div className="space-y-4">
      {error && (
        <div className="rounded border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
          {error}
        </div>
      )}

      <div className="flex items-center gap-3">
        <Button
          onClick={doMerge}
          disabled={selected.size < 2}
          size="sm"
        >
          合并所选（{selected.size}）
        </Button>
        <span className="text-xs text-muted-foreground">先勾选的为合并目标（规范实例）</span>
      </div>

      {allGroups.map((g) => (
        <Card key={g.group_key} className="overflow-hidden">
          <div className="flex items-center justify-between border-b bg-muted px-3 py-2 text-sm">
            <span className="font-medium">归组键：{g.group_key}</span>
            <span className="text-xs text-muted-foreground">{g.candidates.length} 个候选</span>
          </div>
          <Table>
            <TableBody>
              {g.candidates.map((c) => (
                <TableRow key={c.id} className="border-b last:border-0">
                  <TableCell className="w-8 py-2 pl-3">
                    <input
                      type="checkbox"
                      checked={selected.has(c.id)}
                      onChange={() => toggle(c.id)}
                    />
                  </TableCell>
                  <TableCell className="py-2">
                    <div className="flex items-center gap-2">
                      <Badge variant="secondary">
                        {c.candidate_kind}
                      </Badge>
                      {c.is_canonical && (
                        <Badge variant="success">
                          规范实例
                        </Badge>
                      )}
                      {c.degraded_reason && (
                        <Badge variant="warning">
                          降级
                        </Badge>
                      )}
                      <span className="text-foreground">
                        {JSON.stringify(c.extracted_properties).slice(0, 80)}
                      </span>
                    </div>
                    <div className="mt-0.5 text-xs text-muted-foreground">
                      对齐：{c.alignment_result ?? "—"} · 置信度：
                      {c.match_score != null ? c.match_score.toFixed(2) : "—"} · 状态：
                      {c.review_status}
                      {c.source_ref ? ` · 源：${c.source_ref}` : ""}
                    </div>
                  </TableCell>
                  <TableCell className="py-2 pr-3 text-right">
                    <div className="flex justify-end gap-2">
                      <Button
                        onClick={() => doReview(c.id, "confirmed")}
                        size="sm"
                      >
                        确认入库
                      </Button>
                      <Button
                        onClick={() => doReview(c.id, "rejected")}
                        variant="outline"
                        size="sm"
                      >
                        拒绝
                      </Button>
                      <Button
                        onClick={() => doSplit(c)}
                        variant="outline"
                        size="sm"
                      >
                        拆分
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Card>
      ))}
    </div>
  );
}
