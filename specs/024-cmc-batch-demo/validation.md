# 本次验证（2026-09-10）

## 已实现

指定供外版 CMCReport 匹配后展示人工整理的静态图谱：99 条一级关系、4 个工艺阶段、
3 个工艺中间体、1 个最终产品，以及设备、物料、清洗和存放信息。66 条工艺操作逐行
来自原文表 1–4；本次逐字核对全部通过。原件 SHA-256：
`2c1174bf616f30dd28c655fef4261c167762de807c8ad79e148fb8ef18e16436`。

原批记录 v2.2 草稿 `8542466b-d6e6-4052-ae7e-05ca9c99a1c3` 已按用户后续要求
原位特化为“HRS-5592 批记录（演示专用）”，保留 UUID、URL、family 和修订号。
通过 `demo_profile` 引用随代码版本化的 `cmc-batch-demo-v1` 契约与唯一静态图谱，
包含 10 个输出章节契约和人工填写栏。读取图谱和生成均校验同一模板路径与属性；
Word 和在线预览共用保存的 OutputNode 内容。创建 GeneratedReport、制品及审计；
不创建识别运行、业务事实或 V2 ReportRun，也不调用模型。

## 实际执行

后端工作目录 `backend/`：

```bash
.venv/bin/python -m pytest -p no:cacheprovider -q \
  tests/test_api/test_batch_demo.py \
  tests/test_api/test_risk_report.py \
  tests/test_api/test_report_document_runs.py
# 26 passed，4 条既有依赖弃用/schema 命名警告

.venv/bin/ruff check app/api/batch_demo.py app/services/reporting/batch_demo.py \
  app/main.py app/api/extraction.py tests/test_api/test_batch_demo.py \
  scripts/serve_batch_demo_validation.py
# All checks passed
```

末轮补充保存源作业、文档版本和原件哈希后，单独重跑 `test_batch_demo.py`：
13 passed；新增断言可由保存的 source + graph 重算同一图谱哈希。对应服务及测试
Ruff 再次通过。此处是前述 26 项中的定向重跑，不另累计测试数量。

前端工作目录 `frontend/`：`tsc --noEmit` 通过；下列本次相关文件定向 ESLint 通过：
`batch-record-drawer.tsx`、`batch-demo-workspace.tsx`、`use-batch-demo.ts`、
`document-actions-menu.tsx`、`relation-panel.tsx`、报告详情 page、`api.ts`、
`relation-source-ref.ts`、`batch-report-preview.tsx`、`reading-pane.tsx`。
全局 `git diff --check` 通过。

按 quickstart 启动 `serve_batch_demo_validation.py`，使用真实原件副本和内存 SQLite；
运行 `frontend/tests/batch-demo-browser.mjs`，真实浏览器调用真实 FastAPI 路由，
没有拦截或伪造生成响应。通过的交互包括工艺链展开、原件预览/位置定位、10/10 输入
校验、生成、五阶段完成、预览关闭后下载、刷新恢复、390px 抽屉布局、保存结果的报告
中心预览；无浏览器异常。保存后的批记录使用与抽屉相同的表格预览，不出现 AI 生成
标识或原始规则对象。浏览器禁用 `crypto.randomUUID` 复验通过，生成请求键使用
HTTP 内网可用的 `crypto.getRandomValues`。
该流程仅一次显式生成 POST，没有 `/runs`、旧标注或风险生成请求；隔离库实际
`reports=1`、`analyses=0`、`annotations=0`。

本次浏览器制品位于 `/tmp/cmc-batch-demo-browser/`，包含截图和真实生成的
`batch-record-demo.docx`（约 53 KiB）。测试服务关闭时清理临时数据库/服务端制品，
浏览器下载副本保留供检查。

## 范围与限制

代码、隔离集成及当前环境页面验收完成。未运行生产构建、全库测试或 PostgreSQL 并发验收。
幂等的数据库主键冲突保护有实现；本次验证覆盖顺序重放与失败清理，不能将其称为
PostgreSQL 并发实测。后续特化已应用至当前数据库，后端已重启加载代码；前端开发服务
已加载变更。共享原件、本体、已生成报告和用户原有代码改动保留。

静态值属于演示输入；原文重复步骤编号、不同章节分子式/分子量差异均明确保留。
实际批号、实测值、签署未补造，输出只标为演示草稿。

## 模板特化补充验证

后端第一组运行 `test_batch_demo.py`、`test_batch_demo_template.py`、
`test_risk_report.py`、`test_report_document_runs.py`：34 passed。
随后运行 `test_batch_demo_template.py`、`test_report_runs_v2.py`、
`test_ast_template_basic_info.py`、`test_ast_template_default_source_job.py`：36 passed。
两组包含重复的特化测试，不累加；均有相同的 4 条既有警告。
验证包含只读预览、哈希冲突、审计失败回滚、重复特化、普通模板哈希不变、
来源删除/替换/旧抽取恢复/新抽取及普通报告入口拦截、跨用户结果隔离。
末轮补齐携带普通草稿的通用预览入口隔离后，重跑 `test_batch_demo_template.py` 和
`test_report_runs_v2.py`：16 passed；此为前述测试的定向重跑。

