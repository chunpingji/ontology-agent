# 验证

## 并列入口与新文档上传（2026-09-30）

图谱分析与文档分析是并列功能；本节替代下方历史 V2 验证中的「返回文档分析」头部。
图谱分析入口默认展示「上传新文档」，结果页保留顶层功能标签并提供「新建分析」。

1. 打开 `/analysis?tab=graph-analysis`，确认图谱分析和文档分析是并列标签。
2. 选择 .doc / .docx 和本体根类型；缺少任一输入不可开始，选择输入不会上传或启动模型。
3. 点击「开始分析」；成功后的 URL 仍为 `tab=graph-analysis`，并包含新 `documentRun`。
4. 上传失败保留文件和类型，可以重试；HTTP 内网入口也能生成请求键，同草稿重试沿用请求键。
5. 结果页「新建分析」回到本功能上传入口；已有报告、历史、刷新和功能切换均不自动启动识别。

在 `frontend/` 实际执行：

```bash
node --test tests/document-analysis-runs.test.mjs tests/source-harness.test.mjs tests/target-graph.test.mjs
./node_modules/.bin/tsc --noEmit
npm run lint -- src/components/analysis/analysis-tabs.tsx \
  src/components/analysis/graph-analysis-panel.tsx \
  src/components/analysis/graph-analysis-upload.tsx \
  tests/graph-analysis-upload-browser.mjs tests/source-harness-browser.mjs
PLAYWRIGHT_MODULE=/opt/dev/chen/jpi-project/jpi-test/zhjszx-replica/node_modules/playwright/test.mjs \
  node tests/graph-analysis-upload-browser.mjs
PLAYWRIGHT_MODULE=/opt/dev/chen/jpi-project/jpi-test/zhjszx-replica/node_modules/playwright/test.mjs \
  DOCUMENT_BROWSER_OUTPUT=/tmp/source-harness-parallel-navigation-browser \
  node tests/source-harness-browser.mjs
```

结果：**77 passed**，类型检查、定向 ESLint 通过。上传浏览器验证包括本体目录失败重读、
Word 后缀/根类型校验、显式 multipart 上传、失败保留草稿、相同请求键重试、重复点击阻止、
结果定位、刷新、新建和历史/功能切换。模拟了 2 次上传 POST，全部 API 被拦截，无真实模型调用。
现有结果页浏览器回归通过，22 次 GET、0 次写请求，无未拦截 API 或脚本错误。
产物分别位于 `/tmp/graph-analysis-upload-browser/` 与 `/tmp/source-harness-parallel-navigation-browser/`。
390px 截图仍受既有固定侧栏挤压，当前测试的控件可见/页宽断言不代表移动端布局验收。
前端开发容器挂载源码，已通过当前入口加载修改；本轮没有重启后端或创建真实分析运行。

## 工艺物料关系的主体范围（2026-09-22）

权威 TTL 的 `usesMaterial` 定义域为 `CMCReport ∪ SynthesisStep`，值域为
`ProcessMaterial`。报告描述所需物料，步骤实际投入或使用物料；报告级记录不自动
归属到某个步骤。使用单个 `owl:unionOf` 表达两类主体择一适用，避免多个 domain
被解释为必须同时属于报告和步骤。

在 `backend/` 执行：

```bash
.venv/bin/python -m pytest -p no:cacheprovider -q \
  tests/test_cmc_material_ontology.py tests/test_cmc_product_ontology.py \
  tests/test_document_harness/test_ontology.py --tb=short
.venv/bin/ruff check tests/test_cmc_material_ontology.py
```

本轮 **17 passed，4 个既有 warning**，Ruff 通过。新增两个回归在修补前均失败，
修补后从权威 TTL 加载隔离 World，再冻结运行目录，确认工艺物料及五个子类均可达，
进入模型类型菜单并保留规格/用途属性；合成步骤子类继承关系，无关主体、非物料对象
和反向关系不被授权。三元组 diff 为新增 8 条、删除 1 条（含替换中文说明和新增
并集结构）；其他源码字节及原有未提交修改保留。

