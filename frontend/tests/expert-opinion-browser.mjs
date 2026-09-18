// Real React form against isolated HTTP fixtures; never writes to business data.
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const { build } = await import(process.env.ESBUILD_MODULE || "esbuild");
const { chromium, expect } = await import(process.env.PLAYWRIGHT_MODULE || "playwright/test");
const entry = `
import { useState } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ExpertOpinionEntry } from "@/components/reports/expert-opinion-entry";
function App() {
  const [doc, setDoc] = useState("a");
  const [role, setRole] = useState("senior_analyst");
  const [hash, setHash] = useState("a".repeat(64));
  return <><button onClick={() => setDoc(doc === "a" ? "b" : "a")}>switch document</button>
    <button onClick={() => {
      const next = role === "operator" ? "senior_analyst" : "operator";
      localStorage.setItem("slpra.identity", JSON.stringify({username:next, role:next})); setRole(next);
    }}>switch caller</button>
    <button onClick={() => setHash("b".repeat(64))}>stale preview</button>
    <ExpertOpinionEntry target={{document_iri:"urn:" + doc}} runId="run-a" sourceHash={hash} /></>;
}
const client = new QueryClient({defaultOptions:{queries:{retry:false}}});
createRoot(document.getElementById("root")).render(<QueryClientProvider client={client}><App /></QueryClientProvider>);
`;
const bundle = await build({ stdin: { contents: entry, resolveDir: frontend, loader: "tsx" },
  bundle: true, write: false, platform: "browser", format: "iife", tsconfig: path.join(frontend, "tsconfig.json"),
  define: { "process.env.NODE_ENV": '"development"', "process.env.NEXT_PUBLIC_API_URL": '""' } });
const records = [], requests = [], errors = [];
let loseAcknowledgement = true, graph = "graph-1";
const context = (iri) => ({ target: { document_iri: iri }, filename: iri + ".docx",
  source_hash: "a".repeat(64), source_version: "1", recognition_run_id: "run-a",
  graph_artifact_id: graph, graph_content_hash: graph === "graph-1" ? "1".repeat(64) : "2".repeat(64) });
