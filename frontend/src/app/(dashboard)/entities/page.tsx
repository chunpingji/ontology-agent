"use client";

import { useEffect, useState } from "react";
import { searchEntities, getEntity } from "@/lib/api";
import type { EntityShadow, Individual } from "@/lib/api";

export default function EntitiesPage() {
  const [entities, setEntities] = useState<EntityShadow[]>([]);
  const [total, setTotal] = useState(0);
  const [query, setQuery] = useState("");
  const [moduleFilter, setModuleFilter] = useState("");
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<Individual | null>(null);

  const MODULES = ["drug", "equipment", "facility", "contamination", "risk", "cleaning", "integration"];

  useEffect(() => {
    const params: Record<string, string> = { page: String(page), page_size: "20" };
    if (query) params.q = query;
    if (moduleFilter) params.module = moduleFilter;
    searchEntities(params)
      .then((r) => { setEntities(r.items); setTotal(r.total); })
      .catch(console.error);
  }, [query, moduleFilter, page]);

  const handleSelect = (iri: string) => {
    getEntity(iri).then(setSelected).catch(console.error);
  };

  return (
    <div>
      <div className="mb-4 flex gap-3">
        <input
          type="text"
          placeholder="搜索实体..."
          value={query}
          onChange={(e) => { setQuery(e.target.value); setPage(1); }}
          className="rounded-md border px-3 py-1.5 text-sm"
        />
        <select
          value={moduleFilter}
          onChange={(e) => { setModuleFilter(e.target.value); setPage(1); }}
          className="rounded-md border px-3 py-1.5 text-sm"
        >
          <option value="">全部模块</option>
          {MODULES.map((m) => (
            <option key={m} value={m}>{m}</option>
          ))}
        </select>
        <span className="self-center text-sm text-gray-500">共 {total} 个实体</span>
      </div>

      <div className="flex gap-4">
        <div className="flex-1 overflow-auto rounded-lg border bg-white">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b bg-gray-50 text-left text-xs text-gray-500">
                <th className="px-3 py-2">IRI</th>
                <th className="px-3 py-2">标签</th>
                <th className="px-3 py-2">模块</th>
                <th className="px-3 py-2">类</th>
              </tr>
            </thead>
            <tbody>
              {entities.map((e) => (
                <tr
                  key={e.iri}
                  onClick={() => handleSelect(e.iri)}
                  className="cursor-pointer border-b hover:bg-blue-50"
                >
                  <td className="px-3 py-2 font-mono text-xs text-gray-600">
                    {e.iri.split("/").pop()}
                  </td>
                  <td className="px-3 py-2">{e.label_zh || e.label_en || "-"}</td>
                  <td className="px-3 py-2">
                    <span className="rounded bg-gray-100 px-1.5 py-0.5 text-xs">{e.module}</span>
                  </td>
                  <td className="px-3 py-2 text-xs text-gray-500">
                    {e.class_iri.split("/").pop()}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {entities.length === 0 && (
            <p className="p-4 text-center text-sm text-gray-400">暂无数据</p>
          )}
        </div>

        {selected && (
          <div className="w-96 shrink-0 rounded-lg border bg-white p-4">
            <h2 className="mb-2 font-bold">{selected.name}</h2>
            <p className="mb-1 text-xs text-gray-500">{selected.iri}</p>
            {selected.label_zh && <p className="text-sm">{selected.label_zh}</p>}
            <div className="mt-3">
              <h3 className="mb-1 text-sm font-semibold text-gray-500">类型</h3>
              {selected.class_iris.map((c) => (
                <span key={c} className="mr-1 inline-block rounded bg-blue-50 px-2 py-0.5 text-xs text-blue-700">
                  {c.split("/").pop()}
                </span>
              ))}
            </div>
            <div className="mt-3">
              <h3 className="mb-1 text-sm font-semibold text-gray-500">属性</h3>
              <dl className="space-y-1">
                {Object.entries(selected.properties).map(([key, val]) => (
                  <div key={key} className="flex gap-2 text-xs">
                    <dt className="w-40 shrink-0 truncate font-mono text-gray-500">
                      {key.split("/").pop()}
                    </dt>
                    <dd className="text-gray-800">{JSON.stringify(val)}</dd>
                  </div>
                ))}
              </dl>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
