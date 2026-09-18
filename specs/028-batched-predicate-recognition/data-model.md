# 028 内部数据与协议

状态：拟实施契约，以下新类型尚不存在。需求见 [spec.md](spec.md)，函数落点见 [plan.md](plan.md)。类型片段省略校验器及原有通用字段；不作为已实现的库 API 示例。

## 1. 执行粒度

| 粒度 | 标识 | 职责 |
|---|---|---|
| 逻辑识别任务 | 原 task_id / claim_lineage_id | 一个主体、谓词、记录、scope 的识别、覆盖和额度继承 |
| 模型工作单元 | work_unit_id | 1..N 个兼容任务共享请求；成员保存后不重排、不增加 |
| 当前图谱提交 | 原 ExecutionBatch.batch_id | 原子发布本次可交付的成员及当前状态 |

一个工作单元可因核验拆组产生多个当前提交。工作单元不是新运行或数据库表。

## 2. 工作单元与授权

拟在 `ontology_guided/recognition_batch.py` 定义纯契约及组包函数，不持有模型客户端、数据库、图谱或线程池：

```python
class RecognitionBatchPolicy(EvidenceModel):
    version: Literal["predicate-batch-v1"]
    max_members: int  # 1..4

class RecognitionWorkUnit(EvidenceModel):
    work_unit_id: str
    members: list[RecognitionTask]  # 非空、有序，task_id/predicate_iri 各自唯一

class MemberContext(EvidenceModel):
    task_id: str
    context: TaskContext
    card: SchemaCard  # 原单谓词卡

class RecognitionBatchContext(EvidenceModel):
    work_unit_id: str
    members: list[MemberContext]
    # model/protocol hooks 作为私有运行时属性，只绑定在批次层
```

工作单元 ID 由 run_fingerprint、冻结批处理策略、有序 task_id/dependency_hash 派生。成员继续使用原 claim_lineage_id 继承额度。dependency_hash 包含谓词，**不要求成员之间相等**；分别核验依赖有效，并核对共同主体版本、record、scope 和共同身份/范围依赖。

派生模型输入包括：

- `members[]`：task_id、谓词卡片引用、注册实体白名单、原文权限、反证和当前阶段目标。
- `evidence_units[]`：按文档 hash、evidence_id、精确 span、原文 hash 去重的原文。
- 每个成员的原文引用保留自己的 `role / fact_eligible`，禁止全局 OR。共同 evidence_id 不等于相同授权片段。
- 成员保留自己的 context_hash、source_scope_hash、proof_dependencies、field_bindings、counterevidence_refs。整个请求 hash 不替换成员证明 hash。
- 复用 freeze_proposal / build_verification_input 时，TaskContext.protocol_state 必须投影为该成员只读协议视图，包含正确 evidence_revision、assertion_generation 等原字段；不得直接传批次顶层而静默回退 revision=1。成员 Context 不独立绑定写协议或预留模型请求的 hooks。
- 卡片只覆盖选中成员的精确谓词集合，数量策略、range 类及注册实体权限仍按成员裁选。不用 `predicate_iri=None` 开放整份菜单。

此视图按需生成，不另存完整上下文副本；恢复入口仍是成员授权和当前协议。

## 3. 发现输出

```python
class MemberDiscoveryAnswer(EvidenceModel):
    task_id: str
    result: DiscoveryEnvelope

class BatchDiscoveryEnvelope(EvidenceModel):
    members: list[MemberDiscoveryAnswer]
```

内部完整复用原 `entities/properties/relations/reference_bindings/external_links/observations`。各成员有独立 local_id 空间，不允许跨成员引用。两个成员都使用 `e1` 合法，服务器以 `(task_id, local_id)` 路由；注册图实体仍必须属于本成员白名单。

每个 result 只处理该成员谓词；多个值/对象/选择组沿用既有语义。identifier_claims 仍按原标识属性约束，不代表另一个属性任务已完成。

解析与隔离：

1. 非 completed、拒绝、截断或非合法 JSON：保存技术结果，不提取部分候选。
2. 外层不是对象、task_id 越界或重复：整轮协议错误，不任意选择重名结果。
3. 外层可定位且 ID 唯一：逐成员严格解析 result、冻结候选；某成员非法不抹去其他合法成员。
4. 缺失成员记 `member_answer_missing`，禁止补成空数组；显式空回答只表达当前授权范围无候选，不推断全文否定。

Schema 要求完整成员集合；运行时先验证外层原始映射，再逐项调用原严格解析器。不能只依赖递归类型校验而使一项结构错误阻断全部合法成员。额外字段仍拒绝。

## 4. 独立核验输出

```python
class MemberVerificationAnswer(EvidenceModel):
    task_id: str
    result: VerificationEnvelope

class BatchVerificationEnvelope(EvidenceModel):
    members: list[MemberVerificationAnswer]
```

