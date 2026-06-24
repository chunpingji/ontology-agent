"use client";

import { useCallback, useEffect, useState } from "react";
import {
  getConclusionTrace,
  getDashboard,
  type DashboardData,
  type RuleTrace,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";

const RISK_COLOR: Record<string, string> = {
  HighRisk: "bg-destructive text-destructive-foreground",
  MediumRisk: "bg-warning text-warning-foreground",
  LowRisk: "bg-success text-success-foreground",
};

const REFRESH_MS = 5000; // 近实时刷新（≤5s, FR-026/SC-005）

/** 实时推理看板：相容性热力图 + 排期风险 + 规则链溯源（能力三, T050）。 */
export function RealtimeInferencePanel() {
  const [data, setData] = useState<DashboardData | null>(null);
  const [trace, setTrace] = useState<{ id: string; rules: RuleTrace } | null>(null);

  const refresh = useCallback(() => {
    getDashboard().then(setData).catch(console.error);
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, REFRESH_MS);
    return () => clearInterval(t);
  }, [refresh]);

  const openTrace = async (conclusionId: string) => {
    const rules = await getConclusionTrace(conclusionId);
    setTrace({ id: conclusionId, rules });
  };

  if (!data) return <p className="text-sm text-muted-foreground">加载看板…</p>;

  const equipment = [...new Set(data.compatibility_matrix.map((c) => c.equipment))];
  const products = [...new Set(data.compatibility_matrix.map((c) => c.product))];
  const cellOf = (eq: string | null, p: string | null) =>
    data.compatibility_matrix.find((c) => c.equipment === eq && c.product === p);

  return (
    <div className="space-y-6">
      <div>
        <div className="mb-2 flex items-center justify-between">
          <h3 className="font-semibold">设备 × 产品共线相容性</h3>
          <span className="text-xs text-muted-foreground">
            更新于 {new Date(data.updated_at).toLocaleTimeString()}
          </span>
        </div>
        <div className="overflow-x-auto">
          <table className="text-xs">
            <thead>
              <tr>
                <th className="p-1" />
                {products.map((p) => (
                  <th key={p} className="p-1 font-mono">{p}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {equipment.map((eq) => (
                <tr key={eq}>
                  <td className="p-1 font-mono">{eq}</td>
                  {products.map((p) => {
                    const cell = cellOf(eq, p);
                    return (
                      <td key={p} className="p-0.5">
                        {cell ? (
                          <button
                            className={`h-8 w-16 rounded ${
                              RISK_COLOR[cell.risk_level || ""] || "bg-muted"
                            }`}
                            onClick={() => openTrace(cell.conclusion_id)}
                            title="点击查看规则链"
                          >
                            {cell.risk_level}
                          </button>
                        ) : (
                          <div className="h-8 w-16 rounded bg-muted" />
                        )}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div>
        <h3 className="mb-2 font-semibold">未来排期风险</h3>
        {data.schedule_risks.length === 0 ? (
          <p className="text-sm text-muted-foreground">无排期冲突</p>
        ) : (
          <ul className="space-y-1 text-sm">
            {data.schedule_risks.map((r, i) => (
              <li key={i} className="rounded border border-destructive/40 bg-destructive/10 px-3 py-1.5">
                <span className="font-mono">{r.equipment}</span> · {r.date || "—"} ·{" "}
                {r.detail}
              </li>
            ))}
          </ul>
        )}
      </div>

      {trace && (
        <Card className="p-4">
          <CardContent className="p-0">
            <div className="mb-2 flex items-center justify-between">
              <h3 className="font-semibold">规则链溯源</h3>
              <Button
                variant="ghost"
                size="sm"
                className="h-auto p-0 text-xs text-muted-foreground"
                onClick={() => setTrace(null)}
              >
                关闭
              </Button>
            </div>
            <ol className="space-y-1 text-sm">
              {trace.rules.rules_fired.map((r, i) => (
                <li key={i} className="rounded bg-muted px-2 py-1 font-mono text-xs">
                  {String(r.rule_id)} — {String(r.regulation_ref ?? "")}
                </li>
              ))}
            </ol>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
