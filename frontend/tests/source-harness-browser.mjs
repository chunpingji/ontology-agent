// Synthetic UI regression. Every API request is fulfilled locally; no model runs or real credentials.
import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";

const { chromium, expect: baseExpect } = await import(process.env.PLAYWRIGHT_MODULE || "playwright/test");
const expect = baseExpect.configure({ timeout: 15000 });
const origin = process.env.DOCUMENT_BROWSER_ORIGIN || "http://sldpr-demo.infilake.com:8081";
const output = process.env.DOCUMENT_BROWSER_OUTPUT || "/tmp/source-harness-v2-browser";
await mkdir(output, { recursive: true });
const runId = "synthetic-graph-v2";
const documentIri = "urn:document:synthetic-graph-v2";
const run = { contract_version: "document-analysis-runs-v1", extraction_protocol: "document-harness-v2",
  recognition_run_id: runId, run_revision: 1, event_head: 1, artifact_revision: 1,
  status: "paused", stage: "discover", available_actions: [], error: null,
  created_at: "2026-09-22T00:00:00Z", expires_at: null,
  input: { filename: "图谱分析合成验收.docx", root_class_iri: "urn:schema:Report", root_class_label: "报告" } };
const fullSource = { source_id: "source:1", text: "前😀物料编号：AX-07，含量下限为3.8 kg。", page: 2,
  section_id: "section:1", block_id: "block:1", row: null, column: null };
const source = { ...fullSource, text: "物料编号：AX-07", start: 2, end: 12 };
const valueSource = { ...source, text: "AX-07", start: 7, end: 12 };
const card = { iri: "urn:schema:Material", label: "中间体" };
const predicate = { iri: "https://example.test/material/identifier", label: "中间体标识",
  namespace: "https://example.test/material/", domain_text: "物料 <urn:schema:Material>" };
const entity = (id, label, extra = {}) => ({ id, label, role: "entity", class_iri: card.iri, class_label: card.label,
  state: "accepted", reason: "合成验收：原文指称与类型已核对", evidence: [source], ...extra });
const verification = { method: "llm", rule_id: null, rule_version: null, semantic_verdict: "accepted" };
const property = (id, subject_id, label, value, extra = {}) => ({ verification, id, subject_id, field_id: "field:1",
  card, predicate: { ...predicate, label }, predicate_iri: predicate.iri, label, value,
  source_value: source.text, source_unit: null, value_component: "span", value_evidence: [valueSource],
  state: "accepted", reason: "原文值与主体归属已核对", evidence: [source], ...extra });
const relation = (id, subject_id, object_id, extra = {}) => ({ verification, id, subject_id, object_id, label: "关联对象",
  predicate_iri: "urn:related", card, predicate: null, state: "accepted", polarity: "positive", conditions: [],
  reason: "合成关系证据", evidence: [source], ...extra });
const observation = (id, extra = {}) => ({ id, kind: "field", label: `原字段 ${id}`, field_id: `field:${id}`,
  value: "待对齐原值", candidate_subject_ids: [], object_id: null, reason: "合成观察独立保存",
  evidence: [source], discovery_cards: [], alignments: [], ...extra });
