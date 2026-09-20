import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import vm from "node:vm";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

const require = createRequire(import.meta.url);
function load(path) {
  const exports = {};
  const source = readFileSync(new URL(path, import.meta.url), "utf8");
  vm.runInNewContext(ts.transpileModule(source, { compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX,
  } }).outputText, { exports, require(name) {
    if (name.endsWith("/attribute-calibration-list")) return load("../src/components/analysis/attribute-calibration-list.tsx");
    if (name === "@/lib/api") return { getIdentity: () => ({ username: "analyst", role: "senior_analyst" }) };
    if (name === "@/lib/document-analysis") return { formatDocumentAnalysisReason: () => "尚未完成校准" };
    if (name === "@/lib/document-graph") return load("../src/lib/document-graph.ts");
    if (name === "@/lib/utils") return { cn: (...values) => values.filter(Boolean).join(" ") };
    if (name === "@/components/analysis/document-graph-canvas") return { DocumentGraphCanvas: () => null };
    if (name === "@/components/ui/use-document-tree") return { useDocumentTree: () => ({ getItems: () => [] }) };
    if (name.startsWith("@/components/ui/")) return new Proxy({}, {
      get: () => ({ children }) => React.createElement("div", null, children),
    });
    if (name.startsWith("@/") || name.startsWith("./")) return {};
    return require(name);
  } });
  return exports;
}
const { AttributeCalibrationList } = load("../src/components/analysis/attribute-calibration-list.tsx");
const { TemplateDocumentGraphPanel } = load("../src/components/analysis/template-document-graph-panel.tsx");
const { DocumentRelationshipGraph } = load("../src/components/analysis/document-relationship-graph.tsx");

function candidate(extra = {}) {
  return {
    candidate_id: "candidate", field_label: "时间", raw_value: "2026年02月", status: "pending",
    parsed_value: { value: "2026-02", datatype_iri: "http://www.w3.org/2001/XMLSchema#gYearMonth",
      precision: "month", quantity: null, issues: [] },
    options: [], reason_codes: ["attribute_subject_missing"],
    checks: { binding: "failed", metric: "failed", shacl: "not_checked" },
    source_selection_refs: { label: ["label-ref"], value: ["value-ref"] }, ...extra,
  };
}

for (const surface of ["document analysis", "template workspace"]) test(`${surface} exposes candidate-only values without accepted facts`, () => {
  const graph = { entities: [], properties: [], relationships: [], availability: "partial",
    graph_snapshot: { root_ref: { entity_id: "root", revision: 1 } },
    attribute_candidates: [candidate()], coverage: { subjects: [] },
    unresolved: { undetermined: 0, not_checked: 0 } };
  const component = surface === "document analysis"
    ? React.createElement(DocumentRelationshipGraph, {
      artifact: graph, projection: "verified", onProjectionChange() {}, onSelectionRef() {},
    })
    : React.createElement(TemplateDocumentGraphPanel, {
      model: { graph, run: null, select() {}, loading: false, projection: "verified" },
    });
  const html = renderToStaticMarkup(component);
  assert.match(html, /待校准属性 1 项/);
  assert.match(html, /待校准，不用于推理\/报告/);
  assert.match(html, /原值：2026年02月/);
  assert.match(html, /解析值：2026-02/);
  assert.match(html, /类型：gYearMonth/);
  assert.match(html, /精度：月/);
  assert.match(html, /候选归属：尚未确定主体和属性/);
  assert.match(html, /图约束：尚未执行/);
  assert.doesNotMatch(html, /系统验证通过/);
});

test("rejected mapping preserves source and visible owner option; resolved candidates disappear", () => {
  const html = renderToStaticMarkup(React.createElement(AttributeCalibrationList, {
    candidates: [candidate({ status: "rejected_mapping", options: [{ subject_label: "报告甲",
      predicate_label: "计划生产日期", predicate_iri: "urn:plannedDate" }],
    reason_codes: ["field_column_mismatch", "shacl_not_evaluated"] })], select() {},
  }));
  assert.match(html, /当前映射被拒绝/);
  assert.match(html, /报告甲 → 计划生产日期/);
  assert.match(html, /引用范围与字段边界不一致/);
  assert.match(html, /前置检查未通过，图约束检查尚未执行/);
  assert.equal(renderToStaticMarkup(React.createElement(AttributeCalibrationList, {
    candidates: [candidate({ status: "resolved" })], select() {},
  })), "");
});

test("field and value buttons select only their own opaque source references", () => {
  const selections = [];
  const tree = AttributeCalibrationList({ candidates: [candidate()], select: (ref) => selections.push(ref) });
  function buttons(node) {
    if (Array.isArray(node)) return node.flatMap(buttons);
    if (!node || typeof node !== "object") return [];
    if (typeof node.type === "function") return buttons(node.type(node.props));
    if (node.type === "button") return [node];
    return buttons(node.props?.children);
  }
  const sources = buttons(tree);
  assert.deepEqual(Array.from(sources, (node) => node.props["aria-label"]), ["字段原文", "原值原文"]);
  sources.forEach((node) => node.props.onClick());
  assert.deepEqual(selections, ["label-ref", "value-ref"]);
});

test("parsed ranges, bounds and approximate values retain their meaning", () => {
  for (const [quantity, text] of [
    [{ kind: "range", lower: "5", upper: "10", lower_inclusive: true, upper_inclusive: false }, "[5, 10)"],
    [{ kind: "comparison", operator: "ge", lower: "5" }, "≥ 5"],
    [{ kind: "comparison", operator: "approx", scalar: "5" }, "≈ 5"],
  ]) {
    const html = renderToStaticMarkup(React.createElement(AttributeCalibrationList, {
      candidates: [candidate({ parsed_value: { value: null, datatype_iri: "urn:decimal",
        quantity: { ...quantity, source_unit: "mg" }, precision: null, issues: [] } })], select() {},
    }));
    assert.ok(html.includes(`解析值：${text}`), html);
    assert.match(html, /单位：mg/);
    assert.doesNotMatch(html, /尚无可解析的规范值/);
  }
});
