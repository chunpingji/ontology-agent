"use client";

import { useState } from "react";
import { ArrowRight, CheckCircle2, ChevronRight, ClipboardCheck, Copy, Eye, FileClock, FileText, Info, LockKeyhole, Save, Send, Settings2, Workflow } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Check, Choice, Field, ParameterTable, RULE_COLUMNS, RulesTable, Section } from "./controls";
import { INITIAL_STEPS, createInitialOperations, emptyOperation, emptyParameter, parameterRequirement, ruleDetails, validateOperation, type Operation, type Parameter, type ProcessStep, type RuleRow, type RuleSection } from "./data";
import { NameEditor, ParameterEditor, RuleEditor } from "./editors";
import { ProcessTree } from "./tree";

type Modal = { kind: "permissions" | "history" | "preview" | "validation" | "submit" | "step" | "operation" | "continuation" }
  | { kind: "parameter"; parameter: Parameter }
  | { kind: "rule"; section: RuleSection; row: RuleRow };
interface Snapshot { id: string; operation: Operation; version: string; time: string; submitted: boolean }

const MODAL_TITLES = {
  permissions: "权限设置 · Mock 演示", history: "版本历史 · 本次会话", preview: "预览记录结构",
  validation: "配置校验", submit: "提交发布审核 · Mock 演示", step: "新增工艺步骤",
  operation: "新增操作", continuation: "继续加工条件", parameter: "配置参数 / 测量对象", rule: "配置规则",
};

