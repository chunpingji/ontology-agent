import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";
import { webcrypto } from "node:crypto";
import ts from "typescript";

function load(path, globals = {}) {
  const exports = {};
  vm.runInNewContext(ts.transpileModule(readFileSync(new URL(path, import.meta.url), "utf8"), {
    compilerOptions: { module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022 },
  }).outputText, {exports,structuredClone,...globals});
  return exports;
}
const reasons = load("../src/lib/document-analysis.ts");
const helpers = load("../src/lib/document-property-review.ts", {
  crypto:{getRandomValues:webcrypto.getRandomValues.bind(webcrypto)},
  require(name) { assert.equal(name,"./document-analysis");return reasons; },
});
const property = {candidate_id:"value",revision:3,subject_ref:{entity_id:"drug",revision:2},raw_value:"1 mg"};
const run = {recognition_run_id:"run",run_revision:7,status:"paused",identities:{graph_snapshot_id:"graph"}};
const graph = {recognition_run_id:"run",graph_snapshot:{snapshot_id:"graph"},properties:[property],
  entities:[{entity_id:"drug",revision:2,label:"药物A"}]};

test("freeze only the current stopped run and immutable candidate revision",()=>{
  const target=helpers.freezePropertyReviewTarget(run,graph,property);
  assert.equal(target.runRevision,7);assert.equal(target.graphSnapshotId,"graph");assert.equal(target.subjectLabel,"药物A");
  assert.notEqual(target.property,property);
  property.raw_value="changed after click";
  assert.equal(target.property.raw_value,"1 mg");
  for (const status of ["running","queued","deleted","deleting","expired"]) {
    assert.equal(helpers.freezePropertyReviewTarget({...run,status},graph,property),null);
  }
  assert.equal(helpers.freezePropertyReviewTarget(run,{...graph,recognition_run_id:"another"},property),null);
  assert.equal(helpers.freezePropertyReviewTarget(run,{...graph,graph_snapshot:{snapshot_id:"old"}},property),null);
  assert.equal(helpers.freezePropertyReviewTarget(run,graph,{...property,revision:4}),null);
});

test("review head matches candidate and revision, never a same-labelled prior value",()=>{
  const head={candidate_id:"value",candidate_revision:3,revision:2,decision:"rejected"};
  assert.equal(helpers.propertyReviewHead([{...head,candidate_revision:2},head],property),head);
  assert.equal(helpers.propertyReviewHead([{...head,candidate_revision:2}],property),undefined);
});

test("HTTP-compatible idempotency keys need no randomUUID, and unresolved repair is not called fixed",()=>{
  const a=helpers.newPropertyReviewRequestKey(),b=helpers.newPropertyReviewRequestKey();
  assert.match(a,/^[a-f0-9]{32}$/);assert.notEqual(a,b);
  assert.match(helpers.propertyRepairStatus("completed"),/新结果仍待审核/);
  assert.match(helpers.propertyRepairStatus("unresolved"),/未决/);
  assert.match(helpers.propertyRepairStatus("failed"),/失败/);
  assert.match(helpers.propertyRepairStatus("cancelled"),/已取消/);
});

test("paused runs explain pending repairs without hiding already committed terminal results",()=>{
  for (const status of ["queued","running"]) {
    assert.equal(helpers.propertyRepairStatus(status,"paused"),"局部重识别已暂停，恢复运行后继续");
  }
  assert.equal(helpers.propertyRepairStatus("running","running"),"正在局部重识别");
  for (const terminal of ["completed","unresolved","failed","cancelled"]) {
    assert.equal(helpers.propertyRepairStatus(terminal,"paused"),helpers.propertyRepairStatus(terminal));
  }
});

test("repair reason codes explain local budgets, subject failures and missing replacements",()=>{
  for (const [reason_code,explanation] of [
    ["expert_repair_task_limit",/16 项任务上限/],
    ["expert_repair_model_call_budget_exhausted",/32 次模型请求额度已用完/],
    ["expert_subject_localization_required",/需人工核对原文中的正确主体/],
    ["expert_repair_no_replacement",/未形成通过原文核验的新候选/],
    ["expert_repair_execution_failed",/执行失败/],
    ["evidence_unresolved",/证据尚不充分/],
    ["recognition_model_not_configured",/模型尚未配置/],
  ]) assert.match(helpers.propertyRepairReason({reason_code,reason:reason_code}),explanation);
  assert.equal(helpers.propertyRepairReason({reason:"原文中的表头不完整"}),"原文中的表头不完整");
  assert.equal(helpers.propertyRepairReason({}),"");
  assert.match(helpers.propertyRepairReason({reason_code:"unexpected_error"}),/请核对运行状态或补充原文依据/);
});

for (const [method,endpoint] of [["getDocumentPropertyReviews","reviews"],["getDocumentPropertyRepairs","repairs"]]) {
  test(`${method} remains read-only and forwards cancellation`,async()=>{
    const controller=new AbortController();
    const api=load("../src/lib/api.ts",{process:{env:{}},Headers,fetch:async(url,options)=>{
      assert.equal(url,`/api/document-analysis/runs/run%2F1/${endpoint}`);
      assert.equal(options.method??"GET","GET");assert.equal(options.signal,controller.signal);
      return {ok:true,status:200,text:async()=>'{"items":[]}'};
    }});
    await api[method]("run/1",controller.signal);
  });
}

for (const [method,endpoint,body] of [
  ["reviewDocumentProperty","reviews",{request_key:"review-once",expected_run_revision:7,
    graph_snapshot_id:"graph",candidate_id:"value",candidate_revision:3,expected_review_revision:2,
    decision:"rejected",reason_code:"incorrect_value",reason:"原文单位不符"}],
  ["repairDocumentProperty","repairs",{request_key:"repair-once",expected_run_revision:8,review_id:"review-1"}],
]) {
  test(`${method} forwards frozen versions, reason and its own request key`,async()=>{
    const api=load("../src/lib/api.ts",{process:{env:{}},Headers,fetch:async(url,options)=>{
      assert.equal(url,`/api/document-analysis/runs/run/${endpoint}`);assert.equal(options.method,"POST");
      assert.deepEqual(JSON.parse(options.body),body);
      assert.equal(new Headers(options.headers).get("X-User"),"analyst");
      return {ok:true,status:200,text:async()=>"{}"};
    }});
    await api[method]("run",body);
  });
}