这是本体加载与识别目录验证，未重新运行 3.8 模型抽取、发布在线本体或重写原运行。

## 表格实体结构定位（2026-09-22）

每个实体的 anchor 必填，整来源可仅给 source_id，局部对象再补精确 text/occurrence。
程序复用 IR 表格路径、行列和物理单元格；name 可省略，不能因此丢失设备或把对象视为
没有名称。登记失败须保留证据与字段观察，并计入窗口未完成。

在 `backend/` 执行核心与运行集成回归：

```bash
.venv/bin/python -m pytest -p no:cacheprovider -q \
  tests/test_document_harness/ \
  tests/test_extraction/test_document_harness_runtime.py --tb=short
```

本轮 184 passed，4 个既有 warning；最终仅修改 name=null 核验提示后，定向执行
`test_source_anchors.py`、`test_fixed_stage_protocol.py`、`test_controller.py`：
58 passed，4 个既有 warning，两组不相加。定向 Ruff 通过。

指定上传的 3.7「设备需求」使用真实本地模型，对原三个窗口执行发现、类型对齐和实体
核验：五条设备全部采信，定位到 `table:6` 第 1–5 行、第 1 列（0 起）。严格负例未通过：
仅保留标题和表头仍提出 12 个误候选，1 拒绝、11 未决、0 采信；后验脚本退出码 1，
未放宽断言。第三正例窗口因字段容量标记未完成，未验证全部属性/关系或全文。

包括最终提示复核共 40 次模型调用、1145.2596 秒调用耗时；原答、位置核对、复核结果
及成本见[表格实体结构定位实测](../../docs/调研/表格实体结构定位修复与实测-20260922.md)。
本轮未重启主服务、未改写原运行，原页面不会自动展示此次隔离结果。

## 图谱分析 V2（2026-09-22）

本次展示实现对应 `design.pen` 的「图谱分析」Frame，当前独立 Harness 运行默认使用
新版。沿用既有 API，不新增模型调用、数据表或后端执行流程。

根据用户后续要求，三个分区已改为 Tab，默认显示「实体图谱与属性」。本次重跑下方
45 项测试、类型检查、定向 ESLint 和浏览器脚本均通过。浏览器额外验证仅一个分区可见、
键盘切换、实体选择/属性/展开层级/筛选/页码保留，以及主体与观察之间自动切换 Tab；
切换不改变 URL 锚点或重新请求图谱。合成场景仍为 13 次 GET、0 次写请求，无脚本或
控制台错误。该次产物独立保存于 `/tmp/source-harness-tabs-browser/`。

头部按用户截图调整后，再次通过 45 项回归、TypeScript、定向 ESLint 和合成浏览器验证。
检查了内容区单一「图谱分析」标题、返回/刷新操作、文档卡根类型与真实运行/范围状态，
以及刷新后实体、属性和 Tab 选择仍保留。返回链接保留 `documentIri` 和当前
`documentRun`；「更新于」为页面最近成功读取结果的时间。此次浏览器记录 14 次 GET、
0 次写请求，无脚本或控制台错误；截图和结果位于 `/tmp/source-harness-header-browser/`，
其中 `header-desktop.png` 为头部截图。未进行真实模型运行或后端重启。
对应 ESLint 还检查了 `analysis-tabs.tsx` 和 `src/app/(dashboard)/analysis/page.tsx`。

在 `frontend/` 执行的检查：

```bash
node --test tests/source-harness.test.mjs tests/target-graph.test.mjs
./node_modules/.bin/tsc --noEmit
npm run lint -- src/lib/source-harness.ts \
  src/components/analysis/source-harness-panel.tsx \
  src/components/analysis/source-harness-shared.tsx \
  src/components/analysis/source-harness-workspace.tsx \
  src/components/analysis/source-harness-observations.tsx \
  src/components/analysis/graph-analysis-panel.tsx
```

