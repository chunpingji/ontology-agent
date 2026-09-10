# 文档分析工作区布局调整

日期：2026-09-09 UTC。根据用户后续 UI 要求修订 021 FR-069；分析产物、鉴权、
运行身份、预算和模型调用契约不变。

## 界面改动

- 分析历史作为左侧导航，保留任务状态、选中态、刷新、分页和关闭后回看。
- Word 章节树与文档预览为共享工作区；右侧并列“节点元数据”和“关系图谱”。
  原分层元数据中的节点摘要、分页可信度与来源范围在节点元数据标签中展示。
- 切换右侧标签保留 Word 预览实例和当前图谱选择；点击图谱证据保留图谱标签，
  联动同一运行的章节、段落或表格证据。用户改选章节时取消在途的旧来源请求，
  防止迟到响应覆盖最新选择。
- 图谱使用窄栏模式，实体、关系/属性、详情纵向排列，统计为两列，诊断表局部滚动。
  窄窗口中章节树从抽屉访问，预览与详情上下排列；未改全局应用侧栏。

主要文件：
[分析面板](../../frontend/src/components/analysis/document-analysis-panel.tsx)、
[历史导航](../../frontend/src/components/analysis/document-analysis-history.tsx)、
[图谱详情](../../frontend/src/components/analysis/document-relationship-graph.tsx)。

## 已执行静态与契约检查

工作目录 `frontend/`：

```bash
node --test tests/document-analysis-runs.test.mjs tests/document-ranking.test.mjs
npm run lint -- src/components/analysis/document-analysis-panel.tsx \
  src/components/analysis/document-analysis-history.tsx \
  src/components/analysis/document-relationship-graph.tsx
./node_modules/.bin/tsc --noEmit
```

Node **13 passed**；三个组件 ESLint、TypeScript 通过。既有源码断言仅更新标签名称，
没有新增按 CSS 文本复述实现的测试。两个既有浏览器脚本补充实际几何、预览实例保持
和图谱证据联动断言，语法检查通过。静态检查日志为新证据目录中的
`frontend-check-1.log`、`frontend-check-2.log`。

当前 Compose 前端使用 bind mount 与 Next.js 开发热更新，本次无需重启后端、迁移
数据库或中断任何分析运行。浏览器使用合成 API 拦截验证布局，不能当作真实模型
质量验收。新证据目录：`evaluations/022-analysis-layout-20260909/`。

## 实际浏览器验收

复用本机 Chrome **131.0.6778.85** 和外置 Playwright，在当前 Compose 前端执行：

```bash
PLAYWRIGHT_MODULE=/opt/dev/chen/jpi-project/jpi-test/zhjszx-replica/node_modules/playwright/test.mjs \
DOCUMENT_HISTORY_ORIGIN=http://localhost:8081 \
DOCUMENT_HISTORY_OUTPUT=/opt/dev/chen/ontology-agent/evaluations/022-analysis-layout-20260909/browser-07 \
node frontend/tests/document-analysis-history-browser.mjs
```

最终 **passed**，页面运行异常 **0**。1440/1920 桌面验证历史在工作区左侧、
章节树/预览/详情从左至右排列；768 窗口验证历史可选、章节抽屉和预览可用且页面
无横向溢出。两个实体、一条关系及长标签的合成图谱完成以下实际交互：

- 切换详情标签前后保留同一个 Tiptap 预览实例；图谱中的关系选择也保持。
- source GET 将原文定位到对应章节与一个物理锚点，图谱标签保持选中。
- 第二次 source 请求在途时改选章节，释放旧响应后仍保留新章节，不恢复旧定位提示。
- 保留历史关闭/刷新、同名多运行、分页、失败重试、快速切换和显式创建后的回看。

两次 source GET、一次显式创建 POST 均由合成拦截处理，没有请求真实后端或调用模型。
真实隔离 API 浏览器脚本本轮只更新了断言并通过语法检查，没有运行该集成场景。

- [最终报告](../../evaluations/022-analysis-layout-20260909/browser-07/report.json)
- [桌面工作区](../../evaluations/022-analysis-layout-20260909/browser-07/workspace-desktop.png)
- [图谱与原文联动](../../evaluations/022-analysis-layout-20260909/browser-07/graph-source-replay.png)
- [1920 宽屏](../../evaluations/022-analysis-layout-20260909/browser-07/workspace-1920.png)
- [768 窗口](../../evaluations/022-analysis-layout-20260909/browser-07/history-768.png)

375px 实测受既有固定 240px 全局应用侧栏限制，内容区过窄，未通过移动端可用性验证；
失败截图保留在 `browser-03/failure.png`。本次没有改动全局导航，不宣称 375px 已适配。
前几轮的开发入口加载失败、并行模板编辑的瞬时语法错误及测试调整记录均保留，最终
通过结果只引用 `browser-07`。未执行生产构建、后端部署或模型质量评测。
