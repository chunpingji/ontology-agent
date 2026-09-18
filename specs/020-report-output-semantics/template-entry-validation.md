# 进入模板定义即完成保存（2026-09-09）

本页记录创建语义调整时的验证。后续「正在保存」等待修复、当前传输契约及加载结果见 [template-save-latency-validation.md](template-save-latency-validation.md)。

## 当前行为

用户在向导中点击「进入模板定义」，依次保存模板草稿及原始输出样例，两步成功后才跳转到持久模板 ID 的编辑页，默认展示定义页签并提示已保存。立即返回列表、重新打开或刷新，初始模板和样例保留。后续编辑沿用「保存新修订」，不覆盖初始版本。

创建失败时保留向导内容；若模板记录已保存、仅附件失败，则明确提示部分成功，锁定原创建信息，重试只向同一模板 ID 附加样例。保存期间禁用重复提交、变更输入和关闭弹窗。关闭失败提示返回列表时，已保存的草稿仍可见。旧 `/settings/ast-templates/create` 地址回到列表，不再保留未保存创建页或跨页内存草稿。

这次仅调整前端创建时机与路由，复用原有模板创建、附件保存、版本修订接口。没有修改后端代码、启动模型、重启后端或新增迁移。保存为草稿不会自动发布模板。

## 本次验证

`frontend/`：

```sh
./node_modules/.bin/tsc --noEmit --incremental false
npm run lint -- 'src/app/(dashboard)/settings/ast-templates/page.tsx' 'src/app/(dashboard)/settings/ast-templates/create/page.tsx' 'src/app/(dashboard)/settings/ast-templates/[templateId]/page.tsx' src/components/reporting/output-template-editor.tsx src/components/extraction/template-slot-editor.tsx
node --test tests/template-sample-parse.test.mjs tests/reporting-v2.test.mjs tests/extraction-capabilities.test.mjs
PLAYWRIGHT_MODULE=/opt/dev/chen/jpi-project/jpi-test/zhjszx-replica/node_modules/playwright/test.mjs node tests/template-creation-browser.mjs
REPORTING_BROWSER_ORIGIN=http://127.0.0.1:53219 PLAYWRIGHT_MODULE=/opt/dev/chen/jpi-project/jpi-test/zhjszx-replica/node_modules/playwright/test.mjs node tests/reporting-browser.mjs
TEMPLATE_SAMPLE_OUTPUT=/tmp/template-entry-parse-browser PLAYWRIGHT_MODULE=/opt/dev/chen/jpi-project/jpi-test/zhjszx-replica/node_modules/playwright/test.mjs node tests/template-sample-parse-browser.mjs
```

类型检查通过；ESLint 无错误，保留原有 `meta.status` 多余依赖警告；Node **16 passed**。创建浏览器回归通过：模拟首次创建 503、首次附件 503，第二次附件成功后才进入编辑。总计 2 次创建请求（一次失败、一次成功）、2 次附件请求，附件重试没有重复创建。验证保存中锁定、进入即已保存、直接返回/刷新、定义页签保持、后续新修订及旧稿不变。普通 Word 退役接口请求为 0。既有报告编辑器回归通过，44 次合成 API 请求。

浏览器使用 Next.js 16.2.9 的隔离开发副本及 Chrome 131.0.6778.85，API 全部为合成拦截；不冒充真实后端端到端验证。截图 `/tmp/template-entry-browser/saved-template.png` 已目视检查。未运行生产构建，未用用户原件重测。

解析弹窗回归也通过：7 次合成解析请求覆盖换文件、取消、同文件重试及加速超时；并通过浏览器验证旧创建地址自动返回列表。`git diff --check` 通过。

`backend/`：

```sh
.venv/bin/python -m pytest -p no:cacheprovider -q tests/test_api/test_template_sample_creation.py
```

**4 passed**，4 条既有 Starlette/Pydantic 警告。复核既有真实合成 Word 解析、数据库草稿保存、附件字节保留、列表/详情读取和同名版本冲突；使用隔离 SQLite 和临时制品，不写运行中的业务数据。
