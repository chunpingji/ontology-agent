"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";
import { useIdentity, type Role } from "@/lib/use-identity";
import { Separator } from "@/components/ui/separator";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

// --- Nav model (single source of truth — see data-model.md §1) --------------
interface NavItem {
  href: string;
  label: string;
  icon?: string;
  requiredRole?: Role;
}
interface NavGroup {
  title: string;
  items: NavItem[];
}
type NavNode = NavItem | NavGroup;

const NAV: NavNode[] = [
  { href: "/overview", label: "总览", icon: "🏠" },
  { href: "/ontology", label: "本体工作台", icon: "🧬" },
  {
    title: "图谱管理",
    items: [
      { href: "/entities", label: "实体管理", icon: "📦" },
      { href: "/analysis", label: "应用分析", icon: "⚙️" },
    ],
  },
  { href: "/integration", label: "事实源", icon: "🔌" },
  { href: "/approvals", label: "审批中心", icon: "✅", requiredRole: "qa" },
];

const ROLES: { value: Role; label: string }[] = [
  { value: "senior_analyst", label: "高级分析师" },
  { value: "operator", label: "操作员" },
  { value: "qa", label: "QA（质量）" },
];

function isGroup(node: NavNode): node is NavGroup {
  return "items" in node;
}

/** Active when the path equals the href or is nested under it (sub-tabs). */
function isActive(pathname: string, href: string): boolean {
  return pathname === href || pathname.startsWith(`${href}/`);
}

function NavLink({
  item,
  active,
  indent,
}: {
  item: NavItem;
  active: boolean;
  indent?: boolean;
}) {
  return (
    <Link
      href={item.href}
      className={cn(
        "block rounded-md px-3 py-2 text-sm transition-colors",
        indent && "ml-2",
        active
          ? "bg-primary/10 font-medium text-primary"
          : "text-muted-foreground hover:bg-muted hover:text-foreground",
      )}
    >
      {item.icon && <span className="mr-2">{item.icon}</span>}
      {item.label}
    </Link>
  );
}

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const { identity, role, setIdentity } = useIdentity();

  const canSee = (item: NavItem) => !item.requiredRole || item.requiredRole === role;

  return (
    <div className="flex min-h-screen">
      <aside className="flex w-56 shrink-0 flex-col border-r border-border bg-card">
        <div className="px-4 py-5">
          <Link href="/overview" className="text-lg font-bold text-primary">
            SLPRA
          </Link>
          <p className="text-xs text-muted-foreground">v0.1.0</p>
        </div>

        <nav className="flex-1 space-y-1 px-2">
          {NAV.map((node) => {
            if (isGroup(node)) {
              const visible = node.items.filter(canSee);
              if (visible.length === 0) return null;
              const groupActive = visible.some((it) => isActive(pathname, it.href));
              return (
                <div key={node.title} className="pt-3">
                  <p
                    className={cn(
                      "px-3 pb-1 text-xs font-semibold uppercase tracking-wide",
                      groupActive ? "text-primary" : "text-muted-foreground",
                    )}
                  >
                    {node.title}
                  </p>
                  <div className="space-y-1">
                    {visible.map((it) => (
                      <NavLink
                        key={it.href}
                        item={it}
                        active={isActive(pathname, it.href)}
                        indent
                      />
                    ))}
                  </div>
                </div>
              );
            }
            if (!canSee(node)) return null;
            return (
              <NavLink key={node.href} item={node} active={isActive(pathname, node.href)} />
            );
          })}
        </nav>

        <div className="p-3">
          <Separator className="mb-3" />
          <label className="mb-1 block text-xs text-muted-foreground">
            当前身份（开发态）
          </label>
          <Select
            value={role}
            onValueChange={(value) =>
              setIdentity({ username: identity.username, role: value })
            }
          >
            <SelectTrigger className="w-full">
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
      </aside>
      <main className="flex-1 overflow-auto p-6">{children}</main>
    </div>
  );
}
