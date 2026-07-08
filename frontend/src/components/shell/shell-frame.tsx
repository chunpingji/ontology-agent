"use client";

import {
  useCallback,
  useRef,
  useState,
  type MouseEvent as ReactMouseEvent,
  type ReactNode,
} from "react";
import { GripVertical } from "lucide-react";

import { Sidebar } from "@/components/shell/sidebar";
import { TopBar } from "@/components/shell/topbar";
import { cn } from "@/lib/utils";

// 侧栏默认宽度（px），与原固定的 w-60 一致；可拖动调整，双击复位。
const DEFAULT_SIDEBAR_WIDTH = 240;
const MIN_SIDEBAR_WIDTH = 180;
const MAX_SIDEBAR_WIDTH = 480;

/**
 * 应用外壳：侧栏 + 可拖动把手 + 右侧内容区。把手在侧栏与右侧区域之间，
 * 允许鼠标拖动调整侧栏宽度（受控 px，容器左边界到鼠标 X 求得，[180, 480] 夹取）。
 */
export function ShellFrame({ children }: { children: ReactNode }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [sidebarWidth, setSidebarWidth] = useState(DEFAULT_SIDEBAR_WIDTH);
  const [resizing, setResizing] = useState(false);

  const startResize = useCallback((e: ReactMouseEvent) => {
    e.preventDefault();
    const container = containerRef.current;
    if (!container) return;
    const rect = container.getBoundingClientRect();
    setResizing(true);
    const onMove = (ev: MouseEvent) => {
      const raw = ev.clientX - rect.left;
      setSidebarWidth(
        Math.min(MAX_SIDEBAR_WIDTH, Math.max(MIN_SIDEBAR_WIDTH, raw)),
      );
    };
    const onUp = () => {
      setResizing(false);
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
  }, []);
  const resetWidth = useCallback(
    () => setSidebarWidth(DEFAULT_SIDEBAR_WIDTH),
    [],
  );

  return (
    <div ref={containerRef} className="flex h-screen overflow-hidden bg-background">
      <Sidebar width={sidebarWidth} />

      {/* ── Splitter: 拖动把手调整侧栏宽度 ──────────────────────────── */}
      <div
        role="separator"
        aria-orientation="vertical"
        onMouseDown={startResize}
        onDoubleClick={resetWidth}
        title="拖动调整侧栏宽度 · 双击复位"
        className={cn(
          "group relative z-20 w-px shrink-0 cursor-col-resize bg-sidebar-border transition-colors",
          resizing ? "bg-primary" : "hover:bg-primary/50",
        )}
      >
        {/* 加宽命中区（1px 线太难抓）*/}
        <div className="absolute inset-y-0 -left-1.5 -right-1.5 z-10" />
        {/* 抓手指示（hover / 拖动时可见）*/}
        <div
          className={cn(
            "absolute left-1/2 top-1/2 z-20 flex h-8 w-3 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full border bg-background shadow-sm transition-opacity",
            resizing ? "opacity-100" : "opacity-0 group-hover:opacity-100",
          )}
        >
          <GripVertical className="size-3 text-muted-foreground" />
        </div>
      </div>

      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar />
        <main className="flex-1 overflow-auto p-6">{children}</main>
      </div>
    </div>
  );
}
