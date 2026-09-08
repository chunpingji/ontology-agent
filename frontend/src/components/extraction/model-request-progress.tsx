"use client";

import { useEffect, useState } from "react";
import type { ModelRequestProgress as RequestProgress } from "@/lib/api";

const stages: Record<string, string> = {
  entity_recall: "识别实体", entity_type_verification: "复核实体类型",
  property_recall: "识别属性", relationship_recall: "识别关系",
  property_binding: "复核属性依据", relationship_binding: "复核关系依据",
  reference_verification: "复核指代", chapter_summary: "生成章节摘要",
};

export function ModelRequestProgress({ request, attempts }: {
  request?: RequestProgress; attempts?: number;
}) {
  const [now, setNow] = useState(() => Date.now() / 1000);
  const active = !!request && ["queued", "running", "retrying"].includes(request.status);
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => window.clearInterval(timer);
  }, [active]);
  if (!request) return null;
  const elapsed = Math.max(0, Math.floor(active
    ? now - request.created_at : request.elapsed_seconds ?? 0));
  const label = request.status === "queued" ? "等待模型空位"
    : request.status === "retrying" ? "准备重试"
    : request.status === "running" ? "等待模型响应"
    : request.status === "cancelled" ? "已取消请求"
    : request.error_code === "model_total_timeout" ? "已达到总等待时限"
    : request.status === "failed" ? "请求未完成" : "请求已返回";
  return <span className="text-xs text-muted-foreground" role="status">
    {stages[request.stage] ?? "模型处理"}：{label}；第 {request.attempt} 次尝试，
    已用 {elapsed} 秒{active && `，剩余预算 ${Math.max(0, Math.ceil(request.deadline_at - now))} 秒`}
    {request.queue_seconds !== undefined && `；排队 ${request.queue_seconds.toFixed(1)} 秒`}
    {attempts !== undefined && `；已记录请求 ${attempts} 次`}
  </span>;
}
