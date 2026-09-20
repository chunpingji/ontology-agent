# 028 验收步骤

状态：**本机工程验收通过，PostgreSQL 专项和真实模型对照未完成**。实际执行记录见文末。需求见 [spec.md](spec.md)，实现任务见 [tasks.md](tasks.md)。

## 1. 准备与范围

- 使用已有 backend/.venv 和锁定依赖；不安装额外模型、不下载权重。
- 工程测试使用原 conftest 隔离，新增制品明确置于 tmp_path；真实模型须具备一致的冻结输入、模型、参考和预算，并记录成本。
- 先完成 B01—B15，再按受影响路径执行检查。下方测试为按边界划分的回归入口；实际运行组合和结果在文末单独记录。
- 两条比较臂使用同一批次引擎，将 max_members 冻结为 1 和 4；可加 2，其他参数一致。
- 工程测试在隔离环境运行，不修改旧运行、不覆盖历史评测目录。部署按用户单独授权执行，实际结果记入 [部署记录](deployment.md)。

## 2. 工程验收矩阵

| 场景 | 必须满足 |
|---|---|
| 三属性共享同记录 | 无额外工具时单成员 6 次、批次 2 次；逐属性证据和值正确 |
| 多关系/属性混合 | 全部关系先经过自己的 validate_graph；批量独立核验后逐项接纳 |
| 8/9 条待查关系 | 分别至少 1/2 个关系工具轮，再加核验回答；额度不足保持未完成 |
| 一成员剩余额度少 | 不借其他成员额度；可形成更小组，其他完成项不重复调用 |
| 模型漏成员/内层非法 | 合法成员保留，错误成员 not_checked；不补造空候选 |
| 整批无待核验声明 | 仅发现请求；本地完成空核验，不产生核验模型请求、预留或 usage |
| 空目标与非空目标混合 | 只有非空成员参与核验；空成员保留最近实际参与的版本及真实计费 |
| 无候选/未决观察/无效候选 | 分别保留原完成语义；无效实体的依赖关系同步拒绝，不把空结果当作成功事实 |
| 空目标补证或旧付费核验恢复 | 新恢复使用有限重提；旧响应先配对工具、未知请求阻断；零剩余额度可本地收尾 |
| 已预留请求发生传输失败 | 不拆组、不提交成员 outcome；保留已确认结果、pending 及调用计数；冷继续不重发 |
| 重复/越界成员 ID | 明确协议错误，不能任意覆盖或授予权限 |
| 同证据 ID 不同 span | 越界引文拒绝；事实/辅助角色不串用 |
| 不同 range 引用白名单 | A 已注册候选不自动成为 B 的合法对象 |
| 独立反证/条件/否定 | 每成员自己的反证参与核验，限定/选择组保持原语义 |
| 同 mention 两种谓词 | 一个 canonical 节点、两条合格关系、两套成员证明，无版本冲突 |
| 同名异对象/身份绑定冲突 | 不因同批次合并；冲突按原身份规则阻断 |
| record 表示实体 | 沿用原身份，不承诺跨成员自动合并 |
| 请求真实计数 | 4 成员共享 2 次请求：全局=2，每成员参与=2，tokens 不乘 4 |
| 工具预留后无结果 | 已消耗工具额度不退回，新 unit/恢复不会清零 |
| 冷继续 | 组包后、响应后、部分工具后、核验后、图提交前分别断开；不重做已确认模型请求 |
| 组切换恢复 | 连续 verification 组可合法换输入；pending 时换组拒绝；组序号绑定响应 |
| revision>1 补证恢复 | 成员协议视图读取正确证据版本、generation、recovery_used |
| 检索刷新时同轮仍有 pending 工具 | 同组授权刷新合法，所有 call/output 保留；配对完成前不发模型请求 |
| finalizer 才发现补证缺口 | 协调器恢复决策仍可启动；已应用旧 outcome 不阻断新版本结果 |
| 重提/检索响应保存后暂停 | 冷继续消费最新已付费响应；旧阶段结果不能遮蔽新候选，不重复计费 |
| 原子提交失败 | 后一成员证明写入失败时，本次图/覆盖/标记无半提交 |
| 后续技术重试 | search.observe 新 attempt 不改写旧观察，旧成员额度/证据继续有效 |
| 两次真实重试得到相同失败内容 | 结果版本和观察 attempt 仍不同，同次提交重放才幂等 |
| 成员结果换 owner 或版本 | loader 拒绝，不让 leader 或 sibling 的结果授权本成员 |
| 无兼容成员/容量退回 | 单成员走同路径，未选任务队列和公平计数保持正确 |
| 旧冻结运行 | 无 batching 策略不升级；原图谱投影、读/继续契约回归通过 |

