// Real report workspace, Finder/PDE hooks and Tailwind; isolated HTTP fixtures only.
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { createServer } from "node:http";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const { build } = await import(process.env.ESBUILD_MODULE || "esbuild");
const { chromium, expect } = await import(process.env.PLAYWRIGHT_MODULE || "playwright/test");
const output = await mkdtemp(path.join(os.tmpdir(), "report-finder-review-"));
execFileSync(path.join(frontend, "node_modules/.bin/tailwindcss"),
  ["-i", "src/app/globals.css", "-o", path.join(output, "styles.css"), "--minify"], { cwd: frontend });
const css = await readFile(path.join(output, "styles.css"));
const entry = `
import {useState} from 'react';
import {createRoot} from 'react-dom/client';
import {QueryClient,QueryClientProvider} from '@tanstack/react-query';
import {ReportRecognitionWorkspace} from '@/components/reports/report-recognition-workspace';
import {DocumentActionsMenu} from '@/components/reports/document-actions-menu';
import {useIdentity} from '@/lib/use-identity';
function App(){
 const [doc,setDoc]=useState('a');
 const {identity,setIdentity}=useIdentity();
 return <div style={{height:'100vh',display:'flex',flexDirection:'column',padding:16,gap:16}}>
 <div style={{display:'flex',gap:20}}>
 <button onClick={()=>setDoc(doc==='a'?'b':'a')}>切换文档</button>
 <button onClick={()=>setIdentity({username:identity.username==='analyst'?'second':'analyst',role:'senior_analyst'})}>切换用户</button>
 <button onClick={()=>setIdentity({username:identity.username,role:identity.role==='qa'?'senior_analyst':'qa'})}>切换只读权限</button>
 <span data-testid='scope'>{doc+':'+identity.username+':'+identity.role}</span></div>
 <DocumentActionsMenu item={{key:doc,iri:'urn:doc:'+doc,kind:'uploaded-document',type:'CMC',title:'HRS-1597原料临床备样生产信息表_--脱敏-原 - PDE-冲突.docx'}} templateId='old-template'/>
 <ReportRecognitionWorkspace documentIri={'urn:doc:'+doc} templateId='old-template'/>
 </div>;
}
const client = new QueryClient({
 defaultOptions:{queries:{retry:false,refetchOnWindowFocus:false}}
});
window.fixtureRefetchArtifacts=()=>client.invalidateQueries({predicate:q=>['graph','source'].includes(q.queryKey[6])});
createRoot(document.getElementById('root')).render(<QueryClientProvider client={client}><App/></QueryClientProvider>);
`;
const bundle = await build({ stdin: { contents: entry, resolveDir: frontend, loader: "tsx" },
  bundle: true, write: false, platform: "browser", format: "iife", tsconfig: path.join(frontend, "tsconfig.json"),
  define: { "process.env.NODE_ENV": '"development"', "process.env.NEXT_PUBLIC_API_URL": '""' },
  plugins: [{ name: "next-navigation-fixture", setup(builder) {
    builder.onResolve({ filter: /^next\/link$/ }, () => ({ path: "link", namespace: "link-fixture" }));
    builder.onLoad({ filter: /.*/, namespace: "link-fixture" }, () => ({ contents:
      "import {createElement} from 'react';export default function Link({href,children,...props}){return createElement('a',{...props,href:typeof href==='string'?href:href.pathname+'?'+new URLSearchParams(href.query)},children);}", loader: "js", resolveDir: frontend }));
    builder.onResolve({ filter: /^next\/navigation$/ }, () => ({ path: "navigation", namespace: "fixture" }));
    builder.onLoad({ filter: /.*/, namespace: "fixture" }, () => ({ contents:
      "export const usePathname=()=>'/reports/doc';export const useSearchParams=()=>new URLSearchParams('template_id=old-template');export const useRouter=()=>({replace(){}});", loader: "js" }));
  } }],
});
const template = "latest-template";
const formula = "PDE = NOAEL × BW / (F1 × F2 × F3 × F4 × F5)";
const conflict = {
  conflict_key: "pde:hrs1597", summary: "原文与毒理推导跨越两个 OEB 等级。", delta_bands: 2,
  asserted: { pde_mg_day: 0.05, pde_ug_day: 50, band: 3 },
  derived: { band: 5, band_point: 5, pde_ug_day: 0.5, oel_ug_m3: 0.05,
    provisional: false, input_source: "extracted", provenance: { formula,
      factors: { F1_interspecies: 5, F2_intraindividual: 10, F3_duration: 2,
        F4_severity: 1, F5_loael: 10, BW_kg: 50 } } },
};
const requests = [], errors = [], heads = new Map(), decisions = new Map();
let rejectNext = false, holdNext = false, heldSave, holdDecisionRefresh = false, heldDecisionRefresh;
const identityKey = (owner, source) => `${owner}:${source}`;
const head = (owner, source) => {
  const key = identityKey(owner, source);
  if (!heads.has(key)) heads.set(key, { execution: `execution-${owner}-${source}-1`, sequence: 1 });
  return heads.get(key);
};
const decisionKey = (owner, source, execution) => `${owner}:${source}:${execution}`;
const decision = (owner, source, execution) => decisions.get(decisionKey(owner, source, execution)) || {
  job_id: `private-${owner}-${source}`, conflict_key: conflict.conflict_key,
  chosen: "pending", note: "", actor: "", version: 0, decided_at: null,
};
const saveDecision = (owner, source, execution, chosen) => {
  const saved = { ...decision(owner, source, execution), chosen, actor: owner,
    version: decision(owner, source, execution).version + 1, decided_at: "2026-09-15T12:00:00Z" };
  decisions.set(decisionKey(owner, source, execution), saved);
  return saved;
};
const metadata = (source, execution) => ({ template_id: template, source_job_id: source,
  execution_id: execution, document_hash: `hash-${source}`, parser_version: "4",
  structure_hash: `structure-${source}`, analysis_id: `analysis-${source}` });
