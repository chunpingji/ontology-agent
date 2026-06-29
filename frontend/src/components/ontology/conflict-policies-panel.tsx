"use client";

import { useCallback, useEffect, useState } from "react";
import {
  listConflictPolicies,
  publishConflictPolicy,
  updateConflictPolicy,
  type TBoxConflictPolicy,
} from "@/lib/api";
import { Field } from "@/components/ontology/field";
import { ConflictDialog } from "@/components/ontology/conflict-dialog";
import { useVersionConflict } from "@/components/ontology/use-version-conflict";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

const DIMENSION_LABEL: Record<string, string> = {
  dedication: "设备专用化裁决",
  risk_level: "风险等级裁决",
};

const NONE = "—";

/**
 * E13 冲突消解策略面板（US3 / FR-011 / FR-016）：元规则即数据。
 * 固定维度集（dedication / risk_level），仅 GET/PUT。翻转 override_direction 或替换
 * priority_lattice 即改变聚合裁决——纯数据，无源码改动。
 */
export function ConflictPoliciesPanel() {
  const [items, setItems] = useState<TBoxConflictPolicy[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [editStrategy, setEditStrategy] = useState("");
  const [editDirection, setEditDirection] = useState<string>(NONE);
  const [editLattice, setEditLattice] = useState("");
  const [editRef, setEditRef] = useState("");
  const [error, setError] = useState<string | null>(null);
  const { conflict, run, clear } = useVersionConflict();

  const load = useCallback(() => {
    listConflictPolicies().then(setItems).catch((e) => setError(String(e)));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const current = items.find((p) => p.dimension === selected) ?? null;

  const pick = (p: TBoxConflictPolicy) => {
    setSelected(p.dimension);
    setEditStrategy(p.strategy);
    setEditDirection(p.override_direction ?? NONE);
    setEditLattice(p.priority_lattice ? JSON.stringify(p.priority_lattice, null, 2) : "");
    setEditRef(p.regulation_ref ?? "");
    setError(null);
  };

  const saveEdit = async () => {
    if (!current) return;
    setError(null);
    let lattice: Record<string, number> | null = null;
    if (editLattice.trim()) {
      try {
        lattice = JSON.parse(editLattice);
      } catch {
        setError("优先级格点 priority_lattice 不是合法 JSON");
        return;
      }
    }
    try {
      const updated = await run(() =>
        updateConflictPolicy(current.dimension, {
          expected_version: current.version,
          strategy: editStrategy || null,
          override_direction: editDirection === NONE ? null : editDirection,
          priority_lattice: lattice,
          regulation_ref: editRef || null,
        }),
      );
      if (updated) load();
    } catch (e) {
      setError(String(e));
    }
  };

  const publishCurrent = async () => {
    if (!current) return;
    setError(null);
    try {
      await run(() => publishConflictPolicy(current.dimension, current.version));
      load();
    } catch (e) {
      setError(String(e));
    }
  };

  return (
    <div className="flex gap-4">
      <ConflictDialog conflict={conflict} onReload={() => { clear(); load(); }} onDismiss={clear} />

      {/* 左：策略列表（固定维度集） */}
      <div className="w-80 shrink-0 space-y-2">
        <h3 className="text-sm font-semibold text-foreground">冲突策略 (E13)</h3>
        <ul className="divide-y rounded border text-sm">
          {items.map((p) => (
            <li key={p.id}>
              <button
                onClick={() => pick(p)}
                className={`flex w-full items-center gap-2 px-2 py-1.5 text-left hover:bg-accent ${
                  selected === p.dimension ? "bg-accent" : ""
                }`}
              >
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-xs">{DIMENSION_LABEL[p.dimension] ?? p.dimension}</span>
                  <span className="block truncate font-mono text-[11px] text-muted-foreground">
                    {p.strategy}
                    {p.override_direction ? ` · ${p.override_direction}` : ""}
                  </span>
                </span>
                <Badge variant="secondary" className="shrink-0 text-[10px]">{p.status}</Badge>
              </button>
            </li>
          ))}
          {items.length === 0 && <li className="px-2 py-2 text-xs text-muted-foreground">暂无策略</li>}
        </ul>
      </div>

      {/* 右：编辑（仅 PUT，无新建/删除） */}
      <div className="flex-1 space-y-3">
        {error && (
          <p className="rounded bg-destructive/10 px-2 py-1 text-xs text-destructive">{error}</p>
        )}

        {current ? (
          <div className="space-y-2 rounded border p-3">
            <div className="flex items-center justify-between">
              <span className="text-sm font-semibold">
                {DIMENSION_LABEL[current.dimension] ?? current.dimension}
                <span className="ml-2 font-mono text-xs text-muted-foreground">{current.dimension}</span>
              </span>
              <span className="text-xs text-muted-foreground">
                v{current.version} · {current.status}
              </span>
            </div>
            <Field label="聚合策略 strategy" hint="safety_override / max_severity …">
              <Input
                value={editStrategy}
                onChange={(e) => setEditStrategy(e.target.value)}
                className="h-auto rounded px-2 py-1 font-mono text-xs"
              />
            </Field>
            <Field label="覆盖方向 override_direction" hint="安全(restrictive)优先 vs 宽松(permissive)优先">
              <Select value={editDirection} onValueChange={setEditDirection}>
                <SelectTrigger className="h-auto rounded px-2 py-1 text-sm">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={NONE} className="text-sm">（不适用）</SelectItem>
                  <SelectItem value="restrictive_wins" className="text-sm">restrictive_wins（安全优先）</SelectItem>
                  <SelectItem value="permissive_wins" className="text-sm">permissive_wins（宽松优先）</SelectItem>
                </SelectContent>
              </Select>
            </Field>
            <Field label="优先级格点 priority_lattice" hint="JSON：等级→严重度；留空表示不用">
              <Textarea
                value={editLattice}
                onChange={(e) => setEditLattice(e.target.value)}
                rows={4}
                className="rounded px-2 py-1 font-mono text-xs"
              />
            </Field>
            <Field label="法规依据 regulation_ref">
              <Input
                value={editRef}
                onChange={(e) => setEditRef(e.target.value)}
                className="h-auto rounded px-2 py-1 text-sm"
              />
            </Field>
            <div className="flex gap-2">
              <Button size="sm" onClick={saveEdit} className="h-auto px-3 py-1.5 text-sm">
                保存改动
              </Button>
              {current.status === "draft" && (
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={publishCurrent}
                  className="h-auto px-3 py-1.5 text-sm"
                >
                  发布
                </Button>
              )}
            </div>
            <p className="text-[11px] text-muted-foreground">
              翻转覆盖方向或替换格点即改变冲突裁决——纯数据，发布后进入正式状态并留审计。
            </p>
          </div>
        ) : (
          <p className="rounded border border-dashed p-6 text-center text-sm text-muted-foreground">
            从左侧选择一个冲突维度查看 / 编辑。
          </p>
        )}
      </div>
    </div>
  );
}
