import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";
import ts from "typescript";

const exports = {};
vm.runInNewContext(ts.transpileModule(readFileSync(new URL("../src/lib/evidence-graph.ts", import.meta.url), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText, { exports });
const { buildEvidenceGraph, buildDocumentEvidenceGraph, evidenceBranches, publishableEvidence, evidenceValue } = exports;
const { branchProgressText } = exports;

test("empty branches distinguish unavailable history, waiting, citation failure and completed coverage", () => {
  assert.equal(branchProgressText(), "尚无分支处理记录");
  assert.equal(branchProgressText({ status: "queued" }, "running"), "等待识别");
  assert.match(branchProgressText({ status: "entities_failed" }), /关联对象识别未完成/);
  assert.match(branchProgressText({ status: "awaiting_relation" }), /已识别关联对象/);
  assert.equal(branchProgressText({ status: "relation_checked", coverage_complete: false }), "已检查部分关系，尚未形成有效肯定关系");
  assert.match(branchProgressText({ status: "no_match", coverage_complete: false }), /尚未完成/);
  assert.match(branchProgressText({ status: "no_match", coverage_complete: true }), /本轮处理完成/);
  assert.match(branchProgressText({ status: "identified", coverage_complete: false }), /覆盖尚未完成/);
});

test("paused and failed runs do not retain an actively processing branch label", () => {
  assert.equal(branchProgressText({ status: "extracting_relation" }, "running"), "正在验证关系");
  assert.equal(branchProgressText({ status: "extracting_relation" }, "paused"), "已暂停；关系验证尚未完成");
  assert.equal(branchProgressText({ status: "extracting_entities" }, "failed"), "运行失败；关联对象识别尚未完成");
  assert.doesNotMatch(branchProgressText({ status: "extracting_entities" }), /正在/);
});

test("a recognized relation retains unfinished entity-property progress", () => {
  assert.match(branchProgressText({ status: "identified", property_status: "queued" }), /属性等待识别/);
  assert.match(branchProgressText({ status: "identified", property_status: "not_applicable" }), /本体未声明/);
  assert.match(branchProgressText({ status: "identified", property_status: "incomplete" }), /属性识别未完成/);
  assert.match(branchProgressText({ status: "identified", property_status: "extracting" }, "running"), /正在识别关联实体属性/);
  assert.doesNotMatch(branchProgressText({ status: "identified", property_status: "extracting" }, "paused"), /正在识别/);
});
const entity = (id) => ({ candidate_id: id, revision: 1, kind: "entity", text: "同名对象",
  review_status: "confirmed", validation_status: "passed", commit_status: "not_requested" });
const edge = (id, subject, object) => ({ ...entity(id), kind: "relationship",
  subject: { candidate_id: subject, revision: 1 }, object: { candidate_id: object, revision: 1 } });
const property = (id, subject) => ({ ...entity(id), kind: "property", subject: { candidate_id: subject, revision: 1 } });

test("raw values retain their original unit when the normalized unit differs", () => {
  assert.equal(evidenceValue({ literal: { raw_value: "1500", raw_unit: "g", normalized_value: "1.5", canonical_unit: "kg" } }), "1500 g");
  assert.equal(evidenceValue({ literal: { raw_value: "1.50~12.00kg", raw_unit: "kg" } }), "1.50~12.00kg");
});

test("attributes stay attached to their entity even when entity labels coincide", () => {
  const graph = buildEvidenceGraph([entity("doc"), entity("plan-a"), entity("plan-b"),
    edge("a", "doc", "plan-a"), edge("b", "doc", "plan-b"), property("batch-a", "plan-a"), property("batch-b", "plan-b")]);
  assert.equal(graph.roots.length, 1);
  assert.equal(graph.roots[0].candidate_id, "doc");
  assert.equal(graph.properties.get("plan-a")[0].candidate_id, "batch-a");
  assert.equal(graph.properties.get("plan-b")[0].candidate_id, "batch-b");
  assert.equal(graph.relationships.get("doc").length, 2);
});

test("disconnected cycles and missing subjects remain visible", () => {
  const graph = buildEvidenceGraph([entity("a"), entity("b"), entity("standalone"),
    edge("ab", "a", "b"), edge("ba", "b", "a"), property("orphan", "missing")]);
  assert.equal(graph.roots.length, 2);
  assert.equal(graph.orphans[0].candidate_id, "orphan");
  assert.equal(graph.entities.size, 3);
});

const schema = {
  document_class_iri: "urn:CMCReport",
  classes: {
    "urn:CMCReport": { label: "CMC 报告", parents: [], properties: [{ iri: "urn:title", label: "标题" }],
      relationships: [
        { iri: "urn:hasPlan", label: "生产计划", range: ["urn:Plan"] },
        { iri: "urn:previousPlan", label: "原计划", range: ["urn:Plan"] },
        { iri: "urn:uses", label: "使用设备", range: ["urn:Equipment"] },
      ] },
    "urn:Plan": { label: "生产计划", parents: [], properties: [{ iri: "urn:batch", label: "批量" }], relationships: [] },
  },
};
const document = (id) => ({ ...entity(id), class_iri: "urn:CMCReport", identity: { document_root: "source-hash" } });

test("source ontology drives an empty tree without assigning entities by class or name", () => {
  const graph = buildDocumentEvidenceGraph([document("doc"), { ...entity("plan"), class_iri: "urn:Plan" },
    { ...entity("similar-document"), class_iri: "urn:CMCReport" }], schema);
  assert.equal(graph.documentRoots.map((item) => item.candidate_id).join(","), "doc");
  assert.equal(graph.branches.map((item) => item.iri).join(","), "urn:title,urn:hasPlan,urn:previousPlan,urn:uses");
  assert.ok(graph.branches.every((branch) => branch.candidates.length === 0));
  assert.equal(graph.unassociated.map((item) => item.candidate_id).join(","), "plan,similar-document");
  const empty = buildDocumentEvidenceGraph([], schema);
  assert.equal(empty.documentClass, "urn:CMCReport");
  assert.equal(empty.branches.length, 4);
});

test("predicates group their actual instances and keep distinct relations to the same range", () => {
  const graph = buildDocumentEvidenceGraph([document("doc"), entity("a"), entity("b"), entity("c"),
    { ...edge("first", "doc", "a"), predicate_iri: "urn:hasPlan" },
    { ...edge("second", "doc", "b"), predicate_iri: "urn:hasPlan" },
    { ...edge("previous", "doc", "a"), predicate_iri: "urn:previousPlan" },
    edge("deeper", "a", "c"), edge("cycle", "c", "doc"),
    { ...property("batch-a", "a"), predicate_iri: "urn:batch" },
    { ...property("batch-b", "b"), predicate_iri: "urn:batch" }], schema);
  assert.equal(graph.branches.find((item) => item.iri === "urn:hasPlan").candidates.map((item) => item.candidate_id).join(","), "first,second");
  assert.equal(graph.branches.find((item) => item.iri === "urn:previousPlan").candidates[0].candidate_id, "previous");
  assert.equal(graph.connected.size, 4);
  assert.equal(graph.unassociated.length, 0);
  assert.equal(graph.properties.get("a")[0].candidate_id, "batch-a");
  assert.equal(graph.properties.get("b")[0].candidate_id, "batch-b");
});

test("historical document roots share the schema view while preserving candidate identity and subjects", () => {
  const otherRoot = { ...document("other-type"), class_iri: "urn:Plan" };
  const graph = buildDocumentEvidenceGraph([document("old"), document("new"), otherRoot, entity("a"), entity("b"),
    { ...edge("old-edge", "old", "a"), predicate_iri: "urn:hasPlan" },
    { ...edge("new-edge", "new", "b"), predicate_iri: "urn:hasPlan" }], schema);
  assert.equal(graph.documentRoots.length, 2);
  assert.equal(graph.documentClass, "urn:CMCReport");
  assert.equal(graph.branches.find((item) => item.iri === "urn:hasPlan").candidates.map((item) => item.subject.candidate_id).join(","), "old,new");
  assert.equal(graph.unassociated[0].candidate_id, "other-type");
});

test("duplicate schema ranges merge by predicate; unknown assertions remain separate from declared branches", () => {
  const definition = structuredClone(schema.classes["urn:CMCReport"]);
  definition.relationships.push({ iri: "urn:hasPlan", range: ["urn:SpecialPlan"] });
  const branches = evidenceBranches(definition, [{ ...property("extra", "doc"), predicate_iri: "urn:unmodeled" }]);
  assert.equal(branches.filter((branch) => branch.iri === "urn:hasPlan").length, 1);
  assert.equal(branches.find((branch) => branch.iri === "urn:hasPlan").range.join(","), "urn:Plan,urn:SpecialPlan");
  assert.equal(branches.at(-1).declared, false);
  assert.equal(branches.at(-1).candidates[0].candidate_id, "extra");
});

test("rejected, stale and dependent assertions never enter the publish request", () => {
  const rejected = { ...entity("rejected"), review_status: "rejected" };
  const dependent = { ...property("dependent", "ok"), dependency_refs: [{ candidate_id: "blocked-edge", revision: 1 }] };
  const published = { ...entity("published"), commit_status: "succeeded" };
  const oldRef = { ...property("stale", "ok"), subject: { candidate_id: "ok", revision: 2 } };
  const values = [entity("ok"), published, rejected, edge("blocked-edge", "ok", "rejected"), dependent,
    oldRef, property("missing", "absent"), property("good", "published")];
  assert.equal(publishableEvidence(values).map((item) => item.candidate_id).join(","), "ok,good");
});
