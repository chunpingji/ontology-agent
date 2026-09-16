import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { webcrypto } from "node:crypto";
import test from "node:test";
import vm from "node:vm";
import ts from "typescript";

function load(path, globals = {}) {
  const source = readFileSync(new URL(path, import.meta.url), "utf8");
  const compiled = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const exports = {};
  vm.runInNewContext(compiled, { exports, process: { env: {} }, Headers,
    crypto: webcrypto, structuredClone, require: () => ({}), ...globals });
  return exports;
}
const reporting = load("../src/lib/reporting-v2.ts");
const previewModel = load("../src/lib/report-preview.ts");

test("coverage preserves repeated requirements, inactive branches and unknown initial state", () => {
  assert.equal(previewModel.summarizeCoverage(), null);
  const requirement = { requirement_id: "shared", input_id: "input", origin_refs: ["unit-a", "unit-b"], activation: "active", satisfied: true };
  const snapshot = { material_status: "incomplete", coverage: [
    { ...requirement, execution_scope_id: "scope-a" },
    { ...requirement, execution_scope_id: "scope-b", satisfied: false },
    { ...requirement, execution_scope_id: "scope-c", activation: "inactive", satisfied: true },
  ] };
  const summary = previewModel.summarizeCoverage(snapshot);
  assert.equal(summary.total, 2); // Shared output uses do not duplicate requirements; repeated scopes do.
  assert.equal(summary.missing, 1);
  assert.equal(summary.inactive, 1);
  assert.equal(summary.percent, 50);
  assert.equal(previewModel.summarizeCoverage({ material_status: "invalid", coverage: [] }).percent, 0);
});

test("output status respects ancestor requirements and never treats missing results as success", () => {
  const snapshot = { coverage: [{ origin_refs: ["section"], activation: "active", satisfied: false }] };
  const completed = { payload: { output_id: "unit", execution_status: "completed", inactive: false } };
  assert.equal(previewModel.outputCoverage("unit", ["section", "group"], snapshot, [completed]).state, "missing");
  assert.equal(previewModel.outputCoverage("unit", [], { coverage: [] }, []).state, "pending");
  assert.equal(previewModel.outputCoverage("unit", [], undefined, [completed]).state, "pending");
  assert.equal(previewModel.outputCoverage("unit", [], { coverage: [] }, [completed]).state, "ready");
  const failed = { payload: { ...completed.payload, execution_status: "failed" } };
  assert.equal(previewModel.outputCoverage("unit", [], { coverage: [] }, [completed, failed]).state, "failed");
  const inactive = { payload: { ...completed.payload, inactive: true } };
  assert.equal(previewModel.outputCoverage("unit", [], { coverage: [] }, [inactive]).state, "inactive");
});

test("input coverage separates empty output configuration without hiding real material gaps", () => {
  const ready = { requirement_id: "input-ok", input_id: "input", origin_refs: ["unit"],
    activation: "active", satisfied: true, issue_refs: [] };
  const placeholder = { requirement_id: "empty-slot", input_id: "empty-unit", origin_refs: ["empty-unit"],
    activation: "active", satisfied: false, issue_refs: ["empty-slot"] };
  const snapshot = {
    material_status: "incomplete",
    source_bundle: { template: { definitions: { inputs: { input: {} } } } },
    coverage: [ready, placeholder],
    blocking_issues: [{ issue_id: "empty-slot", code: "SLOT_CONFIGURATION_MISSING" }],
  };
  const original = structuredClone(snapshot);
  const summary = previewModel.summarizeCoverage(snapshot);
  assert.equal(summary.percent, 100);
  assert.equal(summary.total, 1);
  assert.equal(summary.configurationMissing, 1);
  assert.equal(previewModel.outputCoverage("empty-unit", [], snapshot).state, "missing");
  assert.deepEqual(snapshot, original); // Frozen material diagnostics stay intact.

  const missing = { ...ready, requirement_id: "field-missing", satisfied: false, issue_refs: ["missing"] };
  const incomplete = previewModel.summarizeCoverage({ ...snapshot, coverage: [ready, missing, placeholder] });
  assert.equal(incomplete.percent, 50);
  assert.equal(incomplete.missing, 1);
  assert.equal(incomplete.configurationMissing, 1);
  assert.equal(previewModel.summarizeCoverage({ ...snapshot, coverage: [placeholder] }).percent, 0);

  // Unknown problems must remain in the input denominator, including old snapshots.
  assert.equal(previewModel.summarizeCoverage({ ...snapshot, blocking_issues: [] }).percent, 50);
  assert.equal(previewModel.summarizeCoverage({ ...snapshot, coverage: [ready, { ...placeholder,
    activation: "inactive" }] }).configurationMissing, 0);
});

