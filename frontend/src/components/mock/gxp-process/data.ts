export type ParameterKind = "数值 · 小数" | "数值 · 整数" | "枚举" | "时长" | "布尔" | "文本";

export interface Parameter {
  id: string;
  name: string;
  target: string;
  kind: ParameterKind;
  unit: string;
  comparison: string;
  value: string;
  upper: string;
  recording: string;
  required: boolean;
  enabled: boolean;
}

export interface RuleRow {
  id: string;
  name: string;
  details: string[];
  required: boolean;
  enabled: boolean;
  parameterId?: string;
}

export type RuleSection = "events" | "checks" | "contingencies";

export interface Operation {
  id: string;
  stepId: string;
  name: string;
  enabled: boolean;
  custom: boolean;
  stage: string;
  source: string;
  parameters: Parameter[];
  events: RuleRow[];
  checks: RuleRow[];
  contingencies: RuleRow[];
  startEvent: string;
  endEvent: string;
  interruption: string;
  precision: string;
  reviewer: string;
  qualityReviewer: string;
  reviewMode: string;
  separateReviewer: boolean;
  allowNotApplicable: boolean;
  continuation: string;
  changeNote: string;
}

export interface ProcessStep { id: string; name: string; custom?: boolean }

const STEP_DEFINITIONS: [string, string[]][] = [
  ["物料接收与放行", ["物料接收", "物料放行"]],
  ["称量与分装", ["称量", "分装"]],
  ["生产准备", ["清场", "设备检查"]],
  ["配液与溶解", ["配液", "搅拌溶解"]],
  ["气体置换与吹扫", ["氮气置换", "管路氮气吹扫"]],
  ["气体保护", ["建立保护气氛", "维持气体保护"]],
  ["投料与转移", ["投料", "物料转移"]],
  ["升温、降温与保温", ["升温", "降温", "保温"]],
  ["搅拌与均质", ["搅拌", "均质"]],
  ["鼓泡与气液反应", ["液下通气", "气液反应"]],
  ["反应与保温反应", ["反应准备确认", "保温反应", "过程取样", "反应终点确认"]],
  ["淬灭、中和与调 pH", ["淬灭", "中和", "调 pH"]],
  ["萃取、洗涤与分层", ["萃取", "洗涤", "分层"]],
  ["浓缩、蒸馏与溶剂置换", ["浓缩", "蒸馏", "溶剂置换"]],
  ["脱色与吸附", ["脱色", "吸附"]],
  ["色谱纯化", ["色谱纯化"]],
  ["成盐与结晶", ["成盐", "结晶"]],
  ["养晶、老化与打浆", ["养晶", "老化", "打浆"]],
  ["过滤与离心", ["过滤", "离心"]],
  ["滤饼洗涤", ["滤饼洗涤"]],
  ["干燥", ["干燥"]],
  ["粉碎、过筛与混合", ["粉碎", "过筛", "混合"]],
  ["包装、标识与储存", ["包装", "标识", "储存"]],
  ["清洁与换批", ["设备清洁", "换批确认"]],
  ["检验、审核与批次处置", ["检验", "批次审核", "批次处置"]],
];

export const INITIAL_STEPS: ProcessStep[] = STEP_DEFINITIONS.map(([name], index) => ({
  id: `OP${String(index + 1).padStart(2, "0")}`, name,
}));

export function emptyOperation(id: string, stepId: string, name: string, custom = true): Operation {
  return {
    id, stepId, name, enabled: true, custom, stage: "临床用 / 商业化", source: "",
    parameters: [], events: [], checks: [], contingencies: [],
    startEvent: "", endEvent: "", interruption: "默认不扣除", precision: "秒",
    reviewer: "工艺负责人", qualityReviewer: "质量负责人", reviewMode: "顺序审核",
    separateReviewer: true, allowNotApplicable: true,
    continuation: "必要检查通过 + 所需终点检验符合 + 审核通过", changeNote: "",
  };
}

export const PARAMETER_KINDS: ParameterKind[] = ["数值 · 小数", "数值 · 整数", "枚举", "时长", "布尔", "文本"];
export const RECORDING_OPTIONS = ["每 30 分钟记录", "每 15 分钟记录", "连续采集", "操作开始时确认", "由起止事件计算", "开始及状态变化时", "异常事件触发", "人工记录"];

export function emptyParameter(id: string): Parameter {
  return { id, name: "", target: "", kind: "数值 · 小数", unit: "℃", comparison: "区间", value: "", upper: "", recording: "人工记录", required: true, enabled: true };
}

