"use client";

import { useState } from "react";
import Link from "next/link";
import { clsx } from "clsx";
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
  const [tab, setTab] = useState<Tab>("reasoning");

  return (
    <div>
      <div className="mb-1 flex items-center justify-between">
        <h1 className="text-xl font-bold">应用分析</h1>
        <Link href="/approvals" className="text-sm text-blue-600 hover:underline">
          前往审批中心 →
        </Link>
      </div>
      <p className="mb-5 text-sm text-gray-500">
        知识图谱的应用 —— 风险推理（PDE/MACO/评估）与图谱查询/统计。
      </p>

      <div className="mb-5 flex gap-1 border-b border-gray-200">
        {TABS.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={clsx(
              "-mb-px border-b-2 px-4 py-2 text-sm transition",
              tab === t.key
                ? "border-blue-600 font-medium text-blue-700"
                : "border-transparent text-gray-500 hover:text-gray-800",
            )}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === "reasoning" ? (
        <div className="space-y-6">
          <AssessmentPanel />
          <div className="grid gap-4 lg:grid-cols-2">
            <PDECalculator />
            <MACOCalculator />
          </div>
        </div>
      ) : (
        <GraphQueryPanel />
      )}
    </div>
  );
}
