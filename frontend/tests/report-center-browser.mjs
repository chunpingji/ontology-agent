// Real /reports page with intercepted API fixtures; no business data is mutated.
import assert from "node:assert/strict";
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || "playwright");
const base = process.env.REPORT_CENTER_BASE_URL || "http://127.0.0.1:3000";
const args = ["--no-sandbox", "--no-proxy-server"];
if (process.env.REPORT_CENTER_HOST_RULES) args.push(`--host-resolver-rules=${process.env.REPORT_CENTER_HOST_RULES}`);
const browser = await chromium.launch({ executablePath: process.env.DOCUMENT_BROWSER_CHROME || "/usr/bin/google-chrome", args });
const phase = "https://ontology.pharma-gmp.cn/slpra/document/Phase_ClinicalI";
const report = (n) => ({
  id: `aaaaaaaa-0000-4000-8000-${String(n).padStart(12, "0")}`,
  job_id: "bbbbbbbb-0000-4000-8000-000000000001", source_filename: `来源${n}.docx`,
  report_type: "batch_record_demo", file_size: 1024, created_at: "2026-09-14T10:00:00Z",
});
const reportTitle = (n) => `批记录报告（演示）（来源${n}.docx）`;
const checks = [];
const deferred = () => {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
};
async function scenario(options = {}) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  await context.addInitScript(() => {
    localStorage.setItem("slpra.token", "synthetic-fixture-token");
    localStorage.setItem("slpra.identity", JSON.stringify({ username: "analyst", role: "senior_analyst" }));
  });
  const state = {
    reports: Array.from({ length: 26 }, (_, n) => report(n + 1)),
    documents: [{ iri: "urn:doc:one", label_zh: "先到文档", class_iri: "urn:document:RegulatoryDocument", properties_json: { hasDevelopmentPhase: phase } }],
    firstError: false, nextError: false, deleteError: false, documentError: false,
    requests: [], finished: [], aborted: [], unexpected: [], errors: [], ...options,
  };
  const page = await context.newPage();
  page.on("requestfinished", (request) => {
    if (new URL(request.url()).pathname.startsWith("/api/")) state.finished.push(request.url());
  });
  page.on("requestfailed", (request) => {
    if (new URL(request.url()).pathname.startsWith("/api/")) state.aborted.push(request.failure()?.errorText);
  });
  page.on("pageerror", (error) => state.errors.push(error.message));
  page.on("dialog", (dialog) => dialog.accept());
  await page.route("**/api/**", async (route) => {
    const request = route.request(), url = new URL(request.url()), method = request.method();
    state.requests.push(`${method} ${url.pathname}${url.search}`);
    const json = (body, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
    if (url.pathname === "/api/entities" && method === "GET") {
      if (state.documentsGate) await state.documentsGate.promise;
      if (state.documentError) return json({ detail: "fixture unavailable" }, 503);
      return json({ items: state.documents, total: state.documents.length });
    }
    if (url.pathname === "/api/reports" && method === "GET") {
      const n = Number(url.searchParams.get("page"));
      assert.equal(url.searchParams.get("page_size"), "25");
      if (n === 1 && state.reportsGate) await state.reportsGate.promise;
      if ((n === 1 && state.firstError) || (n > 1 && state.nextError)) return json({ detail: "fixture unavailable" }, 503);
      return json({ items: state.reports.slice((n - 1) * 25, n * 25), total: state.reports.length, page: n, page_size: 25 });
    }
    if (method === "DELETE" && url.pathname.includes("/reports/")) {
      if (state.deleteError) return json({ detail: "fixture deletion failed" }, 503);
      state.reports = state.reports.filter((item) => item.id !== url.pathname.split("/").at(-1));
      return route.fulfill({ status: 204 });
    }
    if (method === "DELETE" && url.pathname.startsWith("/api/entities/")) {
      const iri = decodeURIComponent(url.pathname.slice("/api/entities/".length));
      state.documents = state.documents.filter((item) => item.iri !== iri);
      return route.fulfill({ status: 204 });
    }
    if (method === "POST" && url.pathname === "/api/integration/connectors") {
      state.upload = request.postDataJSON();
      return json({ id: "fixture-connector" });
    }
    if (method === "POST" && url.pathname === "/api/integration/connectors/fixture-connector/sync") {
      await state.uploadGate.promise;
      const envelope = state.upload.connection_config.upload_payload[0];
      state.documents.push({ iri: `http://slpra.org/facts#${envelope.doc_id}`, label_zh: envelope.title,
        class_iri: state.upload.field_mapping.doc_type_to_class[envelope.doc_type], properties_json: envelope.metadata });
      return json({ run_id: "fixture-sync", status: "completed" });
    }
    state.unexpected.push(`${method} ${url.pathname}`);
    return json({ detail: "unexpected fixture request" }, 404);
  });
  await page.goto(`${base}/reports`, { waitUntil: "domcontentloaded" });
  return { page, state, close: async () => {
    assert.deepEqual(state.unexpected, []);
    assert.deepEqual(state.errors, []);
    await context.close();
  } };
}
async function visible(page, text) { await page.getByText(text, { exact: true }).waitFor(); }
async function remove(page, title) {
  const row = page.locator("li").filter({ has: page.getByRole("link", { name: title, exact: true }) });
  await row.getByRole("button", { name: "更多操作" }).click();
  await page.getByRole("menuitem", { name: "删除", exact: true }).click();
}
try {
  const reportsGate = deferred();
  const s = await scenario({ reportsGate });
  await visible(s.page, "先到文档");
  assert.equal(await s.page.getByRole("link", { name: reportTitle(1), exact: true }).count(), 0);
  reportsGate.resolve();
  await visible(s.page, "已加载 25 / 26 份报告");
  assert.equal(s.state.finished.length, 2, JSON.stringify({requests:s.state.requests, aborted:s.state.aborted}));
  assert.equal(new Set(s.state.requests).size, 2);
  const initialRequests = s.state.requests.length;
  const initialFirstPageRequests = s.state.requests.filter((url) => url === "GET /api/reports?page=1&page_size=25").length;
  checks.push("documents render before the delayed report page; first load completes two requests (development Strict Mode may abort initial requests)");
  const editorResources = await s.page.evaluate(() => performance.getEntriesByType("resource").filter((entry) => /tiptap|prosemirror|reading-pane|word-viewer/i.test(entry.name)).map((entry) => entry.name));
  assert.deepEqual(editorResources, []);
  checks.push("initial list downloads no named editor chunks");
  s.state.nextError = true;
  await s.page.getByRole("button", { name: "加载更多", exact: true }).click();
  await s.page.getByRole("button", { name: "重试加载更多", exact: true }).waitFor();
  await visible(s.page, reportTitle(1));
  await visible(s.page, "先到文档");
  assert.equal(s.state.requests.length, initialRequests + 1);
  s.state.nextError = false;
  await s.page.getByRole("button", { name: "重试加载更多", exact: true }).click();
  await visible(s.page, "已加载 26 / 26 份报告");
  await visible(s.page, reportTitle(26));
  assert.equal(s.state.requests.length, initialRequests + 2);
  assert.equal(s.state.requests.filter((url) => url === "GET /api/reports?page=1&page_size=25").length, initialFirstPageRequests);
  checks.push("next-page failure retains content; retry appends only page 2");
  const beforeDeleteDocs = s.state.requests.filter((url) => url.startsWith("GET /api/entities?")).length;
  await remove(s.page, reportTitle(1));
  await s.page.getByRole("link", { name: reportTitle(1), exact: true }).waitFor({ state: "detached" });
  await visible(s.page, "已加载 25 / 25 份报告");
  await visible(s.page, reportTitle(26));
  assert.equal(s.state.requests.filter((url) => url.startsWith("GET /api/entities?")).length, beforeDeleteDocs);
  checks.push("report deletion refills offset pages and total without refetching documents");
  s.state.deleteError = true;
  await remove(s.page, reportTitle(2));
  await visible(s.page, "删除失败");
  await visible(s.page, reportTitle(2));
  checks.push("failed deletion restores the server list");
  const beforeUploadReports = s.state.requests.filter((url) => url.startsWith("GET /api/reports?")).length;
  s.state.uploadGate = deferred();
  await s.page.getByRole("button", { name: /^临床Ⅰ期/ }).click();
  await s.page.locator('input[type="file"]').setInputFiles({ name: "上传检查.txt", mimeType: "text/plain", buffer: Buffer.from("fixture") });
  await s.page.getByRole("button", { name: "确认上传", exact: true }).click();
  await visible(s.page, "上传检查.txt");
  await s.page.getByText("处理中", { exact: true }).waitFor();
  s.state.uploadGate.resolve();
  await s.page.getByRole("link", { name: "上传检查.txt", exact: true }).waitFor();
  await s.page.waitForFunction(() => !document.body.textContent.includes("处理中"));
  assert.equal(await s.page.getByRole("link", { name: "上传检查.txt", exact: true }).count(), 1);
  assert.equal(s.state.requests.filter((url) => url.startsWith("GET /api/reports?")).length, beforeUploadReports);
  await remove(s.page, "上传检查.txt");
  await s.page.getByRole("link", { name: "上传检查.txt", exact: true }).waitFor({ state: "detached" });
  checks.push("upload placeholder reconciles once, refreshes documents only, and can be deleted");
  await s.close();

  const failed = await scenario({ firstError: true });
  await visible(failed.page, "报告加载失败");
  await visible(failed.page, "先到文档");
  failed.state.firstError = false;
  await failed.page.getByRole("button", { name: "重试", exact: true }).click();
  await visible(failed.page, "已加载 25 / 26 份报告");
  checks.push("first report-page error preserves documents and supports retry");
  await failed.close();

  const documentsGate = deferred();
  const slowDocs = await scenario({ documentsGate });
  await visible(slowDocs.page, reportTitle(1));
  documentsGate.resolve();
  await visible(slowDocs.page, "先到文档");
  checks.push("reports render without waiting for slow documents");
  slowDocs.state.reports = [report(99)];
  slowDocs.state.reportsGate = deferred();
  await slowDocs.page.evaluate(() => {
    localStorage.setItem("slpra.identity", JSON.stringify({ username: "other", role: "qa" }));
    window.dispatchEvent(new Event("slpra:identity-change"));
  });
  await slowDocs.page.getByRole("link", { name: reportTitle(1), exact: true }).waitFor({ state: "detached" });
  slowDocs.state.reportsGate.resolve();
  await visible(slowDocs.page, reportTitle(99));
  await visible(slowDocs.page, "已加载 1 / 1 份报告");
  assert.equal(await slowDocs.page.getByRole("button", { name: "选择文件", exact: true }).count(), 0);
  checks.push("identity switch does not reuse another user's report pages and respects the role gate");
  await slowDocs.close();

  console.log(JSON.stringify({ status: "passed", scope: "real page, synthetic intercepted APIs", checks }, null, 2));
} finally {
  await browser.close();
}
