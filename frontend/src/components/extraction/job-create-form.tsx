"use client";

import { useEffect, useState } from "react";
import {
  createExtractionJob,
  listExtractionConfigs,
  type ExtractionConfig,
  type ExtractionJob,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

/**
 * 抽取作业创建表单（T018, US1）：选择源类型 / 抽取配置 / 上传文件，
 * 调用 `POST /api/extraction/jobs` 真实触发流水线（FR-001/002）。
 */
export function JobCreateForm({ onCreated }: { onCreated: (job: ExtractionJob) => void }) {
  const [configs, setConfigs] = useState<ExtractionConfig[]>([]);
  const [sourceType, setSourceType] = useState("excel");
  const [configId, setConfigId] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [dbSource, setDbSource] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listExtractionConfigs()
      .then((cs) => {
        setConfigs(cs);
        // 默认选中首个配置并同步其源类型，避免类型与配置不匹配。
        if (cs.length && !configId) {
          setConfigId(cs[0].id);
          setSourceType(cs[0].source_type);
        }
      })
      .catch((e) => setError(String(e)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 选择配置时同步源类型，避免类型与配置不匹配。
  const handleConfigChange = (v: string) => {
    setConfigId(v);
    const cfg = configs.find((c) => c.id === v);
    if (cfg) setSourceType(cfg.source_type);
  };

  const isDb = sourceType === "database";

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (!configId) {
      setError("请先选择抽取配置");
      return;
    }
    setSubmitting(true);
    try {
      const job = await createExtractionJob({
        source_type: sourceType,
        config_id: configId,
        file: file ?? undefined,
        db_source: isDb && dbSource ? JSON.parse(dbSource) : undefined,
      });
      onCreated(job);
      setFile(null);
    } catch (err) {
      setError(String(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      {error && (
        <div className="rounded border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
          {error}
        </div>
      )}

      <div className="space-y-1">
        <Label>抽取配置</Label>
        <Select value={configId} onValueChange={handleConfigChange}>
          <SelectTrigger>
            <SelectValue placeholder="选择配置…" />
          </SelectTrigger>
          <SelectContent>
            {configs.map((c) => (
              <SelectItem key={c.id} value={c.id}>
                {c.name}（{c.source_type}）
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <div className="space-y-1">
        <Label>源类型</Label>
        <Select value={sourceType} onValueChange={(v) => setSourceType(v)}>
          <SelectTrigger>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="excel">Excel (.xlsx)</SelectItem>
            <SelectItem value="word">Word (.docx)</SelectItem>
            <SelectItem value="database">数据库（只读反射）</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {!isDb && (
        <div className="space-y-1">
          <Label>上传文件</Label>
          <Input
            type="file"
            accept=".xlsx,.docx"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            className="text-muted-foreground file:mr-3 file:rounded file:border-0 file:bg-primary file:px-3 file:py-2 file:text-sm file:text-primary-foreground hover:file:bg-primary/90"
          />
        </div>
      )}

      {isDb && (
        <div className="space-y-1">
          <Label>数据库源（JSON：dsn_ref 经环境变量注入，凭据不入库）</Label>
          <Textarea
            value={dbSource}
            onChange={(e) => setDbSource(e.target.value)}
            rows={4}
            placeholder='{"dsn_ref": "SLPRA_SOURCE_DSN", "schema_name": "public", "include_tables": ["equipment"]}'
            className="font-mono text-xs"
          />
        </div>
      )}

      <Button type="submit" disabled={submitting}>
        {submitting ? "提交中…" : "创建抽取作业"}
      </Button>
    </form>
  );
}
