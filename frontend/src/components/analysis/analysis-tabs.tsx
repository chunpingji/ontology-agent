"use client";

import { useEffect } from "react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

import { DocumentAnalysisPanel } from "@/components/analysis/document-analysis-panel";
import { GraphQueryPanel } from "@/components/analysis/graph-query-panel";
import { GraphAnalysisPanel } from "@/components/analysis/graph-analysis-panel";
import {
  AssessmentPanel,
  MACOCalculator,
  PDECalculator,
} from "@/components/analysis/reasoning-panels";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

type Tab = "reasoning" | "graph" | "document" | "graph-analysis";

const TABS: { key: Tab; label: string }[] = [
  { key: "reasoning", label: "推理" },
  { key: "graph", label: "图谱查询" },
  { key: "document", label: "文档分析" },
  { key: "graph-analysis", label: "图谱分析" },
];

function isTab(value: string | null): value is Tab {
  return value === "reasoning" || value === "graph" || value === "document" || value === "graph-analysis";
}

export function AnalysisTabs() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const requestedTab = searchParams.get("tab");
  const activeTab: Tab = isTab(requestedTab) ? requestedTab : "reasoning";

  useEffect(() => {
    if (
      activeTab !== "document" ||
      (!searchParams.has("job_id") && !searchParams.has("node_id"))
    ) {
      return;
    }
    const params = new URLSearchParams(searchParams.toString());
    params.delete("job_id");
    params.delete("node_id");
    const query = params.toString();
    router.replace(query ? `${pathname}?${query}` : pathname, { scroll: false });
  }, [activeTab, pathname, router, searchParams]);

  const handleTabChange = (value: string) => {
    if (!isTab(value)) return;
    const params = new URLSearchParams(searchParams.toString());
    if (value === "reasoning") params.delete("tab");
    else params.set("tab", value);
    params.delete("job_id");
    params.delete("node_id");
    const query = params.toString();
    router.replace(query ? `${pathname}?${query}` : pathname, { scroll: false });
  };

  return <>
    {activeTab !== "graph-analysis" && <>
      <div className="mb-1 flex items-center justify-between">
        <h1 className="text-xl font-bold">应用分析</h1>
        <Link href="/approvals" className="text-sm text-primary hover:underline">前往审批中心 →</Link>
      </div>
      <p className="mb-5 text-sm text-muted-foreground">风险推理、图谱查询、文档分析与图谱分析。</p>
    </>}
    <Tabs value={activeTab} onValueChange={handleTabChange}>
      <TabsList aria-label="应用分析功能" className="mb-5 h-auto max-w-full flex-wrap justify-start">
        {TABS.map((tab) => (
          <TabsTrigger key={tab.key} value={tab.key}>
            {tab.label}
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

      <TabsContent value="document">
        <DocumentAnalysisPanel />
      </TabsContent>

      <TabsContent value="graph-analysis">
        <GraphAnalysisPanel />
      </TabsContent>
    </Tabs>
  </>;
}
