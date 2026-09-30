import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import vm from "node:vm";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

const require = createRequire(import.meta.url);
const srcRoot = fileURLToPath(new URL("../src", import.meta.url));
const componentPath = path.join(srcRoot, "components/analysis/source-harness-panel.tsx");
const componentSource = readFileSync(componentPath, "utf8");
const modules = new Map();
function loadModule(filename) {
  if (modules.has(filename)) return modules.get(filename);
  const result = {};
  modules.set(filename, result);
  vm.runInNewContext(ts.transpileModule(readFileSync(filename, "utf8"), { compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX,
  } }).outputText, { exports: result, require: (specifier) => {
    if (specifier === "@/lib/api") return { DOCUMENT_HARNESS_PROTOCOL: "document-harness-v1" };
    if (specifier === "@/lib/document-analysis") return { DOCUMENT_ANALYSIS_STATUS_LABELS: {} };
    if (specifier === "@/lib/utils") return { cn: (...values) => values.filter(Boolean).join(" ") };
    if (specifier.startsWith("@/components/ui/")) return new Proxy({}, { get: (_object, name) => name === "badgeVariants" ? () => "badge" : ({ children, variant: _variant, size: _size, onValueChange: _onValueChange, onOpenChange: _onOpenChange, ...props }) =>
      React.createElement(name === "Button" ? "button" : name === "Input" ? "input" : "div", props, children) });
    if (specifier.startsWith("@/") || specifier.startsWith(".")) {
      const base = specifier.startsWith("@/") ? path.join(srcRoot, specifier.slice(2)) : path.resolve(path.dirname(filename), specifier);
      return loadModule([`${base}.ts`, `${base}.tsx`].find(existsSync));
    }
    return require(specifier);
  } }, { filename });
  return result;
}
const exports = loadModule(componentPath);
const helpers = loadModule(path.join(srcRoot, "lib/source-harness.ts"));
const { rootedCircleLayout } = loadModule(path.join(srcRoot, "lib/source-harness-graph-layout.ts"));

const source = { source_id: "source:one", text: "样品甲", start: 0, end: 3, page: 2, section_id: "s1", block_id: "b1" };
const mention = { id: "object:1", label: "样品甲", role: "样品", class_iri: null, class_label: null, state: "unresolved", reason: "原文指称已定位，类型限定不足", evidence: [source] };
const entity = { ...mention, mentions: [mention] };
function graph() {
  return {
    protocol: "document-harness-v1", run_id: "run", revision: 1, status: "running", stage: "discover",
    progress: { completed_calls: 7, candidate_count: 4, fact_count: 0, windows_total: 10, windows_discovered: 2, windows_reviewed: 1, scope_complete: false,
      stage_costs: [{ stage: "discover", calls: 5, seconds: 123.4, input_tokens: 4000, output_tokens: 2000 }] },
    entities: [entity], coreferences: [], properties: [], relations: [], relation_groups: [], observations: [], targets: [],
  };
}
const render = (Component, props) => renderToStaticMarkup(React.createElement(Component, props));
const observation = (overrides = {}) => ({
  id: "o", kind: "field", label: "原字段", field_id: "f", value: "原值",
  candidate_subject_ids: [], object_id: null, reason: "原文观察", evidence: [source],
  discovery_cards: [], alignments: [], ...overrides,
});

test("options remain a visible group with a separate unresolved timing judgment", () => {
  const value = graph();
  value.entities.push({ ...entity, id: "other", label: "样品乙" });
  value.relation_groups.push({
    id: "g", subject_id: entity.id, object_ids: [entity.id, "other"],
    predicate_iri: "urn:uses", label: "使用对象", card: null, predicate: null,
    participation: "options", selection: "exactly_one", timing: "unspecified",
    timing_state: "unresolved", timing_reason: "原文未说明时间", state: "accepted",
    reason: "两个对象是候选选项", polarity: "positive", conditions: [], evidence: [source],
  });
  const html = render(exports.HarnessCandidates, {
    graph: value, selectedEntity: entity.id, onSelectEntity() {}, onSource() {},
  });
  assert.match(html, /备选对象组/);
  assert.match(html, /择一/);
  assert.match(html, /原文未说明时间/);
  assert.equal(value.relations.length, 0);
});

