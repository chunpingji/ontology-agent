"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";

// 实体管理一级子 Tab（data-model.md §2.1）。文档抽取作为 ABox 充实主流程，
// 保持为一级子标签、不下沉。
const TABS = [
  { href: "/entities", label: "实体浏览/检索", exact: true },
  { href: "/entities/extraction", label: "文档抽取" },
];

export default function EntitiesLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();

  return (
    <div>
      <h1 className="mb-4 text-xl font-bold">实体管理</h1>

      <div className="mb-5 flex gap-1 border-b border-border">
        {TABS.map((tab) => {
          const active = tab.exact
            ? pathname === tab.href
            : pathname.startsWith(tab.href);
          return (
            <Link
              key={tab.href}
              href={tab.href}
              className={cn(
                "-mb-px border-b-2 px-4 py-2 text-sm transition-colors",
                active
                  ? "border-primary font-medium text-primary"
                  : "border-transparent text-muted-foreground hover:text-foreground",
              )}
            >
              {tab.label}
            </Link>
          );
        })}
      </div>

      {children}
    </div>
  );
}
