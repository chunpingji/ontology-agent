// Run against a local frontend with FRONTEND_URL; every API request is intercepted.
// PLAYWRIGHT_MODULE and CHROME_EXECUTABLE may point to an external browser installation.
import assert from "node:assert/strict";
const { chromium, expect } = await import(process.env.PLAYWRIGHT_MODULE || "playwright/test");

const base = "https://ontology.pharma-gmp.cn/slpra/";
const owner = base + "drug-development/CMCReport", parent = base + "core/Report";
const product = base + "drug/DrugProduct", subtype = base + "drug/SterileDrugProduct";
const equipment = base + "equipment/Equipment", mass = base + "drug/mass";
const describes = base + "drug-development/describes", uses = base + "drug-development/usesEquipment";
const unconfigured = base + "drug-development/hasTarget";
const restrictions = [
  { id: "existing-only", kind: "only", property_iri: describes, property_kind: "object", filler_iri: subtype, cardinality: null, version: 3, status: "draft" },
  { id: "existing-card", kind: "max", property_iri: uses, property_kind: "object", filler_iri: null, cardinality: 0, version: 2, status: "draft" },
  { id: "existing-data", kind: "min", property_iri: mass, property_kind: "data", filler_iri: null, cardinality: 1, version: 1, status: "draft" },
  { id: "existing-axiom", kind: "disjoint", property_iri: null, property_kind: null, filler_iri: equipment, cardinality: null, version: 1, status: "draft" },
];
const links = [[describes, "描述", product], [uses, "使用设备", equipment], [unconfigured, "目标", null]]
  .map(([slpra_iri, label, range_iri], i) => ({
    id: `link-${i}`, slpra_iri, label, domain_iri: i === 1 ? parent : owner, range_iri,
    inverse_iri: null, min_cardinality: null, max_cardinality: null, multiplicity: "unspecified",
    version: 1, status: "draft", is_disabled: false, comment: null,
    is_functional: false, is_symmetric: false, is_transitive: false,
    inherited_from_iri: i === 1 ? parent : null, inherited_from_label: i === 1 ? "报告" : null,
  }));
const dataProperties = [{ id: "mass", slpra_iri: mass, label: "质量", domain_iri: owner,
  datatype: "decimal", controlled_vocab: null, unit: "mg", version: 1, status: "draft",
  is_disabled: false, min_cardinality: null, max_cardinality: null, multiplicity: "unspecified" }];
const classes = [[owner, "CMC 报告"], [product, "药物产品"], [equipment, "设备"]]
  .map(([slpra_iri, label], i) => ({
    id: `class-${i}`, slpra_iri, label, comment: "", module: "drug", parent_iri: null,
    bfo_category: null, field_schema: null, status: "draft", version: 1,
    is_reviewed: false, is_disabled: false, confidence: null,
    restrictions: i === 0 ? restrictions : [], mappings: [], created_at: null, updated_at: null,
  }));
