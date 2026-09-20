import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import vm from "node:vm";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

const require = createRequire(import.meta.url);
const source = readFileSync(
  new URL("../src/components/analysis/document-harness.tsx", import.meta.url), "utf8",
);
const compiled = ts.transpileModule(source, { compilerOptions: {
  module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX,
} }).outputText;
const primitive = (tag) => function Primitive({ children, className, title }) {
  return React.createElement(tag, { className, title }, children);
};
const exports = {};
vm.runInNewContext(compiled + "\nexports.Card = SchemaCard; exports.Prompt = Prompt;", {
  exports,
  require(name) {
    if (name === "@/lib/api") return { shouldSubscribeDocumentAnalysisEvents: () => false };
    if (name === "@/lib/utils") return { cn: (...values) => values.filter(Boolean).join(" ") };
    if (name === "lucide-react") return new Proxy({}, { get: () => primitive("svg") });
    if (name.startsWith("@/components/ui/")) return new Proxy({}, {
      get: (_, key) => primitive(key === "Button" ? "button" : "span"),
    });
    return require(name);
  },
});
const render = (component, props) => renderToStaticMarkup(React.createElement(component, props));
const constraints = (label, unit) => ({
  properties: [{ iri: "urn:shared", label, kind: "property", datatype_iris: ["urn:string"] }],
  quantity_policies: [{ predicate_iri: "urn:shared", allowed_forms: ["scalar"],
    allowed_target_units: [unit], unit_requirement: "physical", endpoint_role: null }],
  identity_keys: [], unsupported_constraints: [],
});
const recordCard = {
  kind: "record_discovery", schema_card_id: "record-card", ontology_snapshot_id: "ontology",
  analysis_scope_ref: "scope", class_cards: [
    { class_iri: "urn:Device", label: "装置", ...constraints("型号", "mg") },
    { class_iri: "urn:Component", label: "部件", ...constraints("规格", "kg") },
  ],
};

test("record cards render same-IRI properties and policies under their owning classes", () => {
  const html = render(exports.Card, { card: recordCard });
  const device = html.split('aria-label="装置的属性约束"')[1]
    .split('aria-label="部件的属性约束"')[0];
  const component = html.split('aria-label="部件的属性约束"')[1];
  assert.match(device, /型号/);
  assert.match(device, /mg/);
  assert.doesNotMatch(device, /规格|kg/);
  assert.match(component, /规格/);
  assert.match(component, /kg/);
  assert.doesNotMatch(component, /型号|mg/);
  assert.equal(Object.hasOwn(recordCard, "predicates"), false);
});

test("existing predicate schema cards keep their structured view", () => {
  const details = constraints("型号", "mg");
  const html = render(exports.Card, { card: { ...details, schema_card_id: "single",
    class_iris: ["urn:Device"], predicates: details.properties }, classLabels: { "urn:Device": "装置" } });
  assert.match(html, /允许类型/);
  assert.match(html, /装置/);
  assert.match(html, /型号/);
});

for (const [stage, label] of [["discovery", "识别记录中的实体和属性"],
                            ["verification", "核验实体和属性"]]) {
  test(`record ${stage} shows a task label without a synthetic subject`, () => {
    const data = { configuration: {}, snapshot: { call: {
      task_kind: "record_discovery", call_id: "record-call", stage, model: "qwen",
      subject_label: null, predicate_label: null, members: [], status: "completed",
    }, operations: [], tool_counts: {}, truncated: [] } };
    const information = render(exports.DocumentHarnessInformation, {
      runId: "run", status: "completed", data, error: null,
    });
    assert.match(information, new RegExp(label));
    assert.doesNotMatch(information, /当前任务主体|当前任务谓词|主体：/);
    assert.match(render(exports.DocumentHarnessStream, {
      status: "completed", data, connected: false, error: null,
    }), new RegExp(label));
    const prompt = render(exports.Prompt, { context: {
      schema_card: recordCard, request: { instructions: "授权原文", input: [
        { content: [{ text: JSON.stringify({ stage }) }] },
      ] },
    } });
    assert.match(prompt, new RegExp(label));
    assert.match(prompt, new RegExp(stage === "discovery"
      ? "当前实体与属性发现任务" : "当前实体与属性核验目标"));
  });
}

const promptContext = (input) => ({
  schema_card: { class_iris: [], predicates: [] },
  request: { instructions: "按原文识别", input: [
    { role: "user", content: [{ type: "input_text", text: JSON.stringify(input) }] },
  ] },
});
const fold = (html, title) => html.split(`${title}</summary>`)[1]?.split("</details>")[0];

