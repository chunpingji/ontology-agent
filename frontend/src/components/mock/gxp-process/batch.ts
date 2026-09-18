import { INITIAL_STEPS, createInitialOperations, emptyParameter, type Operation, type Parameter, type ProcessStep } from "./data";

export interface CmcOperationExample {
  operationId: string;
  section: string;
  excerpt: string;
  parameters: Parameter[];
}

export interface MockCmcReport {
  id: string;
  title: string;
  product: string;
  version: string;
  date: string;
  route: string;
  operations: CmcOperationExample[];
}

export interface BatchDraft {
  id: string;
  batchNumber: string;
  generatedAt: string;
  report: MockCmcReport;
  includedOperationIds: string[];
  excludedOperationIds: string[];
  demo: true;
}

function range(id: string, name: string, value: string, upper: string, unit = "℃", recording = "人工记录"): Parameter {
  return { ...emptyParameter(id), name, target: "本操作 · 物料测点", value, upper, unit, recording };
}

function duration(value: string): Parameter {
  return { ...range("duration", "操作时长", value, "", "小时", "由起止事件计算"), kind: "时长", comparison: "不少于", target: "本操作 · 起止事件" };
}

// Authored demonstration fixtures, not extracts from actual reports or approved process limits.
export const MOCK_CMC_REPORTS: MockCmcReport[] = [
  {
    id: "CMC-DEMO-001", title: "示例原料药 A · 合成工艺研发 CMC", product: "示例原料药 A",
    version: "v2.1（演示）", date: "2026-09-08", route: "投料 → 氮气置换 → 升温反应 → 过程取样 → 降温结晶 → 过滤干燥",
    operations: [
      { operationId: "OP03.01", section: "3.2.S.2.2 / 生产准备", excerpt: "核对清场记录、设备清洁状态和本批物料标识后开始操作。", parameters: [] },
      { operationId: "OP07.01", section: "3.2.S.2.2 / 投料", excerpt: "按本批配方顺序投料，逐项记录物料批号、实际投料量与操作时间。", parameters: [] },
      { operationId: "OP05.01", section: "3.2.S.2.2 / 气氛置换", excerpt: "反应釜氮气置换不少于 3 次，记录每次置换的起止时间。", parameters: [{ ...range("purge-count", "置换次数", "3", "", "次"), kind: "数值 · 整数", comparison: "不少于", target: "反应釜" }] },
      { operationId: "OP08.01", section: "3.2.S.2.2 / 升温", excerpt: "将物料升温至 95–105 ℃，达到温度范围后进入保温反应。", parameters: [range("temperature", "物料温度", "95", "105")] },
      { operationId: "OP11.02", section: "3.2.S.2.2 / 保温反应", excerpt: "物料维持 95–105 ℃，搅拌转速 100–150 r/min；至少反应 12 小时，每 3 小时记录物料温度，每 1 小时记录搅拌转速。", parameters: [range("temperature", "物料温度", "95", "105", "℃", "每 3 小时记录"), { ...range("speed", "搅拌转速", "100", "150", "r/min", "每 1 小时记录"), kind: "数值 · 整数", target: "搅拌系统" }, { ...duration("12"), name: "保温时长" }] },
      { operationId: "OP11.03", section: "3.2.S.2.4 / 过程控制", excerpt: "反应开始 12 小时后首次取样，之后每 3 小时取样；反应液每次 1 份、每份 1 mL，分别编号送检。起始物料残留采用 HPLC 检查。", parameters: [] },
      { operationId: "OP08.02", section: "3.2.S.2.2 / 降温", excerpt: "完成反应阶段确认后，将物料降温至 20–25 ℃。", parameters: [range("temperature", "物料温度", "20", "25")] },
      { operationId: "OP17.02", section: "3.2.S.2.2 / 结晶", excerpt: "在 20–25 ℃搅拌结晶不少于 2 小时。", parameters: [range("temperature", "物料温度", "20", "25"), duration("2")] },
      { operationId: "OP19.01", section: "3.2.S.2.2 / 过滤", excerpt: "对结晶悬浮液进行过滤，记录过滤开始、结束时间及滤饼状态。", parameters: [] },
      { operationId: "OP21.01", section: "3.2.S.2.2 / 干燥", excerpt: "滤饼在 45–55 ℃干燥，记录干燥过程温度和起止时间。", parameters: [range("temperature", "物料温度", "45", "55", "℃", "每 1 小时记录")] },
    ],
  },
  {
    id: "CMC-DEMO-002", title: "示例原料药 B · 结晶纯化研发 CMC", product: "示例原料药 B",
    version: "v1.3（演示）", date: "2026-09-09", route: "投料溶解 → 降温结晶 → 养晶 → 过滤洗涤 → 干燥",
    operations: [
      { operationId: "OP07.01", section: "3.2.S.2.2 / 投料", excerpt: "核对粗品及溶剂批号，按批配方投料并记录实际量。", parameters: [] },
      { operationId: "OP04.02", section: "3.2.S.2.2 / 溶解", excerpt: "在 55–65 ℃搅拌至物料溶解，记录溶解终点观察结果。", parameters: [range("temperature", "物料温度", "55", "65")] },
      { operationId: "OP08.02", section: "3.2.S.2.2 / 降温", excerpt: "将溶液降温至 5–10 ℃后进入结晶阶段。", parameters: [range("temperature", "物料温度", "5", "10")] },
      { operationId: "OP17.02", section: "3.2.S.2.2 / 结晶", excerpt: "在 5–10 ℃搅拌结晶，记录晶体析出时间及外观。", parameters: [range("temperature", "物料温度", "5", "10")] },
      { operationId: "OP18.01", section: "3.2.S.2.2 / 养晶", excerpt: "在 5–10 ℃养晶不少于 4 小时。", parameters: [range("temperature", "物料温度", "5", "10"), duration("4")] },
      { operationId: "OP19.01", section: "3.2.S.2.2 / 过滤", excerpt: "过滤收集晶体，记录滤液状态与过滤起止时间。", parameters: [] },
      { operationId: "OP20.01", section: "3.2.S.2.2 / 洗涤", excerpt: "按批配方洗涤滤饼，记录洗涤溶剂批号及实际用量。", parameters: [] },
      { operationId: "OP21.01", section: "3.2.S.2.2 / 干燥", excerpt: "滤饼在 35–45 ℃干燥，记录温度及起止时间。", parameters: [range("temperature", "物料温度", "35", "45", "℃", "每 1 小时记录")] },
    ],
  },
];

