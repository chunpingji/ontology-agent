# HRS-1597 报告模板与 PDE 内联审核验证

日期：2026-09-15。范围仅为 `HRS-1597原料临床备样生产信息表_--脱敏-原 - PDE-冲突.docx`。

## 实现

- 模板上下文只显示“风险评估文档V1版”同根类、创建时间最新的修订，自动选中且没有默认空选项；实际数据核对时为 `v2.6`（`31ce642d-e960-4413-a3c1-75f1a66f025f`）。旧深链也解析到最新修订，其他文档保持原选择行为。
- 目标报告的 Finder 工作区恢复 `b6f9bd4e55822e20dce0a2ef0fa8f6d9fdd472c1` 的卡片、220px 目录、默认 300px 可调图谱、三栏独立滚动与标题行重新识别按钮；复用原有琥珀 PDE 内联卡片，保留当前原文锚点。
- 指定原件显式重新识别时比较同一节点的原文 PDE、NOAEL、种属、周期及 F1–F5。原文提供的因子优先；方法默认值在来源中说明。不将 F4=1 转为三项危害阴性，也不将未知危害导致的保守等级提升当成 PDE 数值冲突。
- 两行原文结构的隔离回归得到第一行 **20 mg/日对100 mg/日、5倍、同属band1**；第二行 **7.5 mg/日**一致，无冲突。缺单位、周期、种属、原文定位或不能计算的值返回待复核说明。
- 审核复用源级决策记录，通过当前用户/模板/源/执行校验和版本 CAS 保存；执行头只记当前审核版本。重新识别后显示待复核，旧记录保留。GET 不派发或保存，旧 Word 退役接口保持拒绝。

## 本次实际验证

| 验证 | 结果 |
|---|---|
| 新模板上下文 API 测试 | 7 passed |
| 新 PDE API、独立连接并发及相关 Finder/旧 PDE/退役入口回归（去重） | 55 passed，4 skipped |
| 新报告 Finder 浏览器脚本 | 通过 |
| 既有 Finder 浏览器脚本 | 通过 |
| Finder Node 客户端测试 | 通过 |
| 受影响 Python Ruff、前端 ESLint、完整 TypeScript 检查 | 通过 |
| 差异空白检查 | 通过 |

4 项 skip 均因专用 PostgreSQL 测试库未配置。SQLite 独立连接的首次创建/更新竞争均只允许一次成功，不能替代 PostgreSQL 并发验收。

新浏览器脚本使用真实组件、hooks、Tailwind 和 Chrome，HTTP 为隔离 fixtures。验证实际三栏坐标、拖动/键盘/双击宽度、琥珀颜色、公式/F1–F5、三种审核选择、刷新回显、409 后等待最新版本再重试、重识别待复核、迟到响应隔离和只读角色。它不等同已部署页面的真实后端验收。

可复跑入口：

```bash
# backend/
.venv/bin/python -m pytest -p no:cacheprovider -q \
  tests/test_api/test_report_recognition_context.py \
  tests/test_api/test_template_finder_pde.py \
  tests/test_extraction/test_template_finder_pde_concurrency.py \
  tests/test_extraction/test_template_finder_pde_postgresql.py

# frontend/，浏览器工具通过 ESBUILD_MODULE / PLAYWRIGHT_MODULE 指向现有安装
node tests/report-finder-review-browser.mjs
node tests/template-finder-browser.mjs
node --test tests/template-finder.test.mjs
./node_modules/.bin/tsc --noEmit --incremental false
```

## 加载与真实页面

加载前已确认文档执行、Finder 执行及模型请求无活动任务，数据库 revision 和代码 head 都是 `0041_template_engine`，没有新迁移。04:38 UTC 使用 `docker compose restart --timeout 30 backend` 加载修改；启动完成后网关 `/api/health` 返回 `status: ok`、`modules_loaded: true`、`module_count: 10`。前端沿用挂载源码的开发热更新。

真实登录页面验收尚待明确授权。自动审批拒绝使用源码默认管理员凭据登录；没有绕过该拒绝、生成替代令牌或保存真实业务审核决定。
