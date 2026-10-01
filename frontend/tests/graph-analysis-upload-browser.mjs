// Synthetic UI regression: all API calls are intercepted; no real documents or model runs.
import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";

const { chromium, expect: baseExpect } = await import(process.env.PLAYWRIGHT_MODULE || "playwright/test");
const expect = baseExpect.configure({ timeout: 15000 });
const origin = process.env.DOCUMENT_BROWSER_ORIGIN || "http://sldpr-demo.infilake.com:8081";
const output = process.env.DOCUMENT_BROWSER_OUTPUT || "/tmp/graph-analysis-upload-browser";
await mkdir(output, { recursive: true });
const runId = "synthetic-graph-upload";
const rootClassIri = "urn:schema:Report";
const filename = "新图谱文档.docx";
const run = {
  contract_version: "document-analysis-runs-v1", extraction_protocol: "document-harness-v2",
  recognition_run_id: runId, run_revision: 1, event_head: 1, artifact_revision: 1,
  status: "paused", stage: "discover", available_actions: [], error: null,
  created_at: "2026-09-30T00:00:00Z", expires_at: null,
  input: { filename, root_class_iri: rootClassIri, root_class_label: "报告" },
};
const graph = {
  protocol: run.extraction_protocol, run_id: runId, revision: 1, status: "paused", stage: "discover",
  progress: {
    completed_calls: 0, candidate_count: 0, fact_count: 0, phase: "reading", reading_windows: { total: 1, saved: 0, complete: 0, incomplete: 0, active: 0 }, scope_complete: false,
    reading: { total_characters: 10, processed_characters: 0, complete_characters: 0, complete: false },
    work_counts: { ready: 0, waiting: 0, pruned: 0, done: 0, failed: 0 },
    candidate_scope_limited: false, rule_verified_count: 0, llm_verified_count: 0, stage_costs: [],
  },
  entities: [{ id: "root", label: "报告根", role: "document_root", class_iri: rootClassIri,
    class_label: "报告", state: "accepted", reason: "用户指定根类型", evidence: [], mentions: [] }],
  properties: [], relations: [], relation_groups: [], targets: [], observations: [],
  coreferences: [], candidate_work: [], interpretation_tasks: [],
};
const writes = [], errors = [], unexpected = [], checks = [];
let catalogUnavailable = true;
let created = false;
let releaseUpload;
const uploadGate = new Promise((resolve) => { releaseUpload = resolve; });
const browser = await chromium.launch({
  executablePath: process.env.DOCUMENT_BROWSER_CHROME || "/usr/bin/google-chrome",
  headless: true,
  args: ["--no-sandbox", "--no-proxy-server", "--host-resolver-rules=MAP sldpr-demo.infilake.com 127.0.0.1"],
});
const context = await browser.newContext({ viewport: { width: 1440, height: 1050 } });
await context.addInitScript(() => {
  localStorage.setItem("slpra.token", "synthetic-local-only");
  localStorage.setItem("slpra.identity", JSON.stringify({ username: "synthetic", role: "senior_analyst" }));
});
const page = await context.newPage();
page.on("pageerror", (error) => errors.push(error.message));
await page.route("**/api/**", async (route) => {
  const request = route.request(), pathname = new URL(request.url()).pathname;
  if (request.method() !== "GET") {
    writes.push({ method: request.method(), pathname, contentType: request.headers()["content-type"],
      body: request.postDataBuffer()?.toString("utf8") });
    if (pathname === "/api/document-analysis/runs" && request.method() === "POST") {
      if (writes.length === 1) return route.fulfill({ status: 503, json: { detail: "合成上传失败，请重试" } });
      await uploadGate;
      created = true;
      return route.fulfill({ status: 202, json: { recognition_run_id: runId } });
    }
    unexpected.push(`${request.method()} ${pathname}`);
    return route.fulfill({ status: 405, json: { detail: "Unexpected write" } });
  }
  if (pathname === "/api/ontology/all-classes") {
    if (catalogUnavailable) return route.fulfill({ status: 503, json: { detail: "合成本体类型加载失败" } });
    return route.fulfill({ json: [{ iri: rootClassIri, label: "报告", name: "Report", module_key: "test" }] });
  }
  if (pathname === "/api/entities") return route.fulfill({ json: { items: [], total: 0 } });
  if (pathname === "/api/document-analysis/runs") return route.fulfill({ json: {
    contract_version: run.contract_version, items: created ? [run] : [], has_more: false,
  } });
  if (pathname === `/api/document-analysis/runs/${runId}`) return route.fulfill({ json: run });
  if (pathname === `/api/document-analysis/runs/${runId}/harness-graph`) return route.fulfill({ json: graph });
  unexpected.push(pathname);
  return route.fulfill({ status: 404, json: { detail: "Unmocked request" } });
});
const screenshot = (name) => page.screenshot({ path: path.join(output, name), fullPage: true, animations: "disabled" });
try {
  await page.goto(`${origin}/analysis?tab=graph-analysis`, { waitUntil: "domcontentloaded", timeout: 60000 });
  const functions = page.getByRole("tablist", { name: "应用分析功能", exact: true });
  const sources = page.getByRole("tablist", { name: "图谱分析文档来源", exact: true });
  const creation = page.getByLabel("创建图谱分析", { exact: true });
  await expect(functions.getByRole("tab", { name: "文档分析", exact: true })).toBeVisible();
  await expect(functions.getByRole("tab", { name: "图谱分析", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(sources.getByRole("tab", { name: "上传新文档", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(creation.getByRole("alert")).toContainText("本体类型加载失败");
  catalogUnavailable = false;
  await page.getByRole("button", { name: "重新读取本体类型", exact: true }).click();
  const file = page.getByLabel("Word 文件", { exact: true });
  const rootClass = page.getByLabel("本体根类型（必选）", { exact: true });
  const start = page.getByRole("button", { name: "开始分析", exact: true });
  await expect(rootClass).toBeEnabled();
  await expect(start).toBeDisabled();
  await file.setInputFiles({ name: "不支持.pdf", mimeType: "application/pdf", buffer: Buffer.from("synthetic") });
  await expect(creation.getByRole("alert")).toHaveText("仅支持 .doc 或 .docx 文件。");
  await expect(start).toBeDisabled();
  await file.setInputFiles({ name: filename, mimeType: "application/vnd.openxmlformats-officedocument.wordprocessingml.document", buffer: Buffer.from("synthetic-word-content") });
  await expect(start).toBeDisabled();
  await rootClass.selectOption(rootClassIri);
  await expect(start).toBeEnabled();
  assert.equal(writes.length, 0);
  checks.push("parallel navigation, upload default, catalog retry, Word validation, required root type, selection creates no run");
  await screenshot("upload-desktop.png");
  await start.click();
  await expect(creation.getByRole("alert")).toContainText("合成上传失败，请重试");
  await expect(rootClass).toHaveValue(rootClassIri);
  assert.equal(await file.evaluate((input) => input.files[0].name), filename);
  await expect(start).toBeEnabled();
  await start.click();
  await expect(page.getByRole("button", { name: "正在上传并创建分析", exact: true })).toBeDisabled();
  await expect(file).toBeDisabled();
  await expect(rootClass).toBeDisabled();
  await expect(sources.getByRole("tab", { name: "选择已有报告", exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "正在上传并创建分析", exact: true }).evaluate((button) => button.click());
  releaseUpload();
  await expect(page).toHaveURL(new RegExp(`tab=graph-analysis&documentRun=${runId}`));
  const document = page.getByRole("region", { name: "当前分析文档", exact: true });
  await expect(document).toContainText(filename);
  await expect(document).toContainText("根类型：报告");
  await expect(functions.getByRole("tab", { name: "图谱分析", exact: true })).toHaveAttribute("aria-selected", "true");
  assert.equal(writes.length, 2);
  const field = (body, name) => body.match(new RegExp(`name="${name}"\\r\\n\\r\\n([^\\r]+)`))?.[1];
  for (const request of writes) {
    assert.equal(request.method, "POST");
    assert.equal(request.pathname, "/api/document-analysis/runs");
    assert.match(request.contentType, /^multipart\/form-data; boundary=/);
    assert.match(request.body, /synthetic-word-content/);
    assert.match(request.body, /filename="新图谱文档.docx"/);
    assert.equal(field(request.body, "root_class_iri"), rootClassIri);
    assert.equal(field(request.body, "metadata_mode"), "generate_summary");
    assert.ok(field(request.body, "request_key"));
  }
  assert.equal(field(writes[0].body, "request_key"), field(writes[1].body, "request_key"));
  checks.push("upload multipart contract, failure preserves draft, stable retry key, duplicate click blocked, result stays in graph analysis");
  await screenshot("uploaded-result.png");
  await page.reload({ waitUntil: "domcontentloaded" });
  await expect(document).toContainText(filename);
  await page.getByRole("button", { name: "刷新结果", exact: true }).click();
  await expect(document).toContainText(filename);
  await page.getByRole("button", { name: "新建分析", exact: true }).click();
  await expect(file).toBeVisible();
  await expect(page).not.toHaveURL(/documentRun=|documentIri=/);
  await sources.getByRole("tab", { name: "选择已有报告", exact: true }).click();
  await expect(page.getByLabel("报告文档", { exact: true })).toBeVisible();
  await functions.getByRole("tab", { name: "文档分析", exact: true }).click();
  await expect(page).toHaveURL(/tab=document/);
  await functions.getByRole("tab", { name: "图谱分析", exact: true }).click();
  await expect(file).toBeVisible();
  await page.getByRole("button", { name: "刷新历史", exact: true }).click();
  await page.getByRole("button", { name: new RegExp(`查看分析 ${filename}`) }).click();
  await expect(document).toContainText(filename);
  assert.equal(writes.length, 2);
  checks.push("reload, refresh, new analysis, existing report entry, function switch and history are read only");
  await page.getByRole("button", { name: "新建分析", exact: true }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(file).toBeVisible();
  await expect(rootClass).toBeVisible();
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true);
  await screenshot("upload-mobile.png");
  assert.deepEqual(errors, []);
  assert.deepEqual(unexpected, []);
  checks.push("390px upload layout, no unexpected requests or page errors");
  console.log(JSON.stringify({ checks, writes: writes.length, output }));
} catch (error) {
  await screenshot("failure.png");
  console.error(error);
  process.exitCode = 1;
} finally {
  releaseUpload();
  await writeFile(path.join(output, "results.json"), JSON.stringify({ checks, writes, errors, unexpected, url: page.url() }, null, 2));
  await browser.close();
}