每个成员先用原 `build_verification_input()` 建完整目标，再组装一次批量请求。回答逐成员调用原 `validate_verification()`，精确核对 target_id、content_hash、facet、原文和反证。目标路由同样使用复合键。

核验超限按**完整成员及其依赖闭包**拆组，不拆单个 target、关系选择组或 facet。组中可混合属性和关系；成员关系须先拿到本成员的 validate_graph 结果。已核验成员从后续请求移除，不重复消费其额度。

## 5. 工具参数与结果归属

不增加 batch_validate 等新工具。批次协议的每个模型工具参数增加必填 `member_task_id`，其余保持原契约。例如：

```json
{"member_task_id":"task-related","claim_id":"claim-r1","shape_profile_id":"现有关系 profile"}
```

- `build_tool_definitions()` 添加当轮允许成员枚举；执行时仍核验该成员的阶段、工具可用性及前置条件。
- 新 `dispatch_member_tool()` 校验成员后，剥离路由字段，把其余参数与原 call_id 交给原 `dispatch_tool()` 和成员 ToolContext。保存的模型响应保持原样。
- ToolResultRecord 增加 member_task_id；内部 result 仍为原 ToolResult。function_call_output 继续按原 call_id 配对，不新增工具传输协议。
- 恢复校验成员归属与保存的调用参数、允许成员及上下文一致。materialized_refs、relation_checks、registered_mentions 均按成员隔离。
- 每响应默认 8 个工具调用是整个响应上限；原每 lineage 工具额度 16 改为对应逻辑成员继承，不能因新 unit 清零。继续在 dispatch 前持久化该成员累计 tool_calls_used，即便未保存结果也不退回。恢复读取当前成员累计值；新 unit 若再次包含同一逻辑 lineage，继承既有协议中该 lineage 已持久化累计值的最大值，不求和、不只数 tool_result。单 in-flight 和唯一成员持有者保证此值单调。
- retrieve_evidence 的 predicate_iri 必须等于路由成员谓词，只更新该成员授权与证据版本。读取 sibling 原文的可见性不授予事实发现或引用权限。
- 外部查询等需要新 mention_ref 的操作仍须等待前一步成功结果，不能在同轮猜测引用。

## 6. 当前协议与版本

公共图谱 `extraction_protocol="ontology-tool-extraction-v1"` 保持既有语义。新运行在冻结 policy 增加 recognition_batching，内部协议使用 `version="ontology-tool-batch-v1"`；adapter.protocol_version 保留现有工具引擎能力标识，不与内部状态版本混用。

BatchToolProtocolState 复用 stage、request_attempt、completed_attempts、pending_request、stage_input_items、turn_refs、completed_tool_results，改变单任务部分：

| 字段 | 当前权威内容 |
|---|---|
| lineage_id | work_unit_id，复用 calls:protocols/results 键空间 |
| work_unit | 一份有序成员描述 |
| member_states[task_id] | base_target、授权、evidence/context hash、revision、generation、recovery、工具计数和 materialized_refs |
| discovery_refs[task_id] | 原 FrozenClaimSet 的独立引用 |
| verification_refs[task_id] | 原 VerifiedClaimSet 的独立引用 |
| outcome_refs[task_id] | 协调器最终产生的原 TaskOutcome 引用 |
| stage_member_ids | 当前请求组，开始请求后不换组 |
| stage_group_seq | 当前阶段组序号，单调增加，区分连续 verification 组及有限重试 |

不以首成员 base_target 冒充整批主体。是否已发布沿用当前工作的成员 applied marker，不另存 published 集合。

同组身份为 `(stage, stage_group_seq, stage_member_ids, 各成员 assertion_generation)`。同组内初始输入通常固定，turn/tool 引用只能追加；唯一证据刷新例外是已确认 retrieve_evidence 引起成员 evidence_revision 单调增加，此时按新授权重建初始视图，保留所有已有模型项和工具配对，不能删改原指令或回答目标。其他同响应工具尚未完成时允许先保存该刷新，但全部 pending 配对完成前不发送新模型请求。

没有 pending_request 且已有工具调用全配对后才允许真正换组。换组序号递增，允许创建新的初始输入并清空当前组 turn/tool 引用，已确认成员阶段结果引用不清除；对应原响应仍保存在独立账本。pending_request 和 model_turn 都绑定组序号与实际成员。恢复验证器必须区分有成功检索依据的同组刷新、合法换组和未经授权的输入改写。

`protocol_result_ref(unit_id, field, value, member_task_id=..., result_version=...)` 对成员 discovery/verification/outcome 加入成员身份和结果版本；model_turn 按 unit+attempt 保存一次；tool_result 按 unit+attempt+call_id 定位并核对成员。

