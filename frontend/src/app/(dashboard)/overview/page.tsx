"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { getKGStats, getPendingSignatures, type KGStats } from "@/lib/api";
import { useIdentity } from "@/lib/use-identity";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

const QUICK_LINKS = [
  { href: "/ontology", label: "本体工作台", desc: "TBox 类层次、关系与约束维护", icon: "🧬" },
  { href: "/entities", label: "实体管理", desc: "ABox 实体浏览/检索与文档抽取", icon: "📦" },
  { href: "/analysis", label: "应用分析", desc: "风险推理（PDE/MACO）与图谱查询", icon: "⚙️" },
  { href: "/integration", label: "事实源", desc: "APS/ERP/MES/LIMS 对齐与实时推理", icon: "🔌" },
];

export default function OverviewPage() {
  const { role } = useIdentity();
  const [stats, setStats] = useState<KGStats | null>(null);
  const [pendingCount, setPendingCount] = useState<number | null>(null);

  useEffect(() => {
    getKGStats().then(setStats).catch(() => setStats(null));
  }, []);

  useEffect(() => {
    if (role !== "qa") return; // count only shown for qa; no need to reset
    getPendingSignatures()
      .then((r) => setPendingCount(r.conclusions.length))
      .catch(() => {});
  }, [role]);

  return (
    <div>
      <h1 className="mb-1 text-2xl font-bold">总览</h1>
      <p className="mb-6 text-sm text-muted-foreground">
        临床药物智能辅助生产平台（SLPRA）——本体 → 实体 → 应用 → 治理
      </p>

      <section className="mb-8">
        <h2 className="mb-3 text-sm font-semibold text-muted-foreground">知识图谱概览</h2>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <Card className="rounded-lg p-4 text-center">
            <p className="text-2xl font-bold text-primary">
              {stats ? (
                stats.total_entities
              ) : (
                <Skeleton className="mx-auto h-8 w-12" />
              )}
            </p>
            <p className="text-sm text-muted-foreground">总实体数</p>
          </Card>
          {stats &&
            Object.entries(stats.by_module)
              .slice(0, 6)
              .map(([mod, count]) => (
                <Card key={mod} className="rounded-lg p-4 text-center">
                  <p className="text-xl font-bold">{count}</p>
                  <p className="text-sm text-muted-foreground">{mod}</p>
                </Card>
              ))}
          {role === "qa" && (
            <Link
              href="/approvals"
              className="rounded-lg border border-warning/40 bg-warning/10 p-4 text-center transition hover:shadow-sm"
            >
              <p className="text-2xl font-bold text-warning">
                {pendingCount ?? <Skeleton className="mx-auto h-8 w-12" />}
              </p>
              <p className="text-sm text-warning">待签结论 →</p>
            </Link>
          )}
        </div>
      </section>

      <section>
        <h2 className="mb-3 text-sm font-semibold text-muted-foreground">快捷入口</h2>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {QUICK_LINKS.map((item) => (
            <Link key={item.href} href={item.href}>
              <Card className="rounded-lg border-border p-5 shadow-sm transition hover:shadow-md">
                <div className="mb-2 text-2xl">{item.icon}</div>
                <h3 className="mb-1 font-semibold">{item.label}</h3>
                <p className="text-sm text-muted-foreground">{item.desc}</p>
              </Card>
            </Link>
          ))}
          {role === "qa" && (
            <Link href="/approvals">
              <Card className="rounded-lg border-border p-5 shadow-sm transition hover:shadow-md">
                <div className="mb-2 text-2xl">✅</div>
                <h3 className="mb-1 font-semibold">审批中心</h3>
                <p className="text-sm text-muted-foreground">Part 11 电子签批、QA 拒绝、审计链验真</p>
              </Card>
            </Link>
          )}
        </div>
      </section>
    </div>
  );
}
