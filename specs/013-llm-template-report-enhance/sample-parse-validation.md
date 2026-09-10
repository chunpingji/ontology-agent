# 样例文档解析卡顿修复验证（2026-09-09）

## 现象与定位

用户在“从样例文档创建模板”上传输出样例后，长时间停留于“正在解析文档…”。运行中的网关日志记录到两次 `/api/ast-templates/parse-sample` 请求，从请求体落临时文件到返回分别约 26 分 50 秒和 27 分 47 秒，均最终返回 HTTP 200，响应为 15,806,578 字节。未获得用户本次上传的原件，未宣称已经对同一原件完成前后对比。

在 python-docx 1.2.0 上复现了样式目录较大时的性能瓶颈：每次 `paragraph.style` 读取都会重新扫描默认样式。100 个普通段落、额外 500 个样式定义的固定合成结构，在 cProfile 下解析耗时从 9.666 秒降到 0.347 秒；旧路径执行 900 次默认样式扫描，占用约 97% 的耗时。该测量是合成文件的性能证据，不代表用户原件的最终耗时。

## 修改边界

- `docx_reader.load_docx_for_reading` 为每次加载的文档独立缓存 python-docx 原有的样式解析方法。复用缺失样式、类型不匹配时的默认回退；不跨文档、用户或文件路径共享。该加载器只供解析使用，不能用于修改样式定义的文档编辑代码。
- 结构解析和格式预览复用该加载器，未改变模型调用、正文、格式、结构哈希或证据标识。
- 前端请求复用认证客户端；从上传到响应体读取总计最多等待 180 秒。超时显示可重试错误；换文件、关闭弹窗、卸载页面会取消客户端请求，迟到响应不得覆盖新选择。
- “取消等待”只中止浏览器等待，不宣称终止服务端已开始的解析线程。可重新选择同一文件；当前文件名单独展示。

## 本次检查

`backend/`：

```sh
.venv/bin/python -m pytest -p no:cacheprovider -q tests/test_extraction/test_docx_reader.py tests/test_extraction/test_docx_structure.py tests/test_extraction/test_word_formatting.py tests/test_api/test_word_evidence.py tests/test_reporting/test_suggest_slots_api.py
```

**45 passed**，4 条既有 Starlette/Pydantic 警告。新增回归比较缓存前后的完整解析输出，包含格式、嵌套/合并单元格和证据；验证默认样式扫描次数不再随段落数增长，同路径替换文件不复用旧样式。

仓库根目录：

```sh
backend/.venv/bin/ruff check --no-cache backend/app/services/extraction/docx_reader.py backend/app/services/extraction/docx_structure.py backend/app/services/extraction/document_annotator.py backend/tests/test_extraction/test_docx_reader.py
```

Ruff 通过。

`frontend/`：

```sh
node --test tests/template-sample-parse.test.mjs tests/api-read-cancellation.test.mjs
npm run lint -- 'src/app/(dashboard)/settings/ast-templates/page.tsx' src/lib/api.ts
./node_modules/.bin/tsc --noEmit --incremental false
PLAYWRIGHT_MODULE=/opt/dev/chen/jpi-project/jpi-test/zhjszx-replica/node_modules/playwright/test.mjs node tests/template-sample-parse-browser.mjs
```

Node 的两个测试文件（共 8 个用例）、定向 ESLint、TypeScript 检查通过。Chrome 131.0.6778.85 在隔离的 Next.js 开发副本通过换文件、取消等待、相同文件重试、关闭重开、超时后重试；共 7 次合成解析请求，无浏览器运行错误。浏览器将 180 秒定时器加速为 100 毫秒，仅用于测试；真实超时常量另由 Node 用例验证。截图位于 `/tmp/template-sample-parse-browser/retry-success.png`。

浏览器全部使用合成 API；真实解析和 API 契约由隔离后端测试覆盖。上述工程验证未调用真实模型，未执行生产构建。

## 用户授权后的后端重启

用户明确要求“请重启”后，执行 `docker restart --time 60 ontology-agent-backend-1`，命令成功退出。仅重启后端容器，新的启动时间为 **2026-09-09 04:16:50 UTC**。后端直接访问（8000）及网关访问（8081）的 `/api/health` 均返回 HTTP 200，`modules_loaded=true`、`module_count=10`。数据库实际 revision 和容器内 Alembic heads 均为 `0033_document_analysis_runs`。

在重启后的容器中用临时合成 Word 文件执行 `parse_word_to_tiptap`，标题、正文和表格解析断言通过，耗时 0.039 秒，并确认独立文档实例的样式缓存已启用。临时文件随检查清理；这项检查没有经过 HTTP 上传，不代表用户原件的实测耗时。

两个原有分析任务在旧租约过期后被自动领取，但恢复被依赖指纹校验拒绝；截至 04:19:06 UTC，均为 `blocked_dependency`，错误码 `FINGERPRINT_MISMATCH`、`retryable=false`，未恢复持续执行。执行租约均已撤销（generation=3）。没有改写依赖指纹、重置任务或绕过恢复校验。

| 运行 ID | 重启后事件水位 | 保留的图谱 / 检查点版本 | 当前状态 |
|---|---:|---:|---|
| `2f6940fa-2959-4998-97b0-a80e296a4933` | 1815 | 1795 | 依赖阻塞 |
| `ef723c59-6ec0-4ea3-b8ac-8d8649badc0f` | 1273 | 1255 | 依赖阻塞 |

只读核验确认两者的原件、结构、元数据、本体快照、排序状态、部分图谱和识别检查点制品引用仍保留，历史事件数量与事件水位一致。已核实阻塞为依赖指纹不匹配；本次没有进一步确定具体变化的依赖项，也未重新发起分析任务。
