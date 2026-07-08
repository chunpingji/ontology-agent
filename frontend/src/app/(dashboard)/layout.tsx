import { AuthGuard } from "@/components/shell/auth-guard";
import { ShellFrame } from "@/components/shell/shell-frame";

/**
 * Application shell (design.pen app frame) — Sidebar + TopBar around routed
 * content (app-shell contract, FR-001/FR-009). The single-source nav model,
 * role gating and identity switcher live in the shell components. The resizable
 * sidebar/content splitter lives in the ShellFrame client component.
 *
 * AuthGuard 包裹整个 shell：未登录（无令牌）时连侧栏/顶栏都不渲染，直接跳登录页。
 */
export default function DashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <AuthGuard>
      <ShellFrame>{children}</ShellFrame>
    </AuthGuard>
  );
}
