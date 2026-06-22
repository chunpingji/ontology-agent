"use client";

import { useEffect, useState } from "react";
import {
  createExtractionJob,
  listExtractionConfigs,
  type ExtractionConfig,
  type ExtractionJob,
} from "@/lib/api";

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
        if (cs.length && !configId) setConfigId(cs[0].id);
      })
      .catch((e) => setError(String(e)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 选择配置时同步源类型，避免类型与配置不匹配。
  useEffect(() => {
    const cfg = configs.find((c) => c.id === configId);
    if (cfg) setSourceType(cfg.source_type);
  }, [configId, configs]);

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
        <div className="rounded border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700">
          {error}
        </div>
      )}

      <div>
        <label className="mb-1 block text-sm font-medium text-gray-700">抽取配置</label>
        <select
          value={configId}
          onChange={(e) => setConfigId(e.target.value)}
          className="w-full rounded border border-gray-300 px-3 py-2 text-sm"
        >
          <option value="" disabled>
            选择配置…
          </option>
          {configs.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name}（{c.source_type}）
            </option>
          ))}
        </select>
      </div>

      <div>
        <label className="mb-1 block text-sm font-medium text-gray-700">源类型</label>
        <select
          value={sourceType}
          onChange={(e) => setSourceType(e.target.value)}
          className="w-full rounded border border-gray-300 px-3 py-2 text-sm"
        >
          <option value="excel">Excel (.xlsx)</option>
          <option value="word">Word (.docx)</option>
          <option value="database">数据库（只读反射）</option>
        </select>
      </div>

      {!isDb && (
        <div>
          <label className="mb-1 block text-sm font-medium text-gray-700">上传文件</label>
          <input
            type="file"
            accept=".xlsx,.docx"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            className="block w-full text-sm text-gray-600 file:mr-3 file:rounded file:border-0 file:bg-blue-600 file:px-3 file:py-2 file:text-sm file:text-white hover:file:bg-blue-700"
          />
        </div>
      )}

      {isDb && (
        <div>
          <label className="mb-1 block text-sm font-medium text-gray-700">
            数据库源（JSON：dsn_ref 经环境变量注入，凭据不入库）
          </label>
          <textarea
            value={dbSource}
            onChange={(e) => setDbSource(e.target.value)}
            rows={4}
            placeholder='{"dsn_ref": "SLPRA_SOURCE_DSN", "schema_name": "public", "include_tables": ["equipment"]}'
            className="w-full rounded border border-gray-300 px-3 py-2 font-mono text-xs"
          />
        </div>
      )}

      <button
        type="submit"
        disabled={submitting}
        className="rounded bg-blue-600 px-4 py-2 text-sm text-white hover:bg-blue-700 disabled:opacity-50"
      >
        {submitting ? "提交中…" : "创建抽取作业"}
      </button>
    </form>
  );
}
