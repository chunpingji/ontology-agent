# 报告中心 Word 对接验证（2026-09-10）

## 本次实现

按[接口契约](contracts/report-document-runs.md)新增登记文档到新运行的适配。
文档 IRI 精确定位原件，登记类型作为根类型，运行源 artifact 保存关联及版本。
报告页 Word 分支不再请求旧 annotated-document；模型运行前可只读预览原件，
显式识别后读取新运行的结构、图谱和证据定位，保留关系栏调宽操作。

当前文档库是登录用户共享范围，新运行及缓存按 owner 隔离。纯原件预览不读取
旧标注缓存、不更新旧作业，也不启动摘要或识别。新运行不自动提交业务事实。

## 已执行验证

后端工作目录 `backend/`：

```bash
.venv/bin/python -m pytest -p no:cacheprovider -q tests/test_api/test_report_document_runs.py tests/test_api/test_template_document_runs.py tests/test_api/test_document_preview.py tests/test_api/test_retired_word_analysis.py
.venv/bin/python -m pytest -p no:cacheprovider -q tests/test_api/test_document_analysis.py tests/test_api/test_document_analysis_schemas.py
.venv/bin/ruff check app/services/document_analysis/report_documents.py app/api/document_analysis.py app/schemas/document_analysis.py tests/test_api/test_report_document_runs.py
```

分别 24 passed、30 passed；Ruff 通过。补充 private/no-store 响应头后，定向重跑
`test_report_document_runs.py`：10 passed。使用现有隔离 SQLite fixture 和临时原件；
测试保留历史库/模型桩边界，没有配置或执行会清表的 PostgreSQL 并发 fixture。

前端工作目录 `frontend/`：

```bash
node --test tests/document-analysis-runs.test.mjs tests/template-document-performance.test.mjs
npm run lint -- 'src/app/(dashboard)/reports/[reportId]/page.tsx' src/components/reports/report-word-workspace.tsx src/components/analysis/use-template-document-run.ts src/lib/api.ts
./node_modules/.bin/tsc --noEmit
```

25 个 Node 测试通过；定向 ESLint、TypeScript 通过。另用既有外部依赖执行：

```bash
ESBUILD_MODULE=/opt/dev/chen/infilake-dw/frontend/node_modules/esbuild/lib/main.js PLAYWRIGHT_MODULE=/opt/dev/chen/jpi-project/jpi-test/zhjszx-replica/node_modules/playwright/test.mjs node tests/report-word-workspace-browser.mjs
ESBUILD_MODULE=/opt/dev/chen/infilake-dw/frontend/node_modules/esbuild/lib/main.js PLAYWRIGHT_MODULE=/opt/dev/chen/jpi-project/jpi-test/zhjszx-replica/node_modules/playwright/test.mjs node tests/template-document-performance-browser.mjs
```

报告工作区 9 项浏览器检查通过：正文、章节导航、只读刷新、显式创建、失败重试保留
幂等键、刷新恢复运行、owner 隔离、迟到响应隔离、原件错误与重试。模板工作区原有
9 项检查通过，含 SSE 合并/断连恢复/取消与证据选择。均运行真实 React/Query/WordViewer，
HTTP 响应来自隔离合成服务，不等同真实后端或模型端到端验收。

报告运行的正文和目录合并使用 metadata 响应，证据选择仍单独读取 source-selection。
浏览器另断言没有重复请求运行级完整 source；最终场景共 26 个请求，其中 2 次 POST
分别为模拟失败及使用同一幂等键重试，其余均为读取。

## 指定原件核验与部署状态

对 `upload-a255fd30-192c-4f81-92df-5f76a56b8383` 使用运行容器内的新适配服务及公开
响应 schema 做只读核验（数据库事务设置 READ ONLY）：原件成功解析为 **30 个章节、
139 个正文顶层节点**，源作业 `5b181648-77a8-4f47-a6b1-5871df27d58c`，登记版本 1。
没有启动该文档的新识别任务，没有导入旧结果或改写原件。

本次未运行生产构建、完整后端套件或真实模型质量评测。后端当前启动命令不含
`--reload`；未重启服务，新增 HTTP 路由需加载新代码后生效。上述原件核验是独立
进程中的服务调用，不声称当前运行中的 HTTP 服务已部署新增路由。
