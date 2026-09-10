"use client";

import type { ReactNode } from "react";
import { Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { cn } from "@/lib/utils";
import { PARAMETER_KINDS, RECORDING_OPTIONS, ruleDetails, type Operation, type Parameter, type ParameterKind, type RuleRow, type RuleSection } from "./data";

export function Choice({ label, value, options, onChange, disabled, className }: {
  label: string; value: string; options: readonly string[]; onChange: (value: string) => void; disabled?: boolean; className?: string;
}) {
  return <Select value={value} onValueChange={onChange} disabled={disabled}>
    <SelectTrigger aria-label={label} className={cn("h-8 gap-2 bg-background text-xs", className)}><SelectValue placeholder="请选择" /></SelectTrigger>
    <SelectContent>{[...new Set([value, ...options])].filter(Boolean).map((option) => <SelectItem key={option} value={option}>{option}</SelectItem>)}</SelectContent>
  </Select>;
}

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return <label className="grid min-w-0 gap-1.5 text-xs text-muted-foreground"><span>{label}</span>{children}</label>;
}

export function Check({ label, checked, onChange, disabled }: { label: string; checked: boolean; onChange: (value: boolean) => void; disabled?: boolean }) {
  return <label className="flex items-center gap-2 whitespace-nowrap text-xs"><Checkbox checked={checked} onCheckedChange={(value) => onChange(value === true)} disabled={disabled} aria-label={label} />{label}</label>;
}

export function Section({ number, title, description, action, onAdd, disabled, children }: {
  number: string; title: string; description: string; action: string; onAdd: () => void; disabled: boolean; children: ReactNode;
}) {
  return <section aria-label={title} className="min-w-0 space-y-3 rounded-lg border bg-background p-4">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <div className="flex flex-wrap items-center gap-2"><span className="text-xs font-semibold text-primary">{number}</span><h2 className="text-sm font-semibold">{title}</h2><span className="text-xs text-muted-foreground">{description}</span></div>
      <Button variant="ghost" size="sm" className="text-primary" onClick={onAdd} disabled={disabled}><Plus />{action}</Button>
    </div>
    {children}
  </section>;
}

export function ParameterRequirement({ parameter, onChange, disabled }: { parameter: Parameter; onChange: (change: Partial<Parameter>) => void; disabled: boolean }) {
  const numeric = parameter.kind.startsWith("数值") || parameter.kind === "时长";
  const prefix = parameter.kind === "文本" ? "长度上限" : parameter.kind === "枚举" ? "允许值" : "必须为";
  return <div className="flex min-w-56 items-center gap-1.5">
    {numeric ? <Choice label={`${parameter.name}比较类型`} value={parameter.comparison} options={["区间", "不少于", "不超过", "等于"]} onChange={(comparison) => onChange({ comparison })} disabled={disabled} className="w-24 shrink-0" /> : <span className="shrink-0 text-xs text-muted-foreground">{prefix}</span>}
    {parameter.kind === "布尔" ? <Choice label={`${parameter.name}期望值`} value={parameter.value} options={["已建立", "未建立"]} onChange={(value) => onChange({ value })} disabled={disabled} /> : <Input aria-label={`${parameter.name}${parameter.comparison === "区间" ? "下限" : "要求值"}`} value={parameter.value} onChange={(event) => onChange({ value: event.target.value })} className={cn("h-8 text-xs", numeric ? "min-w-16" : "min-w-24")} disabled={disabled} />}
    {numeric && parameter.comparison === "区间" && <><span className="text-xs text-muted-foreground">至</span><Input aria-label={`${parameter.name}上限`} value={parameter.upper} onChange={(event) => onChange({ upper: event.target.value })} className="h-8 min-w-16 text-xs" disabled={disabled} /></>}
    {parameter.kind === "文本" && <span className="text-xs text-muted-foreground">字</span>}
  </div>;
}

