# 029 验收步骤

状态：**工程验证已执行；真实模型对照与正式质量评分状态见实施记录；已重新部署供手工测试**。

部署后的输入去重和 Harness 反馈修正另见第 6 节；该轮代码验证完成，尚未重启应用。

需求见 [spec.md](spec.md)，设计见 [plan.md](plan.md)，开发任务见 [tasks.md](tasks.md)。实际结果见 [implementation.md](implementation.md)。下方矩阵保留验收口径，工程用例和真实模型质量结论分别记录。

## 1. 验收准备

1. 固定当前代码基线及待验证代码身份，记录未提交的 028 和相关修复，避免把基线差异归因于本次重构。
2. 复用已有后端环境及测试 fixture；文件制品放入临时目录，不启动业务后端来代替隔离测试。
3. 准备小型受控本体和原文记录 fixture，覆盖多实体、多类型共用谓词、表格字段、代词、否定/条件及记录组成对象。
4. PostgreSQL 测试仅连接 fixture 要求的专用可销毁测试库；未配置导致的 skip 必须报告，不能以 SQLite 结果代替事务竞争验收。
5. 工程 fixture 的模型响应只验证流程和边界，不作为真实模型准确率证据。

## 2. 核心验收矩阵

| 编号 | 场景 | 必须观察到的结果 |
|---|---|---|
| A01 | 一记录含两个不同实体，各有两个属性 | 批量发现，逐项正确绑定；无入边也可接纳实体和属性。 |
| A02 | 两种实体共用属性 IRI，但 datatype/单位不同 | 每项使用其主体类卡，不能取另一个类的定义。 |
| A03 | E1 类型不成立，E2 成立 | E1 依赖属性不接纳；E2 及独立属性保留。 |
| A04 | 实体成立，一个属性缺证 | 保留实体和其他正确属性，缺证项未决。 |
| A05 | 普通实体试用文档根桥接权限 | 冻结/证明门拒绝，不因任务含文档根上下文而放行。 |
| A06 | 表头、摘要或相邻记录出现其他对象 | 可辅助定位，未经事实授权不能产生新实体/属性。 |
| A07 | 合并单元格出现在两条逻辑行 | 精确 mention 不重复；两行各自属性归属仍独立判断。 |
| A08 | 由字段组成的无名称 record 实体 | 完整组成和主体角色通过后成立；值不冒充名称，缺可选属性不自动拒绝。 |
| A09 | record 组成缺一部分引文，或跨记录拼接 | 组成核验不通过，不能仅凭 supported 字段接纳。 |
| A10 | 同名不同批次/工艺阶段/粒度 | 保持不同候选或未决，不直接合并。 |
| A11 | 补发现重提已接纳的完全相同 record | 反馈重复提案并要求使用登记引用，不能因 generation 变化增加节点。 |
| A12 | 前一批只有主体，后一批出现对象与谓词原文 | 重开受影响关系机会，保留原 lineage 成本；其他关系不全量重跑。 |
| A13 | 已证明“其”指向前文实体，再核验“其含 B” | 使用明确授权的既有绑定依赖，当前角色/谓词仍独立核验；失效绑定拒绝。 |
| A14 | 仅有同文档共现或本体允许连接 | 不生成关系；独立实体和属性仍可显示。 |
| A15 | 关系线索指向首次遗漏的对象 | 有界定向补发现，登记后再核验关系；相同失败线索不无限重试。 |
| A16 | 否定、条件、备选组或恰选一个 | 原限定保留；多对象组不拆边，组内属性不变成无条件事实。 |
| A17 | 重复候选通知、同批次重复确认 | 对应任务不重复入队、不重复收费或新增节点。 |
| A18 | 类卡分组、无候选、漏答、技术错误 | 每个记录/卡组覆盖准确；未检查组不算完成，错误不等于空成功。 |
| A19 | 发现后完整核验超限、输出截断或评测总预算耗尽 | 保留未完成与已用成本，不裁剪目标、不发空核验、不换卡组领取新额度；记录请求也受评测总额度限制。 |
| A20 | 发现响应已保存但候选尚未发布时冷继续 | 复用保存响应；候选和关系待办只发布一次。 |
| A21 | 只有请求预留，没有确认响应 | 保留未知结局、成本和未完成，不变成“没有实体”。 |
| A22 | 候选/证明/入队提交中注入错误 | 整次事务回滚，无半写；重试使用保存结果。 |
| A23 | 实体/依赖版本改变，旧关系任务稍后返回 | 旧结果不能发布；只为受影响依赖安排新工作。 |
| A24 | 旧运行继续、新运行启动、GET/切换投影 | 旧策略不变，新策略实际启用；读操作不调用模型，孤立节点可见。 |
| A25 | 一类卡组处理后预算耗尽，或关系队列仍有工作 | 未完成范围明确；扫描末尾不冒充整轮语义达标。 |

