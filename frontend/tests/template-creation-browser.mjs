// UI contract regression with synthetic API responses; no model or live data writes.
import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
const { chromium, expect: baseExpect } = await import(process.env.PLAYWRIGHT_MODULE || "playwright/test");
const expect = baseExpect.configure({ timeout: 15_000 });
const origin = process.env.TEMPLATE_SAMPLE_ORIGIN || "http://127.0.0.1:53219";
const output = "/tmp/template-entry-browser";
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ executablePath: "/usr/bin/google-chrome", args: ["--no-sandbox"] });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const cmc = "https://ontology.pharma-gmp.cn/slpra/drug-development/CMCReport";
const sample = { type: "doc", content: [{ type: "paragraph", content: [{ type: "text", text: "输出样例：评估结论" }] }] };
// Large parsed output previously crossed the network again in both save requests.
const parsedSample = { ...sample, attrs: { padding: "x".repeat(16_000_000) } };
const source = { type: "doc", content: [{ type: "paragraph", content: [{ type: "text", text: "输入原文：CMC 参数" }] }] };
const modes = { repository: "doc_repo_preview", retired: null, template: "template_default" };
const labels = { repository: "文档库 CMC", retired: "历史 CMC", template: "模板专用 CMC" };
const errors = [], requests = [], retiredCalls = [];
let created, createdPayload, revised, revisionPayload, sampleAttached = false, creates = 0, sampleAttempts = 0, holdSave, holdSample, holdNavigation;
page.on("pageerror", (error) => errors.push(error.message));
await page.addInitScript(() => {
  localStorage.setItem("slpra.token", "synthetic-token");
  localStorage.setItem("slpra.identity", JSON.stringify({ username: "analyst", role: "senior_analyst" }));
  window.__progressSubscriptions = [];
  window.EventSource = class { constructor(url) { window.__progressSubscriptions.push(url); } close() {} };
  const originalTimeout = window.setTimeout.bind(window);
  window.setTimeout = (callback, delay, ...args) => originalTimeout(callback,
    window.__fastSaveTimeout && delay === 180_000 ? 1_500 : delay, ...args);
});
await page.route("**/settings/ast-templates/saved-template?**", async (route) => {
  if (route.request().headers().rsc === "1") { holdNavigation = route; return; }
  return route.continue(); // The saved-template link can perform a full navigation.
});
await page.route("**/api/**", async (route) => {
  const request = route.request(), path = new URL(request.url()).pathname;
  requests.push({ method: request.method(), path });
  const reply = (json, status = 200) => route.fulfill({ status, json });
  if (path === "/api/ast-templates/parse-sample") return reply({ content_json: parsedSample, plain_text: "输出样例：评估结论" });
  if (path === "/api/ast-templates" && request.method() === "POST") {
    creates += 1;
    createdPayload = request.postDataJSON();
    if (creates === 1) return reply({ detail: "模拟保存失败，请重试" }, 503);
    holdSave = route;
    return;
  }
  if (path === "/api/ast-templates") return reply(created ? [created] : []);
  if (path.endsWith("/sample")) {
    sampleAttempts += 1;
    assert.equal(new URL(request.url()).searchParams.get("include_content"), "false");
    assert.match(request.postData(), /filename="output.docx"/);
    if (sampleAttempts === 1) return reply({ detail: "模拟附件保存失败" }, 503);
    holdSample = route;
    return;
  }
  if (path.endsWith("/revisions")) {
    revisionPayload = request.postDataJSON();
    revised = { ...created, id: "saved-revision", version: "v2", revision_no: 2,
      schema_json: revisionPayload.schema, schema_hash: "revised-hash" };
    return reply(revised, 201);
  }
  if (path === "/api/ast-templates/saved-revision") return reply(revised);
  if (path.endsWith("/coverage-doc-classes")) return reply({ capable: [cmc] });
  if (path.endsWith("/training-pairs")) return reply([]);
  if (path === "/api/ast-templates/saved-template") return reply(created);
  if (path === "/api/report-contracts") return reply([]);
  if (path === "/api/report-model-context") return reply({ contract_id: "model", definition: { classes: {} } });
  if (path === "/api/entities") return reply({ items: Object.keys(modes).map((id) => ({
    iri: `urn:doc:${id}`, label_zh: labels[id], class_iri: cmc, properties_json: { job_id: id },
  })) });
  if (path === "/api/extraction/jobs") return reply([]);
  if (path.endsWith("/reports")) return reply([]);
  const job = path.match(/^\/api\/extraction\/jobs\/([^/]+)(.*)$/);
  if (job) {
    const [, id, suffix] = job;
    if (!suffix) return reply({ id, source_type: "word", source_mode: modes[id], status: "reviewing" });
    if (id !== "template" && (suffix !== "/annotated-document" || id !== "repository")) {
      retiredCalls.push(path);
      return reply({ detail: "WORD_RECOGNITION_RETIRED: use POST /api/document-analysis/runs" }, 410);
    }
    if (suffix === "/annotated-document") return reply({ source_type: "word", content: source, preview_only: true });
    if (suffix === "/evidence") return reply({ candidates: [], commits: [], snapshot_id: null });
    if (suffix === "/evidence/coverage") return reply({ availability: "available", tasks: [], material_status: "incomplete", required_gaps: 0 });
  }
  errors.push("Unexpected route: " + request.method() + " " + path);
  return reply({});
});