export function ParameterTable({ parameters, disabled, onChange, onEdit }: {
  parameters: Parameter[]; disabled: boolean; onChange: (id: string, change: Partial<Parameter>) => void; onEdit: (parameter: Parameter) => void;
}) {
  const changeKind = (id: string, kind: ParameterKind) => onChange(id, {
    kind, comparison: kind === "文本" ? "长度上限" : kind === "布尔" ? "必须为" : kind === "枚举" ? "允许值" : "区间",
    value: kind === "布尔" ? "已建立" : "", upper: "", unit: ["枚举", "布尔", "文本"].includes(kind) ? "—" : kind === "时长" ? "小时" : "",
  });
  return <Table className="min-w-[1050px] text-xs">
    <TableHeader className="bg-muted/70"><TableRow>{["参数名称 / 测量对象", "参数类型", "单位", "规程要求（随类型变化）", "记录方式 / 采集频率", "填写要求", "管理"].map((title) => <TableHead key={title} className="h-9 whitespace-nowrap text-xs">{title}</TableHead>)}</TableRow></TableHeader>
    <TableBody>{parameters.map((parameter) => <TableRow key={parameter.id} className={cn(!parameter.enabled && "bg-muted/40 text-muted-foreground")}>
      <TableCell className="min-w-36"><span className="font-medium">{parameter.name}</span>{!parameter.enabled && <span className="ml-1 text-muted-foreground">（已停用）</span>}<p className="mt-1 text-[11px] text-muted-foreground">{parameter.target}</p></TableCell>
      <TableCell><Choice label={`${parameter.name}参数类型`} value={parameter.kind} options={PARAMETER_KINDS} onChange={(kind) => changeKind(parameter.id, kind as ParameterKind)} disabled={disabled || !parameter.enabled} className="min-w-28" /></TableCell>
      <TableCell><Input aria-label={`${parameter.name}单位`} className="h-8 w-16 text-xs" value={parameter.unit} onChange={(event) => onChange(parameter.id, { unit: event.target.value })} disabled={disabled || !parameter.enabled} /></TableCell>
      <TableCell><ParameterRequirement parameter={parameter} onChange={(change) => onChange(parameter.id, change)} disabled={disabled || !parameter.enabled} /></TableCell>
      <TableCell><Choice label={`${parameter.name}记录方式`} value={parameter.recording} options={RECORDING_OPTIONS} onChange={(recording) => onChange(parameter.id, { recording })} disabled={disabled || !parameter.enabled} className="min-w-36" /></TableCell>
      <TableCell><Checkbox aria-label={`${parameter.name}必填`} checked={parameter.required} onCheckedChange={(value) => onChange(parameter.id, { required: value === true })} disabled={disabled || !parameter.enabled} /><span className="ml-1.5 whitespace-nowrap">{parameter.recording === "异常事件触发" ? "异常时必填" : "必填"}</span></TableCell>
      <TableCell><div className="flex items-center"><Button variant="link" size="sm" className="h-7 px-1" disabled={disabled || !parameter.enabled} onClick={() => onEdit(parameter)}>编辑</Button><span className="text-muted-foreground">·</span><Button variant="link" size="sm" className="h-7 px-1" disabled={disabled} onClick={() => onChange(parameter.id, { enabled: !parameter.enabled })}>{parameter.enabled ? "停用" : "启用"}</Button></div></TableCell>
    </TableRow>)}{!parameters.length && <TableRow><TableCell colSpan={7} className="h-20 text-center text-muted-foreground">尚未配置参数，添加参数后设置本操作的规程要求。</TableCell></TableRow>}</TableBody>
  </Table>;
}

export const RULE_COLUMNS: Record<RuleSection, string[]> = {
  events: ["事件名称", "触发条件", "要求记录的内容", "关联操作", "记录要求"],
  checks: ["检查项目", "规定动作 / 要求", "检查方式", "触发时机 / 关联项"],
  contingencies: ["触发条件", "处置预案", "负责角色", "解除条件 / 后续要求"],
};

export function RulesTable({ section, operation, disabled, onEdit, onRequired }: {
  section: RuleSection; operation: Operation; disabled: boolean; onEdit: (row: RuleRow) => void; onRequired: (id: string, required: boolean) => void;
}) {
  return <Table className="min-w-[820px] text-xs">
    <TableHeader className="bg-muted/70"><TableRow>{RULE_COLUMNS[section].map((title) => <TableHead key={title} className="h-9 text-xs">{title}</TableHead>)}{section === "checks" && <TableHead className="text-xs">必须通过</TableHead>}<TableHead className="text-xs">管理</TableHead></TableRow></TableHeader>
    <TableBody>{operation[section].map((row) => <TableRow key={row.id}>
      <TableCell className="min-w-32 font-medium">{row.name}</TableCell>{ruleDetails(row, operation).map((value, index) => <TableCell key={index} className={cn(index === 0 && section === "checks" && "min-w-56")}><span className={cn(section === "checks" && index < 2 && "block rounded-md border bg-background px-2 py-1.5")}>{value}</span></TableCell>)}
      {section === "checks" && <TableCell><Checkbox checked={row.required} aria-label={`${row.name}必须通过`} onCheckedChange={(value) => onRequired(row.id, value === true)} disabled={disabled} /><span className="ml-1.5">必须</span></TableCell>}
      <TableCell><Button variant="link" size="sm" className="h-7 px-1" onClick={() => onEdit(row)} disabled={disabled}>编辑</Button></TableCell>
    </TableRow>)}{!operation[section].length && <TableRow><TableCell colSpan={RULE_COLUMNS[section].length + (section === "checks" ? 2 : 1)} className="h-20 text-center text-muted-foreground">尚未配置，点击上方添加。</TableCell></TableRow>}</TableBody>
  </Table>;
}
