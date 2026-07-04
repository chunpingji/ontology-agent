"use client";

import Link from "next/link";
import { useMutation } from "@tanstack/react-query";
import {
  Download,
  Eye,
  FileBarChart,
  FileText,
  Inbox,
  Loader2,
  MoreHorizontal,
  Trash2,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { EmptyState } from "@/components/ui/empty-state";
import { cn } from "@/lib/utils";
import { downloadReportById, type ReportOrDocument } from "@/lib/api";
import { formatBytes, saveBlob } from "./reading-pane";

function detailHref(item: ReportOrDocument) {
  const query: Record<string, string> = {
    kind: item.kind,
    title: item.title,
    type: item.type,
  };
  if (item.date) query.date = item.date;
  if (item.size != null) query.size = String(item.size);
  if (item.kind === "generated-report") {
    if (item.jobId) query.jobId = item.jobId;
    if (item.reportId) query.reportId = item.reportId;
  } else if (item.iri) {
    query.iri = item.iri;
  }
  return { pathname: `/reports/${encodeURIComponent(item.key)}`, query };
}

function formatDate(date: string | null): string {
  return date ? date.slice(0, 10) : "—";
}

export function ItemList({
  items,
  canDelete,
  onDelete,
  selectedCategory,
}: {
  items: ReportOrDocument[];
  canDelete: boolean;
  onDelete: (item: ReportOrDocument) => void;
  selectedCategory?: string | null;
}) {
  const download = useMutation({
    mutationFn: async (item: ReportOrDocument) => {
      if (item.kind !== "generated-report" || !item.jobId || !item.reportId) {
        throw new Error("该条目不支持下载");
      }
      const blob = await downloadReportById(item.jobId, item.reportId);
      saveBlob(blob, item.title || item.key);
    },
  });

  if (items.length === 0) {
    return (
      <EmptyState
        icon={<Inbox />}
        title={selectedCategory ? `"${selectedCategory}"分类暂无内容` : "暂无报告或文档"}
        description={
          selectedCategory
            ? "该分类下还没有报告或文档，换个分类看看。"
            : "系统尚未生成报告，也没有已接入的文档。"
        }
      />
    );
  }

  return (
    <div className="overflow-hidden rounded-lg border border-border">
      <div className="hidden items-center gap-4 border-b border-border bg-muted px-4 py-2 text-xs font-medium text-muted-foreground sm:flex">
        <span className="flex-1">标题</span>
        <span className="w-28 shrink-0">类型</span>
        <span className="w-20 shrink-0">状态</span>
        <span className="w-24 shrink-0">日期</span>
        <span className="w-20 shrink-0 text-right">大小</span>
        <span className="w-9 shrink-0" />
      </div>
      <ul className="divide-y divide-border">
        {items.map((item) => {
          const isReport = item.kind === "generated-report";
          const isProcessing = item.status === "processing";
          const downloading = download.isPending && download.variables?.key === item.key;
          return (
            <li
              key={item.key}
              className={cn(
                "flex items-center gap-4 px-4 py-3 transition-colors",
                isProcessing ? "bg-muted/30" : "hover:bg-accent/40",
              )}
            >
              <div className="flex min-w-0 flex-1 items-center gap-2.5">
                <span className={cn("shrink-0 text-muted-foreground", "[&_svg]:size-4")}>
                  {isProcessing ? (
                    <Loader2 className="animate-spin" />
                  ) : isReport ? (
                    <FileBarChart />
                  ) : (
                    <FileText />
                  )}
                </span>
                {isProcessing ? (
                  <span className="min-w-0 truncate text-sm font-medium text-muted-foreground">
                    {item.title}
                  </span>
                ) : (
                  <Link
                    href={detailHref(item)}
                    className="min-w-0 truncate text-sm font-medium text-foreground hover:text-primary hover:underline"
                    title={item.title}
                  >
                    {item.title}
                  </Link>
                )}
              </div>
              <div className="w-28 shrink-0">
                <Badge variant={isReport ? "secondary" : "outline"} className="max-w-full truncate">
                  {item.type || (isReport ? "报告" : "文档")}
                </Badge>
              </div>
              <div className="w-20 shrink-0">
                {isProcessing ? (
                  <Badge variant="secondary" className="gap-1 font-normal">
                    <Loader2 className="size-3 animate-spin" />
                    处理中
                  </Badge>
                ) : (
                  <Badge variant="outline" className="font-normal text-green-600 border-green-200 bg-green-50 dark:text-green-400 dark:border-green-800 dark:bg-green-950">
                    就绪
                  </Badge>
                )}
              </div>
              <span className="w-24 shrink-0 text-sm text-muted-foreground">
                {formatDate(item.date)}
              </span>
              <span className="w-20 shrink-0 text-right text-sm text-muted-foreground">
                {formatBytes(item.size)}
              </span>
              <div className="w-9 shrink-0">
                {isProcessing ? (
                  <Button variant="ghost" size="icon" disabled aria-label="处理中">
                    <Loader2 className="animate-spin" />
                  </Button>
                ) : (
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button variant="ghost" size="icon" aria-label="更多操作">
                        {downloading ? <Loader2 className="animate-spin" /> : <MoreHorizontal />}
                      </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end">
                      <DropdownMenuItem asChild>
                        <Link href={detailHref(item)}>
                          <Eye />
                          预览
                        </Link>
                      </DropdownMenuItem>
                      {isReport && (
                        <DropdownMenuItem
                          onSelect={() => download.mutate(item)}
                          disabled={downloading}
                        >
                          <Download />
                          下载
                        </DropdownMenuItem>
                      )}
                      {canDelete && (
                        <>
                          <DropdownMenuSeparator />
                          <DropdownMenuItem
                            onSelect={() => onDelete(item)}
                            className="text-destructive focus:text-destructive"
                          >
                            <Trash2 />
                            删除
                          </DropdownMenuItem>
                        </>
                      )}
                    </DropdownMenuContent>
                  </DropdownMenu>
                )}
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
