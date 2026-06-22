"use client";

import { useEffect, useState } from "react";
import {
  createMapping,
  deleteMapping,
  getMappingHealth,
  getMappings,
  type MappingHealth,
  type TBoxMapping,
} from "@/lib/api";
import type { useVersionConflict } from "./use-version-conflict";

const MAPPING_TYPES = ["slpra_iri", "bfo", "field", "external"];

type Conflict = ReturnType<typeof useVersionConflict>;

/**
 * 映射面板（T036）：维护类的 SLPRA·IRI / BFO / 字段映射，并展示全局健康度徽标
 * （ok / 未映射 / 漂移 / 孤立）。
 */
export function OntologyMappingPanel({
  classIri,
  conflict,
  onChanged,
}: {
  classIri: string | null;
  conflict: Conflict;
  onChanged: () => void;
}) {
  const [maps, setMaps] = useState<TBoxMapping[]>([]);
  const [health, setHealth] = useState<MappingHealth | null>(null);
  const [form, setForm] = useState({ mapping_type: "bfo", target: "", source_system: "" });
  const [error, setError] = useState<string | null>(null);

  const loadHealth = () => getMappingHealth().then(setHealth).catch(() => {});

  useEffect(() => {
    loadHealth();
  }, []);

  // 按 classIri 作为 key 挂载，空白态由初值 [] 覆盖；effect 仅在异步回调内 setState。
  useEffect(() => {
    if (classIri) getMappings(classIri).then(setMaps).catch((e) => setError(String(e)));
  }, [classIri]);

  const refresh = () => {
    if (classIri) getMappings(classIri).then(setMaps).catch(() => {});
    loadHealth();
    onChanged();
  };

  const add = async () => {
    if (!classIri) return;
    setError(null);
    try {
      await createMapping(classIri, {
        mapping_type: form.mapping_type,
        target: form.target,
        source_system: form.source_system || null,
      });
      setForm({ mapping_type: "bfo", target: "", source_system: "" });
      refresh();
    } catch (e) {
      setError(String(e));
    }
  };

  const remove = async (m: TBoxMapping) => {
    setError(null);
    try {
      const done = await conflict.run(() => deleteMapping(m.id, m.version).then(() => ({ ok: true })));
      if (done) refresh();
    } catch (e) {
      setError(String(e));
    }
  };

  const healthBadge = (() => {
    if (!health || !classIri) return null;
    const label = health.ok.includes(classIri)
      ? { t: "映射健全", c: "bg-green-100 text-green-700" }
      : health.drift.includes(classIri)
        ? { t: "映射漂移", c: "bg-amber-100 text-amber-700" }
        : health.orphan.includes(classIri)
          ? { t: "孤立映射", c: "bg-purple-100 text-purple-700" }
          : { t: "未映射", c: "bg-red-100 text-red-700" };
    return <span className={`rounded px-2 py-0.5 text-xs ${label.c}`}>{label.t}</span>;
  })();

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-gray-700">映射</h3>
        {healthBadge}
      </div>
      {error && <p className="rounded bg-red-50 px-2 py-1 text-xs text-red-600">{error}</p>}

      {!classIri ? (
        <p className="text-xs text-gray-400">先选择一个类</p>
      ) : (
        <>
          <ul className="divide-y text-sm">
            {maps.map((m) => (
              <li key={m.id} className="flex items-center justify-between py-1.5">
                <span className="text-xs">
                  <span className="rounded bg-gray-100 px-1.5 py-0.5">{m.mapping_type}</span>
                  <span className="ml-2 font-mono text-gray-600">{m.target}</span>
                </span>
                <button onClick={() => remove(m)} className="text-xs text-red-500 hover:underline">
                  删除
                </button>
              </li>
            ))}
            {maps.length === 0 && <li className="py-2 text-xs text-gray-400">暂无映射</li>}
          </ul>

          <div className="space-y-2 rounded border bg-gray-50 p-2">
            <div className="flex gap-2">
              <select
                value={form.mapping_type}
                onChange={(e) => setForm({ ...form, mapping_type: e.target.value })}
                className="w-1/3 rounded border px-2 py-1 text-sm"
              >
                {MAPPING_TYPES.map((t) => (
                  <option key={t} value={t}>
                    {t}
                  </option>
                ))}
              </select>
              <input
                placeholder="target"
                value={form.target}
                onChange={(e) => setForm({ ...form, target: e.target.value })}
                className="w-2/3 rounded border px-2 py-1 text-sm"
              />
            </div>
            <button onClick={add} className="rounded bg-blue-600 px-3 py-1.5 text-sm text-white hover:bg-blue-700">
              添加映射
            </button>
          </div>

          {health && (
            <div className="flex flex-wrap gap-2 pt-1 text-xs text-gray-500">
              <span>健全 {health.ok.length}</span>
              <span>未映射 {health.unmapped.length}</span>
              <span>漂移 {health.drift.length}</span>
              <span>孤立 {health.orphan.length}</span>
            </div>
          )}
        </>
      )}
    </div>
  );
}
