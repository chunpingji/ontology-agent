"use client";

import type { ReactNode } from "react";
import { FlaskConical, Plus, Trash2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Check, Choice, Field } from "./controls";
import { SAMPLING_PURPOSES, SAMPLING_TRIGGERS, emptySamplingPoint, emptySamplingTest, hasSampleTreatment, hasSamplingEvent, hasSamplingInterval, samplingTestRequirement, validateSamplingPlan, type SamplingPlan, type SamplingPoint, type SamplingTest } from "./sampling";

const SAMPLING_TABS = [
  ["01", "取样方案与触发规则"],
  ["02", "取样点与操作方法"],
  ["03", "样品处理与流转"],
  ["04", "检验项目与判定规则"],
  ["05", "时间与事件配置"],
  ["06", "操作检查项目"],
  ["07", "审核与处置预案"],
] as const;

function SamplingSection({ number, title, description, children, action }: {
  number: string; title: string; description: string; children: ReactNode; action?: ReactNode;
}) {
  return <TabsContent value={number} className="mt-4 min-w-0"><section aria-label={title} className="min-w-0 space-y-4 rounded-lg border bg-background p-4">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <div><div className="flex items-center gap-2"><span className="text-xs font-semibold text-primary">{number}</span><h2 className="text-sm font-semibold">{title}</h2></div><p className="mt-1 text-xs leading-relaxed text-muted-foreground">{description}</p></div>
      {action}
    </div>
    {children}
  </section></TabsContent>;
}

function SamplingInput({ label, value, onChange, placeholder, multiline = false, numeric = false }: {
  label: string; value: string; onChange: (value: string) => void; placeholder?: string; multiline?: boolean; numeric?: boolean;
}) {
  return <Field label={label}>{multiline
    ? <Textarea aria-label={label} value={value} onChange={(event) => onChange(event.target.value)} placeholder={placeholder} className="min-h-16 bg-background text-xs" />
    : <Input aria-label={label} value={value} onChange={(event) => onChange(event.target.value)} placeholder={placeholder} inputMode={numeric ? "decimal" : undefined} className="h-8 min-w-0 bg-background text-xs" />}</Field>;
}