完整关系组不允许丢掉失败对象后缩成“成功”子组；共享节点不允许借用其他成员 supported verdict。

## 3. 实施后的定向命令

工作目录 `backend/`。下列为可复现的分组入口，不代表每一条组合均已原样执行。

```bash
.venv/bin/python -m pytest -p no:cacheprovider -q tests/test_extraction/test_semantic_scheduler.py tests/test_extraction/test_tool_engine_contracts.py tests/test_extraction/test_tool_engine_context.py tests/test_extraction/test_tool_engine_freeze.py tests/test_extraction/test_tool_engine_verification.py
```

```bash
.venv/bin/python -m pytest -p no:cacheprovider -q tests/test_extraction/test_tool_runtime.py tests/test_extraction/test_tool_runtime_recall.py tests/test_extraction/test_tool_engine_adapter.py tests/test_extraction/test_tool_engine_turn_plan.py tests/test_extraction/test_relation_validation_harness.py
```

```bash
.venv/bin/python -m pytest -p no:cacheprovider -q tests/test_extraction/test_reference_resolution_finalize.py tests/test_extraction/test_reference_resolution_execution.py tests/test_extraction/test_tool_engine_execution.py tests/test_extraction/test_recognition_coordinator.py tests/test_extraction/test_group_scope_scheduling.py
```

```bash
.venv/bin/python -m pytest -p no:cacheprovider -q tests/test_extraction/test_document_analysis_budget_execution.py tests/test_extraction/test_document_analysis_continuous_budget.py tests/test_extraction/test_tool_engine_recovery.py tests/test_extraction/test_tool_engine_resume.py tests/test_extraction/test_document_current_state.py tests/test_extraction/test_current_state_transactions.py tests/test_extraction/test_document_analysis_execution_recovery.py tests/test_extraction/test_ontology_guided_boundaries.py
```

新增批量测试：

```bash
.venv/bin/python -m pytest -p no:cacheprovider -q tests/test_extraction/test_recognition_batch.py tests/test_extraction/test_batch_model_adapter.py tests/test_extraction/test_batch_executor.py tests/test_extraction/test_batch_current_state.py tests/test_extraction/test_batch_expert_repair.py tests/test_extraction/test_tool_engine_cli.py tests/test_extraction/test_expert_review_core.py
```

配置/API/前端按实际修改再做定向检查。Ruff 仅覆盖本次修改文件，不顺手格式化全库。

专用 PostgreSQL 测试沿用现有 test_tool_engine_postgresql.py 与 fixture 约束。DOCUMENT_ANALYSIS_TEST_DATABASE_URL 必须是 fixture 允许清理的可销毁库；未配置的 skip 如实记录，不能用 SQLite 结论代替并发/事务验收。

## 4. 真实模型对照

已扩展现有 `app.evaluation.ontology_tool_engine` 的 manifest 接口（B17）。复制冻结清单到新的评测目录并设置 `options.batching={"version":"predicate-batch-v1","max_members":1}`，另建 2/4 两臂。新在线身份模式对照同时固定 `reference_resolution_version: 1`；该字段缺失时保留旧身份模式。

