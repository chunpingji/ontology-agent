"use client";

import { useEffect, useState } from "react";
import { ChevronDown, ClipboardCheck, ShieldCheck, Sparkles } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";

/**
 * 文档预览页「操作」弹出菜单（报告中心详情页 · 右上角，紧邻分享按钮）。
 *
 * 三项 AI / 合规操作，按设计稿还原：
 *   · AI 分析——智能提取文档关键信息（拟接 rerunAnnotation：重跑标注、再次处理文档）
 *   · 生成风险评估报告——基于本体评估潜在风险（拟接 generateRiskReport）
 *   · 审计——合规性审查与追踪（拟接合规审计链）
 *
 * 本迭代三项均为**占位**：点击给出「即将上线」轻提示，后端接入留待后续。届时只需把各项
 * onSelect 换成对应调用（jobId 可由 resolveDocumentContent 一并回传），菜单结构不变。
 */
const ACTIONS = [
  {
    key: "ai",
    label: "AI 分析",
    desc: "智能提取文档关键信息",
    Icon: Sparkles,
    tint: "bg-primary/10 text-primary",
  },
  {
    key: "risk",
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

export function DocumentActionsMenu() {
  // 占位反馈：点击某项后短暂展示「即将上线」提示（暂无全局 toast 系统，自管理瞬时通知）。
  // 每次点击生成新对象 → effect 依赖变化 → 重置 2.5s 计时器（连点同一项也能续期）。
  const [coming, setComing] = useState<{ label: string } | null>(null);

  useEffect(() => {
    if (!coming) return;
    const timer = setTimeout(() => setComing(null), 2500);
    return () => clearTimeout(timer);
  }, [coming]);

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button>
            <Sparkles />
            操作
            <ChevronDown />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-64">
          {ACTIONS.map(({ key, label, desc, Icon, tint }) => (
            <DropdownMenuItem
              key={key}
              className="items-start gap-3 py-2.5"
              onSelect={() => setComing({ label })}
            >
              <span
                className={cn(
                  "mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-md [&_svg]:size-4",
                  tint,
                )}
              >
                <Icon />
              </span>
              <div className="min-w-0 space-y-0.5">
                <p className="text-sm font-medium leading-none text-foreground">{label}</p>
                <p className="text-xs leading-snug text-muted-foreground">{desc}</p>
              </div>
            </DropdownMenuItem>
          ))}
        </DropdownMenuContent>
      </DropdownMenu>

      {coming && (
        <div
          role="status"
          className="fixed bottom-6 right-6 z-50 flex items-center gap-2 rounded-md border border-border bg-popover px-4 py-2.5 text-sm text-popover-foreground shadow-lg"
        >
          <Sparkles className="size-4 text-primary" />
          <span>「{coming.label}」功能即将上线</span>
        </div>
      )}
    </>
  );
}
