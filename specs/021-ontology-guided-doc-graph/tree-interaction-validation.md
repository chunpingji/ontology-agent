# 报告树形交互验证（2026-09-10）

## 实现范围

- `frontend/src/components/ui/tree.tsx`：基于 ReUI Radix Tree 的本地适配，MIT 声明与来源保存在同目录 `tree.LICENSE.md`。复用 Radix Slot、Lucide、主题变量及 Tailwind 3；采用非 button 的树节点容器，折叠、标题激活与证据按钮分别处理。
- `use-document-tree.ts`：Headless Tree core/react 固定 1.6.3；单选、方向键、Enter 激活、Space 选择，同步数据与可见节点状态。快照更新保留有效状态；删除或重新归属到折叠分支的焦点都有可见键盘入口；不积累重复展开 ID。
- `ReportDocumentOutline`：直接适配章节 node_id/children/source_range，标题可换行，目录身份包含用户、文档、运行及结构身份。
- `TemplateGraphTree`：复用现有生成森林、属性/关系/覆盖索引，将实体、谓词、属性断言和引用适配成树节点；关系证明附着在对应实体出现或引用行。保留所有证据角色与未关联循环；跳转只展开目标祖先，不附带展开无关的未关联分组。报告与模板共用此实现。
- `package.json` / `package-lock.json` 仅增加两个依赖，没有升级 Tailwind 或其他已有依赖。无后端、本体、接口及识别运行语义改动。

## 本次执行

工作目录均为 `frontend/`（diff 检查除外）。

| 命令 | 结果 |
|---|---|
| `./node_modules/.bin/tsc --noEmit` | 通过 |
| `npm run lint -- src/components/ui/tree.tsx src/components/ui/use-document-tree.ts src/components/reports/report-word-workspace.tsx src/components/analysis/template-document-graph-panel.tsx` | 通过 |
| `node --test tests/template-document-performance.test.mjs tests/document-analysis-runs.test.mjs` | 25/25 通过 |
| `node tests/report-tree-browser.mjs` | 9 组通过；真实 React StrictMode、Headless Tree 和 Tailwind 3，合成章节/图谱 |
| `node tests/report-word-workspace-browser.mjs` | 9 组通过；合成 API，26 次请求，2 次显式创建请求（失败及幂等重试） |
| `node tests/template-document-performance-browser.mjs` | 9 组通过；合成 API，28 次 GET，2 次取消读取 |
| `npm run build` | 生产构建通过，26 个静态页面；最终实现的类型检查通过 |
| 仓库根目录 `git diff --check` | 通过 |

浏览器执行环境：

```bash
export ESBUILD_MODULE=/opt/dev/chen/infilake-dw/frontend/node_modules/esbuild/lib/main.js
export PLAYWRIGHT_MODULE=/opt/dev/chen/jpi-project/jpi-test/zhjszx-replica/node_modules/playwright/test.mjs
node tests/report-tree-browser.mjs
node tests/report-word-workspace-browser.mjs
TEMPLATE_PERFORMANCE_OUTPUT=/tmp/template-document-reui-browser node tests/template-document-performance-browser.mjs
```

新增浏览器验收涵盖：四层章节与准确 source_range；箭头不导航、标题不折叠；方向键/父节点/Enter；共享引用、根循环和未关联循环；属性值、属性/关系依据、主体、条件、反证和实体原文按钮；覆盖及未尝试状态；增量新增/改名/删除/重新归属；身份重置；长标题、缩进、选择和深浅主题。按钮键盘操作不激活外层节点，无嵌套按钮，纯树操作不访问 API。

新增截图和结果位于 `/tmp/report-tree-browser/{light.png,dark.png,result.json}`；模板流程结果位于 `/tmp/template-document-reui-browser/result.json`。这些是合成数据交互证据，不是模型质量评测。

## 当前服务与验证边界

- 当前前端为 Compose override 的开发模式，源码 bind mount。将两个已安装的纯 JS 依赖同步至前端依赖卷；重启前端以清除安装前的模块解析缓存，启动时间为 `2026-09-10T02:17:43.081051208Z`。这是开发服务同步，没有制作/发布生产镜像。
- 后端未重启，启动时间仍为 `2026-09-10T01:43:57.868823852Z`；没有新建、暂停或恢复任何真实识别运行。
- 指定报告路由在重启后返回 HTTP 200；匿名浏览器正常转到登录页，页面错误 0、API 写请求 0。真实原文接口对无令牌请求返回 401；本次未取得用户登录会话，未完成该报告登录后内容的真实浏览器验收。
- 安装提示本机 Node 20.18 低于现有 `eslint-visitor-keys` 的 engine 要求，实际 lint 已通过；部署容器使用 Node 22。构建保留既有多个锁文件的 workspace-root 提示，Tailwind 检查保留既有 Browserslist 数据提示；本次未顺带升级或重配。
