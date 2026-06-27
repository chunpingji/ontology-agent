"use client";

import { useEffect, useRef, useState } from "react";
import { ExtractionDrawer } from "@/components/extraction/extraction-drawer";
import { JobCreateForm } from "@/components/extraction/job-create-form";
import { JobProgress } from "@/components/extraction/job-progress";
import {
  createAutoExtractionJob,
  getExtractionJob,
  listExtractionJobs,
  rerunAnnotation,
  type ExtractionJob,
} from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
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

const ACTIVE_JOB_KEY = "slpra.extraction.activeJobId";
const TERMINAL_STATUS = new Set(["done", "failed"]);
const CLINICAL_KEYWORDS = ["临床备样", "生产信息", "备样生产"];

export default function ExtractionPage() {
  const [jobs, setJobs] = useState<ExtractionJob[]>([]);
  const [activeJobId, setActiveJobIdState] = useState<string | null>(null);
  const [drawerJobId, setDrawerJobId] = useState<string | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [advancedOpen, setAdvancedOpen] = useState(false);

  const [file, setFile] = useState<File | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [clinicalHint, setClinicalHint] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

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

  function handleFileChange(f: File | null) {
    setFile(f);
    if (f) {
      setClinicalHint(CLINICAL_KEYWORDS.some((kw) => f.name.includes(kw)));
    } else {
      setClinicalHint(false);
    }
  }

  async function handleAutoExtract() {
    if (!file) return;
    setError(null);
    setSubmitting(true);
    try {
      const ext = file.name.toLowerCase();
      const sourceType = ext.endsWith(".docx") ? "word" : "excel";
      const job = await createAutoExtractionJob({ file, source_type: sourceType });
      setActiveJobId(job.id);
      setFile(null);
      setClinicalHint(false);
      if (fileRef.current) fileRef.current.value = "";
      await refresh();
    } catch (err) {
      setError(String(err));
    } finally {
      setSubmitting(false);
    }
  }

  async function handleCreated(job: ExtractionJob) {
    setActiveJobId(job.id);
    await refresh();
  }

  async function handleProgressDone() {
    if (activeJobId) {
      await getExtractionJob(activeJobId).catch(() => null);
      if (typeof window !== "undefined") {
        window.sessionStorage.removeItem(ACTIVE_JOB_KEY);
      }
    }
    await refresh();
  }

  function openDrawer(jobId: string) {
    setDrawerJobId(jobId);
    setDrawerOpen(true);
  }

  return (
    <div>
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>实体抽取</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            {error && (
              <div className="rounded border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
                {error}
              </div>
            )}
            <div className="space-y-1">
              <Label>上传文件</Label>
              <Input
                ref={fileRef}
                type="file"
                accept=".xlsx,.docx"
                onChange={(e) => handleFileChange(e.target.files?.[0] ?? null)}
                className="text-muted-foreground file:mr-3 file:rounded file:border-0 file:bg-primary file:px-3 file:py-2 file:text-sm file:text-primary-foreground hover:file:bg-primary/90"
              />
            </div>
            {clinicalHint && (
              <p className="text-sm text-blue-600">
                检测到临床备样/生产信息文件 — 将抽取所有本体模块的目标类
              </p>
            )}
            <Button onClick={handleAutoExtract} disabled={!file || submitting}>
              {submitting ? "提交中..." : "实体抽取"}
            </Button>
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
              <p className="text-sm text-muted-foreground">上传文件后将在此显示各阶段进度。</p>
            )}
          </CardContent>
        </Card>
      </div>

      <Card className="mt-6">
        <CardHeader className="flex flex-row items-center justify-between">
          <CardTitle className="text-base">高级配置</CardTitle>
          <Button variant="ghost" size="sm" onClick={() => setAdvancedOpen(!advancedOpen)}>
            {advancedOpen ? "收起" : "展开"}
          </Button>
        </CardHeader>
        {advancedOpen && (
          <CardContent>
            <JobCreateForm onCreated={handleCreated} />
          </CardContent>
        )}
      </Card>

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
                    <TableCell className="py-2 text-right space-x-2">
                      <Button
                        variant="link"
                        size="sm"
                        onClick={() => setActiveJobId(j.id)}
                        className="h-auto p-0 text-xs"
                      >
                        查看进度
                      </Button>
                      {j.document_path && (
                        <Button
                          variant="link"
                          size="sm"
                          onClick={() => openDrawer(j.id)}
                          className="h-auto p-0 text-xs"
                        >
                          查看标注
                        </Button>
                      )}
                      {TERMINAL_STATUS.has(j.status) && (
                        <Button
                          variant="link"
                          size="sm"
                          onClick={async () => {
                            await rerunAnnotation(j.id);
                            setActiveJobId(j.id);
                          }}
                          className="h-auto p-0 text-xs"
                        >
                          重新标注
                        </Button>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <ExtractionDrawer
        jobId={drawerJobId}
        open={drawerOpen}
        onOpenChange={setDrawerOpen}
      />
    </div>
  );
}
