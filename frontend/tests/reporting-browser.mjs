// All API responses are synthetic. Requires an external Playwright installation.
import assert from "node:assert/strict";
import { readFile, writeFile } from "node:fs/promises";
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || "playwright");
const fixture = JSON.parse(await readFile(new URL("./fixtures/reporting-browser.json", import.meta.url)));
const contracts = [{ contract_id: "ontology-1", kind: "ontology", family_id: "ontology", revision_no: 1, status: "published", definition: { classes: fixture.classes } },
  { contract_id: "view", kind: "view", family_id: "视图", revision_no: 1, status: "published", definition: { allowed_operations: ["project"] } }];
const ast = { node_id: "doc", kind: "document", children: [{ node_id: "p", kind: "paragraph", children: [
  { node_id: "v", kind: "value", text: "E-01", state: "ready", input_ref: { input_id: "rows", field_path: ["code"], record_id: "urn:E1" }, fact_refs: ["fact-one"], provenance_refs: [{ evidence_id: "original-cell" }] },
] }] };
const run = { run_id: "frozen-run", input_snapshot_id: "snapshot", source_bundle_id: "source", execution_status: "completed", material_status: "incomplete",
  review_status: "unreviewed", template_status: "draft", phase: "completed", attempt: 1, revision_no: 1, body_hash: "body-hash", body_ast: ast,
  artifacts: [{ artifact_id: "draft-artifact", purpose: "draft", format: "docx", file_hash: "file-hash" }] };
