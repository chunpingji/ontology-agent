import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import vm from "node:vm";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";
import { createTree } from "@headless-tree/core";

const require = createRequire(import.meta.url);
function load(relative, overrides = {}, globals = {}) {
  const exports = {};
  const source = readFileSync(new URL(relative, import.meta.url), "utf8");
  const compiled = ts.transpileModule(source, { compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX,
  } }).outputText;
  vm.runInNewContext(compiled, { exports, setTimeout, clearTimeout, ...globals,
    require(name) {
      if (name in overrides) return overrides[name];
      // SSR has no mount effects. Mount the real Headless Tree engine explicitly
      // to exercise visible-row rendering; browser tests cover the real React hook.
      if (name === "@headless-tree/react") return { useTree(config) {
        const tree = createTree(config);
        tree.setMounted(true);
        tree.rebuildTree();
        return tree;
      } };
      if (name === "@/components/ui/tree") return load("../src/components/ui/tree.tsx");
      if (name === "@/components/ui/use-document-tree") return load("../src/components/ui/use-document-tree.ts");
      if (name === "@/lib/utils") return load("../src/lib/utils.ts");
      if (name === "@/lib/api") return load("../src/lib/api.ts", {}, { process: { env: {} } });
      if (name.startsWith("@/components/ui/")) return new Proxy({}, {
        get: () => ({ children }) => React.createElement("div", null, children),
      });
      if (name.startsWith("@/") || name.startsWith("./")) return {};
      return require(name);
    },
  });
  return exports;
}
const graphModule = load("../src/components/analysis/template-document-graph-panel.tsx");
const hookModule = load("../src/components/analysis/use-template-document-run.ts");

function fakeClock() {
  let now = 0, sequence = 0;
  const timers = new Map();
  return {
    setTimeout(callback, delay) { const id = ++sequence; timers.set(id, { callback, due: now + delay }); return id; },
    clearTimeout(id) { timers.delete(id); },
    async advance(milliseconds) {
      const until = now + milliseconds;
      while (true) {
        const next = [...timers.entries()].filter(([, timer]) => timer.due <= until)
          .sort((a, b) => a[1].due - b[1].due)[0];
        if (!next) break;
        now = next[1].due;
        timers.delete(next[0]);
        next[1].callback();
        await new Promise(setImmediate);
      }
      now = until;
    },
    now: () => now,
    pending: () => timers.size,
  };
}

test("healthy SSE merges bursts without polling or starving continuous events", async () => {
  const clock = fakeClock();
  const hook = load("../src/components/analysis/use-template-document-run.ts", {}, clock);
  let reads = 0;
  const scheduler = hook.createRunRefreshScheduler(async () => { reads++; });
  scheduler.connection(true);
  await clock.advance(500);
  assert.equal(reads, 1, "connection synchronizes the latest status once");
  await clock.advance(30000);
  assert.equal(reads, 1, "healthy idle SSE has no parallel poll");
  for (let i = 0; i < 20; i++) {
    scheduler.event();
    await clock.advance(100);
  }
  assert.equal(reads, 5, "continuous traffic still produces one read per 500 ms");
  assert.equal(clock.pending(), 0);
  scheduler.close();
});

test("disconnection backs off, reconnection stops the fallback, and hiding cancels timers", async () => {
  const clock = fakeClock();
  const hook = load("../src/components/analysis/use-template-document-run.ts", {}, clock);
  const reads = [];
  const scheduler = hook.createRunRefreshScheduler(async () => { reads.push(clock.now()); });
  await clock.advance(37500);
  assert.deepEqual(reads, [2500, 7500, 17500, 37500]);
  scheduler.connection(true);
  await clock.advance(500);
  assert.equal(reads.length, 5);
  await clock.advance(60000);
  assert.equal(reads.length, 5);
  scheduler.connection(false);
  await clock.advance(2499);
  assert.equal(reads.length, 5);
  scheduler.close();
  await clock.advance(60000);
  scheduler.event();
  assert.equal(reads.length, 5);
  assert.equal(clock.pending(), 0);
});

test("events received during a pending read cause one follow-up and no overlapping request", async () => {
  const clock = fakeClock();
  const hook = load("../src/components/analysis/use-template-document-run.ts", {}, clock);
  let reads = 0, finish;
  const scheduler = hook.createRunRefreshScheduler(() => {
    reads++;
    return new Promise((resolve) => { finish = resolve; });
  });
  scheduler.connection(true);
  await clock.advance(500);
  for (let i = 0; i < 30; i++) scheduler.event();
  await clock.advance(60000);
  assert.equal(reads, 1);
  finish();
  await new Promise(setImmediate);
  await clock.advance(500);
  assert.equal(reads, 2);
  scheduler.close();
  finish();
  await new Promise(setImmediate);
  assert.equal(clock.pending(), 0);
});

const source = { recognition_run_id: "run-a", analysis_id: "analysis-a", document_hash: "document-a",
  structure_hash: "structure-a", content: { type: "doc", content: [] }, selection: null, anchors: [] };
