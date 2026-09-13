# 剪枝与增强检索视图实施记录

日期：2026-09-11。关联[方案](../../docs/剪枝和增强语义检索视图方案.md)、
[契约](contracts/adaptive-retrieval.md)与 `tasks.md` 的 AR 阶段。

本轮交付默认关闭的代码和隔离工程验证。没有修改本体、重启服务、迁移在途运行或执行新一轮真实模型质量评测。
P0 的独立样本/预登记参数材料、最终配置校准、三个真实新运行及独立质量门仍待完成。
用户认可人工专家审核，已提供可执行审核工具；工程检查不代替专家裁决。

## 1. 契约落点与反例

| 项目 | 实现 | 本轮验证依据 |
| --- | --- | --- |
| 六类处置与门水位 | `adaptive_search.py`，稀疏 disposition 和 stage result 引用；原覆盖账本不变 | `test_stage_watermarks_do_not_treat_front_gate_pass_as_recognition`、`test_explicit_continue_reactivates_without_rescoring` |
| 空池及跨槽位活性 | `SearchPreparationResult`，executor 消费无 epoch 结果后继续其他槽位 | `test_pre_gate_all_rejected_has_audit_and_reaches_saturation`、`test_executor_persists_empty_and_ranked_results_without_spinning`（三组） |
| 完整池/准入子集 | `AdmissionDecision` 从完整双意图 observations 派生；精确输入、池、scope/hash/幂等校验 | `test_decision_must_match_actual_committed_epoch`、`test_admission_rejects_missing_intent_and_wrong_model_input`、三条通过不补满测试 |
| v3 与 v4 行为分离 | v3 原测试不改；v4 正常继续多值/反证，observation 保持原行为 | `test_v4_success_continues_and_snapshot_replays_exactly`、`test_observation_matches_v3_task_order_requests_and_stop` |
| 门/决策持久屏障 | 预扣仍经 owner ack；gate、epoch、小决策保存后才准入；失效计划结果留审计但不准入 | `test_executor_crash_after_durable_artifact_replays_without_paid_work_loss`（dense gate/epoch/decision/empty result 四处） |
| 恢复与已付费重用 | 前置评分按记录及精确依赖复用；正常 committed epoch 仍由 used 集合防重 | `test_gate_checkpoint_reuses_paid_stage_without_reembedding`、`test_task_checkpoint_resume_keeps_paid_results_and_source_context` |
| 增强视图及组映射 | `contextual_retrieval.py`，自身/上下文/组独立信号，组最多有序展开 8 个 record，重叠去重 | `test_group_hit_preserves_complementary_members_without_fact_permission`：每谓词仅 8 个成员准入，组上下文均无事实权限 |
| 多视图超长 | context 超长回落完整 self 并保护缺口；全部超长不评分、不剪枝，转有界原文探索 | `test_context_variant_overflow_keeps_self_source`、`test_entirely_unavailable_views_use_bounded_original_source_exploration` |
| 快照与状态体积 | 仅 v4 schema 1；不可变门/决策载荷共用；源视图/组清单单独以 hash 复用 | `test_snapshot_history_is_shared_and_source_scope_changes_fail_closed`，既有增量状态回归 |
| 公开覆盖 | 核心/应用 schema、投影、前端类型及两个面板 | `test_coverage_diagnostics_preserve_legacy_shape_and_validate_subset`、公开投影与 schema 回归、前端检查 |
| 校准/专家隔离 | 默认 disabled；线上拒绝 evaluation_only；模型、视图参数、谓词与各通道阈值均冻结 | 校准参数变化/开发阈值拒绝测试；专家审核缺漏、篡改及 HRS 改标 holdout 反例 |

所有新增领域用例位于 `backend/tests/test_extraction/test_adaptive_retrieval.py`，
专家审核用例位于 `test_adaptive_expert_review.py`。实现没有引入数据库迁移或在线 evaluation 依赖。

## 2. 本轮工程检查

定向组合包含上述新测试，以及 heuristic search/executor/ranking、incremental performance/state、
semantic ranking/execution/dispatch receipts/restore priority/paused retry、ontology-guided boundaries、
document analysis public projection/execution recovery 与 API schemas。使用仓库 `.venv`，
`python -m pytest -p no:cacheprovider -q -o faulthandler_timeout=30`；最终组合 **203 passed**，
耗时 61.41 秒，包含新增 28 项用例。该时间是受控工程测试耗时，不是文档识别耗时。

