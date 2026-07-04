import { Sidebar } from "@/components/shell/sidebar";
import { TopBar } from "@/components/shell/topbar";

/**
 * Application shell (design.pen app frame) — Sidebar + TopBar around routed
 * content (app-shell contract, FR-001/FR-009). The single-source nav model,
 * role gating and identity switcher live in the shell components.
 */
export default function DashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className="flex min-h-screen bg-background">
      <Sidebar />
      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar />
        <main className="flex-1 overflow-auto p-6">{children}</main>
      </div>
    </div>
  );
}