test("successive selections preserve content and reader identity, rejecting foreign or stale locations", () => {
  const before = hookModule.sourceDocumentIdentity(source);
  for (const ref of ["first", "second"]) {
    const selection = { ...source, content: undefined, selection: { selection_ref: ref }, anchors: [{ id: ref }] };
    assert.equal(hookModule.matchingSourceSelection(source, selection, ref), selection);
    assert.equal(hookModule.sourceDocumentIdentity(selection), before);
    for (const key of ["recognition_run_id", "analysis_id", "document_hash", "structure_hash"]) {
      assert.equal(hookModule.matchingSourceSelection(source, { ...selection, [key]: "foreign" }, ref), undefined);
    }
    assert.equal(hookModule.matchingSourceSelection(source, selection, "stale-ref"), undefined);
  }
  assert.deepEqual(source.content, { type: "doc", content: [] });
});

function graphFixture(levels = 12, width = 5) {
  const entities = [], relationships = [];
  for (let level = 0; level < levels; level++) {
    for (let column = 0; column < (level === 0 ? 1 : width); column++) {
      const entity_id = `${level}:${column}`;
      entities.push({ entity_id, label: entity_id, class_label: "实体", source_selection_refs: [] });
      if (!level) continue;
      for (let parent = 0; parent < (level === 1 ? 1 : width); parent++) {
        const subject = `${level - 1}:${parent}`;
        relationships.push({ candidate_id: `${subject}->${entity_id}`, predicate_iri: "relation", predicate_label: "关系",
          subject_ref: { entity_id: subject }, object_ref: { entity_id },
          policy_eligible: true, structural_valid: true, model_supported: true, polarity: "affirmed",
          source_selection_refs: { subject: [], object: [], value: [], predicate_bridge: [], condition: [], counterevidence: [] } });
      }
    }
  }
  return { entities, relationships, properties: [], coverage: { subjects: [] },
    graph_snapshot: { root_ref: { entity_id: "0:0" } } };
}

test("relationship groups retain selection, members and sources without adding graph entities or edges", () => {
  const graph = graphFixture(2, 2);
  const edge = graph.relationships[0];
  graph.extraction_protocol = "ontology-tool-extraction-v1";
  graph.relationships = [];
  graph.relationship_groups = [{ ...edge, candidate_id: "choice", revision: 1,
    object_ref: undefined, object_refs: [{ entity_id: "1:0", revision: 1 }, { entity_id: "1:1", revision: 1 }],
    selection: "one_of", modality: "required", conditions: [],
    proof_ref: { id: "proof", revision: 1 }, decision_refs: [{ id: "decision", revision: 1 }],
    source_selection_refs: { ...edge.source_selection_refs, selection: ["choice-source"] } }];
  const index = graphModule.buildTemplateGraphIndex(graph);
  assert.equal(index.entities.size, 3);
  assert.equal(index.edges.size, 0);
  assert.equal(index.parent.size, 0, "one_of membership does not fabricate factual edges");
  assert.equal(index.roots.length, 3, "independent verified entities remain available");
  const tree = graphModule.buildTemplateTreeData(index);
  assert.equal([...tree.nodes.values()].filter((node) => node.kind === "relationship_group").length, 1);
  assert.equal([...tree.nodes.values()].filter((node) => node.kind === "member").length, 2);
  const html = renderToStaticMarkup(React.createElement(graphModule.TemplateGraphTree, { graph, select() {} }));
  assert.match(html, /恰选一个（2 个成员）/);
  assert.match(html, /组选择依据原文/);
  assert.match(html, /系统验证通过/);
  assert.match(html, /要求/);
});

test("a scoped property explains a filtered parent and keeps its evidence link", () => {
  const graph = graphFixture(1, 1);
  graph.extraction_protocol = "ontology-tool-extraction-v1";
  graph.relationship_groups = [];
  graph.properties = [{ candidate_id: "scoped", revision: 1, subject_ref: { entity_id: "0:0", revision: 1 },
    predicate_iri: "temperature", predicate_label: "温度", raw_value: "20", modality: "asserted", polarity: "affirmed",
    conditions: [{ text: "加热时" }], scope: { scope_id: "scope", members: [{ relation_ref: { id: "parent", revision: 2 }, member_ref: { entity_id: "0:0", revision: 1 } }] },
    policy_eligible: true, structural_valid: true, model_supported: true,
    proof_ref: { id: "proof", revision: 1 }, decision_refs: [{ id: "decision", revision: 1 }],
    source_selection_refs: { value: [], subject: [], object: [], predicate_bridge: [], condition: [], counterevidence: [] } }];
  graph.scope_resolutions = [{ scope_id: "scope", steps: [{ relation_ref: { id: "parent", revision: 2 },
    member_ref: { entity_id: "0:0", revision: 1 }, selection: "alternatives", polarity: "affirmed", modality: "possible",
    conditions: ["限定场景"], applicability: [], evidence_selection_ids: ["scope-source"] }] }];
  const html = renderToStaticMarkup(React.createElement(graphModule.TemplateGraphTree, { graph, select() {} }));
  assert.match(html, /继承范围：parent@2/);
  assert.match(html, /限定场景/);
  assert.match(html, /范围依据原文/);
  assert.match(html, /条件：加热时/);
  assert.equal(graph.entities.length, 1);
  assert.equal(graph.relationship_groups.length, 0);
});

