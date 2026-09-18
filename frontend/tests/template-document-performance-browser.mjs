// Isolated browser regression: real React/Query/tree/WordViewer with synthetic read-only APIs.
// ESBUILD_MODULE and PLAYWRIGHT_MODULE may point to existing external installations.
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const { build } = await import(process.env.ESBUILD_MODULE || "esbuild");
const { chromium, expect } = await import(process.env.PLAYWRIGHT_MODULE || "playwright/test");
const output = process.env.TEMPLATE_PERFORMANCE_OUTPUT || "/tmp/template-document-performance-browser";
await mkdir(output, { recursive: true });
const entry = `
import { useState } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useTemplateDocumentRun } from "@/components/analysis/use-template-document-run";
import { TemplateDocumentGraphPanel } from "@/components/analysis/template-document-graph-panel";
import { WordViewer } from "@/components/extraction/word-viewer";
function App() {
  const [visible, setVisible] = useState(true);
  const [doc, setDoc] = useState("a");
  const model = useTemplateDocumentRun("template", doc, visible);
  return <><button onClick={() => setVisible(!visible)}>toggle panel</button>
    <button onClick={() => setDoc(doc === "a" ? "b" : "a")}>switch document</button>
    <button onClick={() => model.select("first")}>first selection</button>
    <button onClick={() => model.select("second")}>second selection</button>
    <button onClick={() => model.select("late")}>late selection</button>
    <div hidden={!visible}><TemplateDocumentGraphPanel model={model} />
      {model.source && <WordViewer key={model.sourceIdentity} content={model.source.content}
        activeAnchor={model.sourceSelection?.anchors[0] ?? null} />}</div></>;
}
const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
createRoot(document.getElementById("root")).render(<QueryClientProvider client={client}><App /></QueryClientProvider>);
`;
const bundle = await build({ stdin: { contents: entry, resolveDir: frontend, loader: "tsx" },
  bundle: true, write: false, platform: "browser", format: "iife", tsconfig: path.join(frontend, "tsconfig.json"),
  define: { "process.env.NODE_ENV": '"development"', "process.env.NEXT_PUBLIC_API_URL": '""' } });
const contract_version = "document-analysis-runs-v1";
const requests = [], streams = new Set(), held = [], errors = [];
let eventHead = 1, rankingVersion = 1, allowSse = true, holdRun = false, aborted = 0;
let budgetEnabled = true, raceSummary = false, summaryConflicts = 0;
const refs = { subject: [], object: [], value: [], predicate_bridge: [], condition: [], counterevidence: [] };
const entities = ["root", "left", "right", "shared", "tail"].map((entity_id) => ({
  entity_id, label: entity_id, class_label: "实体", source_selection_refs: [],
}));
const relationships = [["root", "left"], ["root", "right"], ["left", "shared"], ["right", "shared"], ["shared", "tail"]]
  .map(([subject, object]) => ({ candidate_id: `${subject}-${object}`, subject_ref: { entity_id: subject },
    object_ref: { entity_id: object }, predicate_iri: "rel", predicate_label: "关系", source_selection_refs: refs,
    policy_eligible: true, structural_valid: true, model_supported: true, polarity: "affirmed" }));
const run = (id) => ({ contract_version, recognition_run_id: id, run_revision: eventHead,
  event_head: eventHead, artifact_revision: eventHead, status: "running", stage: "extracting", ranking_budget_enabled: budgetEnabled,
  identities: { analysis_id: `analysis-${id}`, graph_snapshot_id: `graph-${id}`, structure_snapshot_id: `structure-${id}`,
    ranking_summary_id: `ranking-${rankingVersion}` }, artifacts: { structure: "ready", graph: "partial" },
  progress: { tasks_attempted: eventHead, model_calls: eventHead }, available_actions: [], input: { filename: `${id}.docx` } });
const source = (id) => ({ contract_version, recognition_run_id: id, analysis_id: `analysis-${id}`,
  document_hash: `document-${id}`, structure_hash: `structure-${id}`, filename: `${id}.docx`,
  content: { type: "doc", content: [{ type: "paragraph", content: [{ type: "text", text: `原文 ${id}` }] }] },
  selection: null, anchors: [] });
const graph = (id) => ({ contract_version, recognition_run_id: id, projection: "effective_affirmed",
  availability: "partial", graph_snapshot: { snapshot_id: `graph-${id}`, root_ref: { entity_id: "root" }, analysis_id: `analysis-${id}` },
  entities, relationships, properties: [], coverage: { subjects: [] }, unresolved: { undetermined: 0, not_checked: 0 } });
