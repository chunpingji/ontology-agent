"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { clsx } from "clsx";

const NAV = [
  { href: "/ontology", label: "本体编辑器" },
  { href: "/entities", label: "实体管理" },
  { href: "/extraction", label: "文档抽取" },
  { href: "/reasoning", label: "推理控制台" },
  { href: "/knowledge-graph", label: "知识图谱" },
  { href: "/integration", label: "事实源" },
];

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();

  return (
    <div className="flex min-h-screen">
      <aside className="w-56 shrink-0 border-r border-gray-200 bg-white">
        <div className="px-4 py-5">
          <Link href="/" className="text-lg font-bold text-blue-700">
            SLPRA
          </Link>
          <p className="text-xs text-gray-400">v0.1.0</p>
        </div>
        <nav className="space-y-1 px-2">
          {NAV.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className={clsx(
                "block rounded-md px-3 py-2 text-sm transition",
                pathname.startsWith(item.href)
                  ? "bg-blue-50 font-medium text-blue-700"
                  : "text-gray-600 hover:bg-gray-50"
              )}
            >
              {item.label}
            </Link>
          ))}
        </nav>
      </aside>
      <main className="flex-1 overflow-auto p-6">{children}</main>
    </div>
  );
}
