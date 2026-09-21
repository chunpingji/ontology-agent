import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import vm from "node:vm";
import ts from "typescript";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";

const require = createRequire(import.meta.url);
const cache = new Map();
function load(name) {
  if (cache.has(name)) return cache.get(name);
  const source = readFileSync(new URL(`../src/lib/${name}.ts`, import.meta.url), "utf8");
  const exports = {};
  cache.set(name, exports);
  vm.runInNewContext(ts.transpileModule(source, { compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022,
  } }).outputText, { exports, process: { env: {} }, require: (path) => path.startsWith("@/lib/") ? load(path.slice(6)) : require(path) });
  return exports;
}
const { buildTargetGraph, targetAssertions, targetGraphSubjects, candidateGraphCounts, assertionEndpointEntities, assertionReviewState, targetReviewCounts, visibleTargetAssertions } = load("target-graph");
const { entityRefKey } = load("document-graph");
const root = { entity_id: "root", revision: 1, class_iri: "https://example.org/CMCReport", label: "CMC报告" };
const drug = { entity_id: "drug", revision: 1, class_iri: "https://example.org/Drug", class_label: "药品", label: "药品A" };
const relation = {
  candidate_id: "relation", revision: 1, subject_ref: root, object_ref: drug,
  predicate_iri: "https://example.org/describes", predicate_label: "描述药品",
  direction: "subject_to_object", polarity: "affirmed", modality: "asserted",
  conditions: [], applicability: {}, policy_eligible: true, invalidated: false,
  structural_valid: true, model_supported: true,
};
const target = {
  target_id: "target", subject_ref: root, kind: "relationship", predicate_iri: relation.predicate_iri,
  predicate_label: relation.predicate_label, range_types: [{ iri: drug.class_iri, label: drug.class_label }],
  supported_count: 1, completed: false, state: "partial", multiplicity: "unspecified",
  assertion_refs: [{ id: "relation", revision: 1 }], supported_assertion_refs: [{ id: "relation", revision: 1 }],
};
function artifact(targets = [target], relationships = [relation]) {
  return { phase: "evidence_verification", root, targets, graph: { entities: [root, drug], relationships, properties: [], relationship_groups: [] } };
}

test("one supported object keeps the unknown-size relationship placeholder and incomplete target", () => {
  const graph = buildTargetGraph(artifact(), new Set([entityRefKey(root)]));
  assert.equal(graph.edges.filter((edge) => edge.supported).length, 1);
  assert.equal(graph.edges.filter((edge) => !edge.supported).length, 1);
  assert.equal(graph.nodes.filter((node) => node.kind === "placeholder").length, 1);
  assert.equal(target.completed, false);
});

test("model confidence and eligible flags do not make a candidate edge solid without the exact supported revision", () => {
  const graph = buildTargetGraph(artifact([{ ...target, supported_assertion_refs: [{ id: "relation", revision: 2 }] }]), new Set([entityRefKey(root)]));
  assert.equal(graph.edges.some((edge) => edge.supported), false);
  assert.equal(targetAssertions({ ...target, assertion_refs: [{ id: "relation", revision: 2 }] }, artifact()).length, 0);
});

test("a proof-qualified negative statement is solid and explicitly labelled negative", () => {
  const graph = buildTargetGraph(artifact([{ ...target, completed: true, state: "negated", supported_count: 0 }], [{ ...relation, polarity: "negated" }]), new Set([entityRefKey(root)]));
  assert.equal(graph.edges.length, 1);
  assert.equal(graph.edges[0].supported, true);
  assert.match(graph.edges[0].label, /否定/);
});

test("qualified planned statements retain their qualification on solid edges", () => {
  const graph = buildTargetGraph(artifact([{ ...target, completed: true }], [{ ...relation, modality: "planned", conditions: [{ text: "获批后" }] }]), new Set([entityRefKey(root)]));
  assert.equal(graph.edges[0].supported, true);
  assert.match(graph.edges[0].label, /计划/);
  assert.match(graph.edges[0].label, /附条件/);
});

