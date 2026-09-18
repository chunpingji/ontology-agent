# 文档分析历史入口验证（2026-09-09）

本次补齐 `/analysis?tab=document` 的持久任务发现入口。沿用运行表与保留策略，新增 owner 范围的只读分页 GET；历史项显示文档、本体类型、创建时间、状态和保留期限。创建成功回到列表首页，关闭视图不删除任务；列表每 10 秒刷新并支持手动重试。

选择任务使用 Next.js 16.2.9 支持的原生 `history.replaceState` 同步写入 `documentRun`，避免详情先显示而路由尚未提交时立即刷新丢失选择。列表请求、任务轮询和来源请求均保留取消及迟到响应隔离。

## 本次执行

后端工作目录 `backend/`：

```sh
.venv/bin/python -m pytest -p no:cacheprovider -q -o faulthandler_timeout=30 tests/test_api/test_document_analysis_history.py
.venv/bin/python -m pytest -p no:cacheprovider -q tests/test_api/test_document_analysis.py tests/test_extraction/test_document_analysis_run_store.py
```

分别为 **7 passed** 和 **18 passed**。新增测试覆盖当前 owner、相同角色的其他 owner、同名多运行、创建时间/ID 排序、分页、空列表、认证、参数拒绝、过期/删除过滤、排除评测运行、真实上传后重新发现及 GET 零派发/事件写入。4 条警告来自既有 Starlette/Pydantic API 弃用和字段遮蔽。

仓库根目录执行以下 Ruff 检查通过（初次默认缓存目录不可写，后续禁用缓存）：

```sh
backend/.venv/bin/ruff check --no-cache backend/app/api/document_analysis.py backend/app/schemas/document_analysis.py backend/app/services/document_analysis/application.py backend/app/services/document_analysis/run_store.py backend/tests/test_api/test_document_analysis_history.py
```

前端工作目录 `frontend/`：

```sh
node --test tests/document-analysis-runs.test.mjs tests/document-analysis-retirement.test.mjs tests/document-ranking.test.mjs
npm run lint -- src/components/analysis/document-analysis-history.tsx src/components/analysis/document-analysis-panel.tsx src/lib/document-analysis.ts src/lib/api.ts
./node_modules/.bin/tsc --noEmit --incremental false
```

3 个 Node 测试文件、定向 ESLint 和 TypeScript 检查通过。原增量缓存不可写，类型检查改为关闭增量缓存。后端 TestClient 初次在受限环境阻塞，终止该次运行后，在获准的环境重跑通过；没有将阻塞的运行计为通过。

## 浏览器回归

复用已安装依赖，在 `/tmp/document-history-frontend-i29_plre` 的前端副本启动 Next.js webpack 开发服务器，监听 `127.0.0.1:53219`；没有重启应用后端或写入业务数据。

```sh
PLAYWRIGHT_MODULE=/opt/dev/chen/jpi-project/jpi-test/zhjszx-replica/node_modules/playwright/test.mjs node tests/document-analysis-history-browser.mjs
```

Chrome **131.0.6778.85**、1440×1100 桌面窗口通过：关闭结果后刷新、无任务 URL 重新进入、同名独立任务、查看后立即刷新、分页、快速切换时迟到响应隔离、错误重试、选择文件零上传、显式上传后入列和重新打开。仅产生用户显式启动对应的 **1 次 POST**，其他历史查看均为 GET；无浏览器运行错误。桌面截图：`/tmp/document-analysis-history-browser/history-desktop.png`。

浏览器 API 全部为合成拦截；真实 API 契约由上面的隔离 SQLite 集成测试验证。本次未运行真实模型评测、PostgreSQL 并发验收或生产构建，未部署；不改变原特性的其他发布门禁。

后续用户要求将历史入口改为左侧导航，并将元数据/图谱切换收纳到右侧详情栏；
这部分改动与新浏览器证据单列于 [工作区布局验证](layout-validation.md)，本页保留较早的历史入口验收结果。