test("moving output preserves input references and source origin", () => {
  const template = reporting.emptyTemplate();
  const origin = { document_hash: "doc", evidence_id: "cell-1" };
  const unit = { output_id: "u", title: "设备", inputs: [{ input_ref: "i", alias: "equipment" }],
    bindings: [{ binding_ref: "b" }], origin,
    render: { kind: "narrative", nodes: [{ kind: "input_ref", input_id: "i" }] } };
  template.sections = [{ section_id: "s", title: "", groups: [
    { group_id: "a", title: "", units: [unit] }, { group_id: "b", title: "", units: [] },
  ] }];
  const moved = reporting.moveUnit(template, "u", "b", 0);
  assert.equal(template.sections[0].groups[0].units.length, 1);
  assert.deepEqual(moved.sections[0].groups[1].units[0], unit);
  assert.equal(reporting.consumersOf(moved, "i")[0].output_id, "u");
});

test("business title defaults preserve manual titles, structure and all writing bindings", () => {
  const template = structuredClone(reporting.emptyTemplate());
  template.sections = [{ section_id: "s", title: "upload", narrative: { enabled: true, instructions: "已写Prompt" }, groups: [
    { group_id: "description", title: "表格", units: [{ output_id: "u", title: "产品", inputs: [{ input_ref: "i" }],
      bindings: [{ binding_ref: "b" }], origin: { evidence_id: "e" }, render: { kind: "narrative", mode: "assisted", prompt: { instructions: "保留行文" } } }] },
    { group_id: "manual", title: "人工编写标题", units: [], groups: [{ group_id: "workshop", title: "表格", units: [] }] },
  ] }];
  const original = structuredClone(template);
  const titles = { description: "风险评估对象基本描述", manual: "不得覆盖", workshop: "642 车间设备表", unrelated: "不新增分组" };
  const normalized = reporting.applyStructureTitles(template, titles);
  assert.deepEqual(template, original);
  assert.equal(normalized.sections[0].groups[0].title, titles.description);
  assert.equal(normalized.sections[0].groups[1].title, "人工编写标题");
  assert.equal(normalized.sections[0].groups[1].groups[0].title, titles.workshop);
  assert.deepEqual(normalized.sections[0].groups[0].units, original.sections[0].groups[0].units);
  assert.deepEqual(normalized.sections[0].narrative, original.sections[0].narrative);
  assert.deepEqual(reporting.applyStructureTitles(normalized, titles), normalized);
  assert.deepEqual(reporting.applyStructureTitles(template), template);
});

test("new output templates do not contain business defaults", () => {
  const template = reporting.emptyTemplate();
  assert.equal(template.schema_version, 2);
  assert.equal(template.doc_no, "");
  assert.equal(Object.keys(template.definitions.inputs).length, 0);
  assert.equal(reporting.isTemplateV2({ revision: "v2" }), false);
});

test("sample structure creates V2 authoring placeholders with exact origins and no sample facts", () => {
  const template = reporting.emptyTemplate();
  template.source_slots = [{ source_slot_id: "source", kind: "document", class_iri: "urn:CMC" }];
  const origin = { document_hash: "sample", evidence_id: "field", label_anchor: { evidence_id: "field" } };
  const skeleton = [{ id: "section", title: "产品信息", origin, groups: [{ id: "group", title: "字段", origin,
    candidates: [{ id: "unit", label: "名称", semantic_label: "产品名称", origin, evidence_span: "名称：样品A" }] }] }];
  const result = reporting.materializeTemplateStructure(template, skeleton);
  const unit = reporting.unitsIn(result)[0];
  assert.equal(template.sections.length, 0);
  assert.equal(result.sections[0].section_id, "section");
  assert.equal(result.sections[0].groups[0].group_id, "group");
  assert.equal(unit.output_id, "unit");
  assert.equal(unit.title, "产品名称");
  assert.deepEqual(JSON.parse(JSON.stringify(unit.origin)), origin);
  assert.equal(unit.inputs.length, 0);
  assert.equal(unit.bindings.length, 0);
  assert.equal(unit.render.nodes.length, 0);
  assert.deepEqual(result.definitions, template.definitions);
  assert.deepEqual(result.source_slots, template.source_slots);
  assert(!JSON.stringify(result).includes("样品A"));
  assert.equal(reporting.materializeTemplateStructure(result, skeleton), result);
});

