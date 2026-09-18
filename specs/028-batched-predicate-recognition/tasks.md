# 028 实施任务清单

状态：**全部待实施**。本次只交付规划，不把文档完成记成代码完成。依据 [spec.md](spec.md)、[plan.md](plan.md) 和 [data-model.md](data-model.md)。

路径缩写同 plan。现有测试优先扩展；本文件中标注“拟新增”的路径当前尚不存在。

## A. 契约与纯组包

- [ ] **B01** 建立 `recognition_batch.py` 的 Policy/WorkUnit/MemberContext 及回答外层。独立 local_id、精确成员集合、协议版本、原 claim lineage 继承规则以 fixture 固定。扩展 `test_tool_engine_contracts.py`；拟新增 `test_recognition_batch.py` 测试完整工作单元边界。对应 BFR-01/03/09。
- [ ] **B02** 实现逐成员解析和动态批量 Schema：外层越界/重复拒绝，成员漏答/结构错误隔离，显式空回答不等于全文否定。复用原 freeze/verification 校验；覆盖同名 local_id、错谓词和跨成员引用。依赖 B01；对应 BFR-03/05/06。
- [ ] **B03** 提取 prepare_member_input，构造去重模型视图和 member_protocol_view。扩展 `test_tool_engine_context.py`、`test_tool_engine_freeze.py`，覆盖同 evidence_id 不同 span、fact_eligible 差异、反证和实体白名单隔离、revision>1 恢复。依赖 B01；对应 BFR-04。
- [ ] **B04** 实现 scheduler 非消费预览、容量 pack 和精确消费；仅合已就绪 slot 的当前记录，按成员扣 max_tasks。扩展 `test_semantic_scheduler.py`，验证轮转、公平性、被排除队列不变、跨 subject/revision/record/scope 不合批。依赖 B01/B03；对应 BFR-01/10/13。

## B. 工具、阶段及预算

- [ ] **B05** 增加模型工具 member_task_id 和 dispatch_member_tool；结果/授权/引用按成员路由，保留 call_id 配对。扩展 `test_tool_runtime.py`、`test_tool_runtime_recall.py`、`test_tool_engine_tool_contracts.py`、`test_tool_referent_candidates.py`。依赖 B01/B03；对应 BFR-04/08。
- [ ] **B06** 增加内部 BatchToolProtocolState、stage_group_seq、成员阶段结果引用及组边界验证。修改 current_work 的引用/验证；同组只追加，换组不得存在 pending 请求或未配对工具。扩展 `test_tool_engine_turn_plan.py`、`test_tool_engine_recovery.py`。依赖 B01/B02/B05；对应 BFR-10/11。
- [ ] **B07** 实现 model_call_state v3 物理 receipt 和成员参与额度，barrier 一次预留；恢复读真实请求行。工具累计值在 dispatch 前保存且跨单位继承；修改所有 sum(lineage_calls)/outcome.model_calls 的新路径。扩展 `test_document_analysis_budget_execution.py`、`test_document_analysis_continuous_budget.py`。依赖 B06；对应 BFR-09。
- [ ] **B08** 实现 inspect_work_unit 与批量 discovery/verification：完整成员核验组包、关系工具轮预算、成员错误隔离、冻结结果复用和有限缩组。RecognitionCall 保持一个 worker，成员无独立写 hooks。扩展 `test_tool_engine_adapter.py`、`test_tool_engine_verification.py`、`test_relation_validation_harness.py`。依赖 B02/B03/B05/B06/B07；对应 BFR-02/05/06/08/10。

## C. 图谱与当前状态闭环

- [ ] **B09** 提取协调器 finalize_reviewed_member 和 apply_member_outcome/record_member_completion；按固定次序使用最新 canonical 节点，保留成员证明，最后展开子任务。接回 finalizer validation_feedback 驱动的恢复决策，更新 entity_origins 的成员来源。扩展 `test_reference_resolution_finalize.py`、`test_reference_resolution_execution.py`、`test_tool_engine_execution.py`。依赖 B08；对应 BFR-06/07/13。
- [ ] **B10** 扩展 ExecutionBatch、成员结果包装/loader、persist_batch、write_proofs 和身份授权检查，先写 outcome 制品再读取授权，所有 member outcomes 同事务提交；graph/coverage/applied 标记及展示只发布本次变更。扩展 `test_current_state_transactions.py`、`test_document_current_state.py`。依赖 B06/B09；对应 BFR-11/13。
- [ ] **B11** 保存唯一 active_unit_ref，队列消费和协议同边界；实现 cold restore、成员引用回队列与旧协议显式分派。补证沿原 unit 状态，不重置 generation/recovery/授权。扩展 `test_tool_engine_resume.py`、`test_tool_engine_recovery.py`、`test_document_analysis_execution_recovery.py`。依赖 B04/B06/B07/B10；对应 BFR-09/11。
- [ ] **B12** 接入 executor 主循环及 HeuristicSlotSearch.observe 的成员结果 attempt_id；逻辑任务、物理请求、图谱提交三种数量分别计算。扩展 `test_recognition_coordinator.py`、`test_group_scope_scheduling.py`、`test_slot_completion.py`。依赖 B04/B08/B09/B10/B11；对应 BFR-01/09/13。

