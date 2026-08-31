import { Suspense } from "react";
import Link from "next/link";
import { AnalysisTabs } from "@/components/analysis/analysis-tabs";
import { Skeleton } from "@/components/ui/skeleton";

function AnalysisTabsFallback() {
  return (
    <div className="space-y-5">
      <Skeleton className="h-9 w-72" />
      <Skeleton className="h-64 w-full" />
    </div>
  );
}

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
        风险推理、图谱查询，以及独立于抽取作业和知识图谱的 Word 即时结构分析。
      </p>

      <Suspense fallback={<AnalysisTabsFallback />}>
        <AnalysisTabs />
      </Suspense>
    </div>
  );
}
