import Link from "next/link";

const NAV_ITEMS = [
  { href: "/ontology", label: "本体编辑器", desc: "浏览和管理 SLPRA 本体类层次", icon: "🧬" },
  { href: "/entities", label: "实体管理", desc: "药品、设备、区域等个体的增删改查", icon: "📦" },
  { href: "/extraction", label: "文档抽取", desc: "从 Excel/Word 文档抽取并对齐实体", icon: "📄" },
  { href: "/reasoning", label: "推理控制台", desc: "运行风险评估、MACO/PDE 计算", icon: "⚙️" },
  { href: "/knowledge-graph", label: "知识图谱", desc: "可视化浏览实体关系网络", icon: "🕸️" },
  { href: "/integration", label: "系统集成", desc: "MES/ERP/LIMS/CTMS 接口规范", icon: "🔌" },
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
