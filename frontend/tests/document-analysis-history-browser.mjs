// Synthetic API browser regression; does not contact the application backend or models.
import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";

const { chromium, expect: baseExpect } = await import(process.env.PLAYWRIGHT_MODULE || "playwright/test");
const expect = baseExpect.configure({ timeout: 20_000 });
const origin = process.env.DOCUMENT_HISTORY_ORIGIN || "http://127.0.0.1:53219";
const output = process.env.DOCUMENT_HISTORY_OUTPUT || "/tmp/document-analysis-history-browser";
await mkdir(output, { recursive: true });
const contract_version = "document-analysis-runs-v1";
const rootIri = "https://ontology.example/CMCReport";
const makeRun = (id, filename, created_at) => ({
  contract_version, recognition_run_id: id, run_revision: 1, event_head: 1, artifact_revision: 1,
  status: "paused", stage: "accepted", created_at, expires_at: "2099-01-01T00:00:00Z",
  ranking_budget_enabled: true,
  input: { filename, root_class_iri: rootIri, root_class_label: "CMC 报告", metadata_mode: "generate_summary" },
});
const runs = Array.from({ length: 12 }, (_, index) => makeRun(
  `history-${12 - index}`, index < 2 ? "同名报告.docx" : `历史报告-${12 - index}.docx`,
  new Date(Date.now() - (index + 1) * 3_600_000).toISOString(),
));
runs[2].status = "running";
const sourceRef = "c".repeat(64);
const sourceText = (run) => `合成预览 ${run.recognition_run_id}，明确使用合成设备。`;
const sourceAnchor = (run) => ({
  document_hash: `document-${run.recognition_run_id}`, parser_version: "browser-layout-v1",
  structure_hash: run.recognition_run_id, evidence_id: "evidence:0", section_node_id: "section:0",
  block_id: "p:0", paragraph_index: 0,
  span_start: Array.from(sourceText(run).split("明确使用")[0]).length,
  span_end: Array.from(sourceText(run).split("明确使用")[0]).length + 4,
});
const makeMetadata = (run) => {
  const result = {
  ...run, availability: "ready", filename: run.input.filename, warnings: [], error: null,
  analysis: { analysis_id: `analysis-${run.recognition_run_id}`, structure_hash: run.recognition_run_id },
  metadata_snapshot: null, pagination: null,
  section_tree: {
    node_id: "document:root", node_type: "document", heading: "合成文档章节", level: 0,
    path: [], heading_index: null, is_leaf: true, direct_block_ids: ["p:0"],
    paragraph_indices: [0], table_indices: [], children: [], pages: [],
    source_range: { anchor_block_id: "p:0", start_block_id: "p:0", end_block_id: "p:0", heading_index: null },
    layer_metadata: {
      content_summary: "仅用于浏览器布局回归的合成元数据。", summary_scope: "subtree",
      summary_status: "disabled", summary_source: "none", summary_model: null,
      prompt_version: "browser-layout-v1", content_hash: "b".repeat(64), generated_at: null,
      direct_paragraph_count: 1, direct_table_count: 0, descendant_section_count: 0,
      leaf_count: 1, page_count: 1,
    },
  },
  content: { type: "doc", analysis: {
    ...sourceAnchor(run), evidence_units: [{ ...sourceAnchor(run), text: sourceText(run) }],
  }, content: [
    { type: "paragraph", attrs: { sourceBlockId: "p:0", evidenceId: "evidence:0", sourceParagraphIndex: 0, sectionNodeId: "section:0" },
      content: [{ type: "text", text: sourceText(run) }] },
    { type: "paragraph", attrs: { sourceBlockId: "p:1", sourceParagraphIndex: 1, sectionNodeId: "section:1" },
      content: [{ type: "text", text: `另一个章节 ${run.recognition_run_id}` }] },
  ] },
  };
  result.section_tree.children = ["合成证据章节", "合成第二章节"].map((heading, index) => ({
    ...result.section_tree, node_id: `section:${index}`, node_type: "section", heading, level: 1,
    path: [heading], direct_block_ids: [`p:${index}`], paragraph_indices: [index], children: [],
    source_range: { anchor_block_id: `p:${index}`, start_block_id: `p:${index}`, end_block_id: `p:${index}`, heading_index: index },
  }));
  result.section_tree.is_leaf = false;
  return result;
};
const makeGraph = (run, projection) => ({
  ...run, availability: "ready", projection, properties: [],
  graph_snapshot: { snapshot_id: `graph-${run.recognition_run_id}`, root_ref: { entity_id: "root", revision: 1 } },
  entities: [
    { entity_id: "root", label: `合成报告 ${run.recognition_run_id}`, seed_origin: "user_selected" },
    { entity_id: "equipment", label: "用于验证窄栏换行的合成设备 LongEquipmentIdentifier".repeat(2), seed_origin: "recognized" },
  ].map((item) => ({ ...item, revision: 1, class_iri: rootIri, class_label: "合成类型",
    identity_state: "document_local", independent_review: "unreviewed", source_selection_refs: [] })),
  relationships: [{
    candidate_id: "relationship:" + "d".repeat(64), revision: 1,
    subject_ref: { entity_id: "root", revision: 1 }, object_ref: { entity_id: "equipment", revision: 1 },
    predicate_iri: "https://ontology.example/usesEquipment", predicate_label: "使用设备（合成关系）",
    direction: "subject_to_object", polarity: "affirmed", conditions: [], applicability: {},
    structural_valid: true, model_supported: true, policy_eligible: true, invalidated: false,
    independent_review: "unreviewed", proof_ref: null, decision_refs: [], dependency_refs: [],
    source_selection_refs: { subject: [], object: [], value: [], predicate_bridge: [sourceRef], condition: [], counterevidence: [] },
    reason: "受控合成结果，仅用于浏览器布局与来源导航回归。", reason_code: null,
  }],
  coverage: { records_planned: 1, records_examined: 1, records_unattempted: 0, records_incomplete: 0,
    pending_frontiers: 0, phase2_started: true, subjects: [], stop_reason: null },
  unresolved: { unsupported: 0, undetermined: 0 }, invalidated_refs: [], error: null,
  ranking: { budget_enabled: run.ranking_budget_enabled, requested_mode: "semantic",
    actual_modes: ["semantic"], degraded: false, paused: false, reasons: [], committed_epochs: 1,
    cost: { model_calls: 7, input_pairs: 8, input_tokens: 100, retries: 0, elapsed_seconds: 1 },
    epochs: [{ epoch_id: "epoch:accounted", status: "committed", budget_accounted: true,
      query_id: null, subject_ref: null, predicate_iri: null, plan_id: null,
      requested_mode: "semantic", actual_mode: "semantic", degraded: false, reason: null, records: [] }] },
});
const actionsFor = (run) => run.recognition_run_id === "history-12"
  ? ["resume", "cancel", run.ranking_budget_enabled ? "ranking_budget_disable" : "ranking_budget_enable"]
  : run.status === "running" ? ["pause", "cancel"] : [];
