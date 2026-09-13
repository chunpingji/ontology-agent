// Actual report graph tree, review drawer and Query hooks with isolated HTTP fixtures.
import assert from "node:assert/strict";
import { createServer } from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";

const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const { build } = await import(process.env.ESBUILD_MODULE || "esbuild");
const { chromium, expect } = await import(process.env.PLAYWRIGHT_MODULE || "playwright/test");
const entry = `
import { useState } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useReportDocumentRun } from "@/components/analysis/use-template-document-run";
import { TemplateDocumentGraphPanel } from "@/components/analysis/template-document-graph-panel";
function App() {
  const [caller, setCaller] = useState("analyst");
  const [document, setDocument] = useState("a");
  const model = useReportDocumentRun("urn:document:" + document);
  return <><button onClick={model.refresh}>同步状态</button>
    <button onClick={() => { const next = caller === "analyst" ? "operator" : "analyst";
      localStorage.setItem("slpra.identity", JSON.stringify({username:next, role:next === "analyst" ? "senior_analyst" : "operator"}));
      setCaller(next); }}>切换身份</button>
    <button onClick={() => setDocument(document === "a" ? "b" : "a")}>切换文档</button>
    <TemplateDocumentGraphPanel model={model} /></>;
}
const client = new QueryClient({defaultOptions:{queries:{retry:false}}});
createRoot(document.getElementById("root")).render(<QueryClientProvider client={client}><App /></QueryClientProvider>);
`;
const bundle = await build({ stdin: { contents: entry, resolveDir: frontend, loader: "tsx" },
  bundle: true, write: false, platform: "browser", format: "iife", tsconfig: path.join(frontend, "tsconfig.json"),
  define: { "process.env.NODE_ENV": '"development"', "process.env.NEXT_PUBLIC_API_URL": '""' } });
const requests = [], errors = [], reviews = [], operations = [], receipts = new Map();
let revision = 5, candidateRevision = 1, status = "paused", failRepair = true, loseReviewAck = false;
const now = "2026-09-13T10:00:00Z";
const head = () => reviews.filter((item) => item.candidate_revision === candidateRevision).at(-1);
const run = (id = "a") => ({ recognition_run_id: `run-${id}`, run_revision: revision, event_head: revision,
  artifact_revision: revision, status, ranking_budget_enabled: true,
  identities: { graph_snapshot_id: `graph-${id}-${revision}` },
  artifacts: { structure: "pending", graph: "ready" },
  progress: { tasks_attempted: 2, model_calls: 2, records_planned: 2, records_examined: 2,
    records_incomplete: 0, records_unattempted: 0 },
  available_actions: status === "running" ? ["pause"] : status === "paused" ? ["resume"] : [] });
const graph = (id, projection) => ({ recognition_run_id: `run-${id}`, projection,
  graph_snapshot: { snapshot_id: `graph-${id}-${revision}`, root_ref: { entity_id: "root", revision: 1 } },
  entities: [{ entity_id: "root", revision: 1, label: `药物 ${id}`, class_label: "药物",
    source_selection_refs: [], predicate_menu: [{kind:"property",predicate_iri:"urn:pde",predicate_label:"PDE"}] }],
  relationships: [], properties: (projection === "effective_affirmed" && head()?.decision === "rejected") ? [] : [{
    candidate_id: "candidate-pde", revision: candidateRevision, subject_ref: {entity_id:"root",revision:1},
    predicate_iri: "urn:pde", predicate_label: "PDE", raw_value: `${candidateRevision} mg`, normalized_value: candidateRevision,
    unit: "mg", structural_valid: true, model_supported: true, policy_eligible: true,
    independent_review: head()?.decision ?? "unreviewed", polarity: "affirmed", invalidated: false,
    source_selection_refs: { value:[],subject:[],object:[],predicate_bridge:[],unit:[],condition:[],counterevidence:[] },
  }], coverage: { subjects: [] }, unresolved: { undetermined: 0, not_checked: 0 } });