try {
  await page.goto(`${origin}/settings/ast-templates`, { waitUntil: "networkidle", timeout: 120_000 });
  await page.getByRole("button", { name: "从样例文档创建", exact: true }).first().click();
  const dialog = page.getByRole("dialog");
  await dialog.locator("input").first().fill("CMC 样例回归模板");
  await dialog.getByRole("combobox").click();
  await page.getByRole("option", { name: "CMC 报告", exact: true }).click();
  await dialog.locator('input[type="file"]').setInputFiles({ name: "output.docx", mimeType: "application/vnd.openxmlformats-officedocument.wordprocessingml.document", buffer: Buffer.from("synthetic") });
  await expect(dialog).toContainText("文档已解析");
  const enter = dialog.getByRole("button", { name: "进入模板定义", exact: true });
  await enter.click();
  await expect(dialog.getByRole("alert")).toContainText("未能确认模板保存成功，填写内容和样例已保留");
  await expect(dialog.locator("input").first()).toHaveValue("CMC 样例回归模板");
  await expect(dialog).toContainText("已选择：output.docx");
  assert.equal(new URL(page.url()).pathname, "/settings/ast-templates");
  assert.equal(sampleAttempts, 0);
  await enter.click();
  await expect(dialog.getByRole("button", { name: "正在保存模板…", exact: true })).toBeDisabled();
  await expect(dialog.locator("input").first()).toBeDisabled();
  await page.keyboard.press("Escape");
  await expect(dialog).toBeVisible();
  await expect.poll(() => !!holdSave).toBe(true);
  assert.equal(creates, 2);
  assert.equal(createdPayload.iri_pattern, cmc);
  assert.equal(createdPayload.schema_json.source_slots[0].class_iri, cmc);
  assert.equal(createdPayload.sample_content_json, undefined);
  assert.equal(createdPayload.sample_text, undefined);
  assert(JSON.stringify(createdPayload).length < 5_000);
  created = { ...createdPayload, id: "saved-template", status: "draft", schema_version: 2, revision_no: 1,
    schema_hash: "saved-hash", created_at: "2026-09-09T00:00:00Z", versions: [{ id: "saved-template", version: "v1" }] };
  await holdSave.fulfill({ status: 201, json: created });
  await expect(dialog.getByRole("alert")).toContainText("模板草稿已保存，输出样例尚未确认保存成功");
  assert.equal(new URL(page.url()).pathname, "/settings/ast-templates");
  await expect(dialog.locator("input").first()).toBeDisabled();
  await page.evaluate(() => { window.__fastSaveTimeout = true; });
  await dialog.getByRole("button", { name: "重试保存样例并进入模板定义" }).click();
  await expect.poll(() => !!holdSample).toBe(true);
  await expect(dialog.getByRole("status")).toContainText("模板草稿已保存，正在保存输出样例");
  await expect(dialog.getByRole("alert")).toContainText("保存等待超时，服务端可能仍在处理");
  await expect(dialog.getByRole("button", { name: "返回列表", exact: true })).toBeEnabled();
  assert.equal(creates, 2);
  await holdSample.abort();
  holdSample = null;
  await page.evaluate(() => { window.__fastSaveTimeout = false; });
  await dialog.getByRole("button", { name: "重试保存样例并进入模板定义" }).click();
  await expect.poll(() => !!holdSample).toBe(true);
  assert.equal(creates, 2); // Retry only attaches the sample to the already-saved template.
  assert.equal(new URL(page.url()).pathname, "/settings/ast-templates");
  sampleAttached = true;
  created = { ...created, sample_content_json: sample, sample_text: "输出样例：评估结论" };
  await holdSample.fulfill({ status: 204 });
  await expect.poll(() => !!holdNavigation).toBe(true);
  await expect(dialog.getByRole("status")).toContainText("模板和输出样例已保存");
  await expect(dialog.getByRole("button", { name: /正在保存/ })).toHaveCount(0);
  await expect(dialog.getByRole("button", { name: "返回列表", exact: true })).toBeEnabled();
  await expect(dialog.getByRole("link", { name: "打开已保存的模板" })).toHaveAttribute("href", "/settings/ast-templates/saved-template?tab=template&created=1");
  await page.screenshot({ path: `${output}/saved-navigation-pending.png` });
  await dialog.getByRole("link", { name: "打开已保存的模板" }).click();
  await page.waitForURL("**/settings/ast-templates/saved-template?tab=template&created=1");
  await expect(page.getByRole("tab", { name: "AST模板定义", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByText("输出样例：评估结论", { exact: true })).toBeVisible();
  await expect(page.getByRole("status").filter({ hasText: "模板已创建并保存为草稿" })).toBeVisible();
  const save = page.getByRole("button", { name: "保存新修订", exact: true });
  await expect(save).toBeInViewport();
  await expect(page.getByRole("button", { name: "保存模板", exact: true })).toHaveCount(0);
  assert.equal(requests.some((r) => r.path.startsWith("/api/extraction/jobs/")), false);
  // No extra save is required before leaving or refreshing.
  await page.getByRole("button", { name: "返回列表", exact: true }).click();
  await page.waitForURL("**/settings/ast-templates");
  await expect(page.getByText("CMC 样例回归模板", { exact: true })).toBeVisible();
  await page.reload({ waitUntil: "networkidle" });
  await expect(page.getByText("CMC 样例回归模板", { exact: true })).toBeVisible();
  await page.goto(`${origin}/settings/ast-templates/saved-template?tab=template`, { waitUntil: "networkidle" });
  await page.reload({ waitUntil: "networkidle" });
  await expect(page.getByText("输出样例：评估结论", { exact: true })).toBeVisible();
  await expect(page.getByRole("tab", { name: "AST模板定义", exact: true })).toHaveAttribute("aria-selected", "true");
  assert.equal(creates, 2);
  await page.getByRole("button", { name: "＋章节", exact: true }).click();
  await page.getByLabel("章节标题", { exact: true }).fill("已编辑章节");
  await page.getByRole("tab", { name: "源文档", exact: true }).click();
  await expect(save).toBeInViewport();
  for (const id of ["repository", "retired"]) {
    await page.getByRole("button", { name: new RegExp(labels[id]) }).click();
    await expect(page.getByRole("link", { name: "打开文档分析" })).toBeVisible();
    await expect(page.getByRole("button", { name: "重新识别", exact: true })).toHaveCount(0);
  }
  await page.getByRole("button", { name: /模板专用 CMC/ }).click();
  await expect(page.getByRole("region", { name: "关系图谱识别结果" })).toBeVisible();
  await expect(page.getByRole("button", { name: "重新识别", exact: true })).toBeEnabled();
  assert(requests.some((r) => r.path === "/api/extraction/jobs/template/evidence"));
  assert.deepEqual(retiredCalls, []);
  assert((await page.evaluate(() => window.__progressSubscriptions)).every((url) => url.includes("/template/progress")));
  await page.getByRole("tab", { name: "报告预览", exact: true }).click();
  await expect(save).toBeInViewport();
  await page.setViewportSize({ width: 800, height: 900 });
  await expect(save).toBeInViewport();
  await page.screenshot({ path: `${output}/save-narrow.png` });
  await page.setViewportSize({ width: 1440, height: 900 });
  await save.click();
  await page.waitForURL("**/settings/ast-templates/saved-revision");
  assert.equal(creates, 2);
  assert.equal(sampleAttempts, 3);
  assert.equal(revisionPayload.expected_hash, "saved-hash");
  assert.equal(revisionPayload.expected_revision, 1);
  assert.equal(revisionPayload.schema.sections[0].title, "已编辑章节");
  assert.equal(created.schema_json.sections.length, 0); // Initial saved draft remains unchanged.
  await page.getByRole("tab", { name: "AST模板定义", exact: true }).click();
  await expect(page.getByLabel("章节标题", { exact: true })).toHaveValue("已编辑章节");
  await expect(page.getByText("输出样例：评估结论", { exact: true })).toBeVisible();
  await page.screenshot({ path: `${output}/saved-template.png` });
  assert.deepEqual(errors, []);
  assert.deepEqual(retiredCalls, []);
  console.log(JSON.stringify({ status: "passed", browser: browser.version(), creates, sampleAttempts, sampleAttached, retiredCalls, scope: "synthetic API" }));
} catch (error) {
  await page.screenshot({ path: `${output}/failure.png` });
  await writeFile(`${output}/failure.json`, JSON.stringify({ errors, requests, body: await page.locator("body").innerText() }, null, 2));
  throw error;
} finally { await browser.close(); }