结果：**45 passed**，TypeScript、定向 ESLint 和 `git diff --check` 通过。
测试覆盖显式文档根、四跳路径、多父/环路引用、未连接实体、属性搜索保留祖先、
平行关系分别绘制、负向/条件关系不建立无条件根路径、未知 Token 与实测零值、
同一原字段的多主体结果隔离、值级引用和 Unicode 位置。

浏览器脚本为 `frontend/tests/source-harness-browser.mjs`。复用环境中的 Playwright 和
Chrome；通过 `PLAYWRIGHT_MODULE` 指定 `playwright/test.mjs` 的完整路径，再执行
`node tests/source-harness-browser.mjs`。可使用 `DOCUMENT_BROWSER_ORIGIN`、
`DOCUMENT_BROWSER_CHROME` 和 `DOCUMENT_BROWSER_OUTPUT` 覆盖入口、浏览器和产物目录。
默认访问已配置的开发域名，并在浏览器内将该域名解析到本机，避免开发服务器来源限制。

本次已执行浏览器验证，使用**全部 API 均被拦截的合成运行**，没有向后端发送模型任务：

- 默认选中真实文档根；展开四跳、环路引用；树与图切换及键盘选择保持同一实体。
- 属性切换保留完整范围和下限语义；搜索保留祖先；未连接实体可独立查看。
- 阶段成本的未知和零值、观察分页和筛选重置、主体隔离与多卡原因分别显示。
- 从观察定位已折叠的深层实体；实体反向联动观察；来源错位拒绝标注，重读可恢复。
- 1440px 桌面界面和 390px 窄屏弹窗；弹窗可滚动且不越出视口。页面沿用既有应用侧栏。
- 13 次 GET、0 次写请求；无未拦截 API、浏览器脚本错误或控制台错误。
  开发模式 effect 清理产生的已取消 GET 记录为 `ERR_ABORTED`，不作为接口失败。

产物：`/tmp/source-harness-v2-browser/results.json` 以及该目录下的树、图、观察详情截图。
当前前端容器挂载源码并运行 Next 开发服务，已通过现有访问入口验证加载新版；本次没有
重启后端、创建真实分析运行或执行生产构建。这些 UI 验证不代表真实模型质量评测。

## 原文观察与对齐结果（2026-09-22）

在加载当前后端后的新运行检查：

1. 展开原字段，核对完整原值、引用、候选主体的类型与状态均保留。
2. 发现参考卡区分阅读卡与文档根属性指引；对齐类型卡单独显示，不把参考卡当类型确认。
3. 同一字段可同时显示“有已采信属性”和“有菜单未匹配”。分别展开主体组，原因必须
   属于所显示主体、类型卡和属性菜单；切换当前主体筛选后不借用其他主体的采信结果。
4. 检查继承属性的完整谓词 IRI、所属命名空间与定义域。并集显示“或”，交集显示“且”。
5. 一个原范围的多个派生属性分别显示状态；未知类型、未知卡来源保持未定或未记录。
6. 调用失败单独展示；类别、主体、处理结果筛选和展开操作不创建调用或改写当前状态。

工程验证复用下方核心测试命令；前端执行
`node --test tests/source-harness.test.mjs tests/target-graph.test.mjs`，并运行 TypeScript、
定向 ESLint 和 Ruff。本次不重新运行真实模型，不补写已有运行缺失的本体卡来源。

本次实际结果：后端核心与运行集成 172 passed（4 个既有 warning）；上述两个前端
测试文件 39 passed；TypeScript、定向 ESLint、Ruff 和差异检查通过。主服务仍有运行中的
分析，本次未重启后端，不能据此声称新展示已在该运行生效。

部署后发现旧运行的属性行没有 `value_evidence` 时只读响应曾输出 `null`，违反列表契约。
投影现将“未保存值级引用”规范化为空数组，不借用范围更宽的 claim evidence，也不改写
当前工作状态。回归要求旧属性行通过 `HarnessGraph` 校验，并以指定真实运行复核响应。