test("late or empty structure results preserve manual draft changes", () => {
  const template = reporting.emptyTemplate();
  assert.equal(reporting.materializeTemplateStructure(template, []), template);
  template.sections.push({ section_id: "manual", title: "人工编写", groups: [] });
  const before = JSON.stringify(template);
  assert.equal(reporting.materializeTemplateStructure(template, [{ id: "late", title: "迟到分析", groups: [] }]), template);
  assert.equal(JSON.stringify(template), before);
});

test("custom headers preserve authenticated identity and cancellation", async () => {
  let options;
  const controller = new AbortController();
  const api = load("../src/lib/api.ts", {
    window: { localStorage: { getItem: (key) => key === "slpra.token" ? "signed-token" : null } },
    fetch: async (_path, request) => { options = request; return { ok: true, status: 200, text: async () => "{}" }; },
  });
  await api.fetchAPI("/api/report-runs/test", { headers: new Headers({ Accept: "application/json" }), signal: controller.signal });
  assert.equal(options.headers.Authorization, "Bearer signed-token");
  assert.equal(options.headers["X-User"], "analyst");
  assert.equal(options.headers.accept, "application/json");
  assert.equal(options.signal, controller.signal);
});

test("shared inputs maintain all transitive binding dependencies without changing IDs", () => {
  const template = reporting.emptyTemplate();
  template.definitions.bindings = {
    source: { binding_id: "source", kind: "facts", contract_ref: {}, scope: {} },
    view: { binding_id: "view", kind: "derived", contract_ref: "view-contract", operation: { source: { input_id: "upstream" } } },
  };
  template.definitions.inputs = {
    upstream: { input_id: "upstream", name: "upstream", binding_ref: "source", projection: { kind: "identity" } },
    result: { input_id: "result", name: "result", binding_ref: "view", projection: { kind: "identity" } },
  };
  const unit = { output_id: "output", title: "", inputs: [{ input_ref: "result", alias: "result" }], bindings: [], render: { kind: "narrative" } };
  template.sections = [{ section_id: "s", title: "", groups: [{ group_id: "g", title: "", units: [unit] }] }];
  reporting.syncBindingDependencies(template);
  assert.equal(unit.inputs[0].input_ref, "result");
  assert.deepEqual(Array.from(unit.bindings, (b) => b.binding_ref).sort(), ["source", "view"]);
});

test("ontology menus inherit exact properties and terminate cycles", () => {
  const classes = {
    "urn:A": { parents: ["urn:B"], properties: [{ iri: "urn:prop" }] },
    "urn:B": { parents: ["urn:A"], relationships: [{ iri: "urn:relation", range: ["urn:C"] }] },
    "urn:Unrelated": { properties: [{ iri: "urn:wrong" }] },
  };
  const menu = reporting.ontologyMenu(classes, "urn:A");
  assert.equal(menu.properties.length, 1);
  assert.equal(menu.properties[0].iri, "urn:prop");
  assert.equal(menu.relationships[0].iri, "urn:relation");
});

test("source type changes preserve path and explicit narrowing without mutating the old revision", () => {
  const template = reporting.emptyTemplate();
  template.source_slots = [{ source_slot_id: "doc", kind: "document", class_iri: "urn:Report" }];
  template.definitions.bindings.b = { binding_id: "b", kind: "facts", contract_ref: {
    root_class_iri: "urn:Report", result_class_iri: "urn:SpecificEquipment", release_ref: "old" },
    scope: { source_slot: "doc", predicate_path: [{ predicate_iri: "urn:uses" }] } };
  const changed = reporting.changeSourceType(template, "doc", "urn:OtherReport");
  assert.equal(template.source_slots[0].class_iri, "urn:Report");
  assert.equal(changed.source_slots[0].class_iri, "urn:OtherReport");
  assert.equal(changed.definitions.bindings.b.contract_ref.root_class_iri, "auto:class");
  assert.equal(changed.definitions.bindings.b.contract_ref.result_class_iri, "urn:SpecificEquipment");
  assert.equal(changed.definitions.bindings.b.scope.predicate_path[0].predicate_iri, "urn:uses");
});

test("automatic binding display resolves unique endpoints and leaves unions unresolved", () => {
  const template = reporting.emptyTemplate();
  template.source_slots = [{ source_slot_id: "doc", kind: "document", class_iri: "urn:Report" }];
  const binding = { binding_id: "b", kind: "facts", contract_ref: { root_class_iri: "auto:class", result_class_iri: "auto:class" },
    scope: { source_slot: "doc", predicate_path: [{ predicate_iri: "urn:uses" }] } };
  const classes = { "urn:Report": { relationships: [{ iri: "urn:uses", range: ["urn:Equipment"] }] } };
  assert.equal(reporting.resolveModelBinding(binding, template, classes).contract_ref.result_class_iri, "urn:Equipment");
  classes["urn:Report"].relationships[0].range.push("urn:Material");
  assert.equal(reporting.resolveModelBinding(binding, template, classes).contract_ref.result_class_iri, "auto:class");
});