export function batchNumberIssue(value: string, existing: string[] = []): string {
  const normalized = value.trim();
  if (!normalized) return "请填写目标批号。";
  if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(normalized)) return "批号限 1–64 位字母、数字、点、短横线或下划线，且以字母或数字开头。";
  if (existing.some((item) => item.toLowerCase() === normalized.toLowerCase())) return "本次会话已存在该批号，请使用新批号或切换至已有草稿。";
  return "";
}

export function generateBatchDraft(reportId: string, batchNumber: string, selected: string[], id: string, generatedAt: string): { batch: BatchDraft; steps: ProcessStep[]; operations: Operation[] } {
  const report = MOCK_CMC_REPORTS.find((item) => item.id === reportId);
  if (!report) throw new Error("请选择有效的 CMC 示例报告。");
  const issue = batchNumberIssue(batchNumber);
  if (issue) throw new Error(issue);
  const clauses = report.operations.filter((item) => selected.includes(item.operationId));
  if (!clauses.length) throw new Error("请至少保留一个操作。");
  const templates = createInitialOperations();
  const operations = clauses.map((clause) => {
    const operation = structuredClone(templates.find((item) => item.id === clause.operationId)!);
    operation.batchSourceOperationId = clause.operationId;
    operation.source = `${report.id} · ${report.version} · ${clause.section}（示例）`;
    operation.stage = "临床用";
    operation.configurationLayout = "tabs";
    operation.changeNote = `根据 ${report.id} 静态示例生成批号 ${batchNumber.trim()} 的配置草稿。`;
    operation.parameters = structuredClone(clause.parameters);
    // Rebuild operation-local requirements; no cross-operation links survive trimming.
    operation.events = [
      { id: "start", name: `${operation.name}开始`, details: ["本操作开始", "开始日期 / 时间 + 操作人", "当前操作", "必记"], required: true, enabled: true },
      { id: "end", name: `${operation.name}结束`, details: ["本操作结束", "结束日期 / 时间 + 操作人", "当前操作", "必记"], required: true, enabled: true },
    ];
    operation.startEvent = "start";
    operation.endEvent = "end";
    operation.checks = [
      { id: "source-review", name: "操作要求核对", details: [clause.excerpt, "操作记录与示例条款核对", "当前操作"], required: true, enabled: true },
      ...operation.parameters.map((parameter) => ({ id: `check-${parameter.id}`, name: `${parameter.name}检查`, details: ["", "记录值与配置要求核对", "当前操作"], parameterId: parameter.id, required: true, enabled: true })),
    ];
    operation.contingencies = [{ id: "deviation", name: "记录缺失 / 参数超出要求", details: ["记录异常并保持未决，提交评估", "工艺 / 质量负责人", "所需证据完整且处置获准后继续"], required: true, enabled: true }];
    if (operation.sampling) {
      operation.sampling.title = `${report.product} · 反应过程取样方案（示例）`;
      operation.sampling.version = report.version;
      operation.sampling.procedure = operation.source;
      operation.sampling.applicability = `${report.product} / 批号 ${batchNumber.trim()}`;
      operation.sampling.trigger = "事件 + 固定间隔";
      operation.sampling.triggerEvent = "反应开始（示例报告约定）";
      operation.sampling.firstSample = "反应开始后 12 小时";
      operation.sampling.intervalHours = "3";
      Object.assign(operation.sampling.points[0], { phase: "反应液", location: "反应釜取样口（示例）", amount: "1", count: "1" });
      operation.sampling.tests[0].method = "HPLC（示例，方法编号待配置）";
      // Approval, assay limits and handling requirements remain incomplete draft fields.
    }
    return operation;
  });
  const includedOperationIds = operations.map((item) => item.id);
  const stepIds = [...new Set(operations.map((item) => item.stepId))];
  const steps = stepIds.map((stepId) => ({ ...INITIAL_STEPS.find((item) => item.id === stepId)! }));
  return {
    steps, operations,
    batch: { id, batchNumber: batchNumber.trim(), generatedAt, report: structuredClone(report), includedOperationIds, excludedOperationIds: templates.filter((item) => !includedOperationIds.includes(item.id)).map((item) => item.id), demo: true },
  };
}