export function createInitialOperations(): Operation[] {
  const operations = STEP_DEFINITIONS.flatMap(([, names], index) => {
    const stepId = INITIAL_STEPS[index].id;
    return names.map((name, operationIndex) => emptyOperation(`${stepId}.${String(operationIndex + 1).padStart(2, "0")}`, stepId, name, false));
  });
  const holding = operations.find((operation) => operation.id === "OP11.02")!;
  holding.parameters = [
    { id: "temperature", name: "物料温度", target: "反应物料 · 釜内测点", kind: "数值 · 小数", unit: "℃", comparison: "区间", value: "20.0", upper: "25.0", recording: "每 30 分钟记录", required: true, enabled: true },
    { id: "speed", name: "搅拌转速", target: "搅拌系统", kind: "数值 · 整数", unit: "r/min", comparison: "区间", value: "100", upper: "150", recording: "每 30 分钟记录", required: true, enabled: true },
    { id: "gas", name: "保护气体", target: "设备气相空间", kind: "枚举", unit: "—", comparison: "允许值", value: "氮气（N₂）", upper: "", recording: "操作开始时确认", required: true, enabled: true },
    { id: "duration", name: "保温时长", target: "本操作 · 起止事件", kind: "时长", unit: "小时", comparison: "不少于", value: "12", upper: "", recording: "由起止事件计算", required: true, enabled: true },
    { id: "protection", name: "保护状态", target: "设备气氛", kind: "布尔", unit: "—", comparison: "必须为", value: "已建立", upper: "", recording: "开始及状态变化时", required: true, enabled: true },
    { id: "exception", name: "异常说明", target: "本操作", kind: "文本", unit: "—", comparison: "长度上限", value: "500", upper: "", recording: "异常事件触发", required: true, enabled: true },
  ];
  holding.events = [
    { id: "start", name: "保温开始", details: ["料温满足要求，且搅拌、保护已确认", "完整日期 + 开始时间", "当前操作", "必记"], required: true, enabled: true },
    { id: "end", name: "保温结束", details: ["保温操作结束", "完整日期 + 结束时间", "当前操作", "必记"], required: true, enabled: true },
    { id: "pause", name: "暂停 / 恢复", details: ["发生操作中断或恢复", "成对时间 + 原因", "当前操作", "发生时必记"], required: true, enabled: true },
    { id: "sample", name: "过程取样", details: ["执行规定的过程或终点取样", "取样起止 + 样品标识", "过程取样操作", "发生时必记"], required: true, enabled: true },
  ];
  holding.startEvent = "start";
  holding.endEvent = "end";
  holding.checks = [
    { id: "purge", name: "前置气体置换", details: ["氮气置换 ≥ 3 次", "计数记录核对", "反应开始前 · OP05"], required: true, enabled: true },
    { id: "holding", name: "保温时长检查", details: ["", "参数规则判定", "保温结束 · 当前操作"], parameterId: "duration", required: true, enabled: true },
    { id: "gas-check", name: "气体保护检查", details: ["维持氮气保护 · 覆盖全程", "状态与事件覆盖", "反应 / 取样期间 · OP06"], required: true, enabled: true },
    { id: "sampling", name: "取样过程控制", details: ["按方案取样 · 选择取样方案…", "清单逐项确认", "每次取样 · 过程取样"], required: true, enabled: true },
  ];
  holding.contingencies = [
    { id: "deviation", name: "参数超出规程要求 / 保护中断", details: ["记录偏差，暂停推进", "工艺 / 质量负责人", "影响评估与处置获得批准"], required: true, enabled: true },
    { id: "missing", name: "必记信息缺失 / 终点检验待结果", details: ["保持未决，补充证据", "操作 / 检验负责人", "记录完整，所需检验结果已确认"], required: true, enabled: true },
    { id: "invalid", name: "取样程序不符合 / 分析无效", details: ["评估样品或检验有效性", "QC / 质量负责人", "有效证据形成并完成复核"], required: true, enabled: true },
  ];
  return operations;
}

export function parameterRequirement(parameter: Parameter): string {
  const value = parameter.comparison === "区间" ? `${parameter.value}–${parameter.upper}` : `${parameter.comparison} ${parameter.value}`;
  return `${value}${parameter.kind === "文本" ? " 字" : parameter.unit === "—" ? "" : ` ${parameter.unit}`}`;
}

export function ruleDetails(row: RuleRow, operation: Operation): string[] {
  if (!row.parameterId) return row.details;
  const parameter = operation.parameters.find((item) => item.id === row.parameterId);
  return [parameter ? `引用：${parameter.name} ${parameterRequirement(parameter)}${parameter.enabled ? "" : "（已停用）"}` : "引用参数不存在", ...row.details.slice(1)];
}

export function validateOperation(operation: Operation): string[] {
  const issues: string[] = [];
  if (!operation.name.trim()) issues.push("请填写操作名称。");
  for (const parameter of operation.parameters.filter((item) => item.enabled)) {
    if (!parameter.name.trim() || !parameter.target.trim()) issues.push("参数名称和测量对象不能为空。");
    if (!parameter.value.trim()) issues.push(`${parameter.name}：请填写规程要求。`);
    if (["数值 · 小数", "数值 · 整数", "时长", "文本"].includes(parameter.kind)) {
      const value = Number(parameter.value), upper = Number(parameter.upper);
      if (!Number.isFinite(value)) issues.push(`${parameter.name}：要求值必须是有效数值。`);
      if ((parameter.kind === "文本" || parameter.kind === "时长") && value < 0) issues.push(`${parameter.name}：要求值不能为负数。`);
      if ((parameter.kind === "文本" || parameter.kind === "数值 · 整数") && !Number.isInteger(value)) issues.push(`${parameter.name}：要求值必须为整数。`);
      if (parameter.comparison === "区间" && (!parameter.upper.trim() || !Number.isFinite(upper) || value > upper)) issues.push(`${parameter.name}：请填写有效区间，下限不能大于上限。`);
      if (parameter.kind === "数值 · 整数" && parameter.comparison === "区间" && !Number.isInteger(upper)) issues.push(`${parameter.name}：上限必须为整数。`);
    }
  }
  for (const check of operation.checks.filter((item) => item.enabled && item.parameterId)) {
    if (!operation.parameters.some((item) => item.id === check.parameterId && item.enabled)) issues.push(`${check.name}：引用的参数不存在或已停用。`);
  }
  if (operation.events.length && (!operation.events.some((event) => event.id === operation.startEvent) || !operation.events.some((event) => event.id === operation.endEvent) || operation.startEvent === operation.endEvent)) issues.push("请选择不同的计时开始和结束事件。");
  return issues;
}