const json = (response, value) => { response.writeHead(200, { "Content-Type": "application/json" }); response.end(JSON.stringify(value)); };
const server = createServer((request, response) => {
  const url = new URL(request.url, "http://localhost");
  if (url.pathname === "/") { response.end('<html><body><div id="root"></div><script src="/bundle.js"></script></body></html>'); return; }
  if (url.pathname === "/bundle.js") { response.setHeader("Content-Type", "application/javascript"); response.end(bundle.outputFiles[0].text); return; }
  if (!url.pathname.startsWith("/api/")) { response.writeHead(404); response.end(); return; }
  requests.push({ method: request.method, path: url.pathname, query: url.search });
  response.on("close", () => { if (!response.writableEnded && !url.pathname.endsWith("/events")) aborted++; });
  if (request.method !== "GET") { response.writeHead(405); response.end(); return; }
  if (url.pathname.includes("/templates/")) {
    const id = url.pathname.includes("/sources/b/") ? "b" : "a";
    if (holdRun) held.push({ response, payload: { run: run(id) } });
    else json(response, { run: run(id) });
    return;
  }
  const [, id, artifact] = url.pathname.match(/\/runs\/([^/]+)\/(.+)/) ?? [];
  if (artifact === "events") {
    if (!allowSse) { response.writeHead(503); response.end(); return; }
    response.writeHead(200, { "Content-Type": "text/event-stream", "Cache-Control": "no-cache" });
    response.write("retry: 1000\n\n");
    const stream = { response, id }; streams.add(stream);
    response.on("close", () => streams.delete(stream));
  } else if (artifact === "graph") json(response, graph(id));
  else if (artifact === "ranking-summary") {
    if (raceSummary) {
      raceSummary = false;
      rankingVersion++;
      eventHead++;
      budgetEnabled = false;
    }
    if (url.searchParams.get("expected_summary_id") !== `ranking-${rankingVersion}`
      || url.searchParams.get("expected_budget_enabled") !== String(budgetEnabled)) {
      summaryConflicts++;
      response.writeHead(409, { "Content-Type": "application/json" });
      response.end(JSON.stringify({ error: { code: "RUN_REVISION_CONFLICT", message: "摘要版本已变化",
        current_revision: eventHead, retryable: true } }));
    } else json(response, { budget_enabled: budgetEnabled, cost: { model_calls: rankingVersion } });
  }
  else if (artifact === "source") json(response, source(id));
  else if (artifact === "source-selection") {
    const identity = source(id);
    delete identity.content;
    delete identity.filename;
    const payload = { ...identity, selection: { selection_ref: url.searchParams.get("selection_ref") } };
    if (url.searchParams.get("selection_ref") === "late") held.push({ response, payload });
    else json(response, payload);
  } else { response.writeHead(404); response.end(); }
});
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
const origin = `http://127.0.0.1:${server.address().port}`;
const count = (suffix) => requests.filter((request) => request.path.endsWith(suffix)).length;
const emit = () => {
  eventHead++;
  for (const { response, id } of streams) response.write(`event: progress\nid: ${eventHead}\ndata: ${JSON.stringify({
    ...run(id), event_id: String(eventHead),
  })}\n\n`);
};
const browser = await chromium.launch({ executablePath: process.env.DOCUMENT_BROWSER_CHROME || "/usr/bin/google-chrome",
  args: ["--no-sandbox"] });
