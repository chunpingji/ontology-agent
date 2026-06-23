"use client";

import { useEffect, useState } from "react";
import { getKGStats, runSPARQL } from "@/lib/api";
import type { KGStats } from "@/lib/api";

// 图谱查询面板（自 knowledge-graph/page.tsx 迁入, T014）：KG 统计 + SPARQL。
export function GraphQueryPanel() {
  const [stats, setStats] = useState<KGStats | null>(null);
  const [sparql, setSparql] = useState("SELECT ?s ?p ?o WHERE { ?s ?p ?o } LIMIT 20");
  const [queryResult, setQueryResult] = useState<Record<string, unknown>[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getKGStats().then(setStats).catch(console.error);
  }, []);

  const handleQuery = async () => {
    setError(null);
    try {
      const r = await runSPARQL(sparql);
      setQueryResult(r);
    } catch (e) {
      setError(String(e));
      setQueryResult(null);
    }
  };

  return (
    <div>
      {stats && (
        <div className="mb-6 grid grid-cols-4 gap-3">
          <div className="rounded-lg border bg-white p-4 text-center">
            <p className="text-2xl font-bold text-blue-600">{stats.total_entities}</p>
            <p className="text-sm text-gray-500">总实体数</p>
          </div>
          {Object.entries(stats.by_module).map(([mod, count]) => (
            <div key={mod} className="rounded-lg border bg-white p-4 text-center">
              <p className="text-xl font-bold">{count}</p>
              <p className="text-sm text-gray-500">{mod}</p>
            </div>
          ))}
        </div>
      )}

      <div className="rounded-lg border bg-white p-4">
        <h3 className="mb-3 font-semibold">SPARQL 查询</h3>
        <textarea
          value={sparql}
          onChange={(e) => setSparql(e.target.value)}
          rows={4}
          className="w-full rounded border px-3 py-2 font-mono text-sm"
        />
        <button
          onClick={handleQuery}
          className="mt-2 rounded bg-blue-600 px-4 py-1.5 text-sm text-white hover:bg-blue-700"
        >
          执行查询
        </button>

        {error && <p className="mt-2 text-sm text-red-600">{error}</p>}

        {queryResult && (
          <div className="mt-4 overflow-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b bg-gray-50 text-left text-xs text-gray-500">
                  {queryResult.length > 0 &&
                    Object.keys(queryResult[0]).map((col) => (
                      <th key={col} className="px-3 py-2">{col}</th>
                    ))}
                </tr>
              </thead>
              <tbody>
                {queryResult.map((row, i) => (
                  <tr key={i} className="border-b">
                    {Object.values(row).map((val, j) => (
                      <td key={j} className="px-3 py-1.5 text-xs">
                        {typeof val === "object" ? JSON.stringify(val) : String(val)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="mt-2 text-xs text-gray-400">{queryResult.length} 行结果</p>
          </div>
        )}
      </div>

      <div className="mt-6 rounded-lg border bg-white p-6 text-center">
        <p className="text-gray-400">力导向图可视化 (D3.js) — Phase 3 实现</p>
        <p className="text-xs text-gray-300">节点=实体, 边=对象属性关系</p>
      </div>
    </div>
  );
}
