"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/lib/utils";
import { useIdentity } from "@/lib/use-identity";
import {
  NAV,
  isGroup,
  isActive,
  type NavItem,
} from "@/components/shell/nav";

function NavLink({ item, active }: { item: NavItem; active: boolean }) {
  const Icon = item.icon;
  return (
    <Link
      href={item.href}
      aria-current={active ? "page" : undefined}
      className={cn(
        "flex items-center gap-2.5 rounded-md px-3 py-2 text-sm transition-colors",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sidebar-ring",
        active
          ? "bg-sidebar-primary/10 font-medium text-sidebar-primary"
          : "text-sidebar-foreground hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
      )}
    >
      {Icon && <Icon className="size-4 shrink-0" aria-hidden />}
      <span className="truncate">{item.label}</span>
    </Link>
  );
}

export function Sidebar() {
  const pathname = usePathname();
  const { role } = useIdentity();

  const canSee = (item: NavItem) =>
    !item.requiredRole || item.requiredRole === role;

  const bottomItems = (NAV.filter((n) => !isGroup(n) && n.bottom && canSee(n)) as NavItem[]);

  return (
    <aside className="flex w-60 shrink-0 flex-col border-r border-sidebar-border bg-sidebar text-sidebar-foreground">
      <div className="flex items-center gap-2 px-5 py-5">
        <div className="flex size-8 items-center justify-center rounded-md bg-sidebar-primary text-sm font-bold text-sidebar-primary-foreground">
          S
        </div>
        <div className="leading-tight">
          <Link
            href="/overview"
            className="block text-base font-bold text-foreground"
          >
            SLPRA
          </Link>
          <p className="text-xs text-muted-foreground">智能辅助生产平台</p>
        </div>
      </div>

      <nav className="flex-1 space-y-1 overflow-y-auto px-3 pb-4">
        {NAV.map((node) => {
          if (isGroup(node)) {
            const visible = node.items.filter(canSee);
            if (visible.length === 0) return null;
            return (
              <div key={node.title} className="pt-4 first:pt-1">
                <p className="px-3 pb-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                  {node.title}
                </p>
                <div className="space-y-1">
                  {visible.map((it) => (
                    <NavLink
                      key={it.href}
                      item={it}
                      active={isActive(pathname, it.href)}
                    />
                  ))}
                </div>
              </div>
            );
          }
          if (!canSee(node) || node.bottom) return null;
          return (
            <div key={node.href} className="pt-1">
              <NavLink item={node} active={isActive(pathname, node.href)} />
            </div>
          );
        })}
      </nav>

      {bottomItems.length > 0 && (
        <div className="border-t border-sidebar-border px-3 py-2">
          <div className="space-y-1">
            {bottomItems.map((it) => (
              <NavLink
                key={it.href}
                item={it}
                active={isActive(pathname, it.href)}
              />
            ))}
          </div>
        </div>
      )}

      <div className="border-t border-sidebar-border px-5 py-3">
        <p className="text-xs text-muted-foreground">v0.1.0 · 内网离线部署</p>
      </div>
    </aside>
  );
}
