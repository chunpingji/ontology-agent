// Real integration browser test: standalone fixture API, PostgreSQL and current ontology.
// No route interception. Only use backend/tests/fixtures/mapped_entity_query_app.py.
import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";

const { chromium, expect: baseExpect } = await import(process.env.PLAYWRIGHT_MODULE || "playwright/test");
const expect = baseExpect.configure({ timeout: 20000 });
const origin = process.env.MAPPED_ENTITY_BROWSER_ORIGIN;
if (!origin || !["127.0.0.1", "localhost"].includes(new URL(origin).hostname)) {
  throw new Error("Set MAPPED_ENTITY_BROWSER_ORIGIN to the isolated loopback fixture frontend");
}
const output = process.env.MAPPED_ENTITY_BROWSER_OUTPUT || "/tmp/mapped-entity-query-browser";
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ executablePath: process.env.BROWSER_CHROME || "/usr/bin/google-chrome", args: ["--no-sandbox"] });
const page = await browser.newPage({ viewport: { width: 1500, height: 1000 } });
const errors = [], requests = [];
page.on("pageerror", e => errors.push(String(e)));
page.on("request", r => { if (r.url().includes("/api/")) requests.push({ method: r.method(), path: new URL(r.url()).pathname }); });
try {
  const login = await page.request.post(origin + "/api/auth/login", { data: { username: "query-test", password: "query-test-only" } });
  assert.equal(login.status(), 200);
  const identity = await login.json();
  const headers = { Authorization: `Bearer ${identity.token}` };
  await page.addInitScript(i => {
    localStorage.setItem("slpra.token", i.token);
    localStorage.setItem("slpra.identity", JSON.stringify({ username: i.username, role: i.role }));
  }, identity);
  const api = async (path, data, method = "POST") => {
    const response = await page.request.fetch(origin + path, { method, data, headers });
    assert.ok(response.ok(), `${path}: ${response.status()} ${await response.text()}`);
    return response.json();
  };
  const areaClass = "https://ontology.pharma-gmp.cn/slpra/facility/ProductionArea";
  const areaProperty = "https://ontology.pharma-gmp.cn/slpra/facility/areaIdentifier";
  await page.goto(origin + "/ontology");
  await page.getByRole("button", { name: /^facility/ }).click();
  await page.getByLabel("搜索本体类").fill("ProductionArea");
  await page.getByRole("tree").getByText("ProductionArea", { exact: true }).click();
  await page.getByRole("tab", { name: "映射", exact: true }).click();
  await page.getByRole("button", { name: /(?:应用初始|打开已有) Mock 映射/ }).click();
  await expect(page.getByText("Mock 查询配置 · 可查询", { exact: true })).toBeVisible();
  // A saved query config is active immediately; repeated initial application never duplicates it.
  await page.getByLabel("来源编号命名空间", { exact: true }).fill("urn:mock:production_areas");
  await page.getByRole("button", { name: "保存查询配置" }).click();
  await expect(page.getByText("Mock 查询配置 · 可查询", { exact: true })).toBeVisible();
  const catalog = await api("/api/entities/sources", undefined, "GET");
  const areas = catalog.sources.find(s => s.dataset === "production_areas");
  assert.equal(areas.mappings.filter(m => m.class_iri === areaClass).length, 1);
  assert.equal(areas.mappings[0].identity_properties[0].property_iri, areaProperty);
  await page.screenshot({ path: output + "/mapping.png", fullPage: true });

  // The equipment template uses the same generic path and the real Equipment ontology.
  await page.getByRole("button", { name: /^equipment/ }).click();
  await page.getByLabel("搜索本体类").fill("Equipment");
  await page.getByRole("tree").getByText("Equipment", { exact: true }).click();
  await page.getByRole("tab", { name: "映射", exact: true }).click();
  await page.getByRole("button", { name: /(?:应用初始|打开已有) Mock 映射/ }).click();
  await expect(page.getByText("Mock 查询配置 · 可查询", { exact: true })).toBeVisible();
  const equipment = await api("/api/entities/query", { queries: [{ query_id: "equipment",
    class_iri: "https://ontology.pharma-gmp.cn/slpra/equipment/Equipment", property_filters: [{
      property_iri: "https://ontology.pharma-gmp.cn/slpra/equipment/equipmentID", value: "EQ-001",
      datatype_iri: "http://www.w3.org/2001/XMLSchema#string",
    }],
  }] });
  assert.equal(equipment.results[0].outcome, "matches");
  assert.equal(equipment.results[0].candidates.length, 1);
  assert.equal(equipment.results[0].complete, true);

  await page.goto(origin + "/entities");
  await expect(page.getByTestId("mapped-source-cards")).toBeVisible();
  await expect(page.getByTestId("mapped-source-cards").getByRole("button")).toHaveCount(3);
  await expect(page.getByRole("dialog")).toHaveCount(0);
  assert.equal(requests.filter(r => r.path === "/api/entities").length, 0);
  await page.getByTestId("saved-entity-source").click();
  await expect(page.getByRole("dialog", { name: "已保存实体", exact: true })).toBeVisible();
  await expect(page.getByTestId("saved-entity-results")).toBeVisible();
  await page.getByLabel("搜索已保存实体").fill("absent-saved-entity");
  await expect(page.getByTestId("saved-entity-results")).toContainText("没有符合条件的已保存实体");
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("saved-entity-source")).toBeFocused();
  await page.getByTestId("mapped-source-production_areas").click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(page.getByTestId("mapped-entity-list").getByRole("button")).toHaveCount(3);
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.getByTestId("mapped-source-production_areas")).toBeFocused();
  await page.getByTestId("mapped-source-equipment").click();
  await expect(page.getByTestId("mapped-query-result")).toContainText("共 61 条实体");
  const firstPage = await page.getByTestId("mapped-entity-list").innerText();
  await page.getByRole("button", { name: "下一页", exact: true }).click();
  await expect(page.getByTestId("mapped-query-result")).toContainText("第 2 页");
  await expect(page.getByTestId("mapped-entity-list").getByRole("button")).toHaveCount(11);
  const secondPageIds = await page.getByTestId("mapped-entity-list").getByRole("button").allTextContents();
  assert.ok(secondPageIds.every(text => !firstPage.includes(text)));
  await expect(page.getByRole("button", { name: "下一页", exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "关闭", exact: true }).click();
  await page.getByTestId("mapped-source-production_areas").click();
  await expect(page.getByTestId("mapped-query-result")).toContainText("共 3 条实体");
  await page.getByText("属性精确筛选", { exact: true }).click();
  await page.getByRole("button", { name: "添加属性条件", exact: true }).click();
  await page.getByRole("combobox", { name: "属性 1", exact: true }).click();
  await page.getByRole("option", { name: "生产区域编号", exact: true }).click();
  await page.getByLabel("属性值 1", { exact: true }).fill("642");
  await page.getByRole("button", { name: "筛选", exact: true }).click();
  await expect(page.getByTestId("mapped-query-result")).toContainText("共 1 条实体");
  await page.getByRole("button", { name: /642车间/ }).click();
  const detail = page.getByTestId("mapped-entity-detail");
  await expect(detail).toContainText("来源候选 · 身份未核对");
  await expect(detail).toContainText("col:code");
  const batch = await api("/api/entities/query", { queries: ["611/642/646", "611", "642", "646"].map(value => ({
    query_id: value, class_iri: areaClass, property_filters: [{ property_iri: areaProperty, value,
      datatype_iri: "http://www.w3.org/2001/XMLSchema#string" }],
  })) });
  assert.deepEqual(batch.results.map(r => r.outcome), ["no_match", "no_match", "matches", "matches"]);
  assert.ok(batch.results.every(r => r.complete));
  const before = batch.results[2].candidates[0];
  const records = await api("/api/mock-sources/production-areas", undefined, "GET");
  const record = records.find(r => r.code === "642");
  await api(`/api/mock-sources/production-areas/${record.id}`, { ...record, label: "642车间（更新验收）" }, "PUT");
  await page.getByRole("button", { name: "筛选", exact: true }).click();
  await page.getByRole("button", { name: /642车间（更新验收）/ }).click();
  await expect(page.getByTestId("mapped-entity-detail")).toContainText(before.record_ref.record_id);
  await page.screenshot({ path: output + "/query-detail.png", fullPage: true });

  await page.getByLabel("属性值 1", { exact: true }).fill("611/642/646");
  await page.getByRole("button", { name: "筛选", exact: true }).click();
  await expect(page.getByTestId("mapped-query-result")).toContainText("当前来源没有符合条件的实体");
  // A legal property without a source binding must be shown as unresolved.
  await page.getByRole("combobox", { name: "属性 1", exact: true }).click();
  await page.getByRole("option", { name: "控制措施", exact: true }).click();
  await page.getByLabel("属性值 1", { exact: true }).fill("absent");
  await page.getByRole("button", { name: "筛选", exact: true }).click();
  await expect(page.getByTestId("mapped-query-result")).toContainText("查询未完成，不能判断是否存在实体");
  await expect(page.getByTestId("mapped-query-result")).toContainText("unsupported_filter");
  await page.screenshot({ path: output + "/incomplete.png", fullPage: true });
  await api(`/api/mock-sources/production-areas/${record.id}`, record, "PUT");
  const queryCalls = requests.filter(r => r.path === "/api/entities/query").length;
  await page.reload();
  await expect(page.getByTestId("mapped-source-cards")).toBeVisible();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  assert.equal(requests.filter(r => r.path === "/api/entities/query").length, queryCalls);
  assert.ok(!requests.some(r => /\/document-analysis|\/extraction\/jobs|\/model-requests/.test(r.path)));
  assert.deepEqual(errors, []);
  await writeFile(output + "/result.json", JSON.stringify({ passed: true, queries: batch.results.map(r => ({
    query_id: r.query_id, outcome: r.outcome, complete: r.complete, candidates: r.candidates.length,
  })), equipment_candidates: equipment.results[0].candidates.length, browser_errors: errors, browser_query_calls: queryCalls }, null, 2));
  console.log("PASS: real mapping, query, current data, independent detail, incomplete state and refresh");
} catch (e) {
  await page.screenshot({ path: output + "/failure.png", fullPage: true });
  throw e;
} finally { await browser.close(); }