export function SamplingConfiguration({ plan, disabled, onChange, sections }: {
  plan: SamplingPlan; disabled: boolean; onChange: (plan: SamplingPlan) => void;
  sections: Record<"05" | "06" | "07", ReactNode>;
}) {
  const patch = (change: Partial<SamplingPlan>) => { if (!disabled) onChange({ ...plan, ...change }); };
  const field = (key: Exclude<keyof SamplingPlan, "points" | "tests">, label: string, placeholder: string, multiline = false, numeric = false) =>
    <SamplingInput label={label} value={plan[key]} onChange={(value) => patch({ [key]: value })} placeholder={placeholder} multiline={multiline} numeric={numeric} />;
  const choice = (key: Exclude<keyof SamplingPlan, "points" | "tests">, label: string, options: readonly string[]) =>
    <Field label={label}><Choice label={label} value={plan[key]} options={options} onChange={(value) => patch({ [key]: value })} disabled={disabled} /></Field>;
  const updatePoint = (id: string, change: Partial<SamplingPoint>) => patch({ points: plan.points.map((point) => point.id === id ? { ...point, ...change } : point) });
  const updateTest = (id: string, change: Partial<SamplingTest>) => patch({ tests: plan.tests.map((test) => test.id === id ? { ...test, ...change } : test) });
  const issues = validateSamplingPlan(plan);

  return <div className="min-w-0 space-y-4">
    <div className="space-y-3 rounded-lg border border-primary/20 bg-primary/5 p-4">
      <div className="flex flex-wrap items-center justify-between gap-3"><div className="flex items-center gap-2"><FlaskConical className="size-5 text-primary" /><span className="text-sm font-semibold">过程取样控制方案</span><Badge variant="outline">ICH Q7 · Q9(R1)</Badge></div><Badge variant="secondary">{issues.length ? `${issues.length} 项待完善` : "字段已完整 · 待审核"}</Badge></div>
      <p className="text-xs leading-relaxed text-muted-foreground">从取样时点到结果使用，分别定义操作要求和证据。取样频率、数量与检验限度由适用的批准规程确定；空白项可先保存草稿。</p>
    </div>

    <Tabs defaultValue="01" className="min-w-0">
      <div className="min-w-0 overflow-x-auto rounded-lg border bg-background p-1">
        <TabsList aria-label="过程取样配置分区" className="h-auto w-max min-w-full justify-start gap-1 bg-transparent p-0">
          {SAMPLING_TABS.map(([number, title]) => <TabsTrigger key={number} value={number} className="gap-2 px-3 py-2.5 text-xs data-[state=active]:bg-primary/10 data-[state=active]:text-primary data-[state=active]:shadow-none"><span className="font-mono">{number}</span>{title}</TabsTrigger>)}
        </TabsList>
      </div>
      <fieldset disabled={disabled} aria-label="过程取样专用配置" className="min-w-0">
    <SamplingSection number="01" title="取样方案与触发规则" description="明确适用范围、科学依据及质量批准引用 · ICH Q7 §8.30–8.34">
      <div className="grid gap-3 sm:grid-cols-2 2xl:grid-cols-3">
        {field("title", "方案名称", "填写取样方案名称")}
        {field("version", "方案版本", "填写受控版本")}
        {field("procedure", "取样 SOP / 版本", "文件编号、版本及条款")}
        {field("applicability", "适用品种 / 工艺范围", "产品、路线、反应阶段及批量范围")}
        {field("approvalReference", "质量批准依据", "方案批准记录 / 变更控制编号")}
        {choice("purpose", "取样用途", SAMPLING_PURPOSES)}
      </div>
      {field("rationale", "代表性与风险依据", "开发 / 历史数据、均一性、样品稳定性及风险评估引用；说明时点、位置与样本量的依据", true)}
      <div className="grid gap-3 rounded-md bg-muted/40 p-3 sm:grid-cols-2 2xl:grid-cols-3">
        {choice("trigger", "取样触发方式", SAMPLING_TRIGGERS)}
        {field("firstSample", "首次取样时点", "相对哪个事件、何时执行首次取样")}
        {hasSamplingEvent(plan) && field("triggerEvent", "触发事件 / 条件", "填写应达到的反应阶段或终点候选条件")}
        {hasSamplingInterval(plan) && field("intervalHours", "取样间隔（小时）", "按规程填写", false, true)}
        {field("toleranceMinutes", "允许时间窗（±分钟）", "填写允许偏移；不允许偏移填 0", false, true)}
      </div>
      <p className="text-[11px] leading-relaxed text-muted-foreground">取样间隔独立于物料温度的记录频率。每次记录计划与实际时点，漏取、超时或临时加取须记录原因和处置依据。</p>
    </SamplingSection>

    <SamplingSection number="02" title="取样点与操作方法" description="区分物料对象、相别、点位及样品份数 · ICH Q7 §8.34–8.35" action={<Button variant="ghost" size="sm" disabled={disabled} onClick={() => patch({ points: [...plan.points, emptySamplingPoint(crypto.randomUUID())] })}><Plus />添加取样点</Button>}>
      <Table className="min-w-[860px] text-xs">
        <TableHeader className="bg-muted/70"><TableRow>{["取样点名称", "相别 / 物料对象", "位置 / 取样口", "单份取样量", "单位", "份数", "管理"].map((label) => <TableHead key={label} className="whitespace-nowrap text-xs">{label}</TableHead>)}</TableRow></TableHeader>
        <TableBody>{plan.points.map((point, index) => <TableRow key={point.id}>
          {(["name", "phase", "location", "amount", "unit", "count"] as const).map((key, fieldIndex) => <TableCell key={key}><Input aria-label={`取样点 ${index + 1}${["名称", "相别 / 物料对象", "位置 / 取样口", "单份取样量", "单位", "份数"][fieldIndex]}`} value={point[key]} placeholder={key === "phase" ? "如：反应液 / 有机相" : key === "location" ? "设备编号 / 取样口" : "待配置"} onChange={(event) => updatePoint(point.id, { [key]: event.target.value })} inputMode={["amount", "count"].includes(key) ? "decimal" : undefined} className={`h-8 text-xs ${fieldIndex < 3 ? "min-w-36" : "w-20"}`} /></TableCell>)}
          <TableCell><Button variant="ghost" size="icon" aria-label={`删除取样点 ${index + 1}`} disabled={disabled} onClick={() => patch({ points: plan.points.filter((item) => item.id !== point.id) })}><Trash2 className="size-3.5" /></Button></TableCell>
        </TableRow>)}{!plan.points.length && <TableRow><TableCell colSpan={7} className="py-6 text-center text-muted-foreground">尚未配置取样点，请添加后填写物料对象与位置。</TableCell></TableRow>}</TableBody>
      </Table>
      <div className="grid gap-3 sm:grid-cols-2">
        {choice("pooling", "样品组合方式", ["分别检验", "按方案混合"])}
        {choice("method", "取样方式", ["密闭取样", "开口取样", "在线取样"])}
      </div>
      {plan.pooling === "按方案混合" && field("poolingBasis", "混合规则与论证", "混合点位、比例、操作及代表性依据；保留各子样身份", true)}
      <div className="grid gap-3 2xl:grid-cols-3">
        {field("preparation", "取样前准备", "混匀条件、工具 / 容器清洁与相容性、取样口预处理；弃初样仅在规程规定时执行", true)}
        {field("contaminationControl", "防污染与保护措施", "密闭 / 暴露控制、所需气氛、交叉污染防护；按工艺评估", true)}
        {field("restoration", "取样后设备恢复", "关闭取样口、恢复密封 / 保护并确认设备状态", true)}
      </div>
    </SamplingSection>

    <SamplingSection number="03" title="样品处理与流转" description="保持样品收集后的完整性，并追溯至原批次与时点 · ICH Q7 §8.35、§6.60">
      <div className="grid gap-3 sm:grid-cols-2 2xl:grid-cols-3">
        {choice("treatment", "即时处理方式", ["立即淬灭 / 固定", "稀释后保存", "无需即时处理"])}
        {hasSampleTreatment(plan) && field("treatmentProcedure", "即时处理方法 / 版本", "淬灭 / 稀释 / 固定方法与适用试剂")}
        {hasSampleTreatment(plan) && field("treatmentWithinMinutes", "取样至处理上限（分钟）", "按样品稳定性依据填写", false, true)}
        {field("container", "样品容器与密封", "材质、容量、密封及避光等要求")}
        {field("storage", "保存条件", "温度、避光、气氛等经论证的条件")}
        {field("maxHoldHours", "取样至检验上限（小时）", "从取样完成至开始检验的最长时间", false, true)}
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        {field("transport", "运输与交接要求", "运输条件、交接双方、时间、接收检查及异常反馈", true)}
        {field("stabilityBasis", "样品稳定性依据", "引用保存 / 运输 / 时限研究；无需即时处理时说明理由", true)}
      </div>
      <div className="rounded-md bg-muted/40 p-3 text-xs"><p className="font-medium">每份样品的追溯记录</p><p className="mt-2 leading-relaxed text-muted-foreground">唯一样品号 · 产品 / 物料与批号 · 工序与设备 · 点位 / 相别 · 计划及实际取样时间 · 实际量 · 取样人 · 处理时间 · 交接与接收人 / 时间 / 状态 · 原始检验数据</p></div>
    </SamplingSection>

    <SamplingSection number="04" title="检验项目与判定规则" description="按项目定义方法、接受准则及结果用途 · ICH Q7 §8.30–8.36、§11.15" action={<Button variant="ghost" size="sm" disabled={disabled} onClick={() => patch({ tests: [...plan.tests, emptySamplingTest(crypto.randomUUID())] })}><Plus />添加检验项目</Button>}>
      {plan.tests.map((test, index) => {
        const prefix = `检验项目 ${index + 1}`;
        return <div key={test.id} role="group" aria-label={prefix} className="space-y-3 rounded-md border p-3">
          <div className="flex flex-wrap items-center justify-between gap-2"><span className="text-xs font-medium">{String(index + 1).padStart(2, "0")} · {test.name || "新检验项目"}</span><div className="flex items-center gap-3"><Check label={`${prefix}必需结果`} checked={test.required} onChange={(required) => updateTest(test.id, { required })} disabled={disabled} /><Button variant="ghost" size="icon" aria-label={`删除${prefix}`} disabled={disabled} onClick={() => patch({ tests: plan.tests.filter((item) => item.id !== test.id) })}><Trash2 className="size-3.5" /></Button></div></div>
          <div className="grid gap-3 sm:grid-cols-2 2xl:grid-cols-4">
            <SamplingInput label={`${prefix}名称`} value={test.name} onChange={(name) => updateTest(test.id, { name })} placeholder="按品种选择项目" />
            <SamplingInput label={`${prefix}方法编号`} value={test.method} onChange={(method) => updateTest(test.id, { method })} placeholder="受控检验方法编号" />
            <SamplingInput label={`${prefix}方法版本`} value={test.version} onChange={(version) => updateTest(test.id, { version })} placeholder="适用版本" />
            <Field label={`${prefix}用途`}><Choice label={`${prefix}用途`} value={test.purpose} options={SAMPLING_PURPOSES} onChange={(purpose) => updateTest(test.id, { purpose: purpose as SamplingTest["purpose"] })} disabled={disabled} /></Field>
            <Field label={`${prefix}结果类型`}><Choice label={`${prefix}结果类型`} value={test.resultType} options={["数值", "定性"]} onChange={(resultType) => updateTest(test.id, { resultType: resultType as SamplingTest["resultType"], value: "", upper: "", unit: "", comparison: "不超过" })} disabled={disabled} /></Field>
            {test.resultType === "数值" && <><SamplingInput label={`${prefix}单位 / 结果基准`} value={test.unit} onChange={(unit) => updateTest(test.id, { unit })} placeholder="如：面积 %、mg/g；按方法定义" /><Field label={`${prefix}比较方式`}><Choice label={`${prefix}比较方式`} value={test.comparison} options={["不超过", "不少于", "区间", "等于"]} onChange={(comparison) => updateTest(test.id, { comparison })} disabled={disabled} /></Field></>}
            <SamplingInput label={`${prefix}${test.resultType === "定性" ? "接受描述" : test.comparison === "区间" ? "下限" : "要求值"}`} value={test.value} onChange={(value) => updateTest(test.id, { value })} placeholder="按批准准则填写" numeric={test.resultType === "数值"} />
            {test.resultType === "数值" && test.comparison === "区间" && <SamplingInput label={`${prefix}上限`} value={test.upper} onChange={(upper) => updateTest(test.id, { upper })} placeholder="按批准准则填写" numeric />}
          </div>
        </div>;
      })}
      {!plan.tests.length && <p className="py-4 text-center text-xs text-muted-foreground">尚未配置检验项目，请添加所需检验及接受准则。</p>}
      <p className="text-[11px] leading-relaxed text-muted-foreground">项目为可编辑示意，需确认方法适用性。面积百分比不自动等于质量百分含量或转化率；结果待完成或分析无效时保持未决。</p>
      <div className="space-y-3 border-t pt-3">
        {(plan.purpose === "过程监测 / 调整" || plan.tests.some((test) => test.purpose === "过程监测 / 调整")) && field("adjustmentProcedure", "预批准调整范围与程序", "填写质量部门预批准的调整范围、授权角色及记录要求", true)}
        <div className="grid gap-3 sm:grid-cols-2">{field("deviationProcedure", "偏差 / OOS 适用程序", "区分监测调整、终点 / 规格检验及分析无效；引用适用程序与版本", true)}{field("retestProcedure", "重取样 / 复测程序", "批准条件、责任人、调查依据及原样品 / 原结果关联规则", true)}</div>
        <div className="rounded-md bg-primary/5 p-3 text-xs leading-relaxed"><p className="font-medium text-primary">结果用途决定后续处置</p><p className="mt-1 text-muted-foreground">过程监测 / 调整：仅在质量部门预批准范围内调整并完整记录，Q7 §8.36 通常不要求此类检验启动 OOS 调查。终点 / 规格用途：按适用程序调查超标，所需有效结果和复核未完成时不形成允许继续的结论。</p></div>
      </div>
      <details className="text-xs text-muted-foreground"><summary className="cursor-pointer py-1 font-medium">查看 ICH 依据与适用范围</summary><div className="mt-2 space-y-2 leading-relaxed"><p>上述字段用于落实原则，ICH 未规定通用取样数量、间隔或产品限度。方法、时限与控制程度应有科学依据，并与品种及开发阶段相适应。</p><p><a className="text-primary underline underline-offset-2" href="https://database.ich.org/sites/default/files/Q7%20Guideline.pdf" target="_blank" rel="noreferrer">ICH Q7</a>：§6.52、§6.60（记录）；§8.30–8.36（过程控制与取样）；§11.11–11.15（实验室程序）；§19（临床用原料药）。</p><p><a className="text-primary underline underline-offset-2" href="https://database.ich.org/sites/default/files/ICH_Q9%28R1%29_Guideline_Step4_2023_0126_0.pdf" target="_blank" rel="noreferrer">ICH Q9(R1)</a>：§3–4，基于科学知识与风险确定控制措施。</p></div></details>
    </SamplingSection>
        {Object.entries(sections).map(([number, content]) => <TabsContent key={number} value={number} className="mt-4 min-w-0">{content}</TabsContent>)}
      </fieldset>
    </Tabs>
  </div>;
}

