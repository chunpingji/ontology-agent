"use client";

import { useEffect, useState } from "react";
import { getKGStats, runSPARQL } from "@/lib/api";
import type { KGStats } from "@/lib/api";
import { Card } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableHeader,
  TableBody,
  TableRow,
  TableHead,
  TableCell,
} from "@/components/ui/table";

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
          <Card className="p-4 text-center">
            <p className="text-2xl font-bold text-primary">{stats.total_entities}</p>
            <p className="text-sm text-muted-foreground">总实体数</p>
          </Card>
          {Object.entries(stats.by_module).map(([mod, count]) => (
            <Card key={mod} className="p-4 text-center">
              <p className="text-xl font-bold">{count}</p>
              <p className="text-sm text-muted-foreground">{mod}</p>
            </Card>
          ))}
        </div>
      )}

      <Card className="p-4">
        <h3 className="mb-3 font-semibold">SPARQL 查询</h3>
        <Textarea
          value={sparql}
          onChange={(e) => setSparql(e.target.value)}
          rows={4}
          className="w-full font-mono text-sm"
        />
        <Button
          onClick={handleQuery}
          size="sm"
          className="mt-2"
        >
          执行查询
        </Button>

        {error && <p className="mt-2 text-sm text-destructive">{error}</p>}

        {queryResult && (
          <div className="mt-4 overflow-auto">
            <Table className="w-full text-sm">
              <TableHeader>
                <TableRow className="bg-muted text-left text-xs text-muted-foreground">
                  {queryResult.length > 0 &&
                    Object.keys(queryResult[0]).map((col) => (
                      <TableHead key={col} className="px-3 py-2">{col}</TableHead>
                    ))}
                </TableRow>
              </TableHeader>
              <TableBody>
                {queryResult.map((row, i) => (
                  <TableRow key={i}>
                    {Object.values(row).map((val, j) => (
                      <TableCell key={j} className="px-3 py-1.5 text-xs">
                        {typeof val === "object" ? JSON.stringify(val) : String(val)}
                      </TableCell>
                    ))}
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            <p className="mt-2 text-xs text-muted-foreground">{queryResult.length} 行结果</p>
          </div>
        )}
      </Card>

      <Card className="mt-6 p-6 text-center">
        <p className="text-muted-foreground">力导向图可视化 (D3.js) — Phase 3 实现</p>
        <p className="text-xs text-muted-foreground">节点=实体, 边=对象属性关系</p>
      </Card>
    </div>
  );
}