const json = (response, payload, status = 200) => {
  response.writeHead(status, { "Content-Type": "application/json" }); response.end(JSON.stringify(payload));
};
const server = createServer(async (request, response) => {
  const url = new URL(request.url, "http://localhost");
  if (url.pathname === "/") { response.end('<html><body><div id="root"></div><script src="/bundle.js"></script></body></html>'); return; }
  if (url.pathname === "/bundle.js") { response.end(bundle.outputFiles[0].text); return; }
  if (!url.pathname.startsWith("/api/")) { response.writeHead(404); response.end(); return; }
  let raw = "";
  for await (const chunk of request) raw += chunk;
  const body = raw ? JSON.parse(raw) : null;
  requests.push({ path: url.pathname, method: request.method, body });
  assert.ok(url.pathname.startsWith("/api/report-center/expert-opinions"));
  const user = request.headers["x-user"], role = request.headers["x-role"];
  if (request.method === "POST") {
    let saved = records.find((item) => item.request_key === body.request_key && item.author === user);
    if (!saved) {
      saved = { ...body, opinion_id: String(records.length + 1), author: user,
        author_role: role, revision: 1, created_at: new Date().toISOString() };
      records.unshift(saved);
    }
    graph = "graph-2";
    if (loseAcknowledgement) { loseAcknowledgement = false; json(response, { detail: "网络中断，请重试" }, 503); }
    else json(response, saved, 201);
    return;
  }
  const iri = url.searchParams.get("document_iri"), offset = Number(url.searchParams.get("offset") || 0);
  const items = records.filter((item) => item.author === user && item.context.target.document_iri === iri);
  if (url.pathname.endsWith("/export")) json(response, { opinions: items, calibration_approved: false });
  else json(response, { context: context(iri), can_submit: role !== "operator", items: items.slice(offset, offset + 1), total: items.length, offset, limit: 1 });
});
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
const browser = await chromium.launch({ executablePath: process.env.DOCUMENT_BROWSER_CHROME || "/usr/bin/google-chrome", args: ["--no-sandbox"] });
const page = await browser.newPage();
// Intranet HTTP origins have getRandomValues but do not expose randomUUID.
await page.addInitScript(() => Object.defineProperty(crypto, "randomUUID", { value: undefined }));
page.on("pageerror", (error) => errors.push(error.message));
const open = () => page.getByRole("button", { name: "专家意见", exact: true }).click();
try {
  await page.goto(`http://127.0.0.1:${server.address().port}`);
  assert.equal(requests.length, 0);
  await open();
  await expect(page.getByText("暂无已保存意见。")).toBeVisible();
  await page.getByLabel("意见类型").selectOption("pruning");
  await page.getByLabel("原文位置（选填）").fill("清洗方法表");
  await page.getByLabel("专家意见（必填）").fill("候选有原文依据，疑似误剪枝");
  await page.getByLabel("修改建议（选填）").fill("复查该关系及关联属性");
  await page.getByRole("button", { name: "保存意见", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("网络中断");
  await expect(page.getByLabel("专家意见（必填）")).toHaveValue("候选有原文依据，疑似误剪枝");
  await page.getByRole("button", { name: "保存意见", exact: true }).click();
  await expect(page.getByText("意见已保存。", { exact: true })).toBeVisible();
  const posts = requests.filter((item) => item.method === "POST");
  assert.equal(posts.length, 2);
  assert.equal(posts[0].body.request_key, posts[1].body.request_key);
  assert.equal(posts[1].body.context.graph_artifact_id, "graph-1");
  assert.equal(records.length, 1);
  await page.getByLabel("专家意见（必填）").fill("第二条补充意见");
  await page.getByRole("button", { name: "保存意见", exact: true }).click();
  await expect(page.getByRole("heading", { name: "我的已保存意见（2）" })).toBeVisible();
  await page.getByLabel("专家意见（必填）").fill("尚未保存的草稿");
  await page.getByRole("button", { name: "下一页" }).click();
  await expect(page.getByText("第 2 页", { exact: true })).toBeVisible();
  await expect(page.getByLabel("专家意见（必填）")).toHaveValue("尚未保存的草稿");
  const downloaded = page.waitForEvent("download");
  await page.getByRole("button", { name: "导出 JSON" }).click();
  const download = await downloaded;
  const exported = JSON.parse(await readFile(await download.path(), "utf8"));
  assert.equal(exported.opinions.length, 2);
  assert.equal(exported.calibration_approved, false);
  await page.keyboard.press("Escape");
  await open();
  await expect(page.getByRole("heading", { name: "我的已保存意见（2）" })).toBeVisible();
  await page.keyboard.press("Escape");
  await page.getByText("switch document", { exact: true }).click();
  await open();
  await expect(page.getByText("暂无已保存意见。")).toBeVisible();
  await expect(page.getByLabel("专家意见（必填）")).toHaveValue("");
  await page.keyboard.press("Escape");
  await page.getByText("switch caller", { exact: true }).click();
  await open();
  await expect(page.getByText("当前角色可查看自己的意见；提交需高级分析师或 QA 角色。")).toBeVisible();
  await expect(page.getByRole("button", { name: "保存意见", exact: true })).toHaveCount(0);
  await page.keyboard.press("Escape");
  await page.getByText("switch caller", { exact: true }).click();
  await page.getByText("stale preview", { exact: true }).click();
  await open();
  await expect(page.getByRole("alert")).toContainText("原文版本已变化");
  await page.getByLabel("专家意见（必填）").fill("针对旧预览的意见");
  await expect(page.getByRole("button", { name: "保存意见", exact: true })).toBeDisabled();
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ passed: true, saved: records.length, checks: ["lazy-open", "save-retry-idempotency", "frozen-graph", "pagination-retains-draft", "export", "reopen", "document-isolation", "readonly", "stale-preview"] }));
} finally {
  await browser.close();
  await new Promise((resolve) => server.close(resolve));
}
