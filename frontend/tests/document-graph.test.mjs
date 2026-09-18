import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";
import ts from "typescript";

const source = readFileSync(new URL("../src/lib/document-graph.ts", import.meta.url), "utf8");
const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
const sandbox = { exports: {} };
vm.runInNewContext(code, sandbox);
const { buildDocumentGraph: build, graphSelectionKey: key, assertionQualifier,
  graphEntityLabel, graphPredicateLabel, graphScopeStepLabel, graphQualifierText } = sandbox.exports;
const entity = (id, revision = 1) => ({ entity_id: id, revision, label: id, class_label: "类型", seed_origin: "recognized" });
const ref = (id, revision = 1) => ({ entity_id: id, revision });
const claim = (id, extra = {}) => ({ candidate_id: id, revision: 1, subject_ref: ref("A"),
  predicate_label: "连接", direction: "subject_to_object", polarity: "affirmed", modality: "asserted",
  conditions: [], policy_eligible: true, invalidated: false, ...extra });
const artifact = (extra = {}) => ({ entities: [entity("A"), entity("B"), entity("孤立")],
  relationships: [], relationship_groups: [], properties: [], graph_snapshot: { root_ref: ref("A") }, ...extra });
const entityKey = (id, revision = 1) => key({ kind: "entity", id, revision });

test("root and isolated instances survive; inverse direction respects exact versions", () => {
  const input = artifact({ relationships: [claim("inverse", { object_ref: ref("B"), direction: "object_to_subject" })] });
  const before = JSON.stringify(input);
  const graph = build(input);
  assert.equal(graph.nodes.length, 3);
  assert.equal(graph.nodes.find((node) => node.id === entityKey("A")).root, true);
  assert.ok(graph.nodes.some((node) => node.id === entityKey("孤立")));
  assert.equal(graph.links[0].source, entityKey("B"));
  assert.equal(graph.links[0].target, entityKey("A"));
  assert.equal(JSON.stringify(input), before, "D3 layout input cannot mutate the graph artifact");
});

test("one_of is a qualified connector, never two ordinary facts", () => {
  const graph = build(artifact({ relationship_groups: [claim("choice", {
    selection: "one_of", modality: "possible", object_refs: [ref("B"), ref("孤立")],
  })] }));
  const group = graph.nodes.find((node) => node.kind === "relationship_group");
  assert.match(group.subtitle, /恰选一个.*可能/);
  assert.equal(graph.nodes.filter((node) => node.kind === "entity").length, 3);
  assert.equal(graph.links.length, 3);
  assert.ok(graph.links.every((link) => link.group && link.qualified));
  assert.ok(graph.links.every((link) => link.source === group.id || link.target === group.id));
  const reverse = build(artifact({ relationship_groups: [claim("choice", {
    selection: "one_of", object_refs: [ref("B")], direction: "object_to_subject",
  })] }));
  assert.equal(reverse.links[0].target, entityKey("A"));
  assert.equal(reverse.links[1].source, entityKey("B"));
});

test("missing exact endpoint is disclosed without substituting same ID from another revision", () => {
  const graph = build(artifact({ relationships: [claim("stale", { object_ref: ref("B", 2) })] }));
  assert.equal(graph.links.length, 0);
  assert.equal(graph.missingEndpoints, 1);
  const revisions = build(artifact({ entities: [entity("A"), entity("B"), entity("B", 2)],
    relationships: [claim("current", { object_ref: ref("B", 2) })] }));
  assert.equal(new Set(revisions.nodes.map((node) => node.id)).size, 3);
  assert.equal(revisions.links[0].target, entityKey("B", 2));
});

test("negative, scoped, modal and invalidated claims retain textual qualifications", () => {
  const graph = build(artifact({ relationships: [claim("negative", { object_ref: ref("B"), polarity: "negated",
    modality: "required", invalidated: true, scope: { members: [{}] }, conditions: [{}] })] }));
  assert.equal(graph.links[0].qualified, true);
  assert.match(graph.links[0].label, /已失效.*否定.*要求.*继承限定.*附条件/);
  assert.match(assertionQualifier(claim("candidate", { policy_eligible: false })), /候选/);
});

