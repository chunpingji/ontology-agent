# 四阶段重构实施与验证记录

日期：2026-10-02。工程实现、隔离回归及专用 PostgreSQL 验收已完成；真实模型质量验收与部署仍有待完成项。
本记录对应 [四阶段方案](../../docs/图谱分析四阶段顺序重构方案-20261002.md) 和 FS-T002—012。

后续阅读吞吐优化将当前模型消息协议升级为 v8；本文件下列 v7 记录保留为四阶段重构
当时的验证结果。本次窗口、续读及模型抽样结果见
[阅读吞吐优化验证](../../docs/图谱分析阅读吞吐优化验证-20261002.md)。

## 已实施的行为

- 新运行默认 `execution_policy.flow=four_stage`、模型协议 `document-harness-v7`；公开服务域仍为 `document-harness-v2`。旧 flow 的读取、相同 request_key 重用及继续返回 `HARNESS_NEW_RUN_REQUIRED`，不转换或删除旧数据。
- 原文发现保持两个窗口的有界并行。发现收束后先物化带原文证据的 null 谓词属性与显式关系草案，再按窗口执行类型、属性、关系对齐，最后补全跨窗口引用候选。
- 骨架不要求主体 accepted，不运行编号分组、共指、独立事实核对、数值换算或 SHACL。范围组件只选择原串片段，原值和引文保留。
- 语义阶段按 referents、entities、coreference、assertions 推进。分组按真实提及位置检查覆盖，可使用一次持久纠错；完整 JSON 中的局部逻辑错误登记为不可重试 failed，保留骨架并继续无关任务。
- 成员细分保留原提及和成员链接。单一替换保留当前候选 ID 与原字段；多个主体成员没有明确归属时不复制属性。关系对象细分为完整关系组，参与与时间分别核对。已计划的阶段不会漏掉恢复后新增成员的核对和校准任务。
- 顺序模型契约保存完整、无重复的 `ordered_object_ids` 和精确 `order_evidence`；独立时间核对可保持未决。表格、编号排序、斜杠及共同参与不能单独证明先后、紧邻或并行。
- 确定性阶段零模型调用。读取冻结本体已有 `canonicalUnit`，复用精确字面量与单位工具及共享纯 `shacl_core`；检查结果与语义状态分开。上下界从完整原范围核对单位，保留端点角色、factor/offset。无目标、目标覆盖不足和工具失败不能显示为 SHACL 通过。
- 当前状态、调用账本与唯一展示缓存继续复用。工作与成本按 phase 计数；校准按当前行差量计数。GET 不规划、计算或发起模型任务。前端显示四阶段、局部失败、原提及/成员、单位证据、原值/规范值及独立检查结果。

## 实际工程验证

以下命令在仓库现有依赖环境执行，没有使用历史结果替代本轮验证。

### 后端

在 `backend/` 执行：

```bash
.venv/bin/python -m pytest -p no:cacheprovider -q -rs \
  tests/test_document_harness \
  tests/test_extraction/test_document_harness_runtime.py \
  tests/test_extraction/test_literal_normalizer.py \
  tests/test_extraction/test_tool_shacl.py \
  tests/test_extraction/test_ontology_guided_boundaries.py

.venv/bin/python -m pytest -p no:cacheprovider -q \
  tests/test_extraction/test_tool_engine_quantity.py \
  tests/test_extraction/test_tool_metric.py
```

结果分别为 **704 passed、4 skipped** 和 **65 passed**。
首轮的 4 项 skip 均因没有显式配置专用可销毁的 `DOCUMENT_ANALYSIS_TEST_DATABASE_URL`：
`test_accounting_storage.py` 的事务发布、并发控制和缓存读取测试，以及
`test_parallel_runtime.py` 的 PostgreSQL 测试。没有指向共享数据库或清理业务数据，
SQLite 成功不计为 PostgreSQL 锁、fencing 和并发验收通过。

随后使用本机 PostgreSQL 14，在 `/tmp` 新建独立可销毁实例，仅监听临时 Unix socket，
设置专用 `DOCUMENT_ANALYSIS_TEST_DATABASE_URL` 后补跑：

