import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";
import ts from "typescript";

const source = readFileSync(new URL("../src/lib/api.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
function loadAPI(fetch) {
  const api = {};
  vm.runInContext(compiled, vm.createContext({
    exports: api, process: { env: {} }, Headers, URLSearchParams, fetch,
  }));
  return api;
}
const summary = (n) => ({
  id: `aaaaaaaa-0000-4000-8000-${String(n).padStart(12, "0")}`,
  job_id: "bbbbbbbb-0000-4000-8000-000000000001",
  source_filename: "批记录.docx", report_type: "batch_record_demo",
  file_size: 1024, created_at: "2026-09-14T10:00:00Z",
});
const response = (data) => new Response(JSON.stringify(data));
const summaries = Array.from({ length: 26 }, (_, n) => summary(n));
const reportPage = (page) => ({
  items: summaries.slice((page - 1) * 25, page * 25),
  page, page_size: 25, total: 26,
});

test("report pages each use one summary request, preserving detail coordinates", async () => {
  const requests = [];
  const api = loadAPI(async (url) => {
    requests.push(url);
    return response(reportPage(Number(new URL(url, "http://test").searchParams.get("page"))));
  });
  const first = await api.listReportCenterReports();
  const second = await api.listReportCenterReports(2);
  assert.deepEqual(requests, ["/api/reports?page=1&page_size=25", "/api/reports?page=2&page_size=25"]);
  assert.equal(first.items.length, 25);
  assert.equal(second.items.length, 1);
  assert.equal(second.total, 26);
  assert.deepEqual(JSON.parse(JSON.stringify(first.items[0])), {
    key: summary(0).id, kind: "generated-report", title: "批记录报告（演示）（批记录.docx）",
    category: "batch_record_demo", type: "批记录报告（演示）", date: summary(0).created_at,
    size: 1024, jobId: summary(0).job_id, reportId: summary(0).id,
  });
});

test("documents load independently without querying report jobs", async () => {
  const requests = [];
  const api = loadAPI(async (url) => {
    requests.push(url);
    return response({ items: [{ iri: "urn:doc:one", label_zh: "原始记录", class_iri: "urn:unknown", properties_json: {} }] });
  });
  const items = await api.listReportCenterDocuments();
  assert.equal(requests.length, 1);
  const url = new URL(requests[0], "http://test");
  assert.equal(url.pathname, "/api/entities");
  assert.equal(url.searchParams.get("module"), "document");
  assert.equal(url.searchParams.get("page_size"), "100");
  assert.equal(items[0].title, "原始记录");
  assert.equal(items[0].category, "未分阶段");
});

for (const [label, request] of [
  ["reports", (api, signal) => api.listReportCenterReports(2, signal)],
  ["documents", (api, signal) => api.listReportCenterDocuments(signal)],
]) {
  test(`${label} forwards cancellation`, async () => {
    const controller = new AbortController();
    const api = loadAPI((_url, options) => {
      assert.equal(options.signal, controller.signal);
      return new Promise((_resolve, reject) => {
        options.signal.addEventListener("abort", () => reject(options.signal.reason));
      });
    });
    const pending = request(api, controller.signal);
    controller.abort(new Error("left report center"));
    await assert.rejects(pending, /left report center/);
  });
  test(`${label} failures are not treated as an empty successful list`, async () => {
    const api = loadAPI(async () => new Response("unavailable", { status: 503 }));
    await assert.rejects(request(api), /API 503: unavailable/);
  });
}

test("query-less report detail links resolve later summary pages and stop at total", async () => {
  const requests = [];
  const api = loadAPI(async (url) => {
    requests.push(url);
    return response(reportPage(Number(new URL(url, "http://test").searchParams.get("page"))));
  });
  assert.equal((await api.resolveReportCenterItem(summary(25).id)).reportId, summary(25).id);
  assert.equal(requests.length, 2);
  requests.length = 0;
  assert.equal(await api.resolveReportCenterItem(summary(99).id), null);
  assert.equal(requests.length, 2);
});
