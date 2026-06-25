"use client";

import { useCallback, useEffect, useState } from "react";
import {
  createClassificationCriterion,
  deleteClassificationCriterion,
  listClassificationCriteria,
  updateClassificationCriterion,
  type RulePattern,
  type TBoxClassificationCriterion,
} from "@/lib/api";
import { Field } from "@/components/ontology/field";
import { ConflictDialog } from "@/components/ontology/conflict-dialog";
import { useVersionConflict } from "@/components/ontology/use-version-conflict";
import {
  describePattern,
  RulePatternEditor,
} from "@/components/ontology/rule-pattern-editor";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

const DEFAULT_PATTERN: RulePattern = {
  op: "datatype_facet",
  property: "",
  cmp: "gt",
  value: 0,
};

/**
 * E11 分类判据面板（US3 / FR-016）：充要定义即数据。
 * 改一个阈值 → 即改推断，全程零源码改动；写入限 senior_analyst，乐观并发 409 入对话框。
 */
export function ClassificationCriteriaPanel() {
  const [items, setItems] = useState<TBoxClassificationCriterion[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [editPattern, setEditPattern] = useState<RulePattern>(DEFAULT_PATTERN);
  const [editRef, setEditRef] = useState("");
  const [creating, setCreating] = useState(false);
  const [createForm, setCreateForm] = useState({
    criterion_key: "",
    target_class_iri: "",
    regulation_ref: "",
  });
  const [createPattern, setCreatePattern] = useState<RulePattern>(DEFAULT_PATTERN);
  const [error, setError] = useState<string | null>(null);
  const { conflict, run, clear } = useVersionConflict();

  const load = useCallback(() => {
    listClassificationCriteria().then(setItems).catch((e) => setError(String(e)));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const current = items.find((c) => c.criterion_key === selected) ?? null;

  const pick = (c: TBoxClassificationCriterion) => {
    setSelected(c.criterion_key);
    setEditPattern(c.pattern);
    setEditRef(c.regulation_ref ?? "");
    setCreating(false);
    setError(null);
  };

  const saveEdit = async () => {
    if (!current) return;
    setError(null);
    try {
      const updated = await run(() =>
        updateClassificationCriterion(current.criterion_key, {
          expected_version: current.version,
          pattern: editPattern,
          regulation_ref: editRef || null,
        }),
      );
      if (updated) {
        load();
        setEditPattern(updated.pattern);
      }
    } catch (e) {
      setError(String(e));
    }
  };

  const remove = async () => {
    if (!current) return;
    setError(null);
    try {
      await run(() => deleteClassificationCriterion(current.criterion_key, current.version));
      setSelected(null);
      load();
    } catch (e) {
      setError(String(e));
    }
  };

  const submitCreate = async () => {
    setError(null);
    try {
      const created = await createClassificationCriterion({
        criterion_key: createForm.criterion_key,
        target_class_iri: createForm.target_class_iri,
        pattern: createPattern,
        regulation_ref: createForm.regulation_ref || null,
      });
      setCreating(false);
      setCreateForm({ criterion_key: "", target_class_iri: "", regulation_ref: "" });
      setCreatePattern(DEFAULT_PATTERN);
      load();
      if (created) pick(created);
    } catch (e) {
      setError(String(e));
    }
  };

  return (
    <div className="flex gap-4">
      <ConflictDialog conflict={conflict} onReload={() => { clear(); load(); }} onDismiss={clear} />

      {/* 左：判据列表 */}
      <div className="w-80 shrink-0 space-y-2">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold text-foreground">分类判据 (E11)</h3>
          <Button
            size="sm"
            variant="secondary"
            onClick={() => { setCreating(true); setSelected(null); setError(null); }}
            className="h-auto px-2 py-1 text-xs"
          >
            + 新建
          </Button>
        </div>
        <ul className="divide-y rounded border text-sm">
          {items.map((c) => (
            <li key={c.id}>
              <button
                onClick={() => pick(c)}
                className={`flex w-full items-center gap-2 px-2 py-1.5 text-left hover:bg-accent ${
                  selected === c.criterion_key ? "bg-accent" : ""
                }`}
              >
                <span className="flex min-w-0 flex-1 items-baseline gap-2">
                  <span className="shrink-0 font-mono text-xs">{c.criterion_key}</span>
                  <span className="min-w-0 truncate text-xs text-muted-foreground">
                    {describePattern(c.pattern)}
                  </span>
                </span>
                {c.is_disabled ? (
                  <Badge variant="outline" className="shrink-0 text-[10px]">停用</Badge>
                ) : (
                  <Badge variant="secondary" className="shrink-0 text-[10px]">{c.status}</Badge>
                )}
              </button>
            </li>
          ))}
          {items.length === 0 && <li className="px-2 py-2 text-xs text-muted-foreground">暂无判据</li>}
        </ul>
      </div>

      {/* 右：详情 / 编辑 / 新建 */}
      <div className="flex-1 space-y-3">
        {error && (
          <p className="rounded bg-destructive/10 px-2 py-1 text-xs text-destructive">{error}</p>
        )}

        {creating ? (
          <div className="space-y-2 rounded border p-3">
            <p className="text-xs font-medium text-muted-foreground">新建分类判据</p>
            <Field label="判据键 criterion_key" hint="如 R-DC5">
              <Input
                value={createForm.criterion_key}
                onChange={(e) => setCreateForm({ ...createForm, criterion_key: e.target.value })}
                className="h-auto rounded px-2 py-1 font-mono text-xs"
              />
            </Field>
            <Field label="目标类 target_class_iri" hint="点亮的风险类 slpra_iri">
              <Input
                value={createForm.target_class_iri}
                onChange={(e) => setCreateForm({ ...createForm, target_class_iri: e.target.value })}
                className="h-auto rounded px-2 py-1 font-mono text-xs"
              />
            </Field>
            <Field label="模式 pattern">
              <RulePatternEditor value={createPattern} onChange={setCreatePattern} />
            </Field>
            <Field label="法规依据 regulation_ref" hint="可选">
              <Input
                value={createForm.regulation_ref}
                onChange={(e) => setCreateForm({ ...createForm, regulation_ref: e.target.value })}
                className="h-auto rounded px-2 py-1 text-sm"
              />
            </Field>
            <div className="flex gap-2">
              <Button size="sm" onClick={submitCreate} className="h-auto px-3 py-1.5 text-sm">
                创建
              </Button>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => setCreating(false)}
                className="h-auto px-3 py-1.5 text-sm"
              >
                取消
              </Button>
            </div>
          </div>
        ) : current ? (
          <div className="space-y-2 rounded border p-3">
            <div className="flex items-center justify-between">
              <span className="font-mono text-sm">{current.criterion_key}</span>
              <span className="text-xs text-muted-foreground">
                v{current.version} · {current.status}
              </span>
            </div>
            <p className="text-xs text-muted-foreground">
              目标类：
              <span className="font-mono">
                {current.target_class_label || current.target_class_iri?.split("/").pop() || "—"}
              </span>
            </p>
            <Field label="模式 pattern">
              <RulePatternEditor
                value={editPattern}
                onChange={setEditPattern}
                disabled={current.is_disabled}
              />
            </Field>
            <Field label="法规依据 regulation_ref">
              <Input
                value={editRef}
                onChange={(e) => setEditRef(e.target.value)}
                disabled={current.is_disabled}
                className="h-auto rounded px-2 py-1 text-sm"
              />
            </Field>
            <div className="flex gap-2">
              <Button
                size="sm"
                onClick={saveEdit}
                disabled={current.is_disabled}
                className="h-auto px-3 py-1.5 text-sm"
              >
                保存改动
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={remove}
                disabled={current.is_disabled}
                className="h-auto px-3 py-1.5 text-sm text-destructive"
              >
                停用
              </Button>
            </div>
            <p className="text-[11px] text-muted-foreground">
              改动落草稿（draft 即真源），立即参与推断；进入下一发布批次并留审计。
            </p>
          </div>
        ) : (
          <p className="rounded border border-dashed p-6 text-center text-sm text-muted-foreground">
            从左侧选择一条判据查看 / 编辑，或新建。
          </p>
        )}
      </div>
    </div>
  );
}
