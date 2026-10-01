# Harness v2 性能与剪枝契约

## 2026-10-01 阅读效率补充契约

新运行模型协议 document-harness-v6；本节替代 v5 发现/阅读计数字段。
reading_scope 明示每个来源中的主区间；SourceAnchor 仅能指向含主区间的来源，解码再次校验具体区间。
refine 用 replacement=null 复用付费草稿；有替换时完整替换并保留撤回字段观察。空候选不调用 refine，不把未执行/失败查询当作全文否定。
reading 增加 complete_characters，processed_characters 计已处理区间（包括部分结果）；scope_complete 仍只取完整覆盖状态。
不转换旧运行；缺少当前阅读契约时 GET 返回 HARNESS_NEW_RUN_REQUIRED（409，retryable=false），错误码属于公开 ApiError 枚举。


## 2026-10-01 当前执行契约

本节覆盖下方历史单批条款。模型协议 document-harness-v5，公开 engine 与路由保持。
创建策略冻结 execution_policy.flow=local_reading、reading_concurrency=2（仅允许 1/2）。
cursor.phase 与 active_batches 管理最多两个阅读请求，其余阶段最多一个；清理按 batch_id。
精确 call_key 复用与目标 batch_id 分离，同请求只传输一次，各自使用冻结来源绑定应用。
progress.phase 增加 reading/entities/coreference/graph/done；reading_windows 含 total/saved/complete/incomplete/active。
叶窗口计数替换 windows_total/windows_discovered/windows_reviewed；saved=complete+incomplete<=total。
reading/scope_complete、work_counts、成本和事实计数保持独立；局部保存不表示关系完成。
详细函数/事务契约及 A01—A20 采用[独立方案](../../../docs/图谱分析阅读窗口并行与后置共指重构方案-20261001.md)。

## 2026-09-30 历史契约（当前执行入口以上节为准）

此前消息、函数、批次、错误及前端字段采用
[已批准方案第 12—21 节](../../../docs/图谱分析性能与候选剪枝优化方案-20260930.md)。

公开协议 document-harness-v2，模型协议 document-harness-v4；仅新运行，不转换旧制品。
HarnessProgress 增加 reading、work_counts、candidate_scope_limited、rule_verified_count、llm_verified_count；
stage_costs 增加 unmeasured_attempts。HarnessGraph 增加 candidate_work。
属性/关系/组公开 verification(method/rule_id/rule_version/semantic_verdict)。

Stage：ingest/parse/discover/type_alignment/referent_alignment/referent_candidates/referent_selection/
entity_review/planning/property_alignment/relation_alignment/group_interpretation/evidence_review/coreference_review/complete。
GET 保持只读与 no-store，缺缓存返回 HARNESS_DISPLAY_NOT_READY (409)，保留鉴权、取消及删除检查。

内部 prepared 请求不计费；prepare_batch 与 active_batch 原子登记。
invoke_prepared 使用原 payload/schema/冻结 policy；业务输出和清除 active_batch 原子提交。
Controller 通过回调使用仓储，不导入数据库。CurrentState row 版本与 run 展示版本分开。

候选生成遵循 source hint / 结构角色 / ReferenceCue；属性独立处理。
规则只允许 exact_table_relation/1 和相同依赖复用，普通预检不得冒充语义证明。
未知参与组只补证一次；共享核对中的 R/T 保持同批；所有输出必须覆盖精确输入键集合。

验收映射：H1 → A01—A12，H2 → P01—P14，H3 → V01—V14。

实现细节：当前批次另存 source_bindings，不能从模型 sources 反推物理坐标。
弱候选 selection_hash 保持同桶剪枝不轮转；属性 processed_predicate_iris 支持逐菜单分片原子应用。
组解释改变断言语义时按完整语义重新定 ID，重绑输出并独立核对，避免同语义重复事实。
