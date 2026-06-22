"use client";

import { useEffect, useState } from "react";
import {
  createClass,
  deleteClass,
  disableClass,
  getTBoxClass,
  reviewClass,
  updateClass,
  type TBoxClass,
} from "@/lib/api";
import type { useVersionConflict } from "./use-version-conflict";

const MANAGED_PREFIX = "https://ontology.pharma-gmp.cn/slpra/core/";

type Conflict = ReturnType<typeof useVersionConflict>;

/**
 * 类面板（T032）：新建 / 编辑标签·注释·父类·BFO 范畴 / 审核 / 停用 / 删除。
 * 所有写操作携带 `expected_version`，409 经 {@link useVersionConflict} 走共享冲突处理。
 */
export function ClassPanel({
  iri,
  conflict,
  onChanged,
}: {
  iri: string | null;
  conflict: Conflict;
  onChanged: (iri?: string) => void;
}) {
  const [cls, setCls] = useState<TBoxClass | null>(null);
  const [form, setForm] = useState({ slpra_iri: MANAGED_PREFIX, label: "", comment: "", parent_iri: "", bfo_category: "" });
  const [error, setError] = useState<string | null>(null);

  // 组件按 `iri` 作为 key 挂载，空白态由 useState 初值覆盖；effect 仅在异步回调
  // 内 setState，避免在 effect 体内同步 setState 触发级联渲染。
  useEffect(() => {
    if (!iri) return;
    getTBoxClass(iri)
      .then((c) => {
        setCls(c);
        setForm({
          slpra_iri: c.slpra_iri,
          label: c.label ?? "",
          comment: c.comment ?? "",
          parent_iri: c.parent_iri ?? "",
          bfo_category: c.bfo_category ?? "",
        });
      })
      .catch((e) => setError(String(e)));
  }, [iri]);

  const reload = () => onChanged(iri ?? undefined);

  const handleSave = async () => {
    setError(null);
    try {
      if (!cls) {
        const created = await createClass({
          slpra_iri: form.slpra_iri,
          label: form.label,
          comment: form.comment || null,
          parent_iri: form.parent_iri || null,
          bfo_category: form.bfo_category || null,
        });
        onChanged(created.slpra_iri);
      } else {
        const updated = await conflict.run(() =>
          updateClass(cls.slpra_iri, {
            expected_version: cls.version,
            label: form.label,
            comment: form.comment || null,
            parent_iri: form.parent_iri || null,
            bfo_category: form.bfo_category || null,
          }),
        );
        if (updated) onChanged(updated.slpra_iri);
      }
    } catch (e) {
      setError(String(e));
    }
  };

  const guarded = (fn: () => Promise<unknown>) => async () => {
    setError(null);
    try {
      const r = await conflict.run(fn);
      if (r) reload();
    } catch (e) {
      setError(String(e));
    }
  };

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-gray-700">{cls ? "编辑类" : "新建类"}</h3>
        {cls && (
          <span className="flex items-center gap-2 text-xs">
            <span className="rounded bg-gray-100 px-2 py-0.5">v{cls.version}</span>
            <span className="rounded bg-gray-100 px-2 py-0.5">{cls.status}</span>
            {cls.is_reviewed && <span className="rounded bg-green-100 px-2 py-0.5 text-green-700">已审核</span>}
            {cls.is_disabled && <span className="rounded bg-red-100 px-2 py-0.5 text-red-700">已停用</span>}
          </span>
        )}
      </div>

      {error && <p className="rounded bg-red-50 px-2 py-1 text-xs text-red-600">{error}</p>}

      <Field label="IRI">
        <input
          value={form.slpra_iri}
          disabled={!!cls}
          onChange={(e) => setForm({ ...form, slpra_iri: e.target.value })}
          className="w-full rounded border px-2 py-1 font-mono text-xs disabled:bg-gray-50"
        />
      </Field>
      <Field label="标签">
        <input
          value={form.label}
          onChange={(e) => setForm({ ...form, label: e.target.value })}
          className="w-full rounded border px-2 py-1 text-sm"
        />
      </Field>
      <Field label="注释">
        <textarea
          value={form.comment}
          onChange={(e) => setForm({ ...form, comment: e.target.value })}
          className="w-full rounded border px-2 py-1 text-sm"
          rows={2}
        />
      </Field>
      <Field label="父类 IRI">
        <input
          value={form.parent_iri}
          onChange={(e) => setForm({ ...form, parent_iri: e.target.value })}
          className="w-full rounded border px-2 py-1 font-mono text-xs"
        />
      </Field>
      <Field label="BFO 范畴">
        <input
          value={form.bfo_category}
          onChange={(e) => setForm({ ...form, bfo_category: e.target.value })}
          className="w-full rounded border px-2 py-1 text-sm"
        />
      </Field>

      <div className="flex flex-wrap gap-2 pt-1">
        <button onClick={handleSave} className="rounded bg-blue-600 px-3 py-1.5 text-sm text-white hover:bg-blue-700">
          保存
        </button>
        {cls && (
          <>
            <button
              onClick={guarded(() => reviewClass(cls.slpra_iri, cls.version))}
              className="rounded border px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-50"
            >
              标记已审核
            </button>
            <button
              onClick={guarded(() => disableClass(cls.slpra_iri, cls.version))}
              className="rounded border px-3 py-1.5 text-sm text-amber-700 hover:bg-amber-50"
            >
              停用
            </button>
            <button
              onClick={guarded(() => deleteClass(cls.slpra_iri, cls.version).then(() => ({ ok: true })))}
              className="rounded border px-3 py-1.5 text-sm text-red-600 hover:bg-red-50"
            >
              删除
            </button>
          </>
        )}
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="mb-0.5 block text-xs font-medium text-gray-500">{label}</span>
      {children}
    </label>
  );
}
