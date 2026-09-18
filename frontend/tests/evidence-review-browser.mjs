// Isolated UI regressions: every API request is intercepted with synthetic data.
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const { chromium, expect } = await import(process.env.PLAYWRIGHT_MODULE || "playwright/test");
const fixture = JSON.parse(await readFile(new URL("./fixtures/reporting-browser.json", import.meta.url)));
const anchor = { document_hash: "a".repeat(64), parser_version: "4", structure_hash: "b".repeat(64),
  evidence_id: "paragraph-1", section_node_id: "section:1", block_id: "paragraph:1:0", physical_page_number: 1 };
const sampleContent = { type: "doc", content: [{ type: "paragraph", attrs: { evidenceId: anchor.evidence_id },
  content: [{ type: "text", text: "合成证据来源" }] }],
  analysis: { ...anchor, evidence_units: [{ ...anchor, text: "合成证据来源" }] } };
const candidate = (id, text) => ({ candidate_id: id, revision: 1, kind: "entity", text, class_iri: "urn:Report", class_label: "药物产品",
  assertion_status: "affirmed", validation_status: "passed", validation_issues: [], review_status: "confirmed", review_source: "automatic",
  commit_status: "not_requested", provenance: [{ kind: "document", anchors: [anchor], excerpts: ["合成证据来源"] }], bindings: [], positive_eligible: true });
const graphSchema = { document_class_iri: "urn:Report", document_label: "来源 A.docx", classes: {
  "urn:Report": { label: "CMC 报告", parents: [], properties: [{ iri: "urn:title", label: "文档标题" }], relationships: [
    { iri: "urn:hasProductionPlan", label: "生产计划", range: ["urn:Plan"] },
    { iri: "urn:previousPlan", label: "原生产计划", range: ["urn:Plan"] },
  ] },
  "urn:Plan": { label: "生产计划", parents: [], properties: [{ iri: "urn:plannedBatch", label: "计划批量" }], relationships: [] },
  "urn:Product": { label: "药物产品", parents: [], properties: [], relationships: [] },
} };
const documentCandidate = (id, text) => ({ ...candidate(id, text), class_label: "CMC 报告", identity: { document_root: anchor.document_hash } });
const jobs = {
  job: { graph_schema: graphSchema, candidates: [documentCandidate("one", "合成产品 A"),
    { ...candidate("plan", "临床备样计划"), class_iri: "urn:Plan", class_label: "生产计划" },
    { ...candidate("batch", ""), kind: "property", subject: { candidate_id: "plan", revision: 1 }, predicate_iri: "urn:plannedBatch",
      predicate_label: "计划批量", literal: { raw_value: "12.00", raw_unit: "kg", canonical_unit: "kg" } },
    { ...candidate("edge", ""), kind: "relationship", subject: { candidate_id: "one", revision: 1 }, object: { candidate_id: "plan", revision: 1 },
      predicate_iri: "urn:hasProductionPlan", predicate_label: "生产计划" },
    { ...candidate("mistake", "待提出异议的识别项"), class_iri: "urn:Product" }], commits: [], snapshot_id: null,
    run: { completion: "incomplete", diagnostics: ["ambiguous_source_quote", "ambiguous_source_quote",
      "ambiguous_source_quote", "ambiguous_source_quote", "source_quote_outside_scope", "model_unavailable"] },
    extraction_version: { current: "generic-semantic-v8", stored: ["generic-semantic-v7"], outdated: true } },
  second: { graph_schema: { ...graphSchema, document_label: "来源 B.docx" }, candidates: [documentCandidate("second-root", "合成文档 B"),
    { ...candidate("two", "第二份文档的生产计划"), class_iri: "urn:Plan", class_label: "生产计划" },
    { ...candidate("second-edge", ""), kind: "relationship", subject: { candidate_id: "second-root", revision: 1 },
      object: { candidate_id: "two", revision: 1 }, predicate_iri: "urn:hasProductionPlan", predicate_label: "生产计划" }], commits: [], snapshot_id: null,
    run: { completion: "complete", diagnostics: [] } },
};
const unavailable = { availability: "unavailable", template_id: "fixture", template_version: "v2.2",
  manifest_id: null, required_gaps: null, error: { code: "CONTRACT_NOT_FOUND", actual: "unresolved:ontology",
    message: "模板引用的报告契约不存在，请在模板设置中补齐契约引用。" } };