const writes = [], errors = [], unexpected = [], passed = [];
let failNext = false;
const browser = await chromium.launch({
  executablePath: process.env.CHROME_EXECUTABLE || undefined, args: ["--no-sandbox"],
});
try {
  const page = await browser.newPage({ viewport: { width: 1700, height: 1100 } });
  page.setDefaultTimeout(15000);
  page.on("pageerror", e => errors.push(e.message));
  await page.addInitScript(() => {
    localStorage.setItem("slpra.token", "isolated-ui-fixture");
    localStorage.setItem("slpra.identity", JSON.stringify({ username: "fixture", role: "senior_analyst" }));
  });
  await page.route("**/api/**", async route => {
    const req = route.request(), url = new URL(req.url()), path = decodeURIComponent(url.pathname);
    const reply = (data, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(data) });
    if (req.method() !== "GET") {
      const body = req.postDataJSON();
      writes.push({ method: req.method(), path, body });
      if (req.method() === "POST") {
        assert.equal(path, "/api/ontology/classes/" + owner + "/restrictions");
        const row = { ...body, id: `new-${writes.length}`, version: 1, status: "draft" };
        restrictions.push(row);
        return reply(row, 201);
      }
      assert.equal(req.method(), "PUT", "Editing must update the original restriction");
      const index = restrictions.findIndex(r => path === "/api/ontology/restrictions/" + r.id);
      assert.ok(index >= 0);
      const { expected_version, ...changes } = body;
      if (failNext) { failNext = false; return reply({ detail: "模拟保存失败" }, 500); }
      if (expected_version !== restrictions[index].version) {
        return reply({ detail: { message: "版本冲突", current_version: restrictions[index].version } }, 409);
      }
      restrictions[index] = { ...restrictions[index], ...changes, version: expected_version + 1 };
      return reply(restrictions[index]);
    }
    if (path === "/api/ontology/modules") return reply([{ key: "drug", iri: base + "drug/", label: "药物", class_count: 3, individual_count: 0 }]);
    if (path === "/api/ontology/drug/classes") return reply(classes.map(c => ({ iri: c.slpra_iri, name: c.slpra_iri.split("/").pop(), label: c.label, children: [], individual_count: 0 })));
    if (path === "/api/ontology/link-types") return reply(url.searchParams.has("domain_iri") && url.searchParams.get("domain_iri") !== owner ? [] : links);
    if (path === "/api/ontology/data-properties") return reply(url.searchParams.get("domain_iri") === owner ? dataProperties : []);
    const cls = classes.find(c => path === "/api/ontology/classes/" + c.slpra_iri);
    if (cls) return reply(cls);
    if (path === "/api/ontology/releases") return reply([]);
    unexpected.push(path);
    return reply([]);
  });
  await page.goto((process.env.FRONTEND_URL || "http://127.0.0.1:3182") + "/ontology");
  await page.getByText("CMC 报告", { exact: true }).first().click();
  const axioms = page.getByRole("heading", { name: "类公理", exact: true }).locator("..");
  await expect(axioms.locator("li")).toHaveCount(1);
  await expect(axioms).toContainText("disjoint");
  await page.getByRole("tab", { name: "关系", exact: true }).click();
  const section = page.getByRole("heading", { name: "类约束", exact: true, includeHidden: true }).locator("..");
  const form = section.locator("fieldset");
  const row = iri => page.locator(`li[data-property-iri="${iri}"]`);
  const expand = iri => row(iri).locator(":scope > div > button[aria-expanded]").click();
  const edit = (index = 0) => section.locator("li").nth(index).getByRole("button", { name: "编辑", exact: true }).click();
  const save = () => form.getByRole("button", { name: "保存修改", exact: true }).click();
  const add = () => form.getByRole("button", { name: "添加约束", exact: true }).click();
  const cancel = () => form.getByRole("button", { name: "取消编辑", exact: true }).click();
  const saved = () => expect(form.getByRole("button", { name: "添加约束", exact: true })).toBeEnabled();
  const choose = async kind => {
    await form.getByRole("combobox", { name: "约束类型" }).click();
    await expect(page.getByRole("option", { name: "disjoint", exact: true })).toHaveCount(0);
    await page.getByRole("option", { name: kind, exact: true }).click();
  };

  await expect(section).toHaveCount(0);
  await expand(describes);
  await expect(row(describes).getByRole("heading", { name: "类约束", exact: true })).toBeVisible();
  await expect(section.locator("li")).toHaveCount(1);
  await expect(section).toContainText("SterileDrugProduct");
  await expect(form.getByLabel("约束关系 IRI")).toHaveCount(0);
  passed.push("constraints nested under the matching relation only");

  await edit();
  await expect(form.getByLabel("约束目标类 IRI")).toHaveValue(subtype);
  await form.getByLabel("约束目标类 IRI").fill(equipment);
  await cancel();
  assert.equal(writes.length, 0);
  await edit();
  await expect(form.getByLabel("约束目标类 IRI")).toHaveValue(subtype);
  await choose("some");
  await save();
  await saved();
  assert.deepEqual(writes.at(-1), { method: "PUT", path: "/api/ontology/restrictions/existing-only",
    body: { kind: "some", property_iri: describes, property_kind: "object", filler_iri: subtype, cardinality: null, expected_version: 3 } });
  assert.equal(restrictions.length, 4);
  passed.push("cancel without write; edit preserves ID and narrower target");

  await choose("only");
  await add();
  await saved();
  assert.deepEqual(writes.at(-1).body, { kind: "only", property_iri: describes, property_kind: "object", filler_iri: product, cardinality: null });
  await expect(section.locator("li")).toHaveCount(2);
  passed.push("new only constraint uses the enclosing relation and saved range");

  await edit();
  await choose("max");
  await form.getByLabel("约束基数").fill("2");
  failNext = true;
  await save();
  await expect(section).toContainText("模拟保存失败");
  await expect(form.getByLabel("约束基数")).toHaveValue("2");
  await save();
  await saved();
  assert.deepEqual(writes.at(-1).body, { kind: "max", property_iri: describes, property_kind: "object", filler_iri: null, cardinality: 2, expected_version: 4 });
  passed.push("failed save retains draft; cardinality update clears filler");

  await edit();
  const beforeSwitch = writes.length;
  await expand(uses);
  await expect(row(describes).getByRole("heading", { name: "类约束", exact: true })).toHaveCount(0);
  await expect(section.locator("li")).toHaveCount(1);
  await expect(form.getByRole("button", { name: "保存修改", exact: true })).toHaveCount(0);
  assert.equal(writes.length, beforeSwitch);
  await edit();
  await expect(form.getByLabel("约束基数")).toHaveValue("0");
  await cancel();
  await choose("min");
  await form.getByLabel("约束基数").fill("1");
  await add();
  await expect(section.locator("li")).toHaveCount(2);
  assert.equal(writes.at(-1).path, "/api/ontology/classes/" + owner + "/restrictions");
  assert.equal(writes.at(-1).body.property_iri, uses);
  passed.push("relation switch resets draft; inherited relation writes to current class; zero prefill");

  await expand(unconfigured);
  await choose("only");
  await expect(form.getByRole("button", { name: "添加约束", exact: true })).toBeDisabled();
  await form.getByRole("button", { name: "自定义目标类", exact: true }).click();
  await form.getByLabel("约束目标类 IRI").fill(product);
  await add();
  await expect(section).toContainText("→ DrugProduct");
  assert.equal(writes.at(-1).body.property_iri, unconfigured);
  passed.push("missing range requires a custom target while keeping the relation fixed");

  await expand(describes);
  await edit();
  restrictions[0] = { ...restrictions[0], cardinality: 7, version: 6 };
  await form.getByLabel("约束基数").fill("3");
  await save();
  await expect(page.getByRole("dialog")).toContainText("他人已更新该实体");
  await expect(form.getByLabel("约束基数")).toHaveValue("3");
  assert.equal(restrictions[0].cardinality, 7);
  await page.getByRole("button", { name: "重新加载最新", exact: true }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(form.getByRole("button", { name: "保存修改", exact: true })).toHaveCount(0);
  await edit();
  await expect(form.getByLabel("约束基数")).toHaveValue("7");
  await form.getByLabel("约束基数").fill("4");
  await save();
  await saved();
  assert.equal(writes.at(-1).body.expected_version, 6);
  passed.push("conflict prevents overwrite and reload fetches the latest version");
  if (process.env.SCREENSHOT_PATH) await page.screenshot({ path: process.env.SCREENSHOT_PATH, fullPage: true });
  await expand(describes);
  await expect(section).toHaveCount(0);
  passed.push("relation collapses its constraint editor");

  await page.getByRole("tab", { name: "属性", exact: true }).click();
  await expand(mass);
  await expect(row(mass).getByRole("heading", { name: "类约束", exact: true })).toBeVisible();
  await expect(section.locator("li")).toHaveCount(1);
  await edit();
  await form.getByLabel("约束基数").fill("2");
  await save();
  await expect(form.getByRole("button", { name: "保存修改", exact: true })).toHaveCount(0);
  assert.equal(writes.at(-1).body.property_kind, "data");
  assert.equal(writes.at(-1).body.property_iri, mass);
  passed.push("data restrictions remain editable under their data property");

  await page.getByRole("tab", { name: "基本", exact: true }).click();
  await expect(axioms.locator("li")).toHaveCount(1);
  await axioms.locator("li").getByRole("button", { name: "编辑", exact: true }).click();
  await expect(axioms.getByLabel("约束目标类 IRI")).toHaveValue(equipment);
  await axioms.getByRole("combobox", { name: "约束类型" }).click();
  await expect(page.getByRole("option", { name: "only", exact: true })).toHaveCount(0);
  await page.getByRole("option", { name: "equivalent", exact: true }).click();
  await axioms.getByRole("button", { name: "保存修改", exact: true }).click();
  await expect(axioms.getByRole("button", { name: "保存修改", exact: true })).toHaveCount(0);
  assert.deepEqual(writes.at(-1).body, { kind: "equivalent", property_iri: null, property_kind: null, filler_iri: equipment, cardinality: null, expected_version: 1 });
  passed.push("class axioms stay separate and carry no property binding");

  await page.getByRole("tab", { name: "关系", exact: true }).click();
  await expand(describes);
  await edit();
  const beforeClassSwitch = writes.length;
  await page.getByText("药物产品", { exact: true }).first().click();
  await expect(section).toHaveCount(0);
  assert.equal(writes.length, beforeClassSwitch);
  passed.push("class switch unmounts the old editor without writing");
  assert.deepEqual(errors, []);
  assert.deepEqual(unexpected, []);
  console.log(JSON.stringify({ passed, interceptedWrites: writes.length }));
} finally {
  await browser.close();
}