test("untyped referents retain fields, reasons and exact source text without forcing an ontology identity", () => {
  const value = graph();
  value.properties = [{ id: "field:1", subject_id: entity.id, predicate_iri: null, label: "样品编号", value: "N/A", source_value: "N/A", source_unit: null, value_component: "whole", state: "unresolved", reason: "缺失标记保留为观察", evidence: [{ ...source, text: "样品编号：N/A" }] }];
  value.observations = [observation({ id: "observation:1", kind: "relation", label: "原文关系措辞", field_id: null, value: null, reason: "当前卡没有匹配的合法谓词" })];
  const html = render(exports.HarnessCandidates, { graph: value, selectedEntity: entity.id, onSelectEntity() {}, onSource() {} });
  assert.match(html, /类型限定不足/);
  assert.match(html, /尚未对齐/);
  assert.match(html, /样品编号：N\/A/);
  const detail = render(exports.HarnessObservationDetail, { graph: value, item: value.observations[0], onSelectEntity() {}, onSource() {} });
  assert.match(detail, /当前卡没有匹配的合法谓词/);
  assert.match(html, /查看原文/);
});

test("rejected endpoints retain visible original range observations and candidate ownership", () => {
  const value = graph();
  value.entities[0] = { ...entity, state: "rejected" };
  value.observations = [observation({ id: "obs-range", label: "负载范围", field_id: "range", value: "3.8–6.6 kg", candidate_subject_ids: [entity.id], reason: "归属待核对" })];
  const html = render(exports.HarnessObservationDetail, { graph: value, item: value.observations[0], onSelectEntity() {}, onSource() {} });
  assert.match(html, /原文观察：负载范围/);
  assert.match(html, /完整原值：3.8–6.6 kg/);
  assert.match(html, /候选主体：<button[^>]*>样品甲<\/button>/);
  assert.match(html, /归属待核对/);
});

function mixedObservationGraph() {
  const value = graph();
  const card = { iri: "urn:schema:Intermediate", label: "中间体" };
  const predicate = { iri: "https://example.test/material/identifier", label: "中间体标识",
    namespace: "https://example.test/material/", domain_text: "物料 <urn:schema:Material>" };
  value.entities = [{ ...entity, id: "report", label: "报告甲", class_iri: "urn:schema:Report", class_label: "报告", state: "accepted" },
    { ...entity, id: "material", label: "物料甲", class_iri: card.iri, class_label: card.label, state: "accepted" }];
  value.properties = [{ id: "p", field_id: "f", subject_id: "material", card, predicate,
    predicate_iri: predicate.iri, label: predicate.label, value: "ABC", source_value: "ABC",
    source_unit: null, value_component: "whole", value_evidence: [source], state: "accepted", reason: "原文标识已核对", evidence: [source] }];
  value.observations = [observation({ id: "mixed", label: "物料编号", value: "ABC",
    candidate_subject_ids: ["report", "material"], discovery_cards: [
      { iri: "urn:schema:Report", label: "报告", role: "document_properties" }, { ...card, role: "reading" },
    ], alignments: [
      { subject_id: "report", card: { iri: "urn:schema:Report", label: "报告" }, property_ids: [],
        attempts: [{ state: "unmatched", reason: "报告属性菜单无物料标识", predicates: [] }] },
      { subject_id: "material", card, property_ids: ["p"], attempts: [{ state: "mapped", reason: "已映射", predicates: [predicate] }] },
    ] }), observation({ id: "unowned", label: "未归属字段" }),
    observation({ id: "failure", kind: "failure", label: "发现调用失败", field_id: null, reason: "请求超时" })];
  return value;
}

