import type {
  DocumentAnalysisStatus, DocumentGraphRanking, DocumentRetrievalDiagnostics,
} from "@/lib/api";

export const DOCUMENT_ANALYSIS_STATUS_LABELS: Record<DocumentAnalysisStatus, string> = {
  queued: "已排队",
  running: "运行中",
  paused: "已暂停",
  finished: "本轮识别完成",
  retryable_failure: "可恢复失败",
  blocked_dependency: "依赖阻塞",
  cancelled: "已取消",
  deleting: "正在删除",
  deleted: "已删除",
  expired: "已过期",
};

type CandidateCoverage = { candidate_policy?: "sparse-candidates-v1" | null };

export function documentCoverageLabel(coverage: CandidateCoverage): string {
  return coverage.candidate_policy === "sparse-candidates-v1" ? "候选任务" : "记录";
}

export function documentCoverageScope(coverage: CandidateCoverage): string {
  return coverage.candidate_policy === "sparse-candidates-v1"
    ? "计数仅包含本轮实际入选的主体—谓词候选任务；同一原文可对应多个任务。未入选原文未核验，本轮结束不表示全文事实已穷尽。"
    : "计数沿用该运行冻结的记录覆盖范围；处理完成不构成全文无关系的证明。";
}

export function documentRetrievalSummary(
  coverage: CandidateCoverage & { retrieval_diagnostics?: DocumentRetrievalDiagnostics },
): string {
  const diagnostics = coverage.retrieval_diagnostics;
  if (!diagnostics) return "该运行未采集剪枝诊断。";
  const scope = coverage.candidate_policy === "sparse-candidates-v1"
    ? "搜索范围中" : "未尝试范围中";
  return `${scope} ${diagnostics.records_soft_pruned} 项因检索相关性暂缓，尚未核验；${diagnostics.records_reactivatable} 项可继续检索。${diagnostics.pruning_quality === "unvalidated" ? "剪枝试运行，待专家校准。" : ""}`;
}

export function formatDocumentAnalysisDate(value: string | null): string {
  if (!value) return "运行中不自动到期";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString("zh-CN", { hour12: false });
}

const REASON_LABELS: Record<string, string> = {
  candidate_search_exhausted: "本轮候选检索和核验已完成；未入选原文尚未核验，不表示全文事实已穷尽。",
  adaptive_search_saturated: "当前检索阶段已结束，仍有原文待检查；可恢复运行以继续检索。",
  evidence_recheck_incomplete: "已找到补充证据，部分关系或属性仍待重新核验。",
  no_new_evidence: "尚未找到可补充的新证据。",
  parent_not_effective: "上级关系尚未通过核验，属性任务暂未展开。",
  field_role_source_missing: "缺少字段或叙述角色的原文证明。",
  type_not_supported: "当前原文尚不能证明对象类型。",
  bridge_entailment_not_supported: "缺少连接主体与对象或值的原文证明。",
  predicate_entailment_not_supported: "原文尚不能证明当前关系或属性。",
  applicability_not_supported: "适用条件尚未核清。",
  constraint_unresolved: "属性的数据类型或单位约束尚未确定。",
  unit_source_missing: "缺少修饰当前数值的原文单位。",
  unit_binding_source_missing: "单位证明未覆盖当前数值及单位来源。",
  unit_binding_not_supported: "原文尚未证明单位属于当前数值。",
  unit_record_mismatch: "单位不属于当前数值所在表达式或对应字段。",
  unit_quote_partial: "单位引用不完整，不能省略前缀、分母或指数。",
  quantity_value_partial: "数值引用截取了另一个数字的一部分。",
  source_unit_conflict: "数值后缀单位与引用的来源单位冲突。",
  unit_unknown: "原文单位尚未登记，无法确定其含义。",
  unit_missing_or_incompatible: "原文单位缺失或与属性要求的单位不兼容。",
  unit_conversion_not_exact: "单位换算无法精确表示，尚未配置舍入规则。",
  scalar_value_required: "区间或比较值不能直接作为精确标量，须独立核验字段角色。",
  datatype_mismatch: "原文值与属性数据类型不一致。",
  evidence_unresolved: "证据尚不充分，需要进一步核验。",
  field_binding_missing: "缺少值与原文字段的对应证据。",
  owner_identity_unproven: "两处信息尚未证明属于同一主体，暂不挂接。",
  owner_original_source_missing: "缺少已识别主体的原始来源引用。",
  owner_field_source_missing: "缺少当前行或字段组中主体的来源引用。",
  field_column_mismatch: "候选值与原文列头不一致。",
  entity_reference_not_specific: "候选引用是状态或分类说明，缺少实际对象指称。",
  field_role_not_supported: "原文尚不能证明该值或对象的字段角色。",
  local_coreference_not_supported: "尚未证明该信息属于当前主体。",
  type_ambiguous: "同一提及的类型尚未确定，后续属性暂未展开。",
  bridge_kind_not_in_task_menu: "模型选择的证明方式不适用于当前关系或属性。",
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