- 过程中的一次 196 项组合通过；后续增加组保护、专家及严格输入反例后重新验证，不能把该数量当最终结果。
- 一次扩大组合在 API fixture 附近无进展，已中止；对应排序恢复/暂停模块单独重跑 **12 通过**，随后带故障栈定时器的完整组合 **203 通过**，没有再次触发超时。保留该异常记录，不将中止轮算通过。
- 专用 PostgreSQL 故障模块 `test_performance_postgresql.py`：**10 skipped**，原因是未设置专用 `DOCUMENT_ANALYSIS_TEST_DATABASE_URL`。没有对生产库运行清表测试，SQLite 验证不代替 PG 锁/并发验收。
- 修改范围 Ruff 通过；前端 TypeScript `tsc --noEmit` 和三个修改文件的 ESLint 通过。
- 最后补齐请求首次付费归属与诊断状态后，新增领域/专家、派发回执和公开投影组合复测 **43 通过**（17.38 秒）；与上面 203 项重叠，不相加。
- Node：`document-analysis-runs.test.mjs`、`document-analysis-retirement.test.mjs`，**2 通过**。

定向命令：

```bash
cd backend
.venv/bin/python -m pytest -p no:cacheprovider -q \
  tests/test_extraction/test_adaptive_retrieval.py \
  tests/test_extraction/test_adaptive_expert_review.py
.venv/bin/python -m pytest -p no:cacheprovider -q \
  tests/test_extraction/test_performance_postgresql.py
```

## 3. 人工专家审核包

新工具 `python -m app.evaluation.adaptive_expert_review prepare|validate` 只操作独立本地制品，
不调用模型、不修改线上图谱。参考沿用已有 `ontology-guided-reference-v1`；识别运行不加载参考。
专家必须先核查全部原文并补充遗漏，再审查输出、主体归属、否定条件及完整路径。

已从 HRS-5592 **历史 v9 开发运行**的冻结 IR/本体/图生成：
`/tmp/adaptive-expert-hrs5592-20260911-r2/`。
入口为 `审核说明.md`，制品包含 `records.json`、`reference.json`、`review.json` 和带 hash 的 manifest。
当前均为待审阅草稿。这是审核工具的真实文件验证及开发诊断材料，**不是 v4 新模型结果或独立保留集**。
此前 r1 保留不覆盖；历史运行目录未改写。两份 HRS hash 均禁止重新标为 holdout。

## 4. 性能与启用边界

- 工程夹具证明了减少准入任务、全拒时无精排 epoch、恢复不重新付费、三条通过不补成固定大池等行为；不把夹具的快慢外推为 CMCReport 全文性能。
- 廉价门首版不按关键词缺失剪枝。冷向量化仍可能覆盖全部合格记录；独立 self/group 信号增加输入，因此未提供实际冷启动提速结论。
- 保护来源、双意图匹配、结构组互补命中和上下文缺口阻止相关性淘汰。保护集合也受有界池/轮次/任务预算约束；饱和保留未核验范围。
- 当前软剪枝仍计入 unattempted；只有实际恢复并核验后才可能满足完整覆盖。公开状态没有新增“剪枝完成全文”。
- `AdaptivePolicy(mode="enforce", evaluation_only=True, calibration=None)` 可采集最终增强配置的数据，且不剪枝；它改变搜索行为，不是 observation 的 v3 等价模式。
- 正式校准必须分别给出上下文、自身、组和精排的 discover/counterevidence 阈值，冻结视图参数 hash。未知谓词不剪枝，参数或模型改变拒绝复用。
- 仍需专家批准参考、未暴露文档、预登记质量/性能阈值、至少三个真实新运行以及专用 PG 验收。开关未打开，未部署，不宣称“更早获得正确关系和属性”的目标已达成。

## 后续默认开启与部署

前述结果是初次关闭态实施的历史记录。后续用户授权默认打开并重新部署，新增 `enhanced` 无校准在线模式；工程回归及部署实况见[部署记录](adaptive-retrieval-deployment.md)。专家校准、独立样本与真实质量/性能门继续待验。

后续用户进一步要求开启低分剪枝，实际启用范围见[低分剪枝试运行记录](pruning-trial-deployment.md)。本文前述 enhanced 状态保留为历史部署事实。
