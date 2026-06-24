"use client";

import { useEffect, useState } from "react";
import {
  createRestriction,
  deleteRestriction,
  getTBoxClass,
  type TBoxRestriction,
} from "@/lib/api";
import type { useVersionConflict } from "./use-version-conflict";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

const KINDS = ["some", "only", "exactly", "min", "max", "disjoint", "equivalent"];
const CARD_KINDS = new Set(["exactly", "min", "max"]);
const FILLER_KINDS = new Set(["some", "only", "disjoint", "equivalent"]);

type Conflict = ReturnType<typeof useVersionConflict>;

/**
 * 约束编辑器（T035）：为类增删 OWL 约束
 * （some / only / exactly / min / max / 互斥 disjoint / 等价 equivalent）。
 */
export function RestrictionEditor({
  classIri,
  conflict,
  onChanged,
}: {
  classIri: string | null;
  conflict: Conflict;
  onChanged: () => void;
}) {
  const [items, setItems] = useState<TBoxRestriction[]>([]);
  const [form, setForm] = useState({ kind: "some", property_iri: "", filler_iri: "", cardinality: "" });
  const [error, setError] = useState<string | null>(null);

  // 按 classIri 作为 key 挂载，空白态由初值 [] 覆盖；effect 仅在异步回调内 setState。
  useEffect(() => {
    if (classIri) getTBoxClass(classIri).then((c) => setItems(c.restrictions)).catch((e) => setError(String(e)));
  }, [classIri]);

  const refresh = () => {
    if (classIri) getTBoxClass(classIri).then((c) => setItems(c.restrictions)).catch(() => {});
    onChanged();
  };

  const add = async () => {
    if (!classIri) return;
    setError(null);
    try {
      await createRestriction(classIri, {
        kind: form.kind,
        property_iri: form.property_iri || null,
        property_kind: form.property_iri ? "object" : null,
        filler_iri: form.filler_iri || null,
        cardinality: form.cardinality === "" ? null : Number(form.cardinality),
      });
      setForm({ kind: "some", property_iri: "", filler_iri: "", cardinality: "" });
      refresh();
    } catch (e) {
      setError(String(e));
    }
  };

  const remove = async (r: TBoxRestriction) => {
    setError(null);
    try {
      const done = await conflict.run(() =>
        deleteRestriction(r.id, r.version).then(() => ({ ok: true })),
      );
      if (done) refresh();
    } catch (e) {
      setError(String(e));
    }
  };

  if (!classIri) return <p className="text-xs text-muted-foreground">先选择一个类</p>;

  return (
    <div className="space-y-3">
      <h3 className="text-sm font-semibold text-foreground">约束</h3>
      {error && <p className="rounded bg-destructive/10 px-2 py-1 text-xs text-destructive">{error}</p>}

      <ul className="divide-y text-sm">
        {items.map((r) => (
          <li key={r.id} className="flex items-center justify-between py-1.5">
            <span className="text-xs">
              <span className="rounded bg-primary/10 px-1.5 py-0.5 text-primary">{r.kind}</span>
              <span className="ml-2 font-mono text-muted-foreground">{r.property_iri?.split("/").pop() ?? "—"}</span>
              {r.filler_iri && <span className="ml-1 text-muted-foreground">→ {r.filler_iri.split("/").pop()}</span>}
              {r.cardinality != null && <span className="ml-1 text-muted-foreground">({r.cardinality})</span>}
            </span>
            <Button
              onClick={() => remove(r)}
              variant="link"
              size="sm"
              className="h-auto p-0 text-xs text-destructive hover:underline"
            >
              删除
            </Button>
          </li>
        ))}
        {items.length === 0 && <li className="py-2 text-xs text-muted-foreground">暂无约束</li>}
      </ul>

      <div className="space-y-2 rounded border bg-muted p-2">
        <Select
          value={form.kind}
          onValueChange={(value) => setForm({ ...form, kind: value })}
        >
          <SelectTrigger className="h-auto rounded px-2 py-1 text-sm">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {KINDS.map((k) => (
              <SelectItem key={k} value={k}>
                {k}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        {!["disjoint", "equivalent"].includes(form.kind) && (
          <Input
            placeholder="on property IRI"
            value={form.property_iri}
            onChange={(e) => setForm({ ...form, property_iri: e.target.value })}
            className="h-auto rounded px-2 py-1 font-mono text-xs"
          />
        )}
        {FILLER_KINDS.has(form.kind) && (
          <Input
            placeholder="filler / 目标类 IRI"
            value={form.filler_iri}
            onChange={(e) => setForm({ ...form, filler_iri: e.target.value })}
            className="h-auto rounded px-2 py-1 font-mono text-xs"
          />
        )}
        {CARD_KINDS.has(form.kind) && (
          <Input
            placeholder="基数"
            value={form.cardinality}
            onChange={(e) => setForm({ ...form, cardinality: e.target.value })}
            className="h-auto rounded px-2 py-1 text-sm"
          />
        )}
        <Button onClick={add} size="sm" className="h-auto rounded px-3 py-1.5 text-sm">
          添加约束
        </Button>
      </div>
    </div>
  );
}