定向 Ruff、前端 TypeScript 及 ESLint 通过。隔离浏览器新增验证专用模板页：
生成前样例预览、10 项输入契约、从报告中心生成后模板读取同一保存结果，
两种 GET 的完整 payload 相等。修复了未激活的原文 tab 占用布局空间的问题。
浏览器仍只有一次生成 POST，最终 `reports=1, analyses=0, annotations=0`。

当前数据库特化审计序号为 **443**（`template.specialize_demo`，actor
`codex:user-request`），记录所有被替换字段的前后内容；再次执行返回 `changed:false`。
原 schema hash 为 `639fc7bdf721010a75209efd717d12bdb3e01ce4b5e2e7251aad386a9f04a9d8`，
新 schema hash 为 `ddd9a641cc4d724a6fbacfc6338efdca0907820eecc10cc17fe6bd5f087611e6`。
默认来源由旧模板副本 `885c6aec-9b09-49ec-a368-926100e93a02` 切换为报告中心原件
`6ed48bf1-410a-44cf-9371-2c6c744bd810`；原副本文件和作业保留。
数据库 revision 在重启前后均为 `0035_ranking_budget_control`。

任务状态已核实：模板关联的 `a0db2ad4-aab8-42e0-8e18-006a55c77d76`、
`fbfe58e1-1752-46d6-aeba-0756be7d53d4` 均 cancelled/revoked；旧模板抽取作业仍 paused。
同一报告的 `e6f51101-f2a5-4eec-b482-d41300856134` 已 paused/revoked，避免继续运行新内核。

使用短时操作员身份对现有 Next.js + FastAPI 做只读浏览器验收，临时域名解析指向
本机网关（使用当前已允许的开发域名，未改服务配置）：图谱三级展开、原件、契约、
报告预览、抽屉角色限制及“报告中心原件”跳转通过，零抽取/生成 POST，零页面异常。
截图保留在 `/tmp/cmc-batch-demo-live/`；生成、下载、持久化端到端验证在前述隔离服务完成。
末次加载后再次核实健康接口 200、共享模板输入校验通过、通用预览返回 409；
活动文档识别任务数为 0。隔离验证服务已关闭，临时浏览器认证文件已清理。


## 指定样例版式验证（2026-09-10）

参考模板为 `74cd92d6-41ee-42d5-84a2-b4a736a087b9`，当前 AST sections 为空。
采用其上传的批生产记录样例格式，样例 SHA-256：
`04fc9bc467d1bade90a2601c76c4ca069e00e0fd5058a04182b08e37196fc586`。
有效模板哈希包含参考 schema、Word 样例、sample_content 与渲染器版本。
当前环境 template_hash：
`3e88d136b0e467d0bb09861fafbcf8592be0b2ad4dd781bc37774e777534ea54`；
图谱仍为 `e59b8912039b2c7faaeb743482bd69b46f612ee224d7ac1fd52a1f25ffcea549`。

实现仅从样例继承格式，生成干净 DOCX；无样例媒体、嵌入对象和旧业务值。
共 7 个分节、35 张正文表；四阶段操作行数为 30/12/15/9。
封面保留样例 22pt 标题、留白、合并单元格、行高与下划线；A4 页面、边距、
工序页眉、五列操作表的 tblPr/tblGrid 与样例一致。实际值、检查结论和签署待填写。
参数栏仅显示操作原文的数值片段；完整要求保留在左栏，并附原文坐标。

- `test_batch_demo.py`、`test_batch_demo_template.py`、`test_batch_demo_layout.py`：
  29 passed。覆盖样例不可用/不兼容、样例与定义变更 409、历史下载、66 条原文、
  版式继承、合并与纵向合并清理、样例事实隔离、权限及无模型调用。
- 实际排版校准后，重跑版式测试与完整生成用例：9 passed；最后调整封面空行后，
  版式完整性用例再次 1 passed。后两组是前述测试的子集，不累加。
- 本次修改涉及的 Ruff、前端 TypeScript 和 ESLint 均通过。
- 最新代码的真实 UI → 隔离 FastAPI 浏览器流程通过：模板预览、生成、保存、下载、
  刷新、报告中心读取、模板共享结果及手机抽屉；仅一次生成 POST，
  `reports=1, analyses=0, annotations=0`。产物在 `/tmp/cmc-batch-demo-browser/`。
