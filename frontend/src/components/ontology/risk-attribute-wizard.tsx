"use client";

import { useEffect, useState } from "react";
import {
  createRiskDataProperty,
  getRiskVocabularies,
  type RiskVocabulary,
} from "@/lib/api";

const MANAGED_PREFIX = "https://ontology.pharma-gmp.cn/slpra/core/";

/**
 * 风险属性向导（T037）：基于受控词表（OEB / PDE / 致敏）为类创建风险数据属性，
 * 词表取值由后端 `/risk-vocabularies` 提供（FR-010）。
 */
export function RiskAttributeWizard({
  selectedClassIri,
  onChanged,
  onClose,
}: {
  selectedClassIri: string | null;
  onChanged: () => void;
  onClose?: () => void;
}) {
  const [vocabs, setVocabs] = useState<RiskVocabulary[]>([]);
  const [vocab, setVocab] = useState<string>("");
  const [form, setForm] = useState({ slpra_iri: MANAGED_PREFIX, label: "" });
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getRiskVocabularies()
      .then((v) => {
        setVocabs(v);
        if (v.length) setVocab(v[0].key);
      })
      .catch((e) => setError(String(e)));
  }, []);

  const current = vocabs.find((v) => v.key === vocab);

  const submit = async () => {
    setError(null);
    setMsg(null);
    try {
      await createRiskDataProperty({
        slpra_iri: form.slpra_iri,
        label: form.label,
        domain_iri: selectedClassIri || null,
        vocab,
      });
      setMsg(`已创建风险属性（${vocab}）`);
      onChanged();
      onClose?.();
    } catch (e) {
      setError(String(e));
    }
  };

  return (
    <div className="space-y-2">
      <h3 className="text-sm font-semibold text-gray-700">风险属性向导</h3>
      {error && <p className="rounded bg-red-50 px-2 py-1 text-xs text-red-600">{error}</p>}
      {msg && <p className="rounded bg-green-50 px-2 py-1 text-xs text-green-600">{msg}</p>}

      <select
        value={vocab}
        onChange={(e) => setVocab(e.target.value)}
        className="w-full rounded border px-2 py-1 text-sm"
      >
        {vocabs.map((v) => (
          <option key={v.key} value={v.key}>
            {v.key} — {v.label}
          </option>
        ))}
      </select>

      {current && (
        <div className="flex flex-wrap gap-1">
          {current.values.map((val) => (
            <span key={val} className="rounded bg-rose-50 px-1.5 py-0.5 text-xs text-rose-700">
              {val}
            </span>
          ))}
        </div>
      )}

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
      <div className="flex gap-2">
        <button onClick={submit} className="rounded bg-rose-600 px-3 py-1.5 text-sm text-white hover:bg-rose-700">
          创建风险属性
        </button>
        {onClose && (
          <button
            onClick={onClose}
            className="rounded border px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-50"
          >
            取消
          </button>
        )}
      </div>
    </div>
  );
}
