"use client";

import { usePathname } from "next/navigation";

import { navTitle } from "@/components/shell/nav";
import { useIdentity, type Role } from "@/lib/use-identity";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

const ROLES: { value: Role; label: string }[] = [
  { value: "senior_analyst", label: "高级分析师" },
  { value: "operator", label: "操作员" },
  { value: "qa", label: "QA（质量）" },
];

function initials(name: string): string {
  const trimmed = name.trim();
  if (!trimmed) return "U";
  return trimmed.slice(0, 2).toUpperCase();
}

export function TopBar() {
  const pathname = usePathname();
  const { identity, role, setIdentity } = useIdentity();
  const title = navTitle(pathname);

  return (
    <header className="sticky top-0 z-30 flex h-14 shrink-0 items-center justify-between border-b border-border bg-background/95 px-6 backdrop-blur supports-[backdrop-filter]:bg-background/80">
      <h1 className="truncate text-base font-semibold text-foreground">{title}</h1>

      <div className="flex items-center gap-3">
        <div className="flex items-center gap-2">
          <label className="hidden text-xs text-muted-foreground sm:block">
            当前身份（开发态）
          </label>
          <Select
            value={role}
            onValueChange={(value) =>
              setIdentity({ username: identity.username, role: value })
            }
          >
            <SelectTrigger className="h-8 w-[140px]" aria-label="切换身份">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {ROLES.map((r) => (
                <SelectItem key={r.value} value={r.value}>
                  {r.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="flex items-center gap-2">
          <Avatar className="size-8">
            <AvatarFallback>{initials(identity.username)}</AvatarFallback>
          </Avatar>
          <span className="hidden text-sm text-foreground md:block">
            {identity.username}
          </span>
        </div>
      </div>
    </header>
  );
}
