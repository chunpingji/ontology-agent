"use client";

import { useState, type FormEvent } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { DialogFooter } from "@/components/ui/dialog";
import { Field, Choice, Check, ParameterRequirement, RULE_COLUMNS } from "./controls";
import { PARAMETER_KINDS, RECORDING_OPTIONS, ruleDetails, validateOperation, emptyOperation, type Operation, type Parameter, type ParameterKind, type RuleRow, type RuleSection } from "./data";

export function ParameterEditor({ initial, onSave }: { initial: Parameter; onSave: (value: Parameter) => void }) {
  const [value, setValue] = useState(initial);
  const [error, setError] = useState("");
  const patch = (change: Partial<Parameter>) => setValue((current) => ({ ...current, ...change }));
  const submit = (event: FormEvent) => {
    event.preventDefault();
    const operation = { ...emptyOperation("validation", "", "参数"), parameters: [value] };
    const issues = validateOperation(operation);
    if (issues.length) { setError(issues.join(" ")); return; }
    onSave(value);
  };
  return <form className="space-y-4" onSubmit={submit}>
    <div className="grid grid-cols-2 gap-3"><Field label="参数名称"><Input required value={value.name} onChange={(event) => patch({ name: event.target.value })} /></Field><Field label="测量对象"><Input required value={value.target} onChange={(event) => patch({ target: event.target.value })} /></Field>
      <Field label="参数类型"><Choice label="参数类型" value={value.kind} options={PARAMETER_KINDS} onChange={(kind) => patch({ kind: kind as ParameterKind, comparison: kind === "文本" ? "长度上限" : kind === "枚举" ? "允许值" : kind === "布尔" ? "必须为" : "区间", value: kind === "布尔" ? "已建立" : "", upper: "", unit: ["文本", "枚举", "布尔"].includes(kind) ? "—" : kind === "时长" ? "小时" : "" })} /></Field><Field label="单位"><Input value={value.unit} onChange={(event) => patch({ unit: event.target.value })} /></Field></div>
    <div className="space-y-1.5"><p className="text-xs text-muted-foreground">规程要求</p><ParameterRequirement parameter={value} onChange={patch} disabled={false} /></div>
    <Field label="记录方式"><Choice label="记录方式" value={value.recording} options={RECORDING_OPTIONS} onChange={(recording) => patch({ recording })} /></Field>
    <Check label="必填" checked={value.required} onChange={(required) => patch({ required })} />
    {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
    <DialogFooter><Button type="submit">保存参数</Button></DialogFooter>
  </form>;
}

export function RuleEditor({ section, initial, operation, onSave }: { section: RuleSection; initial: RuleRow; operation: Operation; onSave: (value: RuleRow) => void }) {
  const [row, setRow] = useState(initial);
  const details = ruleDetails(row, operation);
  return <form className="space-y-4" onSubmit={(event) => { event.preventDefault(); onSave(row); }}>
    <Field label={RULE_COLUMNS[section][0]}><Input required value={row.name} onChange={(event) => setRow({ ...row, name: event.target.value })} /></Field>
    {RULE_COLUMNS[section].slice(1).map((label, index) => <Field key={label} label={label}><Input required value={details[index]} disabled={index === 0 && Boolean(row.parameterId)} onChange={(event) => setRow({ ...row, details: row.details.map((value, fieldIndex) => fieldIndex === index ? event.target.value : value) })} /></Field>)}
    {row.parameterId && <p className="text-xs text-muted-foreground">此检查引用参数规程，请在过程参数表中修改要求值。</p>}
    {section === "checks" && <Check label="必须通过" checked={row.required} onChange={(required) => setRow({ ...row, required })} />}
    <DialogFooter><Button type="submit">保存{section === "events" ? "事件" : section === "checks" ? "检查项目" : "预案"}</Button></DialogFooter>
  </form>;
}

export function NameEditor({ label, onSave }: { label: string; onSave: (value: string) => void }) {
  const [name, setName] = useState("");
  return <form className="space-y-4" onSubmit={(event) => { event.preventDefault(); if (name.trim()) onSave(name.trim()); }}>
    <Field label={`${label}名称`}><Input autoFocus required value={name} onChange={(event) => setName(event.target.value)} /></Field><DialogFooter><Button type="submit">确认新增</Button></DialogFooter>
  </form>;
}
