import { Suspense } from "react";
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
      <Suspense fallback={<AnalysisTabsFallback />}>
        <AnalysisTabs />
      </Suspense>
    </div>
  );
}