## 3. 工程测试落点与复核命令

新增测试均已落地。模型响应受控的测试验证授权、状态与业务行为，不代表真实模型 precision/recall。

| 实际文件（位于 `backend/tests/test_extraction/`） | 重点覆盖 |
|---|---|
| `test_record_discovery_contracts.py` | 无伪造主体、继承与焦点范围、同谓词不同类约束、分页完整歧义组。 |
| `test_record_model_adapter.py` | 实体/属性完整核验、记录组成和重复提案、空目标、格式修正、容量、有限重提和保存响应继续。 |
| `test_record_executor.py` | 无入边候选、局部失败、原文/根权限、后到端点、定向补发现、未知请求、分页覆盖及成本。 |
| `test_record_reference_dependencies.py`、`test_record_reference_pipeline.py` | 跨记录指称的原证明重建、当前授权、版本/范围/身份反例和真实关系阶段消费。 |
| `test_record_relation_adapter.py` | 单任务/批次仅引用登记端点，显式属性修复保持原属性契约。 |
| `test_record_discovery_current_state.py` | 当前工作原子性、原任务/结果归属、冷继续、反馈重开、严格审核边界、SQLite 与专用 PostgreSQL 竞争。 |
| `test_record_property_repair.py` | 记录源属性审核、真实主体修复、正确值发布及拒绝版本保留。 |
| `test_record_focus_scope.py` | 焦点路径重复类型仍保留后续合法关系，旧管线继续按原 hop 过滤。 |
| `test_record_page_semantics.py` | 后页预算不足保留前页支持统计，分页冷继续不重复调用、不丢端点。 |

API 新增 [test_record_property_reviews.py](../../backend/tests/test_api/test_record_property_reviews.py)，前端新增 [document-harness.test.mjs](../../frontend/tests/document-harness.test.mjs)。冻结、上下文、独立核验、原指称、旧批次、图投影和共享边界的现有测试已随定向整合回归执行，完整 40 模块列表保存在 [verification-command.json](verification-command.json)。

在 `backend/` 定向复核记录发现（这是一组复核入口，最终整合运行结果另见实施记录）：

```bash
.venv/bin/python -m pytest -p no:cacheprovider -q \
  tests/test_extraction/test_record_discovery_contracts.py \
  tests/test_extraction/test_record_model_adapter.py \
  tests/test_extraction/test_record_executor.py \
  tests/test_extraction/test_record_reference_dependencies.py \
  tests/test_extraction/test_record_reference_pipeline.py \
  tests/test_extraction/test_record_relation_adapter.py \
  tests/test_extraction/test_record_discovery_current_state.py \
  tests/test_extraction/test_record_property_repair.py \
  tests/test_extraction/test_record_focus_scope.py \
  tests/test_extraction/test_record_page_semantics.py \
  tests/test_api/test_record_property_reviews.py
```

在仓库根目录，已执行的专用 PostgreSQL 入口：

```bash
backend/.venv/bin/python backend/scripts/test_document_analysis_postgresql.py \
  tests/test_extraction/test_record_discovery_current_state.py \
  tests/test_extraction/test_current_state_transactions.py \
  -k 'postgresql or record_review'
```

本次结果 **22 passed、23 deselected，无 skip**，其中 5 项实际使用 PostgreSQL，17 项为 SQLite 严格审核边界。脚本只用于其确认的可销毁测试数据库，不能指向业务库。常规整合运行未配置该入口时的 4 个 skip，不计为 PostgreSQL 已通过的依据。

前端 `frontend/` 的已执行 Harness SSR 命令为 `node tests/document-harness.test.mjs`，实际 4 个子测试通过。既有 Node 测试、TypeScript 和定向 ESLint 也已通过；早期精确命令组合未留存，详见实施记录，未声称生产构建或浏览器真实后端验收。

## 4. 真实模型对照

### 4.1 固定条件

基线策略为 028 同主体多谓词批处理；实验为本方案记录优先管线。已执行的有限对照在同一代码快照中选择两条策略，具体版本限制见实施记录。正式验收固定：