test("cyclic ontology/entity relations expand once per entity and never duplicate a root node", () => {
  const back = { ...relation, candidate_id: "back", subject_ref: drug, object_ref: root };
  const backTarget = { ...target, target_id: "back-target", subject_ref: drug, completed: true,
    assertion_refs: [{ id: "back", revision: 1 }], supported_assertion_refs: [{ id: "back", revision: 1 }] };
  const graph = buildTargetGraph(artifact([{ ...target, completed: true }, backTarget], [relation, back]), new Set([entityRefKey(root), entityRefKey(drug)]));
  assert.equal(graph.nodes.length, 2);
  assert.equal(graph.edges.length, 2);
});

test("relationship groups retain alternatives semantics rather than flattening members into ordinary facts", () => {
  const value = artifact([{ ...target, completed: true }], []);
  value.graph.relationship_groups = [{ ...relation, object_ref: undefined, object_refs: [drug], selection: "alternatives" }];
  const graph = buildTargetGraph(value, new Set([entityRefKey(root)]));
  assert.equal(graph.nodes.filter((node) => node.kind === "group").length, 1);
  assert.equal(graph.edges.length, 2);
  assert.match(graph.nodes.find((node) => node.kind === "group").subtitle, /可选成员/);
});

test("visible graph limits leave the source artifact intact and report omitted nodes", () => {
  const value = artifact();
  const graph = buildTargetGraph(value, new Set([entityRefKey(root)]), 1);
  assert.equal(graph.nodes.length, 1);
  assert.ok(graph.hiddenItems > 0);
  assert.equal(value.graph.relationships.length, 1);
  assert.equal(value.targets.length, 1);
});

test("isolated subjects and every counted property remain available through the entity selector without invented edges", () => {
  const property = { ...target, target_id: "isolated-property", subject_ref: drug, subject_label: drug.label,
    kind: "property", predicate_iri: "https://example.org/name", predicate_label: "名称" };
  const value = artifact([property], []);
  const graph = buildTargetGraph(value, new Set([entityRefKey(root)]));
  assert.equal(graph.nodes.length, 1);
  assert.equal(graph.edges.length, 0);
  const subjects = targetGraphSubjects(value);
  assert.equal(subjects.find((item) => item.id === entityRefKey(drug)).properties, 1);
  assert.equal(subjects.find((item) => item.id === entityRefKey(drug)).label, drug.label);
  assert.equal(subjects.reduce((total, item) => total + item.properties, 0), 1);
});

test("subjects whose graph entity is not projected can still expose their counted targets", () => {
  const hidden = { entity_id: "hidden", revision: 2 };
  const value = artifact([{ ...target, subject_ref: hidden, subject_label: "未关联主体" }], []);
  const subjects = targetGraphSubjects(value);
  assert.equal(subjects.find((item) => item.id === entityRefKey(hidden)).relationships, 1);
  assert.equal(subjects.find((item) => item.id === entityRefKey(hidden)).label, "未关联主体");
});

test("initial root fan-out labels follow object rows instead of crowding the curve midpoints", () => {
  const source = readFileSync(new URL("../src/components/analysis/target-graph-canvas.tsx", import.meta.url), "utf8");
  const exports = {};
  vm.runInNewContext(ts.transpileModule(source, { compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX,
  } }).outputText, { exports, require: (path) => {
    if (path.startsWith("@/lib/")) return load(path.slice(6));
    if (path === "@/components/ui/button") return { Button: ({ children, ...props }) => React.createElement("button", props, children) };
    return require(path);
  } });
  const value = artifact(Array.from({ length: 12 }, (_, index) => ({ ...target,
    target_id: `target-${index}`, assertion_refs: [], supported_assertion_refs: [], state: "pending",
  })), []);
  const html = renderToStaticMarkup(React.createElement(exports.TargetGraphCanvas, {
    artifact: value, selectedSubject: entityRefKey(root), onSelectSubject() {}, onSelectTarget() {},
  }));
  const labels = [...html.matchAll(/<text x="([\d.]+)" y="([\d.]+)" text-anchor="end"/g)];
  assert.equal(labels.length, 12);
  assert.equal(new Set(labels.map((match) => match[1])).size, 1);
  for (let index = 1; index < labels.length; index += 1) {
    assert.equal(Number(labels[index][2]) - Number(labels[index - 1][2]), 115);
  }
});