const range = { anchor_block_id: "heading", start_block_id: "heading", end_block_id: "body" };
const sourceArtifact = (source, execution) => ({ ...metadata(source, execution), filename: `${source}.docx`,
  content: { type: "doc", content: [
    { type: "heading", attrs: { level: 1, sourceBlockId: "heading", sectionNodeId: "production" },
      content: [{ type: "text", text: `生产信息 ${source}` }] },
    { type: "paragraph", attrs: { sourceBlockId: "body" },
      content: [{ type: "text", text: "HRS-1597 原文 PDE 0.05 mg/日；毒理参数待人工核对。" }] },
  ] },
  section_tree: { node_id: "document", heading: "文档", level: 0, source_range: range,
    children: [{ node_id: "production", heading: "生产信息", level: 1, children: [], source_range: range }] },
  pagination: { status: "unavailable", total_pages: null }, warnings: [],
});
const graphArtifact = (source, execution) => ({ ...metadata(source, execution), counts: { nodes: 1, properties: 1 },
  doc_class: { doc_class_iri: "urn:CMCReport", label: "CMC 报告", score: 1, signals: [] },
  relationships: [{ subject_class_iri: "urn:CMCReport", subject_class_label: "CMC 报告", subject_text: source,
    predicate_iri: "urn:assessment", predicate_label: "共线评估", object_class_iri: "urn:Assessment",
    object_class_label: "共线风险", object_text: `PDE 评估 ${source}`, object_source: "document", source_ref: null,
    object_data_properties: [{ iri: "urn:pde", label: "原文 PDE", value: "0.05 mg/日" }],
    sub_relationships: [], conflict }],
});
const json = (response, value, status = 200) => {
  response.writeHead(status, { "Content-Type": "application/json" }); response.end(JSON.stringify(value));
};
let holdStatus = false, heldStatus;
const retiredRequests = [];
const reportSchema = JSON.parse(await readFile(new URL("./fixtures/reporting-browser.json", import.meta.url))).template;
const server = createServer(async (request, response) => {
  const url = new URL(request.url, "http://localhost");
  if (url.pathname === "/") { response.end('<html lang="zh"><head><link rel="stylesheet" href="/styles.css"></head><body><div id="root"></div><script src="/bundle.js"></script></body></html>'); return; }
  if (url.pathname === "/bundle.js") { response.setHeader("Content-Type", "application/javascript"); response.end(bundle.outputFiles[0].text); return; }
  if (url.pathname === "/styles.css") { response.setHeader("Content-Type", "text/css"); response.end(css); return; }
  if (url.pathname === "/favicon.ico") { response.end(); return; }
  let raw = ""; for await (const chunk of request) raw += chunk;
  const body = raw ? JSON.parse(raw) : null;
  const owner = request.headers["x-user"], role = request.headers["x-role"];
  requests.push({ path: url.pathname, method: request.method, owner, role, body,
    execution: url.searchParams.get("execution_id") });
  if (url.pathname === "/api/ast-templates/recognition-context") {
    const doc = url.searchParams.get("document_iri");
    const selected = { template_id: template, name: "风险评估文档V1版", version: "v2.6", schema_version: 2,
      recognition_mode: "finder_legacy", finder_profile_id: "cmc_baseline_v1" };
    json(response, { document_iri: doc, source_job_id: `source-${doc.split(":").at(-1)}`,
      selected, templates: [selected], selection_required: false, selection_locked: true }); return;
  }
  if (url.pathname === "/api/ast-templates/latest-template") {
    json(response, { id: template, name: "风险评估文档V1版", version: "v2.6", schema_json: reportSchema }); return;
  }
  const match = url.pathname.match(/^\/api\/ast-templates\/latest-template\/sources\/(source-[ab])\/finder(.*)$/);
  if (!match) { json(response, { detail: `unexpected ${url.pathname}` }, 404); return; }
  const source = match[1], part = match[2], current = head(owner, source);
  const requestedExecution = url.searchParams.get("execution_id");
  if (part && requestedExecution && requestedExecution !== current.execution) {
    retiredRequests.push({ source, execution: requestedExecution });
    json(response, { detail: { code: "RESULT_UNAVAILABLE", message: "当前执行结果不可用，请读取最新状态" } }, 409); return;
  }
  if (request.method === "POST" && role !== "senior_analyst") {
    json(response, { detail: "禁止写入" }, 403); return;
  }
  if (part === "/pde-conflict/decision") {
    const execution = url.searchParams.get("execution_id");
    if (execution !== current.execution) { json(response, { detail: { message: "执行已变化" } }, 409); return; }
    if (request.method === "GET") {
      if (holdDecisionRefresh) {
        holdDecisionRefresh = false;
        heldDecisionRefresh = () => json(response, decision(owner, source, execution));
        return;
      }
      json(response, decision(owner, source, execution)); return;
    }
    assert.equal(body.expected_version, decision(owner, source, execution).version);
    if (rejectNext) {
      rejectNext = false;
      const remote = saveDecision(owner, source, execution, "asserted");
      holdDecisionRefresh = true;
      json(response, { detail: { message: "审核版本已变化", current_version: remote.version } }, 409); return;
    }
    const save = () => json(response, saveDecision(owner, source, execution, body.chosen));
    if (holdNext) { holdNext = false; heldSave = save; return; }
    save(); return;
  }
  if (!part && request.method === "POST") {
    assert.equal(body.expected_execution_id, current.execution);
    assert.equal(typeof body.request_key, "string");
    const next = { sequence: current.sequence + 1, execution: `execution-${owner}-${source}-${current.sequence + 1}` };
    heads.set(identityKey(owner, source), next);
    holdStatus = true;
    json(response, { execution_id: next.execution, status: "queued", mode: "finder_legacy" }, 202); return;
  }
  if (!part) {
    const send = () => json(response, { ...metadata(source, current.execution), mode: "finder_legacy", status: "completed",
    stage: "completed", input: { source_hash: `hash-${source}` }, has_result: true,
    stale: false, error: null, counts: { nodes: 1, properties: 1 } });
    if (holdStatus) heldStatus = send; else send(); return;
  }
  if (part === "/source") { json(response, sourceArtifact(source, current.execution)); return; }
  if (part === "/graph") { json(response, graphArtifact(source, current.execution)); return; }
  json(response, { detail: `unexpected Finder route ${part}` }, 404);
});
await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
const browser = await chromium.launch({ executablePath: process.env.DOCUMENT_BROWSER_CHROME || "/usr/bin/google-chrome", args: ["--no-sandbox"] });
try {
  const page = await browser.newPage({ viewport: { width: 1700, height: 1100 } });
  page.on("pageerror", error => { errors.push(error.message); console.error(error.stack); });
  const decisionPosts = () => requests.filter(row => row.method === "POST" && row.path.endsWith("/pde-conflict/decision"));
  const runPosts = () => requests.filter(row => row.method === "POST" && row.path.endsWith("/finder"));
  const chosen = (name) => (name === "重新识别" ? page.locator('[aria-label="关系图谱"]') : page).getByRole("button", { name, exact: true });
  const derived = () => chosen("采纳推导（band 5）");
  const asserted = () => chosen("采纳原文（band 3）");
  const pending = () => chosen("待复核");
  const expectCurrent = (text) => expect(page.getByText(new RegExp(`^当前：${text}`))).toBeVisible();
  await page.goto(`http://127.0.0.1:${server.address().port}`);
  const selector = page.getByRole("combobox", { name: "模板上下文" });
  await expect(selector).toHaveValue(template);
  await expect(selector.locator("option")).toHaveCount(1);
  await expect(selector.locator("option")).toHaveText("风险评估文档V1版 · v2.6 · 本体指引1.0");
  await expect(selector.locator('option[value=""]')).toHaveCount(0);
  await expect(derived()).toBeEnabled();
  await expect(asserted()).toBeEnabled();
  await expect(pending()).toBeEnabled();
  assert.equal(decisionPosts().length, 0);
  assert.equal(runPosts().length, 0, "opening the report must only read existing results");
  for (const title of ["目录", "文档预览", "关系图谱"]) {
    await expect(page.getByText(title, { exact: true }).first()).toBeVisible();
  }
  const graphCard = page.locator('[aria-label="关系图谱"]');
  const separator = page.getByRole("separator", { name: "调整关系图谱宽度" });
  await expect(separator).toHaveAttribute("aria-valuenow", "300");
  const graphBox = await graphCard.boundingBox();
  const docBox = await page.locator(".tiptap").boundingBox();
  const outlineBox = await page.getByRole("tree", { name: "文档章节" }).boundingBox();
  assert(outlineBox.x < docBox.x && docBox.x < graphBox.x, "directory, document and graph must form three ordered panes");
  await separator.focus(); await separator.press("ArrowLeft");
  await expect(separator).toHaveAttribute("aria-valuenow", "320");
  await expect.poll(async () => Math.round((await graphCard.boundingBox()).width)).toBe(320);
  await separator.press("ArrowRight");
  const handle = await separator.boundingBox();
  await page.mouse.move(handle.x + handle.width / 2, handle.y + 25);
  await page.mouse.down(); await page.mouse.move(handle.x - 60, handle.y + 25); await page.mouse.up();
  assert(Number(await separator.getAttribute("aria-valuenow")) > 350);
  await separator.dblclick();
  await expect(separator).toHaveAttribute("aria-valuenow", "300");
  const banner = graphCard.locator("div.bg-amber-50").filter({ hasText: "PDE 潜能等级冲突" }).first();
  await expect(banner).toBeVisible();
  assert.equal(await banner.evaluate(node => getComputedStyle(node).backgroundColor), "rgb(255, 251, 235)");
  await expect(graphCard.getByText("PDE 评估 source-a", { exact: true })).toBeVisible();
  await chosen("推导依据（F1–F5 / 公式）").click();
  await expect(page.getByText(formula, { exact: true })).toBeVisible();
  for (const label of ["F1 种属外推", "F2 个体差异", "F3 暴露周期", "F4 严重度", "F5 LOAEL 外推"]) {
    await expect(page.getByText(label, { exact: true })).toBeVisible();
  }
  await derived().click(); await expectCurrent("采纳推导");
  assert.deepEqual(decisionPosts().at(-1).body, { chosen: "derived", expected_version: 0 });
  assert.equal(decisionPosts().at(-1).execution, "execution-analyst-source-a-1");
  await page.reload(); await expectCurrent("采纳推导");
  assert.equal(decisionPosts().length, 1, "refresh must reload the saved choice without writing");
  rejectNext = true;
  await asserted().click();
  await expect.poll(() => !!heldDecisionRefresh).toBe(true);
  await expect(derived()).toBeDisabled();
  await expect(asserted()).toBeDisabled();
  await expect(pending()).toBeDisabled();
  heldDecisionRefresh(); heldDecisionRefresh = undefined;
  await expect(page.getByRole("alert")).toContainText("已刷新当前状态");
  await expectCurrent("采纳原文");
  await derived().click(); await expectCurrent("采纳推导");
  assert.deepEqual(decisionPosts().at(-1).body, { chosen: "derived", expected_version: 2 });
  await expect(page.getByRole("alert")).toHaveCount(0);
  await pending().click(); await expectCurrent("待复核");
  assert.deepEqual(decisionPosts().at(-1).body, { chosen: "pending", expected_version: 3 });
  await chosen("操作").click();
  await page.getByRole("menuitem", { name: "生成风险评估报告" }).click();
  const drawer = page.getByRole("dialog", { name: "风险评估报告", exact: true });
  await expect(drawer.getByRole("button", { name: "开始生成", exact: true })).toBeEnabled();
  await drawer.getByRole("button", { name: "重新读取与校验", exact: true }).click();
  await expect.poll(() => runPosts().length).toBe(1);
  await expect.poll(() => !!heldStatus).toBe(true);
  await expect(drawer.getByRole("button", { name: "开始生成", exact: true })).toBeDisabled();
  assert.deepEqual(retiredRequests, [], "reread must publish the new head before any artifact refetch");
  holdStatus = false; heldStatus(); heldStatus = undefined;
  await expect(drawer.getByText("已重新读取并校验关系图谱。", { exact: true })).toBeVisible();
  await expect(drawer.getByRole("button", { name: "开始生成", exact: true })).toBeEnabled();
  await expect(drawer.getByRole("alert")).toHaveCount(0);
  await drawer.getByRole("button", { name: "关闭", exact: true }).click();
  await expect(derived()).toBeEnabled();
  await expect(page.getByText(/^当前：/)).toHaveCount(0);
  assert.equal(head("analyst", "source-a").execution, "execution-analyst-source-a-2");
  assert.equal(decision("analyst", "source-a", "execution-analyst-source-a-2").chosen, "pending");
  assert.equal(decisionPosts().length, 4, "rerunning must not copy or automatically write a decision");
  // A result replaced in another tab is recovered by reading the head, never by starting work.
  heads.set(identityKey("analyst", "source-a"), { sequence: 3, execution: "execution-analyst-source-a-3" });
  await page.evaluate(() => window.fixtureRefetchArtifacts());
  await expect.poll(() => requests.some(r => r.path.endsWith("/graph") && r.execution === "execution-analyst-source-a-3")).toBe(true);
  await expect(derived()).toBeEnabled();
  await expect(page.getByRole("alert")).toHaveCount(0);
  assert.equal(runPosts().length, 1, "recovering an obsolete result must only read the current head");
  holdNext = true;
  await derived().click(); await expect.poll(() => !!heldSave).toBe(true);
  await chosen("切换文档").click();
  await expect(page.getByTestId("scope")).toHaveText("b:analyst:senior_analyst");
  await expect(graphCard.getByText("PDE 评估 source-b", { exact: true })).toBeVisible();
  await expect(derived()).toBeEnabled();
  heldSave(); heldSave = undefined;
  await expect(page.getByText(/^当前：/)).toHaveCount(0);
  await asserted().click(); await expectCurrent("采纳原文");
  assert.deepEqual(decisionPosts().at(-1).body, { chosen: "asserted", expected_version: 0 });
  assert.equal(decisionPosts().at(-1).execution, "execution-analyst-source-b-1");
  holdNext = true;
  await derived().click(); await expect.poll(() => !!heldSave).toBe(true);
  await chosen("切换用户").click();
  await expect(page.getByTestId("scope")).toHaveText("b:second:senior_analyst");
  await expect(derived()).toBeEnabled();
  heldSave(); heldSave = undefined;
  await expect(page.getByText(/^当前：/)).toHaveCount(0);
  await asserted().click(); await expectCurrent("采纳原文");
  assert.equal(decisionPosts().at(-1).owner, "second");
  assert.equal(decisionPosts().at(-1).execution, "execution-second-source-b-1");
  assert.deepEqual(decisionPosts().at(-1).body, { chosen: "asserted", expected_version: 0 });
  const writes = requests.filter(row => row.method === "POST").length;
  await chosen("切换只读权限").click();
  await expect(page.getByTestId("scope")).toHaveText("b:second:qa");
  await expect(banner).toBeVisible();
  await expect(derived()).toHaveCount(0);
  await expect(asserted()).toHaveCount(0);
  await expect(pending()).toHaveCount(0);
  await expect(chosen("重新识别")).toBeDisabled();
  assert.equal(requests.filter(row => row.method === "POST").length, writes);
  assert(requests.every(row => row.path.startsWith("/api/ast-templates/")));
  assert.deepEqual(errors, []);
  console.log("Report Finder browser checks passed: pinned latest context, three panes/resize, inline PDE provenance, three choices, CAS/reload/retry, rerun and document/user/role isolation; HTTP fixtures only.");
} finally {
  heldSave?.();
  heldDecisionRefresh?.();
  heldStatus?.();
  await browser.close();
  await new Promise(resolve => server.close(resolve));
  await rm(output, { recursive: true, force: true });
}