```bash
.venv/bin/python -m app.evaluation.ontology_tool_engine run --manifest /path/to/new/manifest.json --output /path/to/new/result
```

1. 固定原文及 IR、本体、模型名称/版本、可选工具、检索策略、参考、总体预算、每成员预算、输入/输出容量。
2. 用同一引擎分别运行 max_members=1/2/4，每臂使用新的输出目录和运行身份；参考答案只传评分端。
3. 固定成员集合微基准记录实际请求数、共享原文长度、tokens、工具/模型/总耗时和失败原因。
4. 完整端到端运行记录图谱 P/R/F1、条件/否定/选择组、身份重复、未完成率，检查调度和后继展开变化。
5. 对存在随机波动的真实模型结果报告多次运行的中位数/范围，不以单例最优值宣称整体提速。
6. 以物理 calls:requests/model_turn usage 汇总成本，不用成员 outcome.model_calls 求和；提供同任务范围与同总预算两种比较视角，避免较早停止造成假提速。

报告至少包含：

| 臂 | 实际请求 | 输入/输出 tokens | 总耗时 | 实体 P/R/F1 | 属性 P/R/F1 | 关系 P/R/F1 | 未完成率 | 实际批次大小 |
|---|---|---|---|---|---|---|---|---|
| 单成员 | 待测 | 待测 | 待测 | 待测 | 待测 | 待测 | 待测 | 1 |
| 最多 2 成员 | 待测 | 待测 | 待测 | 待测 | 待测 | 待测 | 待测 | 待测 |
| 最多 4 成员 | 待测 | 待测 | 待测 | 待测 | 待测 | 待测 | 待测 | 待测 |

不为本规划预设已经通过的提速或质量阈值。质量变差、未完成率升高或 token 膨胀时分析原因后调整装箱参数，不先降低核验要求。

## 5. 规划阶段验证记录

2026-09-18 实际完成：5 份文档、26 个本地链接、14 项需求与 18 项任务映射、引用的既有测试文件、代码围栏及尾部空白检查全部通过；相关路径 `git diff --check` 通过。只读设计复核已补齐阶段组切换、成员证据版本、工具预留额度、finalizer 后补证、结果 owner/版本和同结果重试标识。

未运行应用测试、真实模型、数据库迁移、服务重启或部署；表内模型效果均为待测。已有用户未提交改动保留，既有文档仅补本特性入口链接。

## 6. 实施阶段验收记录（2026-09-18）

已在 `021-ontology-guided-doc-graph` 实施；没有迁移、服务重启或部署。测试使用受控模型及隔离存储，不能据此宣称真实模型已提速或质量不退化。

- 最终七文件合并运行：**130 passed**（39.49 秒），命令见上方新增批量测试组。涵盖五个新增测试文件、评测 CLI 和专家修复核心；验证单/多成员同路径、8/9 关系工具轮、成员额度、物理计费、暂停/补证/重提、相同失败新版本、实体复用、公平调度及事务回滚。
- 原执行器/冷继续/协调器回归：**53 passed**。
- 原身份复用、scope 调度、slot 完成、公有投影及文档分析 API：**102 passed**。API 最初受沙箱 socketpair 线程通信限制挂起；允许本机通信后完整复跑通过，无需修改业务代码。
- 专家修复 scope 和新批量修复回归：**20 passed**，冷继续后的修复新增两次物理请求，API 操作结果同步记两次。
- 原执行预算开关测试：**2 passed**。
- 连续执行预算、依赖边界、配置和 PostgreSQL 工具引擎入口组合：**39 passed、9 skipped**。九项跳过均因未配置 `DOCUMENT_ANALYSIS_TEST_DATABASE_URL`，未用 SQLite 代替 PostgreSQL 锁/并发验收。
- 微基准的受控模型结果：同一记录三属性，max_members=1/2/4 分别为 **6/4/2 次物理请求**；每个属性均参与发现和独立核验两次。输入/输出容量充足；容量拆组另有反例覆盖。
- 前端定向 ESLint、TypeScript `tsc --noEmit` 已通过；`node --test tests/document-analysis-runs.test.mjs tests/document-graph.test.mjs` 两个测试文件通过。后端全部 25 个改动 Python 文件定向 Ruff、五份文档的 24 个本地链接/代码围栏及 `git diff --check` 已通过。
- 真实模型预检：当前配置为 `qwen2.5:14b` 且 revision 为空；已有冻结清单要求 `Qwen3.6-35B-A3B` 及固定 revision，清单内 `/app/models/gliner2.5-multi-v1-20260916` 不存在。因此 B18 未执行，未创建伪造质量或耗时结果。

