// Real React/Query/WordViewer with isolated HTTP fixtures; no live model or business writes.
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
import { ReportWordWorkspace } from "@/components/reports/report-word-workspace";
function App() {
  const [doc, setDoc] = useState("a");
  const [caller, setCaller] = useState("analyst");
  return <><button onClick={() => setDoc(doc === "a" ? "b" : "a")}>switch document</button>
    <button onClick={() => {
      const next = caller === "analyst" ? "second-user" : "analyst";
      localStorage.setItem("slpra.identity", JSON.stringify({username:next, role:"senior_analyst"}));
      setCaller(next);
    }}>switch caller</button>
    <ReportWordWorkspace key={doc} documentIri={"http://example.test/facts#" + doc} /></>;
}
const client = new QueryClient({defaultOptions:{queries:{retry:false}}});
createRoot(document.getElementById("root")).render(<QueryClientProvider client={client}><App /></QueryClientProvider>);
`;
const bundle = await build({ stdin: { contents: entry, resolveDir: frontend, loader: "tsx" },
  bundle: true, write: false, platform: "browser", format: "iife",
  tsconfig: path.join(frontend, "tsconfig.json"),
  define: { "process.env.NODE_ENV": '"development"', "process.env.NEXT_PUBLIC_API_URL": '""' } });
const requests = [], errors = [], held = [];
const runs = new Map();
let holdSource = false, failSource = false, failCreate = false;
const content = (id) => ({ type: "doc", content: [
  { type: "heading", attrs: { level: 1, sourceBlockId: `heading-${id}` },
    content: [{ type: "text", text: `章节 ${id}` }] },
  { type: "paragraph", attrs: { sourceBlockId: `body-${id}` },
    content: [{ type: "text", text: `原件正文 ${id}` }] },
] });
const tree = (id) => ({ node_id: `doc-${id}`, heading: `${id}.docx`, node_type: "document", children: [{
  node_id: `section-${id}`, node_type: "section", heading: `章节 ${id}`, level: 1, children: [],
  source_range: { anchor_block_id: `heading-${id}`, start_block_id: `heading-${id}`, end_block_id: `body-${id}` },
}] });
const source = (id) => ({ document_iri: `http://example.test/facts#${id}`, source_job_id: `job-${id}`,
  document_version: "1", root_class_iri: "urn:CMCReport", document_hash: `hash-${id}`,
  analysis_id: `analysis-${id}`, structure_hash: `structure-${id}`, filename: `${id}.docx`,
  content: content(id), section_tree: tree(id), pagination: { mode: "single_page_fallback" }, warnings: [] });
const run = (id) => ({ recognition_run_id: `run-${id}`, run_revision: 2, event_head: 2,
  artifact_revision: 2, status: "paused", ranking_budget_enabled: true,
  input: { filename: `${id}.docx`, root_class_label: "CMC 报告" },
  identities: { analysis_id: `analysis-${id}`, graph_snapshot_id: `graph-${id}`,
    structure_snapshot_id: `structure-${id}`, metadata_snapshot_id: `metadata-${id}` },
  artifacts: { structure: "ready", graph: "partial", metadata: "ready" },
  progress: { tasks_attempted: 2, model_calls: 1 }, available_actions: ["resume"] });
const graph = (id) => ({ recognition_run_id: `run-${id}`, projection: "effective_affirmed",
  graph_snapshot: { snapshot_id: `graph-${id}`, root_ref: { entity_id: `root-${id}` } },
  entities: [{ entity_id: `root-${id}`, label: `新图谱 ${id}`, class_label: "CMC 报告", source_selection_refs: [] }],
  relationships: [], properties: [], coverage: { subjects: [] }, unresolved: { undetermined: 1, not_checked: 2 } });
