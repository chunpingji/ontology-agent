# Harness v2 性能与剪枝契约

详细消息、函数、批次、错误及前端字段采用
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
