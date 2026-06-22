"use client";

import { useState } from "react";
import { ConnectorManager } from "@/components/integration/connector-manager";
import { RealtimeInferencePanel } from "@/components/integration/realtime-inference-panel";

type Tab = "dashboard" | "connectors";

export default function IntegrationPage() {
  const [tab, setTab] = useState<Tab>("dashboard");

  return (
    <div>
      <h1 className="mb-4 text-xl font-bold">实时事实源与推理看板</h1>
      <div className="mb-6 flex gap-2 border-b">
        <button
          className={`px-3 py-2 text-sm ${
            tab === "dashboard"
              ? "border-b-2 border-blue-600 font-semibold text-blue-600"
              : "text-gray-500"
          }`}
          onClick={() => setTab("dashboard")}
        >
          实时看板
        </button>
        <button
          className={`px-3 py-2 text-sm ${
            tab === "connectors"
              ? "border-b-2 border-blue-600 font-semibold text-blue-600"
              : "text-gray-500"
          }`}
          onClick={() => setTab("connectors")}
        >
          连接器管理
        </button>
      </div>

      {tab === "dashboard" ? <RealtimeInferencePanel /> : <ConnectorManager />}
    </div>
  );
}
