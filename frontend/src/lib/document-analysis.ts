import type { DocumentAnalysisStatus, DocumentGraphRanking } from "@/lib/api";

export const DOCUMENT_ANALYSIS_STATUS_LABELS: Record<DocumentAnalysisStatus, string> = {
  queued: "已排队",
  running: "运行中",
  paused: "已暂停",
  finished: "已完成",
  retryable_failure: "可恢复失败",
  blocked_dependency: "依赖阻塞",
  cancelled: "已取消",
  deleting: "正在删除",
  deleted: "已删除",
  expired: "已过期",
};

export function formatDocumentAnalysisDate(value: string | null): string {
  if (!value) return "运行中不自动到期";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString("zh-CN", { hour12: false });
}

const REASON_LABELS: Record<string, string> = {
  ranking_paused: "语义排序已暂停，尚未完成本轮排序。",
  ranking_call_budget_exhausted: "排序请求预算已用完，恢复不会重置已用额度。",
  ranking_token_budget_exhausted: "排序输入 tokens 预算已用完，恢复不会重置已用额度。",
  ranking_timeout: "排序请求超时，本轮排序尚未完成。",
  ranking_input_too_long: "排序输入超过模型允许的长度，本轮排序尚未完成。",
  semantic_ranking_model_unavailable: "语义排序模型不可用，需要检查模型环境。",
  ranking_unavailable: "语义排序能力暂不可用，需要检查模型环境。",
  ranking_model_returned_incomplete_batch: "排序模型返回的批次不完整，本轮结果未提交。",
  ranking_model_returned_nonfinite_score: "排序模型返回了无效分数，本轮结果未提交。",
  ranking_model_identity_changed: "排序模型版本与本次运行的冻结配置不一致。",
  ranking_configuration_changed: "排序配置与本次运行的冻结配置不一致。",
  ranking_environment_changed: "排序环境与本次运行的冻结环境不一致。",
  ranking_cuda_out_of_memory: "GPU 显存不足，本轮排序尚未完成。",
  ranking_cuda_unavailable: "GPU 排序环境不可用。",
  ranking_cuda_device_missing: "配置的排序 GPU 不可见。",
  ranking_cuda_version_mismatch: "CUDA 版本与排序配置不一致。",
  ranking_cuda_architecture_unsupported: "当前 GPU 架构不受排序环境支持。",
  ranking_cuda_kernel_failed: "GPU 排序计算失败。",
  ranking_cuda_precision_override: "GPU 排序精度与冻结配置不一致。",
  ranking_cuda_environment_changed: "GPU 排序环境与冻结配置不一致。",
  ranking_cuda_probe_timeout: "GPU 排序环境检查超时。",
  ranking_cuda_probe_failed: "GPU 排序环境检查失败。",
  ranking_model_placement_mismatch: "排序模型未加载到配置的计算设备。",
  attempted_incomplete: "部分原文已尝试处理，但尚未完成识别或验证。",
  task_budget_exhausted: "本轮任务预算已用完，仍有原文待检查。",
  operator_pause: "运行已按暂停操作停止，已保留检查点和已用额度。",
  queue_exhausted: "本轮范围内的原文检查已结束。",
  counterevidence_unresolved: "仍有反证或冲突待核验，本轮分析尚未完整结束。",
  service_failure: "识别服务不可用，已保留已有结果和真实覆盖。",
  model_interrupted: "模型处理已中断，部分原文尚未完成识别或验证。",
  recognition_model_not_configured: "关系识别模型尚未配置，无法继续识别或验证。",
  fingerprint_mismatch: "运行依赖与冻结配置不一致，已阻止继续执行。",
};

export function formatDocumentAnalysisReason(reason: string, fallback?: string): string {
  if (reason === "ranking_technical_failure" || reason.startsWith("ranking_technical_failure:")) {
    return "排序发生技术故障，本轮排序尚未完成。";
  }
  return REASON_LABELS[reason] || fallback || "分析尚未完整结束，具体原因见技术诊断。";
}

export function documentRankingPauseReasons(ranking?: DocumentGraphRanking): string[] {
  const pending = ranking?.epochs
    .filter((epoch) => epoch.status === "paused" && epoch.reason)
    .map((epoch) => epoch.reason as string) ?? [];
  return [...new Set(pending.length > 0 ? pending : ranking?.reasons ?? [])];
}

export function formatDocumentRankingPause(
  ranking?: DocumentGraphRanking,
  historical = false,
  budgetEnabled = ranking?.budget_enabled ?? true,
): string {
  const reasons = documentRankingPauseReasons(ranking);
  const introduction = historical ? "最近提交的排序快照记录了暂停。" : "语义排序已暂停。";
  const explanations = reasons.map((reason) => {
    if (!budgetEnabled && (
      reason === "ranking_call_budget_exhausted" || reason === "ranking_token_budget_exhausted"
    )) {
      return historical
        ? "此前因排序预算耗尽暂停；预算限制现已禁用，运行正在继续，禁用期间不计账。"
        : "此前因排序预算耗尽暂停；预算限制现已禁用，可显式恢复运行，禁用期间不计账。";
    }
    return formatDocumentAnalysisReason(reason);
  });
  return reasons.length > 0
    ? `${introduction}${[...new Set(explanations)].join(" ")}`
    : `${introduction}尚未提交本轮完整排序。`;
}
