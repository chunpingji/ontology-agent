// Static UI acceptance only: synthetic login, no backend or production data.
import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || "playwright");
const origin = process.env.GXP_BROWSER_ORIGIN || "http://127.0.0.1:3128";
const output = process.env.GXP_BROWSER_OUTPUT || "/tmp/gxp-process-browser";
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ executablePath: process.env.GXP_BROWSER_CHROME || "/usr/bin/google-chrome", args: ["--no-sandbox"] });
const errors = [], warnings = [], apiRequests = [];
let status = "failed";
try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  await context.addInitScript(() => {
    localStorage.setItem("slpra.token", "synthetic-gxp-mock-token");
    localStorage.setItem("slpra.identity", JSON.stringify({ username: "Mock", role: "senior_analyst" }));
  });
  await context.route("**/api/**", (route) => {
    apiRequests.push(route.request().url());
    return route.abort();
  });
  const page = await context.newPage();
  page.setDefaultTimeout(15000);
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
    if (message.type() === "warning") warnings.push(message.text());
  });
  await page.goto(`${origin}/mock/gxp-process`, { waitUntil: "networkidle", timeout: 120000 });
  await page.getByRole("heading", { name: "通用工艺操作设置" }).waitFor();
  const nav = page.getByRole("link", { name: "GxP工艺规程配置", exact: true });
  assert.equal(await nav.getAttribute("aria-current"), "page");
  assert.deepEqual((await page.locator("aside").first().getByRole("link").allTextContents()).slice(-3), ["抽取配置", "报告模板", "GxP工艺规程配置"]);
  assert.equal(await page.locator('a[aria-current="page"]').count(), 1);
  await page.getByText("25 个步骤", { exact: true }).waitFor();
  const tree = page.getByRole("navigation", { name: "操作模板库" });
  const parameters = page.getByRole("region", { name: "过程参数与记录配置", exact: true });
  assert.equal(await parameters.locator("tbody tr").count(), 6);
  await page.screenshot({ path: `${output}/desktop.png`, fullPage: true });

  await page.getByRole("button", { name: "校验配置", exact: true }).click();
  await page.getByText("本地字段校验通过", { exact: true }).waitFor();
  await page.getByRole("button", { name: "返回配置", exact: true }).click();
  await page.getByLabel("物料温度下限", { exact: true }).fill("30");
  await page.getByRole("button", { name: "校验配置", exact: true }).click();
  await page.getByText("物料温度：请填写有效区间，下限不能大于上限。", { exact: true }).waitFor();
  await page.getByRole("button", { name: "返回配置", exact: true }).click();
  await page.getByLabel("物料温度下限", { exact: true }).fill("21");
  await page.getByLabel("保温时长要求值", { exact: true }).fill("14");
  await page.getByText("引用：保温时长 不少于 14 小时", { exact: true }).waitFor();

  await page.getByLabel("搜索步骤或操作", { exact: true }).fill("氮气");
  await tree.getByRole("button", { name: "氮气置换", exact: true }).click();
  assert.equal(await parameters.locator("tbody tr").count(), 1);
  await parameters.getByText("尚未配置参数，添加参数后设置本操作的规程要求。", { exact: true }).waitFor();
  await page.getByLabel("搜索步骤或操作", { exact: true }).fill("保温反应");
  await tree.getByRole("button", { name: "保温反应", exact: true }).click();
  assert.equal(await page.getByLabel("物料温度下限", { exact: true }).inputValue(), "21");
  await page.getByLabel("搜索步骤或操作", { exact: true }).fill("没有此操作");
  await page.getByText("未找到匹配的步骤或操作", { exact: true }).waitFor();
  await page.getByLabel("搜索步骤或操作", { exact: true }).fill("");

  await page.getByRole("button", { name: "添加参数 / 测量对象", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("参数名称", { exact: true }).fill("压力");
  await dialog.getByLabel("测量对象", { exact: true }).fill("反应釜");
  await dialog.getByLabel("单位", { exact: true }).fill("MPa");
  await dialog.getByLabel("压力下限", { exact: true }).fill("0.1");
  await dialog.getByLabel("压力上限", { exact: true }).fill("0.2");
  await dialog.getByRole("button", { name: "保存参数", exact: true }).click();
  await dialog.waitFor({ state: "hidden" });
  await page.getByLabel("压力下限", { exact: true }).waitFor();
  assert.equal(await parameters.locator("tbody tr").count(), 7);

  await page.getByRole("button", { name: "复制操作", exact: true }).click();
  assert.equal(await page.getByLabel("操作名称", { exact: true }).inputValue(), "保温反应（副本）");
  await page.getByLabel("物料温度下限", { exact: true }).fill("22");
  await tree.getByRole("button", { name: "保温反应", exact: true }).click();
  assert.equal(await page.getByLabel("物料温度下限", { exact: true }).inputValue(), "21");
  await page.getByRole("button", { name: "停用操作", exact: true }).click();
  assert.equal(await page.getByLabel("操作名称", { exact: true }).isDisabled(), true);
  await page.getByRole("button", { name: "启用操作", exact: true }).click();

  await page.getByRole("button", { name: "权限设置", exact: true }).click();
  await page.getByRole("combobox", { name: "演示权限", exact: true }).click();
  await page.getByRole("option", { name: "已授权成员 · 只读", exact: true }).click();
  await page.getByRole("button", { name: "完成", exact: true }).click();
  assert.equal(await page.getByLabel("操作名称", { exact: true }).isDisabled(), true);
  assert.equal(await page.getByRole("button", { name: "保存配置草稿", exact: true }).isDisabled(), true);
  assert.equal(await page.getByRole("button", { name: "新增步骤", exact: true }).isDisabled(), true);
  await page.getByRole("button", { name: "权限设置", exact: true }).click();
  await page.getByRole("combobox", { name: "演示权限", exact: true }).click();
  await page.getByRole("option", { name: "工艺管理员 · 可编辑", exact: true }).click();
  await page.getByRole("button", { name: "完成", exact: true }).click();

  await page.getByRole("button", { name: "预览记录结构", exact: true }).click();
  await page.getByText("保温反应 · 空白记录结构", { exact: true }).waitFor();
  assert.equal(await page.getByRole("dialog").getByText("未判定", { exact: true }).count(), 4);
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "保存配置草稿", exact: true }).click();
  await page.getByText("请先填写变更说明，再保存或模拟提交。", { exact: true }).waitFor();
  await page.getByLabel("变更说明", { exact: true }).fill("静态页面验证：调整温度并添加压力记录。");
  await page.getByLabel("规程来源", { exact: true }).fill("Mock 规程示例 §11.2");
  await page.getByRole("button", { name: "保存配置草稿", exact: true }).click();
  await page.getByText("Mock 草稿 v1.6 已保存至本次会话，刷新页面将恢复示例。", { exact: true }).waitFor();
  await page.getByRole("button", { name: "版本历史", exact: true }).click();
  await page.getByText("v1.6 · 草稿", { exact: true }).waitFor();
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "提交发布审核", exact: true }).click();
  await page.getByRole("button", { name: "确认模拟提交", exact: true }).click();
  await page.getByRole("dialog").waitFor({ state: "hidden" });
  await page.getByText("已模拟提交 v1.7，未发起真实发布审核。刷新页面将恢复示例。", { exact: true }).waitFor();
  await page.screenshot({ path: `${output}/saved.png`, fullPage: true });

  await page.getByRole("button", { name: "新增步骤", exact: true }).click();
  await page.getByLabel("步骤名称", { exact: true }).fill("自定义验证步骤");
  await page.getByRole("button", { name: "确认新增", exact: true }).click();
  await page.getByText("26 个步骤", { exact: true }).waitFor();
  assert.equal(await page.getByLabel("操作名称", { exact: true }).inputValue(), "自定义操作");
  await page.getByRole("button", { name: "新增操作", exact: true }).click();
  await page.getByRole("dialog").getByLabel("操作名称", { exact: true }).fill("自定义记录");
  await page.getByRole("button", { name: "确认新增", exact: true }).click();
  assert.equal(await page.getByLabel("操作名称", { exact: true }).inputValue(), "自定义记录");

  await page.reload({ waitUntil: "networkidle" });
  await page.getByText("25 个步骤", { exact: true }).waitFor();
  assert.equal(await page.getByLabel("物料温度下限", { exact: true }).inputValue(), "20.0");
  for (const width of [1440, 1024, 768]) {
    await page.setViewportSize({ width, height: 1000 });
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), `Document overflow at ${width}`);
    assert.ok(await page.locator("main").evaluate((element) => element.scrollWidth <= element.clientWidth), `Main overflow at ${width}`);
    await page.screenshot({ path: `${output}/width-${width}.png`, fullPage: true });
  }
  assert.deepEqual(errors, []);
  assert.deepEqual(warnings, []);
  assert.deepEqual(apiRequests, []);
  const signedOut = await browser.newPage();
  await signedOut.goto(`${origin}/mock/gxp-process`);
  await signedOut.waitForURL(/\/login\?next=/);
  await signedOut.close();
  status = "passed";
} finally {
  await writeFile(`${output}/result.json`, JSON.stringify({ status, errors, warnings, apiRequests }, null, 2));
  await browser.close();
}
console.log(JSON.stringify({ status, output, errors, warnings, apiRequests }));
