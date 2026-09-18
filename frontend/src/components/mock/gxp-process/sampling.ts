export const SAMPLING_PURPOSES = ["过程监测 / 调整", "终点判定"] as const;
export const SAMPLING_TRIGGERS = ["事件触发", "固定间隔", "事件 + 固定间隔"] as const;

export interface SamplingPoint {
  id: string;
  name: string;
  phase: string;
  location: string;
  amount: string;
  unit: string;
  count: string;
}

export interface SamplingTest {
  id: string;
  name: string;
  method: string;
  version: string;
  resultType: "数值" | "定性";
  unit: string;
  comparison: string;
  value: string;
  upper: string;
  purpose: typeof SAMPLING_PURPOSES[number];
  required: boolean;
}

export interface SamplingPlan {
  title: string;
  version: string;
  procedure: string;
  approvalReference: string;
  applicability: string;
  purpose: typeof SAMPLING_PURPOSES[number];
  rationale: string;
  trigger: typeof SAMPLING_TRIGGERS[number];
  triggerEvent: string;
  firstSample: string;
  intervalHours: string;
  toleranceMinutes: string;
  points: SamplingPoint[];
  pooling: string;
  poolingBasis: string;
  method: string;
  preparation: string;
  contaminationControl: string;
  restoration: string;
  treatment: string;
  treatmentProcedure: string;
  treatmentWithinMinutes: string;
  container: string;
  storage: string;
  transport: string;
  maxHoldHours: string;
  stabilityBasis: string;
  tests: SamplingTest[];
  adjustmentProcedure: string;
  deviationProcedure: string;
  retestProcedure: string;
}

export function emptySamplingPoint(id: string): SamplingPoint {
  return { id, name: "", phase: "", location: "", amount: "", unit: "mL", count: "" };
}

export function emptySamplingTest(id: string): SamplingTest {
  return { id, name: "", method: "", version: "", resultType: "数值", unit: "", comparison: "不超过", value: "", upper: "", purpose: "过程监测 / 调整", required: true };
}

export function createSamplingPlan(): SamplingPlan {
  return {
    title: "反应过程取样方案", version: "", procedure: "", approvalReference: "",
    applicability: "", purpose: "过程监测 / 调整", rationale: "",
    trigger: "事件触发", triggerEvent: "达到批准规程规定的反应阶段", firstSample: "",
    intervalHours: "", toleranceMinutes: "",
    points: [{ ...emptySamplingPoint("reaction-liquid"), name: "反应液取样点" }],
    pooling: "分别检验", poolingBasis: "", method: "密闭取样",
    preparation: "", contaminationControl: "", restoration: "",
    treatment: "立即淬灭 / 固定", treatmentProcedure: "", treatmentWithinMinutes: "",
    container: "", storage: "", transport: "", maxHoldHours: "", stabilityBasis: "",
    tests: [{ ...emptySamplingTest("starting-material"), name: "起始物料残留" }],
    adjustmentProcedure: "", deviationProcedure: "", retestProcedure: "",
  };
}

export const hasSamplingInterval = (plan: SamplingPlan) => plan.trigger !== "事件触发";
export const hasSamplingEvent = (plan: SamplingPlan) => plan.trigger !== "固定间隔";
export const hasSampleTreatment = (plan: SamplingPlan) => plan.treatment !== "无需即时处理";

export function samplingTestRequirement(test: SamplingTest): string {
  const value = test.value.trim() || "待配置";
  if (test.resultType === "定性") return `符合：${value}`;
  return `${test.comparison === "区间" ? `${value}–${test.upper.trim() || "待配置"}` : `${test.comparison} ${value}`} ${test.unit.trim() || "（单位 / 基准待配置）"}`;
}

