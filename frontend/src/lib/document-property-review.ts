import type {
  DocumentAnalysisGraphArtifact, DocumentAnalysisRun, DocumentGraphProperty,
  DocumentPropertyReview, DocumentPropertyReviewReason,
} from "@/lib/api";
import { formatDocumentAnalysisReason } from "./document-analysis";

export const PROPERTY_REVIEW_REASONS: Record<DocumentPropertyReviewReason, string> = {
  incorrect_value: "值或单位错误",
  incorrect_property: "属性类型错误",
  incorrect_subject: "主体归属错误",
  incorrect_scope: "条件或适用范围错误",
  unsupported: "原文不支持该结论",
  other: "其他",
};

export interface PropertyReviewTarget {
  runId: string;
  runRevision: number;
  graphSnapshotId: string;
  property: DocumentGraphProperty;
  subjectLabel: string;
}

export function freezePropertyReviewTarget(
  run: DocumentAnalysisRun, graph: DocumentAnalysisGraphArtifact, property: DocumentGraphProperty,
): PropertyReviewTarget | null {
  if (["queued", "running", "deleting", "deleted", "expired"].includes(run.status)
      || graph.recognition_run_id !== run.recognition_run_id || !graph.graph_snapshot
      || graph.graph_snapshot.snapshot_id !== run.identities.graph_snapshot_id
      || !graph.properties.some((item) => item.candidate_id === property.candidate_id
        && item.revision === property.revision)) return null;
  return {
    runId: run.recognition_run_id, runRevision: run.run_revision,
    graphSnapshotId: graph.graph_snapshot.snapshot_id,
    property: structuredClone(property),
    subjectLabel: graph.entities.find((item) => item.entity_id === property.subject_ref.entity_id
      && item.revision === property.subject_ref.revision)?.label ?? "主体",
  };
}

export function propertyReviewHead(
  heads: DocumentPropertyReview[], property: DocumentGraphProperty,
): DocumentPropertyReview | undefined {
  return heads.find((item) => item.candidate_id === property.candidate_id
    && item.candidate_revision === property.revision);
}

export function newPropertyReviewRequestKey(): string {
  return Array.from(crypto.getRandomValues(new Uint8Array(16)),
    (byte) => byte.toString(16).padStart(2, "0")).join("");
}

export function propertyReviewError(error: unknown): string {
  const message = error instanceof Error ? error.message : String(error);
  const body = message.replace(/^API \d+:\s*/, "");
  try {
    const parsed = JSON.parse(body);
    return typeof parsed.detail === "string" ? parsed.detail
      : parsed.error?.message ?? message;
  } catch { return message; }
}

export function propertyRepairStatus(status: string, runStatus?: string): string {
  if (["queued", "running"].includes(status) && runStatus === "paused") {
    return "局部重识别已暂停，恢复运行后继续";
  }
  return ({ queued: "局部重识别已排队", running: "正在局部重识别",
    completed: "局部重识别已结束，新结果仍待审核", unresolved: "局部重识别未决，待补证或人工处理",
    failed: "局部重识别失败", cancelled: "局部重识别已取消" } as Record<string, string>)[status]
    ?? "局部重识别状态待同步";
}

export function propertyRepairReason(result: Record<string, unknown>): string {
  const code = typeof result.reason_code === "string" ? result.reason_code.trim() : "";
  const reason = typeof result.reason === "string" ? result.reason.trim() : "";
  if (!code && !reason) return "";
  return formatDocumentAnalysisReason(code || reason,
    reason && !/^[a-zA-Z][\w:.-]*$/.test(reason) ? reason
      : "局部重识别尚未完成，请核对运行状态或补充原文依据。");
}