```bash
.venv/bin/python -m pytest -p no:cacheprovider -q -rs \
  tests/test_document_harness/test_accounting_storage.py \
  tests/test_document_harness/test_parallel_runtime.py
```

结果 **31 passed、0 skipped**，其中最初跳过的 4 项实际使用 PostgreSQL。
首次补测发现两个用例仍用旧 stage 名读取成本汇总；断言已同步为 `discovery:discover`，
保留调用数、Token、耗时和重复完成只计费一次的原约束，复验通过。
结果日志：`/tmp/fs_four_stage_postgresql.txt`；临时实例已关闭并清理，未接触应用数据库。
测试结构由当前 ORM metadata 建表并登记当前 head，本轮不声称验证历史迁移链。

定向新增/扩展用例覆盖四阶段顺序、语义前骨架、十三成员与七处提及、分组遗漏隔离、
细分归属与依赖失效、顺序集合、独立时间结论、精确换算、SHACL focus/异常、
暂停继续、付费完整 JSON 的局部失败终态、GET 零写入，以及 phase/check 计数。

后续仅改动原串组件函数签名和相关调用时，对 controller、four_stage_flow、observations、
value_boundaries、evidence_gate 再执行定向回归：**103 passed**。
最后的前端规范范围/成本排序调整后，Node 再验为 **51 passed**，源码 ESLint 与类型检查通过。
受影响后端源码和五个新增测试文件的 Ruff 检查通过。
既有 FastAPI/httpx 与 Pydantic 弃用/字段命名警告仍存在，本次未修改无关模块。

### 前端

在 `frontend/` 执行：

```bash
node --test tests/source-harness.test.mjs tests/document-harness.test.mjs
./node_modules/.bin/tsc --noEmit
npm run lint -- src/lib/api.ts src/lib/source-harness.ts \
  src/components/analysis/source-harness-panel.tsx \
  src/components/analysis/source-harness-workspace.tsx \
  src/components/analysis/source-harness-relation-graph.tsx \
  tests/source-harness.test.mjs tests/source-harness-browser.mjs
```

Node 回归 **51 passed**；类型检查通过；定向 ESLint 为 **0 errors**，
测试渲染辅助函数有 4 个既有 unused-vars 警告。成本按业务阶段排序，规范范围保留开闭性。

合成浏览器验收使用临时复制的前端目录与现有依赖，不读取 `.env`，不修改仓库 Next 配置，
不重启现有应用；测试服务器为 `127.0.0.1:3107`。
临时依赖符号链接触发 Turbopack 对项目根的限制，按 Next.js 16.2.9 官方 CLI 文档改用
`next dev --webpack`。源码测试完成后关闭该临时服务器。

```bash
PLAYWRIGHT_MODULE=/opt/dev/chen/jpi-project/jpi-test/zhjszx-replica/node_modules/playwright/test.mjs \
  DOCUMENT_BROWSER_ORIGIN=http://127.0.0.1:3107 \
  DOCUMENT_BROWSER_OUTPUT=/tmp/source-harness-four-stage-final-browser \
  node tests/source-harness-browser.mjs
```

结果：14 组场景通过，**22 次 GET、0 次写请求、0 次未拦截 API、0 个页面/控制台错误**。
产物在 `/tmp/source-harness-four-stage-final-browser/`，包括 results.json 和截图。
这是合成 API 的界面验收，不等同真实后端/模型集成或质量评测。
最初通过 `127.0.0.1:8081` 的检查因现有反向代理 HMR 握手失败、页面未加载而失败；
该失败没有被计入通过结果。

## 尚未完成

1. **真实模型新运行与质量评测**：当前主机配置 `local_llm_enabled=false`，
   `model_revision` 和 tokenizer 路径为空。没有改变模型开关，没有发起真实识别调用。
   本轮没有计算首条骨架产出时间、真实调用/token、最终未连接数量或 precision/recall。
   这些结果需要在已配置识别模型的环境中，用固定原件、本体、模型、预算及独立专家参考完成。
2. **部署**：没有重启后端、执行部署或宣称线上默认流程已核验。
   已调查旧运行 `9292d88c-9242-438e-a22a-02d856b07877` 没有被补跑、转换或修改。

不修改权威 TTL，不新增数据库表、历史图副本或业务事实提交入口。