- 相同原始文档及解析制品、本体快照、文档类型、分析范围和深度。
- 相同模型、推理配置、工具能力、精排策略和运行硬件。
- 相同总模型预算、超时及结束判据；每任务预算语义差异另行报告，不只比较任务数量。
- 同一份经独立核验的参考，参考不进入识别输入。覆盖跨记录、多实体、同名冲突和缺关系负例。

这里的总预算由评测入口执行。在线仍沿用一次显式启动/继续的连续窗口及累计请求账本；不能把继续新开窗口误写成返还已使用的任务额度，也不因本次重构新增全生命周期预算产品能力。

为每次运行创建新目录和运行身份，保留历史评测原文、参考和输出。使用 [活动评测入口](../../backend/app/evaluation/README.md) 的共享核心；实施后的策略参数以实际代码和帮助输出为准，本方案不虚构尚未实现的 CLI 开关。

### 4.2 分别报告

| 指标 | 说明 |
|---|---|
| 实体 precision/recall/F1 | 物理提及、正确类型和身份误合并/重复情况。 |
| 属性 precision/recall/F1 | 完整主体、谓词、值、单位、极性/条件与原文证明。 |
| 关系 precision/recall/F1 | 完整主客体、谓词、方向、组语义和范围。 |
| 完整路径 | 正确实体端点及每一跳原文证明，而非单纯图连通。 |
| 覆盖与未完成 | 记录/类卡、关系候选范围，未尝试、技术失败、未决及未评分项。 |
| 成本 | 实际请求数、input/output tokens、模型/工具/精排/总耗时。 |
| 首次可用结果时间 | 首个已核验实体、属性、关系；与整轮完成耗时分开。 |

不得以减少类型范围、较早耗尽队列、降低覆盖或漏记错误作为提效。若没有合格参考，只报告未具备正式质量评分条件及真实成本，不用模型自评替代。

用户尚未指定提速比例或质量容差：如结果存在准确率与成本取舍，提交实际差异供判断，不自行宣布达标。

## 5. 实施后的上线核对

1. 必要工程及真实模型验证结果有记录，失败和跳过项未隐藏。
2. 新运行冻结策略为 `record-entity-first-v1`，实际首轮出现记录发现任务；当前代码默认值已由新运行冻结与实际执行测试验证；部署后仍须核对运行实例。
3. 新图能出现已核验孤立实体及属性；关系仍需原文证据，不能自动连到根。
4. 暂停继续复用原运行/当前状态；旧运行继续其冻结管线，已付费结果不热迁移。
5. 页面查询不启动任务。记录已部署版本、实际启用策略和真实结果；工程实施后已按追加授权重新部署，实际核验见 [deployment.md](deployment.md)，历史运行保持原策略。

## 6. 手工反馈修正的实际验证

2026-09-19，本轮修正后的后端命令（工作目录 `backend/`）：

```bash
.venv/bin/python -m pytest -p no:cacheprovider -q \
  tests/test_extraction/test_model_context_projection.py \
  tests/test_extraction/test_tool_engine_adapter.py \
  tests/test_extraction/test_batch_model_adapter.py \
  tests/test_extraction/test_record_model_adapter.py \
  tests/test_extraction/test_record_relation_adapter.py \
  tests/test_extraction/test_tool_engine_context.py \
  tests/test_extraction/test_recognition_batch.py \
  tests/test_extraction/test_batch_answer_corrections.py \
  tests/test_extraction/test_record_reference_dependencies.py \
  tests/test_extraction/test_ontology_guided_boundaries.py

.venv/bin/python -m pytest -p no:cacheprovider -q \
  tests/test_extraction/test_tool_engine_recovery.py
```

分别 **218 passed（25.66 秒）**、**13 passed（2.29 秒）**，无 skip；两组模块不重叠。均有 4 条既有库用法警告。覆盖章节共享、权限/修订负例、真实适配器请求、完整核验、旧指令与保存输入继续、补证后输入格式保持及共享核心边界。

前端（工作目录 `frontend/`）实际执行：

```bash
node tests/document-harness.test.mjs
./node_modules/.bin/tsc --noEmit
npm run lint -- src/components/analysis/document-harness.tsx
```

**7 个 SSR 测试通过，类型检查和定向 ESLint 通过。** 受影响的 8 个 Python 文件以 `.venv/bin/ruff check --no-cache` 检查通过；`git diff --check` 及新增文档链接检查通过。本轮没有数据库结构或事务行为变更，未运行 PostgreSQL 验收、生产构建、真实模型或真实后端浏览器测试。

输入体积测量和三项后续评估见 [context-input-review.md](context-input-review.md)。本轮未重启服务，不把文件修改当作后端已加载新代码。