## 基础验证

1. 在 `backend/` 执行：

   ```bash
   .venv/bin/python -m pytest -p no:cacheprovider -q \
     tests/test_document_harness/ \
     tests/test_extraction/test_document_harness_runtime.py \
     tests/test_extraction/test_document_analysis_run_store.py \
     tests/test_api/test_template_document_runs.py \
     tests/test_api/test_document_analysis_history.py \
     tests/test_api/test_document_analysis_events.py \
     tests/test_api/test_document_analysis_schemas.py
   ```

   2026-09-21 综合执行结果：150 passed，4 个既有 warning。
   随后将断言阶段的 `relations` 也设为必填，对新核心目录及运行集成重新执行：
   109 passed，4 个既有 warning。两组结果有重叠，不相加。
   最后补充跨分片类型联合比较及反例后，同一核心及集成范围为 112 passed，
   定向 Ruff 和差异检查通过。
2. 前端执行新增 harness 定向 Node 测试、TypeScript 和定向 ESLint。
3. 使用新建运行检查 engine=`document-harness-v1`，旧执行协议未进入新请求。
4. 原文提及先可见；本体对齐后关系虚线；独立核对通过才实线。
5. 核对来源、未匹配字段、同名不同角色、缺失值、非法 IRI、空/错位引用反例。
6. 暂停继续不重复已保存请求；刷新及来源跳转没有模型写操作。
7. 固定用户指定上传的新运行记录首提及/首关系候选和阶段成本。
   未完成全文与人工参考核对时不报告最终完整度、准确率或速度提升。

实测记录见 [独立 Harness 重构与实测](../../docs/调研/独立Harness重构与实测-20260921.md)。

## 层级支持与覆盖重排

新代码的新运行默认在发现前选卡。检查发现请求的 `schema_guidance.classes`、
当前窗口的 `guidance_class_iris`，以及独立结果域 `harness:rankings`；未入选类型
仍须出现在后续类型目录检查中，排名分数不得作为原文证据。

2026-09-21 执行核心目录、运行集成及 `tests/test_cmc_product_ontology.py`：
128 passed，定向 Ruff 和差异检查通过。使用真实本地模型对同章节、修订后本体
验证了同预算选卡，DrugProduct/API 的次序由 3/未入选变为 1/2。
详情见 [层级支持与覆盖重排实测](../../docs/调研/本体层级支持与覆盖重排实测-20260921.md)。
这是排序实测，尚未重启发布主服务，亦未完成新的生成式发现或最终事实质量验收。

## 原观察与关系端点解耦

2026-09-22 执行 `tests/test_document_harness/` 和
`tests/test_extraction/test_document_harness_runtime.py`：154 passed，4 个既有 warning。
前端定向 Node 测试 11 passed；TypeScript、定向 ESLint、Ruff 和差异检查通过。

1. 发现输出必须逐实体回答 `field_ids` 与 `source_fields`，无归属字段放
   `unowned_fields`；模型可读输入同时包含任务与输出 Schema。
2. 在类型确认前检查原范围、精确引用和候选主体已保存，没有提前派生边界。
3. 确认主体后按合法谓词拆分范围，两个数值保留同一原字段、完整范围及单位。
4. 关系仍未决或尚未形成根路径时，观察保存和主体属性核验继续执行；后续窗口
   确认主体后可以复用已保存字段。未确认主体不能派生上下限。
5. 同一原字段的不同组件不能同时写入同一个谓词；保留完整原值和冲突原因。

指定报告实测及最终原答复核中，`3.8- 6.6kg` 先保存，计划确认后两个批量边界
采信；本体二跳类型的实际步骤在无根路径时保留 `30~35℃`，错误拆分被拦截。
三跳规则通过合成回归，真实三跳及全文质量未验收。本次未重启主服务。
完整来源、复用与新增调用成本见
[属性观察与端点确认解耦实测](../../docs/调研/属性观察与端点确认解耦实测-20260922.md)。