function candidateArtifact(targets = [target], relationships = [relation]) {
  const value = artifact(targets, relationships);
  value.phase = "candidate_graph";
  value.graph.entities = [
    { ...root, source_selection_refs: ["source:root"] },
    { ...drug, source_selection_refs: ["source:drug"] },
  ];
  value.graph.relationships = relationships.map((item) => ({ ...item,
    proof_ref: null, policy_eligible: false, model_supported: false,
    source_selection_refs: { predicate_bridge: ["source:relation"] },
  }));
  return value;
}

function loadAnalysisComponent(name) {
  const source = readFileSync(new URL(`../src/components/analysis/${name}.tsx`, import.meta.url), "utf8");
  const exports = {};
  vm.runInNewContext(ts.transpileModule(source, { compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX,
  } }).outputText, { exports, require: (path) => {
    if (path === "next/navigation") return {};
    if (path.startsWith("@/lib/")) return load(path.slice(6));
    if (path.startsWith("@/components/")) return new Proxy({}, { get: (_object, key) => ({ children, ...props }) =>
      React.createElement(key === "Button" ? "button" : "div", props, children) });
    return require(path);
  } });
  return exports;
}

test("candidate phase keeps unverified document edges dashed even when supported references are present", () => {
  const value = candidateArtifact([{ ...target, completed: true }]);
  const graph = buildTargetGraph(value, new Set([entityRefKey(root)]));
  assert.equal(graph.edges.length, 1);
  assert.equal(graph.edges[0].supported, false);
  assert.equal(graph.nodes.some((node) => node.kind === "placeholder"), false);
  assert.equal(graph.nodes.some((node) => node.subtitle.includes("属性")), false);
  assert.equal(targetAssertions(value.targets[0], value)[0].proof_ref, null);
  const { TargetGraphCanvas } = loadAnalysisComponent("target-graph-canvas");
  const html = renderToStaticMarkup(React.createElement(TargetGraphCanvas, {
    artifact: value, selectedSubject: entityRefKey(root), onSelectSubject() {}, onSelectTarget() {},
  }));
  assert.match(html, /所有关系均为虚线/);
  assert.match(html, /stroke-dasharray="6 5"/);
  assert.doesNotMatch(html, /原文已实证|点击节点查看属性/);
});

test("candidate count includes document candidates once and excludes ontology placeholders and root", () => {
  const value = candidateArtifact([target, { ...target, target_id: "placeholder", assertion_refs: [] }]);
  value.graph.entities.push({ ...drug });
  value.graph.relationship_groups.push({ ...relation, candidate_id: "group", object_ref: undefined,
    object_refs: [drug, { ...drug, entity_id: "alternative" }], selection: "alternatives" });
  value.targets.push({ ...target, target_id: "group-target", assertion_refs: [{ id: "group", revision: 1 }] });
  const counts = candidateGraphCounts(value);
  assert.equal(counts.entities, 1);
  assert.equal(counts.relationships, 2);
  const { CandidateGraphSummary } = loadAnalysisComponent("graph-analysis-panel");
  const html = renderToStaticMarkup(React.createElement(CandidateGraphSummary, { artifact: value }));
  assert.match(html, /候选实体/);
  assert.match(html, /原文关系候选/);
  assert.doesNotMatch(html, /完整度|已实证|%/);
});

