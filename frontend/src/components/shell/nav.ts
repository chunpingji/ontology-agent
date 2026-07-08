import type { LucideIcon } from "lucide-react";
import {
  LayoutDashboard,
  Network,
  Boxes,
  Workflow,
  Plug,
  Waypoints,
  FileStack,
  ClipboardCheck,
  Settings,
  FileText,
  Database,
  Building2,
  UserCog,
  Wrench,
  Factory,
  Users,
  ShieldCheck,
} from "lucide-react";
import type { Role } from "@/lib/use-identity";

/**
 * Single-source navigation / IA model for the app shell (data-model §1).
 *
 * Every destination reachable today MUST appear here (FR-005); `requiredRole`
 * gating is carried over verbatim from the pre-restyle layout (FR-006). New-page
 * entries (数据源接入 /connector, 数据映射 /data-mapping, 报告中心 /reports,
 * 审批 /approval) are added by their own user stories; legacy /integration and
 * /approvals are removed here as their superseding pages ship (redirects in
 * next.config.ts).
 */

export interface NavItem {
  href: string;
  label: string;
  icon?: LucideIcon;
  requiredRole?: Role;
  /** Pinned to the sidebar bottom (outside the scrollable nav). */
  bottom?: boolean;
}

export interface NavGroup {
  title: string;
  items: NavItem[];
}

export type NavNode = NavItem | NavGroup;

export const NAV: NavNode[] = [
  { href: "/overview", label: "总览", icon: LayoutDashboard },
  {
    title: "数据接入",
    items: [
      // 数据源接入 /connector (US2) supersedes 事实源 /integration (redirect in next.config.ts).
      { href: "/connector", label: "数据源接入", icon: Plug },
      { href: "/data-mapping", label: "数据映射", icon: Waypoints },
    ],
  },
  {
    title: "知识图谱",
    items: [
      { href: "/ontology", label: "本体工作台", icon: Network },
      { href: "/entities", label: "实体管理", icon: Boxes },
      { href: "/analysis", label: "应用分析", icon: Workflow },
    ],
  },
  {
    title: "报告与审批",
    items: [
      { href: "/reports", label: "报告中心", icon: FileStack },
      // 审批 /approval supersedes 审批中心 /approvals (redirect in next.config.ts); QA-gated as before.
      { href: "/approval", label: "审批管理", icon: ClipboardCheck },
    ],
  },
  {
    title: "Mock 数据",
    items: [
      { href: "/mock/departments", label: "部门", icon: Building2 },
      { href: "/mock/roles", label: "角色", icon: UserCog },
      { href: "/mock/equipment", label: "设备", icon: Wrench },
      { href: "/mock/production-areas", label: "生产区域", icon: Factory },
      { href: "/mock/assessment-team", label: "评估小组", icon: Users },
      { href: "/mock/approver-team", label: "审批小组", icon: ShieldCheck },
    ],
  },
  { href: "/settings", label: "抽取配置", icon: Settings, bottom: true },
  { href: "/settings/ast-templates", label: "报告模板", icon: FileText, bottom: true },
];

export function isGroup(node: NavNode): node is NavGroup {
  return "items" in node;
}

/** Active when the path equals the href or is nested under it (sub-tabs). */
export function isActive(pathname: string, href: string): boolean {
  return pathname === href || pathname.startsWith(`${href}/`);
}

/**
 * Deepest matching nav label for the top-bar title; falls back to a static map
 * for reachable destinations not present as a top-level nav item.
 */
const STATIC_TITLES: Record<string, string> = {};

export function navTitle(pathname: string): string {
  const flat: NavItem[] = NAV.flatMap((n) => (isGroup(n) ? n.items : [n]));
  const match = flat
    .filter((it) => isActive(pathname, it.href))
    .sort((a, b) => b.href.length - a.href.length)[0];
  if (match) return match.label;
  const staticMatch = Object.keys(STATIC_TITLES)
    .filter((href) => isActive(pathname, href))
    .sort((a, b) => b.length - a.length)[0];
  return staticMatch ? STATIC_TITLES[staticMatch] : "SLPRA 平台";
}
