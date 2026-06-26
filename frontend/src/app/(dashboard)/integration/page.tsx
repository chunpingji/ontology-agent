"use client";

import { useState } from "react";
import { ConnectorManager } from "@/components/integration/connector-manager";
import { DocRepoPanel } from "@/components/integration/doc-repo-panel";
import { RealtimeInferencePanel } from "@/components/integration/realtime-inference-panel";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

type Tab = "dashboard" | "connectors" | "documents";

export default function IntegrationPage() {
  const [tab, setTab] = useState<Tab>("dashboard");

  return (
    <div>
      <h1 className="mb-4 text-xl font-bold">实时事实源与推理看板</h1>
      <Tabs value={tab} onValueChange={(v) => setTab(v as Tab)}>
        <div className="mb-6 border-b">
          <TabsList className="h-auto gap-2 bg-transparent p-0">
            <TabsTrigger
              value="dashboard"
              className="rounded-none border-b-2 border-transparent bg-transparent px-3 py-2 text-sm text-muted-foreground shadow-none data-[state=active]:border-primary data-[state=active]:bg-transparent data-[state=active]:font-semibold data-[state=active]:text-primary data-[state=active]:shadow-none"
            >
              实时看板
            </TabsTrigger>
            <TabsTrigger
              value="connectors"
              className="rounded-none border-b-2 border-transparent bg-transparent px-3 py-2 text-sm text-muted-foreground shadow-none data-[state=active]:border-primary data-[state=active]:bg-transparent data-[state=active]:font-semibold data-[state=active]:text-primary data-[state=active]:shadow-none"
            >
              连接器管理
            </TabsTrigger>
            <TabsTrigger
              value="documents"
              className="rounded-none border-b-2 border-transparent bg-transparent px-3 py-2 text-sm text-muted-foreground shadow-none data-[state=active]:border-primary data-[state=active]:bg-transparent data-[state=active]:font-semibold data-[state=active]:text-primary data-[state=active]:shadow-none"
            >
              研发文档溯源
            </TabsTrigger>
          </TabsList>
        </div>

        <TabsContent value="dashboard" className="mt-0">
          <RealtimeInferencePanel />
        </TabsContent>
        <TabsContent value="connectors" className="mt-0">
          <ConnectorManager />
        </TabsContent>
        <TabsContent value="documents" className="mt-0">
          <DocRepoPanel />
        </TabsContent>
      </Tabs>
    </div>
  );
}
