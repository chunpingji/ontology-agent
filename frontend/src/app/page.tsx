import Link from "next/link";

// 注:根路径 `/` 已由 next.config.ts 重定向至 `/overview`;此页作为兜底，
// 链接与标签同步导航定稿（本体工作台 / 应用分析 等），不含旧平铺路由。
const NAV_ITEMS = [
  { href: "/overview", label: "总览", desc: "平台概览与各分区快捷入口", icon: "🏠" },
  { href: "/ontology", label: "本体工作台", desc: "浏览和管理 SLPRA 本体类层次（TBox）", icon: "🧬" },
  { href: "/entities", label: "实体管理", desc: "实体浏览/检索与文档抽取（ABox）", icon: "📦" },
  { href: "/analysis", label: "应用分析", desc: "风险推理（PDE/MACO）与图谱查询/统计", icon: "⚙️" },
  { href: "/integration", label: "事实源", desc: "APS/ERP/MES/LIMS/CTMS 事实源对齐与实时推理", icon: "🔌" },
];

export default function HomePage() {
  return (
    <main className="mx-auto max-w-5xl px-6 py-16">
      <h1 className="mb-2 text-3xl font-bold">临床药物智能辅助生产平台</h1>
      <p className="mb-10 text-gray-600">
        SLPRA — Shared-Line Production Risk Assessment Platform
      </p>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {NAV_ITEMS.map((item) => (
          <Link
            key={item.href}
            href={item.href}
            className="rounded-lg border border-gray-200 bg-white p-5 shadow-sm transition hover:shadow-md"
          >
            <div className="mb-2 text-2xl">{item.icon}</div>
            <h2 className="mb-1 font-semibold">{item.label}</h2>
            <p className="text-sm text-gray-500">{item.desc}</p>
          </Link>
        ))}
      </div>
    </main>
  );
}