按用户后续要求，新在线运行默认启用 `{"version":"predicate-batch-v1","max_members":4}`，无需额外配置。需要覆盖容量时，在 `ontology_extraction_options` 中设置 `batching`，支持 `max_members=1/2/4`；旧运行依冻结策略继续，不能用新配置升级旧状态。离线对照仍以各 manifest 的显式配置为准。

后续用户授权的部署与重启已执行，运行环境核验见 [部署记录](deployment.md)。

## 7. 批处理失败修复验证（2026-09-18）

运行 `a337a5f1-d9be-4ed5-8ec4-191aedd236b6` 暴露空目标核验 Schema 和请求异常处理问题。新增 14 项回归覆盖空目标、发现/核验传输失败、worker 返回错误、已完成图谱保留、冷继续不重发，以及运行/图谱 GET 的明确诊断。修复前 11 项故障回归均失败，包含线上同一 `pending batch cannot publish results` 异常；修复后通过。

本轮实际执行以下组合，**214 passed**（98.11 秒），无跳过：

```bash
.venv/bin/python -m pytest -p no:cacheprovider -q --tb=short tests/test_extraction/test_batch_model_adapter.py tests/test_extraction/test_batch_executor.py tests/test_extraction/test_batch_current_state.py tests/test_extraction/test_tool_engine_contracts.py tests/test_extraction/test_tool_engine_resume.py tests/test_extraction/test_tool_engine_configuration.py tests/test_extraction/test_document_analysis_execution_recovery.py tests/test_extraction/test_document_analysis_continuous_budget.py tests/test_extraction/test_document_analysis_budget_execution.py tests/test_extraction/test_ontology_guided_boundaries.py tests/test_api/test_document_analysis.py
```

八个修改的 Python 文件 Ruff 通过。上述工程测试使用隔离 SQLite 和受控模型，不替代 PostgreSQL 并发验收或完整文档质量评测；模型端合成烟测与实际部署结果单列于部署记录。

## 8. 工具调用优化验证（2026-09-18）

根据证据 ID 误用、单次读取超限、空消歧上下文和空目标核验反复调用工具的问题，调整模型可见参数与批次请求：

- `inspect_evidence` / `propose_mentions` 的参数列出授权 `evidence_id`、去重要求和实际 `maxItems`（默认 16）；锚点工具列出授权 ID，本体卡引用列出当前卡片。共享 `unit_id` 只用于定位输入中的文本，不可作为工具证据 ID。批次 Schema 的枚举并集不扩大成员权限。
- 单次证据数量超限返回 `evidence_unit_limit_exceeded`，提示按 `maxItems` 拆分；真实工具调用额度耗尽仍返回原错误。
- 仅在锚点工具执行边界将 `context_text=""` 或模型输出的字面字符串 `"null"` 视为未提供上下文；保留原始调用，仍要求唯一、精确、非空且已授权的引文。其他非空错误上下文、歧义、越权和无原文引文继续拒绝；不改写已冻结的声明或哈希。
- 当时全组核验目标为空时仍保留一次真实的最终回答请求，不提供工具；该策略不满足原单任务跳过空核验的约束，已由第 9 节修复替代。候选校验失败诊断及已有请求成本仍保留。

