"use client";

import Link from "next/link";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import {
  AssessmentPanel,
  MACOCalculator,
  PDECalculator,
} from "@/components/analysis/reasoning-panels";
import { GraphQueryPanel } from "@/components/analysis/graph-query-panel";

type Tab = "reasoning" | "graph";

const TABS: { key: Tab; label: string }[] = [
  { key: "reasoning", label: "推理" },
  { key: "graph", label: "图谱查询" },
];

export default function AnalysisPage() {
  return (
    <div>
      <div className="mb-1 flex items-center justify-between">
        <h1 className="text-xl font-bold">应用分析</h1>
        <Link href="/approvals" className="text-sm text-primary hover:underline">
          前往审批中心 →
        </Link>
      </div>
      <p className="mb-5 text-sm text-muted-foreground">
        知识图谱的应用 —— 风险推理（PDE/MACO/评估）与图谱查询/统计。
      </p>

      <Tabs defaultValue="reasoning">
        <TabsList className="mb-5">
          {TABS.map((t) => (
            <TabsTrigger key={t.key} value={t.key}>
              {t.label}
            </TabsTrigger>
          ))}
        </TabsList>

        <TabsContent value="reasoning">
          <div className="space-y-6">
            <AssessmentPanel />
            <div className="grid gap-4 lg:grid-cols-2">
              <PDECalculator />
              <MACOCalculator />
            </div>
          </div>
        </TabsContent>

        <TabsContent value="graph">
          <GraphQueryPanel />
        </TabsContent>
      </Tabs>
    </div>
  );
}