const json = (response, payload, code = 200) => {
  response.writeHead(code, { "Content-Type": "application/json" }); response.end(JSON.stringify(payload));
};
const server = createServer(async (request, response) => {
  const url = new URL(request.url, "http://localhost");
  if (url.pathname === "/") { response.end('<html><body><div id="root"></div><script src="/bundle.js"></script></body></html>'); return; }
  if (url.pathname === "/bundle.js") { response.setHeader("Content-Type", "application/javascript"); response.end(bundle.outputFiles[0].text); return; }
  if (!url.pathname.startsWith("/api/")) { response.writeHead(404); response.end(); return; }
  let body = "";
  for await (const chunk of request) body += chunk;
  const caller = request.headers["x-user"];
  requests.push({ method: request.method, path: url.pathname, query: url.search, caller, body });
  if (url.pathname.startsWith("/api/document-analysis/documents/")) {
    const id = url.searchParams.get("document_iri").split("#")[1];
    const key = `${caller}:${id}`;
    if (url.pathname.endsWith("/runs")) {
      if (request.method === "POST") {
        if (failCreate) { json(response, { error: { message: "识别服务暂不可用" } }, 503); return; }
        runs.set(key, run(id));
        json(response, { recognition_run_id: `run-${id}` }, 202);
      } else json(response, { run: runs.get(key) ?? null });
    } else if (failSource) json(response, { error: { message: "文档原件不可用" } }, 404);
    else if (holdSource) held.push({ response, payload: source(id) });
    else json(response, source(id));
    return;
  }
  const [, id, resource] = url.pathname.match(/\/runs\/run-([^/]+)\/(.+)/) ?? [];
  if (resource === "metadata") json(response, { ...source(id), recognition_run_id: `run-${id}`,
    analysis: { analysis_id: `analysis-${id}`, document_hash: `hash-${id}`, structure_hash: `structure-${id}` } });
  else if (resource === "source") json(response, { ...source(id), recognition_run_id: `run-${id}`, selection: null, anchors: [] });
  else if (resource === "graph") json(response, graph(id));
  else if (resource === "ranking-summary") json(response, { cost: { model_calls: 0 } });
  else { response.writeHead(404); response.end(); }
});
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
const browser = await chromium.launch({ executablePath: process.env.DOCUMENT_BROWSER_CHROME || "/usr/bin/google-chrome", args: ["--no-sandbox"] });
const page = await browser.newPage();
page.on("pageerror", (error) => errors.push(error.message));
const posts = () => requests.filter((request) => request.method === "POST");
try {
  await page.goto(`http://127.0.0.1:${server.address().port}`);
  await expect(page.locator(".tiptap")).toContainText("原件正文 a");
  await page.getByRole("navigation", { name: "目录", exact: true }).getByRole("treeitem", { name: "章节 a", exact: true }).click();
  await expect(page.locator(".tiptap")).toContainText("原件正文 a");
  assert.equal(posts().length, 0);
  await page.reload();
  await expect(page.locator(".tiptap")).toContainText("原件正文 a");
  assert.equal(posts().length, 0);
  // Failed writes retain their key so retry cannot accidentally create a second run.
  failCreate = true;
  await page.getByRole("button", { name: "开始识别", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("识别服务暂不可用");
  const firstKey = JSON.parse(posts()[0].body).request_key;
  failCreate = false;
  await page.getByRole("button", { name: "开始识别", exact: true }).click();
  await expect(page.locator('[data-entity-id="root-a"]')).toContainText("新图谱 a");
  assert.equal(JSON.parse(posts()[1].body).request_key, firstKey);
  await expect(page.locator(".tiptap")).toContainText("原件正文 a");
  await page.reload();
  await expect(page.locator('[data-entity-id="root-a"]')).toContainText("新图谱 a");
  assert.equal(posts().length, 2);
  // Another caller never reuses the previous caller's graph cache.
  await page.getByRole("button", { name: "switch caller" }).click();
  await expect(page.getByText("尚未开始识别。", { exact: true })).toBeVisible();
  await expect(page.locator('[data-entity-id="root-a"]')).toHaveCount(0);
  await expect(page.locator(".tiptap")).toContainText("原件正文 a");
  // A delayed source for B must not overwrite A after navigating away.
  holdSource = true;
  await page.getByRole("button", { name: "switch document" }).click();
  await expect.poll(() => held.length).toBe(1);
  holdSource = false;
  await page.getByRole("button", { name: "switch document" }).click();
  for (const pending of held.splice(0)) json(pending.response, pending.payload);
  await expect(page.locator(".tiptap")).toContainText("原件正文 a");
  await expect(page.locator(".tiptap")).not.toContainText("原件正文 b");
  // Failed source loading is distinct from a document that has no headings.
  failSource = true;
  await page.getByRole("button", { name: "switch document" }).click();
  await expect(page.getByRole("alert")).toContainText("文档原件不可用");
  await expect(page.getByText("正文加载失败，暂无法显示目录。", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "开始识别", exact: true })).toBeDisabled();
  failSource = false;
  await page.getByRole("button", { name: "刷新", exact: true }).click();
  await expect(page.locator(".tiptap")).toContainText("原件正文 b");
  assert.equal(posts().length, 2);
  assert.ok(requests.every((request) => !request.path.startsWith("/api/extraction")));
  assert.equal(requests.filter((request) => /\/runs\/run-[^/]+\/source$/.test(request.path)).length, 0,
    "metadata already carries the original content; no duplicate full source read");
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ ok: true, checks: ["preview", "outline", "read-only refresh", "explicit create",
    "idempotent retry", "restore run", "owner isolation", "late response isolation", "source error and retry"],
    requests: requests.length, posts: posts().length }));
} finally {
  for (const pending of held) pending.response.destroy();
  await browser.close();
  server.closeAllConnections();
  await new Promise((resolve) => server.close(resolve));
}