test("selecting an isolated candidate renders its own expandable targets without inventing a root edge", () => {
  const isolatedTarget = { ...target, target_id: "isolated-target", subject_ref: drug,
    subject_label: drug.label, assertion_refs: [], supported_assertion_refs: [], state: "pending" };
  const value = candidateArtifact([isolatedTarget], []);
  const id = entityRefKey(drug);
  const collapsed = buildTargetGraph(value, new Set([entityRefKey(root)]), 80, id);
  assert.equal(collapsed.nodes.length, 1);
  assert.equal(collapsed.nodes[0].id, id);
  assert.equal(collapsed.nodes[0].expandable, true);
  assert.equal(collapsed.edges.length, 0);
  const expanded = buildTargetGraph(value, new Set([id]), 80, id);
  assert.equal(expanded.edges.length, 1);
  assert.equal(expanded.edges[0].source, id);
  assert.equal(expanded.edges[0].supported, false);
  assert.match(expanded.nodes.find((node) => node.kind === "placeholder").subtitle, /本体关系目标/);
  assert.equal(expanded.nodes.some((node) => node.id === entityRefKey(root)), false);
  const { TargetGraphCanvas } = loadAnalysisComponent("target-graph-canvas");
  const html = renderToStaticMarkup(React.createElement(TargetGraphCanvas, {
    artifact: value, selectedSubject: id, onSelectSubject() {}, onSelectTarget() {},
  }));
  assert.match(html, /展开药品A的关系/);
});

test("candidate details preserve relationship and endpoint source buttons without proof or completion claims", () => {
  const value = candidateArtifact();
  value.targets[0] = { ...target, reason: "原文关系候选，尚未核验。" };
  const assertion = value.graph.relationships[0];
  const endpoints = assertionEndpointEntities(assertion, value);
  assert.deepEqual(Array.from(endpoints, ({ role, entity }) => [role, entity.source_selection_refs[0]]), [
    ["主体", "source:root"], ["对象", "source:drug"],
  ]);
  const { TargetDetails } = loadAnalysisComponent("graph-analysis-panel");
  const clicked = [];
  const view = React.createElement(TargetDetails, {
    target: value.targets[0], artifact: value, focused: true, onEvidence: (ref) => clicked.push(ref),
  });
  const html = renderToStaticMarkup(view);
  assert.match(html, /原文候选 · 未核验/);
  assert.match(html, /主体原文 1/);
  assert.match(html, /对象原文 1/);
  assert.doesNotMatch(html, /已实证|识别完成|范围已核对|范围待核对/);
  function clickButtons(node) {
    if (Array.isArray(node)) return node.forEach(clickButtons);
    if (!React.isValidElement(node)) return;
    if (typeof node.type === "function") return clickButtons(node.type(node.props));
    if (node.type === "button") node.props.onClick?.();
    clickButtons(node.props.children);
  }
  clickButtons(view);
  assert.deepEqual(clicked, ["source:relation", "source:root", "source:drug"]);
});

test("candidate groups retain alternative member semantics and remain dashed", () => {
  const value = candidateArtifact([target], []);
  value.graph.relationship_groups = [{ ...relation, object_ref: undefined,
    object_refs: [drug], selection: "alternatives", proof_ref: null,
    source_selection_refs: { predicate_bridge: ["source:group"] } }];
  const graph = buildTargetGraph(value, new Set([entityRefKey(root)]));
  assert.equal(graph.edges.length, 2);
  assert.equal(graph.edges.every((edge) => !edge.supported), true);
  assert.match(graph.nodes.find((node) => node.kind === "group").subtitle, /可选成员/);
  assert.equal(candidateGraphCounts(value).relationships, 1);
});