test("one field displays separate subject-card reasons, accepted properties and defining namespace", () => {
  const value = mixedObservationGraph();
  const table = render(exports.HarnessObservations, { graph: value, selectedEntity: "material", onSelectEntity() {}, onSource() {} });
  assert.match(table, /原文观察与对齐结果（2）/);
  assert.match(table, /观察数量不等于未采信数量/);
  assert.match(table, /物料编号：ABC/);
  assert.match(table, /有已采信属性/);
  assert.match(table, /有菜单未匹配/);
  const html = render(exports.HarnessObservationDetail, { graph: value, item: value.observations[0], onSelectEntity() {}, onSource() {} });
  const report = html.indexOf("属性对齐主体：报告甲");
  const material = html.indexOf("属性对齐主体：物料甲");
  assert.ok(report >= 0 && material > report);
  assert.match(html.slice(report, material), /报告属性菜单无物料标识/);
  assert.doesNotMatch(html.slice(report, material), /原文标识已核对/);
  assert.match(html.slice(material), /属性核对原因：原文标识已核对/);
  assert.match(html, /所属本体命名空间：https:\/\/example.test\/material\//);
  assert.match(html, /属性定义域：物料 &lt;urn:schema:Material&gt;/);
  assert.match(html, /文档根属性指引/);
  assert.match(html, /阅读卡/);
  assert.match(table, /调用失败（1）/);
  assert.match(table, /请求超时/);
  assert.doesNotMatch(table, /原文观察与未采信原因/);
});

test("filters preserve mixed accepted and unmatched outcomes and keep failures separate", () => {
  const value = mixedObservationGraph();
  const filter = (kind, subject, result, selected = "material") => Array.from(
    exports.filterHarnessObservations(value, kind, subject, result, selected), (item) => item.id,
  );
  assert.deepEqual(filter("all", "all", "all"), ["mixed", "unowned"]);
  assert.deepEqual(filter("field", "selected", "accepted"), ["mixed"]);
  assert.deepEqual(filter("field", "all", "unmatched"), ["mixed"]);
  assert.deepEqual(filter("field", "selected", "unmatched", "report"), ["mixed"]);
  assert.deepEqual(filter("field", "selected", "accepted", "report"), []);
  assert.deepEqual(filter("all", "unowned", "all"), ["unowned"]);
  assert.deepEqual(filter("entity", "all", "all"), []);
  assert.deepEqual(filter("all", "selected", "all", null), []);
  assert.deepEqual(filter("all", "all", "rejected"), []);
  value.properties[0].state = "rejected";
  assert.deepEqual(filter("all", "all", "accepted"), []);
  assert.deepEqual(filter("all", "all", "rejected"), ["mixed"]);
});

test("unrecorded cards remain unknown despite an entity having a current confirmed type", () => {
  const value = mixedObservationGraph();
  value.properties[0].card = null;
  value.observations = [observation({ candidate_subject_ids: ["material"], alignments: [
    { subject_id: "material", card: null, property_ids: ["p"], attempts: [] },
  ] })];
  const html = render(exports.HarnessObservationDetail, { graph: value, item: value.observations[0], onSelectEntity() {}, onSource() {} });
  assert.match(html, /主体当前类型：.*中间体/);
  assert.match(html, /本次对齐类型卡：未记录/);
  assert.match(html, /未记录发现参考卡/);
});

test("one original range can retain both accepted and pending derived properties", () => {
  const value = mixedObservationGraph();
  value.properties[0] = { ...value.properties[0], label: "范围下限", value: "3.8", value_component: "lower" };
  value.properties.push({ ...value.properties[0], id: "upper", label: "范围上限", value: "6.6", value_component: "upper", state: "unresolved", reason: "上限含义待核对" });
  value.observations[0].value = "3.8–6.6 kg";
  value.observations[0].alignments[1].property_ids.push("upper");
  const html = render(exports.HarnessObservationDetail, { graph: value, item: value.observations[0], onSelectEntity() {}, onSource() {} });
  assert.match(html, /完整原值：3.8–6.6 kg/);
  assert.match(html, /范围下限：3.8/);
  assert.match(html, /范围上限：6.6/);
  assert.match(html, /上限含义待核对/);
  const results = Array.from(exports.observationResults(value.observations[0], new Map(value.properties.map((p) => [p.id, p])), "material"));
  assert.deepEqual(results, ["accepted", "pending"]);
});

test("an incomplete observation response does not crash or hide other entity results", () => {
  const value = mixedObservationGraph();
  delete value.observations[0].alignments;
  const html = render(exports.HarnessCandidates, { graph: value, selectedEntity: "material", onSelectEntity() {}, onSource() {} });
  assert.match(html, /观察上下文暂不可用/);
  assert.match(html, /实体 2 · 属性 1/);
  assert.match(html, /中间体标识：ABC/);
});

test("a derived bound displays its original full range and unit", () => {
  const value = graph();
  value.properties = [{ id: "p", subject_id: entity.id, predicate_iri: "urn:bound", label: "负载下限", value: "3.8", source_value: "3.8–6.6 kg", source_unit: "kg", value_component: "lower", state: "candidate", reason: "待核对", evidence: [source] }];
  const html = render(exports.HarnessCandidates, { graph: value, selectedEntity: entity.id, onSelectEntity() {}, onSource() {} });
  assert.match(html, /原值：3.8–6.6 kg/);
  assert.match(html, /下限（kg）/);
});

test("a selected property value retains the full observation and its own exact source span", () => {
  const value = graph();
  const original = { ...source, text: "管理标识： AX-07", start: 0, end: 11 };
  const selected = { ...original, text: "AX-07", start: 6, end: 11 };
  value.properties = [{ id: "p-span", subject_id: entity.id, predicate_iri: "urn:code", label: "管理标识", value: "AX-07", source_value: original.text, source_unit: null, value_component: "span", value_evidence: [selected], state: "accepted", reason: "原文值与主体归属已核对", evidence: [original] }];
  const html = render(exports.HarnessCandidates, { graph: value, selectedEntity: entity.id, onSelectEntity() {}, onSource() {} });
  assert.match(html, /管理标识：AX-07/);
  assert.match(html, /原值：管理标识： AX-07/);
  assert.match(html, /取值位置/);
  assert.doesNotMatch(html, /上限|下限/);
});

test("Sigma relation graph exposes each relation state, negative polarity and conditions", () => {
  const value = graph();
  value.entities.push({ ...entity, id: "object:2", label: "样品乙" });
  value.relations = ["candidate", "unresolved", "rejected", "accepted"].map((state) => ({
    id: state, subject_id: entity.id, object_id: "object:2", predicate_iri: "urn:related", label: "关联", state,
    reason: "原文结果", evidence: [source], polarity: state === "accepted" ? "negative" : "positive", conditions: ["满足条件时"],
  }));
  const html = render(exports.HarnessRelationCanvas, { graph: value, subject: entity, revealGroups: true, onSelectEntity() {} });
  const edges = [...html.matchAll(/data-relation-id="relation:([^"]+)" data-state="([^"]+)" data-polarity="([^"]+)"/g)];
  assert.equal(edges.length, 4, "parallel relations remain distinct in the displayed graph");
  assert.deepEqual(new Set(edges.map((edge) => edge[2])), new Set(["candidate", "unresolved", "rejected", "accepted"]));
  assert.equal(edges.find((edge) => edge[1] === "accepted")[3], "negative");
  assert.match(html, /否定 · 附条件/);
});

test("ontology targets stay visible without fabricating instance nodes or edges", () => {
  const value = graph();
  value.targets = [{ id: "target:1", subject_id: entity.id, predicate_iri: "urn:contains", label: "包含", kind: "relation", range_labels: ["成分"], state: "pending" }];
  const html = render(exports.HarnessRelationCanvas, { graph: value, subject: entity, onSelectEntity() {} });
  assert.doesNotMatch(html, /data-relation-id=/);
  assert.doesNotMatch(html, /成分/);
  const details = render(exports.HarnessCandidates, { graph: value, selectedEntity: entity.id, onSelectEntity() {}, onSource() {} });
  assert.match(details, /当前本体卡目标（1）/);
  assert.match(details, /包含/);
  assert.match(details, /成分/);
  assert.match(details, /待发现/);
  assert.equal(value.entities.length, 1);
});

test("same labels with distinct entity IDs and roles remain separate selectable referents", () => {
  const value = graph();
  value.entities.push({ ...entity, id: "object:2", role: "原料" });
  const html = render(exports.HarnessCandidates, { graph: value, selectedEntity: entity.id, onSelectEntity() {}, onSource() {} });
  assert.match(html, /实体 2 · 属性 0/);
  assert.equal([...html.matchAll(/data-entity-id="object:[12]"/g)].length, 2);
});

test("calls and candidates do not appear as accepted facts or document completeness", () => {
  const html = render(exports.HarnessProgress, { graph: graph() });
  assert.match(html, /已完成调用/);
  assert.match(html, /已保存候选/);
  assert.match(html, /已采信事实/);
  assert.match(html, /全文范围尚未核对完成/);
  assert.match(render(exports.HarnessStageCosts, { graph: graph() }), /123\.4/);
  assert.doesNotMatch(html, /\d+%/);
});

test("missing provider token usage displays unknown while measured zero remains zero", () => {
  const value = graph();
  value.progress.stage_costs = [
    { stage: "discover", calls: 1, seconds: 4.2, input_tokens: null, output_tokens: null },
    { stage: "type_alignment", calls: 1, seconds: 2.1, input_tokens: 0, output_tokens: 23 },
  ];
  const html = render(exports.HarnessStageCosts, { graph: value });
  assert.equal([...html.matchAll(/<td[^>]*>未知<\/td>/g)].length, 2);
  assert.match(html, /<td[^>]*>0<\/td>/);
  assert.match(html, /<td[^>]*>23<\/td>/);
});

test("source excerpts use Unicode code point offsets, including supplementary-plane characters", () => {
  const text = "前😀样品甲后";
  const html = render(exports.HarnessSourcePreview, {
    source: { ...source, text, row: null, column: null },
    reference: { ...source, text: "样品甲", start: 2, end: 5 },
  });
  assert.match(html, /前😀<mark[^>]*>样品甲<\/mark>后/);
});

function hierarchyGraph() {
  const value = graph();
  value.entities = ["a", "root", "b", "c", "d", "detached", "negative", "conditional", "rejected"].map((id) => ({
    ...entity, id, label: id, role: id === "root" ? "document_root" : "sample", state: id === "d" ? "unresolved" : "accepted",
  }));
  const relation = (id, subject_id, object_id, extra = {}) => ({ id, subject_id, object_id,
    predicate_iri: "urn:relation", label: id, state: "accepted", polarity: "positive", conditions: [], evidence: [], ...extra });
  value.relations = [relation("ra", "root", "a"), relation("rb", "root", "b", { state: "candidate" }),
    relation("ac", "a", "c"), relation("bc", "b", "c"), relation("cd", "c", "d"), relation("ca", "c", "a"),
    relation("back", "d", "root"), relation("negative", "root", "negative", { polarity: "negative" }),
    relation("conditional", "root", "conditional", { conditions: ["条件成立时"] }),
    relation("rejected", "root", "rejected", { state: "rejected" }), relation("missing", "root", "missing")];
  return value;
}

test("hierarchy uses explicit roots and shortest paths, preserving cycles and multiple parents as references", () => {
  const value = hierarchyGraph();
  const snapshot = JSON.stringify(value);
  const hierarchy = helpers.buildHarnessHierarchy(value);
  assert.deepEqual(Array.from(hierarchy.roots, (item) => item.id), ["root"]);
  assert.deepEqual(Array.from(hierarchy.depth, ([id, depth]) => [id, depth]), [["root", 0], ["a", 1], ["b", 1], ["c", 2], ["d", 3]]);
  assert.deepEqual(Array.from(hierarchy.disconnected, (item) => item.id), ["detached", "negative", "conditional", "rejected"]);
  const { rows, truncated } = helpers.harnessTreeRows(hierarchy, new Set(value.entities.map((item) => item.id)), Infinity, new Map(), false);
  const entityRows = rows.filter((row) => row.kind === "entity");
  assert.equal(entityRows.length, 8);
  assert.equal(truncated, false);
  assert.deepEqual(Array.from(entityRows.filter((row) => row.reference), (row) => row.relation.id).sort(), ["back", "bc", "ca"]);
  assert.ok(entityRows.filter((row) => row.reference).every((row) => !row.expandable && !row.expanded));
  assert.equal(JSON.stringify(value), snapshot, "display computation must not mutate acceptance or graph data");
});

function predicateGroupGraph() {
  const value = hierarchyGraph();
  const relation = (id, subject_id, object_id, predicate_iri, label, state = "accepted") => ({
    id, subject_id, object_id, predicate_iri, label, state, polarity: "positive", conditions: [], evidence: [],
  });
  value.relations = [relation("01", "root", "a", "urn:uses", "使用物料"),
    relation("02", "root", "c", "urn:contains", "包含"),
    relation("03", "root", "b", "urn:uses", "使用物料", "unresolved"),
    relation("04", "root", "d", "urn:other-uses", "使用物料"),
    relation("05", "a", "detached", "urn:uses", "使用物料")];
  return value;
}

test("tree groups interleaved relations by parent and predicate IRI while preserving member states", () => {
  const value = predicateGroupGraph();
  const snapshot = JSON.stringify(value);
  const hierarchy = helpers.buildHarnessHierarchy(value);
  const { rows } = helpers.harnessTreeRows(hierarchy, new Set(value.entities.map((item) => item.id)), Infinity, new Map(), false);
  assert.deepEqual(Array.from(rows.filter((row) => row.kind === "predicate"), (row) => [row.subjectId, row.predicateIri]),
    [["root", "urn:uses"], ["a", "urn:uses"], ["root", "urn:contains"], ["root", "urn:other-uses"]]);
  assert.deepEqual(Array.from(rows.filter((row) => row.kind === "entity"), (row) => row.entity.id),
    ["root", "a", "detached", "b", "c", "d"]);
  assert.equal(rows.find((row) => row.kind === "entity" && row.entity.id === "b").relation.state, "unresolved");
  assert.equal(hierarchy.depth.get("b"), 1, "predicate headings do not add graph hops");
  const html = render(exports.HarnessCandidates, { graph: value, selectedEntity: "root", onSelectEntity() {}, onSource() {} });
  const tree = html.match(/<ul[^>]*aria-label="分层实体列表"[^>]*>(.*?)<\/ul>/s)[1];
  assert.equal((tree.match(/data-predicate-iri="urn:uses" data-subject-id="root"/g) ?? []).length, 1);
  assert.match(tree, /data-entity-id="a"/);
  assert.match(tree, /data-entity-id="b"/);
  assert.match(tree, /关系未决/);
  assert.match(tree, /关系已采信/);
  assert.equal(JSON.stringify(value), snapshot);
});

test("predicate groups collapse independently and filtering reveals only matching member paths", () => {
  const value = predicateGroupGraph();
  const hierarchy = helpers.buildHarnessHierarchy(value);
  const visible = new Set(value.entities.map((item) => item.id));
  const allRows = helpers.harnessTreeRows(hierarchy, visible, Infinity, new Map(), false).rows;
  const group = allRows.find((row) => row.kind === "predicate" && row.subjectId === "root" && row.predicateIri === "urn:uses");
  const expanded = new Map([[group.id, false]]);
  const collapsed = helpers.harnessTreeRows(hierarchy, visible, Infinity, expanded, false).rows;
  assert.deepEqual(Array.from(collapsed.filter((row) => row.kind === "entity"), (row) => row.entity.id), ["root", "c", "d"]);
  assert.equal(collapsed.find((row) => row.id === group.id).expanded, false);
  const filtered = helpers.filterHarnessEntities(value, hierarchy, "detached", "all");
  const revealed = helpers.harnessTreeRows(hierarchy, filtered.visible, 0, expanded, true).rows;
  assert.deepEqual(Array.from(revealed.filter((row) => row.kind === "entity"), (row) => row.entity.id), ["root", "a", "detached"]);
  assert.ok(revealed.filter((row) => row.kind === "predicate").every((row) => row.expanded));
  const limited = helpers.harnessTreeRows(hierarchy, visible, Infinity, new Map(), false, 2);
  assert.equal(limited.rows.length, 1, "pagination does not leave an expanded predicate heading without a member");
  assert.equal(limited.truncated, true);
});

test("relation graph opens every predicate group by default and keeps distinct relations", () => {
  const value = predicateGroupGraph();
  const expanded = render(exports.HarnessRelationCanvas, { graph: value, subject: value.entities.find((item) => item.id === "root"), onSelectEntity() {} });
  assert.equal((expanded.match(/data-predicate-iri="urn:uses" data-subject-id="root"/g) ?? []).length, 1);
  assert.equal((expanded.match(/data-predicate-iri="urn:other-uses" data-subject-id="root"/g) ?? []).length, 1,
    "same label with a different predicate IRI remains a separate group");
  assert.match(expanded, /收起使用物料（2）/);
  assert.match(expanded, /data-relation-id="relation:01"[^>]+>/);
  assert.match(expanded, /data-relation-id="relation:03" data-state="unresolved"/);
  assert.match(expanded, /data-entity-id="a"/);
  assert.match(expanded, /data-entity-id="b"/);
  assert.match(expanded, /data-entity-id="detached"/);
  assert.equal((expanded.match(/data-predicate-iri="urn:uses"/g) ?? []).length, 2,
    "nested group remains separate from the parent group");
});

test("rooted circular layout centers the document root and separates connected and detached rings", () => {
  const nodes = [
    { id: "root", x: 5, y: 5 }, { id: "group", x: 6, y: 5 },
    { id: "child", x: 7, y: 5 }, { id: "detached", x: -2, y: -2 },
  ];
  const positions = rootedCircleLayout(nodes, [
    { source: "root", target: "group" }, { source: "group", target: "child" },
  ], "root");
  const distance = (id) => Math.hypot(positions.get(id).x, positions.get(id).y);
  assert.equal(positions.get("root").x, 0);
  assert.equal(positions.get("root").y, 0);
  assert.ok(distance("group") < distance("child"));
  assert.ok(distance("child") < distance("detached"));
  assert.equal(positions.size, nodes.length);
  const withoutRoot = rootedCircleLayout(nodes.slice(1), [], "missing-root");
  assert.equal(withoutRoot.size, nodes.length - 1);
  assert.ok([...withoutRoot.values()].every(({ x, y }) => Math.hypot(x, y) > 0));
});

test("root selection never guesses the first entity and root attributes remain accessible", () => {
  const value = hierarchyGraph();
  value.properties = [{ id: "root-p", subject_id: "root", label: "文档编号", predicate_iri: null, card: null, predicate: null,
    value: "ROOT-1", source_value: "ROOT-1", source_unit: null, value_component: "whole", value_evidence: [],
    state: "candidate", reason: "待核对", evidence: [] }];
  const html = render(exports.HarnessCandidates, { graph: value, selectedEntity: null, onSelectEntity() {}, onSource() {} });
  assert.match(html, /data-entity-id="root" aria-pressed="true"/);
  assert.match(html, /文档编号：ROOT-1/);
  value.entities = value.entities.filter((item) => item.id !== "root");
  const noRoot = render(exports.HarnessCandidates, { graph: value, selectedEntity: null, onSelectEntity() {}, onSource() {} });
  assert.match(noRoot, /尚无文档根/);
  assert.doesNotMatch(noRoot, /data-entity-id="[^"]+" aria-pressed="true"/);
});

test("property search retains ancestors without making unmatched siblings visible", () => {
  const value = hierarchyGraph();
  value.properties = [{ subject_id: "d", label: "编号", predicate_iri: "urn:identifier", value: "AX-07", source_value: "编号 AX-07" }];
  const hierarchy = helpers.buildHarnessHierarchy(value);
  const filtered = helpers.filterHarnessEntities(value, hierarchy, "ax-07", "unresolved");
  assert.deepEqual(Array.from(filtered.matched), ["d"]);
  assert.deepEqual(Array.from(filtered.visible).sort(), ["a", "c", "d", "root"]);
  const rows = helpers.harnessTreeRows(hierarchy, filtered.visible, 0, new Map(), true).rows;
  assert.ok(rows.some((row) => row.kind === "entity" && row.entity.id === "d"));
  assert.ok(!rows.some((row) => row.kind === "entity" && row.entity.id === "b"));
  const collapsed = helpers.harnessTreeRows(hierarchy, filtered.visible, 0, new Map(), false).rows;
  assert.deepEqual(Array.from(collapsed, (row) => row.entity.id), ["root"]);
  const expanded = helpers.harnessTreeRows(hierarchy, filtered.visible, 0, new Map([["root", true]]), false).rows;
  assert.deepEqual(Array.from(expanded.filter((row) => row.kind === "entity"), (row) => row.entity.id), ["root", "a"]);
});

test("bounded hierarchy rendering reports truncation and supports multiple document roots", () => {
  const value = hierarchyGraph();
  value.entities.push({ ...entity, id: "other-root", role: "document_root" });
  const hierarchy = helpers.buildHarnessHierarchy(value);
  assert.equal(hierarchy.depth.get("other-root"), 0);
  const limited = helpers.harnessTreeRows(hierarchy, new Set(value.entities.map((item) => item.id)), Infinity, new Map(), false, 3);
  assert.equal(limited.rows.length, 3);
  assert.equal(limited.truncated, true);
});

test("observation search covers source, discovery cards, predicates and alignment reasons with ownership isolation", () => {
  const value = mixedObservationGraph();
  for (const query of ["中间体", "urn:schema:Intermediate", "https://example.test/material/identifier", "报告属性菜单无物料标识", "样品甲"]) {
    const matches = exports.filterHarnessObservations(value, "field", "entity:material", "accepted", null, query);
    assert.deepEqual(Array.from(matches, (item) => item.id), ["mixed"]);
  }
  value.observations[0].alignments[0].property_ids.push("p");
  assert.deepEqual(Array.from(exports.filterHarnessObservations(value, "field", "entity:report", "accepted", null)), []);
  const html = render(exports.HarnessObservationDetail, { graph: value, item: value.observations[0], onSelectEntity() {}, onSource() {} });
  assert.match(html.slice(html.indexOf("属性对齐主体：报告甲"), html.indexOf("属性对齐主体：物料甲")), /关联属性不可用：p/);
});

test("cost totals never replace partial or absent measurements with zero", () => {
  const costs = [
    { stage: "discover", calls: 1, seconds: 10, input_tokens: null, output_tokens: 0 },
    { stage: "type_alignment", calls: 2, seconds: 30, input_tokens: 0, output_tokens: 20 },
  ];
  const total = helpers.harnessCosts(costs);
  assert.equal(total.calls, 3);
  assert.equal(total.seconds, 40);
  assert.equal(total.input, null);
  assert.equal(total.output, 20);
  assert.equal(total.tokens, null);
  assert.equal(helpers.harnessCosts([]).tokens, null);
  costs[0].input_tokens = 0;
  assert.equal(helpers.harnessCosts(costs).input, 0);
  assert.equal(helpers.harnessCosts(costs).tokens, 20);
  const value = graph();
  value.progress.stage_costs = costs;
  const html = render(exports.HarnessStageCosts, { graph: value });
  assert.match(html, /25%/);
  assert.match(html, /75%/);
  assert.doesNotMatch(render(exports.HarnessProgress, { graph: value }), /\d+%/);
});

test("new graph and source clients perform authenticated GETs without starting model work", async () => {
  const requests = [];
  const apiExports = {};
  const apiSource = readFileSync(new URL("../src/lib/api.ts", import.meta.url), "utf8");
  vm.runInNewContext(ts.transpileModule(apiSource, { compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022,
  } }).outputText, { exports: apiExports, process: { env: {} }, Headers,
    fetch: async (url, options) => { requests.push({ url, options }); return { ok: true, status: 200, text: async () => "{}" }; },
  });
  const controller = new AbortController();
  await apiExports.getDocumentHarnessGraph("run", controller.signal);
  await apiExports.getDocumentHarnessSource("run", "source:one", controller.signal);
  assert.deepEqual(requests.map((item) => item.url), [
    "/api/document-analysis/runs/run/harness-graph", "/api/document-analysis/runs/run/harness-source/source%3Aone",
  ]);
  for (const { options } of requests) {
    assert.equal(options.method ?? "GET", "GET");
    assert.equal(options.signal, controller.signal);
    assert.equal(options.headers["X-Role"], "senior_analyst");
    assert.equal(options.cache, "no-store");
  }
});

