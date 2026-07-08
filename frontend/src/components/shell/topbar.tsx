"use client";

import { usePathname, useRouter } from "next/navigation";
import { LogOut } from "lucide-react";

import { navTitle } from "@/components/shell/nav";
import { useIdentity } from "@/lib/use-identity";
import { logout } from "@/lib/api";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

// 真实认证后角色由登录令牌决定、只读展示（旧「开发态」角色下拉已移除，避免任意越权切换）。
const ROLE_LABELS: Record<string, string> = {
  senior_analyst: "高级分析师",
  operator: "操作员",
  qa: "QA（质量）",
};

function initials(name: string): string {
  const trimmed = name.trim();
  if (!trimmed) return "U";
  return trimmed.slice(0, 2).toUpperCase();
}

export function TopBar() {
  const pathname = usePathname();
  const router = useRouter();
  const { identity, role } = useIdentity();
  const title = navTitle(pathname);

  async function onLogout() {
    await logout();
    router.replace("/login");
  }

  return (
    <header className="sticky top-0 z-30 flex h-14 shrink-0 items-center justify-between border-b border-border bg-background/95 px-6 backdrop-blur supports-[backdrop-filter]:bg-background/80">
      <h1 className="truncate text-base font-semibold text-foreground">{title}</h1>

      <div className="flex items-center gap-3">
        <div className="flex items-center gap-2">
          <Avatar className="size-8">
            <AvatarFallback>{initials(identity.username)}</AvatarFallback>
          </Avatar>
          <span className="hidden text-sm text-foreground md:block">
            {identity.username}
          </span>
          <Badge variant="secondary" className="hidden sm:inline-flex">
            {ROLE_LABELS[role] ?? role}
          </Badge>
        </div>

        <Button
          variant="ghost"
          size="sm"
          onClick={onLogout}
          className="gap-1.5 text-muted-foreground hover:text-foreground"
          aria-label="退出登录"
        >
          <LogOut className="h-4 w-4" />
          <span className="hidden sm:inline">退出登录</span>
        </Button>
      </div>
    </header>
  );
}
