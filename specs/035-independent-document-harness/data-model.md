# 当前 Harness 性能数据模型

## 2026-10-01 阅读效率契约

模型协议升级 document-harness-v6。Window.payload.reading_scope 使用本请求 source_id 和相对字符区间；辅助来源可作证据，不能替代主区间的提及。
DiscoveryRefinement = {replacement: Discovery|null, source_suggestions: [...]}; null 表示原草稿全部保留。
阅读指引 classes 保留定义、属性标签和身份键；shared definitions 按内容分组且标明适用类，保留局部 range 的区别。
reading.processed_characters 为已有局部结果的主区间并集；reading.complete_characters 为完整叶窗口的主区间并集；0<=complete<=processed<=total。
目录缓存在现有 OntologyEngine 内存中，随 schema mutation/load/close 失效；不新增数据库字段。


## 2026-10-01 替换的当前执行模型

权威详细契约采用[独立方案第 5—9 节](../../docs/图谱分析阅读窗口并行与后置共指重构方案-20261001.md)。
cursor: phase(reading/entities/coreference/graph/done)、entity_window_id、active_batches 映射和 reading_windows。
BatchRef: batch_id/call_key/stage/window_id/step/targets/alias_bindings/source_bindings。
windows: reading_state(pending/split/complete/incomplete)、entity_phase、计划树、guidance、lookup_work。
lookup_work 只引用 draft_call_key 与必要反馈；模型正文/回答只在账本。
entities 增加 name_candidates，名称及归属窗口按稳定顺序派生；字段/证据集合增量合并。
仅一个恢复入口，删除 active_window_id/active_batch 及旧阅读计数；无新表或迁移。

## 2026-09-30 历史存储模型（当前执行入口以上节为准）

此前详细字段契约：[方案第 12—19 节](../../docs/图谱分析性能与候选剪枝优化方案-20260930.md)。

- DocumentRunRequest：新增可空的 call_* 轻量列；新 Harness 请求必填，其他域/旧行不回填。
- harness:metrics/main：调用阶段、候选、事实、work、证明及范围受限计数，按新旧贡献差值更新。
- harness:display/graph：唯一 GraphBase，work_version 对齐 run 的公开展示版本；人工答案和成本不放入 base。
- harness:work：稳定任务，ready/waiting/pruned/done/failed；语义依赖指纹与扩展 basis 分开。
- harness:reference_cues：原文引用线索及截断标志，不把名称命中当身份证明。
- harness:windows：坐标窗口树；cursor 仅保留当前窗口、当前批次及小型进度。
- 当前断言 verification：method、语义结论、指纹及规则身份；端点门控决定最终 state。

所有修改在既有 fence 下原子提交。调用 begin/finish 只读取本条请求和 metrics；结果不可变。
原文引用沿冻结 DocumentIR，规则字段 table_path 为 list[str]。无新的队列、表格规则 UI 或历史快照。

实现补充：WorkItem.selection_hash 固定同一弱候选桶的准入结果，避免继续时轮换剪枝；
processed_predicate_iris 保存属性菜单的当前分片进度。active_batch.source_bindings 独立保存短别名的物理坐标，
因为发给模型的 sources 隐去内部 evidence_id/offset。type_work 只保留本类型阶段尚待应用的分片结果，
应用后清除；实体 type_input_hash 防止父子窗口重复处理同一语义输入。
组澄清生成新的完整语义 ID，原子重绑工作输出；source_timing_verdict 与关系 verification 分别保留原判定。