test("the independent view does not import old graph, evidence adapters or old graph endpoints", () => {
  const parsed = ts.createSourceFile("source-harness-panel.tsx", componentSource, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const imported = parsed.statements.filter(ts.isImportDeclaration);
  for (const declaration of imported) {
    assert.doesNotMatch(declaration.moduleSpecifier.text, /target-graph|document-graph|word-viewer/);
    assert.doesNotMatch(declaration.importClause?.getText(parsed) ?? "", /getDocumentAnalysisTargetGraph|getDocumentAnalysisSourceSelection|DocumentAnalysisEntityRef|EvidenceAnchor|GraphSnapshot/);
  }
});


test("missing coreference data does not prevent opening or searching entity details", () => {
  for (const missing of ["all", "selected-mentions", "other-mentions", "coreferences"]) {
    const value = graph();
    value.entities = [{ ...entity }, { ...entity, id: "other", mentions: [{ ...mention, id: "other" }] }];
    value.properties = [{ id: "field:1", subject_id: entity.id, predicate_iri: null, label: "样品编号", value: "AX-07", source_value: "AX-07", source_unit: null, value_component: "whole", state: "unresolved", reason: "主体归属待核对", evidence: [source] }];
    if (missing === "all") for (const item of value.entities) delete item.mentions;
    if (missing === "selected-mentions") delete value.entities[0].mentions;
    if (missing === "other-mentions") delete value.entities[1].mentions;
    if (missing === "all" || missing === "coreferences") delete value.coreferences;

    const html = render(exports.HarnessCandidates, { graph: value, selectedEntity: entity.id, onSelectEntity() {}, onSource() {} });
    assert.match(html, /原文提及与共指数据暂不可用/, missing);
    assert.match(html, /样品编号/, missing);
    assert.match(html, /AX-07/, missing);
    assert.match(html, /查看原文/, missing);
    assert.doesNotMatch(html, /暂无共指判定|尚未与其他提及归并|0 处提及/, missing);
    const hierarchy = helpers.buildHarnessHierarchy(value);
    assert.ok(helpers.filterHarnessEntities(value, hierarchy, "样品甲", "all").matched.has(entity.id), missing);
    assert.ok(helpers.filterHarnessEntities(value, hierarchy, "AX-07", "all").matched.has(entity.id), missing);
  }
});

test("merged entity retains aliases, same/different/unresolved reasons and source links", () => {
  const value = graph();
  const later = { ...mention, id: "later", label: "甲号试样", evidence: [{ ...source, source_id: "source:later", section_id: "s2" }] };
  value.entities = [{ ...entity, mentions: [mention, later] }, { ...entity, id: "other", label: "另一个样品", mentions: [{ ...mention, id: "other" }] }];
  value.coreferences = [
    { id: "same", left_mention_id: mention.id, right_mention_id: "later", verdict: "same", basis: "explicit_alias", reason: "原文明确简称", evidence: [source], proof: [source], applied: true },
    { id: "different", left_mention_id: mention.id, right_mention_id: "other", verdict: "different", basis: "distinct", reason: "原文属于不同批次", evidence: [source], proof: [source], applied: false },
    { id: "unknown", left_mention_id: "later", right_mention_id: "other", verdict: "unresolved", basis: "insufficient", reason: "仅同名，证据不足", evidence: [source], proof: [], applied: false },
  ];
  const html = render(exports.HarnessCandidates, { graph: value, selectedEntity: entity.id, onSelectEntity() {}, onSource() {} });
  assert.match(html, /2 处提及 · 3 项判定/);
  for (const text of ["甲号试样", "已归并到同一文档内实体", "不同批次", "仅同名，证据不足", "同一", "不同", "未决"]) assert.ok(html.includes(text), text);
  const hierarchy = helpers.buildHarnessHierarchy(value);
  assert.ok(helpers.filterHarnessEntities(value, hierarchy, "甲号试样", "all").matched.has(entity.id));
  value.coreferences[0].applied = false;
  const conflict = render(exports.HarnessCandidates, { graph: value, selectedEntity: entity.id, onSelectEntity() {}, onSource() {} });
  assert.match(conflict, /组内存在未决、缺失或冲突判定/);
});