export function validateSamplingPlan(plan: SamplingPlan): string[] {
  const issues: string[] = [];
  const required = (label: string, value: string) => {
    if (!value.trim()) issues.push(`过程取样：请填写${label}。`);
  };
  const positive = (label: string, value: string, integer = false, allowZero = false) => {
    const number = Number(value);
    if (!value.trim() || !Number.isFinite(number) || (allowZero ? number < 0 : number <= 0) || (integer && !Number.isInteger(number))) {
      issues.push(`过程取样：${label}须为${allowZero ? "非负数" : integer ? "正整数" : "正数"}。`);
    }
  };
  const fields: [string, string][] = [
    ["方案名称", plan.title], ["方案版本", plan.version], ["取样 SOP / 版本", plan.procedure],
    ["质量批准依据", plan.approvalReference], ["适用品种 / 工艺范围", plan.applicability],
    ["代表性与风险依据", plan.rationale], ["首次取样时点", plan.firstSample],
    ["取样前准备", plan.preparation], ["防污染与保护措施", plan.contaminationControl],
    ["取样后设备恢复", plan.restoration], ["样品容器与密封", plan.container],
    ["保存条件", plan.storage], ["运输与交接要求", plan.transport],
    ["样品稳定性依据", plan.stabilityBasis], ["偏差 / OOS 适用程序", plan.deviationProcedure],
    ["重取样 / 复测程序", plan.retestProcedure],
  ];
  fields.forEach(([label, value]) => required(label, value));
  if (hasSamplingEvent(plan)) required("触发事件 / 条件", plan.triggerEvent);
  if (hasSamplingInterval(plan)) positive("取样间隔（小时）", plan.intervalHours);
  positive("允许时间窗（±分钟）", plan.toleranceMinutes, false, true);
  if (plan.pooling === "按方案混合") required("混合规则与论证", plan.poolingBasis);
  if (hasSampleTreatment(plan)) {
    required("即时处理方法 / 版本", plan.treatmentProcedure);
    positive("取样至处理上限（分钟）", plan.treatmentWithinMinutes);
  }
  positive("取样至检验上限（小时）", plan.maxHoldHours);
  if (hasSampleTreatment(plan) && Number(plan.treatmentWithinMinutes) > Number(plan.maxHoldHours) * 60) issues.push("过程取样：即时处理时限不能晚于取样至检验上限。");
  if (!plan.points.length) issues.push("过程取样：至少配置一个取样点。");
  plan.points.forEach((point, index) => {
    const prefix = `取样点 ${index + 1}`;
    required(`${prefix}名称`, point.name);
    required(`${prefix}相别 / 物料对象`, point.phase);
    required(`${prefix}位置 / 取样口`, point.location);
    required(`${prefix}单位`, point.unit);
    positive(`${prefix}单份取样量`, point.amount);
    positive(`${prefix}份数`, point.count, true);
  });
  if (!plan.tests.length) issues.push("过程取样：至少配置一个检验项目。");
  plan.tests.forEach((test, index) => {
    const prefix = `检验项目 ${index + 1}`;
    required(`${prefix}名称`, test.name);
    required(`${prefix}方法编号`, test.method);
    required(`${prefix}方法版本`, test.version);
    required(`${prefix}接受准则`, test.value);
    if (test.resultType === "数值") {
      required(`${prefix}单位 / 结果基准`, test.unit);
      if (test.value.trim() && !Number.isFinite(Number(test.value))) issues.push(`过程取样：${prefix}要求值必须是有效数值。`);
      if (test.comparison === "区间" && (!test.upper.trim() || !Number.isFinite(Number(test.upper)) || Number(test.value) > Number(test.upper))) issues.push(`过程取样：${prefix}区间无效，下限不能大于上限。`);
    }
  });
  if (plan.purpose === "终点判定" && !plan.tests.some((test) => test.purpose === "终点判定" && test.required)) issues.push("过程取样：终点判定须配置至少一个必需的终点检验项目。");
  if (plan.purpose === "过程监测 / 调整" || plan.tests.some((test) => test.purpose === "过程监测 / 调整")) required("预批准调整范围与程序", plan.adjustmentProcedure);
  return issues;
}
