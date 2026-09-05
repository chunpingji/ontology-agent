"use client";

import { useEffect } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

import { DocumentAnalysisPanel } from "@/components/analysis/document-analysis-panel";
import { GraphQueryPanel } from "@/components/analysis/graph-query-panel";
import {
  AssessmentPanel,
  MACOCalculator,
  PDECalculator,
} from "@/components/analysis/reasoning-panels";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

type Tab = "reasoning" | "graph" | "document";

const TABS: { key: Tab; label: string }[] = [
  { key: "reasoning", label: "推理" },
  { key: "graph", label: "图谱查询" },
  { key: "document", label: "文档分析" },
];

function isTab(value: string | null): value is Tab {
  return value === "reasoning" || value === "graph" || value === "document";
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

  return (
    <Tabs value={activeTab} onValueChange={handleTabChange}>
      <TabsList className="mb-5">
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
    </Tabs>
  );
}
