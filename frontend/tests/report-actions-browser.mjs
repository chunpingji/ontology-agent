// Real report header/menu/Sheet/DOCX preview. Supports a pytest-owned isolated API.
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
const { template: schema } = JSON.parse(await readFile(new URL("./fixtures/reporting-browser.json", import.meta.url)));
const title = "HRS-1597原料临床备样生产信息表_--脱敏-原 - PDE-冲突.docx";
const integration = JSON.parse(process.env.REPORT_ACTIONS_INTEGRATION || "null");
const output = await mkdtemp(path.join(os.tmpdir(), "report-actions-"));
execFileSync(path.join(frontend, "node_modules/.bin/tailwindcss"),
  ["-i", "src/app/globals.css", "-o", path.join(output, "styles.css"), "--minify"], { cwd: frontend });
const css = await readFile(path.join(output, "styles.css"));
const docx = execFileSync(path.join(frontend, "../backend/.venv/bin/python"), ["-c", "from docx import Document; from io import BytesIO; import sys; d=Document(); d.add_paragraph('风险评估报告合成正文'); b=BytesIO(); d.save(b); sys.stdout.buffer.write(b.getvalue())"]);
const entry = `
import {useState} from 'react';
import {createRoot} from 'react-dom/client';
import {QueryClient,QueryClientProvider} from '@tanstack/react-query';
import {DocumentActionsMenu} from '@/components/reports/document-actions-menu';
import {ReportRecognitionWorkspace} from '@/components/reports/report-recognition-workspace';
import {useIdentity} from '@/lib/use-identity';
function App(){
 const [doc,setDoc]=useState(${JSON.stringify(integration?.source || "hrs1597")});const [template,setTemplate]=useState(${JSON.stringify(integration?.template || "old-template")});
 const {setIdentity}=useIdentity();
 window.fixtureControls={setDoc,setTemplate,setIdentity};
 return <div className='flex h-screen flex-col'><header className='flex justify-end p-4'><DocumentActionsMenu templateId={template}
 item={{key:doc,iri:'urn:'+doc,kind:'uploaded-document',title:${JSON.stringify(title)},type:'CMC'}}/></header>
 ${integration ? "<ReportRecognitionWorkspace documentIri={'urn:'+doc} templateId={template}/>" : ""}</div>;
}
createRoot(document.getElementById('root')).render(<QueryClientProvider client={new QueryClient({
 defaultOptions:{queries:{retry:false,refetchOnWindowFocus:false}}
})}><App/></QueryClientProvider>);
`;
const bundle = await build({ stdin: { contents: entry, resolveDir: frontend, loader: "tsx" },
  bundle: true, write: false, platform: "browser", format: "iife", tsconfig: path.join(frontend, "tsconfig.json"),
  define: { "process.env.NODE_ENV": '"development"', "process.env.NEXT_PUBLIC_API_URL": '""' },
  plugins: [{ name: "next-fixture", setup(builder) {
    builder.onResolve({ filter: /^next\/link$/ }, () => ({ path: "link", namespace: "link-fixture" }));
    builder.onLoad({ filter: /.*/, namespace: "link-fixture" }, () => ({ contents:
      "import {createElement} from 'react';export default function Link({href,children,...props}){return createElement('a',{...props,href:typeof href==='string'?href:href.pathname+'?'+new URLSearchParams(href.query)},children);}", loader: "js", resolveDir: frontend }));
    builder.onResolve({ filter: /^next\/navigation$/ }, () => ({ path: "navigation", namespace: "fixture" }));
    builder.onLoad({ filter: /.*/, namespace: "fixture" }, () => ({ contents:
      "export const usePathname=()=>'/reports/doc';export const useSearchParams=()=>new URLSearchParams();export const useRouter=()=>({replace(){}});", loader: "js" }));
  } }],
});
const requests = [], errors = [], held = [], runs = new Map();
let failTemplate = true, holdTemplate = false, completed = false, failGenerate = true, holdGenerate = true;
let readCount = 0, readComplete = true;
const ast = { node_id: "doc", kind: "document", children: [{ node_id: "p", kind: "paragraph", children: [
  { node_id: "text", kind: "text", text: "风险评估报告合成正文" },
] }] };
const json = (response, data, status = 200) => {
  response.writeHead(status, { "Content-Type": "application/json" }); response.end(JSON.stringify(data));
};
const server = createServer(async (request, response) => {
  const url = new URL(request.url, "http://localhost"), pathname = url.pathname;
  if (pathname === "/") { response.end('<html><head><link rel="stylesheet" href="/styles.css"></head><body><div id="root"></div><script src="/bundle.js"></script></body></html>'); return; }
  if (pathname === "/bundle.js") { response.setHeader("Content-Type", "application/javascript"); response.end(bundle.outputFiles[0].text); return; }
  if (pathname === "/styles.css") { response.setHeader("Content-Type", "text/css"); response.end(css); return; }
  if (pathname === "/favicon.ico") { response.writeHead(204); response.end(); return; }
  let raw = ""; for await (const chunk of request) raw += chunk;
  const body = raw ? JSON.parse(raw) : null, owner = request.headers["x-user"];
  requests.push({ pathname, method: request.method, body, owner });
  if (integration) {
    const upstream = await fetch(integration.origin + request.url, { method: request.method,
      headers: { "Content-Type": "application/json", "X-User": owner, "X-Role": request.headers["x-role"] },
      ...(raw ? { body: raw } : {}),
    });
    response.writeHead(upstream.status, { "Content-Type": upstream.headers.get("content-type") || "application/json" });
    response.end(Buffer.from(await upstream.arrayBuffer())); return;
  }
  if (pathname === "/api/ast-templates/recognition-context") {
    const selected = { template_id: url.searchParams.get("template_id") === "old-template" ? "latest-template" : url.searchParams.get("template_id"),
      name: "风险评估文档V1版", version: "v2.6", schema_version: 2, recognition_mode: "finder_legacy" };
    json(response, { selected, templates: [selected], selection_locked: true, selection_required: false,
      document_iri: url.searchParams.get("document_iri"), source_job_id: url.searchParams.get("document_iri").slice(4) }); return;
  }
  const templateMatch = pathname.match(/^\/api\/ast-templates\/([^/]+)$/);
  if (templateMatch) {
    if (failTemplate) { failTemplate = false; json(response, { detail: "模板读取暂时失败" }, 503); return; }
    const data = { id: templateMatch[1], name: templateMatch[1] === "latest-template" ? "风险评估文档V1版" : "切换后的模板", version: "v2.6",
      schema_json: schema, schema_hash: templateMatch[1], default_source_job_id: "hrs5678" };
    if (holdTemplate) held.push(() => json(response, data)); else json(response, data);
    return;
  }
  const finderMatch = pathname.match(/^\/api\/ast-templates\/([^/]+)\/sources\/([^/]+)\/finder(.*)$/);
  if (finderMatch && request.method === "POST") {
    readCount++; readComplete = false;
    json(response, { status: "queued", execution_id: "reread-" + readCount }, 202); return;
  }
  if (finderMatch && request.method === "GET") {
    const [, templateId, source, suffix] = finderMatch;
    if (suffix === "/source") json(response, { content: { type: "doc", content: [{ type: "paragraph", content: [{ type: "text", text: "当前 HRS-1597 合成原文" }] }] } });
    else json(response, { execution_id: readCount ? "reread-" + readCount : [owner, templateId, source].join(":"), template_id: templateId, source_job_id: source,
      status: readComplete ? "completed" : "running", has_result: readComplete, stale: false });
    return;
  }
  if (pathname === "/api/extraction/jobs") {
    json(response, [{ id: "hrs1597", source_filename: title }, { id: "hrs5678", source_filename: "另一份模板默认源.docx" }, { id: "other-source", source_filename: "另一份报告.docx" }]); return;
  }
  if (pathname.endsWith("/reports")) { json(response, []); return; }
  if (pathname === "/api/report-previews" && request.method === "POST") {
    if (failGenerate) { failGenerate = false; json(response, { detail: "生成请求暂时失败" }, 503); return; }
    const id = "run-" + (runs.size + 1);
    runs.set(id, { body, owner });
    const send = () => json(response, { run_id: id, execution_status: "running", material_status: "pending", attempt: 1, revision_no: 1, artifacts: [] });
    if (holdGenerate) held.push(send); else send(); return;
  }
  const runMatch = pathname.match(/^\/api\/report-runs\/([^/]+)(.*)$/);
  if (runMatch && runs.has(runMatch[1])) {
    const [, id, suffix] = runMatch, frozen = runs.get(id), source = frozen.body.source_bindings.doc;
    if (suffix === "/inputs") json(response, { input_snapshot_id: "snapshot-" + id, material_status: "ready", coverage: [], blocking_issues: [],
      source_bundle: { template: schema, sources: { doc: { ...source, kind: "finder_demo", template_id: frozen.body.template_id, execution_id: source.finder_execution_id } } } });
    else if (suffix === "/outputs") json(response, []);
    else if (suffix === "/artifacts/draft") { response.setHeader("Content-Type", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"); response.end(docx); }
    else json(response, { run_id: id, execution_status: completed ? "completed" : "running", material_status: completed ? "ready" : "pending",
      input_snapshot_id: completed ? "snapshot-" + id : null, demonstration: true, attempt: 1, revision_no: 1,
      body_ast: completed ? ast : null, artifacts: completed ? [{ artifact_id: "draft", format: "docx", purpose: "draft" }] : [] });
    return;
  }
  errors.push("Unexpected API: " + request.method + " " + pathname);
  json(response, { detail: "Unexpected API" }, 404);
});
await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
const browser = await chromium.launch({ executablePath: process.env.DOCUMENT_BROWSER_CHROME || "/usr/bin/google-chrome", args: ["--no-sandbox"] });
try {
  const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
  page.on("pageerror", error => errors.push(error.message));
  await page.goto("http://127.0.0.1:" + server.address().port);
  const menu = page.getByRole("button", { name: "操作", exact: true });
  const drawer = page.getByRole("dialog", { name: "风险评估报告", exact: true });
  async function open() {
    await menu.click();
    await page.getByRole("menuitem", { name: "生成风险评估报告" }).click();
    await expect(drawer).toBeVisible();
  }
  await expect(menu).toBeVisible();
  assert.equal(requests.filter(r => r.pathname === "/api/ast-templates/latest-template").length, 0, "schema is loaded only when the drawer opens");
  await open();
  if (!integration) {
  await expect(drawer.getByRole("alert")).toContainText("模板读取暂时失败");
  await drawer.getByRole("button", { name: "重新加载模板" }).click();
  }
  if (integration && !integration.initialResult) await expect(drawer.getByRole("button", { name: "开始生成", exact: true })).toBeDisabled();
  else await expect(drawer.getByRole("button", { name: "开始生成", exact: true })).toBeEnabled();
  await expect(drawer.getByText("生成计划", { exact: true })).toBeVisible();
  await expect(drawer.getByText("0%", { exact: true })).toBeVisible();
  for (const name of ["读取与校验数据", "匹配报告模板", "风险识别与评估", "生成报告文档", "保存生成结果"]) await expect(drawer.getByText(name, { exact: true })).toBeVisible();
  const bounds = await drawer.boundingBox(), startBounds = await drawer.getByRole("button", { name: "开始生成", exact: true }).boundingBox();
  assert.equal(bounds.width, 576, "original narrow drawer width");
  assert(startBounds.y > 900, "start button stays in the bottom footer");
  if (process.env.REPORT_ACTIONS_SCREENSHOT) await drawer.screenshot({ path: process.env.REPORT_ACTIONS_SCREENSHOT });
  assert.equal(requests.filter(r => r.method === "POST").length, 0, "opening/retrying must not generate or re-identify");
  await drawer.getByRole("button", { name: "重新读取与校验", exact: true }).click();
  if (!integration) {
    await expect(drawer.getByRole("button", { name: "开始生成", exact: true })).toBeDisabled();
    readComplete = true;
  }
  await expect(drawer.getByText("已重新读取并校验关系图谱。", { exact: true })).toBeVisible({ timeout: 20000 });
  await drawer.getByRole("button", { name: "开始生成", exact: true }).click();
  if (!integration) {
    await expect(drawer.getByRole("alert")).toContainText("生成请求暂时失败");
    await drawer.getByRole("button", { name: "重试生成", exact: true }).click();
    await expect.poll(() => held.length).toBe(1);
    await expect(drawer.getByRole("button", { name: "生成中", exact: true })).toBeDisabled();
    const posts = requests.filter(r => r.pathname === "/api/report-previews");
    assert.equal(posts[0].body.idempotency_key, posts[1].body.idempotency_key, "uncertain retries reuse the request key");
    await drawer.getByRole("button", { name: "关闭", exact: true }).click();
    await expect(drawer).toHaveCount(0);
    for (const release of held.splice(0)) release(); holdGenerate = false;
    await open();
    await expect(drawer.getByRole("button", { name: "生成中", exact: true })).toBeDisabled();
  }
  await expect.poll(() => requests.some(r => r.pathname === "/api/report-previews")).toBe(true);
  const post = requests.find(r => r.pathname === "/api/report-previews");
  assert.equal(post.pathname, "/api/report-previews");
  assert.equal(post.body.mode, "report");
  assert.equal(post.body.template_id, integration?.template || "latest-template");
  const binding = Object.values(post.body.source_bindings)[0];
  assert.equal(binding.job_id, integration?.source || "hrs1597");
  assert(binding.finder_execution_id);
  if (!integration) {
    assert.equal(binding.finder_execution_id, "reread-1");
    assert.deepEqual(post.body.draft_schema, schema);
  }
  completed = true;
  await expect(drawer.getByText("生成完成", { exact: true })).toBeVisible({ timeout: 20000 });
  await expect(drawer.getByText("100%", { exact: true })).toBeVisible();
  await expect(drawer).toContainText("风险评估文档V1版 · v2.6");
  await drawer.getByRole("button", { name: "预览", exact: true }).click();
  const preview = page.getByRole("dialog", { name: "风险评估报告预览", exact: true });
  await expect(preview.getByText(integration ? "重复值😀" : "风险评估报告合成正文", { exact: true }).first()).toBeVisible();
  await preview.getByRole("button", { name: "Close", exact: true }).click();
  await expect(preview).toHaveCount(0);
  await expect(drawer).toBeVisible();
  await expect(drawer.getByRole("button", { name: "预览", exact: true })).toBeFocused();
  const download = page.waitForEvent("download");
  await drawer.getByRole("button", { name: "下载报告", exact: true }).click();
  const file = await download;
  assert.match(file.suggestedFilename(), /\.docx$/);
  const bytes = await readFile(await file.path());
  assert.equal(bytes.subarray(0, 2).toString(), "PK", "download is a real DOCX ZIP");
  if (integration) await file.saveAs(integration.download);
  await drawer.getByRole("button", { name: "关闭", exact: true }).click();
  await expect(drawer).toHaveCount(0);
  await expect(menu).toBeFocused();
  await open();
  await expect(drawer.getByText("生成完成", { exact: true })).toBeVisible();

  if (!integration) {

  // A cached document/template switch must also discard the open drawer state.
  await page.evaluate(() => window.fixtureControls.setDoc("other-source"));
  await expect(drawer).toHaveCount(0);
  await open();
  await expect(drawer.getByText("生成计划", { exact: true })).toBeVisible();
  holdTemplate = true;
  await page.evaluate(() => window.fixtureControls.setTemplate("second-template"));
  await expect(drawer).toHaveCount(0);
  await open();
  await expect.poll(() => held.length).toBe(1);
  await page.evaluate(() => window.fixtureControls.setIdentity({ username: "second", role: "senior_analyst" }));
  await expect(drawer).toHaveCount(0);
  holdTemplate = false;
  for (const release of held.splice(0)) release();
  await open();
  await expect(drawer.getByText("风险评估报告合成正文", { exact: true })).toHaveCount(0);
  await expect(drawer.getByRole("button", { name: "开始生成", exact: true })).toBeEnabled();
  assert(requests.some(r => r.pathname === "/api/ast-templates/second-template" && r.owner === "second"));
  assert.equal(requests.filter(r => r.pathname === "/api/report-previews").length, 2);
  }
  assert(!requests.some(r => /document-analysis|batch-demo|pde-conflict|generate-risk/.test(r.pathname)));
  assert.deepEqual(errors, []);
  console.log("Report actions browser passed: original five-step UI/footer, reread, selected source/template/execution, real DOCX preview/download" + (integration ? "; real isolated backend." : ", error/idempotent retry, close/reopen background task, identity isolation."));
} finally {
  for (const release of held) release();
  await browser.close(); await new Promise(resolve => server.close(resolve));
  await rm(output, { recursive: true, force: true });
}