const graph = { coreferences: [], relation_groups: [], candidate_work: [], interpretation_tasks: [], protocol: "document-harness-v2", run_id: runId, revision: 1, status: "paused", stage: "evidence_review",
  progress: { completed_calls: 5, candidate_count: 20, fact_count: 8, phase: "graph", reading_windows: { total: 10, saved: 10, complete: 5, incomplete: 5, active: 0 }, scope_complete: false,
    reading: { total_characters: 4000, processed_characters: 2000, complete_characters: 800, complete: false },
    work_counts: { ready: 0, waiting: 1, pruned: 2, done: 5, failed: 0 },
    candidate_scope_limited: true, rule_verified_count: 0, llm_verified_count: 8, stage_costs: [
      { stage: "discover", calls: 2, seconds: 30, input_tokens: null, output_tokens: null, unmeasured_attempts: 0 },
      { stage: "type_alignment", calls: 3, seconds: 10, input_tokens: 0, output_tokens: 23, unmeasured_attempts: 0 },
    ] },
  // Root deliberately not first; d is four hops away and material has multiple parents.
  entities: [entity("material", "物料甲"), entity("root", "报告根", { role: "document_root", class_iri: "urn:schema:Report", class_label: "报告" }),
    entity("plan", "生产计划"), entity("workshop", "车间甲"), entity("d", "深层设备"),
    entity("detached", "未连接样品", { state: "unresolved" }), entity("negative", "否定关联对象"),
    entity("material-2", "02002668"), entity("material-3", "丙酮")],
  properties: [property("root-p", "root", "文档编号", "DOC-001"), property("p", "material", "中间体标识", "AX-07"),
    property("p-bound", "material", "含量下限", "3.8", { source_value: "3.8–6.6 kg", source_unit: "kg", value_component: "lower", state: "unresolved", value_evidence: [] }),
    property("deep-p", "d", "设备编号", "DEEP-04")],
  relations: [relation("01", "root", "plan"), relation("02", "plan", "material", { state: "candidate" }),
    relation("03", "material", "workshop"), relation("04", "workshop", "d"),
    relation("05", "workshop", "material"), relation("06", "d", "plan"),
    relation("07", "root", "material-2", { predicate_iri: "urn:uses", label: "使用物料" }),
    relation("08", "root", "material-3", { predicate_iri: "urn:uses", label: "使用物料", state: "unresolved" }),
    relation("negative-edge", "root", "negative", { polarity: "negative", conditions: ["特定条件下"] })],
  targets: [{ id: "target", subject_id: "material", kind: "relation", label: "待发现成分", predicate_iri: "urn:component", range_labels: ["成分"], state: "pending" }],
  observations: [observation("mixed", { label: "物料编号", value: "AX-07", candidate_subject_ids: ["root", "material"],
    discovery_cards: [{ iri: "urn:schema:Report", label: "报告", role: "document_properties" }, { ...card, role: "reading" }],
    alignments: [
      { subject_id: "root", card: { iri: "urn:schema:Report", label: "报告" }, property_ids: [], attempts: [{ state: "unmatched", reason: "报告属性菜单无物料标识", predicates: [] }] },
      { subject_id: "material", card, property_ids: ["p"], attempts: [{ state: "mapped", reason: "精确匹配", predicates: [predicate] }] },
    ] }), observation("deep", { label: "设备编号", candidate_subject_ids: ["d"] }),
    ...Array.from({ length: 11 }, (_, index) => observation(`unowned-${index}`)),
    observation("failure", { kind: "failure", label: "模型请求失败", reason: "合成请求超时" })],
};
for (const item of graph.entities) item.mentions = [{ ...item }];
graph.relation_groups.push({
  verification,
  id: "options", subject_id: "plan", subject_mention_id: "plan",
  object_ids: ["workshop", "negative"], object_mention_ids: ["workshop", "negative"],
  predicate_iri: "urn:area-options", label: "生产区域选项", card, predicate: null,
  state: "accepted", reason: "确认候选地点集合，尚未选择实际地点", evidence: [source],
  polarity: "positive", conditions: [], participation: "options", selection: "exactly_one",
  timing: "unspecified", timing_state: "unresolved", timing_reason: "原文未说明时间关系",
});
const material = graph.entities.find((item) => item.id === "material");
material.mentions.push({ ...material.mentions[0], id: "material-later", label: "甲号物料" });
graph.coreferences = [{ id: "coref-same", left_mention_id: "material", right_mention_id: "material-later",
  verdict: "same", basis: "explicit_alias", reason: "后文明确引用甲号物料，适用范围为本文档。",
  evidence: [source], proof: [source], applied: true },
{ id: "coref-unknown", left_mention_id: "material", right_mention_id: "material-2",
  verdict: "unresolved", basis: "insufficient", reason: "名称相似不能证明是同一物料。",
  evidence: [source], proof: [], applied: false }];
const writes = [], reads = [], errors = [], unexpected = [], checks = [], resourceFailures = [], consoleErrors = [];
let sourceMismatch = false;
let coreferenceDataAvailable = true;
const browser = await chromium.launch({ executablePath: process.env.DOCUMENT_BROWSER_CHROME || "/usr/bin/google-chrome",
  headless: true, args: ["--no-sandbox", "--no-proxy-server", "--host-resolver-rules=MAP sldpr-demo.infilake.com 127.0.0.1"] });