let page;
try {
  page = await browser.newPage();
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(origin);
  await expect(page.locator(".tiptap")).toContainText("原文 a");
  await expect(page.locator("[data-entity-id]")).toHaveCount(4);
  const reader = await page.locator(".tiptap").elementHandle();
  await page.getByRole("button", { name: "first selection", exact: true }).click();
  await expect.poll(() => count("source-selection")).toBe(1);
  await page.getByRole("button", { name: "second selection", exact: true }).click();
  await expect.poll(() => count("source-selection")).toBe(2);
  assert.equal(count("source"), 1);
  assert.ok(await page.locator(".tiptap").evaluate((node, previous) => node === previous, reader));
  await page.locator('[data-entity-id="left"] [data-tree-action="toggle"]').click();
  await expect(page.locator('[data-entity-id="shared"]')).toHaveCount(0);
  await page.locator('[data-entity-reference="shared"]').click();
  await expect(page.locator('[data-entity-id="shared"]')).toHaveCount(1);
  await expect(page.locator('[data-entity-id="tail"]')).toHaveCount(1);
  await page.locator('[data-entity-id="root"] [data-tree-action="toggle"]').click();
  await expect(page.locator("[data-entity-id]")).toHaveCount(1);
  await page.locator('[data-entity-id="root"] [data-tree-action="toggle"]').click();
  const graphReads = count("graph"), sourceReads = count("source");
  rankingVersion++;
  emit();
  await expect.poll(() => count("ranking-summary")).toBe(2);
  assert.equal(count("graph"), graphReads, "ranking-only event must not reread graph");
  assert.equal(count("source"), sourceReads);
  const summaryReads = count("ranking-summary");
  rankingVersion++;
  raceSummary = true;
  emit();
  await expect.poll(() => summaryConflicts).toBe(1);
  await expect.poll(() => count("ranking-summary")).toBe(summaryReads + 2);
  await expect(page.getByRole("status").filter({ hasText: `排序调用 ${rankingVersion} 次` })).toBeVisible();
  assert.equal(count("graph"), graphReads, "a stale summary synchronizes status without rereading the graph");
  const pinned = new URLSearchParams(requests.filter((request) => request.path.endsWith("ranking-summary")).at(-1).query);
  assert.equal(pinned.get("expected_summary_id"), `ranking-${rankingVersion}`);
  assert.equal(pinned.get("expected_budget_enabled"), "false");
  await page.waitForTimeout(800);
  const healthyReads = requests.length;
  await page.waitForTimeout(3000);
  assert.equal(requests.length, healthyReads, "healthy stream has no parallel polling");
  allowSse = false;
  for (const { response } of streams) response.end();
  const beforeFallback = count("runs");
  await expect.poll(() => count("runs"), { timeout: 6000 }).toBeGreaterThan(beforeFallback);
  allowSse = true;
  // A new subscription after showing the panel restores the same active run.
  await page.getByRole("button", { name: "toggle panel", exact: true }).click();
  await page.waitForTimeout(300);
  const hiddenReads = requests.length;
  await page.waitForTimeout(3000);
  assert.equal(requests.length, hiddenReads, "hidden panel stops every display request and SSE reconnect");
  await page.getByRole("button", { name: "toggle panel", exact: true }).click();
  await expect.poll(() => streams.size).toBe(1);
  await page.waitForTimeout(800);
  holdRun = true;
  emit();
  await expect.poll(() => held.length).toBe(1);
  await page.getByRole("button", { name: "toggle panel", exact: true }).click();
  await expect.poll(() => aborted).toBeGreaterThan(0);
  holdRun = false;
  for (const { response, payload } of held.splice(0)) json(response, payload);
  await page.getByRole("button", { name: "toggle panel", exact: true }).click();
  await expect.poll(() => streams.size).toBe(1);
  await page.getByRole("button", { name: "late selection", exact: true }).click();
  await expect.poll(() => held.length).toBe(1);
  await page.getByRole("button", { name: "switch document", exact: true }).click();
  await expect(page.locator(".tiptap")).toContainText("原文 b");
  for (const { response, payload } of held.splice(0)) json(response, payload);
  await expect(page.locator(".tiptap")).toContainText("原文 b");
  assert.ok(await page.locator(".tiptap").evaluate((node, previous) => node !== previous, reader));
  assert.equal(requests.filter((request) => request.method !== "GET").length, 0);
  assert.deepEqual(errors, []);
  await page.screenshot({ path: path.join(output, "complete.png"), fullPage: true });
  await writeFile(path.join(output, "result.json"), JSON.stringify({ status: "passed", scope: "synthetic APIs; real React Query/tree/WordViewer",
    checks: ["selection content cache and stable reader", "lazy collapse and shared reference jump", "ranking-only version refresh",
      "stale summary and budget identity conflict recovery", "healthy SSE no polling", "disconnected fallback",
      "hidden no requests", "hidden abort", "late response document isolation"],
    requests, aborted, summaryConflicts }, null, 2));
  console.log(`PASS: 9 browser regressions; ${requests.length} GETs; ${aborted} cancelled reads. ${output}`);
} catch (error) {
  await writeFile(path.join(output, "failure.json"), JSON.stringify({ errors, requests,
    body: await page?.locator("body").innerText() }, null, 2));
  console.error(JSON.stringify({ errors, requests }));
  throw error;
} finally {
  await browser.close();
  for (const { response } of streams) response.end();
  for (const { response } of held) response.end();
  await new Promise((resolve) => server.close(resolve));
}