const available = { availability: "available", template_id: "fixture", template_version: "v2.2",
  manifest_id: "frozen-inputs", snapshot_id: "published-one", required_gaps: 0, completion: "complete",
  material_status: "ready", tasks: [], diagnostics: [] };
jobs.job.calculations = [{ calculation_id: "c".repeat(64), subject_candidate_id: "plan", subject_label: "临床备样计划",
  status: "conflict", review_status: "pending", linked_to_source: true, blocks_conclusion: true, stale_decision: false,
  contract_ref: "urn:calculation:pde-check:1.0.0", method_version: "1.0.0", method_hash: "d".repeat(64),
  formula: "PDE = NOAEL × BW / (F1 × F2 × F3 × F4 × F5)", limitations: "合成案例，仅用于验证界面。",
  asserted_pde_mg_day: "1.8", derived_pde_mg_day: "0.05", effective_pde_mg_day: null,
  asserted_band: 2, derived_band: 4, ratio: "36", ratio_threshold: "2", inputs: { noael: "0.5" },
  factors: { f1: { value: "5", source: "species_table" } }, issues: [], differences: ["pde_ratio", "oeb_band"],
  input_evidence: { noael: [{ candidate_id: "dose", revision: 1, literal: { raw_value: "0.5 mg/kg/day" }, provenance: [{ anchors: [anchor] }] }] },
  decision_revision: 0, decision: null }];