export function GxpProcessMock() {
  const [steps, setSteps] = useState<ProcessStep[]>(INITIAL_STEPS);
  const [operations, setOperations] = useState(createInitialOperations);
  const [selectedId, setSelectedId] = useState("OP11.02");
  const [expanded, setExpanded] = useState(["OP05", "OP11"]);
  const [search, setSearch] = useState("");
  const [canEdit, setCanEdit] = useState(true);
  const [modal, setModal] = useState<Modal | null>(null);
  const [notice, setNotice] = useState("");
  const [snapshots, setSnapshots] = useState<Snapshot[]>([]);
  const [dirty, setDirty] = useState<string[]>([]);
  const current = operations.find((operation) => operation.id === selectedId)!;
  const step = steps.find((item) => item.id === current.stepId)!;
  const disabled = !canEdit || !current.enabled;
  const history = snapshots.filter((snapshot) => snapshot.operation.id === current.id);
  const lastSaved = history.at(-1);
  const issues = validateOperation(current);

  function update(change: Partial<Operation>) {
    if (!canEdit) return;
    setOperations((items) => items.map((item) => item.id === current.id ? { ...item, ...change } : item));
    setDirty((ids) => ids.includes(current.id) ? ids : [...ids, current.id]);
    setNotice("");
  }
  const updateParameter = (id: string, change: Partial<Parameter>) => update({ parameters: current.parameters.map((parameter) => parameter.id === id ? { ...parameter, ...change } : parameter) });
  const newRule = (section: RuleSection) => setModal({ kind: "rule", section, row: { id: crypto.randomUUID(), name: "", details: RULE_COLUMNS[section].slice(1).map(() => ""), required: true, enabled: true } });
  function saveRule(section: RuleSection, row: RuleRow) {
    update({ [section]: current[section].some((item) => item.id === row.id) ? current[section].map((item) => item.id === row.id ? row : item) : [...current[section], row] });
    setModal(null);
  }
  function addOperation(name: string, copy = false) {
    if (!canEdit) return;
    const nextIndex = Math.max(0, ...operations.filter((item) => item.stepId === current.stepId).map((item) => Number(item.id.split(".")[1]))) + 1;
    const id = `${current.stepId}.${String(nextIndex).padStart(2, "0")}`;
    const operation = copy ? { ...structuredClone(current), id, name, custom: true, changeNote: "" } : emptyOperation(id, current.stepId, name);
    setOperations((items) => [...items, operation]);
    setSelectedId(id);
    setExpanded((ids) => [...new Set([...ids, current.stepId])]);
    setSearch("");
    setDirty((ids) => [...ids, id]);
    setModal(null);
    setNotice(`${copy ? "已复制" : "已新增"}操作，仅保留在本次 Mock 会话中。`);
  }
  function addStep(name: string) {
    if (!canEdit) return;
    const id = `OP${String(steps.length + 1).padStart(2, "0")}`;
    const operation = emptyOperation(`${id}.01`, id, "自定义操作");
    setSteps((items) => [...items, { id, name, custom: true }]);
    setOperations((items) => [...items, operation]);
    setSelectedId(operation.id);
    setExpanded((ids) => [...ids, id]);
    setDirty((ids) => [...ids, operation.id]);
    setSearch("");
    setModal(null);
    setNotice("已新增自定义步骤，可在右侧配置首个操作。");
  }
  function save(submitted = false) {
    if (disabled) return;
    if (issues.length) { setModal({ kind: "validation" }); return; }
    if (!current.changeNote.trim()) { setNotice("请先填写变更说明，再保存或模拟提交。"); return; }
    if (submitted && !current.source.trim()) { setNotice("请先填写规程来源，再模拟提交发布审核。"); return; }
    const version = `v1.${6 + history.length}`;
    setSnapshots((items) => [...items, { id: crypto.randomUUID(), operation: structuredClone(current), version, time: new Date().toLocaleTimeString("zh-CN", { hour12: false }), submitted }]);
    setDirty((ids) => ids.filter((id) => id !== current.id));
    setModal(null);
    setNotice(submitted ? `已模拟提交 ${version}，未发起真实发布审核。刷新页面将恢复示例。` : `Mock 草稿 ${version} 已保存至本次会话，刷新页面将恢复示例。`);
  }

  return <div className="-m-6 min-w-0 bg-accent/30">
    <div className="flex flex-wrap items-center justify-between gap-3 border-b bg-background px-6 py-3">
      <div className="flex items-center gap-2 text-xs text-muted-foreground"><Workflow className="size-4 text-primary" /><span>原料药工艺</span><ChevronRight className="size-3" /><span className="text-foreground">通用操作设置</span><Badge variant="secondary" className="ml-1">静态 Mock</Badge></div>
      <p className="text-xs text-muted-foreground">仅供界面演示 · 刷新恢复示例</p>
    </div>
    <header className="flex flex-wrap items-center justify-between gap-4 px-6 py-5">
      <div><div className="flex flex-wrap items-center gap-3"><h1 className="text-xl font-bold tracking-tight">通用工艺操作设置</h1><Badge variant="outline" className="border-primary/20 bg-primary/5 text-primary">{lastSaved?.version ?? "v1.5"} {dirty.includes(current.id) ? "草稿 · 未保存" : lastSaved?.submitted ? "待审核（演示）" : "草稿"}</Badge></div><p className="mt-1.5 text-xs text-muted-foreground">通用模板 / 化学合成原料药 / 定义操作、记录要求与审核处置规则</p></div>
      <div className="flex flex-wrap items-center gap-2"><span className="mr-1 text-xs text-muted-foreground">演示权限：{canEdit ? "工艺管理员 · 可编辑" : "已授权成员 · 只读"}</span><Button variant="outline" size="sm" onClick={() => setModal({ kind: "permissions" })}><Settings2 />权限设置</Button><Button variant="outline" size="sm" onClick={() => setModal({ kind: "history" })}><FileClock />版本历史</Button></div>
    </header>
    <div className="grid min-w-0 items-start border-t lg:grid-cols-[280px_minmax(0,1fr)] 2xl:grid-cols-[312px_minmax(0,1fr)]">
      <ProcessTree steps={steps} operations={operations} selectedId={selectedId} search={search} expanded={expanded} disabled={!canEdit} onSearch={setSearch} onExpand={(id) => setExpanded((ids) => ids.includes(id) ? ids.filter((value) => value !== id) : [...ids, id])} onSelect={(id) => { setSelectedId(id); setNotice(""); }} onAddStep={() => setModal({ kind: "step" })} onAddOperation={() => setModal({ kind: "operation" })} onCollapse={() => setExpanded(expanded.length ? [] : steps.map((item) => item.id))} />
      <div className="min-w-0 space-y-4 p-4 xl:p-6">
        <section aria-label="操作基础属性" className="space-y-3">
          <div className="flex flex-wrap items-start justify-between gap-2"><div><div className="flex flex-wrap items-center gap-2"><span className="font-mono text-xs text-muted-foreground">{current.id}</span><h2 className="text-lg font-semibold">{current.name || "未命名操作"}</h2><Badge variant="secondary" className={current.enabled ? "bg-primary/10 text-primary" : ""}>操作模板 · {current.enabled ? "已启用" : "已停用"}</Badge>{current.custom && <Badge variant="outline">自定义</Badge>}</div><p className="mt-2 text-xs leading-relaxed text-muted-foreground">定义{current.name}的操作要求与记录结构，可复用于适用工艺。预设值可由授权用户按规程调整。</p></div><div className="flex gap-2"><Button variant="outline" size="sm" disabled={!canEdit} onClick={() => addOperation(`${current.name}（副本）`, true)}><Copy />复制操作</Button><Button variant="outline" size="sm" disabled={!canEdit} onClick={() => update({ enabled: !current.enabled })}>{current.enabled ? "停用操作" : "启用操作"}</Button></div></div>
          {!canEdit && <p className="flex items-center gap-2 rounded-md border bg-background p-3 text-xs text-muted-foreground"><LockKeyhole className="size-4" />当前为只读演示，可查看配置和预览记录结构。</p>}
          <div className="grid gap-3 sm:grid-cols-2 2xl:grid-cols-4">
            <Field label="操作名称"><Input className="h-8 bg-background text-xs" value={current.name} onChange={(event) => update({ name: event.target.value })} disabled={disabled} /></Field>
            <Field label="所属工艺步骤"><Input className="h-8 bg-background text-xs" value={`${step.id} · ${step.name}`} readOnly /></Field>
            <Field label="适用阶段"><Choice label="适用阶段" value={current.stage} options={["临床用 / 商业化", "临床用", "商业化"]} onChange={(stage) => update({ stage })} disabled={disabled} /></Field>
            <Field label="规程来源"><Input className="h-8 bg-background text-xs" placeholder="选择规程文件 / 条款引用…" value={current.source} onChange={(event) => update({ source: event.target.value })} disabled={disabled} /></Field>
          </div>
        </section>

        <Section number="01" title="过程参数与记录配置" description="按参数类型设置规程要求" action="添加参数 / 测量对象" disabled={disabled} onAdd={() => setModal({ kind: "parameter", parameter: emptyParameter(crypto.randomUUID()) })}>
          <ParameterTable parameters={current.parameters} disabled={disabled} onChange={updateParameter} onEdit={(parameter) => setModal({ kind: "parameter", parameter })} />
          <p className="flex items-start gap-2 text-[11px] leading-relaxed text-muted-foreground"><Info className="mt-0.5 size-3.5 shrink-0" />支持数值、枚举、时长、布尔和文本等类型；表中为模板预设示例，发布前需确认适用工艺及规程依据。</p>
        </Section>

        <Section number="02" title="时间与事件配置" description="定义必须记录的事件" action="添加事件" disabled={disabled} onAdd={() => newRule("events")}>
          <RulesTable section="events" operation={current} disabled={disabled} onEdit={(row) => setModal({ kind: "rule", section: "events", row })} onRequired={() => {}} />
          <div className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-md bg-muted/40 p-3 text-xs"><span className="font-medium">时长计算</span>
            <Choice label="计算开始事件" value={current.events.find((event) => event.id === current.startEvent)?.name ?? ""} options={current.events.map((event) => event.name)} onChange={(name) => update({ startEvent: current.events.find((event) => event.name === name)?.id ?? "" })} disabled={disabled || !current.events.length} className="w-32" /><ArrowRight className="size-3 text-muted-foreground" />
            <Choice label="计算结束事件" value={current.events.find((event) => event.id === current.endEvent)?.name ?? ""} options={current.events.map((event) => event.name)} onChange={(name) => update({ endEvent: current.events.find((event) => event.name === name)?.id ?? "" })} disabled={disabled || !current.events.length} className="w-32" />
            <span className="text-muted-foreground">中断处理</span><Choice label="中断计时策略" value={current.interruption} options={["默认不扣除", "扣除暂停时长", "重新计时"]} onChange={(interruption) => update({ interruption })} disabled={disabled} className="w-32" /><span className="text-muted-foreground">记录精度</span><Choice label="时间精度" value={current.precision} options={["秒", "分钟"]} onChange={(precision) => update({ precision })} disabled={disabled} className="w-20" />
          </div>
        </Section>

        <Section number="03" title="操作检查项目" description="配置规定动作、检查方式及必过条件" action="添加检查项目" disabled={disabled} onAdd={() => newRule("checks")}>
          <div className="flex flex-wrap justify-between gap-2 rounded-md bg-primary/5 px-3 py-2 text-[11px]"><span className="text-muted-foreground">检查要求来源：当前操作参数 / 关联操作 / 自定义规程</span><span className="text-primary">{disabled ? "配置只读" : "配置草稿可编辑"}</span></div>
          <RulesTable section="checks" operation={current} disabled={disabled} onEdit={(row) => setModal({ kind: "rule", section: "checks", row })} onRequired={(id, required) => update({ checks: current.checks.map((row) => row.id === id ? { ...row, required } : row) })} />
          <div className="flex flex-wrap items-center gap-3 text-xs"><span className="font-medium">结果选项</span>{["符合", "不符合", "未决"].map((value) => <Badge key={value} variant="outline" className="font-normal">{value}</Badge>)}<Check label="允许“不适用”，须填理由" checked={current.allowNotApplicable} onChange={(allowNotApplicable) => update({ allowNotApplicable })} disabled={disabled} /><span className="ml-auto text-muted-foreground">初始状态：未判定</span></div>
          <p className="text-[11px] text-muted-foreground">引用参数的检查与参数规程保持一致；自定义检查可设置动作、次数、时长或所需证据。</p>
        </Section>

        <Section number="04" title="审核与处置预案" description="定义审核角色、异常响应及恢复条件" action="添加预案" disabled={disabled} onAdd={() => newRule("contingencies")}>
          <div className="grid gap-3 sm:grid-cols-2 2xl:grid-cols-4"><Field label="操作复核角色"><Choice label="操作复核角色" value={current.reviewer} options={["工艺负责人", "班组负责人", "授权复核员"]} onChange={(reviewer) => update({ reviewer })} disabled={disabled} /></Field><Field label="质量审核角色"><Choice label="质量审核角色" value={current.qualityReviewer} options={["质量负责人", "QA", "QC 负责人"]} onChange={(qualityReviewer) => update({ qualityReviewer })} disabled={disabled} /></Field><Field label="审核模式"><Choice label="审核模式" value={current.reviewMode} options={["顺序审核", "并行审核"]} onChange={(reviewMode) => update({ reviewMode })} disabled={disabled} /></Field><div className="flex items-end pb-2"><Check label="执行人与复核人分离" checked={current.separateReviewer} onChange={(separateReviewer) => update({ separateReviewer })} disabled={disabled} /></div></div>
          <RulesTable section="contingencies" operation={current} disabled={disabled} onEdit={(row) => setModal({ kind: "rule", section: "contingencies", row })} onRequired={() => {}} />
          <div className="flex flex-wrap items-center gap-3 rounded-md bg-primary/5 px-3 py-2 text-xs"><span className="font-medium text-primary">继续加工条件</span><span className="flex-1">{current.continuation}</span><Button variant="link" size="sm" onClick={() => setModal({ kind: "continuation" })} disabled={disabled}>编辑条件组合</Button></div>
        </Section>

        <div className="space-y-3"><Field label="变更说明"><Textarea value={current.changeNote} onChange={(event) => update({ changeNote: event.target.value })} placeholder="填写本次通用操作设置的变更内容与依据…" className="min-h-16 bg-background text-xs" disabled={disabled} /></Field>
          <div className="flex flex-wrap items-center justify-between gap-3 border-t pt-4"><p className="max-w-sm text-[11px] leading-relaxed text-muted-foreground">保存为模板草稿，发布审核通过后供适用工艺引用。<br />当前为静态 Mock，保存和提交仅在本次会话内演示。</p><div className="flex flex-wrap gap-2"><Button variant="outline" size="sm" onClick={() => setModal({ kind: "validation" })}><ClipboardCheck />校验配置</Button><Button variant="outline" size="sm" onClick={() => setModal({ kind: "preview" })}><Eye />预览记录结构</Button><Button variant="outline" size="sm" disabled={disabled} onClick={() => save()}><Save />保存配置草稿</Button><Button size="sm" disabled={disabled} onClick={() => setModal({ kind: "submit" })}><Send />提交发布审核</Button></div></div>
          <div role="status" aria-live="polite" className="text-xs leading-relaxed text-primary">{notice}</div>
        </div>
      </div>
    </div>

    <Dialog open={modal !== null} onOpenChange={(open) => { if (!open) setModal(null); }}><DialogContent className={modal?.kind === "preview" ? "max-h-[85vh] max-w-3xl overflow-y-auto" : "max-h-[85vh] overflow-y-auto"}>
      <DialogHeader><DialogTitle>{modal ? MODAL_TITLES[modal.kind] : ""}</DialogTitle><DialogDescription>{modal?.kind === "permissions" ? "切换本页面的演示视图，不修改真实账号权限。" : `${current.id} · ${current.name} · 仅作用于静态 Mock`}</DialogDescription></DialogHeader>
      {modal?.kind === "step" && <NameEditor label="步骤" onSave={addStep} />}
      {modal?.kind === "operation" && <NameEditor label="操作" onSave={(name) => addOperation(name)} />}
      {modal?.kind === "parameter" && <ParameterEditor initial={modal.parameter} onSave={(parameter) => { update({ parameters: current.parameters.some((item) => item.id === parameter.id) ? current.parameters.map((item) => item.id === parameter.id ? parameter : item) : [...current.parameters, parameter] }); setModal(null); }} />}
      {modal?.kind === "rule" && <RuleEditor section={modal.section} initial={modal.row} operation={current} onSave={(row) => saveRule(modal.section, row)} />}
      {modal?.kind === "permissions" && <><div className="space-y-3 rounded-md bg-muted/50 p-4 text-sm"><p>查看：已授权成员</p><p>编辑：工艺管理员、授权配置员</p><p>发布审核：质量负责人</p></div><Choice label="演示权限" value={canEdit ? "工艺管理员 · 可编辑" : "已授权成员 · 只读"} options={["工艺管理员 · 可编辑", "已授权成员 · 只读"]} onChange={(value) => setCanEdit(value === "工艺管理员 · 可编辑")} /><DialogFooter><Button onClick={() => setModal(null)}>完成</Button></DialogFooter></>}
      {modal?.kind === "validation" && <><div className="space-y-3 text-sm">{issues.length ? <><p className="font-medium text-destructive">发现 {issues.length} 项待修正配置</p><ul className="list-disc space-y-2 pl-5">{issues.map((issue, index) => <li key={index}>{issue}</li>)}</ul></> : <p className="flex items-center gap-2 text-success"><CheckCircle2 className="size-5" />本地字段校验通过</p>}<p className="text-xs leading-relaxed text-muted-foreground">本次仅检查字段格式、数值区间和事件/参数引用。示例阈值、规程依据与适用性仍需审核，未执行生产合规判定。</p>{!current.parameters.length && <p className="text-xs text-muted-foreground">此操作尚未配置过程参数。</p>}</div><DialogFooter><Button variant="outline" onClick={() => setModal(null)}>返回配置</Button></DialogFooter></>}
      {modal?.kind === "history" && <div className="space-y-3">{history.length ? [...history].reverse().map((snapshot) => <div key={snapshot.id} className="space-y-2 rounded-md border p-3"><div className="flex items-center justify-between text-sm"><span className="font-medium">{snapshot.version} · {snapshot.submitted ? "待审核（演示）" : "草稿"}</span><span className="text-xs text-muted-foreground">{snapshot.time}</span></div><p className="text-xs text-muted-foreground">{snapshot.operation.changeNote}</p></div>) : <p className="py-6 text-center text-sm text-muted-foreground">本次会话暂无保存版本。v1.5 为画板示例草稿。</p>}<p className="text-xs text-muted-foreground">版本仅保留在本次页面会话中，刷新后清除。</p></div>}
      {modal?.kind === "preview" && <RecordPreview operation={current} />}
      {modal?.kind === "continuation" && <><Field label="继续加工条件组合"><Textarea value={current.continuation} onChange={(event) => update({ continuation: event.target.value })} disabled={disabled} /></Field><p className="text-xs text-muted-foreground">此处为条件组合的文字示意，不执行规则计算。</p><DialogFooter><Button onClick={() => setModal(null)}>完成</Button></DialogFooter></>}
      {modal?.kind === "submit" && <><p className="text-sm leading-relaxed">模拟将当前配置保存为新版本草稿，并进入“待审核”状态。此操作不会发布模板或发送真实审核请求。</p><div className="rounded-md bg-muted/50 p-3 text-xs leading-relaxed"><p>操作：{current.name}</p><p>规程来源：{current.source || "尚未填写"}</p><p>变更说明：{current.changeNote || "尚未填写"}</p></div>{notice && <p role="alert" className="text-sm text-destructive">{notice}</p>}<DialogFooter><Button variant="outline" onClick={() => setModal(null)}>取消</Button><Button disabled={disabled} onClick={() => save(true)}>确认模拟提交</Button></DialogFooter></>}
    </DialogContent></Dialog>
  </div>;
}

