"use client";

import { useCallback, useEffect, useState } from "react";
import {
  createDecisionRule,
  deleteDecisionRule,
  DECISION_RULE_GROUPS,
  listDecisionRules,
  updateDecisionRule,
  type DecisionRuleGroup,
  type RulePattern,
  type TBoxDecisionRule,
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
import { Textarea } from "@/components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

const GROUP_LABEL: Record<DecisionRuleGroup, string> = {
  equipment_dedication: "设备专用化 (R-ED)",
  scenario_identification: "场景识别 (R-SC)",
  contamination_risk: "污染风险 (R-CP)",
};

const DEFAULT_ANTECEDENT: RulePattern = {
  op: "boolean_has_value",
  property: "",
  value: true,
};

/**
 * E12 决策规则面板（US3 / FR-016）：产生式 R-ED / R-SC / R-CP 即数据。
 * 调 priority、改前件模式、换法规依据均为纯数据写入；按 rule_group 过滤。
 */
export function DecisionRulesPanel() {
  const [groupFilter, setGroupFilter] = useState<DecisionRuleGroup | "all">("all");
  const [items, setItems] = useState<TBoxDecisionRule[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [editAntecedent, setEditAntecedent] = useState<RulePattern>(DEFAULT_ANTECEDENT);
  const [editPriority, setEditPriority] = useState(100);
  const [editRef, setEditRef] = useState("");
  const [creating, setCreating] = useState(false);
  const [createForm, setCreateForm] = useState({
    rule_key: "",
    rule_group: "equipment_dedication" as DecisionRuleGroup,
    label: "",
    priority: "100",
    regulation_ref: "",
    consequent: '{"requires_dedication": true}',
  });
  const [createAntecedent, setCreateAntecedent] = useState<RulePattern>(DEFAULT_ANTECEDENT);
  const [error, setError] = useState<string | null>(null);
  const { conflict, run, clear } = useVersionConflict();

  const load = useCallback(() => {
    listDecisionRules(groupFilter === "all" ? undefined : groupFilter)
      .then(setItems)
      .catch((e) => setError(String(e)));
  }, [groupFilter]);

  useEffect(() => {
    load();
  }, [load]);

  const current = items.find((r) => r.rule_key === selected) ?? null;

  const pick = (r: TBoxDecisionRule) => {
    setSelected(r.rule_key);
    setEditAntecedent(r.antecedent);
    setEditPriority(r.priority);
    setEditRef(r.regulation_ref ?? "");
    setCreating(false);
    setError(null);
  };

  const saveEdit = async () => {
    if (!current) return;
    setError(null);
    try {
      const updated = await run(() =>
        updateDecisionRule(current.rule_key, {
          expected_version: current.version,
          antecedent: editAntecedent,
          priority: editPriority,
          regulation_ref: editRef || null,
        }),
      );
      if (updated) {
        load();
        setEditAntecedent(updated.antecedent);
      }
    } catch (e) {
      setError(String(e));
    }
  };

  const remove = async () => {
    if (!current) return;
    setError(null);
    try {
      await run(() => deleteDecisionRule(current.rule_key, current.version));
      setSelected(null);
      load();
    } catch (e) {
      setError(String(e));
    }
  };

  const submitCreate = async () => {
    setError(null);
    let consequent: Record<string, unknown>;
    try {
      consequent = JSON.parse(createForm.consequent);
    } catch {
      setError("结论 consequent 不是合法 JSON");
      return;
    }
    try {
      const created = await createDecisionRule({
        rule_key: createForm.rule_key,
        rule_group: createForm.rule_group,
        antecedent: createAntecedent,
        consequent,
        priority: Number(createForm.priority) || 100,
        label: createForm.label || null,
        regulation_ref: createForm.regulation_ref || null,
      });
      setCreating(false);
      load();
      if (created) pick(created);
    } catch (e) {
      setError(String(e));
    }
  };

  return (
    <div className="flex gap-4">
      <ConflictDialog conflict={conflict} onReload={() => { clear(); load(); }} onDismiss={clear} />

      {/* 左：规则列表 + 分组过滤 */}
      <div className="w-80 shrink-0 space-y-2">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold text-foreground">决策规则 (E12)</h3>
          <Button
            size="sm"
            variant="secondary"
            onClick={() => { setCreating(true); setSelected(null); setError(null); }}
            className="h-auto px-2 py-1 text-xs"
          >
            + 新建
          </Button>
        </div>
        <Select value={groupFilter} onValueChange={(v) => setGroupFilter(v as DecisionRuleGroup | "all")}>
          <SelectTrigger className="h-auto rounded px-2 py-1 text-xs">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all" className="text-sm">全部分组</SelectItem>
            {DECISION_RULE_GROUPS.map((g) => (
              <SelectItem key={g} value={g} className="text-sm">{GROUP_LABEL[g]}</SelectItem>
            ))}
          </SelectContent>
        </Select>
        <ul className="divide-y rounded border text-sm">
          {items.map((r) => (
            <li key={r.id}>
              <button
                onClick={() => pick(r)}
                className={`flex w-full items-center gap-2 px-2 py-1.5 text-left hover:bg-accent ${
                  selected === r.rule_key ? "bg-accent" : ""
                }`}
              >
                <span className="min-w-0 flex-1">
                  <span className="font-mono text-xs">{r.rule_key}</span>
                  <span className="ml-2 text-[10px] text-muted-foreground">P{r.priority}</span>
                  <span className="ml-1 block truncate text-xs text-muted-foreground">
                    {describePattern(r.antecedent)}
                  </span>
                </span>
                {r.is_disabled ? (
                  <Badge variant="outline" className="shrink-0 text-[10px]">停用</Badge>
                ) : (
                  <Badge variant="secondary" className="shrink-0 text-[10px]">{r.status}</Badge>
                )}
              </button>
            </li>
          ))}
          {items.length === 0 && <li className="px-2 py-2 text-xs text-muted-foreground">暂无规则</li>}
        </ul>
      </div>

      {/* 右：详情 / 编辑 / 新建 */}
      <div className="flex-1 space-y-3">
        {error && (
          <p className="rounded bg-destructive/10 px-2 py-1 text-xs text-destructive">{error}</p>
        )}

        {creating ? (
          <div className="space-y-2 rounded border p-3">
            <p className="text-xs font-medium text-muted-foreground">新建决策规则</p>
            <div className="flex gap-2">
              <Field label="规则键 rule_key" className="w-1/2">
                <Input
                  value={createForm.rule_key}
                  onChange={(e) => setCreateForm({ ...createForm, rule_key: e.target.value })}
                  className="h-auto rounded px-2 py-1 font-mono text-xs"
                />
              </Field>
              <Field label="分组 rule_group" className="w-1/2">
                <Select
                  value={createForm.rule_group}
                  onValueChange={(v) => setCreateForm({ ...createForm, rule_group: v as DecisionRuleGroup })}
                >
                  <SelectTrigger className="h-auto rounded px-2 py-1 text-sm">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {DECISION_RULE_GROUPS.map((g) => (
                      <SelectItem key={g} value={g} className="text-sm">{GROUP_LABEL[g]}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </Field>
            </div>
            <Field label="前件 antecedent">
              <RulePatternEditor value={createAntecedent} onChange={setCreateAntecedent} />
            </Field>
            <Field label="结论 consequent" hint="JSON，逐字镜像 RuleResult.conclusion">
              <Textarea
                value={createForm.consequent}
                onChange={(e) => setCreateForm({ ...createForm, consequent: e.target.value })}
                rows={3}
                className="rounded px-2 py-1 font-mono text-xs"
              />
            </Field>
            <div className="flex gap-2">
              <Field label="优先级 priority" className="w-1/3">
                <Input
                  type="number"
                  value={createForm.priority}
                  onChange={(e) => setCreateForm({ ...createForm, priority: e.target.value })}
                  className="h-auto rounded px-2 py-1 text-sm"
                />
              </Field>
              <Field label="法规依据 regulation_ref" className="w-2/3">
                <Input
                  value={createForm.regulation_ref}
                  onChange={(e) => setCreateForm({ ...createForm, regulation_ref: e.target.value })}
                  className="h-auto rounded px-2 py-1 text-sm"
                />
              </Field>
            </div>
            <div className="flex gap-2">
              <Button size="sm" onClick={submitCreate} className="h-auto px-3 py-1.5 text-sm">创建</Button>
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
              <span className="font-mono text-sm">{current.rule_key}</span>
              <span className="text-xs text-muted-foreground">
                {GROUP_LABEL[current.rule_group]} · v{current.version} · {current.status}
              </span>
            </div>
            <Field label="前件 antecedent">
              <RulePatternEditor
                value={editAntecedent}
                onChange={setEditAntecedent}
                disabled={current.is_disabled}
              />
            </Field>
            <Field label="结论 consequent" hint="只读（结构随规则组而定）">
              <pre className="overflow-x-auto rounded border bg-muted p-2 font-mono text-[11px]">
                {JSON.stringify(current.consequent, null, 2)}
              </pre>
            </Field>
            <div className="flex gap-2">
              <Field label="优先级 priority" className="w-1/3">
                <Input
                  type="number"
                  value={String(editPriority)}
                  onChange={(e) => setEditPriority(Number(e.target.value))}
                  disabled={current.is_disabled}
                  className="h-auto rounded px-2 py-1 text-sm"
                />
              </Field>
              <Field label="法规依据 regulation_ref" className="w-2/3">
                <Input
                  value={editRef}
                  onChange={(e) => setEditRef(e.target.value)}
                  disabled={current.is_disabled}
                  className="h-auto rounded px-2 py-1 text-sm"
                />
              </Field>
            </div>
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
          </div>
        ) : (
          <p className="rounded border border-dashed p-6 text-center text-sm text-muted-foreground">
            从左侧选择一条决策规则查看 / 编辑，或新建。
          </p>
        )}
      </div>
    </div>
  );
}