const json = (response, payload, code = 200) => {
  response.writeHead(code, {"Content-Type":"application/json"}); response.end(JSON.stringify(payload));
};
const server = createServer(async (request, response) => {
  const url = new URL(request.url, "http://localhost");
  if (url.pathname === "/") { response.end('<html><body><div id="root"></div><script src="/bundle.js"></script></body></html>'); return; }
  if (url.pathname === "/bundle.js") { response.setHeader("Content-Type","application/javascript"); response.end(bundle.outputFiles[0].text); return; }
  let raw = "";
  for await (const chunk of request) raw += chunk;
  const body = raw ? JSON.parse(raw) : null;
  requests.push({path:url.pathname, method:request.method, body, caller:request.headers["x-user"]});
  if (url.pathname === "/api/document-analysis/documents/runs") {
    json(response, {run:run(url.searchParams.get("document_iri").slice(-1))}); return;
  }
  const [, id, resource] = url.pathname.match(/\/runs\/run-([^/]+)\/(.+)/) ?? [];
  const allowed = request.headers["x-role"] !== "operator" && !["running","queued"].includes(status);
  if (resource === "graph") json(response, graph(id, url.searchParams.get("projection")));
  else if (resource === "ranking-summary") json(response, {cost:{model_calls:0}});
  else if (resource === "events") { response.writeHead(200,{"Content-Type":"text/event-stream"}); response.write(": ready\n\n"); }
  else if (resource === "reviews" && request.method === "GET") json(response, {
    run_revision:revision,items:reviews,heads:head() ? [head()] : [],can_review:allowed,can_repair:allowed,
  });
  else if (resource === "repairs" && request.method === "GET") json(response, {run_revision:revision,items:operations,can_repair:allowed});
  else if (resource === "reviews" && request.method === "POST") {
    if (receipts.has(body.request_key)) { json(response,receipts.get(body.request_key),201); return; }
    if (!allowed) { json(response,{detail:"没有审核权限"},403); return; }
    if (body.expected_run_revision !== revision || body.candidate_revision !== candidateRevision
      || body.expected_review_revision !== (head()?.revision ?? 0)) {
      json(response,{detail:{message:"审核目标版本已变化",current_revision:revision}},409); return;
    }
    const item = {...body,review_id:`review-${reviews.length+1}`,revision:(head()?.revision ?? 0)+1,
      author:request.headers["x-user"],author_role:request.headers["x-role"],created_at:now};
    reviews.push(item); revision++;
    const receipt = {review:item,run:run(id)};
    receipts.set(body.request_key,receipt);
    if (loseReviewAck) { loseReviewAck=false; json(response,{detail:"审核回执丢失，请重试"},503); }
    else json(response,receipt,201);
  } else if (resource === "repairs" && request.method === "POST") {
    if (receipts.has(body.request_key)) { json(response,receipts.get(body.request_key),202); return; }
    if (failRepair) { failRepair=false; json(response,{detail:"修复服务暂不可用"},503); return; }
    assert.equal(body.review_id,head().review_id); assert.equal(body.expected_run_revision,revision);
    status="queued"; revision++;
    const operation={operation_id:"repair-1",review_id:body.review_id,candidate_id:"candidate-pde",
      candidate_revision:candidateRevision,status:"queued",subject_ref:{entity_id:"root",revision:1},predicate_iri:"urn:pde",
      record_ids:["record-1"],max_tasks:16,max_model_calls:32,result:{},created_at:now,updated_at:now};
    operations.unshift(operation);
    const receipt={operation,run:run(id)};receipts.set(body.request_key,receipt);json(response,receipt,202);
  } else if (resource === "pause" && request.method === "POST") {
    status="paused"; revision++;json(response,run(id));
  } else { json(response,{detail:"fixture route not found"},404); }
});
await new Promise((resolve)=>server.listen(0,"127.0.0.1",resolve));
const browser=await chromium.launch({executablePath:process.env.DOCUMENT_BROWSER_CHROME || "/usr/bin/google-chrome",args:["--no-sandbox"]});
const page=await browser.newPage();
await page.addInitScript(()=>Object.defineProperty(crypto,"randomUUID",{value:undefined}));
page.on("pageerror",(error)=>errors.push(error.message));
const posts=(resource)=>requests.filter((item)=>item.method === "POST" && item.path.endsWith(resource));
const open=()=>page.getByRole("button",{name:"专家审核",exact:true}).click();
try {
  await page.goto(`http://127.0.0.1:${server.address().port}`);
  await expect(page.getByRole("button",{name:"专家审核",exact:true})).toBeVisible();
  assert.equal(requests.filter((item)=>item.path.endsWith("/reviews")).length,0);
  assert.equal(posts("/reviews").length,0);
  await open();
  await expect(page.getByRole("button",{name:"驳回并局部重识别",exact:true})).toBeDisabled();
  await expect(page.getByLabel("审核理由（驳回必填）")).toHaveAttribute("maxlength","4000");
  await page.getByLabel("审核理由（驳回必填）").fill("单位与原文表头不符");
  await page.getByRole("button",{name:"驳回并局部重识别",exact:true}).click();
  await expect(page.getByRole("alert")).toContainText("驳回已保存，局部重识别尚未确认启动");
  assert.equal(reviews.length,1); assert.equal(posts("/reviews").length,1);
  await page.getByRole("button",{name:"重试局部重识别",exact:true}).click();
  await expect(page.getByText("局部重识别已排队",{exact:true})).toBeVisible();
  assert.equal(posts("/reviews").length,1); assert.equal(posts("/repairs").length,2);
  assert.equal(posts("/repairs")[0].body.request_key,posts("/repairs")[1].body.request_key);
  assert.equal(posts("/repairs")[1].body.expected_run_revision,posts("/reviews")[0].body.expected_run_revision+1);
  status="finished"; revision++; operations[0].status="unresolved"; operations[0].result={reason:"原文不足以判定",replacement_candidate_refs:[]};
  await expect(page.getByText("局部重识别未决，待补证或人工处理",{exact:true})).toBeVisible();
  await page.keyboard.press("Escape");
  await page.getByRole("button",{name:"同步状态",exact:true}).click();
  await page.getByLabel("图谱结果范围").selectOption("rejected");
  await open();
  await expect(page.getByText("理由：单位与原文表头不符",{exact:true})).toBeVisible();
  loseReviewAck=true;
  await page.getByRole("button",{name:"确认属性",exact:true}).click();
  await expect(page.getByRole("button",{name:"重试原审核提交",exact:true})).toBeVisible();
  await page.getByRole("button",{name:"重试原审核提交",exact:true}).click();
  await expect(page.getByText("人工确认已保存；系统证明资格保持原判定。",{exact:true})).toBeVisible();
  assert.equal(reviews.length,2); assert.equal(posts("/reviews").length,3);
  assert.equal(reviews[1].reason_code,"other");
  assert.equal(posts("/reviews")[1].body.request_key,posts("/reviews")[2].body.request_key);
  assert.equal(posts("/repairs").length,2,"confirmation must never start repair");
  await page.keyboard.press("Escape");
  await page.getByLabel("图谱结果范围").selectOption("all_candidates");
  await open();
  await page.getByLabel("审核理由（驳回必填）").fill("必须保留的版本冲突草稿");
  candidateRevision++; revision++;
  await page.getByRole("button",{name:"驳回属性",exact:true}).click();
  await expect(page.getByLabel("审核理由（驳回必填）")).toHaveValue("必须保留的版本冲突草稿");
  await expect(page.getByText(/理由草稿已保留/)).toBeVisible();
  assert.equal(reviews.length,2);
  await page.keyboard.press("Escape");
  status="running";revision++;
  await page.getByRole("button",{name:"同步状态",exact:true}).click();
  await page.getByRole("button",{name:"暂停以审核",exact:true}).click();
  await expect(page.getByRole("button",{name:"专家审核",exact:true})).toBeVisible();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  assert.equal(posts("/pause").length,1);
  await open();
  await expect(page.getByText("原始值：2 mg",{exact:true})).toBeVisible();
  await page.keyboard.press("Escape");
  await page.getByRole("button",{name:"切换身份",exact:true}).click();
  await open();
  await expect(page.getByRole("button",{name:"确认属性",exact:true})).toBeDisabled();
  await expect(page.getByLabel("审核理由（驳回必填）")).toHaveValue("");
  await page.keyboard.press("Escape");
  await page.getByRole("button",{name:"切换文档",exact:true}).click();
  await expect(page.getByText("药物 b",{exact:false})).toBeVisible();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  assert.deepEqual(errors,[]);
  console.log(JSON.stringify({ok:true,checks:["lazy reads","required rejection reason","partial failure retry",
    "independent idempotency","confirmation without model","stale draft retention","pause before freeze",
    "rejected projection","repair unresolved","role and document isolation","HTTP random key compatibility"],
    review_posts:posts("/reviews").length,repair_posts:posts("/repairs").length}));
} finally {
  await browser.close();server.closeAllConnections();await new Promise((resolve)=>server.close(resolve));
}