test("batch prompts separate each member's sources from tasks without merging permissions", () => {
  const context = promptContext({
    work_unit_id: "work-unit", stage: "discovery",
    members: [
      { task_id: "fact-task", predicate_iri: "urn:hasPart", verification_input: null,
        schema_card: { label: "MEMBER_SCHEMA" },
        source_catalog: [{ record_id: "record", summary: "SECTION_SUMMARY" }],
        evidence_refs: [{ unit_id: "unit", role: "target", fact_eligible: true }] },
      { task_id: "binding-task", predicate_iri: "urn:describes", verification_input: null,
        schema_card: { label: "OTHER_SCHEMA" },
        evidence_refs: [{ unit_id: "unit", role: "binding", fact_eligible: false }] },
    ],
    evidence_units: [{ unit_id: "unit", text: "SHARED_ORIGINAL_TEXT" }],
  });
  const original = JSON.stringify(context);
  const html = render(exports.Prompt, { context });
  const task = fold(html, "当前声明发现任务");
  assert.match(task, /fact-task/);
  assert.match(task, /binding-task/);
  assert.match(task, /urn:hasPart/);
  assert.doesNotMatch(task, /MEMBER_SCHEMA|OTHER_SCHEMA|SECTION_SUMMARY|SHARED_ORIGINAL_TEXT|evidence_refs/);
  const sources = fold(html, "授权原文与摘要 · 查看输入片段");
  const factSources = sources.split("fact-task")[1].split("binding-task")[0];
  const bindingSources = sources.split("binding-task")[1];
  assert.match(factSources, /SECTION_SUMMARY/);
  assert.match(factSources, /target/);
  assert.match(factSources, /fact_eligible&quot;: true/);
  assert.match(bindingSources, /binding/);
  assert.match(bindingSources, /fact_eligible&quot;: false/);
  assert.match(sources, /SHARED_ORIGINAL_TEXT/);
  assert.doesNotMatch(sources, /MEMBER_SCHEMA|OTHER_SCHEMA/);
  const messages = fold(html, "本轮消息与工具返回 · 按提交顺序");
  assert.match(messages, /MEMBER_SCHEMA/);
  assert.match(messages, /SHARED_ORIGINAL_TEXT/);
  assert.equal(JSON.stringify(context), original);
});

test("shared section descriptions stay in sources and keep per-member record references", () => {
  const context = promptContext({
    stage: "discovery", work_unit_id: "compact-unit",
    shared_context: { source_sections: { "section-ref": {
      section_id: "section", title: "SECTION_TITLE", summary: "COMPACT_SUMMARY", summary_source: "llm",
    } } },
    members: [{ task_id: "task", schema_card: { predicates: [] },
      source_catalog: [{ record_id: "record-1", section_ref: "section-ref", authorized_evidence_ids: ["evidence-1"] }],
      evidence_refs: [{ evidence_id: "evidence-1", fact_eligible: false }],
    }],
  });
  const html = render(exports.Prompt, { context });
  assert.doesNotMatch(fold(html, "当前声明发现任务"), /COMPACT_SUMMARY|source_sections|source_catalog/);
  const sources = fold(html, "授权原文与摘要 · 查看输入片段");
  assert.equal(sources.match(/COMPACT_SUMMARY/g)?.length, 1);
  assert.match(sources, /SECTION_TITLE/);
  assert.match(sources, /section_ref/);
  assert.match(sources, /record-1/);
  assert.match(sources, /evidence-1/);
  assert.match(sources, /fact_eligible&quot;: false/);
});

test("scalar verification keeps claim targets while moving catalog and original text to sources", () => {
  const html = render(exports.Prompt, { context: promptContext({
    stage: "verification", task_id: "verify-task", schema_card: { label: "SCHEMA" },
    verification_input: { targets: [{ target_id: "target", payload: { quote: "CLAIM_QUOTE" } }] },
    source_catalog: [{ record_id: "record", summary: "SUMMARY" }],
    evidence_units: [{ evidence_id: "evidence", text: "ORIGINAL", fact_eligible: true }],
  }) });
  const task = fold(html, "当前声明核验目标");
  assert.match(task, /verify-task/);
  assert.match(task, /CLAIM_QUOTE/);
  assert.doesNotMatch(task, /SCHEMA|SUMMARY|ORIGINAL/);
  const sources = fold(html, "授权原文与摘要 · 查看输入片段");
  assert.match(sources, /SUMMARY/);
  assert.match(sources, /ORIGINAL/);
  assert.match(sources, /fact_eligible&quot;: true/);
});