- 使用 LibreOffice 7.3 打开实际样例和生成 DOCX 并导出 PDF，核对封面与工艺表。
  最终输出在 `/tmp/hrs5592-rendered/final/`。浏览器为连续分节预览，分页以 Word
  排版为准；未以浏览器模拟分页替代 Word 校验。
- 当前后端已重启加载，真实 Next.js + FastAPI 的两入口数据一致、样例预览和抽屉
  角色门禁通过，零页面异常及零抽取 POST。截图在 `/tmp/cmc-batch-demo-live/`。
  数据库 revision 仍为 `0035_ranking_budget_control`；健康接口通过。
- 再次确认两条模板分析运行 cancelled、源报告运行 paused、旧模板作业 paused；
  活动分析执行、旧抽取执行和模型请求均为 0。临时认证文件与隔离服务已清理。

此轮未执行全库测试或 PostgreSQL 并发验收；不改写已保存的历史报告。


## 打印件留空调整（2026-09-10）

按用户要求，批号、实际记录、检查、生产地点和签署单元格使用空字符串，
保留字段标签、表格样式和未勾选框；内联日期、偏差及末尾填写项只保留标签。
同步修正共享演示说明中的占位文案。渲染版本为 `sample-layout-v2`，新版本不会
重放旧版结果；历史文件保留。

定向回归 `test_batch_demo.py`、`test_batch_demo_layout.py` 共 21 项，首次 19 项通过，
2 项全文检查发现共享说明仍含旧占位文案；修正该说明后，两项完整生成/版式用例复验通过。
本次修改的 Ruff 和前端定向 ESLint 通过。最新浏览器生成、预览、保存、下载、刷新及
模板共享流程通过，全文占位文案计数为 0，操作表填写列为空；仍只有一次生成 POST，
`reports=1, analyses=0, annotations=0`。下载实件 XML 检查通过：66 条操作保留，
198 个操作记录/签署单元格为空。产物位于 `/tmp/cmc-batch-demo-print/`。

当前环境已加载更新，源模板上下文全文无占位文案，输入校验通过。
新 template_hash 为 `0421be809f9eb0faff6d81c95927b4ba21e67c00b0314d53a0131b64cf53ec7b`；
graph_hash 为 `cea262623a1ab36db09a87e148610542aa3cc36ee6b0c68e24c843d9ae31c491`
（仅演示说明文本调整，业务事实不变）。健康检查通过，数据库 revision 保持
`0035_ranking_budget_control`，活动分析、旧抽取执行和模型请求仍为 0。
临时验证服务已关闭。

## 生产操作记录逐参数调整（2026-09-10）

渲染版本升级为 `sample-layout-v3`。人工核对 66 条源操作与参考样例的参数行，
将绑定、原文摘要、参数名称和记录格式保存到 `operation-forms.json`，其哈希纳入
有效模板版本。四阶段分别包含 214/150/179/190 条参数，共 733 条；第三阶段另保留
1 条无参数的说明，故四张操作表正文为 214/150/180/190 行。操作原文和两列签署
按操作纵向合并，参数与记录列每行独立；记录栏保留单位、时间格式、设备检查引用
及未勾选框，实际数值与签署为空。样例中的计算合并行拆为名称和填写区，不复制
样例中的业务数量及与源操作冲突的编号引用。

- `test_batch_demo.py`、`test_batch_demo_template.py`、`test_batch_demo_layout.py`：
  **31 passed**，4 条既有警告。覆盖完整生成/下载、733 条参数及单位、全部操作原文、
  纵向合并、样例字段错位/实测值污染拒绝、版本冲突、历史下载和权限隔离。
  首轮发现段落样式缓存键的 `XmlString` 不可哈希，转成普通字符串后全组通过。
- 定向 Ruff、前端 TypeScript、定向 ESLint、浏览器脚本语法及 `git diff --check` 通过。
- 真实 UI → 隔离 FastAPI 流程通过：模板预览、生成、逐参数行与 rowSpan、下载、
  刷新回读、报告中心和模板共享结果、手机抽屉。全程一次生成 POST，零抽取请求，
  最终 `reports=1, analyses=0, annotations=0`。首次加载及保存结果读取超过原脚本
  的 5 秒等待；脚本增加初始上下文就绪检查，异步读取断言允许 30 秒，业务断言保留。
  测试制品位于 `/tmp/cmc-batch-demo-parameters/`，下载 Word 为 99,266 字节。
- 下载实件逐行检查名称、单位、独立参数/记录单元格、空签署及无“待填写”通过。
  LibreOffice 7.3 转 PDF 得 92 页 A4，文本检查无空白页；查看操作页确认参数/单位
  逐行及纵向合并。PDF 位于该制品目录的 `render/`，浏览器预览仍为连续分节，
  分页以 Word/办公软件实际排版为准。