function reviewArtifact() {
  const accepted = { ...relation, candidate_id: "accepted", decision_status: "supported",
    proof_ref: { id: "proof", revision: 1 }, source_selection_refs: { predicate_bridge: ["source:accepted"] },
    validation_diagnostics: [], independent_review: "unreviewed" };
  const pending = { ...accepted, candidate_id: "pending", decision_status: "undetermined",
    proof_ref: null, reason: "原文主体归属尚不明确。" };
  const rejected = { ...accepted, candidate_id: "rejected", decision_status: "unsupported",
    reason: "谓词与原文角色不符。", reason_code: "predicate_mismatch", proof_ref: null };
  const value = candidateArtifact([{ ...target, reason: "保留全部本体识别目标。",
    assertion_refs: [accepted, pending, rejected].map((item) => ({ id: item.candidate_id, revision: 1 })),
    supported_assertion_refs: [{ id: "accepted", revision: 1 }],
  }]);
  value.phase = "evidence_review";
  value.graph.relationships = [accepted, pending, rejected];
  value.summary = { relationships: { total: 2, supported: 1, completed: 0, percent: 0 },
    properties: { total: 4, supported: 0, completed: 0, percent: 0 }, notes: [], pending_expansion_count: 0 };
  return value;
}

test("evidence review draws accepted edges solid and pending dashed, hiding unaccepted by default without changing targets", () => {
  const value = reviewArtifact();
  const before = JSON.stringify(value);
  const hidden = buildTargetGraph(value, new Set([entityRefKey(root)]));
  const edges = hidden.edges.filter((edge) => edge.reviewState);
  assert.equal(edges.length, 2);
  assert.equal(edges.filter((edge) => edge.supported).length, 1);
  assert.equal(edges.some((edge) => edge.reviewState === "not_accepted"), false);
  const shown = buildTargetGraph(value, new Set([entityRefKey(root)]), 80, undefined, true);
  assert.equal(shown.edges.filter((edge) => edge.reviewState).length, 3);
  assert.equal(shown.edges.find((edge) => edge.reviewState === "not_accepted").supported, false);
  assert.equal(JSON.stringify(value), before);
  assert.equal(value.summary.relationships.total, 2);
  assert.equal(visibleTargetAssertions(value.targets[0], value).length, 2);
  assert.equal(visibleTargetAssertions(value.targets[0], value, true).length, 3);
  assert.deepEqual({ ...targetReviewCounts(value) }, { accepted: 1, pending: 1, not_accepted: 1 });
});

test("unaccepted evidence details and exact reason can be revealed while pending remains visible", () => {
  const value = reviewArtifact();
  const { TargetDetails, EvidenceReviewSummary } = loadAnalysisComponent("graph-analysis-panel");
  const render = (showNotAccepted) => renderToStaticMarkup(React.createElement(TargetDetails, {
    target: value.targets[0], artifact: value, focused: false, onEvidence() {}, showNotAccepted,
  }));
  assert.match(render(false), /已隐藏 1 条未采信候选/);
  assert.match(render(false), /原文主体归属尚不明确/);
  assert.doesNotMatch(render(false), /谓词与原文角色不符/);
  assert.match(render(true), /谓词与原文角色不符/);
  assert.match(render(true), /predicate_mismatch/);
  assert.doesNotMatch(render(true), /已隐藏 1 条/);
  const summary = renderToStaticMarkup(React.createElement(EvidenceReviewSummary, { artifact: value }));
  assert.match(summary, /关系采信目标/);
  assert.match(summary, /1 \/ 2/);
  assert.match(summary, /属性采信目标/);
  assert.match(summary, /0 \/ 4/);
  assert.doesNotMatch(summary, /完整度|%/);
});

