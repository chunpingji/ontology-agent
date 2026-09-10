import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";
import ts from "typescript";

function loadModule(name, dependencies = {}) {
  const source = readFileSync(new URL(`../src/components/mock/gxp-process/${name}.ts`, import.meta.url), "utf8");
  const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
  const exports = {};
  vm.runInNewContext(compiled, { exports, require: (id) => {
    assert.ok(id in dependencies, `Unexpected dependency: ${id}`);
    return dependencies[id];
  } });
  return exports;
}
const sampling = loadModule("sampling");
const { createInitialOperations, validateOperation } = loadModule("data", { "./sampling": sampling });
const { createSamplingPlan, validateSamplingPlan } = sampling;

// Synthetic configuration for field-validation tests; these values are not product limits.
function completePlan() {
  const plan = createSamplingPlan();
  for (const key of ["version", "procedure", "approvalReference", "applicability", "rationale", "firstSample", "preparation", "contaminationControl", "restoration", "treatmentProcedure", "container", "storage", "transport", "stabilityBasis", "adjustmentProcedure", "deviationProcedure", "retestProcedure"]) plan[key] = `测试依据：${key}`;
  Object.assign(plan, { toleranceMinutes: "0", treatmentWithinMinutes: "5", maxHoldHours: "2" });
  Object.assign(plan.points[0], { phase: "测试反应液", location: "测试取样口", amount: "2", count: "1" });
  Object.assign(plan.tests[0], { method: "TEST-METHOD", version: "test-1", unit: "面积 %", value: "0.5" });
  return plan;
}

test("sampling starts incomplete without inheriting holding record frequency or product limits", () => {
  const operations = createInitialOperations();
  const holding = operations.find((operation) => operation.id === "OP11.02");
  const operation = operations.find((item) => item.id === "OP11.03");
  assert.equal(holding.parameters.find((parameter) => parameter.id === "temperature").recording, "每 3 小时记录");
  assert.equal(operation.sampling.intervalHours, "");
  assert.equal(operation.sampling.points[0].amount, "");
  assert.equal(operation.sampling.tests[0].value, "");
  assert.ok(validateOperation(operation).some((issue) => issue.includes("质量批准依据")));
  operation.sampling = completePlan();
  assert.deepEqual([...validateOperation(operation)], []);
  const copy = structuredClone(operation);
  copy.sampling.points[0].amount = "99";
  assert.equal(operation.sampling.points[0].amount, "2");
  assert.equal(createInitialOperations().find((item) => item.id === "OP11.03").sampling.points[0].amount, "");
});

test("schedule validates only applicable trigger fields and rejects invalid intervals", () => {
  const plan = completePlan();
  plan.intervalHours = "invalid hidden value";
  assert.deepEqual([...validateSamplingPlan(plan)], []);
  plan.trigger = "固定间隔";
  plan.triggerEvent = "";
  for (const interval of ["", "0", "-1", "Infinity", "NaN"]) {
    plan.intervalHours = interval;
    assert.ok(validateSamplingPlan(plan).some((issue) => issue.includes("取样间隔")), interval);
  }
  plan.intervalHours = "1.5";
  assert.deepEqual([...validateSamplingPlan(plan)], []);
  plan.trigger = "事件 + 固定间隔";
  assert.ok(validateSamplingPlan(plan).some((issue) => issue.includes("触发事件")));
  plan.triggerEvent = "测试事件";
  plan.toleranceMinutes = "-1";
  assert.ok(validateSamplingPlan(plan).some((issue) => issue.includes("允许时间窗")));
});

test("points require identity, positive amounts, integer counts and pooling justification", () => {
  const plan = completePlan();
  plan.points[0].count = "1.5";
  plan.points[0].amount = "0";
  plan.points[0].phase = "";
  plan.pooling = "按方案混合";
  const issues = validateSamplingPlan(plan);
  for (const field of ["份数", "单份取样量", "相别", "混合规则"]) assert.ok(issues.some((issue) => issue.includes(field)), field);
  plan.points = [];
  assert.ok(validateSamplingPlan(plan).some((issue) => issue.includes("至少配置一个取样点")));
});

test("sample treatment is conditional but stability evidence always remains required", () => {
  const plan = completePlan();
  plan.treatmentWithinMinutes = "121";
  assert.ok(validateSamplingPlan(plan).some((issue) => issue.includes("不能晚于")));
  plan.treatment = "无需即时处理";
  plan.treatmentWithinMinutes = "";
  plan.treatmentProcedure = "";
  assert.deepEqual([...validateSamplingPlan(plan)], []);
  plan.stabilityBasis = "";
  assert.ok(validateSamplingPlan(plan).some((issue) => issue.includes("稳定性依据")));
});

test("endpoint purpose needs required endpoint evidence and does not use monitoring exemption", () => {
  const plan = completePlan();
  plan.purpose = "终点判定";
  assert.ok(validateSamplingPlan(plan).some((issue) => issue.includes("必需的终点检验")));
  plan.tests[0].purpose = "终点判定";
  plan.tests[0].required = false;
  assert.ok(validateSamplingPlan(plan).some((issue) => issue.includes("必需的终点检验")));
  plan.tests[0].required = true;
  plan.adjustmentProcedure = "";
  assert.deepEqual([...validateSamplingPlan(plan)], []);
  plan.deviationProcedure = "";
  assert.ok(validateSamplingPlan(plan).some((issue) => issue.includes("OOS")));
});

test("numeric ranges and qualitative criteria keep distinct validation requirements", () => {
  const plan = completePlan();
  const item = plan.tests[0];
  item.comparison = "区间";
  item.upper = "0.1";
  assert.ok(validateSamplingPlan(plan).some((issue) => issue.includes("区间无效")));
  item.resultType = "定性";
  item.value = "符合测试描述";
  item.unit = "";
  assert.deepEqual([...validateSamplingPlan(plan)], []);
  item.value = "";
  assert.ok(validateSamplingPlan(plan).some((issue) => issue.includes("接受准则")));
  plan.tests = [];
  assert.ok(validateSamplingPlan(plan).some((issue) => issue.includes("至少配置一个检验项目")));
});
