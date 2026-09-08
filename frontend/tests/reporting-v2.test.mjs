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

test("new output templates do not contain business defaults", () => {
  const template = reporting.emptyTemplate();
  assert.equal(template.schema_version, 2);
  assert.equal(template.doc_no, "");
  assert.equal(Object.keys(template.definitions.inputs).length, 0);
  assert.equal(reporting.isTemplateV2({ revision: "v2" }), false);
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
