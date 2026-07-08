"use client";

import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  ChevronDown,
  ClipboardCheck,
  Loader2,
  ShieldCheck,
  Sparkles,
  TriangleAlert,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { saveBlob } from "@/components/reports/reading-pane";
import {
  generateRiskReportBlob,
  resolveDocumentJobId,
  type ReportOrDocument,
} from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * 文档预览页「操作」弹出菜单（报告中心详情页 · 右上角，紧邻分享按钮）。
 *
 * 三项 AI / 合规操作，按设计稿还原：
 *   · AI 分析——智能提取文档关键信息（占位：拟接 rerunAnnotation）
 *   · 生成风险评估报告——**已接线**「通过模板生成报告」：解析文档关联的抽取 jobId →
 *     `generateRiskReportBlob`（后端按文档类别 resolve_template → 模板分节渲染，
 *     屏蔽同步/异步两条路径）→ 下载 .docx。
 *   · 审计——合规性审查与追踪（占位：拟接合规审计链）
 *
 * AI 分析 / 审计仍为占位：点击给出「即将上线」轻提示，后端接入留待后续。
 */
const RISK_KEY = "risk";

const ACTIONS = [
  {
    key: RISK_KEY,
    label: "生成风险评估报告",
    desc: "基于本体评估潜在风险",
    Icon: ShieldCheck,
    tint: "bg-amber-500/10 text-amber-600 dark:text-amber-500",
  },
  {
    key: "audit",
    label: "审计",
    desc: "合规性审查与追踪",
    Icon: ClipboardCheck,
    tint: "bg-blue-500/10 text-blue-600 dark:text-blue-500",
  },
] as const;

type Notice = { tone: "info" | "success" | "error"; text: string };

const TONE_STYLES: Record<Notice["tone"], { className: string; Icon: typeof Sparkles }> = {
  info: { className: "text-popover-foreground", Icon: Sparkles },
  success: { className: "text-emerald-600 dark:text-emerald-500", Icon: ShieldCheck },
  error: { className: "text-destructive", Icon: TriangleAlert },
};

/** 把 `fetchAPI` 抛出的 `API 4xx: {"detail":"…"}` 提炼成可读的中文提示。 */
function friendlyError(error: unknown): string {
  const message = error instanceof Error ? error.message : String(error);
  const match = message.match(/API \d+:\s*([\s\S]*)$/);
  if (match) {
    try {
      const body = JSON.parse(match[1]);
      if (body && typeof body.detail === "string") return body.detail;
    } catch {
      /* 非 JSON 响应体——回退到原始文本 */
    }
  }
  return message;
}

export function DocumentActionsMenu({ item }: { item: ReportOrDocument }) {
  const queryClient = useQueryClient();
  // 瞬时提示（info/success/error）：自动消隐。生成过程的「生成中」态由 mutation 的
  // pending 独立驱动（常驻至落定），二者互斥渲染。
  const [notice, setNotice] = useState<Notice | null>(null);

  useEffect(() => {
    if (!notice) return;
    const timer = setTimeout(() => setNotice(null), notice.tone === "error" ? 4000 : 2500);
    return () => clearTimeout(timer);
  }, [notice]);

  const generate = useMutation({
    mutationFn: async () => {
      const iri = item.kind === "uploaded-document" ? item.iri : undefined;
      if (!iri) throw new Error("仅支持对上传文档生成风险评估报告");
      const jobId = await resolveDocumentJobId(iri);
      if (!jobId) throw new Error("该文档未关联抽取任务，无法生成报告");
      const blob = await generateRiskReportBlob(jobId);
      const base = (item.title || "report").replace(/\.docx$/i, "");
      saveBlob(blob, `风险评估表_${base}.docx`);
    },
    onSuccess: () => {
      setNotice({ tone: "success", text: "风险评估报告已生成并下载" });
      // 新报告随后出现在报告中心列表——失效缓存，返回列表时自动刷新出来。
      queryClient.invalidateQueries({ queryKey: ["report-center"] });
    },
    onError: (error) => setNotice({ tone: "error", text: friendlyError(error) }),
  });

  const busy = generate.isPending;

  const handleSelect = (key: string, label: string) => {
    if (key === RISK_KEY) {
      if (!busy) generate.mutate();
      return;
    }
    setNotice({ tone: "info", text: `「${label}」功能即将上线` });
  };

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button disabled={busy}>
            {busy ? <Loader2 className="animate-spin" /> : <Sparkles />}
            操作
            <ChevronDown />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-64">
          {ACTIONS.map(({ key, label, desc, Icon, tint }) => {
            const isRisk = key === RISK_KEY;
            const rowBusy = isRisk && busy;
            return (
              <DropdownMenuItem
                key={key}
                className="items-start gap-3 py-2.5"
                disabled={rowBusy}
                // 保持菜单在生成期间不因选中而关闭，让「生成中」态可见于按钮。
                onSelect={(event) => {
                  if (rowBusy) event.preventDefault();
                  handleSelect(key, label);
                }}
              >
                <span
                  className={cn(
                    "mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-md [&_svg]:size-4",
                    tint,
                  )}
                >
                  {rowBusy ? <Loader2 className="animate-spin" /> : <Icon />}
                </span>
                <div className="min-w-0 space-y-0.5">
                  <p className="text-sm font-medium leading-none text-foreground">{label}</p>
                  <p className="text-xs leading-snug text-muted-foreground">
                    {rowBusy ? "正在生成，请稍候…" : desc}
                  </p>
                </div>
              </DropdownMenuItem>
            );
          })}
        </DropdownMenuContent>
      </DropdownMenu>

      {busy ? (
        <div
          role="status"
          className="fixed bottom-6 right-6 z-50 flex items-center gap-2 rounded-md border border-border bg-popover px-4 py-2.5 text-sm text-popover-foreground shadow-lg"
        >
          <Loader2 className="size-4 animate-spin text-primary" />
          <span>正在通过模板生成风险评估报告…</span>
        </div>
      ) : notice ? (
        <div
          role="status"
          className={cn(
            "fixed bottom-6 right-6 z-50 flex max-w-sm items-center gap-2 rounded-md border border-border bg-popover px-4 py-2.5 text-sm shadow-lg",
            TONE_STYLES[notice.tone].className,
          )}
        >
          {(() => {
            const Icon = TONE_STYLES[notice.tone].Icon;
            return <Icon className="size-4 shrink-0" />;
          })()}
          <span>{notice.text}</span>
        </div>
      ) : null}
    </>
  );
}