fixture.template.sections[0].title = "设备信息";
fixture.template.sections[0].groups[0].title = "设备明细";
const requirements = [
  { requirement_id: "ready", input_id: "rows", field_path: ["code"], origin_refs: ["table"], execution_scope_id: "root", required: true, activation: "active", satisfied: true, issue_refs: [] },
  { requirement_id: "gap", input_id: "rows", field_path: ["spec"], origin_refs: ["two"], execution_scope_id: "root", required: true, activation: "active", satisfied: false, issue_refs: ["missing-spec"] },
];
let previewCount = 0, reportCreated = false;
const frozen = new Map();
const sampleContent = { type: "doc", content: [{ type: "paragraph", content: [{ type: "text", text: "模板样例原文" }] }] };
let saved, preview, layoutRequest, compilationRequest, metadataSaved, sessionCreated = false;
let owner = "原责任人";
const requests = [], errors = [];
const browser = await chromium.launch({ executablePath: process.env.REPORTING_CHROME || "/usr/bin/google-chrome", args: ["--no-sandbox"] });
try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  await context.addInitScript(() => {
    localStorage.setItem("slpra.token", "synthetic-browser-token");
    window.EventSource = class { close() {} };
  });
  await context.route("**/api/**", async (route) => {
    const request = route.request(), path = new URL(request.url()).pathname;
    const payload = ["POST", "PATCH"].includes(request.method()) ? request.postDataJSON() : null;
    requests.push({ path, authorization: request.headers().authorization });
    let response;
    if (path === "/api/report-model-context") response = contracts[0];
    else if (path === "/api/report-contracts") response = contracts;
    else if (path === "/api/ast-templates/coverage-doc-classes") response = { capable: ["urn:Report"] };
    else if (path.endsWith("/training-pairs")) response = [];
    else if (path.endsWith("/revisions")) { saved = payload; response = { id: "fixture-2" }; }
    else if (path.endsWith("/compile")) {
      compilationRequest = payload;
      response = { valid: false, compilation_id: "compile", diagnostics: [{ code: "REVIEW_REQUIRED", message: "合成待确认事项" }] };
    }
    else if (path.startsWith("/api/ast-templates/")) {
      if (request.method() === "PATCH") { metadataSaved = payload; owner = payload.owner; }
      response = { id: path.split("/").at(-1), name: "合成模板", version: "v2", revision_no: 1,
        status: "draft", owner, doc_no: "TEST-01", iri_pattern: "urn:Report", sample_content_json: sampleContent,
        schema_hash: "schema-hash", schema_json: saved?.schema ?? fixture.template,
        versions: [{id: "fixture", version: "v1"}, {id: "fixture-2", version: "v2"}],
        default_source_job_id: "job", default_source_filename: "合成来源.docx" };
    }
    else if (path === "/api/entities") response = { items: [] };
    else if (path.endsWith("/annotated-document")) response = { content: sampleContent, relationships: [], doc_class: null };
    else if (path.endsWith("/evidence")) response = { candidates: [], commits: [], snapshot_id: "source-snapshot" };
    else if (path.endsWith("/evidence/coverage")) response = { availability: "available", material_status: "ready", completion: "complete", required_gaps: 0, diagnostics: [], tasks: [], snapshot_id: "source-snapshot" };
    else if (path === "/api/extraction/jobs") response = [{ id: "job", source_filename: "合成来源.docx", status: "reviewing" }, { id: "other-job", source_filename: "另一来源.docx", status: "reviewing" }];
    else if (path.endsWith("/reports")) response = reportCreated ? [{ id: "history", job_id: "job", report_run_id: "frozen-run", report_artifact_id: "draft-artifact", file_size: 12, created_at: "2026-09-06T08:00:00Z", actor: "fixture" }] : [];
    else if (path === "/api/report-previews") {
      if (payload.mode === "layout") { layoutRequest = payload; response = { body_ast: { kind: "document", children: [] } }; }
      else {
        preview = payload;
        const id = payload.mode === "report" ? "frozen-run" : "data-run-" + ++previewCount;
        reportCreated ||= payload.mode === "report";
        response = { ...run, run_id: id, input_snapshot_id: "snapshot-" + id };
        frozen.set(id, { run: response, snapshot: { input_snapshot_id: response.input_snapshot_id, source_bundle: { template: payload.draft_schema, preview_mode: payload.mode, sources: payload.source_bindings }, coverage: requirements,
          material_status: "incomplete", blocking_issues: [{ issue_id: "missing-spec", code: "REQUIRED_INPUT_UNMET", state: "missing", message: "缺少设备规格" }] } });
      }
    }
    else if (/\/report-runs\/[^/]+\/inputs$/.test(path)) response = frozen.get(path.split("/")[3]).snapshot;
    else if (/\/report-runs\/[^/]+\/outputs$/.test(path)) response = ["table", "two"].map((output_id) => ({ id: output_id, payload: { output_id, execution_scope_id: "root", execution_status: "completed", inactive: false, output_ast: ast } }));
    else if (/\/report-runs\/[^/]+\/artifacts\/draft-artifact$/.test(path)) {
      await route.fulfill({ status: 200, contentType: "application/vnd.openxmlformats-officedocument.wordprocessingml.document", body: "synthetic-docx" }); return;
    }
    else if (/\/report-runs\/[^/]+$/.test(path)) response = frozen.get(path.split("/")[3]).run;
    else if (path.endsWith("/content-versions")) response = [{ id: "content", content_hash: "content-hash", payload: {}, actor: "fixture" }];
    else if (path.endsWith("/reviews")) response = [{ id: "review", actor: "qa-fixture", payload: { decision: "approved", reason: "合成审核" } }];
    else if (path.endsWith("/signing-sessions")) {
      if (request.method() === "POST") { sessionCreated = true; response = { id: "session", status: "open", revision_no: 0, content_hash: "content-hash", signatures: [], events: [], envelope_ids: [], policy: { signature_slots: [] } }; }
      else response = sessionCreated ? [{ id: "session", status: "open" }] : [];
    } else { errors.push("Unexpected API route: " + path); response = {}; }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(response) });
  });
  const page = await context.newPage();
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto((process.env.REPORTING_BROWSER_ORIGIN || "http://127.0.0.1:3107") + "/settings/ast-templates/fixture", { waitUntil: "networkidle" });
  const workspace = page.getByRole("tablist", { name: "模板工作区" });
  await workspace.waitFor();
  assert.deepEqual(await workspace.getByRole("tab").allTextContents(), ["基本信息", "源文档", "AST模板定义", "报告预览"]);
  assert.equal(await page.getByRole("tab", { name: "基本信息", exact: true }).getAttribute("aria-selected"), "true");
  await page.getByLabel("责任人", { exact: true }).fill("新责任人");
  await page.getByRole("button", { name: "保存基本信息", exact: true }).click();
  await page.getByText("已保存", { exact: true }).waitFor();
  assert.equal(metadataSaved.owner, "新责任人");
  assert.equal(Object.hasOwn(metadataSaved, "status"), false);
  await page.getByRole("tab", { name: "源文档", exact: true }).click();
  await page.getByRole("button", { name: "合成来源.docx" }).click();
  await page.getByRole("region", { name: "关系图谱识别结果" }).waitFor();
  await page.getByText("模板样例原文", { exact: true }).waitFor();
  await page.getByRole("tab", { name: "AST模板定义", exact: true }).click();
  const definitionPanel = page.getByRole("complementary", { name: "输出模板定义" });
  const sampleBounds = await page.getByText("模板样例原文", { exact: true }).boundingBox();
  const definitionBounds = await definitionPanel.boundingBox();
  assert(sampleBounds.x + sampleBounds.width <= definitionBounds.x);
  const splitter = page.getByTitle("拖动调整宽度 · 双击复位", { exact: true });
  const splitterBounds = await splitter.boundingBox();
  await page.mouse.move(splitterBounds.x, splitterBounds.y + 200);
  await page.mouse.down();
  await page.mouse.move(splitterBounds.x - 80, splitterBounds.y + 200);
  await page.mouse.up();
  assert((await definitionPanel.boundingBox()).width >= definitionBounds.width + 75);
  await splitter.dblclick();
  assert.equal((await definitionPanel.boundingBox()).width, definitionBounds.width);
  await page.getByRole("button", { name: "设备表", exact: true }).click();
  await page.getByRole("tab", { name: "输入变量", exact: true }).click();
  assert.match(await page.locator("body").innerText(), /共享影响：设备表、共享说明/);
  await page.getByLabel("显示名称", { exact: true }).fill("设备清单");
  assert.equal(await page.getByLabel("添加本体字段").locator("option[value='urn:code']").count(), 1);
  await page.getByRole("button", { name: "共享说明", exact: true }).click();
  assert.equal(await page.getByLabel("显示名称", { exact: true }).inputValue(), "设备清单");
  await page.getByRole("button", { name: "上移内容", exact: true }).nth(1).click();
  await page.getByRole("button", { name: "校验语义", exact: true }).click();
  await page.getByText("请修复以下问题", { exact: true }).waitFor();
  assert.equal(compilationRequest.draft_schema.definitions.inputs.rows.label, "设备清单");
  assert.equal(await page.getByRole("button", { name: "发布此修订", exact: true }).count(), 0);
  await page.getByText("投影与逐字段约束", { exact: true }).click();
  const pendingProjection = "{ 尚未应用的配置";
  await page.getByLabel("投影与逐字段约束", { exact: true }).fill(pendingProjection);
  await page.getByRole("tab", { name: "报告预览", exact: true }).click();
  const reportTab = page.getByRole("tabpanel", { name: "报告预览", exact: true });
  const progress = reportTab.getByRole("region", { name: "生成进度", exact: true });
  const structure = reportTab.getByRole("region", { name: "报告结构", exact: true });
  const progressBounds = await progress.boundingBox(), structureBounds = await structure.boundingBox();
  assert.equal(structureBounds.width, 420);
  assert.equal(progressBounds.y, structureBounds.y);
  assert(progressBounds.x + progressBounds.width <= structureBounds.x + 1);
  assert.equal(await reportTab.getByLabel("适用时间", { exact: true }).isVisible(), false);
  assert.equal(await structure.getByRole("button", { name: "下载报告", exact: true }).isDisabled(), true);
  assert.match(await structure.innerText(), /待检查/);
  assert.match(await progress.innerText(), /0%/);
  assert.equal(previewCount, 0); // Opening the tab never creates a report or a snapshot.
  await Promise.all([
    page.waitForResponse((response) => response.url().endsWith("/report-previews")),
    page.getByRole("button", { name: "版式预览", exact: true }).click(),
  ]);
  assert.equal(layoutRequest.draft_schema.definitions.inputs.rows.label, "设备清单");
  await reportTab.locator("summary").filter({ hasText: "来源与生成设置" }).click();
  await page.getByLabel("适用时间", { exact: true }).fill("2026-09-06");
  await reportTab.locator("summary").filter({ hasText: "来源与生成设置" }).click();
  await page.getByRole("button", { name: "刷新覆盖率", exact: true }).click();
  await structure.getByRole("button", { name: "未满足 1", exact: true }).waitFor();
  assert.equal(preview.mode, "data");
  assert.match(await structure.innerText(), /50%/);
  assert.match(await progress.innerText(), /60%/);
  await structure.getByRole("button", { name: "未满足 1", exact: true }).click();
  await structure.getByText("缺少设备规格", { exact: true }).waitFor();
  assert.equal(await structure.getByRole("button", { name: "共享说明 有缺口", exact: true }).getAttribute("aria-pressed"), "true");
  await page.getByRole("button", { name: "E-01", exact: true }).click();
  assert.match(await structure.getByRole("complementary", { name: "输入与依据" }).innerText(), /fact-one/);
  assert.equal(preview.draft_schema.definitions.inputs.rows.label, "设备清单");
  assert.equal(preview.source_bindings.doc.job_id, "job");
  await page.getByRole("button", { name: "生成报告", exact: true }).click();
  await page.getByRole("dialog", { name: "覆盖率不完整" }).waitFor();
  await page.getByRole("button", { name: "仍然生成", exact: true }).click();
  await reportTab.getByRole("region", { name: "历史报告" }).waitFor();
  assert.equal(preview.mode, "report");
  const downloadEvent = page.waitForEvent("download");
  await structure.getByRole("button", { name: "下载报告", exact: true }).click();
  assert.match((await downloadEvent).suggestedFilename(), /draft-artifa/);
  assert.match(await progress.innerText(), /100%/);
  await reportTab.locator("summary").filter({ hasText: /^正文审核与签署$/ }).click();
  await page.getByRole("button", { name: "创建签署会话", exact: true }).click();
  assert.equal(await page.getByRole("button", { name: "封装正式报告", exact: true }).isDisabled(), true);
  await page.getByRole("tab", { name: "AST模板定义", exact: true }).click();
  assert.equal(await page.getByLabel("显示名称", { exact: true }).inputValue(), "设备清单");
  assert.equal(await page.getByLabel("投影与逐字段约束", { exact: true }).inputValue(), pendingProjection);
  await page.getByRole("tab", { name: "报告预览", exact: true }).click();
  assert.equal(await reportTab.getByLabel("适用时间", { exact: true }).inputValue(), "2026-09-06");
  await page.getByRole("button", { name: "E-01", exact: true }).waitFor();
  assert.equal(await page.getByRole("button", { name: "封装正式报告", exact: true }).isDisabled(), true);
  // Refresh coverage independently; the generated report and its signing session survive.
  await page.getByRole("button", { name: "刷新覆盖率", exact: true }).click();
  await page.getByRole("button", { name: "返回已生成报告", exact: true }).waitFor();
  assert.equal(await page.getByRole("button", { name: "封装正式报告", exact: true }).isDisabled(), true);
  await page.getByRole("button", { name: "返回已生成报告", exact: true }).click();
  // A changed source must not relabel frozen coverage as a result of the new source.
  await reportTab.locator("summary").filter({ hasText: "来源与生成设置" }).click();
  await reportTab.getByLabel("报告来源 doc", { exact: true }).selectOption("other-job");
  await reportTab.getByRole("status").filter({ hasText: "上次固定的检查结果" }).waitFor();
  await reportTab.getByLabel("报告来源 doc", { exact: true }).selectOption("job");
  await reportTab.locator("summary").filter({ hasText: "来源与生成设置" }).click();
  assert.equal(await reportTab.getByRole("status").count(), 0);
  await page.screenshot({path: "/tmp/report-preview-dashboard-desktop.png"});
  await page.setViewportSize({ width: 800, height: 1000 });
  assert((await structure.boundingBox()).y >= (await progress.boundingBox()).y + (await progress.boundingBox()).height);
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
  await page.screenshot({path: "/tmp/report-preview-dashboard-narrow.png"});
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.getByRole("tab", { name: "AST模板定义", exact: true }).click();
  await page.getByRole("button", { name: "保存新修订", exact: true }).click();
  await page.waitForURL("**/settings/ast-templates/fixture-2");
  await page.waitForLoadState("networkidle");
  assert.equal(saved.schema.sections[0].groups[0].units[0].output_id, "two");
  assert.equal(saved.schema.sections[0].groups[0].units[1].origin.evidence_id, "original-cell");
  assert.equal(saved.schema.definitions.inputs.rows.input_id, "rows");
  await page.getByRole("tab", { name: "AST模板定义", exact: true }).click();
  await page.getByRole("button", { name: "设备表", exact: true }).click();
  await page.getByRole("tab", { name: "数据绑定", exact: true }).click();
  await page.getByLabel("来源方式", { exact: true }).selectOption("derived");
  await page.getByLabel("规则或业务数据定义", { exact: true }).selectOption("view");
  await page.getByLabel("视图操作", { exact: true }).waitFor();
  assert.equal(await page.getByLabel("视图操作", { exact: true }).inputValue(), "project");
  assert.equal(requests.filter((r) => r.authorization !== "Bearer synthetic-browser-token").length, 0);
  assert.deepEqual(errors, []);
  await page.getByRole("tab", { name: "基本信息", exact: true }).click();
  await page.getByRole("combobox", { name: "版本", exact: true }).click();
  await page.getByRole("option", { name: "v1", exact: true }).click();
  await page.waitForURL("**/settings/ast-templates/fixture");
  assert(!requests.some((request) => request.path.endsWith("/ast-coverage")));
  const result = { status: "pass", synthetic_api: true, checks: ["019 workspace tabs and default page", "b6f9bd4 progress/structure layout and responsive stacking", "real V2 coverage and missing-item navigation", "draft generation and artifact download", "metadata editing and version switching", "source document and evidence review", "sample left and resizable definition right", "shared input editing across tabs", "stable IDs and origin after reorder/save", "unsaved draft preview and stale source notice", "preview and signing state across tabs and coverage refresh", "formal envelope disabled for incomplete material", "exact ontology menus and view controls", "authenticated requests without legacy report execution"], api_requests: requests.length };
  if (process.env.REPORTING_BROWSER_RESULT) await writeFile(process.env.REPORTING_BROWSER_RESULT, JSON.stringify(result, null, 2) + "\n");
  console.log(JSON.stringify(result));
} finally { await browser.close(); }