- 当前后端已重启加载，健康接口通过；共享模板返回 `sample-layout-v3`、正确行数
  和输入校验通过。有效 template_hash 为
  `5ea465d5251e1779504b2891bc0d340d1c351f9e2a2b70d67a82a7b629f83d42`；
  graph_hash 仍为 `cea262623a1ab36db09a87e148610542aa3cc36ee6b0c68e24c843d9ae31c491`。
  数据库 revision 保持 `0035_ranking_budget_control`，两条模板运行仍 cancelled/revoked，
  源报告运行仍 paused/revoked，旧模板作业仍 paused；活动分析、抽取执行和模型请求为 0。
- 当前 Next.js + FastAPI 的只读浏览器复验通过：两入口上下文完全一致，模板操作表
  参数行数、参数名称/kg 和纵向合并正确，原件、三级图谱、契约和抽屉角色门禁正常；
  零页面异常、零生成/抽取 POST。截图位于 `/tmp/cmc-batch-demo-parameters-live/`。
  临时认证文件已清理，隔离验证服务已关闭。

本轮未执行全库测试、生产构建或 PostgreSQL 并发验收；历史生成文件保持原样，
刷新后重新生成才能得到新的逐参数打印件。

## 模板页眉、表格字体和 A14 原表（2026-09-10）

按用户后续要求升级为 `sample-layout-v4`。五个正文工序页眉直接保留对应样例分节的
完整 XML（包括字体、原文、车间、表格、段落及命名空间），不再重建工序名或清空车间。
附加来源说明不伪造模板工序页眉。

A14 操作记录改为样例表 10–26（从 0 编号）的整段内容：5 张称量表、10 张工艺表、
2 张 TLC 留白表，38 条模板操作原样保留；同时复制原来的表间空行、分页及标题。
静态图谱仍含 66 条源操作，其他三阶段输出继续采用源操作。此轮是用户明确授权的
模板内容例外，A14 的计算式、编号与参数不再按源报告重组；实测值和签署保持留空。
预览保存逐段、逐 run 的中西文字体、字号、斜体与上下标信息；Word 原表保留原格式，
其他填充表在文字不变时保留各 run，替换文字时显式继承中西文字体与字号。

- 演示后端三组回归共 **33 passed**，4 条既有警告。新增页眉和 A14 原文修改后的
  版本失效/历史下载验证，逐表比较完整内容及格式，验证中西文字体、字号、斜体的
  Word 与 AST 保存结果。补回表间标题和分页后，两项完整生成/下载用例再次 **2 passed**，
  为前述测试子集，不累加。沙箱内 TestClient 在线程通信处阻塞；隔离测试在沙箱外完成，
  仍仅使用内存 SQLite 与临时制品，不运行应用启动生命周期。
- Ruff、TypeScript（禁用增量写入）、定向 ESLint、浏览器脚本语法与差异空白检查通过。
- 最终真实 UI → 隔离 FastAPI 流程通过：13 张操作表、5 张称量表、2 张 TLC 表，
  A14 页眉车间、首条 500L 投料、逐参数行和字体均有断言；生成、下载、刷新回读、
  模板共享历史及手机抽屉正常。一次生成 POST，零抽取请求、零页面异常，
  `reports=1, analyses=0, annotations=0`。制品位于 `/tmp/cmc-batch-demo-v4-final/`。
- 最终下载的 Word 为 **145,653 字节**；5 个页眉、A14 的 17 张表及各表之前的
  标题/分页段落与参考样例 XML 逐一相等（忽略无关的在作用域内命名空间声明）。
  无样例媒体或嵌入对象，全文无“待填写”。LibreOffice 7.3 转 PDF 为 **101 页 A4**，
  第 15 页为模板 A14 首张工艺表；已检查实际页眉、字体、参数和表间分页。

本轮未执行全库测试、生产构建或 PostgreSQL 并发验收。历史报告保留，重新生成使用新版本。

当前演示环境已加载 `sample-layout-v4`，真实 Next.js + FastAPI 只读浏览器验收通过：
模板与报告两入口上下文相等，页眉车间、A14 首条 500L 投料、13 张工艺表、5 张称量表、
2 张 TLC 留白表及抽屉角色门禁正常；零页面异常、零生成/抽取 POST。
截图位于 `/tmp/cmc-batch-demo-v4-live/`。当前 template_hash 为
`d38d0222a529da854fc6b1dc39c181564928b8bb3e38a4a74ca659b452fb26cd`，
graph_hash 仍为 `cea262623a1ab36db09a87e148610542aa3cc36ee6b0c68e24c843d9ae31c491`。
数据库 revision 保持 `0035_ranking_budget_control`；两条模板运行 cancelled/revoked，
源报告运行 paused/revoked，旧模板作业 paused；活动分析、抽取执行和模型请求均为 0。
临时认证文件已清理，隔离验证服务已关闭。