const context = await browser.newContext({ viewport: { width: 1440, height: 1050 } });
await context.addInitScript(() => {
  localStorage.setItem("slpra.token", "synthetic-local-only");
  localStorage.setItem("slpra.identity", JSON.stringify({ username: "synthetic", role: "senior_analyst" }));
});
const page = await context.newPage();
page.setDefaultTimeout(15000);
page.on("pageerror", (error) => errors.push(error.message));
page.on("console", (message) => { if (message.type() === "error") consoleErrors.push(message.text()); });
page.on("requestfailed", (request) => resourceFailures.push({ path: new URL(request.url()).pathname, error: request.failure()?.errorText }));
page.on("response", (response) => { if (response.status() >= 400) resourceFailures.push({ path: new URL(response.url()).pathname, status: response.status() }); });
await page.route("**/api/**", async (route) => {
  const request = route.request(), pathname = new URL(request.url()).pathname;
  reads.push(pathname);
  if (request.method() !== "GET") {
    writes.push({ method: request.method(), pathname });
    return route.fulfill({ status: 405, json: { detail: "Test forbids writes" } });
  }
  if (pathname === "/api/entities") return route.fulfill({ json: { items: [], total: 0 } });
  if (pathname === "/api/document-analysis/runs") return route.fulfill({ json: { contract_version: run.contract_version, items: [run], has_more: false } });
  if (pathname === `/api/document-analysis/runs/${runId}`) return route.fulfill({ json: run });
  if (pathname === `/api/document-analysis/runs/${runId}/harness-graph`) return route.fulfill({ json: coreferenceDataAvailable ? graph : {
    ...graph, coreferences: undefined,
    entities: graph.entities.map((item) => ({ ...item, mentions: undefined })),
  } });
  if (pathname.startsWith(`/api/document-analysis/runs/${runId}/harness-source/`)) return route.fulfill({ json: sourceMismatch ? { ...fullSource, text: "错位原文" } : fullSource });
  unexpected.push(pathname);
  return route.fulfill({ status: 404, json: { detail: "Unmocked request" } });
});
const screenshot = (name) => page.screenshot({ path: path.join(output, name), fullPage: true, animations: "disabled" });
try {
  await page.goto(`${origin}/analysis?tab=graph-analysis&documentRun=${runId}&documentIri=${encodeURIComponent(documentIri)}`, { waitUntil: "domcontentloaded", timeout: 60000 });
  const panel = page.getByTestId("source-harness-v2");
  await expect(panel.getByText("原文范围已处理 2,000 / 4,000 字符", { exact: true })).toBeVisible();
  await expect(panel.getByText("完整覆盖 800 / 4,000 字符", { exact: true })).toBeVisible();
  const header = page.getByTestId("graph-analysis-header");
  const document = panel.getByRole("region", { name: "当前分析文档", exact: true });
  const tabs = panel.getByRole("tablist", { name: "图谱分析分区", exact: true });
  const entityTab = tabs.getByRole("tab", { name: "实体图谱与属性", exact: true });
  const costTab = tabs.getByRole("tab", { name: "各阶段模型成本", exact: true });
  const observationTab = tabs.getByRole("tab", { name: "原文观察与对齐结果", exact: true });
  const workspace = panel.getByRole("region", { name: "实体图谱与属性", exact: true });
  const observations = panel.getByRole("region", { name: "原文观察与对齐结果", exact: true });
  const tree = workspace.getByRole("list", { name: "分层实体列表" });
  await expect(tree.locator('[data-entity-id="root"]')).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByRole("main").getByRole("heading", { level: 1 })).toHaveText("图谱分析");
  await expect(header.getByText("沿文档根逐层查看实体、属性与原文依据。", { exact: true })).toBeVisible();
  await expect(page.getByLabel("报告文档", { exact: true })).toHaveCount(0);
  const functions = page.getByRole("tablist", { name: "应用分析功能", exact: true });
  await expect(functions.getByRole("tab", { name: "文档分析", exact: true })).toBeVisible();
  await expect(functions.getByRole("tab", { name: "图谱分析", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("button", { name: "刷新结果", exact: true })).toHaveCount(1);
  await expect(header.getByRole("link", { name: "返回文档分析" })).toHaveCount(0);
  await expect(header.getByRole("button", { name: "新建分析", exact: true })).toBeVisible();
  await expect(document).toContainText("图谱分析合成验收.docx");
  await expect(document).toContainText("根类型：报告");
  await expect(document).toContainText("本轮原文阅读范围尚未处理完成");
  await expect(document).toContainText("已暂停");
  await expect(document.locator("time")).toHaveText(/更新于 \d{2}:\d{2}/);
  await expect(document).not.toContainText("示例数据");
  const headerBounds = await header.boundingBox(), documentBounds = await document.boundingBox();
  await page.screenshot({ path: path.join(output, "header-desktop.png"), animations: "disabled", clip: {
    x: headerBounds.x, y: headerBounds.y, width: headerBounds.width, height: documentBounds.y + documentBounds.height - headerBounds.y,
  } });
  await expect(entityTab).toHaveAttribute("aria-selected", "true");
  await expect(panel.getByRole("tabpanel", { name: "各阶段模型成本", exact: true })).toHaveCount(0);
  await expect(observations).toHaveCount(0);
  await expect(panel.locator('a[href*="-entities"], a[href*="-costs"], a[href*="-observations"]')).toHaveCount(0);
  await expect(workspace.getByRole("heading", { name: "文档编号：DOC-001" })).toBeVisible();
  const materialGroup = tree.locator('[data-predicate-iri="urn:uses"][data-subject-id="root"]');
  await expect(materialGroup).toHaveCount(1);
  await expect(tree.getByText("使用物料", { exact: true })).toHaveCount(1);
  await expect(tree.locator('[data-entity-id="material-2"]')).toContainText("关系已采信");
  await expect(tree.locator('[data-entity-id="material-3"]')).toContainText("关系未决");
  await materialGroup.getByRole("button").click();
  await expect(tree.locator('[data-entity-id="material-2"]')).toHaveCount(0);
  await expect(tree.locator('[data-entity-id="material-3"]')).toHaveCount(0);
  await expect(tree.locator('[data-entity-id="plan"]')).toBeVisible();
  await materialGroup.getByRole("button").press("Enter");
  await tree.locator('[data-entity-id="material-3"]').click();
  await expect(workspace.getByRole("heading", { name: "丙酮", exact: true })).toBeVisible();
  await tree.locator('[data-entity-id="root"]').click();
  await workspace.getByRole("button", { name: "收起生产计划的下级关系" }).click();
  await workspace.getByLabel("文档根实体层级树").screenshot({ path: path.join(output, "predicate-groups.png"), animations: "disabled" });
  checks.push("one predicate group for accepted and unresolved materials, independent collapse, keyboard expand and entity selection");
  await expect(tree.locator('[data-entity-id="d"]')).toHaveCount(0);
  await workspace.getByLabel("展开层级").selectOption("999999");
  await tree.locator('[data-entity-id="d"]').click();
  await expect(workspace.getByRole("heading", { name: "设备编号：DEEP-04" })).toBeVisible();
  await expect(tree.getByText("引用 · 不重复展开")).toHaveCount(2);
  checks.push("explicit root, four hops, cycles and multiple parents");
  await tree.locator('[data-entity-id="plan"]').first().click();
  await workspace.getByRole("tab", { name: /^关联关系/ }).click();
  await expect(workspace.getByText("备选对象组 · 择一：", { exact: false })).toBeVisible();
  await expect(workspace.getByText("原文未说明时间关系", { exact: true })).toBeVisible();
  await expect(workspace.getByText("确认候选地点集合，尚未选择实际地点", { exact: true })).toBeVisible();
  checks.push("grouped options visible with independent unresolved timing and original source");
  await tree.locator('[data-entity-id="d"]').first().click();
  await workspace.getByLabel("展开层级").selectOption("0");

  await workspace.getByRole("button", { name: "关系图", exact: true }).click();
  const graphPanel = workspace.getByRole("region", { name: "关系图面板", exact: true });
  const nodePanel = workspace.getByRole("region", { name: "节点属性面板", exact: true });
  const sigma = graphPanel.getByRole("img", { name: "Sigma.js 实体关系图" });
  await expect(sigma.locator("canvas").first()).toBeVisible();
  const controls = graphPanel.locator("details").filter({ hasText: "键盘操作与图中节点" });
  await controls.locator("summary").click();
  const graphNodes = controls.getByLabel("图中节点列表");
  const graphRelations = controls.getByLabel("图中关系列表");
  await expect(graphNodes.locator('[data-entity-id="d"]')).toHaveAttribute("aria-pressed", "true");
  const graphMaterialGroup = graphNodes.locator('[data-predicate-iri="urn:uses"][data-subject-id="root"]');
  await expect(graphMaterialGroup).toHaveCount(1);
  await expect(graphMaterialGroup).toHaveAttribute("aria-expanded", "true");
  await expect(graphNodes.locator('[data-entity-id="material-2"]')).toBeVisible();
  await expect(graphNodes.locator('[data-entity-id="material-3"]')).toBeVisible();
  await expect(graphNodes.locator('[data-entity-id="d"]')).toBeVisible();
  await expect(graphRelations.locator('[data-relation-id="relation:07"]')).toHaveAttribute("data-state", "accepted");
  await expect(graphRelations.locator('[data-relation-id="relation:08"]')).toHaveAttribute("data-state", "unresolved");
  await graphMaterialGroup.press("Enter");
  await expect(graphMaterialGroup).toHaveAttribute("aria-expanded", "false");
  await expect(graphNodes.locator('[data-entity-id="material-2"]')).toHaveCount(0);
  await graphMaterialGroup.press("Enter");
  await expect(graphMaterialGroup).toHaveAttribute("aria-expanded", "true");
  checks.push("Sigma.js graph starts fully expanded independently of tree depth; keyboard controls collapse predicates and preserve relation states");
  await graphNodes.locator('[data-entity-id="material"]').press("Enter");
  await expect(nodePanel.getByRole("heading", { name: "物料甲", exact: true })).toBeVisible();
  await expect(nodePanel.getByRole("heading", { name: "中间体标识：AX-07", exact: true })).toBeVisible();
  const coreference = nodePanel.locator("details").filter({ has: page.locator("summary", { hasText: "原文提及与共指" }) });
  await coreference.locator("summary").click();
  await expect(coreference).toContainText("2 处提及 · 2 项判定");
  await expect(coreference).toContainText("已归并到同一文档内实体");
  await expect(coreference).toContainText("名称相似不能证明是同一物料");
  await coreference.getByRole("button").first().click();
  await expect(page.getByRole("dialog")).toContainText(source.text);
  await page.getByRole("dialog").getByRole("button", { name: "Close" }).click();
  await page.screenshot({ path: path.join(output, "coreference.png"), animations: "disabled" });
  await coreference.locator("summary").click();
  checks.push("merged mentions, same/unresolved reasons and exact source dialog without write requests");
  const graphPanelBounds = await graphPanel.boundingBox(), nodePanelBounds = await nodePanel.boundingBox();
  assert.ok(graphPanelBounds.x + graphPanelBounds.width <= nodePanelBounds.x + 1, "desktop graph must be left of node properties");
  assert.ok(Math.abs(graphPanelBounds.y - nodePanelBounds.y) <= 1, "desktop graph and node properties must share a top edge");
  checks.push("desktop left-right graph layout and node click updates the property panel");
  await workspace.getByRole("button", { name: "适应画布" }).click();
  await screenshot("graph-desktop.png");
  await workspace.getByRole("button", { name: "层级树", exact: true }).click();
  await workspace.getByLabel("展开层级").selectOption("999999");
  await expect(tree.locator('[data-entity-id="material"]').first()).toHaveAttribute("aria-pressed", "true");
  await workspace.getByRole("button", { name: "含量下限", exact: true }).click();
  await expect(workspace.getByText("原值：3.8–6.6 kg · 下限（kg）", { exact: true })).toBeVisible();
  await costTab.click();
  await expect(workspace).toHaveCount(0);
  await entityTab.click();
  await expect(workspace.getByText("原值：3.8–6.6 kg · 下限（kg）", { exact: true })).toBeVisible();
  await expect(workspace.getByLabel("展开层级")).toHaveValue("999999");
  run.status = "finished";
  graph.status = "finished";
  graph.progress.scope_complete = true;
  graph.progress.reading.complete = true;
  graph.progress.reading.processed_characters = graph.progress.reading.total_characters;
  graph.progress.reading.complete_characters = graph.progress.reading.total_characters;
  const refreshedGraph = page.waitForResponse((response) => response.url().endsWith("/harness-graph") && response.ok());
  await header.getByRole("button", { name: "刷新结果", exact: true }).click();
  await refreshedGraph;
  await expect(document).toContainText("本轮已结束");
  await expect(document).toContainText("本轮原文阅读范围已处理");
  await expect(workspace.getByText("原值：3.8–6.6 kg · 下限（kg）", { exact: true })).toBeVisible();
  await expect(entityTab).toHaveAttribute("aria-selected", "true");
  checks.push("parallel function navigation, real run summary and refresh time, new analysis entry, refresh without resetting selection");
  await workspace.getByLabel("搜索实体或属性").fill("DEEP-04");
  await expect(tree.locator('[data-entity-id="d"]')).toBeVisible();
  await expect(tree.locator('[data-entity-id="root"]')).toBeVisible();
  await expect(workspace.getByRole("button", { name: "全部折叠" })).toBeDisabled();
  await workspace.getByRole("button", { name: "清除筛选", exact: true }).click();
  await workspace.getByText("未连接到文档根（2）", { exact: true }).click();
  await workspace.locator('[data-entity-id="detached"]').click();
  await expect(workspace.getByRole("heading", { name: "未连接样品", exact: true })).toBeVisible();
  checks.push("graph and tree selection, property bounds, search ancestors, detached entities");

  const costs = panel.getByRole("region", { name: "各阶段模型成本" });
  const graphReadsBeforeTabs = reads.filter((pathname) => pathname.endsWith("/harness-graph")).length;
  await costTab.click();
  await expect(costs).toContainText("Token 合计 未知");
  await expect(costs.getByRole("cell", { name: "0", exact: true })).toBeVisible();
  await expect(costs).toContainText("75%");
  await costTab.press("ArrowRight");
  await expect(observationTab).toHaveAttribute("aria-selected", "true");
  await expect(costs).toHaveCount(0);
  await expect(observations).toContainText("显示 1–10 条，共 13 条");
  await observations.getByRole("button", { name: "下一页" }).click();
  await expect(observations).toContainText("显示 11–13 条");
  await costTab.click();
  await observationTab.click();
  await expect(observations).toContainText("显示 11–13 条");
  await observations.getByLabel("搜索原文、属性或本体卡").fill("报告属性菜单无物料标识");
  await expect(observations).toContainText("显示 1–1 条，共 1 条");
  await entityTab.click();
  await observationTab.click();
  await expect(observations.getByLabel("搜索原文、属性或本体卡")).toHaveValue("报告属性菜单无物料标识");
  assert.equal(reads.filter((pathname) => pathname.endsWith("/harness-graph")).length, graphReadsBeforeTabs);
  assert.equal(new URL(page.url()).hash, "");
  checks.push("exclusive tabs, keyboard navigation, retained entity/filter/page state, no hash changes or graph reloads");
  await observations.getByLabel("观察主体").selectOption("entity:root");
  await observations.getByLabel("观察处理结果").selectOption("accepted");
  await expect(observations.getByRole("status")).toContainText("没有符合筛选条件的观察。");
  await observations.getByLabel("观察主体").selectOption("entity:material");
  await observations.getByRole("button", { name: "查看观察 mixed 详情" }).click();
  let dialog = page.getByRole("dialog", { name: "原文观察详情" });
  await expect(dialog).toContainText("报告属性菜单无物料标识");
  await expect(dialog).toContainText("属性核对原因：原文值与主体归属已核对");
  await expect(dialog).toContainText("所属本体命名空间：https://example.test/material/");
  await expect(dialog).toContainText("文档根属性指引");
  await screenshot("observation-desktop.png");
  await dialog.getByRole("button", { name: /AX-07.*查看原文/ }).last().click();
  dialog = page.getByRole("dialog", { name: "原文定位" });
  await expect(dialog.locator("mark")).toHaveText("AX-07");
  await expect(dialog.locator("blockquote")).toHaveText(fullSource.text);
  await dialog.getByRole("button", { name: "Close" }).click();
  checks.push("unknown usage, scoped results, pagination reset, independent card reasons and Unicode source");

  await observations.getByRole("button", { name: "重置", exact: true }).click();
  await entityTab.click();
  await workspace.getByRole("button", { name: "全部折叠" }).click();
  await observationTab.click();
  await observations.getByRole("button", { name: "查看观察 deep 详情" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "深层设备", exact: true }).click();
  await expect(entityTab).toHaveAttribute("aria-selected", "true");
  await expect(tree.locator('[data-entity-id="d"]')).toHaveAttribute("aria-pressed", "true");
  await workspace.getByRole("tab", { name: /关联观察/ }).click();
  await workspace.getByRole("button", { name: "查看该实体的原文观察" }).click();
  await expect(observationTab).toHaveAttribute("aria-selected", "true");
  await expect(observations.getByLabel("观察主体")).toHaveValue("entity:d");
  await expect(observations).toContainText("显示 1–1 条，共 1 条");
  sourceMismatch = true;
  await observations.getByRole("button", { name: "查看观察 deep 详情" }).click();
  await page.getByRole("dialog").getByRole("button", { name: /查看原文/ }).click();
  await expect(page.getByRole("dialog").getByRole("alert")).toContainText("已拒绝标注");
  await expect(page.getByRole("dialog").locator("mark")).toHaveCount(0);
  sourceMismatch = false;
  await page.getByRole("button", { name: "重新读取原文" }).click();
  await expect(page.getByRole("dialog").locator("mark")).toHaveText(source.text);
  await page.getByRole("dialog").getByRole("button", { name: "Close" }).click();
  checks.push("locate collapsed deep entity, linked observations, reject mismatched source, retry");

  await observations.getByRole("button", { name: "重置", exact: true }).click();
  await entityTab.click();
  await tree.locator('[data-entity-id="material"]').first().click();
  await screenshot("tree-desktop.png");
  const desktopOverflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1);
  assert.equal(desktopOverflow, false, "desktop page must not overflow horizontally");
  await page.setViewportSize({ width: 390, height: 844 });
  await screenshot("tree-mobile.png");
  const mobileOverflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1);
  assert.equal(mobileOverflow, false, "mobile page must not overflow horizontally");
  await observationTab.click();
  await observations.getByRole("button", { name: "查看观察 mixed 详情" }).click();
  await expect(page.getByRole("dialog", { name: "原文观察详情" })).toBeVisible();
  const bounds = await page.getByRole("dialog").boundingBox();
  assert.ok(bounds.x >= 0 && bounds.x + bounds.width <= 391);
  await screenshot("observation-mobile.png");
  checks.push("desktop and mobile layout");
  await page.getByRole("dialog").getByRole("button", { name: "Close" }).click();
  await entityTab.click();
  coreferenceDataAvailable = false;
  await header.getByRole("button", { name: "刷新结果", exact: true }).click();
  await expect(workspace.getByRole("status")).toContainText("原文提及与共指数据暂不可用");
  await tree.locator('[data-entity-id="plan"]').first().click();
  await tree.locator('[data-entity-id="material"]').first().click();
  await expect(workspace.getByRole("heading", { name: "物料甲", exact: true })).toBeVisible();
  await workspace.getByLabel("搜索实体或属性").fill("AX-07");
  await expect(tree.locator('[data-entity-id="material"]').first()).toBeVisible();
  await expect(workspace.getByRole("button", { name: "中间体标识", exact: true })).toBeVisible();
  coreferenceDataAvailable = true;
  await header.getByRole("button", { name: "刷新结果", exact: true }).click();
  await expect(workspace.locator("summary", { hasText: "原文提及与共指" })).toContainText("2 处提及 · 2 项判定");
  await expect(workspace.getByRole("status")).toHaveCount(0);
  checks.push("missing coreference fields preserve node selection and search; refresh restores saved decisions");
  assert.deepEqual(writes, []);
  assert.deepEqual(unexpected, []);
  assert.deepEqual(errors, []);
  assert.deepEqual(consoleErrors, []);
  checks.push("GET only, no unmocked API requests, no browser errors");
  console.log(JSON.stringify({ checks, reads: reads.length, output }));
} catch (error) {
  await screenshot("failure.png");
  console.error(error);
  process.exitCode = 1;
} finally {
  await writeFile(path.join(output, "results.json"), JSON.stringify({ checks, writes, reads, errors, consoleErrors, unexpected, resourceFailures, url: page.url(), text: (await page.locator("body").innerText()).slice(0, 1000) }, null, 2));
  await browser.close();
}
