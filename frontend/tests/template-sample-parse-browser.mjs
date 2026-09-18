// Synthetic API UI regression: no application backend or model calls.
import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";

const { chromium, expect: baseExpect } = await import(process.env.PLAYWRIGHT_MODULE || "playwright/test");
const expect = baseExpect.configure({ timeout: 15_000 });
const origin = process.env.TEMPLATE_SAMPLE_ORIGIN || "http://127.0.0.1:53219";
const output = process.env.TEMPLATE_SAMPLE_OUTPUT || "/tmp/template-sample-parse-browser";
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ executablePath: "/usr/bin/google-chrome", args: ["--no-sandbox"] });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
const errors = [];
page.on("pageerror", (error) => errors.push(error.message));
await page.addInitScript(() => {
  localStorage.setItem("slpra.token", "synthetic-sample-token");
  localStorage.setItem("slpra.identity", JSON.stringify({ username: "analyst", role: "senior_analyst" }));
  const original = window.setTimeout.bind(window);
  window.setTimeout = (callback, delay, ...args) => original(callback,
    delay === 180_000 && window.__testFastTimeout ? 100 : delay, ...args);
});
const waiting = [];
let requests = 0;
let mode = "pending";
const response = (text) => ({
  content_json: { type: "doc", content: [{ type: "paragraph", content: [{ type: "text", text }] }] },
  plain_text: text,
});
await page.route("**/api/**", async (route) => {
  const url = new URL(route.request().url());
  if (url.pathname === "/api/ast-templates/parse-sample") {
    requests += 1;
    if (mode === "success") return route.fulfill({ json: response("最新文档") });
    waiting.push(route);
    return;
  }
  return route.fulfill({ json: [] });
});
const open = async () => page.getByRole("button", { name: "从样例文档创建", exact: true }).first().click();
const dialog = page.getByRole("dialog");
const input = dialog.locator('input[type="file"]');
const select = async (name) => input.setInputFiles({ name, mimeType: "application/vnd.openxmlformats-officedocument.wordprocessingml.document", buffer: Buffer.from("synthetic sample") });
const fulfillOld = async (route) => { await route.fulfill({ json: response("旧文档不应覆盖当前选择") }).catch(() => {}); };

try {
  await page.goto(`${origin}/settings/ast-templates`, { waitUntil: "domcontentloaded", timeout: 120_000 });
  await open();
  await select("first.docx");
  await expect(dialog.getByRole("status")).toContainText("正在解析文档");
  await expect.poll(() => requests).toBe(1);
  mode = "success";
  await select("second.docx");
  await expect(dialog).toContainText("文档已解析（4 字符）");
  await expect(dialog).toContainText("已选择：second.docx");
  await fulfillOld(waiting.shift());
  await expect(dialog.locator("input").first()).toHaveValue("second");
  await expect(dialog.getByRole("status")).toHaveCount(0);

  mode = "pending";
  await select("cancel.docx");
  await expect.poll(() => requests).toBe(3);
  await dialog.getByRole("button", { name: "取消等待" }).click();
  await expect(dialog.getByRole("status")).toHaveCount(0);
  await expect(dialog.getByRole("button", { name: "进入模板定义" })).toBeDisabled();
  await fulfillOld(waiting.shift());
  await expect(dialog).not.toContainText("文档已解析");
  mode = "success";
  await select("cancel.docx");
  await expect(dialog).toContainText("文档已解析（4 字符）");

  mode = "pending";
  await select("close.docx");
  await expect.poll(() => requests).toBe(5);
  await dialog.getByRole("button", { name: "取消", exact: true }).click();
  await open();
  await fulfillOld(waiting.shift());
  await expect(dialog.getByRole("status")).toHaveCount(0);
  await expect(dialog).not.toContainText("文档已解析");
  await expect(dialog.locator("input").first()).toHaveValue("");

  await page.evaluate(() => { window.__testFastTimeout = true; });
  await select("timeout.docx");
  await expect(dialog).toContainText("样例文档解析超时");
  await expect(dialog.getByRole("status")).toHaveCount(0);
  await fulfillOld(waiting.shift());
  mode = "success";
  await select("timeout.docx");
  await expect(dialog).toContainText("文档已解析（4 字符）");
  await expect(dialog).not.toContainText("解析超时");
  assert.deepEqual(errors, []);
  await page.screenshot({ path: path.join(output, "retry-success.png") });
  await page.goto(`${origin}/settings/ast-templates/create`, { waitUntil: "networkidle" });
  await page.waitForURL("**/settings/ast-templates");
  await expect(page.getByRole("button", { name: "从样例文档创建", exact: true }).first()).toBeVisible();
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ status: "passed", browser: browser.version(), requests, scope: "synthetic API; 180-second timeout accelerated only in browser test" }));
} catch (error) {
  await page.screenshot({ path: path.join(output, "failure.png") });
  await writeFile(path.join(output, "failure.json"), JSON.stringify({ errors, requests, body: await page.locator("body").innerText() }, null, 2));
  throw error;
} finally {
  for (const route of waiting) await route.abort().catch(() => {});
  await browser.close();
}
