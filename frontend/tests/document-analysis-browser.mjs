// Real API acceptance against document_analysis_browser_fixture.py; no route interception.
import assert from "node:assert/strict";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";

const fixtureDirectory = path.resolve(process.env.DOCUMENT_BROWSER_FIXTURE || "");
assert.ok(process.env.DOCUMENT_BROWSER_FIXTURE, "Set DOCUMENT_BROWSER_FIXTURE to the isolated fixture output.");
const manifest = JSON.parse(await readFile(path.join(fixtureDirectory, "manifest.json"), "utf8"));
const credentials = JSON.parse(await readFile(path.join(fixtureDirectory, "credentials.json"), "utf8"));
const output = path.join(fixtureDirectory, process.env.DOCUMENT_BROWSER_OUTPUT || "browser");
await mkdir(output, { recursive: true });
const { chromium, expect } = await import(process.env.PLAYWRIGHT_MODULE || "playwright/test");
const backend = new URL(manifest.backend_origin);
assert.equal(backend.hostname, "127.0.0.1", "The fixture backend must be isolated on loopback.");
const checkpoint = async () => {
  const response = await fetch(new URL("/__browser_checkpoint", backend));
  assert.equal(response.status, 200);
  return response.json();
};
const login = await fetch(new URL("/api/auth/login", backend), {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(credentials),
});
assert.equal(login.status, 200);
const identity = await login.json();
assert.deepEqual(await checkpoint(), manifest.baseline);
const errors = [], requests = [], failures = [];
const browser = await chromium.launch({
  executablePath: process.env.DOCUMENT_BROWSER_CHROME || "/usr/bin/google-chrome",
  args: ["--no-sandbox"],
});
const context = await browser.newContext({ viewport: { width: 1600, height: 1100 } });
await context.addInitScript(({ token, username, role }) => {
  localStorage.setItem("slpra.token", token);
  localStorage.setItem("slpra.identity", JSON.stringify({ username, role }));
}, identity);
await context.tracing.start({ screenshots: true, snapshots: true });
const page = await context.newPage();
page.setDefaultTimeout(15_000);
page.on("pageerror", (error) => errors.push(error.message));
page.on("request", (request) => {
  const url = new URL(request.url());
  if (url.pathname.startsWith("/api/")) requests.push({ method: request.method(), path: url.pathname });
});
page.on("response", (response) => {
  const url = new URL(response.url());
  if (url.pathname.startsWith("/api/") && response.status() >= 400) {
    failures.push({ status: response.status(), path: url.pathname });
  }
});
const urlFor = (run) => `${manifest.frontend_origin}/analysis?tab=document&documentRun=${run.run_id}`;
const drawer = page.getByRole("dialog", { name: "文档分析详情", exact: true });
const graphCanvas = () => page.getByRole("group", { name: "可交互关系图谱", exact: true });
const nodeDetails = () => page.getByLabel("节点属性与关系", { exact: true });
const statusCard = page.getByLabel("文档分析运行状态", { exact: true });
const first = manifest.runs[0], second = manifest.runs[1];
const graphTab = () => page.getByRole("tab", { name: "关系图谱", exact: true });
const metadataTab = () => page.getByRole("tab", { name: "节点元数据", exact: true });
const preview = page.getByLabel("原始文档预览", { exact: true });
const chapterTree = page.getByLabel("文档分析工作区", { exact: true }).getByLabel("Word 章节树", { exact: true }).first();
const switchRun = async (run) => page.evaluate((url) => window.history.pushState(null, "", url), urlFor(run));
let result = { status: "failed", scope: manifest.scope };
try {
  await page.goto(`${manifest.frontend_origin}/analysis?tab=document`, { waitUntil: "domcontentloaded", timeout: 120_000 });
  const cards = page.getByRole("list", { name: "历史分析任务" });
  await expect(cards).toBeVisible({ timeout: 60_000 });
  assert.ok(await cards.evaluate((node) => getComputedStyle(node).gridTemplateColumns.split(" ").length) > 1);
  await page.screenshot({ path: path.join(output, "history-cards.png"), fullPage: true });
  const historyCard = cards.getByRole("button", { name: new RegExp(`^查看分析 ${first.filename}`) });
  await historyCard.click();
  await expect(drawer).toBeVisible();
  assert.ok(Math.abs((await drawer.boundingBox()).width - 1600 * 2 / 3) < 2, "Drawer must occupy 2/3 of the desktop viewport");
  await drawer.getByRole("button", { name: "关闭", exact: true }).click();
  await expect(drawer).toHaveCount(0);
  await expect(historyCard).toBeFocused();
  await expect(page).not.toHaveURL(/documentRun=/);
  await historyCard.click();
  await expect(statusCard).toContainText(first.filename, { timeout: 60_000 });
  await expect(metadataTab()).toHaveAttribute("aria-selected", "true");
  await expect(chapterTree).toBeVisible();
  await expect(preview.locator(".tiptap")).toBeVisible();
  const editor = await preview.locator(".tiptap").elementHandle();
  await graphTab().click();
  await expect(graphTab()).toHaveAttribute("aria-selected", "true");
  await expect(chapterTree).toBeVisible();
  await expect(preview.locator(".tiptap")).toBeVisible();
  assert.ok(await preview.locator(".tiptap").evaluate((node, previous) => node === previous, editor),
    "Graph view must preserve the Word preview editor");
  await editor.dispose();
  await expect(page.getByRole("button").filter({ hasText: first.label }).first()).toBeVisible();
  await page.locator("summary").filter({ hasText: "记录处理顺序与检索诊断" }).click();
  assert.ok(first.ranking.actual_modes.includes("semantic"));
  await expect(page.getByText(`请求模式：semantic；实际模式：${first.ranking.actual_modes.join("、")}`,
    { exact: true })).toBeVisible();
  const epoch = page.locator("details details").first();
  await epoch.locator("summary").click();
  await expect(epoch.getByRole("columnheader", { name: "原始分数", exact: true })).toBeVisible();
  assert.ok(await epoch.locator("tbody tr").count() > 0);
  await page.screenshot({ path: path.join(output, "ranking.png"), fullPage: true });

  await nodeDetails().getByRole("button").filter({ hasText: "使用设备" }).click();
  await expect(page.getByText("主体 → 对象", { exact: true })).toBeVisible();
  const sourceButton = nodeDetails().getByRole("button", { name: / · 原文 1$/ }).first();
  const [sourceResponse] = await Promise.all([
    page.waitForResponse((response) =>
      response.url().includes(`/runs/${first.run_id}/source?`) && response.status() === 200),
    sourceButton.click(),
  ]);
  const source = await sourceResponse.json();
  assert.equal(source.recognition_run_id, first.run_id);
  assert.ok(source.anchors.length > 0);
  await expect(page.getByText("已从关系图谱定位原文", { exact: true })).toBeVisible();
  await expect(graphTab()).toHaveAttribute("aria-selected", "true");
  await expect(chapterTree).toBeVisible();
  await expect(page.locator(".tiptap").filter({ hasText: first.label })).toBeVisible();
  await page.screenshot({ path: path.join(output, "evidence.png"), fullPage: true });

  await switchRun(second);
  await expect(statusCard).toContainText(second.filename);
  await graphTab().click();
  await expect(page.getByRole("button").filter({ hasText: second.label }).first()).toBeVisible();
  await expect(page.getByRole("button").filter({ hasText: first.label })).toHaveCount(0);
  await expect(page.locator("summary").filter({ hasText: "排序已降级" })).toBeVisible();
  await page.getByLabel("图谱投影", { exact: true }).selectOption("all_candidates");
  await expect(page.getByRole("button").filter({ hasText: second.label }).first()).toBeVisible();
  await page.getByLabel("图谱投影", { exact: true }).selectOption("effective_affirmed");

  // Start a real read for A, immediately switch to B, and require B's state to
  // survive A's aborted or late response without replacing the source/graph.
  const inFlight = page.waitForRequest((request) => request.url().includes(`/runs/${first.run_id}/graph`));
  await switchRun(first);
  await inFlight;
  await switchRun(second);
  await expect(statusCard).toContainText(second.filename);
  await expect(page.getByRole("button").filter({ hasText: second.label }).first()).toBeVisible();
  await expect(page.getByRole("button").filter({ hasText: first.label })).toHaveCount(0);
  await page.reload({ waitUntil: "domcontentloaded" });
  await expect(statusCard).toContainText(second.filename);
  await graphTab().click();
  await expect(page.getByRole("button").filter({ hasText: second.label }).first()).toBeVisible();
  await expect(page.locator("summary").filter({ hasText: "排序已降级" })).toBeVisible();
  await page.screenshot({ path: path.join(output, "restored-second-run.png"), fullPage: true });
  if (manifest.tool_run) {
    const tool = manifest.tool_run;
    const [toolResponse] = await Promise.all([
      page.waitForResponse((response) => response.url().includes(`/runs/${tool.run_id}/graph?projection=verified`)
        && response.status() === 200),
      switchRun(tool),
    ]);
    await expect(statusCard).toContainText(tool.filename);
    await graphTab().click();
    const toolGraph = await toolResponse.json();
    assert.equal(toolGraph.extraction_protocol, "ontology-tool-extraction-v1");
    assert.equal(toolGraph.relationships.length, 0);
    assert.equal(toolGraph.relationship_groups.length, 1);
    assert.equal(toolGraph.entities.length, 4, "selection groups do not add ontology entities");
    assert.equal(toolGraph.relationship_groups[0].selection, "alternatives");
    assert.equal(toolGraph.relationship_groups[0].modality, "possible");
    assert.equal(toolGraph.scope_resolutions[0].scope_id, tool.scope_id);
    await expect(page.getByLabel("图谱投影", { exact: true })).toHaveValue("verified");
    await expect(page.getByRole("button").filter({ hasText: tool.isolated_label }).first()).toBeVisible();
    assert.equal(await graphCanvas().locator('[data-node-kind="entity"]').count(), 4);
    assert.equal(await graphCanvas().locator('[data-node-kind="relationship_group"]').count(), 1);
    const isolated = graphCanvas().getByRole("button", { name: new RegExp(`^查看实体 ${tool.isolated_label}`) });
    await isolated.focus();
    await isolated.press("Enter");
    await expect(nodeDetails()).toContainText(tool.isolated_label);
    await expect(nodeDetails()).toContainText("0 条关系 · 0 个关系组 · 0 项属性");
    await graphCanvas().getByRole("button", { name: /^查看关系组 使用设备/ }).click();
    await expect(nodeDetails().getByText("可选成员", { exact: true })).toBeVisible();
    await expect(nodeDetails().getByText("可能", { exact: true })).toBeVisible();
    const beforeZoom = await graphCanvas().locator(":scope > g").getAttribute("transform");
    await page.getByRole("button", { name: "放大图谱", exact: true }).click();
    assert.notEqual(await graphCanvas().locator(":scope > g").getAttribute("transform"), beforeZoom);
    const graphElement = await graphCanvas().locator('[data-node-kind="relationship_group"]').elementHandle();
    const transform = await graphCanvas().locator(":scope > g").getAttribute("transform");
    await page.waitForResponse((response) => response.url().includes(`/runs/${tool.run_id}/graph?projection=verified`)
      && response.status() === 200);
    await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    assert.ok(await graphCanvas().locator('[data-node-kind="relationship_group"]').evaluate((node, previous) => node === previous, graphElement),
      "Unchanged polling must not recreate nodes or discard focus");
    assert.equal(await graphCanvas().locator(":scope > g").getAttribute("transform"), transform, "Polling must preserve zoom");
    await graphElement.dispose();
    await page.getByRole("button", { name: "适配全图", exact: true }).click();
    await graphCanvas().scrollIntoViewIfNeeded();
    await page.screenshot({ path: path.join(output, "graph-canvas.png"), fullPage: true });
    await expect(page.getByText("组选择依据", { exact: true })).toBeVisible();
    const [conditionalResponse] = await Promise.all([
      page.waitForResponse((response) => response.url().includes(`/runs/${tool.run_id}/graph?projection=conditional`)
        && response.status() === 200),
      page.getByLabel("图谱投影", { exact: true }).selectOption("conditional"),
    ]);
    const conditional = await conditionalResponse.json();
    assert.equal(conditional.relationship_groups.length, 0, "the unconditioned parent is filtered");
    assert.equal(conditional.properties.length, 1);
    assert.equal(conditional.scope_resolutions[0].steps[0].relation_ref.id, tool.group_id);
    await graphCanvas().getByRole("button", { name: /^查看实体 设备甲/ }).click();
    await expect(nodeDetails()).toContainText("范围示例值");
    await graphCanvas().scrollIntoViewIfNeeded();
    await page.screenshot({ path: path.join(output, "node-properties.png"), fullPage: true });
    await nodeDetails().getByRole("button").filter({ hasText: "范围示例值" }).click();
    await expect(page.getByText("继承范围", { exact: true })).toBeVisible();
    await expect(page.getByText("继承依据 1：上级关系 → 设备甲", { exact: true })).toBeVisible();
    const scopeSelection = conditional.scope_resolutions[0].steps[0].evidence_selection_ids[0];
    const [scopeSource] = await Promise.all([
      page.waitForResponse((response) => response.url().includes(`/runs/${tool.run_id}/source?`)
        && response.status() === 200),
      page.getByRole("button", { name: "继承依据 1：上级关系 → 设备甲 · 原文 1", exact: true }).click(),
    ]);
    assert.equal(new URL(scopeSource.url()).searchParams.get("selection_ref"), scopeSelection);
    assert.ok((await scopeSource.json()).anchors.length > 0);
    await expect(page.getByText("已从关系图谱定位原文", { exact: true })).toBeVisible();
    await page.screenshot({ path: path.join(output, "tool-group-inherited-scope.png"), fullPage: true });
    await page.reload({ waitUntil: "domcontentloaded" });
    await expect(statusCard).toContainText(tool.filename);
    await graphTab().click();
    await expect(page.getByLabel("图谱投影", { exact: true })).toHaveValue("verified");
    await switchRun(second);
    await expect(statusCard).toContainText(second.filename);
    await graphTab().click();
    await expect(page.getByLabel("图谱投影", { exact: true })).toHaveValue("effective_affirmed");
    await expect(page.getByLabel("图谱投影", { exact: true }).locator('option[value="verified"]')).toHaveCount(0);
  }
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(drawer).toBeVisible();
  assert.ok(Math.abs((await drawer.boundingBox()).width - 390) < 2, "Mobile drawer must be full width");
  await graphCanvas().scrollIntoViewIfNeeded();
  await graphCanvas().locator('[data-node-kind="entity"]').first().click();
  await expect(nodeDetails()).toBeInViewport();
  const widths = await drawer.evaluate((node) => ({ width: node.clientWidth, scroll: node.scrollWidth }));
  assert.ok(widths.scroll <= widths.width + 1, "Drawer must not overflow horizontally");
  await page.screenshot({ path: path.join(output, "mobile-graph.png"), fullPage: true });
  await page.keyboard.press("Escape");
  await expect(drawer).toHaveCount(0);
  await expect(page).not.toHaveURL(/documentRun=/);
  assert.deepEqual(await checkpoint(), manifest.baseline, "Read-only UI changed durable state or model requests.");
  assert.ok(requests.length > 0);
  assert.deepEqual(requests.filter((request) => request.method !== "GET"), []);
  assert.deepEqual(failures, []);
  assert.deepEqual(errors, []);
  result = { status: "passed", scope: manifest.scope, api_requests: requests.length,
    checks: ["history card grid", "2/3 desktop and full-width mobile drawer", "close restores card focus",
      "keyboard node selection with scoped properties and relationships", "D3 group connectors and zoom controls", "mobile no overflow", "real persisted graph", "semantic ranking diagnostics", "degraded ranking",
      "graph tab preserves chapter tree and preview editor", "source replay keeps graph visible",
      "source anchor replay", "projection switching", "rapid run switching", "refresh recovery",
      ...(manifest.tool_run ? ["protocol-selected verified graph", "group selection without new entities/edges",
        "independent verified entity", "filtered parent scope explanation and original source link",
        "legacy protocol restored after tool run"] : []),
      "unchanged database watermarks and model requests", "GET-only browser API traffic"],
    baseline: manifest.baseline };
} catch (error) {
  result.error = error.stack;
  await page.screenshot({ path: path.join(output, "failure.png"), fullPage: true });
  throw error;
} finally {
  await writeFile(path.join(output, "report.json"), JSON.stringify({ ...result, errors, failures, requests }, null, 2) + "\n");
  await context.tracing.stop({ path: path.join(output, "trace.zip") });
  await browser.close();
  console.log(JSON.stringify({ status: result.status, output, api_requests: requests.length }));
}
