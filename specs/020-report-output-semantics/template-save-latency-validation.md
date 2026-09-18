# 模板创建保存等待修复（2026-09-09）

## 调查依据与修改

用户反馈点击「进入模板定义」长时间显示「正在保存」。本次查看网关请求记录、模板数据库元数据及原件存在性，未输出样例正文：创建请求已返回 201，附件请求已返回 200；解析接口与附件接口响应各为 15,806,578 字节。数据库中该模板为 draft，样例解析结构及原件均已保存。编辑路由请求也已返回，但原前端在保存成功后一直保留 creating 锁，直到路由卸载才结束，因此无法准确反映已保存状态。未对原浏览器做性能采样，不能把完整等待时间归因于某一项。

当前创建 POST 仅提交模板定义和元数据；样例内容由附件保存写入，不再重复上传解析 JSON。附件接口 `include_content=false` 在原件落盘、解析结构入库及提交成功后返回 204，不再下载整份解析结果；不带参数的编辑页替换样例仍返回完整预览。

向导区分模板和样例保存进度。两步完成立即解除保存锁，显示「模板和输出样例已保存」及直接打开持久模板的链接，再发起页面跳转。已保存状态不重新提交；附件重试复用已有模板 ID。两次保存请求各有 180 秒等待上限，超时提示服务端可能仍在处理，不宣称回滚；关闭弹窗刷新列表供核对。

## 本次执行验证

前端：

```sh
./node_modules/.bin/tsc --noEmit --incremental false
npm run lint -- 'src/app/(dashboard)/settings/ast-templates/page.tsx' src/lib/api.ts
node --test tests/template-sample-parse.test.mjs tests/reporting-v2.test.mjs tests/extraction-capabilities.test.mjs
PLAYWRIGHT_MODULE=/opt/dev/chen/jpi-project/jpi-test/zhjszx-replica/node_modules/playwright/test.mjs node tests/template-creation-browser.mjs
```

类型检查、定向 ESLint 通过；Node 20 passed。Chrome 131.0.6778.85 / Next.js 16.2.9 隔离开发副本的合成 API 浏览器回归通过：16 MB 解析响应下创建负载小于 5 KB；创建首次 503 后成功一次；附件先 503、再停滞超时（将 180 秒加速为 1.5 秒）、最后 204。总计 2 次创建请求和 3 次附件请求，没有重复创建成功。阻断编辑路由 RSC 响应后，已保存提示和可关闭按钮保持可用，直接链接可进入持久编辑页。继续验证列表往返、刷新、样例恢复、后续新修订和普通 Word 退役接口零调用。保存成功且导航等待截图 `/tmp/template-entry-browser/saved-navigation-pending.png` 已目视检查。既有 Select 受控切换及 Tiptap underline 重复扩展警告仍存在。

后端：

```sh
.venv/bin/python -m pytest -p no:cacheprovider -q tests/test_api/test_template_sample_creation.py
.venv/bin/ruff check app/api/ast_templates.py tests/test_api/test_template_sample_creation.py
```

6 passed，4 条既有 Starlette/Pydantic 警告；Ruff 通过。使用真实合成 Word、隔离 SQLite 和临时目录：兼容完整响应与 204 响应，两种情况均验证原件字节、解析结构、列表/详情及同名版本冲突；模拟提交失败时必须抛错、保留旧附件并清理新文件。新增失败用例首次执行因共用 tmp_path 下的 ontology fixture 目录导致断言失败，改用独立 uploads 子目录隔离制品后通过，未放宽清理断言。

## 运行服务

沿用此前重启授权，确认有效分析/模型租约及活动抽取任务均为 0 后，执行 `docker restart ontology-agent-backend-1`。重启后直连及网关健康接口均为 200，运行服务 OpenAPI 已暴露 `include_content` 参数。数据库 revision 与代码 head 均为 `0035_ranking_budget_control`；这是工作区已有的新增布尔字段迁移，由既有启动流程从 0034 应用，本修复没有新增迁移。

网关实际返回的前端脚本已包含轻量附件请求及已保存入口。再次只读核对原模板仍为 draft、解析结构非空且原件存在。没有使用用户原件重新创建模板、发起模型任务或运行生产构建；浏览器 API 拦截与隔离数据库测试不等同真实用户原件端到端性能复测。
