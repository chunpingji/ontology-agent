"use client";

import { useState } from "react";
import { createDataProperty } from "@/lib/api";

const MANAGED_PREFIX = "https://ontology.pharma-gmp.cn/slpra/core/";
const DATATYPES = ["string", "integer", "float", "boolean", "date", "dateTime"];

/**
 * 数据属性面板（T033）：为类创建带数据类型/单位的字段属性。
 */
export function DataPropertyPanel({
  selectedClassIri,
  onChanged,
}: {
  selectedClassIri: string | null;
  onChanged: () => void;
}) {
  const [form, setForm] = useState({
    slpra_iri: MANAGED_PREFIX,
    label: "",
    domain_iri: selectedClassIri ?? "",
    datatype: "string",
    unit: "",
  });
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    setError(null);
    setMsg(null);
    try {
      await createDataProperty({
        slpra_iri: form.slpra_iri,
        label: form.label,
        domain_iri: form.domain_iri || null,
        datatype: form.datatype,
        unit: form.unit || null,
      });
      setMsg("已创建数据属性");
      onChanged();
    } catch (e) {
      setError(String(e));
    }
  };

  return (
    <div className="space-y-2">
      <h3 className="text-sm font-semibold text-gray-700">新建数据属性</h3>
      {error && <p className="rounded bg-red-50 px-2 py-1 text-xs text-red-600">{error}</p>}
      {msg && <p className="rounded bg-green-50 px-2 py-1 text-xs text-green-600">{msg}</p>}
      <input
        placeholder="IRI"
        value={form.slpra_iri}
        onChange={(e) => setForm({ ...form, slpra_iri: e.target.value })}
        className="w-full rounded border px-2 py-1 font-mono text-xs"
      />
      <input
        placeholder="标签"
        value={form.label}
        onChange={(e) => setForm({ ...form, label: e.target.value })}
        className="w-full rounded border px-2 py-1 text-sm"
      />
      <input
        placeholder="domain IRI"
        value={form.domain_iri}
        onChange={(e) => setForm({ ...form, domain_iri: e.target.value })}
        className="w-full rounded border px-2 py-1 font-mono text-xs"
      />
      <div className="flex gap-2">
        <select
          value={form.datatype}
          onChange={(e) => setForm({ ...form, datatype: e.target.value })}
          className="w-1/2 rounded border px-2 py-1 text-sm"
        >
          {DATATYPES.map((d) => (
            <option key={d} value={d}>
              {d}
            </option>
          ))}
        </select>
        <input
          placeholder="单位（可选）"
          value={form.unit}
          onChange={(e) => setForm({ ...form, unit: e.target.value })}
          className="w-1/2 rounded border px-2 py-1 text-sm"
        />
      </div>
      <button onClick={submit} className="rounded bg-blue-600 px-3 py-1.5 text-sm text-white hover:bg-blue-700">
        创建数据属性
      </button>
    </div>
  );
}