let createCount = 0;
let failHistory = false;
let delayedRunId = null;
let releaseDelayed = null;
let delayedRequest = null;
let delaySource = false;
let sourceRequested = null;
let releaseSource = null;
let sourceCompleted = null;
let sourceReads = 0;
let budgetConflict = false;
const budgetRequests = [];
const writes = [];
const errors = [];
const reads = [];
const resourceFailures = [];
const consoleErrors = [];
const browser = await chromium.launch({
  executablePath: process.env.DOCUMENT_BROWSER_CHROME || "/usr/bin/google-chrome",
  args: ["--no-sandbox"],
});
const context = await browser.newContext({ viewport: { width: 1440, height: 1100 } });
await context.addInitScript(() => {
  localStorage.setItem("slpra.token", "synthetic-history-token");
  localStorage.setItem("slpra.identity", JSON.stringify({ username: "analyst", role: "senior_analyst" }));
});
const page = await context.newPage();
page.setDefaultTimeout(20_000);
page.on("pageerror", (error) => errors.push(error.message));
page.on("console", (message) => { if (message.type() === "error") consoleErrors.push(message.text()); });
page.on("requestfailed", (request) => resourceFailures.push({
  path: new URL(request.url()).pathname, error: request.failure()?.errorText,
}));
page.on("response", (response) => {
  if (response.status() >= 400 && !new URL(response.url()).pathname.startsWith("/api/")) {
    resourceFailures.push({ path: new URL(response.url()).pathname, status: response.status() });
  }
});
await page.route("**/api/**", async (route) => {
  const request = route.request();
  const url = new URL(request.url());
  const pathname = url.pathname;
  reads.push(pathname);
  if (request.method() !== "GET") writes.push({ method: request.method(), pathname });
  if (pathname === "/api/ontology/all-classes") {
    return route.fulfill({ json: [{ iri: rootIri, name: "CMCReport", label: "CMC 报告", module_key: "test" }] });
  }
  if (pathname === "/api/document-analysis/runs") {
    if (request.method() === "POST") {
      createCount += 1;
      const item = makeRun(`new-${createCount}`, "新上传.docx", new Date().toISOString());
      runs.unshift(item);
      return route.fulfill({ status: 202, json: { ...item, idempotent_replay: false, links: {} } });
    }
    if (failHistory) return route.fulfill({ status: 503, json: { error: { message: "暂时不可用" } } });
    const offset = Number(url.searchParams.get("offset") || 0);
    const limit = Number(url.searchParams.get("limit") || 20);
    return route.fulfill({ json: { contract_version, items: runs.slice(offset, offset + limit), has_more: offset + limit < runs.length } });
  }
  if (pathname.startsWith("/api/document-analysis/runs/")) {
    const [id, artifact, operation] = pathname.slice("/api/document-analysis/runs/".length).split("/");
    const run = runs.find((item) => item.recognition_run_id === id);
    assert.ok(run, `Unexpected run ${id}`);
    if (artifact === "ranking-budget") {
      assert.equal(request.method(), "POST");
      assert.ok(["enable", "disable"].includes(operation));
      assert.ok(actionsFor(run).includes(`ranking_budget_${operation}`), "Budget changes require an available action");
      const body = request.postDataJSON();
      budgetRequests.push({ operation, body });
      assert.equal(body.expected_revision, run.run_revision);
      assert.ok(body.request_key && body.reason);
      run.run_revision += 1;
      run.event_head += 1;
      if (budgetConflict) {
        budgetConflict = false;
        return route.fulfill({ status: 409, json: { error: { message: "运行版本已更新，请重试" } } });
      }
      run.ranking_budget_enabled = operation === "enable";
      return route.fulfill({ json: { ...run, operation: `ranking_budget_${operation}`,
        operation_status: "accepted", available_actions: actionsFor(run) } });
    }
    if (!artifact && id === delayedRunId) {
      delayedRequest?.();
      await new Promise((resolve) => { releaseDelayed = resolve; });
    }
    if (artifact === "metadata") return route.fulfill({ json: makeMetadata(run) });
    if (artifact === "graph") return route.fulfill({ json: makeGraph(run, url.searchParams.get("projection")) });
    if (artifact === "source") {
      assert.equal(url.searchParams.get("selection_ref"), sourceRef);
      sourceReads += 1;
      if (delaySource) {
        sourceRequested?.();
        await new Promise((resolve) => { releaseSource = resolve; });
      }
      await route.fulfill({ json: {
        contract_version, recognition_run_id: id, analysis_id: `analysis-${id}`,
        document_hash: `document-${id}`, structure_hash: id, filename: run.input.filename,
        content: makeMetadata(run).content, anchors: [sourceAnchor(run)],
        selection: { section_node_id: "section:0", span_refs: [] },
      } });
      sourceCompleted?.();
      return;
    }
    if (artifact === "events") return route.fulfill({ status: 204 });
    return route.fulfill({ json: {
      ...run, identities: {}, artifacts: { source: "ready", structure: "ready", metadata: "ready", graph: "ready" },
      progress: {
        model_calls: 0, tasks_attempted: 0, phase_counts: {}, stop_reason: null,
        records_planned: 0, records_examined: 0, records_incomplete: 0, records_unattempted: 0,
        decisions: { supported: 0, unsupported: 0, undetermined: 0, prerequisite_failed: 0 },
        event_head: 1, artifact_revision: 1,
      },
      available_actions: actionsFor(run), error: null,
    } });
  }
  return route.fulfill({ json: [] });
});
const history = page.getByLabel("文档分析历史", { exact: true });
const tasks = history.getByRole("button", { name: /^查看分析 / });
const status = page.getByLabel("文档分析运行状态", { exact: true });
const workspace = page.getByLabel("文档分析工作区", { exact: true });
const details = page.getByLabel("文档分析详情", { exact: true });
const preview = page.getByLabel("原始文档预览", { exact: true });
const metadataTab = page.getByRole("tab", { name: "节点元数据", exact: true });
const graphTab = page.getByRole("tab", { name: "关系图谱", exact: true });
const budgetControl = status.getByLabel("排序预算限制", { exact: true });
const layoutChecks = [];
const assertNoHorizontalOverflow = async () => {
  const dimensions = await page.evaluate(() => ({
    viewport: document.documentElement.clientWidth,
    page: document.documentElement.scrollWidth,
  }));
  assert.ok(dimensions.page <= dimensions.viewport + 1, `Page overflows horizontally: ${JSON.stringify(dimensions)}`);
};
const close = async () => {
  await status.getByRole("button", { name: "关闭视图", exact: true }).click();
  await expect(page).not.toHaveURL(/documentRun=/);
  await expect(status).toHaveCount(0);
};