for (const [stage, label] of [["discovery", "属性消歧"], ["verification", "属性归属核验"]]) {
  test(`attribute ${stage} identifies its purpose without inventing a subject`, () => {
    const data = { configuration: {}, snapshot: { call: {
      task_kind: "property_disambiguation", call_id: "field-call", stage, model: "qwen",
      subject_label: null, predicate_label: null, members: [], status: "completed",
    }, operations: [], tool_counts: {}, truncated: [] } };
    const html = render(exports.DocumentHarnessInformation, {
      runId: "run", status: "completed", data, error: null,
    });
    assert.match(html, new RegExp(label));
    assert.doesNotMatch(html, /当前任务主体|当前任务谓词|主体：/);
    assert.match(render(exports.DocumentHarnessStream, {
      status: "completed", data, connected: false, error: null,
    }), new RegExp(label));
    const context = promptContext({ stage, task: { purpose: "property_disambiguation" },
      attribute_disambiguation: { field_id: "field", label: "编号", value: "A-01" } });
    context.schema_card = recordCard;
    const prompt = render(exports.Prompt, { context });
    assert.match(prompt, new RegExp(label));
    assert.doesNotMatch(prompt, /当前实体与属性发现任务|当前实体与属性核验目标/);
    assert.match(prompt, /A-01/);
  });
}

test("attribute result table distinguishes missing values, unresolved work, and verified results", () => {
  const data = { attribute_disambiguations: [
    { field_id: "missing", label: "编号", value: null, candidate_count: 0,
      attribute_status: "unresolved", work_status: "examined", disambiguation_attempts: 0,
      reason_code: "attribute_value_missing" },
    { field_id: "incomplete", label: "样品编号", value: "S-01", candidate_count: 2,
      attribute_status: "unresolved", work_status: "incomplete", disambiguation_attempts: 1,
      reason_code: "attribute_subject_missing" },
    { field_id: "resolved", label: "报告编号", value: "S-01", candidate_count: 1,
      attribute_status: "resolved", work_status: "examined", disambiguation_attempts: 2,
      reason_code: null },
    { field_id: "pending", label: null, value: null, candidate_count: null,
      attribute_status: "pending", work_status: "pending", disambiguation_attempts: 0,
      reason_code: null },
  ] };
  const html = render(exports.HarnessAttributeDisambiguations, { data, status: "running" });
  assert.match(html, /3 项待处理，共 4 项/);
  assert.match(html, /未提供值/);
  assert.match(html, /原文字段缺少值/);
  assert.match(html, /未决/);
  assert.match(html, /本轮未完成/);
  assert.match(html, /尚未找到相关已登记主体/);
  assert.match(html, /2 对/);
  assert.match(html, /已确认归属/);
  assert.match(html, /已尝试 2 轮/);
  assert.match(html, /尚未准备/);
  assert.equal(html.match(/S-01/g)?.length, 2);
  assert.equal((html.match(/<tr/g) || []).length, 5);
});

test("old harness payloads keep an empty attribute result view", () => {
  const html = render(exports.HarnessAttributeDisambiguations, {
    data: { snapshot: null }, status: "finished",
  });
  assert.match(html, /当前没有待消歧属性记录/);
  assert.doesNotMatch(html, /已确认归属/);
});

test("active attribute work is shown as executing without inventing a completed result", () => {
  const html = render(exports.HarnessAttributeDisambiguations, { status: "running", data: {
    attribute_disambiguations: [{ field_id: "active", label: "编号", value: "D-01",
      candidate_count: 2, attribute_status: "pending", work_status: "active",
      disambiguation_attempts: 0, reason_code: null }],
  } });
  assert.match(html, /执行中/);
  assert.match(html, /待消歧/);
  assert.match(html, /D-01/);
  assert.doesNotMatch(html, /等待处理|已确认归属|本轮已处理/);
});

test("paused runs display retained active field work as paused without changing its state", () => {
  const data = { attribute_disambiguations: [{ field_id: "paused", label: "编号", value: "D-01",
    candidate_count: 2, attribute_status: "unresolved", work_status: "active",
    disambiguation_attempts: 1, reason_code: "attribute_ambiguous" }] };
  const html = render(exports.HarnessAttributeDisambiguations, { data, status: "paused" });
  assert.match(html, /已暂停/);
  assert.match(html, /未决/);
  assert.doesNotMatch(html, /执行中|已确认归属/);
  assert.equal(data.attribute_disambiguations[0].work_status, "active");
});