test("layered shared graphs create a linear canonical forest and mount only expanded branches", () => {
  const graph = graphFixture();
  const index = graphModule.buildTemplateGraphIndex(graph);
  assert.equal(index.entities.size, 56);
  assert.equal(index.parent.size, 55, "the exponentially many paths never duplicate an entity subtree");
  assert.equal(index.roots.length, 1);
  const html = renderToStaticMarkup(React.createElement(graphModule.TemplateGraphTree, { graph, select() {} }));
  assert.equal((html.match(/data-entity-id=/g) ?? []).length, 11, "closed depth-two entities do not mount their descendants");
  assert.equal((html.match(/data-entity-reference=/g) ?? []).length, 20, "shared descendants retain reference navigation");
  assert.doesNotMatch(html, /data-entity-id="3:/);
  const path = graphModule.templateGraphJumpPath(index, "11:4");
  assert.equal(path.length, 23);
  assert.ok(!path.includes("unassociated"), "a connected reference must not open unrelated roots");
  assert.ok(path.includes(JSON.stringify(["entity", "0:0"])));
  assert.ok(path.includes(JSON.stringify(["entity", "11:4"])));
});

test("disconnected cycles remain reachable as lazy roots instead of disappearing or recursing", () => {
  const graph = graphFixture(2, 1);
  for (const id of ["cycle-a", "cycle-b"]) graph.entities.push({ entity_id: id, label: id,
    class_label: "实体", source_selection_refs: [] });
  const base = graph.relationships[0];
  graph.relationships.push({ ...base, candidate_id: "a-b", subject_ref: { entity_id: "cycle-a" }, object_ref: { entity_id: "cycle-b" } },
    { ...base, candidate_id: "b-a", subject_ref: { entity_id: "cycle-b" }, object_ref: { entity_id: "cycle-a" } });
  const index = graphModule.buildTemplateGraphIndex(graph);
  assert.equal(index.roots.length, 2);
  assert.equal(index.parent.size, 2);
  const path = graphModule.templateGraphJumpPath(index, "cycle-b");
  assert.ok(path.includes("unassociated"));
  assert.equal(path.length, 4);
  const html = renderToStaticMarkup(React.createElement(graphModule.TemplateGraphTree, { graph, select() {} }));
  assert.match(html, /未关联实体（1 组）/);
  assert.doesNotMatch(html, /data-entity-id="cycle-/);
});

test("indexes preserve separate subjects, predicates, properties and incomplete coverage", () => {
  const graph = graphFixture(2, 2);
  const property = { candidate_id: "property", subject_ref: { entity_id: "1:1" }, predicate_iri: "field", raw_value: "17" };
  const coverage = { subject_ref: { entity_id: "1:1" }, predicate_iri: "field", records_planned: 9,
    records_examined: 2, records_incomplete: 1, records_unattempted: 6 };
  graph.properties.push(property);
  graph.coverage.subjects.push(coverage);
  const index = graphModule.buildTemplateGraphIndex(graph);
  assert.equal(index.properties.get("1:1").get("field")[0], property);
  assert.equal(index.coverage.get("1:1").get("field"), coverage);
  assert.equal(index.properties.get("1:0"), undefined);
  assert.match(graphModule.branchProgress(index.coverage.get("1:1").get("field")), /1 条处理未完成/);
});

test("quantity properties show raw and normalized values with their own unit sources", () => {
  const graph = graphFixture(1, 1);
  graph.properties.push({
    candidate_id: "quantity", subject_ref: { entity_id: "0:0" },
    predicate_iri: "batch-min", predicate_label: "预计批量下限（kg）",
    raw_value: "3800", normalized_value: "3.8", unit: "kg",
    structural_valid: true, model_supported: true, policy_eligible: true, polarity: "affirmed",
    source_selection_refs: { value: ["value-ref"], unit: ["unit-ref"], subject: [],
      object: [], predicate_bridge: [], condition: [], counterevidence: [] },
  });
  const render = () => renderToStaticMarkup(React.createElement(graphModule.TemplateGraphTree,
    { graph, select() {} }));
  const html = render();
  assert.match(html, /3800/);
  assert.match(html, /规范化值：3\.8 kg/);
  assert.match(html, /单位依据原文/);
  assert.match(html, /系统验证通过/);
  Object.assign(graph.properties[0], { normalized_value: { form: "interval",
    lower: "3.8", upper: "4", lower_inclusive: true, upper_inclusive: false } });
  const interval = render();
  assert.match(interval, /规范化值：\[3\.8, 4\) kg/);
  assert.doesNotMatch(interval, /\[object Object\]/);
  Object.assign(graph.properties[0], { normalized_value: null, unit: null,
    structural_valid: false, policy_eligible: false,
    reason: "数值/单位核验未通过：原文单位不兼容。模型语义说明：原文支持当前计划的批量下限。" });
  const failed = render();
  assert.match(failed, /原文单位不兼容/);
  assert.doesNotMatch(failed, /规范化值/);
});