test("empty projections and self/parallel relationships preserve their actual cardinality", () => {
  const empty = build(artifact({ entities: [], graph_snapshot: null }));
  assert.equal(empty.nodes.length, 0);
  assert.equal(empty.links.length, 0);
  const graph = build(artifact({ relationships: [claim("self", { object_ref: ref("A") }),
    claim("one", { object_ref: ref("B") }), claim("two", { object_ref: ref("B") })] }));
  assert.equal(graph.links.length, 3);
  assert.equal(new Set(graph.links.map((link) => link.id)).size, 3);
});

test("business names replace hashes and IRIs without changing endpoint identities or property values", () => {
  const rootId = "a".repeat(64), objectId = "b".repeat(64), relationId = "c".repeat(64);
  const input = artifact({
    entities: [{ ...entity(rootId), label: "测试报告", class_iri: "urn:Report", class_label: "报告" },
      { ...entity(objectId), class_iri: "urn:Route", class_label: "合成路线" }],
    graph_snapshot: { root_ref: ref(rootId) },
    relationships: [claim(relationId, { subject_ref: ref(rootId), object_ref: ref(objectId),
      predicate_iri: "urn:hasRoute", predicate_label: "含合成路线" })],
    properties: [claim("property", { raw_value: "EQ-001", predicate_label: "设备编号" })],
  });
  const before = JSON.stringify(input);
  const graph = build(input);
  assert.equal(graph.nodes[0].label, "测试报告");
  assert.equal(graph.nodes[1].label, "合成路线");
  assert.equal(graph.links[0].label, "含合成路线");
  assert.equal(graph.links[0].target, entityKey(objectId));
  assert.equal(graph.links[0].selection.id, relationId);
  assert.equal(JSON.stringify(input), before);
  assert.equal(graphEntityLabel({ ...entity("id"), label: "EQ-001" }), "EQ-001");
});

test("missing names use readable ontology terms or explicit missing labels, never opaque identifiers", () => {
  assert.equal(graphPredicateLabel({ predicate_label: "", predicate_iri: "https://example.org/hasRoute" }), "hasRoute");
  assert.equal(graphPredicateLabel({ predicate_label: "urn:hasRoute", predicate_iri: "urn:hasRoute" }), "hasRoute");
  assert.equal(graphPredicateLabel({ predicate_label: "d".repeat(64), predicate_iri: "urn:" + "e".repeat(64) }), "未命名属性或关系");
  assert.equal(graphEntityLabel(), "当前视图未显示的实体");
  assert.equal(graphEntityLabel({ ...entity("id"), label: "d231338c-f96f-436a-b759-85fc5610578c", class_label: "", class_iri: "urn:Report" }), "Report");
  const graph = build(artifact({
    entities: [{ ...entity("A"), predicate_menu: [{ predicate_iri: "urn:hasRoute", predicate_label: "含合成路线", kind: "relationship" }] }, entity("B")],
    relationships: [claim("named-from-menu", { predicate_iri: "urn:hasRoute", predicate_label: "hasRoute", object_ref: ref("B") })],
  }));
  assert.equal(graph.links[0].label, "含合成路线", "An IRI local name must not overwrite an available ontology label");
});

test("scope names use exact relation and entity revisions, with explicit fallback for filtered parents", () => {
  const input = artifact({ relationships: [claim("parent", { predicate_label: "使用设备", object_ref: ref("B") })] });
  const step = { relation_ref: { id: "parent", revision: 1 }, member_ref: ref("B") };
  assert.equal(graphScopeStepLabel(step, input), "使用设备 → B");
  assert.equal(graphScopeStepLabel({ ...step, relation_ref: { id: "parent", revision: 2 } }, input), "上级关系 → B");
  assert.equal(graphScopeStepLabel({ ...step, member_ref: ref("B", 2) }, input), "使用设备 → 当前视图未显示的实体");
});

test("condition text preserves business limitations while reference metadata stays out of display text", () => {
  assert.equal(graphQualifierText([{ text: "温度不高于 25 ℃" }]), "温度不高于 25 ℃");
  assert.equal(graphQualifierText({ qualifiers: [{ text: "仅限试验阶段", predicate_iri: "urn:phase", evidence_selection_ids: ["e".repeat(64)] }] }), "仅限试验阶段");
  assert.equal(graphQualifierText({ legacy_scope: "opaque-scope" }), "存在限定，详见原文或技术详情");
  assert.equal(graphQualifierText({}), "");
});