## D. 配置、公开契约与验收

- [ ] **B13** freeze_tool_engine_policy 和 _configured_tool_adapter 接入冻结 batching 策略及 fingerprint；旧运行不升级，新策略单成员/多成员同路径。保持公共 extraction_protocol 和 verified 投影。扩展 `test_tool_engine_configuration.py`、API `test_document_analysis.py`、`test_document_analysis_public_projection.py`。依赖 B12；对应 BFR-11/12。
- [ ] **B14** 当前 Harness 观察补充成员描述并匹配实际 request，复用原展示缓存；如新增 schema 字段则同步前端类型/组件及定向测试。禁止展示“批次完成”替代真实运行状态。依赖 B13；对应 BFR-12。
- [ ] **B15** 更新本特性的机器契约/合成正反例以及必要的 027 引用；新批量外层单独版本，不覆盖旧 Schema。按实现决定从新类型生成 fixtures 或新增 `contracts/` 文件并做漂移校验，不在多个文件手写重复权威定义。依赖 B02/B05/B06；对应 BFR-03/08/11。
- [ ] **B16** 完成 quickstart 的工程矩阵、边界检查和定向静态检查；实际运行测试、skip、环境限制分别记录。不得以 SQLite 通过替代专用 PostgreSQL 的事务/所有权验收。依赖 B07—B15；对应所有 BFR。
- [ ] **B17** 扩展既有评测 manifest 的冻结 batching 字段，支持同路径 max_members=1/2/4；修正真实请求/工具/成员参与计数、质量及耗时汇总，不新增评测平台。依赖 B13/B15；对应 BFR-14。
- [ ] **B18** 固定输入/参考/预算运行微基准与完整图谱真实模型对照，使用新输出目录；报告质量、成本、容量退回率和未完成项，再决定新运行默认值。依赖 B16/B17；对应 BFR-14。

## 依赖与并行归属

```text
B01 → B02 / B03
B03 → B04 / B05
B02+B05 → B06 → B07
B02+B03+B05+B06+B07 → B08 → B09 → B10
B04+B06+B07+B10 → B11 → B12 → B13 → B14
B02+B05+B06 → B15
B07..B15 → B16；B13+B15 → B17；B16+B17 → B18
```

建议按文件归属并行：调度/组包，协议/工具，上下文，存储/预算；executor 主循环及应用由一个集成负责人修改，避免并发编辑大文件。先完成契约评审再拆分实现；B08—B12 未闭环前不允许在线启用。

## 每项交付要求

- 与对应 BFR 和真实失败模式关联，测试不得仅复述实现。
- 不放宽原有反例、原文权限、关系证明和身份约束。
- 标明改动文件、实际验证命令/结果、未完成项；模型质量和部署状态单独写。
- 本计划没有时间估算承诺。实际实现量主要取决于状态/预算接入和冷继续验证，不以“只改提示词”估算。

## 需求到任务索引

| 需求 | 实施任务 |
|---|---|
| BFR-01 | B01、B04、B12 |
| BFR-02 | B08、B12 |
| BFR-03 | B01、B02、B15 |
| BFR-04 | B03、B05 |
| BFR-05 | B02、B08 |
| BFR-06 | B02、B08、B09 |
| BFR-07 | B09 |
| BFR-08 | B05、B08、B15 |
| BFR-09 | B07、B11、B12 |
| BFR-10 | B04、B06、B08 |
| BFR-11 | B06、B10、B11、B13 |
| BFR-12 | B13、B14 |
| BFR-13 | B04、B09、B10、B12 |
| BFR-14 | B17、B18 |