export function SamplingPreview({ plan }: { plan: SamplingPlan }) {
  const value = (text: string) => text.trim() || "待配置";
  return <div className="space-y-4 text-xs">
    <section className="space-y-2"><h3 className="text-sm font-semibold">取样方案</h3><p>{plan.title} · {value(plan.version)} · {plan.purpose}</p><p>适用范围：{value(plan.applicability)} · SOP：{value(plan.procedure)}</p><p>批准依据：{value(plan.approvalReference)}</p><p>{plan.trigger} · 首次取样：{value(plan.firstSample)}{hasSamplingEvent(plan) && ` · 条件：${value(plan.triggerEvent)}`}{hasSamplingInterval(plan) && ` · 每 ${value(plan.intervalHours)} 小时`} · 时间窗 ±{value(plan.toleranceMinutes)} 分钟</p><p>代表性与风险依据：{value(plan.rationale)}</p></section>
    <section className="space-y-2"><h3 className="text-sm font-semibold">取样点与方法</h3>{plan.points.map((point) => <p key={point.id} className="border-b py-2">{value(point.name)} · {value(point.phase)} · {value(point.location)} · {value(point.amount)} {value(point.unit)} / 份 × {value(point.count)} 份</p>)}<p>{plan.method} · {plan.pooling}{plan.pooling === "按方案混合" && `：${value(plan.poolingBasis)}`}</p><p>取样前准备：{value(plan.preparation)}</p><p>防污染与保护：{value(plan.contaminationControl)}</p><p>设备恢复：{value(plan.restoration)}</p></section>
    <section className="space-y-2"><h3 className="text-sm font-semibold">样品处理与交接</h3><p>{plan.treatment}{hasSampleTreatment(plan) && ` · ${value(plan.treatmentProcedure)} · 取样后 ${value(plan.treatmentWithinMinutes)} 分钟内处理`}</p><p>容器：{value(plan.container)} · 保存：{value(plan.storage)}</p><p>运输与交接：{value(plan.transport)} · 取样后 {value(plan.maxHoldHours)} 小时内开始检验</p><p>稳定性依据：{value(plan.stabilityBasis)}</p><div className="grid gap-2 rounded-md bg-muted/40 p-3 sm:grid-cols-2">{["唯一样品号 / 原样品关联", "产品 / 物料与批号", "工序 / 设备 / 点位 / 相别", "计划 / 实际取样时间", "实际取样量 / 取样人", "处理时间 / 试剂批号", "交接 / 接收人及时间 / 状态", "检验时间 / 原始数据 / 复核人"].map((label) => <p key={label}>{label}：待填写</p>)}</div></section>
    <section className="space-y-2"><h3 className="text-sm font-semibold">检验要求</h3>{plan.tests.map((test) => <div key={test.id} className="space-y-1 border-b py-2"><p>{value(test.name)} · {test.required ? "必需结果" : "可选结果"} · {test.purpose}</p><p>{value(test.method)} / {value(test.version)} · {samplingTestRequirement(test)}</p><p className="text-muted-foreground">实际结果：待填写 · 判定：未决</p></div>)}<p>预批准调整：{value(plan.adjustmentProcedure)}</p><p>偏差 / OOS 程序：{value(plan.deviationProcedure)}</p><p>重取样 / 复测：{value(plan.retestProcedure)}</p></section>
    <section className="space-y-2"><h3 className="text-sm font-semibold">独立状态记录</h3><div className="grid gap-2 sm:grid-cols-2">{[["取样执行", "未开始"], ["取样程序符合性", "待确认"], ["样品适用性", "待确认"], ["分析运行", "未检验"], ["项目结果", "未决"], ["继续加工决定", "待审核"]].map(([label, status]) => <div key={label} className="flex justify-between rounded-md border p-2"><span>{label}</span><Badge variant="outline">{status}</Badge></div>)}</div><p className="text-muted-foreground">取样结果用于适用步骤的控制，不代表批次放行；重取样与复测保留原始结果和批准依据。</p></section>
  </div>;
}