## 根节点独立文本与本体卡直接发现实证

2026-09-22 对根节点新增原文字段、无标签字段和主体区分完成工程回归：
`tests/test_document_harness/` 及 `tests/test_extraction/test_document_harness_runtime.py`
为 158 passed，4 个既有 warning，定向 Ruff 通过。本轮未部署这些修复。

1. `document_source_fields` 必填；原文没有标签时 `label=null`，不把卡片标签当引文。
2. 根节点发现输入包含其合法属性语义，正文对象的观察不因局部 ID 重名而串入根节点。
3. 使用真实封面和根卡全部 8 个属性直接提出候选，再调用独立证据核验。
   完整封面得到密级 `绝密▲长期` 且采信；只移除该原文单元后返回 `not_found`。
4. 核对两个失败边界：编号值夹带标签仍被采信，负例标题因核验器重判根类型被拒绝。
5. 后验重核不调用模型：在仓库根执行
   `python backend/data/evaluations/direct-root-card-20260922-501505f7/verify.py`。

此处直接抽取为隔离实证，尚未接入在线发现；工程路径仍先保存观察再做属性对齐。
两组各一次发现、一次核验，共 144.76 秒，不报告全文准确率或提速。详见
[本体卡直接发现独立文本属性实测](../../docs/调研/本体卡直接发现独立文本属性实测-20260922.md)。

## 属性值边界与核验职责修订

2026-09-22 执行核心目录及运行集成：171 passed，4 个既有 warning，定向 Ruff 通过。
前端 `node --test tests/source-harness.test.mjs` 为 15 passed；TypeScript 和定向 ESLint 通过。
检查属性 span 的精确 value_evidence、完整 source_value 和原观察均保留；whole 带标签
不自动拒绝。跨字段/伪造/重复位置引用及 N/A 取值反例不能被采信。
实体核验仅接收实体，属性/关系核验沿用 user_selected/model_review/unconfirmed 的
类型依据；类型疑点独立保留，程序不把 rejected 强改成 accepted。

复用上一轮已保存发现，重新执行同封面的两组属性对齐和核验，共 4 次本地调用、
120.16 秒。正例编号通过 span 得到纯编号，完整原文不变；两组标题均采信，对照组
未新增密级候选。此次未重新发现或分析全文，也未部署。
实测报告见 [属性值边界与根类型核验分工](../../docs/调研/属性值边界与根类型核验分工实测-20260922.md)。
在仓库根执行下述后验核对不会触发模型：

```bash
python backend/data/evaluations/root-attribute-boundaries-20260922-b841416a/verify.py
```

## 跨章节共指验证（2026-09-23）

新建分析默认在全文窗口后进入“核对文档内共指”。归并实体详情显示每处提及和成对
判定；属性、关系保留原始提及及条件；同名缺证、不同批次和冲突链不得自动合并。
在 `backend/` 执行：

```bash
.venv/bin/python -m pytest -p no:cacheprovider -q \
  tests/test_document_harness/ tests/test_extraction/test_document_harness_runtime.py --tb=short
```

本轮完整定向套件 **204 passed**；之后新增两端别名来源解码反例单独执行 **1 passed**，
两者互不重复，4 个既有 warning。Ruff 通过。前端两组 Node 测试 **49 passed**，
TypeScript 和定向 ESLint 通过；浏览器合成 API 验证共指展开、原文定位及已有操作，
16 次 GET、0 次写请求，无页面错误，制品位于 `/tmp/document-coreference-browser-20260923/`。

真实本地模型运行入口（output 必须是新目录）：

```bash
python -m app.evaluation.document_coreference_probe \
  --output /app/data/evaluations/document-coreference-NEW \
  --source-run 19e34873-feb3-41c7-af3d-79cc59743e88
```

