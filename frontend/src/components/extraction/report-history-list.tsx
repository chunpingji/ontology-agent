"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import type { GeneratedReportDTO } from "@/lib/api";

interface ReportHistoryListProps {
  reports: GeneratedReportDTO[];
  onDownload?: (report: GeneratedReportDTO) => void;
}

export function ReportHistoryList({ reports, onDownload }: ReportHistoryListProps) {
  const [expandedId, setExpandedId] = useState<string | null>(null);

  if (reports.length === 0) {
    return (
      <p className="text-sm text-muted-foreground py-4">暂无历史报告。</p>
    );
  }

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead className="w-[180px]">生成时间</TableHead>
          <TableHead>操作人</TableHead>
          <TableHead className="text-center">规则数</TableHead>
          <TableHead>覆盖</TableHead>
          <TableHead className="text-right">操作</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {reports.map((r) => {
          const cov = (r.rules_summary as Record<string, unknown>)?.coverage as
            | Record<string, unknown>
            | undefined;
          const isExpanded = expandedId === r.id;

          return (
            <TableRow key={r.id} className="group">
              <TableCell className="text-xs tabular-nums">
                {new Date(r.created_at).toLocaleString("zh-CN", {
                  year: "numeric", month: "2-digit", day: "2-digit",
                  hour: "2-digit", minute: "2-digit", second: "2-digit",
                })}
              </TableCell>
              <TableCell className="text-xs">{r.actor}</TableCell>
              <TableCell className="text-center text-xs">{r.rules_fired_count}</TableCell>
              <TableCell>
                {cov ? (
                  <div className="flex items-center gap-1">
                    <Badge variant="outline" className="text-[10px] font-normal">
                      {String(cov.filled ?? 0)}/{String(cov.total_slots ?? 0)}
                    </Badge>
                    {Number(cov.missing_required ?? 0) > 0 && (
                      <Badge variant="destructive" className="text-[10px]">
                        {String(cov.missing_required)} 缺失
                      </Badge>
                    )}
                    <button
                      onClick={() => setExpandedId(isExpanded ? null : r.id)}
                      className="text-[10px] text-blue-600 hover:underline ml-1"
                    >
                      {isExpanded ? "收起" : "查看覆盖"}
                    </button>
                  </div>
                ) : (
                  <span className="text-xs text-muted-foreground">—</span>
                )}
                {isExpanded && cov && (
                  <CoverageDetail coverage={cov} />
                )}
              </TableCell>
              <TableCell className="text-right">
                <Button
                  variant="link"
                  size="sm"
                  className="h-auto p-0 text-xs"
                  onClick={() => onDownload?.(r)}
                >
                  下载
                </Button>
              </TableCell>
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}

function CoverageDetail({ coverage }: { coverage: Record<string, unknown> }) {
  const slots = (coverage.slots as Array<Record<string, unknown>>) ?? [];

  return (
    <div className="mt-2 max-h-40 overflow-y-auto rounded border bg-muted/30 p-2">
      <div className="space-y-0.5">
        {slots.map((s, i) => (
          <div key={i} className="flex items-center gap-2 text-[10px]">
            <StatusDot status={String(s.status ?? "")} />
            <span className="truncate">{String(s.label ?? s.slot_id ?? "")}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function StatusDot({ status }: { status: string }) {
  const colors: Record<string, string> = {
    filled: "bg-green-500",
    inferred: "bg-blue-500",
    missing_required: "bg-red-500",
    blank_optional: "bg-gray-400",
    manual: "bg-yellow-500",
    dismissed: "bg-gray-300",
  };
  return <span className={`inline-block h-1.5 w-1.5 rounded-full ${colors[status] ?? "bg-gray-400"}`} />;
}
