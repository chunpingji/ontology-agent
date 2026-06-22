"use client";

import { useEffect, useState } from "react";
import { AlignmentReview } from "@/components/extraction/alignment-review";
import { JobCreateForm } from "@/components/extraction/job-create-form";
import { JobProgress } from "@/components/extraction/job-progress";
import {
  getExtractionJob,
  listExtractionJobs,
  type ExtractionJob,
} from "@/lib/api";

const STATUS_LABELS: Record<string, string> = {
  pending: "待处理",
  running: "运行中",
  parsing: "解析中",
  extracting: "抽取中",
  aligning: "对齐中",
  reviewing: "待审核",
  done: "完成",
  failed: "失败",
};

export default function ExtractionPage() {
  const [jobs, setJobs] = useState<ExtractionJob[]>([]);
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const activeJob = jobs.find((j) => j.id === activeJobId) ?? null;

  async function refresh() {
    try {
      setJobs(await listExtractionJobs());
    } catch {
      /* ignore */
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  async function handleCreated(job: ExtractionJob) {
    setActiveJobId(job.id);
    await refresh();
  }

  async function handleProgressDone() {
    if (activeJobId) {
      // 终态时刷新该作业与列表，候选数随之更新。
      await getExtractionJob(activeJobId).catch(() => null);
    }
    await refresh();
  }

  return (
    <div>
      <h1 className="mb-4 text-xl font-bold">文档抽取</h1>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <div className="rounded-lg border bg-white p-6">
          <h2 className="mb-3 font-semibold">创建抽取作业</h2>
          <JobCreateForm onCreated={handleCreated} />
        </div>

        <div className="rounded-lg border bg-white p-6">
          <h2 className="mb-3 font-semibold">实时进度</h2>
          {activeJobId ? (
            <JobProgress jobId={activeJobId} onDone={handleProgressDone} />
          ) : (
            <p className="text-sm text-gray-500">创建作业后将在此显示各阶段进度。</p>
          )}
        </div>
      </div>

      {activeJob && (activeJob.status === "reviewing" || activeJob.status === "done") && (
        <div className="mt-6 rounded-lg border bg-white p-6">
          <h2 className="mb-3 font-semibold">对齐审核 — {activeJob.source_filename ?? activeJob.source_type}</h2>
          <AlignmentReview jobId={activeJob.id} />
        </div>
      )}

      <div className="mt-6 rounded-lg border bg-white p-6">
        <h2 className="mb-3 font-semibold">抽取作业</h2>
        {jobs.length === 0 ? (
          <p className="text-sm text-gray-500">暂无作业。</p>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b text-left text-gray-500">
                <th className="py-2">源文件</th>
                <th className="py-2">类型</th>
                <th className="py-2">状态</th>
                <th className="py-2">候选数</th>
                <th className="py-2"></th>
              </tr>
            </thead>
            <tbody>
              {jobs.map((j) => (
                <tr key={j.id} className="border-b last:border-0">
                  <td className="py-2">{j.source_filename ?? "—"}</td>
                  <td className="py-2">{j.source_type}</td>
                  <td className="py-2">
                    <span
                      className={`rounded px-2 py-0.5 text-xs ${
                        j.status === "failed"
                          ? "bg-red-100 text-red-700"
                          : j.status === "reviewing" || j.status === "done"
                            ? "bg-green-100 text-green-700"
                            : "bg-blue-100 text-blue-700"
                      }`}
                    >
                      {STATUS_LABELS[j.status] ?? j.status}
                    </span>
                  </td>
                  <td className="py-2">{j.total_candidates}</td>
                  <td className="py-2 text-right">
                    <button
                      onClick={() => setActiveJobId(j.id)}
                      className="text-xs text-blue-600 hover:underline"
                    >
                      查看进度
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
