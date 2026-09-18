# 演示验收

1. 登录报告中心，打开指定 HRS-5592 供外版 Word。图谱标注静态演示，展开“工艺描述”
   下四个工艺阶段及其产出中间体；检查设备、物料、存放条件的属性和原文来源。
2. 操作 → 生成批记录报告，展开输入校验和模板路径，开始生成。
3. 检查五个阶段完成，预览批生产记录封面和五列工艺表，下载 DOCX。
   版式来源为模板 74cd92d6-41ee-42d5-84a2-b4a736a087b9 的 Word 样例。
   页眉工序名称和车间应与模板一致，如“SM5592-A14的制备 / 642/646车间”。
   A14 操作记录整段采用模板的 17 张原表：5 张称量表、10 张工艺表、2 张 TLC 留白表，
   38 条操作按模板原顺序输出，首条为 500L 反应釜投料，保留表间标题和分页。
   表内中文、西文字体及字号应与模板一致。图谱仍保留 66 条源操作，其他三阶段的
   输出继续采用源操作，三张工艺表为 150/180/190 行（含 1 条无参数说明）。
   参数独占一行，实际记录留白并预填单位；A14 样例自身的单个计算项跨列格式按原样保留。
   例如“1，4-二氧六环”单独一行，记录列为留白加 kg；钯试剂另一行使用 g。
   预览及 Word 中的批号、实际数值、签署栏应为空白，全文不出现“待填写”。
4. 刷新后再打开抽屉，历史结果仍可预览/下载；报告中心可找到新结果。
5. 切换其他文档不显示此批记录演示入口；源文件变化时阻止使用过期演示数据。
6. 打开 `/settings/ast-templates/8542466b-d6e6-4052-ae7e-05ca9c99a1c3`，
   应显示“HRS-5592 批记录（演示专用）”，可切换原文与关系图谱、模板契约和报告预览。
   模板的 10 项输入校验与报告中心一致；生成后两处查看同一用户的同一份保存结果。
   页面不显示上传来源、重新识别或普通模板发布操作。

本流程不启动模型、不更新已有识别运行，不代表生产执行或质量批准。

## 首次配置既有模板

`backend/scripts/specialize_batch_demo_template.py` 默认只预览；须指定当前 schema hash
和操作人，添加 `--apply` 才执行。仅允许该 ID 的非默认 V2 草稿；工具保存前后审计、
校验原件与源登记，相同配置重复执行不产生新写入。当前环境已完成特化，无需重复配置。

```bash
.venv/bin/python scripts/specialize_batch_demo_template.py \
  --expected-hash CURRENT_SCHEMA_HASH --actor OPERATOR
```

## 隔离复验

在 `backend/` 启动只绑定本机的临时验证服务；`--source` 指向原件的本地副本，
`--layout-sample` 指向指定参考模板的 Word 样例副本。
服务验证原件 SHA-256，使用内存 SQLite 和临时制品目录，退出自动清理；
不执行应用启动迁移、TTL 播种或模型预热。此工具仅供开发验收。

```bash
.venv/bin/python scripts/serve_batch_demo_validation.py \
  --source /absolute/path/to/source.docx \
  --layout-sample /absolute/path/to/layout-sample.docx
```

在 `frontend/`，使用现有 Tailwind 编译样式，再运行真实 UI → 隔离 FastAPI 流程。
浏览器工具使用外部已安装 esbuild/Playwright，无需修改项目依赖；示例变量值替换为
当地安装的模块入口。每次完整复验需要重新启动一个空的验证服务。

```bash
./node_modules/.bin/tailwindcss -i src/app/globals.css -o /tmp/cmc-batch-demo.css
BATCH_DEMO_TEST_BACKEND=http://127.0.0.1:18769 \
BATCH_DEMO_BROWSER_CSS=/tmp/cmc-batch-demo.css \
ESBUILD_MODULE=/absolute/path/to/esbuild/lib/main.js \
PLAYWRIGHT_MODULE=/absolute/path/to/playwright/test.mjs \
node tests/batch-demo-browser.mjs
```

默认截图和下载文件输出到 `/tmp/cmc-batch-demo-browser/`；可用
`BATCH_DEMO_BROWSER_OUTPUT` 指定目录。浏览器脚本会真实生成一个演示报告，不能指向生产服务。
