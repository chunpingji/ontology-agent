import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";
import ts from "typescript";

function load(name, dependencies = {}) {
  const source = readFileSync(new URL(`../src/components/mock/gxp-process/${name}.ts`, import.meta.url), "utf8");
  const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
  const exports = {};
  vm.runInNewContext(compiled, { exports, structuredClone, require: (id) => {
    assert.ok(id in dependencies, `Unexpected dependency: ${id}`);
    return dependencies[id];
  } });
  return exports;
}
const sampling = load("sampling");
const data = load("data", { "./sampling": sampling });
const { MOCK_CMC_REPORTS: reports, generateBatchDraft, batchNumberIssue } = load("batch", { "./data": data });
const generate = (report, selected = report.operations.map((item) => item.operationId)) => generateBatchDraft(report.id, " DEMO-001 ", selected, "workspace-test", "2026-09-10T00:00:00Z");

test("report selection changes the route and material parameters, without template presets leaking", () => {
  const a = generate(reports[0]), b = generate(reports[1]);
  assert.equal(a.operations.length, 10);
  assert.equal(b.operations.length, 8);
  assert.equal(b.operations.some((item) => item.id === "OP11.02"), false);
  assert.equal(a.operations.find((item) => item.id === "OP21.01").parameters[0].value, "45");
  assert.equal(b.operations.find((item) => item.id === "OP21.01").parameters[0].value, "35");
  const holding = a.operations.find((item) => item.id === "OP11.02");
  assert.equal(holding.parameters.find((item) => item.id === "temperature").value, "95");
  assert.equal(holding.parameters.some((item) => item.id === "gas"), false);
  assert.deepEqual([...data.validateOperation(holding)], []);
});

test("trimming is deterministic, preserves report order and removes empty groups and dangling checks", () => {
  const selected = ["OP21.01", "OP11.02", "OP21.01", "not-applicable"];
  const result = generate(reports[0], selected);
  assert.deepEqual([...result.operations.map((item) => item.id)], ["OP11.02", "OP21.01"]);
  assert.deepEqual([...result.steps.map((item) => item.id)], ["OP11", "OP21"]);
  assert.equal(result.batch.excludedOperationIds.length + result.operations.length, data.createInitialOperations().length);
  assert.ok(result.batch.excludedOperationIds.includes("OP05.01"));
  for (const operation of result.operations) {
    assert.deepEqual([...data.validateOperation(operation)], []);
    assert.equal(operation.checks.some((item) => item.details.join(" ").includes("OP05")), false);
  }
});

test("each draft retains immutable source provenance and independent nested configuration", () => {
  const a = generate(reports[0]), b = generate(reports[0]);
  const operation = a.operations.find((item) => item.id === "OP11.02");
  assert.equal(operation.batchSourceOperationId, "OP11.02");
  assert.match(operation.source, /CMC-DEMO-001.*3.2.S.2.2/);
  operation.parameters[0].value = "999";
  a.batch.report.operations[0].excerpt = "edited";
  assert.equal(b.operations.find((item) => item.id === "OP11.02").parameters[0].value, "95");
  assert.notEqual(reports[0].operations[0].excerpt, "edited");
  assert.equal(data.createInitialOperations().find((item) => item.id === "OP11.02").parameters[0].value, "20.0");
  const plan = a.operations.find((item) => item.sampling).sampling;
  assert.equal(plan.firstSample, "反应开始后 12 小时");
  assert.equal(plan.intervalHours, "3");
  assert.equal(plan.points[0].count, "1");
  assert.equal(plan.points[0].amount, "1");
  assert.equal(plan.approvalReference, "");
  assert.equal(plan.tests[0].value, "");
  assert.ok(sampling.validateSamplingPlan(plan).length > 0);
  assert.equal(a.batch.demo, true);
  assert.equal(a.batch.batchNumber, "DEMO-001");
});

test("invalid report, empty trimming and unsafe or duplicate batch numbers cannot generate", () => {
  assert.throws(() => generateBatchDraft("missing", "DEMO-001", ["OP11.02"], "id", "now"), /CMC/);
  assert.throws(() => generate(reports[0], []), /至少/);
  for (const value of [" ", "../demo", "x/y", "x".repeat(65)]) assert.ok(batchNumberIssue(value), value);
  assert.match(batchNumberIssue(" demo-001 ", ["DEMO-001"]), /已存在/);
  assert.equal(batchNumberIssue(" DEMO-002 ", ["DEMO-001"]), "");
});
