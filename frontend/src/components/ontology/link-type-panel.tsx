"use client";

import { useState } from "react";
import { createLinkType } from "@/lib/api";

const MANAGED_PREFIX = "https://ontology.pharma-gmp.cn/slpra/core/";

/**
 * 对象属性 / 关系面板（T033）：创建 domain→range 关系，支持基数与逆属性。
 */
export function LinkTypePanel({
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
    range_iri: "",
    inverse_iri: "",
    min_cardinality: "",
    max_cardinality: "",
  });
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    setError(null);
    setMsg(null);
    try {
      await createLinkType({
        slpra_iri: form.slpra_iri,
        label: form.label,
        domain_iri: form.domain_iri || null,
        range_iri: form.range_iri || null,
        inverse_iri: form.inverse_iri || null,
        min_cardinality: form.min_cardinality === "" ? null : Number(form.min_cardinality),
        max_cardinality: form.max_cardinality === "" ? null : Number(form.max_cardinality),
      });
      setMsg("已创建关系");
      onChanged();
    } catch (e) {
      setError(String(e));
    }
  };

  return (
    <div className="space-y-2">
      <h3 className="text-sm font-semibold text-gray-700">新建对象属性 / 关系</h3>
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
      <input
        placeholder="range IRI"
        value={form.range_iri}
        onChange={(e) => setForm({ ...form, range_iri: e.target.value })}
        className="w-full rounded border px-2 py-1 font-mono text-xs"
      />
      <div className="flex gap-2">
        <input
          placeholder="min 基数"
          value={form.min_cardinality}
          onChange={(e) => setForm({ ...form, min_cardinality: e.target.value })}
          className="w-1/2 rounded border px-2 py-1 text-sm"
        />
        <input
          placeholder="max 基数"
          value={form.max_cardinality}
          onChange={(e) => setForm({ ...form, max_cardinality: e.target.value })}
          className="w-1/2 rounded border px-2 py-1 text-sm"
        />
      </div>
      <input
        placeholder="逆属性 IRI（可选）"
        value={form.inverse_iri}
        onChange={(e) => setForm({ ...form, inverse_iri: e.target.value })}
        className="w-full rounded border px-2 py-1 font-mono text-xs"
      />
      <button onClick={submit} className="rounded bg-blue-600 px-3 py-1.5 text-sm text-white hover:bg-blue-700">
        创建关系
      </button>
    </div>
  );
}
