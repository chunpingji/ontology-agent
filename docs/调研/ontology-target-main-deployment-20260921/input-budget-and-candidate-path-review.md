# 输入预算、原有探索门与候选关系路径核对

核对日期：2026-09-21。本文区分原有 `evidence_verification` 路径与最新
`candidate_graph` 路径；工程测试使用受控模型回答，不是 CMCReport 真实质量评测。

## 完整核验 Schema 的输入预算

`tool_model_adapter.py::_project_request` 先构建完整的模型短引用投影，再对
`canonical_json(projection.request)` 计数。新增的 target/facet tuple、`const` 及完整
Schema 均计入 `max_input_tokens`，并检查输入加预留输出不超过总上下文。
这是完整请求 JSON 的 tokenizer 计数，不声称与提供方套用模板后的 usage 完全相等。

- 单条 record 核验在预留模型调用前生成真实请求并检查容量。超限的多 target 批次二分；
  单 target 仍超限则返回 `context_budget_exceeded`，不自动付费重试。
- 多成员批次也在预约前测量实际请求；超限时按完整成员拆分。其单成员内部不进一步拆
  targets，这是该路径现有限制，不能把它描述为任意目标集合都能自动完成。
- 实际发送前还会再次投影及计数。打包期的 discovery 估算不取代 verification 检查。
- executor 对这类超限保存 `not_checked`、`complete=False` 的结果，不启用
  `technical_once` 重试。普通未完成最终为 `ANALYSIS_INCOMPLETE`，不能误记成全文否定。
- 新确认的付费回答会更新进展时钟，重复回执不会。没有新持久结果时仍可能触发真实的
  无进展超时；不能由容量检查推导“永不超时”。

主要实现位置：
[请求容量](../../../backend/app/services/extraction/ontology_guided/tool_model_adapter.py)、
[record 分片](../../../backend/app/services/extraction/ontology_guided/record_model_adapter.py)、
[多成员请求](../../../backend/app/services/extraction/ontology_guided/batch_model_adapter.py)。

## 原有属性补查与探索门

以下描述原有 `evidence_verification` 行为，不是候选图的新需求：

1. 区域发现直接提出的属性可随实体独立核验、保留，不需要已证明根入边。
2. entity-only 区域发现之后，实体缺失属性的自动补查受入边激活限制。
   已有歧义属性的 calibration 是另一条线路，不能代替从未发现的缺失属性扫描。
3. `register_entity_subject` 登记实体身份并编译局部菜单，但原有路径不直接开放菜单。
   `expand_tool_relations` 先经 `frontier_eligibility`，再凭已证明入边开放下层探索。
4. `subject_is_active` 还要求实体 supported、类型/指称证明、来源和版本依赖有效。
   只跳过 verification HTTP 不能绕过这些门，也不能产出合法候选图。

相关行为由现有 `test_record_executor.py` 中孤立实体属性保留、未证明根关系不展开、
entity-only 属性在已证明入边后调度的用例覆盖。

## 最新候选关系路径

`RecordDiscoveryPolicy.graph_phase` 默认为 `evidence_verification`，新实证可明确使用
`candidate_graph`。实体类型与指称继续核验，实体发现的 properties 输出被禁用。
候选关系路径由以下最小切点组成：

- 单谓词 adapter 在确认并冻结 discovery 后直接投影、保存 outcome。
- 多谓词 batch 不安排候选成员的 verification；返回真实 `discovery_ref` 与
  `result_version`，不伪造 `verification_ref`。finalize 仍检查当前端点 revision。
- candidate discovery 不再为后续关系 verification 预留一次模型调用。
- 冻结仍检查合法谓词/类型范围、已登记端点、引文与授权原文。候选不要求桥接链闭合、
  source assertion 完整覆盖或 selection 已证明，且选择组不能被缩短。
- `candidate_relations.py` 保留原文、主体/对象/谓词引文、方向、否定、模态、条件与
  完整对象组。状态统一为 `not_checked`；三个证明 flags 为 false，无 proof/decision。
  不调用关系语义 verifier 或 `validate_graph`，不创造新的实体或身份合并。
- 探索入口在 executor 单独开放已核验实体的合法本体菜单；候选边不充当实体身份或
  已证明入边。该部分由主任务实施，不能将“实体可探索”解释为“关系已实证”。

实现与测试：
[候选投影](../../../backend/app/services/extraction/ontology_guided/candidate_relations.py)、
[候选冻结](../../../backend/app/services/extraction/ontology_guided/claim_freeze.py)、
[候选 transport 测试](../../../backend/tests/test_extraction/test_candidate_relation_adapter.py)。

首轮六文件回归实际为 147 项通过；其后新增单/批 discovery 冷恢复和不完整桥接原文
角色测试，候选文件最终单独 12 项通过，Ruff 通过。两次运行不合并成一次全套结果。
冷恢复覆盖已保存模型回答和已保存首个成员 discovery，剩余模型预算为零时直接完成
其余候选投影，不新增付费调用或 verification 引用。

关系发现完成仅代表授权范围已执行候选发现，不能计为关系实证完成或属性识别完整。
真实性、召回与成本仍以主任务的新 CMCReport 运行及逐项原文审查为准。