默认包含固定正反例、完整发现流程和指定真实运行中至多三对已确认的跨章节提及。
`--cases` 可限定新复测范围；期望只在输出后比较，不进入识别输入。来源运行只读取，
实测调用会写既有模型调度/用量记录。结果及范围见
[实测报告](../../docs/调研/跨章节共指最小实现与实测-20260923.md)。

## 本体身份标识与编号对应验证（2026-09-23）

新增 `ProductionArea.areaIdentifier` 与既有 `identityKey` 注解；默认阅读卡、实体核验和
共指输入使用当前冻结本体的身份属性及完整键组。程序不按分隔符拆实体，发现协议不再
包含 `name_parts` 或车间样例。旧运行冻结本体不回填。

在 `backend/` 执行工程验证：

```bash
.venv/bin/python -m pytest -p no:cacheprovider -q \
  tests/test_document_harness/ tests/test_extraction/test_document_harness_runtime.py \
  tests/test_facility_identity_ontology.py
```

真实本地模型入口（必须使用新输出目录；模型调用写既有调度和用量记录）：

```bash
python -m app.evaluation.document_identifier_probe \
  --output /app/data/evaluations/identifier-guidance-NEW \
  --source-ir /controlled/source/ir.json --part all
```

`ablation` 比较 A 简化卡、B 身份标记、C 完整语义和 C_tool 一次定位反馈；`pipeline`
从指定原文段落重新发现、对齐并独立核验；`identity` 的类型是 fixture 前提，单独测试
共指，不得当成端到端类型识别。`self-review --baseline <ablation目录>` 复用 C 的输入和
提议，仅再调用一次模型，作为工具条件的调用次数对照；`real-feedback --baseline
<pipeline目录>` 对真实完整句子比较再次核对和工具反馈。期望仅用于输出后的评分。

验证结果及未达标项见[验证记录](../../docs/调研/本体身份标识引导多实体编号识别验证-20260923.md)。

专用语义绑定工具评估（同一模型的独立有界任务，未接入线上）：

```bash
python -m app.evaluation.identifier_binding_tool_probe \
  --output /app/data/evaluations/identifier-binding-tool-NEW \
  --baseline /app/data/evaluations/identifier-pipeline-COMPLETED \
  --source-ir /controlled/source/ir.json
```

主入口另支持 `--part review`，以有原文定位的候选假设分别验证数量与编号属性归属。

对已完成 A/B/C 输出执行严格再评分（不调用模型；output 仍须新建）：

```bash
python -m app.evaluation.document_identifier_probe \
  --output /app/data/evaluations/identifier-assessment-NEW \
  --source-ir /controlled/source/ir.json --part assess \
  --baseline /app/data/evaluations/identifier-ablation-COMPLETED
```

本次工程验证 228 个不同测试通过；真实模型严格回归 A/B 各 8/15、C 与 C＋定位反馈
各 10/15。目标原句未得到三个区域，整体识别验收未通过。完整成本、失败样例与生效范围
以验证记录为准。
# 2026-09-30 性能与剪枝验证入口

使用新协议创建运行，旧运行不做转换。本轮实际结果及未验收项见
[性能与候选剪枝实施验证](validation-performance.md)，不混用上方历史验证数量。

1. 在 backend/ 用既有 .venv 执行 `python -m pytest -p no:cacheprovider -q tests/test_document_harness/ tests/test_extraction/test_document_harness_runtime.py`。
2. 在 frontend/ 执行 `node --test tests/source-harness.test.mjs tests/target-graph.test.mjs`、`./node_modules/.bin/tsc --noEmit`。
3. 对照方案 A/P/V：成本未知与复用、无连接大实体集、反证保护、组不拆边、缓存只读及暂停批次。
4. PostgreSQL 只使用符合 fixture 要求的专用测试库；未配置报告 skip，不能以 SQLite 替代。
5. 真实评测固定输入/本体/模型/参考/预算的新运行，分别报告候选召回、最终事实和成本。
   没有批准参考仅报告工程和费用，不修改在途运行、不重启服务。