function RecordPreview({ operation }: { operation: Operation }) {
  return <div className="space-y-5 text-sm"><div className="rounded-md bg-muted/50 p-3"><p className="flex items-center gap-2 font-medium"><FileText className="size-4 text-primary" />{operation.name} · 空白记录结构</p><p className="mt-1 text-xs text-muted-foreground">展示配置对应的记录要求。实际值尚未填写，检查结果均为未判定。</p></div>
    <section className="space-y-2"><h3 className="font-semibold">过程参数</h3>{operation.parameters.filter((parameter) => parameter.enabled).map((parameter) => <div key={parameter.id} className="grid grid-cols-[1fr_2fr] gap-3 border-b py-2 text-xs"><span>{parameter.name}<span className="ml-1 text-muted-foreground">{parameter.required ? "（必填）" : "（可选）"}</span></span><div><p>{parameterRequirement(parameter)}</p><p className="mt-1 text-muted-foreground">{parameter.recording} · 实际值：待填写</p></div></div>)}{!operation.parameters.some((parameter) => parameter.enabled) && <p className="text-xs text-muted-foreground">尚未配置</p>}</section>
    <section className="space-y-2"><h3 className="font-semibold">时间事件</h3>{operation.events.map((event) => <p key={event.id} className="border-b py-2 text-xs">{event.name} · {event.details[1]} · 待记录</p>)}</section>
    <section className="space-y-2"><h3 className="font-semibold">检查项目</h3>{operation.checks.map((check) => <div key={check.id} className="flex items-center justify-between gap-3 border-b py-2 text-xs"><div><p className="font-medium">{check.name}</p><p className="mt-1 text-muted-foreground">{ruleDetails(check, operation)[0]}</p></div><Badge variant="outline" className="shrink-0">未判定</Badge></div>)}</section>
    <section className="space-y-2"><h3 className="font-semibold">审核与处置</h3><p className="text-xs">{operation.reviewer} → {operation.qualityReviewer} · {operation.reviewMode}</p>{operation.contingencies.map((row) => <p key={row.id} className="border-b py-2 text-xs">{row.name} → {row.details[0]}</p>)}<p className="text-xs text-muted-foreground">继续加工条件：{operation.continuation}</p></section>
  </div>;
}
