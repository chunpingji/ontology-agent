# 从输出样例创建模板修复验证（2026-09-09）

本文件保留第一轮修复的验证证据。用户随后要求「进入模板定义即保存成功并进入编辑状态」，当前创建语义和验证见 [template-entry-validation.md](template-entry-validation.md)；下述未保存创建页已被替换。

## 问题与修复

新建页默认进入「源文档」且自动选择首个匹配文档；关系图谱无条件使用旧证据接口，普通 Word 返回 410。保存按钮只存在于「AST模板定义」右栏，用户在默认页签找不到保存入口，返回列表后也没有创建记录。

现在新建直接展示输出样例和定义，不自动选择输入文档；全局工具条保留「保存模板」/「保存新修订」。未保存状态明确提示，返回按钮和面包屑离开需确认，刷新/关闭页面提示未保存；这里没有实现全站导航或浏览器后退拦截。保存失败在页内提示并保留草稿，重复点击由进行中的保存状态和同步锁拦截。创建请求使用编辑后的来源类型及文档编号；原始格式文件仍通过已有样例接口附加。

源文档详情响应只新增 `source_mode`。前端按源类型与模式决定是否读取旧正文、证据及进度；普通 Word 引导到文档分析，模板专用 Word 和 Excel 保留既有能力。没有将模板输出样例当成输入文档，也没有从页面读取发起模型识别。

## 实际验证

`backend/`：

```sh
.venv/bin/python -m pytest -p no:cacheprovider -q tests/test_api/test_template_sample_creation.py tests/test_api/test_automatic_template_context.py
.venv/bin/python -m pytest -p no:cacheprovider -q tests/test_api/test_retired_word_analysis.py tests/test_api/test_document_preview.py
.venv/bin/ruff check --no-cache app/schemas/extraction.py tests/test_api/test_template_sample_creation.py
```

分别 **7 passed**、**12 passed**；每组 4 条既有 Starlette/Pydantic 警告。Ruff 通过。新保存测试使用真实合成 `.docx`、隔离 SQLite 和 `tmp_path`：解析→保存草稿→附加格式→列表→详情、同名版本冲突均通过，原件字节保留且未创建抽取作业。没有运行真实模型。

`frontend/`：

```sh
node --test tests/extraction-capabilities.test.mjs tests/reporting-v2.test.mjs tests/template-sample-parse.test.mjs
./node_modules/.bin/tsc --noEmit --incremental false
npm run lint -- 'src/app/(dashboard)/settings/ast-templates/page.tsx' 'src/app/(dashboard)/settings/ast-templates/create/page.tsx' src/components/reporting/output-template-editor.tsx src/components/extraction/template-slot-editor.tsx src/lib/api.ts src/lib/extraction-capabilities.ts
PLAYWRIGHT_MODULE=/opt/dev/chen/jpi-project/jpi-test/zhjszx-replica/node_modules/playwright/test.mjs node tests/template-creation-browser.mjs
REPORTING_BROWSER_ORIGIN=http://127.0.0.1:53219 PLAYWRIGHT_MODULE=/opt/dev/chen/jpi-project/jpi-test/zhjszx-replica/node_modules/playwright/test.mjs node tests/reporting-browser.mjs
```

Node **16 passed**；类型检查通过；ESLint 无错误，保留 `template-slot-editor.tsx` 中已有的 `meta.status` 多余依赖警告。浏览器使用临时 Next.js 16.2.9 开发副本、Chrome 131.0.6778.85，全部 API 为合成响应。

新建回归覆盖默认页签、各页签保存入口、800px 窄屏入口、离开确认、503 后重试保留章节、保存中禁用、原始格式附加、列表刷新与重新打开。普通 Word 的退役接口请求 **0 次**；`preview_only=true` 的模板专用来源仍读取证据。既有报告编辑器 44 次合成 API 请求回归通过。初次用例遇到 Next 路由播报器造成的 alert 选择器歧义，已限定业务错误；同时运行开发服务器浏览器用例时曾发生创建导航退回列表，单独运行完整新建回归通过，未将该次超时计为通过。控制台有既有 Select 受控状态及 Tiptap 重复扩展警告，无最终用例页面运行异常。

截图已目视检查：`/tmp/template-creation-browser/saved-template.png`、`/tmp/template-creation-browser/save-narrow.png`。未执行生产构建，未使用用户原件重测。

## 生效检查

沿用用户此前的后端重启授权，仅执行 `docker restart --time 60 ontology-agent-backend-1`；新启动时间为 **2026-09-09 06:30:31 UTC**。网关 `/api/health` 返回 200，10 个模块已加载。容器中响应模型包含 `source_mode`；数据库 revision 与代码 head 均为此前已存在的 `0034_model_request_run_id`，本次修复没有新增迁移。临时前端开发服务已停止，`git diff --check` 通过。

较早检查有效分析租约为 0；重启时核验已有一项新的运行任务。任务 `4cfb7417-a873-45b4-ae78-a30632523300` 在旧租约过期后取得 generation=13，06:33:24 UTC 心跳更新，状态为 `running`、无停止原因。观察时事件水位仍为 401，未据此宣称完成新分析批次或整个任务；没有手工改写任务/检查点。历史依赖阻塞任务保持原状。
