"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import type { ASTCoverageDTO } from "@/lib/api";

interface CoverageSummaryCardProps {
  coverage: ASTCoverageDTO;
  onScrollToMissing?: () => void;
}

export function CoverageSummaryCard({ coverage, onScrollToMissing }: CoverageSummaryCardProps) {
  const { total_slots, filled, inferred, missing_required, blank_optional, manual, dismissed } = coverage;
  const completedCount = filled + inferred;
  const pct = total_slots > 0 ? Math.round((completedCount / total_slots) * 100) : 0;
  const hasMissing = missing_required > 0;

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center justify-between text-base">
          <span>覆盖率</span>
          {hasMissing ? (
            <Badge variant="destructive" className="text-xs">
              {missing_required} 个必填缺失
            </Badge>
          ) : (
            <Badge className="bg-green-600 text-xs hover:bg-green-600">素材完备</Badge>
          )}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex items-center gap-2">
          <div className="h-2 flex-1 rounded-full bg-muted overflow-hidden">
            <div
              className="h-full rounded-full bg-green-500 transition-all"
              style={{ width: `${pct}%` }}
            />
          </div>
          <span className="text-sm font-medium tabular-nums">{pct}%</span>
        </div>

        <div className="grid grid-cols-3 gap-2 text-center text-xs">
          <StatusCount label="已填充" count={filled} color="bg-green-500" />
          <StatusCount label="已推断" count={inferred} color="bg-blue-500" />
          <StatusCount label="缺失" count={missing_required} color="bg-red-500" />
          <StatusCount label="手工" count={manual} color="bg-yellow-500" />
          <StatusCount label="可选空" count={blank_optional} color="bg-gray-400" />
          <StatusCount label="不适用" count={dismissed} color="bg-gray-300" />
        </div>

        <div className="text-xs text-muted-foreground">
          共 {total_slots} 个槽位
        </div>

        {hasMissing && onScrollToMissing && (
          <button
            onClick={onScrollToMissing}
            className="text-xs text-blue-600 hover:underline"
          >
            查看缺失详情
          </button>
        )}
      </CardContent>
    </Card>
  );
}

function StatusCount({ label, count, color }: { label: string; count: number; color: string }) {
  return (
    <div className="flex items-center justify-center gap-1">
      <span className={`inline-block h-2 w-2 rounded-full ${color}`} />
      <span className="text-muted-foreground">{label}</span>
      <span className="font-medium">{count}</span>
    </div>
  );
}
