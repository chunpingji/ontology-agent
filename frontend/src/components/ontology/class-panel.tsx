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
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";

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
        <h3 className="text-sm font-semibold text-foreground">{cls ? "编辑类" : "新建类"}</h3>
        {cls && (
          <span className="flex items-center gap-2 text-xs">
            <Badge variant="secondary">v{cls.version}</Badge>
            <Badge variant="secondary">{cls.status}</Badge>
            {cls.is_reviewed && <Badge variant="success">已审核</Badge>}
            {cls.is_disabled && <Badge variant="destructive">已停用</Badge>}
          </span>
        )}
      </div>

      {error && <p className="rounded bg-destructive/10 px-2 py-1 text-xs text-destructive">{error}</p>}

      <Field label="IRI">
        <Input
          value={form.slpra_iri}
          disabled={!!cls}
          onChange={(e) => setForm({ ...form, slpra_iri: e.target.value })}
          className="h-auto rounded px-2 py-1 font-mono text-xs shadow-none disabled:bg-muted"
        />
      </Field>
      <Field label="标签">
        <Input
          value={form.label}
          onChange={(e) => setForm({ ...form, label: e.target.value })}
          className="h-auto rounded px-2 py-1 text-sm shadow-none"
        />
      </Field>
      <Field label="注释">
        <Textarea
          value={form.comment}
          onChange={(e) => setForm({ ...form, comment: e.target.value })}
          className="min-h-0 rounded px-2 py-1 text-sm shadow-none"
          rows={2}
        />
      </Field>
      <Field label="父类 IRI">
        <Input
          value={form.parent_iri}
          onChange={(e) => setForm({ ...form, parent_iri: e.target.value })}
          className="h-auto rounded px-2 py-1 font-mono text-xs shadow-none"
        />
      </Field>
      <Field label="BFO 范畴">
        <Input
          value={form.bfo_category}
          onChange={(e) => setForm({ ...form, bfo_category: e.target.value })}
          className="h-auto rounded px-2 py-1 text-sm shadow-none"
        />
      </Field>

      <div className="flex flex-wrap gap-2 pt-1">
        <Button onClick={handleSave} size="sm" className="h-auto rounded px-3 py-1.5 text-sm">
          保存
        </Button>
        {cls && (
          <>
            <Button
              variant="outline"
              size="sm"
              onClick={guarded(() => reviewClass(cls.slpra_iri, cls.version))}
              className="h-auto rounded px-3 py-1.5 text-sm text-foreground"
            >
              标记已审核
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={guarded(() => disableClass(cls.slpra_iri, cls.version))}
              className="h-auto rounded px-3 py-1.5 text-sm text-warning hover:bg-warning/10"
            >
              停用
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={guarded(() => deleteClass(cls.slpra_iri, cls.version).then(() => ({ ok: true })))}
              className="h-auto rounded px-3 py-1.5 text-sm text-destructive hover:bg-destructive/10"
            >
              删除
            </Button>
          </>
        )}
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="mb-0.5 block text-xs font-medium text-muted-foreground">{label}</span>
      {children}
    </label>
  );
}