以下 12 个测试文件最终合并运行 **287 passed**（63.25 秒），无跳过；五个本次修改的 Python 文件 Ruff 通过：

```bash
.venv/bin/python -m pytest -p no:cacheprovider -q tests/test_extraction/test_batch_model_adapter.py tests/test_extraction/test_tool_runtime.py tests/test_extraction/test_tool_runtime_recall.py tests/test_extraction/test_tool_engine_tool_contracts.py tests/test_extraction/test_tool_engine_adapter.py tests/test_extraction/test_tool_engine_resume.py tests/test_extraction/test_recognition_batch.py tests/test_extraction/test_batch_executor.py tests/test_extraction/test_batch_current_state.py tests/test_extraction/test_batch_expert_repair.py tests/test_extraction/test_ontology_guided_boundaries.py tests/test_api/test_document_analysis.py
```

工程测试使用受控模型及隔离存储；不据此推断真实文档识别准确率或整体提速比例。模型烟测和重启核验见 [部署记录](deployment.md)。

## 9. 空目标与原单任务约束修复（2026-09-18）

批次只合并物理请求，不放宽证据、独立核验、预算或完成判定。第 8 节“空目标仍调用一次模型”的处理已被以下实现替代：

- 无候选、仅观察及全部候选被原文校验淘汰时，复用确定性空核验结果和原 finalizer。没有可核验声明的成员不进入模型核验请求，混合批次也逐成员排除。
- `record_no_claims` 保留原完成语义；unknown/ambiguous/unbound 观察及无效声明保持未完成，`source_excerpt_mismatch`、`entity_dependency_invalid` 等诊断不删除。非空关系继续要求原工具检查及独立核验。
- 本地结果沿用最后实际请求的成员版本，不新增预留、model_turn 或 usage；即使余额为零仍能收尾。旧核验响应和工具配对保留，未知请求阻断；旧混合批次完成已保存的关系检查后，仅让非空成员继续调用。
- 全部候选失效后的恢复采用原有限重提，单任务补证入口遵守同一约束。新提案成功、清空候选、返回相同无效内容分别处理，不重置证据权限、generation 或恢复额度。
- 重提/补证响应已保存但旧阶段结果仍存在时，冷继续按实际 attempt 消费新响应和工具；避免旧失败结果遮蔽新候选或重复计费。

新增故障测试先复现多余空核验、混合成员参与、冷继续漏读付费重提响应及单任务补证空核验；修复后以下四文件 **124 passed**（65.46 秒）：

```bash
.venv/bin/python -m pytest -p no:cacheprovider -q --tb=short tests/test_extraction/test_batch_model_adapter.py tests/test_extraction/test_batch_executor.py tests/test_extraction/test_batch_current_state.py tests/test_extraction/test_tool_engine_recovery.py
```

API 的核验传输失败用例改为提供真实非空候选，保留原失败状态、未知请求、覆盖与 GET 不重发断言；空候选现在直接完成，不能再用来触发核验网络故障。

其余受影响路径最终合并运行 **324 passed、9 skipped**（74.34 秒）：

```bash
.venv/bin/python -m pytest -p no:cacheprovider -q --tb=short tests/test_extraction/test_recognition_batch.py tests/test_extraction/test_batch_expert_repair.py tests/test_extraction/test_tool_engine_adapter.py tests/test_extraction/test_tool_engine_turn_plan.py tests/test_extraction/test_tool_engine_freeze.py tests/test_extraction/test_tool_engine_verification.py tests/test_extraction/test_tool_engine_resume.py tests/test_extraction/test_tool_engine_contracts.py tests/test_extraction/test_tool_engine_configuration.py tests/test_extraction/test_relation_validation_harness.py tests/test_extraction/test_document_analysis_execution_recovery.py tests/test_extraction/test_document_analysis_model_call_recovery.py tests/test_extraction/test_document_analysis_continuous_budget.py tests/test_extraction/test_document_analysis_budget_execution.py tests/test_extraction/test_ontology_guided_boundaries.py tests/test_extraction/test_tool_engine_postgresql.py tests/test_api/test_document_analysis.py
```