try {
  await page.goto(`${origin}/analysis?tab=document`, { waitUntil: "domcontentloaded", timeout: 120_000 });
  await expect(tasks).toHaveCount(10);
  await expect(tasks.filter({ hasText: "同名报告.docx" })).toHaveCount(2);
  await tasks.first().click();
  await expect(status).toContainText("history-12");
  await expect(page).toHaveURL(/documentRun=history-12/);
  await expect(budgetControl).toContainText("已启用");
  await budgetControl.getByRole("button", { name: "禁用排序预算限制", exact: true }).click();
  await expect(budgetControl).toContainText("已禁用");
  await expect(budgetControl).toContainText("预算统计已暂停（显示启用期间累计值）");
  await expect(budgetControl).toContainText("禁用期间不预扣或累计排序预算");
  await expect(budgetControl).toContainText("不关闭 embedding 召回或 reranker 精排");
  await expect(status.getByRole("button", { name: "恢复", exact: true })).toBeVisible();
  assert.equal(runs[0].status, "paused", "Changing the ranking budget must not resume the run");
  await graphTab.click();
  await expect(details.getByLabel("图谱排序预算限制", { exact: true })).toContainText("已禁用");
  await details.locator("summary").filter({ hasText: "记录处理顺序与检索诊断" }).click();
  await expect(details).toContainText("预留 tokens 100");
  await expect(details).toContainText("预算统计已暂停（显示启用期间累计值）");
  await page.screenshot({ path: path.join(output, "ranking-budget-disabled.png"), fullPage: true });
  budgetConflict = true;
  await budgetControl.getByRole("button", { name: "启用排序预算限制", exact: true }).click();
  await expect(status.getByRole("alert")).toContainText("运行版本已更新，请重试");
  await expect(budgetControl).toContainText("已禁用");
  await expect(status).toContainText(`revision ${runs[0].run_revision} / event ${runs[0].event_head}`);
  await budgetControl.getByRole("button", { name: "启用排序预算限制", exact: true }).click();
  await expect(budgetControl).toContainText("已启用");
  await expect(status.getByRole("alert")).toHaveCount(0);
  await expect(details.getByLabel("图谱排序预算限制", { exact: true })).toContainText("已启用");
  await expect(details).toContainText("预留 tokens 100");
  assert.equal(runs[0].status, "paused");
  assert.equal(new Set(budgetRequests.map((item) => item.body.request_key)).size, 3);
  await page.screenshot({ path: path.join(output, "ranking-budget-enabled.png"), fullPage: true });
  await metadataTab.click();
  layoutChecks.push("explicit budget disable/enable keeps run paused, preserves accounting and handles CAS conflicts");
  await expect(preview.locator(".tiptap")).toContainText("合成预览 history-12");
  await expect(metadataTab).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("tab", { name: "分层元数据", exact: true })).toHaveCount(0);
  const chapter = workspace.getByLabel("Word 章节树", { exact: true }).first();
  await expect(chapter).toBeVisible();
  const [historyBox, workspaceBox, chapterBox, previewBox, detailsBox] = await Promise.all([
    history.boundingBox(), workspace.boundingBox(), chapter.boundingBox(), preview.boundingBox(), details.boundingBox(),
  ]);
  assert.ok(historyBox && workspaceBox && chapterBox && previewBox && detailsBox);
  assert.ok(historyBox.x + historyBox.width <= workspaceBox.x, "History must be left of the analysis workspace");
  assert.ok(chapterBox.x + chapterBox.width <= previewBox.x, "Chapter tree must be left of the preview");
  assert.ok(previewBox.x + previewBox.width <= detailsBox.x, "Metadata/graph details must be right of the preview");
  await assertNoHorizontalOverflow();
  const editor = await preview.locator(".tiptap").elementHandle();
  await graphTab.click();
  await expect(graphTab).toHaveAttribute("aria-selected", "true");
  await expect(chapter).toBeVisible();
  await expect(preview.locator(".tiptap")).toBeVisible();
  const relation = details.getByRole("button").filter({ hasText: "使用设备（合成关系）" });
  await relation.click();
  await expect(details.getByText("主体 → 对象", { exact: true })).toBeVisible();
  const evidence = details.getByRole("button", { name: sourceRef, exact: true });
  await evidence.click();
  await expect(workspace.getByText("已从关系图谱定位原文", { exact: true })).toBeVisible();
  await expect(workspace.getByText("· 1 个物理证据锚点", { exact: true })).toBeVisible();
  await expect(graphTab).toHaveAttribute("aria-selected", "true");
  await expect(preview.locator('[data-evidence-id="evidence:0"]')).toContainText(sourceText(runs[0]));
  await expect(preview).not.toContainText(/来源已失效|原文证据未出现在此预览中|来源定位失败/);
  await expect(preview.getByText("1 级章节", { exact: true })).toBeVisible();
  await metadataTab.click();
  await expect(details.getByRole("heading", { name: "合成证据章节", exact: true })).toBeVisible();
  await graphTab.click();
  await expect(details.getByText("主体 → 对象", { exact: true })).toBeVisible();
  await assertNoHorizontalOverflow();
  await page.screenshot({ path: path.join(output, "graph-source-replay.png"), fullPage: true });

  delaySource = true;
  const sourceStarted = new Promise((resolve) => { sourceRequested = resolve; });
  const sourceFinished = new Promise((resolve) => { sourceCompleted = resolve; });
  await evidence.click();
  await sourceStarted;
  await chapter.getByText("合成第二章节", { exact: true }).click();
  await expect(workspace.getByText("正在校验并定位图谱证据", { exact: true })).toHaveCount(0);
  releaseSource();
  await sourceFinished;
  delaySource = false;
  await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  await expect(workspace.getByText("已从关系图谱定位原文", { exact: true })).toHaveCount(0);
  await expect(graphTab).toHaveAttribute("aria-selected", "true");
  await expect(preview.locator('[data-source-block-id="p:1"]')).toHaveClass(/document-location-highlight/);
  await metadataTab.click();
  await expect(metadataTab).toHaveAttribute("aria-selected", "true");
  await expect(details.getByRole("heading", { name: "合成第二章节", exact: true })).toBeVisible();
  assert.ok(await preview.locator(".tiptap").evaluate((node, previous) => node === previous, editor),
    "Switching detail tabs must preserve the Word preview editor");
  await editor.dispose();
  await page.screenshot({ path: path.join(output, "workspace-desktop.png"), fullPage: true });
  layoutChecks.push("desktop history/tree/preview/details geometry", "detail tabs preserve chapter tree and preview editor",
    "compact graph with long labels replays a physical source span while staying on graph tab",
    "chapter selection survives an aborted or late source response");
  await close();
  await page.reload();
  await expect(tasks).toHaveCount(10);
  await tasks.nth(1).click();
  await expect(status).toContainText("history-11");
  await expect(budgetControl.getByRole("button")).toHaveCount(0);
  await page.reload();
  await expect(status).toContainText("history-11");
  await close();
  await tasks.nth(2).click();
  await expect(status).toContainText("history-10");
  await expect(budgetControl).toContainText("运行期间不可调整排序预算限制，请先暂停运行");
  await expect(budgetControl.getByRole("button")).toHaveCount(0);
  await close();
  layoutChecks.push("read-only and running states do not expose budget mutation controls");
  await history.getByRole("button", { name: "下一页" }).click();
  await expect(tasks).toHaveCount(2);
  await tasks.last().click();
  await expect(status).toContainText("history-1");
  await close();
  await history.getByRole("button", { name: "上一页" }).click();
  await expect(tasks).toHaveCount(10);

  delayedRunId = "history-12";
  const requested = new Promise((resolve) => { delayedRequest = resolve; });
  await tasks.first().click();
  await requested;
  await tasks.nth(1).click();
  await expect(status).toContainText("history-11");
  delayedRunId = null;
  releaseDelayed();
  await expect(status).not.toContainText("history-12");
  await close();
  assert.equal(createCount, 0, "History reads must never start a new analysis");

  failHistory = true;
  await history.getByRole("button", { name: "刷新历史" }).click();
  await expect(history.getByRole("alert")).toContainText("加载失败");
  await expect(history).not.toContainText("暂无分析历史");
  failHistory = false;
  await history.getByRole("button", { name: "重试", exact: true }).click();
  await expect(history.getByRole("alert")).toHaveCount(0);

  await page.locator('input[type="file"]').setInputFiles({ name: "新上传.docx", mimeType: "application/vnd.openxmlformats-officedocument.wordprocessingml.document", buffer: Buffer.from("synthetic upload") });
  await page.getByLabel("选择本体类型", { exact: true }).selectOption(rootIri);
  assert.equal(createCount, 0, "File selection must not upload");
  await page.getByRole("button", { name: "开始分析", exact: true }).click();
  await expect(status).toContainText("new-1");
  await expect(tasks.first()).toContainText("新上传.docx");
  await close();
  await page.goto(`${origin}/analysis?tab=document`);
  await tasks.first().click();
  await expect(status).toContainText("new-1");
  await page.screenshot({ path: path.join(output, "history-desktop.png"), fullPage: true });
  // The shared application shell keeps a fixed sidebar; phone-width shell
  // adaptation is outside this component's layout contract.
  for (const width of [768]) {
    await page.setViewportSize({ width, height: 1100 });
    await expect(history).toBeVisible();
    await expect(preview).toBeVisible();
    await expect(details).toBeVisible();
    await assertNoHorizontalOverflow();
    await graphTab.click();
    await expect(graphTab).toHaveAttribute("aria-selected", "true");
    await expect(preview.locator(".tiptap")).toBeVisible();
    const treeButton = page.getByRole("button", { name: "章节树", exact: true });
    await treeButton.click();
    const treeSheet = page.getByRole("dialog");
    await expect(treeSheet.getByText("合成文档章节", { exact: true })).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(treeSheet).toHaveCount(0);
    await expect(graphTab).toHaveAttribute("aria-selected", "true");
    await tasks.nth(1).click();
    await expect(status).toContainText("history-12");
    await expect(preview.locator(".tiptap")).toContainText("合成预览 history-12");
    await tasks.first().click();
    await expect(status).toContainText("new-1");
    await expect(preview.locator(".tiptap")).toContainText("合成预览 new-1");
    await assertNoHorizontalOverflow();
    await page.screenshot({ path: path.join(output, `history-${width}.png`), fullPage: true });
    layoutChecks.push(`${width}px history navigation, chapter sheet and preview remain usable without page overflow`);
  }
  await page.setViewportSize({ width: 1920, height: 1100 });
  await expect(chapter).toBeVisible();
  await expect(preview.locator(".tiptap")).toBeVisible();
  const [wideHistory, wideTree, widePreview, wideDetails] = await Promise.all([
    history.boundingBox(), chapter.boundingBox(), preview.boundingBox(), details.boundingBox(),
  ]);
  assert.ok(wideHistory && wideTree && widePreview && wideDetails);
  assert.ok(wideHistory.x + wideHistory.width <= wideTree.x);
  assert.ok(wideTree.x + wideTree.width <= widePreview.x);
  assert.ok(widePreview.x + widePreview.width <= wideDetails.x);
  await assertNoHorizontalOverflow();
  await graphTab.click();
  await expect(graphTab).toHaveAttribute("aria-selected", "true");
  await expect(preview.locator(".tiptap")).toBeVisible();
  await page.screenshot({ path: path.join(output, "workspace-1920.png"), fullPage: true });
  layoutChecks.push("1920px history/tree/preview/details geometry");
  assert.equal(createCount, 1);
  assert.equal(sourceReads, 2);
  assert.deepEqual(writes, [
    { method: "POST", pathname: "/api/document-analysis/runs/history-12/ranking-budget/disable" },
    { method: "POST", pathname: "/api/document-analysis/runs/history-12/ranking-budget/enable" },
    { method: "POST", pathname: "/api/document-analysis/runs/history-12/ranking-budget/enable" },
    { method: "POST", pathname: "/api/document-analysis/runs" },
  ]);
  assert.deepEqual(errors, []);
  const report = { status: "passed", browser: browser.version(), createCount, sourceReads, budgetRequests, output, layoutChecks,
    writes, scope: "Synthetic API UI regression; no backend or model execution" };
  await writeFile(path.join(output, "report.json"), JSON.stringify(report, null, 2) + "\n");
  console.log(JSON.stringify(report));
} catch (error) {
  await page.screenshot({ path: path.join(output, "failure.png"), fullPage: true });
  await writeFile(path.join(output, "failure.json"), JSON.stringify({ errors, consoleErrors, resourceFailures,
    reads, url: page.url(), body: await page.locator("body").innerText() }, null, 2));
  throw error;
} finally {
  releaseDelayed?.();
  releaseSource?.();
  await browser.close();
}
