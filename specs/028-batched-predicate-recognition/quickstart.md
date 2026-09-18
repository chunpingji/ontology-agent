# 028 验收步骤

状态：**设计验收方案，尚未执行工程或真实模型测试**。本次文档检查结果见文末。需求见 [spec.md](spec.md)，实现任务见 [tasks.md](tasks.md)。

## 1. 准备与范围

- 使用已有 backend/.venv 和锁定依赖；不安装额外模型、不下载权重。
- 工程测试使用原 conftest 隔离，新增制品明确置于 tmp_path；运行真实模型另外授权并记录成本。
- 先完成 B01—B15，再按受影响路径执行检查。下方现有测试路径已在规划时定位；新增测试文件须由对应任务实现后才可运行。
- 两条比较臂使用同一批次引擎，将 max_members 冻结为 1 和 4；可加 2，其他参数一致。
- 不重启服务、不修改旧运行、不覆盖历史评测目录。生产部署不属于本次规划或测试授权。

## 2. 工程验收矩阵

| 场景 | 必须满足 |
|---|---|
| 三属性共享同记录 | 无额外工具时单成员 6 次、批次 2 次；逐属性证据和值正确 |
| 多关系/属性混合 | 全部关系先经过自己的 validate_graph；批量独立核验后逐项接纳 |
| 8/9 条待查关系 | 分别至少 1/2 个关系工具轮，再加核验回答；额度不足保持未完成 |
| 一成员剩余额度少 | 不借其他成员额度；可形成更小组，其他完成项不重复调用 |
| 模型漏成员/内层非法 | 合法成员保留，错误成员 not_checked；不补造空候选 |
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
| 原子提交失败 | 后一成员证明写入失败时，本次图/覆盖/标记无半提交 |
| 后续技术重试 | search.observe 新 attempt 不改写旧观察，旧成员额度/证据继续有效 |
| 两次真实重试得到相同失败内容 | 结果版本和观察 attempt 仍不同，同次提交重放才幂等 |
| 成员结果换 owner 或版本 | loader 拒绝，不让 leader 或 sibling 的结果授权本成员 |
| 无兼容成员/容量退回 | 单成员走同路径，未选任务队列和公平计数保持正确 |
| 旧冻结运行 | 无 batching 策略不升级；原图谱投影、读/继续契约回归通过 |

完整关系组不允许丢掉失败对象后缩成“成功”子组；共享节点不允许借用其他成员 supported verdict。

## 3. 实施后的定向命令

以下命令**尚未在本次规划中执行**。工作目录 `backend/`。

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

新增 test_recognition_batch.py 后单独运行；配置/API/前端按实际修改再做定向检查。Ruff 仅覆盖本次修改文件，不顺手格式化全库。

专用 PostgreSQL 测试沿用现有 test_tool_engine_postgresql.py 与 fixture 约束。DOCUMENT_ANALYSIS_TEST_DATABASE_URL 必须是 fixture 允许清理的可销毁库；未配置的 skip 如实记录，不能用 SQLite 结论代替并发/事务验收。

## 4. 真实模型对照

工程通过后，扩展现有 `app.evaluation.ontology_tool_engine` 的 manifest 接口（B17），新增 batching 字段先通过 schema 和契约测试；本规划不虚构当前已有 CLI 参数。

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

## 5. 本次规划验证记录

2026-09-18 实际完成：5 份文档、26 个本地链接、14 项需求与 18 项任务映射、引用的既有测试文件、代码围栏及尾部空白检查全部通过；相关路径 `git diff --check` 通过。只读设计复核已补齐阶段组切换、成员证据版本、工具预留额度、finalizer 后补证、结果 owner/版本和同结果重试标识。

未运行应用测试、真实模型、数据库迁移、服务重启或部署；表内模型效果均为待测。已有用户未提交改动保留，既有文档仅补本特性入口链接。