test("automatic rules preserve historical check IDs and do not require a template toggle", () => {
  const template = reporting.emptyTemplate();
  template.source_slots = [{ source_slot_id: "doc", kind: "document", class_iri: "urn:CMC" }];
  const rule = { contract_id: "method", kind: "calculation", definition: { root_class_iri: "urn:CMC" } };
  assert.equal(reporting.automaticChecks(template, [rule]).length, 1);
  template.calculation_checks = [{ check_id: "historical", source_slot: "doc", contract_ref: "method" }];
  assert.equal(reporting.automaticChecks(template, [rule])[0].check_id, "historical");
  assert.equal(reporting.automaticChecks(template, [rule]).length, 1);
});

test("semantic suggestions preserve concurrent author edits and source identity", () => {
  const baseline = reporting.emptyTemplate();
  const unit = { output_id: "u", title: "设备", inputs: [], bindings: [], render: { kind: "narrative", mode: "composed", nodes: [] } };
  baseline.sections = [{ section_id: "s", groups: [{ group_id: "g", units: [unit] }] }];
  const patch = { output_id: "u", unit: { ...unit, title: "建议设备表" }, bindings: {}, inputs: {}, record_sources: {} };
  const edited = structuredClone(baseline); edited.sections[0].groups[0].units[0].title = "人工标题";
  assert.equal(reporting.unitsIn(reporting.applySemanticPatches(edited, baseline, [patch]))[0].title, "人工标题");
  const next = reporting.applySemanticPatches(baseline, baseline, [patch]);
  assert.equal(reporting.unitsIn(next)[0].title, "建议设备表");
  assert.equal(reporting.unitsIn(baseline)[0].title, "设备");
  const changedSource = { ...baseline, source_slots: [{ source_slot_id: "new", class_iri: "urn:Other" }] };
  assert.equal(reporting.applySemanticPatches(changedSource, baseline, [patch]), changedSource);
  const changedRevision = { ...baseline, template_revision_id: "other" };
  assert.equal(reporting.applySemanticPatches(changedRevision, baseline, [patch]), changedRevision);
});

test("table coalescing joins only untouched headers from the same physical table", () => {
  const baseline = reporting.emptyTemplate();
  const units = ["编号", "名称", "规格"].map((title, column_index) => ({ output_id: String(column_index), title,
    inputs: [], bindings: [], render: { kind: "narrative", mode: "composed", nodes: [] },
    origin: { label_anchor: { table_path: ["table-a"], row_index: 0, column_index }, evidence_id: `e${column_index}` } }));
  baseline.sections = [{ section_id: "s", groups: [{ group_id: "g", units }] }];
  const merged = reporting.unitsIn(reporting.coalesceUnconfiguredTables(baseline));
  assert.equal(merged.length, 1); assert.equal(merged[0].origin.slot_columns.length, 3);
  assert.equal(reporting.unitsIn(baseline).length, 3);
  const otherTable = structuredClone(baseline); otherTable.sections[0].groups[0].units[2].origin.label_anchor.table_path = ["table-b"];
  assert.equal(reporting.unitsIn(reporting.coalesceUnconfiguredTables(otherTable)).length, 3);
  const edited = structuredClone(baseline); edited.sections[0].groups[0].units[1].render.nodes = [{ kind: "text", text: "人工说明" }];
  assert.equal(reporting.unitsIn(reporting.coalesceUnconfiguredTables(edited)).length, 3);
});

test("table footer stays separate when empty headers are combined", () => {
  const value = reporting.emptyTemplate();
  const headers = ["编号", "名称", "规格"].map((title, col) => ({ output_id: String(col), title, inputs: [], bindings: [],
    origin: { label_anchor: { table_path: ["outer", "nested"], row_index: 0, column_index: col } }, render: { kind: "narrative", mode: "composed", nodes: [] } }));
  const footer = { ...structuredClone(headers[0]), output_id: "note", title: "备注" };
  footer.origin.label_anchor.row_index = 19;
  value.sections = [{ section_id: "s", groups: [{ group_id: "g", units: [...headers, footer] }] }];
  const result = reporting.unitsIn(reporting.coalesceUnconfiguredTables(value));
  assert.equal(result.length, 2); assert.equal(result[1].output_id, "note");
  assert.equal(result[0].origin.slot_columns.length, 3);
});
