"use client";

import { useCallback, useEffect, useState } from "react";
import { createAction, getActions, type TBoxAction } from "@/lib/api";
import { Field } from "@/components/ontology/field";

const MANAGED_PREFIX = "https://ontology.pharma-gmp.cn/slpra/core/";

/**
 * Action 面板（T034）：定义操作（actor / target / pre / post / params）。
 * 仅定义，不触发推理（R10）。
 */
export function ActionPanel({ selectedClassIri }: { selectedClassIri: string | null }) {
  const [actions, setActions] = useState<TBoxAction[]>([]);
  const [form, setForm] = useState({
    slpra_iri: MANAGED_PREFIX,
    label: "",
    actor_iri: selectedClassIri ?? "",
    target_iri: "",
  });
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    getActions().then(setActions).catch((e) => setError(String(e)));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const submit = async () => {
    setError(null);
    try {
      await createAction({
        slpra_iri: form.slpra_iri,
        label: form.label,
        actor_iri: form.actor_iri || null,
        target_iri: form.target_iri || null,
      });
      load();
    } catch (e) {
      setError(String(e));
    }
  };

  return (
    <div className="space-y-3">
      <h3 className="text-sm font-semibold text-gray-700">操作 (Action)</h3>
      {error && <p className="rounded bg-red-50 px-2 py-1 text-xs text-red-600">{error}</p>}

      <div className="space-y-2 rounded border bg-gray-50 p-2">
        <p className="text-xs font-medium text-gray-600">新建操作</p>
        <Field label="IRI" hint="操作唯一标识">
          <input
            placeholder="IRI"
            value={form.slpra_iri}
            onChange={(e) => setForm({ ...form, slpra_iri: e.target.value })}
            className="w-full rounded border px-2 py-1 font-mono text-xs"
          />
        </Field>
        <Field label="标签" hint="显示名称">
          <input
            placeholder="标签"
            value={form.label}
            onChange={(e) => setForm({ ...form, label: e.target.value })}
            className="w-full rounded border px-2 py-1 text-sm"
          />
        </Field>
        <div className="flex gap-2">
          <Field label="执行者 actor" hint="发起操作的类 IRI" className="w-1/2">
            <input
              placeholder="actor IRI"
              value={form.actor_iri}
              onChange={(e) => setForm({ ...form, actor_iri: e.target.value })}
              className="w-full rounded border px-2 py-1 font-mono text-xs"
            />
          </Field>
          <Field label="目标 target" hint="操作作用的类 IRI" className="w-1/2">
            <input
              placeholder="target IRI"
              value={form.target_iri}
              onChange={(e) => setForm({ ...form, target_iri: e.target.value })}
              className="w-full rounded border px-2 py-1 font-mono text-xs"
            />
          </Field>
        </div>
        <button onClick={submit} className="rounded bg-blue-600 px-3 py-1.5 text-sm text-white hover:bg-blue-700">
          创建操作
        </button>
      </div>

      <ul className="divide-y text-sm">
        {actions.map((a) => (
          <li key={a.id} className="flex items-center justify-between py-1.5">
            <span>
              <span className="font-mono text-xs text-gray-500">{a.slpra_iri.split("/").pop()}</span>
              {a.label && <span className="ml-2">{a.label}</span>}
            </span>
            <span className="text-xs text-gray-400">{a.status}</span>
          </li>
        ))}
        {actions.length === 0 && <li className="py-2 text-xs text-gray-400">暂无操作</li>}
      </ul>
    </div>
  );
}