两组共 **448 passed、9 skipped**；九项 PostgreSQL 用例因未配置专用 `DOCUMENT_ANALYSIS_TEST_DATABASE_URL` 跳过。六个修改的 Python 文件 Ruff、六份特性文档的本地链接及 `git diff --check` 通过。使用隔离 SQLite、真实当前状态存储和受控模型，未进行完整文档真实模型质量/速度对照，不以工程通过替代该结论。

## 10. 回答结构与原文覆盖修复（2026-09-19）

运行 `99033b53-e9ea-425f-b348-132915d4dc0f` 的生产计划任务中，模型分析识别了时间、批次数和批量上下限，但保存的最终回答为 `entities=[]`、`properties=[]`，关系对象 ID 又与关系自身 ID 相同。本地严格解析可复现 `endpoint_is_not_an_entity`；传输层直接保存模型端返回的 `output`，没有从实体数组删除这些数据。

两个空数组含义不同：该成员只发现“文档→生产计划”关系，收窄 Schema 本来就要求 `properties` 为空；新计划实体应在 `entities` 中定义，并通过 `record_components` 的 `role/quote` 数组保留字段和值的原文，实体入图后才抽取其属性。模型分析里的字段字典不符合此结构。批处理原先删除了单任务提示中的可读回答 Schema，而本次冻结的 `strict_answers=false`；该组合缺少提示正文中的完整结构约束。不能据此断言模型服务内部具体在哪个生成步骤丢失信息，也不能把 Thinking 作为事实直接入图。

本次修复：

- 提示正文包含与实际 `text.format.schema` 一致的批次 Schema，说明实体/关系 ID、记录组成、后继属性任务和桥接引用的区别。
- 单成员和拆分后的成员均可在原额度内修正已确认的错误回答，反馈含具体路径、原因和本成员最终回答；不携带 Thinking，不借用兄弟成员证据。反馈随当前阶段输入保存，冷继续不重放已付费请求。
- 缺失实体、误填桥接 ID 在有后续核验额度时先修正发现结果。记录对象 `referent` 原文覆盖不足在核验阶段反馈；继续保留最终入图的完整覆盖门禁。
- 保留 `0.30~5.00kg` 完整区间，分别按已声明属性策略提取下限和上限，不将区间直接当作一个标量。

新增 `test_batch_answer_corrections.py` 的 **12 项**回归。以下两组实际运行合计 **277 passed**，无跳过；五个受影响 Python 文件 Ruff 通过：

```bash
.venv/bin/python -m pytest -p no:cacheprovider -q tests/test_extraction/test_batch_answer_corrections.py tests/test_extraction/test_batch_executor.py tests/test_extraction/test_batch_current_state.py tests/test_extraction/test_batch_expert_repair.py tests/test_extraction/test_tool_engine_adapter.py tests/test_extraction/test_tool_engine_quantity.py tests/test_extraction/test_ontology_guided_boundaries.py
.venv/bin/python -m pytest -p no:cacheprovider -q tests/test_extraction/test_tool_engine_verification.py tests/test_extraction/test_reference_resolution_finalize.py tests/test_extraction/test_relation_validation_harness.py tests/test_extraction/test_batch_model_adapter.py
```

上述为隔离数据库、受控模型和真实解析/核验代码的工程验证；本次未重启服务、未重跑原运行或进行新的真实模型质量评测。原运行最后核对为用满 32 次调用后暂停，代码修改不改写其历史结果。
