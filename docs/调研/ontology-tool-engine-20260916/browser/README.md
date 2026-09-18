# 027 关系图谱真实 API / 浏览器验证

2026-09-16：通过。Chrome / Playwright 对隔离前后端发出 53 次真实 API 请求，全部为 GET；页面未拦截或替换 API 返回。测试前后 PostgreSQL 表计数、运行水位和受控模型调用计数保持一致，浏览器未记录页面异常或失败的 API 响应。

验证了新协议默认 `verified`、组的 selection / modality、4 个本体实体与 1 个关系组（不生成重复单边或虚拟本体实体）、独立实体可见、过滤父组后仍能解释属性的继承范围并跳转原文、刷新恢复，以及切回旧协议自动恢复 `effective_affirmed`。

- [结果与请求记录](report.json)
- [过滤父组后的继承范围和原文选区](tool-group-inherited-scope.png)
- [旧运行刷新后的图谱](restored-second-run.png)

环境采用本次新建的 `/tmp` 独立 PostgreSQL 14 集群、专用非管理员角色及空的 `*_browser_test` 数据库。前端为 `/tmp` 副本，复用本地依赖，使用 Next.js 16.2.9 的 `--webpack`，后端关闭正常 lifespan、后台 worker 和真实模型。未连接业务数据库或重启共享服务。验收后停止了本次前后端；临时数据库集群交给并行的 PostgreSQL 事务验收继续使用。

复用仓库现有入口，先准备专用空的 loopback PostgreSQL 数据库及独立前端副本，再运行：

```bash
# backend/；变量只指向隔离环境，输出目录必须尚不存在。
.venv/bin/python scripts/document_analysis_browser_fixture.py \
  --database-url "$DOCUMENT_BROWSER_DATABASE_URL" \
  --output "$DOCUMENT_BROWSER_FIXTURE" \
  --port 56881 --frontend-origin http://127.0.0.1:45059

# 独立前端副本目录；复用现有 node_modules，无安装或下载。
NEXT_PUBLIC_API_URL=http://127.0.0.1:56881 \
  ./node_modules/.bin/next dev --webpack --hostname 127.0.0.1 --port 45059

# frontend/；PLAYWRIGHT_MODULE 指向已有本地 playwright/test.mjs。
DOCUMENT_BROWSER_OUTPUT=browser-2 node tests/document-analysis-browser.mjs
```

前两个运行由旧协议受控响应产生；第三个运行使用真实 Word 解析与原文锚点，注入并持久化组、属性和独立实体的受控图契约制品。此结果只证明公共 API 与页面协作，不证明新版抽取协调器、Qwen 调用或抽取质量/F1。原始 trace 保存在临时验收目录，没有纳入仓库。
