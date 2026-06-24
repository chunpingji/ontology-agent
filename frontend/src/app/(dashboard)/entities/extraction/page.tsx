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
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

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

// 进行中流持久化（research D1）：把当前关注的作业 id 存入 sessionStorage，
// 切换子 Tab / 组件重挂载后据此重订阅进度流（服务端为权威）。
const ACTIVE_JOB_KEY = "slpra.extraction.activeJobId";
const TERMINAL_STATUS = new Set(["done", "failed"]);

export default function ExtractionPage() {
  const [jobs, setJobs] = useState<ExtractionJob[]>([]);
  const [activeJobId, setActiveJobIdState] = useState<string | null>(null);
  const activeJob = jobs.find((j) => j.id === activeJobId) ?? null;

  const setActiveJobId = (id: string | null) => {
    setActiveJobIdState(id);
    if (typeof window === "undefined") return;
    if (id) window.sessionStorage.setItem(ACTIVE_JOB_KEY, id);
    else window.sessionStorage.removeItem(ACTIVE_JOB_KEY);
  };

  async function refresh() {
    try {
      setJobs(await listExtractionJobs());
    } catch {
      /* ignore */
    }
  }

  // On mount: reload the job list and restore any in-flight job from
  // sessionStorage. Restoring activeJobId remounts <JobProgress>, which
  // re-subscribes to the SSE stream; the server replays the current stage/pct.
  useEffect(() => {
    listExtractionJobs().then(setJobs).catch(() => {});
    const saved =
      typeof window !== "undefined"
        ? window.sessionStorage.getItem(ACTIVE_JOB_KEY)
        : null;
    if (!saved) return;
    getExtractionJob(saved)
      .then((job) => {
        setActiveJobIdState(job.id);
        if (TERMINAL_STATUS.has(job.status)) {
          window.sessionStorage.removeItem(ACTIVE_JOB_KEY);
        }
      })
      .catch(() => {
        window.sessionStorage.removeItem(ACTIVE_JOB_KEY);
      });
  }, []);

  async function handleCreated(job: ExtractionJob) {
    setActiveJobId(job.id);
    await refresh();
  }

  async function handleProgressDone() {
    if (activeJobId) {
      // 终态时刷新该作业与列表，候选数随之更新;并清除进行中标记。
      await getExtractionJob(activeJobId).catch(() => null);
      if (typeof window !== "undefined") {
        window.sessionStorage.removeItem(ACTIVE_JOB_KEY);
      }
    }
    await refresh();
  }

  return (
    <div>
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>创建抽取作业</CardTitle>
          </CardHeader>
          <CardContent>
            <JobCreateForm onCreated={handleCreated} />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>实时进度</CardTitle>
          </CardHeader>
          <CardContent>
            {activeJobId ? (
              <JobProgress key={activeJobId} jobId={activeJobId} onDone={handleProgressDone} />
            ) : (
              <p className="text-sm text-muted-foreground">创建作业后将在此显示各阶段进度。</p>
            )}
          </CardContent>
        </Card>
      </div>

      {activeJob && (activeJob.status === "reviewing" || activeJob.status === "done") && (
        <Card className="mt-6">
          <CardHeader>
            <CardTitle>
              对齐审核 — {activeJob.source_filename ?? activeJob.source_type}
            </CardTitle>
          </CardHeader>
          <CardContent>
            <AlignmentReview key={activeJob.id} jobId={activeJob.id} />
          </CardContent>
        </Card>
      )}

      <Card className="mt-6">
        <CardHeader>
          <CardTitle>抽取作业</CardTitle>
        </CardHeader>
        <CardContent>
          {jobs.length === 0 ? (
            <p className="text-sm text-muted-foreground">暂无作业。</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow className="border-b text-left text-muted-foreground">
                  <TableHead className="py-2">源文件</TableHead>
                  <TableHead className="py-2">类型</TableHead>
                  <TableHead className="py-2">状态</TableHead>
                  <TableHead className="py-2">候选数</TableHead>
                  <TableHead className="py-2"></TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {jobs.map((j) => (
                  <TableRow key={j.id} className="border-b last:border-0">
                    <TableCell className="py-2">{j.source_filename ?? "—"}</TableCell>
                    <TableCell className="py-2">{j.source_type}</TableCell>
                    <TableCell className="py-2">
                      <Badge
                        variant={
                          j.status === "failed"
                            ? "destructive"
                            : j.status === "reviewing" || j.status === "done"
                              ? "success"
                              : "default"
                        }
                      >
                        {STATUS_LABELS[j.status] ?? j.status}
                      </Badge>
                    </TableCell>
                    <TableCell className="py-2">{j.total_candidates}</TableCell>
                    <TableCell className="py-2 text-right">
                      <Button
                        variant="link"
                        size="sm"
                        onClick={() => setActiveJobId(j.id)}
                        className="h-auto p-0 text-xs"
                      >
                        查看进度
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
