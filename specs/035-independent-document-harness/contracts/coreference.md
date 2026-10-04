# 文档内共指契约

新增模型阶段 `coreference_review`：固定 pair_id 响应；verdict 为 same/different/unresolved，
basis 为 explicit_alias/scoped_identifier/explicit_reference/distinct/insufficient，
包括 confidence、evidence（本次来源 ID）、proof（逐字 Quote 数组）和 reason。
explicit_alias 还需 alias_binding：left/right 引用各自 anchor 内的不同称谓，declaration
包含这两个称谓且原文明确建立别名关系。程序拒绝相同名称、错端点和未覆盖的别名声明。
发现及实体核验分别保存和接受重复提及，不把“不是另一个独立对象”当作拒绝理由。
same 需要强依据、confidence >= 0.9、非空逐字 proof 和覆盖两端的 evidence；不同也需要
原文证据，缺失或引用无效转 unresolved 并记录程序理由。模型结构错误按现有调用失败处理。

`GET /api/document-analysis/runs/{id}/harness-graph` 保持只读，增加：
- `entities[].mentions`：原文提及完整公开实体字段，含原始 id、类型、角色、状态、原文。
- `properties[].subject_mention_id`，`relations[].subject_mention_id/object_mention_id`：
  原始提及端点；subject_id/object_id 是文档内统一节点。
- `coreferences[]`：id、left_mention_id/right_mention_id、verdict、basis、reason、
  evidence、proof、applied。applied 仅当同一判定属于无冲突的一致组。
- stage/stage_costs 支持 `coreference_review`。候选/事实计数继续计原始提及和断言，不把
  归并数量当质量指标。只读投影归并不更改权威提及或模型判断。

文档根不参与共指。所有数据仍受原有 run owner 鉴权，来源读取沿用精确引用接口。

## 2026-10-03 重复提及与关系展示修复

同一物理提及的去重在发现阶段按文档、来源、精确位置完成，角色改写不新增提及。
不同位置的同名对象继续独立；跨位置归并仍需上述独立共指核验。

本体明确声明的完整身份键组可从已绑定的逐字编号和作用域值召回共指工作，不依赖
外部数据映射。组件缺失、多值、未解析或仅有普通身份标记时，不以单一编号替代完整键组。
匹配键只生成有界待核验工作，不直接归并；沿用置信度、两端证据及组内冲突门禁。

API 保留全部原始关系记录及原提及端点。前端树、画布、计数和关系详情共享展示聚合：
仅 canonical 主体、canonical 对象、非空谓词、极性、完整条件列表和状态均相同的关系
显示一次，展开可见所有原始关系的端点、理由、核验方式和原文证据。
不同端点、未对齐谓词、状态、否定或条件不合并；关系组不拆成二元边参与聚合。
展示聚合不修改权威事实、采信结论或后端原始断言计数。