成员阶段结果在现有 calls:results 中的包装为 `{lineage_id, member_task_id, result_version, field, value}`，其中 value 保持原 FrozenClaimSet/VerifiedClaimSet/TaskOutcome；结果版本为该成员的 `stage_group_seq、last_participating_request_attempt、assertion_generation、evidence_revision`。引用 hash 包括完整包装身份与内容。共享 model_turn/tool_result 使用各自明确包装和校验规则，不伪装成某个成员的独占结果。

`load_protocol_result(..., member_task_id, result_version)` 与 attach_protocol_results 必须传递、核对 owner 和版本后重算引用；旧协议仍使用原三字段包装。成员最后参与 request attempt 从实际 receipt 派生，不取其他成员后来请求的全局最大值。

当前工作只增加一个 `active_unit_ref`，指向上述唯一协议，不再复制 members 或完整上下文。原 scheduler 已消费的成员和 active_unit_ref 在同一次现有安全边界中持久化。单位关闭时清引用，但不删除既有调用账本。

不新增 batch_history、会话副本、成员图谱快照、旁路恢复文件或每轮全量重写的大状态对象。

## 7. 请求预算

批次运行 `model_call_state.version=3`。一次实际请求仍保存一张 request receipt，增加实际参与成员：

```python
receipt = {
    "sequence": physical_reservation_sequence,
    "lineage_id": work_unit_id,
    "protocol_attempt": attempt,
    "stage": stage,
    "member_task_ids": active_task_ids,
    "member_lineage_ids": active_claim_lineage_ids,
    # 保留原 request hash / run fingerprint / ordinal 等字段
}
```

```text
实际预留请求数 = 唯一请求 receipt 数 = 当前 reservation_sequence
成员参与次数 = receipts 中包含其 member_lineage_id 的请求数
成员剩余量 = 冻结 max_lineage_calls（现为 4） - 该成员参与次数
```

一次请求包含 4 个成员：实际次数 +1，每成员参与次数各 +1，token usage 只记一次。请求组内成员都计参与，即便某成员该轮没有工具调用；组包可先排除无需参与者。

成员参与次数从 calls:requests 派生，运行内存可增量缓存，不新增持久计数副本。restore_calls 当前返回空 reservations，必须补读请求行，不能从空列表恢复额度。全局及连续运行预算按真实 receipt 计，失败和未知结局均消耗已预留额度。

不把成员剩余额度相加当作批次可用额度。某请求是否可发，由**所有实际参与成员均有余额**、全局余额、连续执行窗口共同决定。工作单元可有多组后续核验，各组仅扣其参与成员；没有独立的“批次再赠 4 次”额度，也不将旧单成员上限机械用作整组所有拆分请求的总上限。

修复、缩批、更换 unit 和冷继续都沿用 member_lineage_id。已核验成员不为其他成员补证付出参与额度。

## 8. 发布、覆盖与失败

| 情况 | 当前行为 |
|---|---|
| 打包前未就绪或容量排除 | 保持未尝试和原队列位置，不 observe/mark_record |
| 已包含在请求但漏答/非法 | attempted_incomplete / not_checked，保留原因 |
| 已有候选但证据不足 | undetermined，保留逐声明结果 |
| 明确不支持候选 | unsupported，不转换为原文否定事实 |
| 部分声明支持 | 独立接纳，完整度按成员真实情况计算 |
| 成员本次结果发布 | 写本成员应用标记、检索观察和 coverage |

HeuristicSlotSearch.observe 的 attempt_id 改为由 unit_id、member_task_id、result_version、成员结果引用派生。同一提交重放一致，后续真实修复即使返回相同失败内容也因实际参与 request attempt 不同而分别记录；防止原 task_id 不变导致同 attempt 改写观察。

一次提交中按冻结顺序逐成员 `finalize → apply`，下一成员使用最新已接纳 canonical 节点。该提交的图、证明、覆盖、应用标记、协议及展示缓存复用既有事务一起发布。事务未提交时只重做确定性物化；已提交成员跳过，均不重做模型请求。

应用标记精确绑定 `(unit_id, member_task_id, result_version, outcome_ref)`，表示同一结果已发布，不永久封闭该成员。确定性检查发现需要补证的缺口时，由协调器调用原恢复决策；有授权、有剩余额度的成员仍能在原 unit 生成新 evidence_revision/generation 和新结果引用，旧引用的应用幂等性不阻断真实的新结果。作废哪些 verification/outcome 引用依原证据/声明版本规则决定，其他成员不受影响。

因核验拆组而先交付部分成员时，其余成员继续由 active unit 唯一持有；不能同时放回 scheduler。技术重试和补证只缩小 stage_member_ids，不另建 unit 或初始化成员状态。确需交回原调度队列时，任务只携带原 unit/member 引用，重新激活同一成员状态；generation、evidence_revision、authorization、recovery_used 和阶段引用一并保留。不得同时存在活动 unit 待办和同成员排队待办。
