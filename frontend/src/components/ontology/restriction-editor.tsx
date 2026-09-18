"use client";

import { useEffect, useRef, useState } from "react";
import {
  createRestriction,
  deleteRestriction,
  getTBoxClass,
  updateRestriction,
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

const PROPERTY_KINDS = ["some", "only", "exactly", "min", "max"];
const CLASS_AXIOM_KINDS = ["disjoint", "equivalent"];
const CARD_KINDS = new Set(["exactly", "min", "max"]);
const FILLER_KINDS = new Set(["some", "only", "disjoint", "equivalent"]);
const CLASS_KINDS = new Set(CLASS_AXIOM_KINDS);
const shortIri = (iri: string) => iri.split(/[/#]/).filter(Boolean).at(-1) || iri;
const emptyForm = (kind: string) => ({ kind, filler_iri: "", cardinality: "" });

type PropertyBinding = {
  propertyIri: string;
  propertyKind: "object" | "data";
  defaultFillerIri: string | null;
};

type Conflict = ReturnType<typeof useVersionConflict>;

/**
 * 约束编辑器（T035）：为类增删改 OWL 约束
 * （some / only / exactly / min / max / 互斥 disjoint / 等价 equivalent）。
 */
export function RestrictionEditor({
  classIri,
  binding,
  conflict,
  onChanged,
}: {
  classIri: string | null;
  // null 仅用于不依赖属性的互斥 / 等价类公理。
  binding: PropertyBinding | null;
  conflict: Conflict;
  onChanged: () => void;
}) {
  const [items, setItems] = useState<TBoxRestriction[]>([]);
  const kinds = binding ? PROPERTY_KINDS : CLASS_AXIOM_KINDS;
  const [form, setForm] = useState(() => emptyForm(kinds[0]));
  const [editing, setEditing] = useState<TBoxRestriction | null>(null);
  const [manual, setManual] = useState(false);
  const [saving, setSaving] = useState(false);
  const active = useRef(false);
  const [error, setError] = useState<string | null>(null);
  const classConstraint = binding === null;
  const needsFiller = FILLER_KINDS.has(form.kind);
  const needsCardinality = CARD_KINDS.has(form.kind);
  const propertyIri = binding?.propertyIri ?? "";
  const fillerIri = !needsFiller ? "" : classConstraint || manual
    ? form.filler_iri.trim() : binding?.defaultFillerIri ?? "";
  const cardinality = needsCardinality && form.cardinality !== ""
    ? Number(form.cardinality) : null;
  const canSave = kinds.includes(form.kind) && (classConstraint || !!propertyIri) && (!needsFiller || !!fillerIri)
    && (!needsCardinality || (cardinality !== null
      && Number.isSafeInteger(cardinality) && cardinality >= 0));
  const visibleItems = items.filter((r) => binding
    ? !CLASS_KINDS.has(r.kind) && r.property_iri === binding.propertyIri
      && (r.property_kind === null || r.property_kind === binding.propertyKind)
    : CLASS_KINDS.has(r.kind));
  const Heading = binding ? "h4" : "h3";
  const targetLabel = binding?.propertyKind === "data" ? "目标数据类型" : "目标类";

  // 父级按类及关系版本重挂载；切换后不让旧请求改变当前选择。
  useEffect(() => {
    active.current = true;
    if (classIri) getTBoxClass(classIri)
      .then((c) => { if (active.current) setItems(c.restrictions); })
      .catch((e) => { if (active.current) setError(String(e)); });
    return () => { active.current = false; };
  }, [classIri]);

  const refresh = () => {
    if (!active.current) return;
    if (classIri) getTBoxClass(classIri)
      .then((c) => { if (active.current) setItems(c.restrictions); }).catch(() => {});
    onChanged();
  };

  const resetForm = () => {
    setEditing(null);
    setForm(emptyForm(kinds[0]));
    setManual(false);
    setError(null);
  };

  const startEdit = (restriction: TBoxRestriction) => {
    setEditing(restriction);
    setForm({
      kind: restriction.kind,
      filler_iri: restriction.filler_iri ?? "",
      cardinality: restriction.cardinality?.toString() ?? "",
    });
    // 编辑保留约束自身的目标类（可比关系 range 更窄）；关系始终由所在行绑定。
    setManual(true);
    setError(null);
  };

  const save = async () => {
    if (!classIri || !canSave || saving) return;
    setError(null);
    setSaving(true);
    try {
      const payload = {
        kind: form.kind,
        property_iri: propertyIri || null,
        property_kind: binding ? (editing ? editing.property_kind : binding.propertyKind) : null,
        filler_iri: fillerIri || null,
        cardinality,
      };
      if (editing) {
        const updated = await conflict.run(() => updateRestriction(editing.id, {
          ...payload, expected_version: editing.version,
        }));
        if (!updated) return;
      } else {
        await createRestriction(classIri, payload);
      }
      if (!active.current) return;
      resetForm();
      refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  };

  const remove = async (r: TBoxRestriction) => {
    if (saving) return;
    setError(null);
    setSaving(true);
    try {
      const done = await conflict.run(() =>
        deleteRestriction(r.id, r.version).then(() => ({ ok: true })),
      );
      if (done && active.current) {
        if (editing?.id === r.id) resetForm();
        refresh();
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  };

  if (!classIri) return <p className="text-xs text-muted-foreground">先选择一个类</p>;

  return (
    <div className="space-y-3">
      <Heading className="text-sm font-semibold text-foreground">{binding ? "类约束" : "类公理"}</Heading>
      <p className="text-xs text-muted-foreground" title={classIri}>
        作用于当前类：{shortIri(classIri)}
        {classConstraint && "；设置类之间的互斥或等价关系。"}
      </p>
      {error && <p className="rounded bg-destructive/10 px-2 py-1 text-xs text-destructive">{error}</p>}

      <ul className="divide-y text-sm">
        {visibleItems.map((r) => (
          <li key={r.id} className={`flex items-center justify-between gap-2 py-1.5 ${editing?.id === r.id ? "bg-primary/5" : ""}`}>
            <span className="text-xs">
              <span className="rounded bg-primary/10 px-1.5 py-0.5 text-primary">{r.kind}</span>
              {r.filler_iri && <span className="ml-1 text-muted-foreground" title={r.filler_iri}>→ {shortIri(r.filler_iri)}</span>}
              {r.cardinality != null && <span className="ml-1 text-muted-foreground">({r.cardinality})</span>}
            </span>
            <div className="flex shrink-0 items-center gap-3">
              <Button onClick={() => startEdit(r)} disabled={saving} variant="link" size="sm"
                className="h-auto p-0 text-xs">
                编辑
              </Button>
              <Button
                onClick={() => remove(r)}
                disabled={saving}
                variant="link"
                size="sm"
                className="h-auto p-0 text-xs text-destructive hover:underline"
              >
                删除
              </Button>
            </div>
          </li>
        ))}
        {visibleItems.length === 0 && <li className="py-2 text-xs text-muted-foreground">{binding ? "当前属性暂无类约束" : "暂无类公理"}</li>}
      </ul>

      <fieldset disabled={saving} className="min-w-0 space-y-2 rounded border bg-muted p-2">
        {editing && <p className="text-xs font-medium">{binding ? "编辑类约束" : "编辑类公理"}</p>}
        <Select
          value={form.kind}
          disabled={saving}
          onValueChange={(value) => setForm({ ...form, kind: value, cardinality: "" })}
        >
          <SelectTrigger aria-label="约束类型" className="h-auto rounded px-2 py-1 text-sm">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {kinds.map((k) => (
              <SelectItem key={k} value={k}>
                {k}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        {binding && (
          <div className="space-y-2 text-xs">
            <p className="break-all text-muted-foreground" title={propertyIri} aria-label="当前约束关系">
              已绑定当前{binding.propertyKind === "object" ? "关系" : "数据属性"}：{shortIri(propertyIri)}
            </p>
            {needsFiller && !manual && (
              <p className="break-all" title={binding.defaultFillerIri ?? undefined}>
                {targetLabel}：{binding.defaultFillerIri ? shortIri(binding.defaultFillerIri) : "尚未配置，请自定义目标"}
              </p>
            )}
            {needsFiller && <Button
              type="button"
              variant="link"
              size="sm"
              className="h-auto p-0 text-xs"
              onClick={() => {
                setManual(!manual);
                setForm({ ...form, filler_iri: binding.defaultFillerIri ?? "" });
              }}
            >
              {manual ? "使用属性默认目标" : `自定义${targetLabel}`}
            </Button>}
          </div>
        )}
        {needsFiller && (classConstraint || manual) && (
          <Input
            aria-label={`约束${targetLabel} IRI`}
            placeholder={`${targetLabel} IRI`}
            value={form.filler_iri}
            onChange={(e) => setForm({ ...form, filler_iri: e.target.value })}
            className="h-auto rounded px-2 py-1 font-mono text-xs"
          />
        )}
        {needsCardinality && (
          <Input
            aria-label="约束基数"
            type="number"
            min={0}
            step={1}
            placeholder="基数"
            value={form.cardinality}
            onChange={(e) => setForm({ ...form, cardinality: e.target.value })}
            className="h-auto rounded px-2 py-1 text-sm"
          />
        )}
        <div className="flex items-center gap-2">
          <Button onClick={save} disabled={!canSave || saving} size="sm" className="h-auto rounded px-3 py-1.5 text-sm">
            {saving ? "保存中…" : editing ? "保存修改" : "添加约束"}
          </Button>
          {editing && (
            <Button onClick={resetForm} disabled={saving} variant="outline" size="sm"
              className="h-auto rounded px-3 py-1.5 text-sm">
              取消编辑
            </Button>
          )}
        </div>
      </fieldset>
    </div>
  );
}
