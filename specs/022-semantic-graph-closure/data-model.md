# Data Model

以下对应当前代码布局；概念状态与真实持久字段分别说明。

- `SubjectSlotQuery`：subject_ref、predicate_iri、retrieval_intent、model_text、trusted_context、excluded_sources、dependency_refs、query_dependency_hash。未验证身份与假设保存在排除来源中；逐对 model_input_hash 在观察中记录。
- `RetrievalView`：record_id、model_text、structure_text、metadata_text、source/view hash、source_refs/binding_refs、omitted_refs、token_count、complete/not_rerankable。原文引用身份复用 RecordIndex。
- `RankingPolicy`：模式、pool/batch、意图权重、通道配额、token/call/time/retry 上限、失败与消融政策。模型/tokenizer 不可变身份另存 service/epoch；两者和调度常量一起冻结到运行 fingerprint。
- `RankingEpoch`：epoch_id/seq、plan/subject/query/pool/policy/permission hash、完整池及顺序、queries、retrieval_views、observations、模型身份、实际模式和成本。持久 status 为 ready/degraded/paused/committed；准备/评分过程由服务预算和缓存快照记录，失效轮次存 executor 的 discarded_epochs，不能继续应用。
- `RankingObservation`：query/record/epoch、逐对 input hash、channel hits/raw scores/ranks、raw pair score、intent/pool ranks、view/score status、authority=retrieval_only。批次耗时、排队、实测 tokens 和缓存观察另存 service 的 model_observations；预扣费用不伪装为实耗。
- `RecognitionTask`：task_id、claim_lineage_id、subject/predicate/record、dependency_hash；阶段、章节、ranking_epoch_seq/pool_rank 仅为调度元数据。任务身份不含排名，lineage 不因版本或重验刷新调用额度。
- `RecallLedger`：与冻结 RecordIndex 全集独立比较，逐槽位 unattempted/attempted_incomplete/examined；评分成功不计原文 examined。
- `TaskContext`：VerificationTarget、目标/主体/必要/反证原文 fragments、权限、遗漏和预算状态。新 object/value 只来自当前目标，binding 不能独自成为新 claim 来源。
- `GraphNode` 与断言版本：物理节点的同版原文解释不可被后续关系判定覆盖；主体查询的信任另由有效精确入边及其 proof refs 冻结。边/属性载荷改变形成递增 revision；相同语义目标的模型尝试具有独立决策/证明身份，不原地改写旧证明。
- `DependencyIndex`：精确版本的证明 AND/OR 依赖、语义范围订阅和失效闭包。晚到潜在冲突及断言资格撤回停止依赖使用；恢复父证明后，下游必须真正重验。
- `CheckpointEnvelope`：frontier、recall_ledger、task_outcomes、dependency_index、graph_state、diagnostics、ranking_state 等。每条 outcome 保存当时临时排除的排序槽位；ranking_state 还由运行独立制品保存 service、committed_at 和 discarded_epochs，评分前成本写入不依赖原文批次已产生。

所有运行制品沿用现有 owner/fence、event head 与 revision CAS，不新增表、不写中央业务事实。
