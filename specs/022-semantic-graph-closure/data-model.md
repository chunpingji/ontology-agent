# Data Model

以下对应当前代码布局；概念状态与真实持久字段分别说明。

- `SubjectSlotQuery`：subject_ref、predicate_iri、retrieval_intent、model_text、trusted_context、excluded_sources、dependency_refs、query_dependency_hash。未验证身份与假设保存在排除来源中；逐对 model_input_hash 在观察中记录。
- `RetrievalView`：record_id、model_text、structure_text、metadata_text、source/view hash、source_refs/binding_refs、omitted_refs、token_count、complete/not_rerankable。原文引用身份复用 RecordIndex。
- `RankingPolicy`：模式、pool/batch、意图权重、通道配额、token/call/time/retry 上限、失败与消融政策。模型/tokenizer 不可变身份另存 service/epoch；两者和调度常量一起冻结到运行 fingerprint。
- `RankingEpoch`：epoch_id/seq、plan/subject/query/pool/policy/permission hash、完整池及顺序、queries、retrieval_views、observations、模型身份、实际模式和成本。持久 status 为 ready/degraded/paused/committed；准备/评分过程由服务预算和缓存快照记录，失效轮次存 executor 的 discarded_epochs，不能继续应用。
- `RankingObservation`：query/record/epoch、逐对 input hash、channel hits/raw scores/ranks、raw pair score、intent/pool ranks、view/score status、authority=retrieval_only。批次耗时、排队、实测 tokens 和缓存观察另存 service 的 model_observations；预扣费用不伪装为实耗。
- `RecognitionTask`：task_id、claim_lineage_id、subject/predicate/record、dependency_hash；阶段、章节、ranking_epoch_seq/pool_rank 仅为调度元数据。任务身份不含排名，lineage 不因版本或重验刷新调用额度。
- `RecallLedger`：新 `sparse-candidates-v1` 逐槽位统计实际准入候选的 unattempted/attempted_incomplete/examined；共享 RecordIndex 全集只校验来源与搜索域，不预建逐槽位覆盖条目。旧冻结运行继续与原全集比较；评分成功不计原文 examined。
- `TaskContext`：VerificationTarget、目标/主体/必要/反证原文 fragments、权限、遗漏和预算状态。新 object/value 只来自当前目标，binding 不能独自成为新 claim 来源。
- `GraphNode` 与断言版本：物理节点的同版原文解释不可被后续关系判定覆盖；主体查询的信任另由有效精确入边及其 proof refs 冻结。边/属性载荷改变形成递增 revision；相同语义目标的模型尝试具有独立决策/证明身份，不原地改写旧证明。
- `DependencyIndex`：精确版本的证明 AND/OR 依赖、语义范围订阅和失效闭包。晚到潜在冲突及断言资格撤回停止依赖使用；恢复父证明后，下游必须真正重验。
- `CheckpointEnvelope`：frontier、recall_ledger、task_outcomes、dependency_index、graph_state、diagnostics、ranking_state 等。每条 outcome 保存当时临时排除的排序槽位；ranking_state 还由运行独立制品保存 service、committed_at 和 discarded_epochs，评分前成本写入不依赖原文批次已产生。

所有运行制品沿用现有 owner/fence、event head 与 revision CAS，不新增表、不写中央业务事实。

## 性能增量（2026-09-10）

- 源制品冻结 `performance_policy`：存储/前沿版本、单个识别任务在途和模板公平开关。
  无该字段的旧运行按原策略恢复；新开关只影响后续创建。
- `ranking_state`、`graph`、`recognition_checkpoint` 的大载荷保存为存储版本2引用树；
  逻辑对象及其校验不变。每块由运行身份和内容hash确定，引用包含
  `artifact_id/content_hash/schema_version`；`DocumentRunArtifact`索引负责保留和清理，
  无共享全局缓存或额外表。旧inline格式独立读取，不在GET升级。
- `public_graph`、`ranking_summary`、`source_header`、`source_selections` 是原提交边界
  同时产生的读取制品；不包含向量或完整检索计划。运行公开身份增加
  `structure_snapshot_id/ranking_summary_id`，图版本、状态与预算分别驱动展示。
- 前沿版本2保存完整冻结计划、记录顺序、游标和稀疏任务状态；选择任务时才实例化。
  未展开机会仍计覆盖；模板交错策略单独冻结，旧前沿和默认公平次序保留。
- 排序v2为每个权限/槽位/记录/意图保留基础调用和有限技术重试；v1历史额度不升级。

接口、恢复及失败契约见 [performance.md](contracts/performance.md)。