test("SHACL warnings do not downgrade accepted raw evidence or label it a mere candidate", () => {
  const value = reviewArtifact();
  value.graph.relationships[0] = { ...value.graph.relationships[0], policy_eligible: false,
    validation_diagnostics: [{ check: "shacl", status: "failed", reason_codes: ["domain_warning"], message: "SHACL 约束告警，原文证据仍成立。" }] };
  assert.equal(assertionReviewState(value.targets[0], value.graph.relationships[0]), "accepted");
  const graph = buildTargetGraph(value, new Set([entityRefKey(root)]));
  const accepted = graph.edges.find((edge) => edge.reviewState === "accepted");
  assert.equal(accepted.supported, true);
  assert.doesNotMatch(accepted.label, /候选/);
  const { TargetDetails } = loadAnalysisComponent("graph-analysis-panel");
  const html = renderToStaticMarkup(React.createElement(TargetDetails, {
    target: value.targets[0], artifact: value, focused: false, onEvidence() {},
  }));
  assert.match(html, /SHACL 检查 · 告警/);
  assert.match(html, /domain_warning/);
  assert.match(html, /已采信/);
  assert.match(html, /独立告警不改变原文证据采信状态/);
});

test("raw value and raw unit remain separate from normalized value and unit", () => {
  const { PropertyValues } = loadAnalysisComponent("graph-analysis-panel");
  const property = { raw_value: "5", raw_unit: "g", normalized_value: 5000, unit: "mg", normalization_available: true };
  const html = renderToStaticMarkup(React.createElement(PropertyValues, { property }));
  assert.match(html, /原文值<\/dt><dd[^>]*>5<\/dd>/);
  assert.match(html, /原文单位<\/dt><dd>g<\/dd>/);
  assert.match(html, /规范化值<\/dt><dd[^>]*>5000<\/dd>/);
  assert.match(html, /规范化单位<\/dt><dd>mg<\/dd>/);
  const unavailable = renderToStaticMarkup(React.createElement(PropertyValues, { property: { ...property, normalization_available: false } }));
  assert.match(unavailable, /原文值<\/dt><dd[^>]*>5<\/dd>/);
  assert.match(unavailable, /规范化值<\/dt><dd[^>]*>未生成<\/dd>/);
  assert.doesNotMatch(unavailable, /5000/);
});

test("normalized zero and false are displayed rather than treated as missing", () => {
  const { PropertyValues } = loadAnalysisComponent("graph-analysis-panel");
  for (const normalized of [0, false]) {
    const html = renderToStaticMarkup(React.createElement(PropertyValues, { property: {
      raw_value: String(normalized), raw_unit: null, normalized_value: normalized,
      unit: null, normalization_available: true,
    } }));
    assert.match(html, new RegExp(`规范化值</dt><dd[^>]*>${normalized}</dd>`));
    assert.doesNotMatch(html, /未生成/);
  }
});

test("an unaccepted incoming relationship does not hide a separately selectable child's accepted property", () => {
  const value = reviewArtifact();
  value.graph.relationships = [value.graph.relationships[2]];
  value.targets[0] = { ...value.targets[0], assertion_refs: [{ id: "rejected", revision: 1 }], supported_assertion_refs: [] };
  const prop = { ...relation, candidate_id: "child-property", subject_ref: drug, raw_value: "0",
    object_ref: undefined, direction: "subject_to_value", decision_status: "supported", proof_ref: { id: "proof-child", revision: 1 },
    source_selection_refs: { value: ["source:child"] }, validation_diagnostics: [], normalized_value: 0,
    unit: null, raw_unit: null, normalization_available: true };
  value.graph.properties = [prop];
  const propertyTarget = { ...target, target_id: "child-property-target", subject_ref: drug, subject_label: drug.label, kind: "property",
    assertion_refs: [{ id: "child-property", revision: 1 }], supported_assertion_refs: [{ id: "child-property", revision: 1 }] };
  value.targets.push(propertyTarget);
  assert.equal(targetGraphSubjects(value).find((item) => item.id === entityRefKey(drug)).properties, 1);
  assert.equal(visibleTargetAssertions(propertyTarget, value).length, 1);
  assert.equal(assertionReviewState(propertyTarget, prop), "accepted");
  const graph = buildTargetGraph(value, new Set([entityRefKey(drug)]), 80, entityRefKey(drug));
  assert.equal(graph.nodes[0].id, entityRefKey(drug));
});