let coverageMode = "unavailable", failReview = true, holdCoverage = false, holdReview = false, failCalculation = true;
let executionNumber = 0;
let releaseCoverage, releaseReview, coverageHeld, reviewHeld;
const errors = [], requests = [];
let page;
const browser = await chromium.launch({ executablePath: process.env.REPORTING_CHROME || "/usr/bin/google-chrome", args: ["--no-sandbox"] });
try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1100 } });
  await context.addInitScript(() => {
    localStorage.setItem("slpra.token", "synthetic-browser-token");
    window.__progressSources = [];
    window.EventSource = class {
      constructor(url) { this.url = url; window.__progressSources.push(this); }
      close() { this.closed = true; }
    };
  });
  await context.route("**/api/**", async (route) => {
    const request = route.request(), path = new URL(request.url()).pathname;
    requests.push({ path, method: request.method(), body: request.postDataJSON() });
    const send = (json, status = 200) => route.fulfill({ status, json });
    if (path === "/api/report-contracts") return send([]);
    if (path === "/api/report-model-context") return send({ contract_id: "ontology-1", kind: "ontology",
      family_id: "ontology", revision_no: 1, status: "published", definition: { classes: fixture.classes } });
    if (path === "/api/ast-templates/coverage-doc-classes") return send({ capable: ["urn:Report"] });
    if (path.endsWith("/training-pairs")) return send([]);
    if (path.startsWith("/api/ast-templates/")) return send({ id: "fixture", name: "合成模板", version: "v2.2", status: "draft",
      schema_json: fixture.template, sample_content_json: sampleContent, default_source_job_id: "job", default_source_filename: "来源 A.docx" });
    if (path === "/api/extraction/jobs") return send([
      { id: "job", source_filename: "来源 A.docx", status: "reviewing" },
      { id: "second", source_filename: "来源 B.docx", status: "reviewing" },
    ]);
    if (/^\/api\/extraction\/jobs\/[^/]+$/.test(path)) return send({
      id: path.split("/")[4], source_type: "word", source_mode: "template_default", status: "reviewing",
    });
    if (path === "/api/entities") return send({ items: [{ iri: "urn:doc:second", class_iri: "urn:Report",
      label_zh: "来源 B.docx", properties_json: { job_id: "second" } }] });
    if (path.endsWith("/annotated-document")) return send({ content: sampleContent, relationships: [], doc_class: null });
    if (path.endsWith("/reports")) return send([]);
    if (path.endsWith("/evidence/extract") || /\/annotation\/(rerun|resume)$/.test(path)) {
      return send({ job_id: "job", run_id: `run-${++executionNumber}`, status: "queued", has_checkpoint: true }, 202);
    }
    if (path.endsWith("/annotation/pause")) return send({ status: "pause_requested" });
    if (path.endsWith("/evidence")) return send(jobs[path.split("/")[4]]);
    if (path.endsWith("/calculations/decision")) {
      if (failCalculation) return send({ detail: "synthetic stale calculation" }, 409);
      const body = request.postDataJSON(), result = jobs.job.calculations[0];
      assert.equal(body.calculation_id, result.calculation_id);
      assert.equal(body.expected_revision, result.decision_revision);
      assert.ok(body.reason.trim());
      result.decision_revision += 1;
      result.review_status = body.choice;
      result.blocks_conclusion = !["asserted", "derived"].includes(body.choice);
      result.effective_pde_mg_day = body.choice === "derived" ? "0.05" : body.choice === "asserted" ? "1.8" : null;
      result.decision = { choice: body.choice, reason: body.reason, revision: result.decision_revision,
        actor: "analyst", decided_at: "2026-09-07T00:00:00Z" };
      return send(result.decision);
    }
    if (path.endsWith("/evidence/coverage")) {
      if (holdCoverage && path.includes("/job/")) {
        coverageHeld();
        await new Promise((resolve) => { releaseCoverage = resolve; });
        return send({ detail: "late coverage failure" }, 503);
      }
      if (coverageMode === "error") return send({ detail: "synthetic optional outage" }, 503);
      return send(coverageMode === "available" ? available : unavailable);
    }
    if (path.endsWith("/review")) {
      if (failReview) return send({ detail: "synthetic review rejected" }, 422);
      const current = path.includes("/two/") ? jobs.second : jobs.job;
      const item = current.candidates.find((item) => path.includes(`/${item.candidate_id}/`));
      if (holdReview) {
        reviewHeld();
        await new Promise((resolve) => { releaseReview = resolve; });
      }
      item.review_status = request.postDataJSON().decision;
      item.review_source = "manual";
      item.review_reason = request.postDataJSON().reason;
      return send(item);
    }
    if (path.endsWith("/evidence/commits")) {
      const commit = { commit_id: "commit-one", status: "succeeded", snapshot_id: "published-one", attempts: 1,
        error: null, items: request.postDataJSON().items };
      for (const ref of request.postDataJSON().items) jobs.job.candidates.find((item) => item.candidate_id === ref.candidate_id).commit_status = "succeeded";
      jobs.job.snapshot_id = commit.snapshot_id;
      jobs.job.commits = [commit];
      return send(commit);
    }
    errors.push("Unexpected API request: " + request.method() + " " + path);
    return send({ detail: "unexpected synthetic route" }, 500);
  });
  page = await context.newPage();
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto((process.env.REPORTING_BROWSER_ORIGIN || "http://127.0.0.1:3107") + "/settings/ast-templates/fixture", { waitUntil: "networkidle" });
  await page.getByRole("tab", { name: "源文档", exact: true }).click();
  await page.getByRole("button", { name: "来源 A.docx", exact: true }).click();
  const panel = page.getByRole("region", { name: "关系图谱识别结果", exact: true });
  const tree = panel.getByRole("region", { name: "关系图谱树", exact: true });
  const productionBranch = tree.locator('[aria-label="文档关系"] > ul > [data-predicate-iri="urn:hasProductionPlan"]');
  await expect(productionBranch.locator('[data-candidate-id="plan"] [data-candidate-id="batch"]')).toContainText("计划批量：12.00 kg");
  await expect(tree.locator('[data-document-class="urn:Report"]')).toHaveCount(1);
  await expect(tree.locator('[aria-label="文档关系"] > ul > li')).toHaveCount(2);
  await expect(tree.locator('[data-predicate-iri="urn:previousPlan"]')).toContainText("未识别到关系");
  await expect(tree.locator('[data-predicate-iri="urn:title"]')).toContainText("未识别到值");
  await expect(tree.locator('[data-candidate-id="one"] [data-candidate-id="plan"]')).toHaveCount(0);
  await expect(tree.locator('[data-unassociated]')).not.toHaveAttribute("open");
  await expect(tree.locator('[data-document-records]')).toHaveCount(0);
  await expect(tree.getByText(/文档识别记录/)).toHaveCount(0);
  await expect(tree.getByText("合成产品 A", { exact: true })).toHaveCount(0);
  await expect(tree.getByText("自动通过", { exact: true })).toHaveCount(4);
  await expect(panel.getByRole("button", { name: "确认", exact: true })).toHaveCount(0);
  await expect(panel.locator("pre")).toHaveCount(0);
  await expect(panel.getByText(/报告数据检查暂不可用/)).toBeVisible();
  await expect(panel.getByRole("button", { name: "发布通过项（5）", exact: true })).toBeEnabled();
  assert.equal(requests.filter((entry) => entry.path.endsWith("/review")).length, 0);
  await productionBranch.locator('[data-candidate-id="plan"] > details > div').first().getByRole("button", { name: /查看原文/ }).click();
  await expect(page.locator("[data-evidence-id='paragraph-1']")).toBeVisible();
  await page.screenshot({ path: "/tmp/ontology-evidence-tree.png", fullPage: true });
  console.log("PASS: source ontology predicates organize the tree, including empty branches and nested property values");

  const pdeCard = productionBranch.getByRole("region", { name: "PDE 计算校验", exact: true });
  await expect(pdeCard).toContainText("36 倍");
  await expect(pdeCard).toContainText("PDE 数值比值超过阈值");
  await expect(pdeCard).toContainText("报告结论保持待评估");
  await pdeCard.getByRole("button", { name: "采纳计算值", exact: true }).click();
  await expect(pdeCard.getByRole("button", { name: "保存处理结果", exact: true })).toBeDisabled();
  await pdeCard.getByLabel("PDE 处理理由", { exact: true }).fill("已复核公式及原文参数");
  await pdeCard.getByRole("button", { name: "保存处理结果", exact: true }).click();
  await expect(pdeCard.getByRole("alert")).toContainText("synthetic stale calculation");
  await expect(pdeCard.getByLabel("PDE 处理理由", { exact: true })).toHaveValue("已复核公式及原文参数");
  failCalculation = false;
  await pdeCard.getByRole("button", { name: "保存处理结果", exact: true }).click();
  await expect(pdeCard).toContainText("已采纳计算值");
  await expect(pdeCard).toContainText("已复核公式及原文参数");
  await panel.getByRole("button", { name: "刷新状态", exact: true }).click();
  await expect(pdeCard).toContainText("已采纳计算值");
  await pdeCard.getByRole("button", { name: "对校验结果提出异议", exact: true }).click();
  await pdeCard.getByLabel("PDE 处理理由", { exact: true }).fill("F3 的适用周期需要进一步确认");
  await pdeCard.getByRole("button", { name: "保存处理结果", exact: true }).click();
  await expect(pdeCard).toContainText("已提出异议，待解决");
  await expect(pdeCard).toContainText("报告结论保持待评估");
  await page.screenshot({ path: "/tmp/ontology-pde-check.png", fullPage: true });
  console.log("PASS: PDE differences appear under their entity; reason, CAS errors, decisions and objections survive refresh");

  const disputed = tree.locator('[data-candidate-id="mistake"]');
  await tree.locator('[data-unassociated] > summary').click();
  await tree.locator('[data-class-iri="urn:Product"] > summary').click();
  await disputed.locator("summary").click();
  await disputed.getByRole("button", { name: "提出异议", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "拒绝识别结果", exact: true });
  await expect(dialog.getByRole("button", { name: "确认拒绝", exact: true })).toBeDisabled();
  await dialog.getByLabel("拒绝理由", { exact: true }).fill("此处是供应商名称，不是药物产品");
  await dialog.getByRole("button", { name: "确认拒绝", exact: true }).click();
  await expect(dialog.getByRole("alert")).toContainText("synthetic review rejected");
  await expect(dialog.getByLabel("拒绝理由", { exact: true })).toHaveValue("此处是供应商名称，不是药物产品");
  failReview = false;
  await dialog.getByRole("button", { name: "确认拒绝", exact: true }).click();
  await expect(dialog).toHaveCount(0);
  await expect(disputed.getByText("已拒绝", { exact: true })).toBeVisible();
  await expect(disputed.getByText(/拒绝理由：此处是供应商名称/)).toBeVisible();
  const review = requests.filter((entry) => entry.path.endsWith("/review")).at(-1);
  assert.equal(review.body.expected_review_status, "confirmed");
  await panel.getByRole("button", { name: "刷新状态", exact: true }).click();
  await expect(disputed.getByText(/拒绝理由：此处是供应商名称/)).toBeVisible();
  console.log("PASS: rejection requires a reason, preserves it after errors/refresh, and is available on approved nodes");

  coverageMode = "error";
  await panel.getByRole("button", { name: "发布通过项（4）", exact: true }).click();
  await expect(panel.getByText("已发布通过项，可用于报告。", { exact: true })).toBeVisible();
  await expect(panel.getByText(/报告数据检查加载失败/)).toBeVisible();
  await expect(panel.getByRole("alert")).toHaveCount(0);
  const published = requests.find((entry) => entry.path.endsWith("/evidence/commits"));
  assert.equal(published.body.items.some((item) => item.candidate_id === "mistake"), false);
  console.log("PASS: publication excludes rejections and remains successful when the separate coverage check fails");

  coverageMode = "available";
  await panel.getByRole("button", { name: "刷新状态", exact: true }).click();
  await expect(panel.getByText("报告所需数据：0 项缺口 · 已满足", { exact: true })).toBeVisible();
  await expect(panel.getByText(/报告数据检查加载失败/)).toHaveCount(0);
  holdCoverage = true;
  const waitingCoverage = new Promise((resolve) => { coverageHeld = resolve; });
  await panel.getByRole("button", { name: "刷新状态", exact: true }).click();
  await waitingCoverage;
  await page.getByRole("button", { name: /来源 B.docx/ }).click();
  await expect(tree.locator('[data-candidate-id="two"]')).toHaveCount(1);
  await expect(tree.locator('[data-document-class] > p').first()).toHaveText("来源 B.docx");
  releaseCoverage(); holdCoverage = false;
  await expect(panel.getByText(/当前包含旧版本识别结果/)).toHaveCount(0);
  await expect(panel.getByText(/late coverage failure/)).toHaveCount(0);
  console.log("PASS: switching documents ignores late coverage responses");

  holdReview = true;
  const waitingReview = new Promise((resolve) => { reviewHeld = resolve; });
  await tree.locator('[data-candidate-id="two"] > details > div').getByRole("button", { name: "提出异议", exact: true }).click();
  await dialog.getByLabel("拒绝理由", { exact: true }).fill("第二份文档的异议");
  await dialog.getByRole("button", { name: "确认拒绝", exact: true }).click();
  await waitingReview;
  // Exercise a document switch during a pending mutation, even with the modal open.
  await page.getByRole("button", { name: "来源 A.docx", exact: true, includeHidden: true }).dispatchEvent("click");
  await expect(productionBranch.locator('[data-candidate-id="plan"]')).toBeVisible();
  await expect(tree.locator('[data-candidate-id="one"]')).toHaveCount(0);
  await expect(dialog).toHaveCount(0);
  const reviewResponse = page.waitForResponse((response) => response.url().endsWith("/two/review"));
  const secondReads = requests.filter((entry) => entry.path === "/api/extraction/jobs/second/evidence").length;
  releaseReview();
  await (await reviewResponse).finished();
  await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  await expect(panel.getByText("已拒绝此项并记录理由，相关依赖项已停止用于后续报告。", { exact: true })).toHaveCount(0);
  assert.equal(requests.filter((entry) => entry.path === "/api/extraction/jobs/second/evidence").length, secondReads);
  console.log("PASS: a rejection from the previous document cannot overwrite the current document");

  // Historical source runs can contain only entities and duplicate document roots.
  jobs.job.candidates = [documentCandidate("old-root", "同名文档"), documentCandidate("new-root", "同名文档"),
    { ...candidate("unbound-plan", "只有实体没有关系的计划"), class_iri: "urn:Plan", class_label: "生产计划" }];
  await panel.getByRole("button", { name: "刷新状态", exact: true }).click();
  await expect(tree.locator('[data-document-class]')).toHaveCount(1);
  await expect(tree.locator('[data-document-records]')).toHaveCount(0);
  await expect(tree.getByText("同名文档", { exact: true })).toHaveCount(0);
  await expect(productionBranch).toContainText("未识别到关系");
  await expect(productionBranch.locator('[data-candidate-id="unbound-plan"]')).toHaveCount(0);
  await expect(tree.locator('[data-unassociated]')).not.toHaveAttribute("open");
  await tree.locator('[data-unassociated] > summary').click();
  await tree.locator('[data-class-iri="urn:Plan"] > summary').click();
  await expect(tree.getByText("只有实体没有关系的计划", { exact: true })).toBeVisible();
  console.log("PASS: document records are hidden while the schema and unassociated entities remain accessible");

  const plan = (id, text) => ({ ...candidate(id, text), class_iri: "urn:Plan", class_label: "生产计划" });
  const relation = (id, subject, object, predicate = "urn:hasProductionPlan") => ({ ...candidate(id, ""), kind: "relationship",
    subject: { candidate_id: subject, revision: 1 }, object: { candidate_id: object, revision: 1 },
    predicate_iri: predicate, predicate_label: predicate === "urn:nextPlan" ? "后续计划" : "生产计划" });
  jobs.job.graph_schema = structuredClone(graphSchema);
  jobs.job.graph_schema.classes["urn:Plan"].relationships = [{ iri: "urn:nextPlan", label: "后续计划", range: ["urn:Plan", "urn:Report"] }];
  jobs.job.candidates = [documentCandidate("cycle-root", "循环测试文档"), plan("plan-a", "同名计划"), plan("plan-b", "同名计划"),
    relation("to-a", "cycle-root", "plan-a"), relation("to-b", "cycle-root", "plan-b"),
    { ...relation("shared", "cycle-root", "plan-a", "urn:previousPlan"), assertion_status: "conditional" },
    relation("back", "plan-a", "cycle-root", "urn:nextPlan")];
  for (let index = 0; index < 10; index++) {
    jobs.job.candidates.push(plan(`deep-${index}`, `深层计划 ${index}`),
      relation(`deep-edge-${index}`, index === 0 ? "plan-b" : `deep-${index - 1}`, `deep-${index}`, "urn:nextPlan"));
  }
  await panel.getByRole("button", { name: "刷新状态", exact: true }).click();
  await expect(productionBranch.locator(":scope > details > summary")).toContainText("2 条关系");
  await expect(tree.locator('[data-candidate-id="plan-a"]')).toHaveCount(1);
  await expect(tree.locator('[data-candidate-id="plan-b"]')).toHaveCount(1);
  await expect(tree.locator('[data-candidate-id="shared"]')).toContainText("有条件");
  await expect(tree.locator('[data-candidate-id="deep-9"]')).toHaveCount(1);
  await expect(tree.locator('[data-unassociated]')).toHaveCount(0);
  await tree.locator('[data-candidate-id="plan-a"] > details > summary').click();
  await tree.locator('[data-candidate-id="shared"]').getByRole("button", { name: /查看实体/ }).click();
  await expect(tree.locator('[data-candidate-id="plan-a"] > details')).toHaveAttribute("open");
  await tree.locator('[data-candidate-id="back"]').getByRole("button", { name: /返回文档属性与关系/ }).click();
  await expect(tree.locator('[data-document-class] > summary')).toBeFocused();
  await expect(tree.locator('[data-document-class]')).toHaveAttribute("open");
  await expect(tree.locator('[data-document-records]')).toHaveCount(0);
  await expect(tree.getByText("循环测试文档", { exact: true })).toHaveCount(0);
  console.log("PASS: shared entities, cycle references and deep paths remain accessible under the document");
  const evidenceReads = () => requests.filter((entry) => entry.path.endsWith("/job/evidence")).length;
  const coverageReads = () => requests.filter((entry) => entry.path.endsWith("/coverage")).length;
  const emitProgress = (event) => page.evaluate((event) => {
    const source = window.__progressSources.findLast((s) => !s.closed && s.url.includes("/job/progress"));
    if (!source?.onmessage) throw new Error("No active progress subscriber");
    source.onmessage({ data: JSON.stringify(event) });
  }, { job_id: "job", stage: "annotating", annotation_stage: "extracting", status: "running",
    pct: 0, degraded: false, started_at: Date.now() / 1000 - 20, ...event });
  const beforeEvidence = evidenceReads(), beforeCoverage = coverageReads();
  await emitProgress({ data_revision: 1, tasks_processed: 1 });
  await expect(page.getByRole("button", { name: "暂停并保存结果", exact: true })).toBeVisible();
  const modelRequest = { request_id: "request-1", logical_call_id: "call-1", attempt: 1,
    stage: "entity_type_verification", created_at: Date.now() / 1000 - 3,
    deadline_at: Date.now() / 1000 + 40 };
  await emitProgress({ tasks_processed: 1, model_calls: 2, http_attempts: 1,
    model_request: { ...modelRequest, status: "queued" } });
  await expect(page.getByRole("status").filter({ hasText: "等待模型空位" })).toContainText("复核实体类型");
  await emitProgress({ tasks_processed: 1, model_calls: 2, http_attempts: 2,
    model_request: { ...modelRequest, status: "running", queue_seconds: 2.5 } });
  await expect(page.getByRole("status").filter({ hasText: "等待模型响应" })).toContainText("排队 2.5 秒");
  await emitProgress({ tasks_processed: 1, model_calls: 2, http_attempts: 3,
    model_request: { ...modelRequest, attempt: 2, status: "retrying" } });
  await expect(page.getByRole("status").filter({ hasText: "准备重试" })).toContainText("第 2 次尝试");
  await expect(page.getByRole("status").filter({ hasText: "准备重试" })).toContainText("已记录请求 3 次");
  console.log("PASS: model queue, active request and retry events show separate attempts and waiting budget");
  // The tree remains usable while the clock advances and SSE publishes counts.
  await tree.locator('[data-candidate-id="plan-b"] > details > summary').click();
  await expect(tree.locator('[data-candidate-id="plan-b"] > details')).not.toHaveAttribute("open");
  jobs.job.candidates.push(plan("incremental-plan", "批次新增计划"),
    relation("incremental-edge", "cycle-root", "incremental-plan"));
  for (let revision = 2; revision <= 10; revision++) await emitProgress({ data_revision: revision, tasks_processed: revision });
  await expect(tree.getByText("批次新增计划", { exact: true })).toBeVisible();
  assert.ok(evidenceReads() - beforeEvidence <= 2, "SSE bursts must coalesce graph reads");
  assert.equal(coverageReads(), beforeCoverage, "incremental graph reads must not compile coverage");
  const settledReads = evidenceReads();
  await page.waitForTimeout(2200);
  assert.equal(evidenceReads(), settledReads, "clock ticks must not refetch graph data");
  await emitProgress({ annotation_stage: "complete", status: "done", data_revision: 11 });
  await expect(page.getByRole("button", { name: "暂停并保存结果", exact: true })).toHaveCount(0);
  await expect.poll(coverageReads).toBeGreaterThan(beforeCoverage);
  console.log("PASS: incremental SSE refresh is throttled, coverage waits for completion, and the graph stays interactive");
  await panel.getByText("识别进度与工具", { exact: true }).click();
  await panel.getByLabel("识别操作理由", { exact: true }).fill("已检查模型与失败原因");
  const continueButton = panel.getByRole("button", { name: "继续未处理任务（每批最多 8 项）", exact: true });
  const retryButton = panel.getByRole("button", { name: "重试失败任务", exact: true });
  const extractionRequests = () => requests.filter((entry) => entry.path.endsWith("/evidence/extract"));
  await continueButton.click();
  await expect(page.getByRole("button", { name: "暂停并保存结果", exact: true })).toBeVisible();
  await expect(continueButton).toBeDisabled();
  await expect(retryButton).toBeDisabled();
  assert.equal(extractionRequests().length, 1);
  assert.deepEqual(extractionRequests()[0].body, { pause_after: 8 });
  await emitProgress({ run_id: "run-1", tasks_processed: 18, model_calls: 24, data_revision: 12 });
  await expect(page.getByRole("status").filter({ hasText: "已处理 18 项" })).toBeVisible();
  await page.getByRole("button", { name: "暂停并保存结果", exact: true }).click();
  assert.equal(requests.filter((entry) => entry.path.endsWith("/annotation/pause")).length, 1);
  await emitProgress({ run_id: "run-1", annotation_stage: "paused", status: "paused",
    has_checkpoint: true, can_resume: true, data_revision: 13 });
  await expect(continueButton).toBeEnabled();
  await expect(retryButton).toBeEnabled();
  await retryButton.click();
  await expect(continueButton).toBeDisabled();
  await expect(retryButton).toBeDisabled();
  assert.deepEqual(extractionRequests()[1].body, {
    retry_failed: true, reason: "已检查模型与失败原因", pause_after: 8,
  });
  await emitProgress({ run_id: "run-2", annotation_stage: "complete", status: "done", data_revision: 14 });
  await expect(continueButton).toBeEnabled();
  assert.equal(executionNumber, 2);
  console.log("PASS: continue/retry accept 202, subscribe to one run, disable duplicate starts, and share pause/terminal refresh");
  assert.deepEqual(errors, []);
} catch (error) {
  console.error({ url: page?.url(), errors, text: (await page?.locator("body").innerText())?.slice(0, 1600) });
  throw error;
} finally {
  releaseCoverage?.();
  releaseReview?.();
  await browser.close();
}
