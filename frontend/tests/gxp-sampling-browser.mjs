import assert from "node:assert/strict";

// Runs in the existing static Mock browser harness; no backend or real sample records.
export async function verifySampling(page, tree, output) {
  const tab = async (number) => {
    const trigger = page.getByRole("tab", { name: new RegExp(`^${number}`) });
    await trigger.click();
    await page.getByRole("tabpanel", { name: new RegExp(`^${number}`) }).waitFor();
    assert.equal(await trigger.getAttribute("aria-selected"), "true");
    assert.equal(await page.getByRole("tabpanel").count(), 1);
  };
  const fieldTabs = [
    ["02", ["取样前准备", "防污染与保护措施", "取样后设备恢复"]],
    ["03", ["即时处理方法 / 版本", "取样至处理上限（分钟）", "样品容器与密封", "保存条件", "运输与交接要求", "取样至检验上限（小时）", "样品稳定性依据"]],
    ["04", ["预批准调整范围与程序", "偏差 / OOS 适用程序", "重取样 / 复测程序"]],
  ];
  const showField = async (label) => {
    const number = label.startsWith("取样点 ") ? "02" : label.startsWith("检验项目 ") ? "04" : fieldTabs.find(([, labels]) => labels.includes(label))?.[0] ?? "01";
    if (await page.getByRole("tab", { name: new RegExp(`^${number}`) }).getAttribute("aria-selected") !== "true") await tab(number);
  };
  const select = async (label, option) => {
    await page.getByRole("combobox", { name: label, exact: true }).click();
    await page.getByRole("option", { name: option, exact: true }).click();
  };
  await tree.getByRole("button", { name: "过程取样", exact: true }).click();
  await page.getByRole("region", { name: "取样方案与触发规则", exact: true }).waitFor();
  assert.equal(await page.getByRole("tablist", { name: "过程取样配置分区" }).getByRole("tab").count(), 7);
  assert.equal(await page.getByRole("tab", { name: /^01/ }).getAttribute("aria-selected"), "true");
  assert.equal(await page.getByRole("region", { name: "取样点与操作方法", exact: true }).count(), 0);
  await page.getByRole("tab", { name: /^01/ }).focus();
  await page.keyboard.press("End");
  await page.getByRole("tabpanel", { name: /^07/ }).waitFor();
  await page.keyboard.press("Home");
  await page.getByRole("tabpanel", { name: /^01/ }).waitFor();
  await page.keyboard.press("ArrowRight");
  await page.getByRole("tabpanel", { name: /^02/ }).waitFor();
  assert.equal(await page.getByLabel("取样点 1单份取样量", { exact: true }).inputValue(), "");
  await tab("04");
  assert.equal(await page.getByLabel("检验项目 1要求值", { exact: true }).inputValue(), "");
  await tab("01");
  assert.equal(await page.getByLabel("取样间隔（小时）", { exact: true }).count(), 0);
  await page.getByRole("button", { name: "校验配置", exact: true }).click();
  await page.getByText("过程取样：请填写质量批准依据。", { exact: true }).waitFor();
  await page.getByRole("button", { name: "返回配置", exact: true }).click();
  await page.getByLabel("变更说明", { exact: true }).fill("静态取样方案测试草稿");
  await page.getByRole("button", { name: "保存配置草稿", exact: true }).click();
  await page.getByRole("status").filter({ hasText: "仍有" }).waitFor();
  await page.getByRole("button", { name: "提交发布审核", exact: true }).click();
  await page.getByRole("button", { name: "确认模拟提交", exact: true }).click();
  await page.getByText("过程取样：请填写质量批准依据。", { exact: true }).waitFor();
  await page.getByRole("button", { name: "返回配置", exact: true }).click();

  // Synthetic inputs solely exercise the UI; none are ICH-prescribed values.
  const fields = {
    "方案版本": "TEST-1", "取样 SOP / 版本": "TEST-SOP v1", "质量批准依据": "测试批准引用",
    "适用品种 / 工艺范围": "测试品种 / 测试路线", "代表性与风险依据": "测试开发及风险评估依据",
    "首次取样时点": "测试事件后按方案取样", "允许时间窗（±分钟）": "0",
    "取样点 1相别 / 物料对象": "测试反应液", "取样点 1位置 / 取样口": "TEST-R01 / S01",
    "取样点 1单份取样量": "2", "取样点 1份数": "1", "取样前准备": "测试准备规则",
    "防污染与保护措施": "测试保护规则", "取样后设备恢复": "测试恢复规则",
    "即时处理方法 / 版本": "TEST-QUENCH v1", "取样至处理上限（分钟）": "5",
    "样品容器与密封": "测试相容容器", "保存条件": "按测试稳定性条件", "运输与交接要求": "测试交接规则",
    "取样至检验上限（小时）": "2", "样品稳定性依据": "TEST-STABILITY",
    "检验项目 1方法编号": "TEST-METHOD", "检验项目 1方法版本": "TEST-1",
    "检验项目 1单位 / 结果基准": "面积 %", "检验项目 1要求值": "0.5",
    "预批准调整范围与程序": "测试调整授权", "偏差 / OOS 适用程序": "TEST-OOS v1",
    "重取样 / 复测程序": "TEST-RETEST v1：关联原始记录", "规程来源": "TEST-PROCESS v1",
  };
  for (const [label, value] of Object.entries(fields)) {
    await showField(label);
    await page.getByLabel(label, { exact: true }).fill(value);
  }
  await tab("01");
  await select("取样触发方式", "固定间隔");
  await page.getByLabel("取样间隔（小时）", { exact: true }).fill("3");
  await tab("02");
  await page.getByRole("button", { name: "添加取样点", exact: true }).click();
  await page.getByLabel("取样点 2名称", { exact: true }).fill("临时测试点");
  await page.getByRole("button", { name: "删除取样点 2", exact: true }).click();
  await tab("04");
  await page.getByRole("button", { name: "添加检验项目", exact: true }).click();
  await page.getByLabel("检验项目 2名称", { exact: true }).fill("测试定性项目");
  await select("检验项目 2结果类型", "定性");
  await page.getByLabel("检验项目 2接受描述", { exact: true }).fill("符合测试描述");
  await page.getByRole("button", { name: "删除检验项目 2", exact: true }).click();
  await tab("01");
  await select("取样用途", "终点判定");
  await page.getByRole("button", { name: "校验配置", exact: true }).click();
  await page.getByText("过程取样：终点判定须配置至少一个必需的终点检验项目。", { exact: true }).waitFor();
  await page.getByRole("button", { name: "返回配置", exact: true }).click();
  await tab("04");
  await select("检验项目 1用途", "终点判定");
  await page.getByRole("button", { name: "校验配置", exact: true }).click();
  await page.getByText("本地字段校验通过", { exact: true }).waitFor();
  await page.getByRole("button", { name: "返回配置", exact: true }).click();
  await tab("05");
  await select("时间精度", "分钟");
  await tab("06");
  await page.getByRole("button", { name: "添加检查项目", exact: true }).waitFor();
  await tab("07");
  await select("质量审核角色", "质量负责人");
  await tab("05");
  assert.equal(await page.getByRole("combobox", { name: "时间精度", exact: true }).innerText(), "分钟");
  await tab("07");
  assert.equal(await page.getByRole("combobox", { name: "质量审核角色", exact: true }).innerText(), "质量负责人");

  await page.getByRole("button", { name: "复制操作", exact: true }).click();
  await page.getByLabel("方案名称", { exact: true }).fill("副本测试方案");
  await tab("02");
  await page.getByLabel("取样点 1单份取样量", { exact: true }).fill("9");
  await tree.getByRole("button", { name: "过程取样", exact: true }).click();
  assert.equal(await page.getByLabel("方案名称", { exact: true }).inputValue(), "反应过程取样方案");
  await tab("02");
  assert.equal(await page.getByLabel("取样点 1单份取样量", { exact: true }).inputValue(), "2");
  await tree.getByRole("button", { name: "保温反应", exact: true }).click();
  assert.equal(await page.getByRole("combobox", { name: "物料温度记录方式", exact: true }).innerText(), "每 3 小时记录");
  await tree.getByRole("button", { name: "过程取样", exact: true }).click();
  assert.equal(await page.getByLabel("取样间隔（小时）", { exact: true }).inputValue(), "3");
  await page.getByRole("button", { name: "停用操作", exact: true }).click();
  assert.equal(await page.getByLabel("方案名称", { exact: true }).isDisabled(), true);
  await tab("02");
  assert.equal(await page.getByLabel("取样点 1单份取样量", { exact: true }).isDisabled(), true);
  await page.getByRole("button", { name: "启用操作", exact: true }).click();
  await page.getByRole("button", { name: "权限设置", exact: true }).click();
  await select("演示权限", "已授权成员 · 只读");
  await page.getByRole("button", { name: "完成", exact: true }).click();
  for (const label of ["方案名称", "取样点 1单份取样量", "检验项目 1方法编号"]) {
    await showField(label);
    assert.equal(await page.getByLabel(label, { exact: true }).isDisabled(), true);
  }
  await tab("01");
  assert.equal(await page.getByRole("combobox", { name: "取样用途", exact: true }).isDisabled(), true);
  await tab("02");
  assert.equal(await page.getByRole("button", { name: "添加取样点", exact: true }).isDisabled(), true);
  await tab("05");
  assert.equal(await page.getByRole("combobox", { name: "时间精度", exact: true }).isDisabled(), true);
  await tab("06");
  assert.equal(await page.getByRole("button", { name: "添加检查项目", exact: true }).isDisabled(), true);
  await tab("07");
  assert.equal(await page.getByRole("combobox", { name: "质量审核角色", exact: true }).isDisabled(), true);
  await page.getByRole("button", { name: "预览记录结构", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByRole("heading", { name: "独立状态记录", exact: true }).waitFor();
  await dialog.getByText("实际结果：待填写 · 判定：未决", { exact: true }).waitFor();
  assert.ok((await dialog.innerText()).includes("每 3 小时"));
  assert.ok((await dialog.innerText()).includes("不超过 0.5 面积 %"));
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "权限设置", exact: true }).click();
  await select("演示权限", "工艺管理员 · 可编辑");
  await page.getByRole("button", { name: "完成", exact: true }).click();
  await page.getByRole("button", { name: "提交发布审核", exact: true }).click();
  await page.getByRole("button", { name: "确认模拟提交", exact: true }).click();
  await page.getByText("已模拟提交 v1.7，未发起真实发布审核。刷新页面将恢复示例。", { exact: true }).waitFor();

  for (const width of [1440, 1024, 768]) {
    await page.setViewportSize({ width, height: 1000 });
    for (const number of ["01", "02", "03", "04", "05", "06", "07"]) {
      await tab(number);
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), `Sampling document overflow at ${width}, tab ${number}`);
      assert.ok(await page.locator("main").evaluate((element) => element.scrollWidth <= element.clientWidth), `Sampling main overflow at ${width}, tab ${number}`);
    }
    await page.getByRole("tablist", { name: "过程取样配置分区" }).scrollIntoViewIfNeeded();
    await page.screenshot({ path: `${output}/sampling-tab-07-${width}.png`, fullPage: true });
    await tab("01");
    await page.screenshot({ path: `${output}/sampling-${width}.png`, fullPage: true });
  }
}
